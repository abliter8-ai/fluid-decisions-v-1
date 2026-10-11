"""Offline tests for the constraints job (IP-009). Recorded answers, no network."""
from __future__ import annotations

from unittest.mock import Mock

import pytest

from fluid.audit import Audit
from fluid.ledger import Ledger
from fluid.service import Decisions


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


def _held(p):
    return {'type': 'noul', 'noul': p}


def _sev(index, n=4):
    return {'type': 'score', 'score': index,
            'probabilities': {str(k): (1.0 if k == index else 0.0) for k in range(n)}}


def _batch(*per_item):
    def batch(requests, budget_s=None):
        return {'results': [{'answers': a, 'usage': {'input_tokens': 3}} for a in per_item],
                'timings': {}, 'lane': 'max'}
    return batch


CONSTRAINTS = [{'id': 'hours', 'rule': 'Driver is within the hours cap.'},
               {'id': 'licence', 'rule': 'Driver holds the required licence class.'}]


def test_all_hold_is_pass(svc):
    service, client = svc
    client.system_one_batch.side_effect = _batch(
        {'held': _held(.95), 'severity': _sev(0)}, {'held': _held(.96), 'severity': _sev(0)})
    out = service.constraints({'driver': 'A'}, CONSTRAINTS)
    assert out['verdict'] == 'pass' and not out['failing'] and out['passes'] == 1
    assert set(out['constraints']) == {'hours', 'licence'}


def test_a_failing_constraint_is_no_feasible_candidate_with_order_and_severity(svc):
    service, client = svc
    client.system_one_batch.side_effect = _batch(
        {'held': _held(.95), 'severity': _sev(0)},
        {'held': _held(.05), 'severity': _sev(3)})   # licence fails, critical
    out = service.constraints({'driver': 'A'}, CONSTRAINTS)
    assert out['verdict'] == 'no_feasible_candidate'
    assert out['failing'] == [{'id': 'licence', 'severity': 'critical'}]
    assert out['constraints']['licence']['status'] == 'fails'
    assert out['constraint_order'] == ['hours', 'licence']   # caller order preserved


def test_uncertain_triggers_one_second_pass_then_resolves(svc):
    service, client = svc
    client.system_one_batch.side_effect = [
        _batch({'held': _held(.95), 'severity': _sev(0)},
               {'held': _held(.5), 'severity': _sev(1)})(None),   # licence uncertain
        _batch({'held': _held(.95), 'severity': _sev(1)})(None),  # second pass resolves it
    ]
    out = service.constraints({'driver': 'A'}, CONSTRAINTS)
    assert out['passes'] == 2 and out['verdict'] == 'pass'


def test_missing_held_is_uncertain_and_asks(svc):
    service, client = svc
    client.system_one_batch.side_effect = _batch(
        {'held': _held(.95), 'severity': _sev(0)}, {'severity': _sev(0)})  # no held on licence
    # second pass also lacks held -> stays uncertain -> ask
    client.system_one_batch.side_effect = [
        _batch({'held': _held(.95), 'severity': _sev(0)}, {'severity': _sev(0)})(None),
        _batch({'severity': _sev(0)})(None)]
    out = service.constraints({'driver': 'A'}, CONSTRAINTS)
    assert out['verdict'] == 'ask' and 'licence' in out['uncertain']


def test_invalid_input_is_a_failure(svc):
    service, _ = svc
    assert service.constraints('', CONSTRAINTS)['verdict'] == 'ask'
    assert service.constraints({'x': 1}, [])['verdict'] == 'ask'
    assert service.constraints({'x': 1}, [{'id': 'a'}])['verdict'] == 'ask'          # no rule
    dup = [{'id': 'a', 'rule': 'r'}, {'id': 'a', 'rule': 's'}]
    assert service.constraints({'x': 1}, dup)['reasons'] == ['duplicate_constraint_id']


def test_no_label_proposes_a_fix(svc):
    service, client = svc
    client.system_one_batch.side_effect = _batch(
        {'held': _held(.05), 'severity': _sev(2)}, {'held': _held(.95), 'severity': _sev(0)})
    out = service.constraints({'driver': 'A'}, CONSTRAINTS)
    blob = repr(out).lower()
    assert 'suggest' not in blob and 'fix' not in blob and 'recommend' not in blob
