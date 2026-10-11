"""Offline tests for the validity job (IP-009). Recorded answers, no network."""
from __future__ import annotations

from unittest.mock import Mock

import pytest

from fluid.audit import Audit
from fluid.ledger import Ledger
from fluid.service import Decisions

SPEC = {'types': ['application', 'invoice'],
        'checks': [{'id': 'name', 'rule': 'The applicant name is present.'},
                   {'id': 'date', 'rule': 'The form is dated.'}],
        'completeness_levels': ['empty', 'partial', 'complete']}


@pytest.fixture
def svc(tmp_path):
    client = Mock()
    client.call_timeout = None
    client.first_timeout = 8.0
    client.request_budget.return_value = {
        'budget_s': 2.0, 'budget_source': 'measured', 'expected_ms': 500.0,
        'exceeds_budget': False, 'total_chars': 10, 'questions': 4}
    service = Decisions(client=client, audit=Audit(tmp_path / 'a.jsonl'), ledger=Ledger(tmp_path / 's'))
    return service, client


def _noul(p):
    return {'type': 'noul', 'noul': p}


def _choice(choice, probs):
    return {'type': 'choice', 'choice': choice, 'probabilities': probs}


def _score(index, n=3):
    return {'type': 'score', 'score': index, 'probabilities': {str(k): (1.0 if k == index else 0.0) for k in range(n)}}


def _answers(name=.95, date=.95, dtype='application', dtop=.9, completeness=2):
    return {'check.name': _noul(name), 'check.date': _noul(date),
            'document_type': _choice(dtype, {'application': dtop, 'invoice': 1 - dtop, 'unknown': 0.0}),
            'completeness': _score(completeness)}


def _single(answers):
    return {'answers': answers, 'lane': 'max', 'timings': {}, 'usage': {}}


def test_all_checks_hold_known_type_is_valid(svc):
    service, client = svc
    client.system_one.return_value = _single(_answers())
    out = service.validity('A filled application form.', SPEC)
    assert out['verdict'] == 'valid' and not out['failing']
    assert out['document_type'] == 'application' and out['completeness'] == 'complete'
    assert out['note'] == 'validity is not support'   # never read as truth
    assert out['page_split'] is False


def test_a_failing_check_is_invalid(svc):
    service, client = svc
    client.system_one.return_value = _single(_answers(date=.05))
    out = service.validity('x', SPEC)
    assert out['verdict'] == 'invalid' and out['failing'] == ['date']


def test_unknown_type_abstains_before_checks(svc):
    service, client = svc
    client.system_one.return_value = _single(_answers(date=.02, dtype='unknown', dtop=.1))
    out = service.validity('x', SPEC)
    assert out['verdict'] == 'abstain'   # type gate comes first, even with a failing check


def test_low_type_confidence_abstains(svc):
    service, client = svc
    answers = _answers()
    # application is the top choice but only 0.40 confident (below type_line 0.5).
    answers['document_type'] = _choice('application', {'application': .40, 'invoice': .35, 'unknown': .25})
    client.system_one.return_value = _single(answers)
    out = service.validity('x', SPEC)
    assert out['verdict'] == 'abstain' and out['document_type_top'] == pytest.approx(.40)


def test_uncertain_check_asks(svc):
    service, client = svc
    client.system_one.return_value = _single(_answers(date=.5))
    out = service.validity('x', SPEC)
    assert out['verdict'] == 'ask' and 'date' in out['uncertain']


def test_invalid_spec_or_empty_packet_is_refused_not_scored(svc):
    service, client = svc
    assert service.validity('', SPEC)['verdict'] == 'ask'                 # no packet, no images
    assert service.validity('x', {'types': [], 'checks': [], 'completeness_levels': []})['verdict'] == 'ask'
    client.system_one.assert_not_called()


def test_page_split_composes_each_check_by_maximum(svc):
    service, client = svc
    # First (single-request) budget exceeds; the page-split's own budget fits.
    client.request_budget.side_effect = [
        {'budget_s': None, 'budget_source': 'measured', 'expected_ms': 9e9, 'exceeds_budget': True,
         'total_chars': 99999, 'questions': 4},
        {'budget_s': 4.0, 'budget_source': 'measured', 'expected_ms': 500.0, 'exceeds_budget': False,
         'total_chars': 20000, 'questions': 6}]
    # Two pages for the check nouls (name appears on page 2, date on page 1), then
    # the document_type + completeness over the first page.
    client.system_one_batch.return_value = {'results': [
        {'answers': {'check.name': _noul(.1), 'check.date': _noul(.9)}, 'usage': {}},
        {'answers': {'check.name': _noul(.96), 'check.date': _noul(.2)}, 'usage': {}},
        {'answers': {'document_type': _choice('application', {'application': .9, 'invoice': .1, 'unknown': 0.0}),
                     'completeness': _score(2)}, 'usage': {}},
    ], 'timings': {}, 'lane': 'max'}
    out = service.validity('x' * 30000, SPEC)   # oversized text, no images
    assert out['page_split'] is True
    # each check is the max across pages: name 0.96, date 0.9 -> both hold -> valid
    assert out['checks']['name']['holds'] == pytest.approx(.96)
    assert out['checks']['date']['holds'] == pytest.approx(.9)
    assert out['verdict'] == 'valid'
