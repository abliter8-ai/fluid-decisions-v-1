from __future__ import annotations

import io
import json

from fluid import cli, hook


class FakeDecisions:
    def __init__(self, verdict="allow"):
        self.verdict = verdict
        self.closed = False

    def classify(self, state, **kwargs):
        return {"verdict": self.verdict, "kind": "classify", "state": state}

    def route(self, state, **kwargs):
        return {"verdict": self.verdict, "kind": "route"}

    def gate(self, tool, data, **kwargs):
        return {"verdict": self.verdict, "tool": tool, "input": data}

    def recheck_pending(self, session_id):
        return {"rechecked": 0}

    def verify(self, source, claims):
        return {"verdict": self.verdict, "claims": claims}

    def constraints(self, candidate, constraints, images=None):
        return {"verdict": self.verdict, "job": "constraints", "failing": []}

    def validity(self, packet, spec, images=None):
        return {"verdict": self.verdict, "job": "validity"}

    def health(self):
        return {"verdict": "clean", "healthy": True}

    def post_result(self, result, **kwargs):
        return {"verdict": self.verdict, "result": result}

    def close(self):
        self.closed = True


def test_cli_classify_reads_json_and_returns_json(monkeypatch, capsys):
    fake = FakeDecisions()
    monkeypatch.setattr(cli, "Decisions", lambda: fake)
    assert cli.main(["classify", "--state", '{"text":"hello"}']) == 0
    assert json.loads(capsys.readouterr().out)["state"] == {"text": "hello"}
    assert fake.closed


def test_cli_gate_deny_exit(monkeypatch, capsys):
    monkeypatch.setattr(cli, "Decisions", lambda: FakeDecisions("deny"))
    assert cli.main(["gate", "--tool", "shell", "--input", '{"command":"rm"}']) == 1
    assert json.loads(capsys.readouterr().out)["verdict"] == "deny"


def test_cli_incomplete_deny_still_returns_incomplete(monkeypatch, capsys):
    class Incomplete(FakeDecisions):
        def gate(self, tool, data, **kwargs):
            return {"verdict": "deny", "complete": False}

    monkeypatch.setattr(cli, "Decisions", Incomplete)
    assert cli.main(["gate", "--tool", "read", "--input", "{}"]) == 2
    assert json.loads(capsys.readouterr().out)["complete"] is False


def test_cli_session_clear(monkeypatch, capsys):
    class Clearable(FakeDecisions):
        def clear_session(self, session_id, reason="operator clearance"):
            return {"verdict": "allow", "taint_cleared": True, "session": session_id, "reason": reason}

    monkeypatch.setattr(cli, "Decisions", Clearable)
    assert cli.main(["session", "clear", "--session", "s1", "--reason", "false positive"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["taint_cleared"] is True and result["reason"] == "false positive"


def test_cli_recheck_original_file_or_stdin(monkeypatch, capsys, tmp_path):
    seen = []

    class Recheckable(FakeDecisions):
        def post_result(self, text, **kwargs):
            seen.append((text, kwargs))
            return {"verdict": "allow", "checked": True, "resolved": True,
                    "check": {"status": "scored_clean"}}

    monkeypatch.setattr(cli, "Decisions", Recheckable)
    original = tmp_path / 'original.txt'
    original.write_bytes(b'original\r\ntext\r\n')
    assert cli.main(['session', 'recheck', '--session', 's', '--file', str(original)]) == 0
    assert seen[-1] == ('original\r\ntext\r\n', {'session_id': 's', 'recheck': True})
    assert json.loads(capsys.readouterr().out)['resolved']
    monkeypatch.setattr(cli.sys, 'stdin', io.TextIOWrapper(io.BytesIO(b'original\r\ntext\r\n')))
    assert cli.main(['session', 'recheck', '--session', 's']) == 0
    assert seen[-1][0] == 'original\r\ntext\r\n'


def test_post_hook_unscored_release_is_visible_and_exit_zero(monkeypatch, capsys):
    class Unscored(FakeDecisions):
        def post_result(self, text, **kwargs):
            return {'verdict': 'allow', 'complete': True, 'checked': False,
                    'check': {'status': 'unscored', 'reason': 'timeout'}}

    monkeypatch.setattr(hook, '_service', Unscored)
    monkeypatch.setattr(hook, '_socket_path', lambda: None)  # force the in-process fallback
    monkeypatch.setattr(hook.sys, 'stdin', io.StringIO(json.dumps(
        {'event': 'post_result', 'session_id': 's', 'result': 'ordinary text'})))
    assert hook.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert not result['checked'] and result['check']['reason'] == 'timeout'


def test_cli_constraints_no_feasible_candidate_exits_one(monkeypatch, capsys):
    class NoFeasible(FakeDecisions):
        def constraints(self, candidate, constraints, images=None):
            return {"verdict": "no_feasible_candidate", "failing": [{"id": "x", "severity": "major"}]}

    monkeypatch.setattr(cli, "Decisions", NoFeasible)
    assert cli.main(["constraints", "--candidate", "{}", "--constraints",
                     '[{"id":"x","rule":"r"}]']) == 1
    assert json.loads(capsys.readouterr().out)["verdict"] == "no_feasible_candidate"


def test_cli_validity_abstain_exits_two(monkeypatch, capsys):
    class Abstain(FakeDecisions):
        def validity(self, packet, spec, images=None):
            return {"verdict": "abstain"}

    monkeypatch.setattr(cli, "Decisions", Abstain)
    spec = '{"types":["a"],"checks":[{"id":"c","rule":"r"}],"completeness_levels":["x"]}'
    assert cli.main(["validity", "--packet", "a filled form", "--spec", spec]) == 2


def test_cli_malformed_input_is_ask(monkeypatch, capsys):
    monkeypatch.setattr(cli, "Decisions", lambda: FakeDecisions())
    assert cli.main(["classify", "--state", "{"]) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["verdict"] == "ask"


def test_hook_selects_pre_and_post_without_network():
    fake = FakeDecisions()
    assert hook.handle({"event": "pre_tool", "tool_name": "read", "tool_input": {}}, fake)["tool"] == "read"
    assert hook.handle({"event": "post_result", "session_id": "s", "result": "ok"}, fake)["result"] == "ok"
    assert hook.handle({"event": "unknown"}, fake)["verdict"] == "ask"


def test_hook_main_invalid_json(monkeypatch, capsys):
    monkeypatch.setattr(hook.sys, "stdin", io.StringIO("{"))
    assert hook.main() == 2
    assert json.loads(capsys.readouterr().out)["verdict"] == "ask"
