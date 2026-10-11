"""Budget derivation for every model call type (IP-005 section 1).

The client derives a per-call budget from the measured cost model and a YAML
policy; the service budgets every gate, classify, route and verify call and
declines to send a call whose expected cost exceeds the YAML cap. No number
lives in Python: margin and cap come from the pack, the floor is ``FD_TIMEOUT``
and the fixed override is ``FD_CALL_TIMEOUT``.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock
from unittest.mock import Mock

import httpx
import pytest

from fluid.audit import Audit
from fluid.client import ClientError, D1Client
from fluid.ledger import Ledger
from fluid.service import Decisions

BUDGET = {"budget_margin": 2.0, "max_budget_s": 10}


def _client(**env):
    with mock.patch.dict(os.environ, env, clear=True):
        return D1Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"status": "ok"})))


def test_request_budget_includes_per_question_term():
    client = _client()
    client.cost_models["max"] = {"intercept_ms": 400.0, "ms_per_char": 0.05, "ms_per_question": 50.0}
    budget = client.request_budget(2000, 12, BUDGET)
    # 400 + 0.05*2000 + 50*12 = 1100 ms; required = 2 * 1100 / 1000 = 2.2 s (above the 1 s floor).
    assert budget["expected_ms"] == 1100.0
    assert budget["budget_source"] == "measured"
    assert budget["budget_s"] == pytest.approx(2.2)
    assert budget["total_chars"] == 2000 and budget["questions"] == 12
    assert budget["exceeds_budget"] is False
    # The same state with one question costs the per-question difference less.
    one = client.request_budget(2000, 1, BUDGET)
    assert one["expected_ms"] == pytest.approx(1100.0 - 50.0 * 11)
    client.close()


def test_post_result_wrapper_carries_no_per_question_term():
    client = _client()
    client.cost_models["max"] = {"intercept_ms": 100.0, "ms_per_char": 0.05, "ms_per_question": 20.0}
    # The wrapper passes question_count=0 so IP-003 numbers are unchanged.
    assert client.post_result_budget(12000, BUDGET)["expected_ms"] == 700.0
    client.close()


def test_floor_is_fd_timeout_and_never_below_it():
    client = _client(FD_TIMEOUT="1")
    client.cost_models["max"] = {"intercept_ms": 10.0, "ms_per_char": 0.0, "ms_per_question": 0.0}
    budget = client.request_budget(10, 1, BUDGET)
    assert budget["budget_s"] == 1.0  # floored at FD_TIMEOUT, not 0.04
    client.close()


def test_floor_s_policy_raises_the_floor_above_fd_timeout():
    # IP-006: a YAML floor_s lifts the derived-budget floor above FD_TIMEOUT so
    # small-state calls no longer time out at one second under lane load.
    client = _client(FD_TIMEOUT="1")
    client.cost_models["max"] = {"intercept_ms": 10.0, "ms_per_char": 0.0, "ms_per_question": 0.0}
    budget = client.request_budget(10, 1, {"budget_margin": 2.0, "max_budget_s": 10, "floor_s": 2.0})
    assert budget["budget_source"] == "measured" and budget["budget_s"] == 2.0
    # With no cost model the fallback still uses FD_TIMEOUT, not floor_s.
    client.cost_models.clear()
    fallback = client.request_budget(10, 1, {"budget_margin": 2.0, "max_budget_s": 10, "floor_s": 2.0})
    assert fallback["budget_source"] == "fallback" and fallback["budget_s"] == 1.0
    client.close()


def test_over_cap_sets_exceeds_and_clamps_to_cap():
    client = _client()
    client.cost_models["max"] = {"intercept_ms": 0.0, "ms_per_char": 1.0, "ms_per_question": 0.0}
    budget = client.request_budget(12000, 1, {"budget_margin": 2.0, "max_budget_s": 10})
    # required = 2 * 12000 ms = 24 s > 10 s cap
    assert budget["exceeds_budget"] is True
    assert budget["budget_s"] == 10.0
    client.close()


def test_fd_call_timeout_override_is_capped_and_can_exceed():
    client = _client(FD_CALL_TIMEOUT="4")
    assert client.call_timeout == 4.0
    client.cost_models["max"] = {"intercept_ms": 100.0, "ms_per_char": 0.05, "ms_per_question": 0.0}
    budget = client.request_budget(12000, 1, {"budget_margin": 2.0, "max_budget_s": 3},
                                   override=client.call_timeout)
    # override 4 s, capped to 3 s; required 2*700/1000 = 1.4 s < override, so not exceeded
    assert budget["budget_source"] == "override" and budget["budget_s"] == 3.0
    # A fixed override smaller than the required budget is rejected before sending.
    tight = client.request_budget(12000, 1, {"budget_margin": 2.0, "max_budget_s": 30},
                                  override=0.5)
    assert tight["exceeds_budget"] is True
    client.close()


def test_absent_cost_model_falls_back_to_fixed_timeout():
    client = _client(FD_TIMEOUT="1")
    budget = client.request_budget(12000, 12, BUDGET)
    assert budget["budget_source"] == "fallback"
    assert budget["expected_ms"] is None
    assert budget["budget_s"] == 1.0
    assert budget["exceeds_budget"] is False
    client.close()


def test_invalid_fd_call_timeout_is_rejected():
    for value in ("0", "nan", "-1"):
        with pytest.raises(ValueError):
            _client(FD_CALL_TIMEOUT=value)


# ---- service-level: every call carries a budget; over-cap asks without sending ----

def _service(tmp_path, exceeds=False):
    client = Mock()
    client.call_timeout = None
    client.first_timeout = 8.0
    client.request_budget.return_value = {
        "budget_s": 2.0, "budget_source": "measured", "expected_ms": 500.0,
        "exceeds_budget": exceeds, "total_chars": 42, "questions": 12,
    }
    client.post_result_budget.return_value = {
        "budget_s": 2.0, "budget_source": "measured", "expected_ms": 500.0, "exceeds_budget": False,
    }
    service = Decisions(client=client, audit=Audit(tmp_path / "audit.jsonl"),
                        ledger=Ledger(tmp_path / "sessions"))
    return service, client


def _gate_answers():
    from fluid.policy import load_pack
    answers = {name: {"type": "noul", "noul": 0.05}
               for name, q in load_pack("gate")["questions"].items() if q["type"] == "noul"}
    levels = load_pack("gate")["policy"]["risk_order"]
    answers["operation"] = {"type": "score", "score": 0, "confidence": 1,
                            "legend": {str(i): v for i, v in enumerate(levels)},
                            "probabilities": {str(i): float(i == 0) for i in range(len(levels))}}
    return {"answers": answers, "lane": "max", "usage": {"input_tokens": 7, "output_tokens": 0},
            "timings": {"ms": 90}}


def test_gate_result_and_audit_carry_the_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("FD_GATE_PACK", "gate-packet")  # budget mechanics on the flat packet gate
    service, client = _service(tmp_path)
    client.system_one.return_value = _gate_answers()
    result = service.gate("read_file", {"path": "README.md"}, user_request="read it")
    assert result["budget"]["budget_source"] == "measured"
    assert result["budget"]["total_chars"] == 42 and result["budget"]["questions"] == 12
    record = json.loads(Path(service.audit.path).read_text().splitlines()[-1])
    assert record["budget"]["budget_source"] == "measured"
    # The first call per lane is granted the first-call timeout so a cold shape
    # warms; the recorded budget keeps the derived value.
    assert client.system_one.call_args.kwargs["budget_s"] == 8.0
    assert result["budget"]["budget_s"] == 2.0
    # A second call on the now-primed lane uses the derived transport budget.
    service.gate("read_file", {"path": "OTHER.md"}, user_request="again")
    assert client.system_one.call_args.kwargs["budget_s"] == 2.0


def test_gate_over_cap_returns_ask_without_sending(tmp_path):
    service, client = _service(tmp_path, exceeds=True)
    result = service.gate("read_file", {"path": "x"}, user_request="read it")
    assert result["verdict"] == "ask" and result["reasons"] == ["exceeds_budget"]
    assert result["budget"]["exceeds_budget"] is True
    client.system_one.assert_not_called()
    client.system_one_batch.assert_not_called()


def test_over_cap_still_runs_static_denials(tmp_path):
    service, client = _service(tmp_path, exceeds=True)
    # A statically denied command is denied before the budget is ever consulted.
    result = service.gate("shell", {"command": "rm -rf /tmp/example"})
    assert result["verdict"] == "deny"
    client.request_budget.assert_not_called()
    client.system_one.assert_not_called()


def test_route_and_verify_over_cap_ask_without_sending(tmp_path):
    service, client = _service(tmp_path, exceeds=True)
    route = service.route("A support ticket about billing")
    assert route["verdict"] == "ask" and route["reasons"] == ["exceeds_budget"]
    verify = service.verify("A source sentence.", ["A claim."])
    assert verify["verdict"] == "ask" and verify["reasons"] == ["exceeds_budget"]
    client.system_one.assert_not_called()
    client.system_one_batch.assert_not_called()


def test_timed_out_gate_decision_carries_its_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("FD_GATE_PACK", "gate-packet")  # budget mechanics on the flat packet gate
    # IP-006: a timed-out gate decision still reports the budget it was given.
    service, client = _service(tmp_path)
    client.system_one.side_effect = ClientError("timeout")
    result = service.gate("read_file", {"path": "x"}, user_request="read it")
    assert result["verdict"] == "ask" and result["reasons"] == ["timeout"]
    assert result["budget"]["budget_source"] == "measured"


def test_no_budget_block_keeps_fixed_timeouts(tmp_path, monkeypatch):
    monkeypatch.setenv("FD_GATE_PACK", "gate-packet")  # budget mechanics on the flat packet gate
    from fluid.policy import load_pack
    service, client = _service(tmp_path)
    client.system_one.return_value = _gate_answers()
    pack = load_pack("gate")
    del pack["policy"]["budget"]
    with mock.patch("fluid.service.load_pack", lambda _: pack):
        result = service.gate("read_file", {"path": "x"}, user_request="read it")
    # With no budget block the pack reports not_requested and no budget_s is forced.
    assert result["budget"]["budget_source"] == "not_requested"
    assert client.system_one.call_args.kwargs["budget_s"] is None
    client.request_budget.assert_not_called()
