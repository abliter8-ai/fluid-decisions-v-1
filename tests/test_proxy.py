"""Offline tests for fluid proxy (CR-010). No network: a fake upstream via an
httpx MockTransport and a fake Decisions stub. Six behaviours from the spec."""
from __future__ import annotations

import json
import threading

import httpx

from fluid import proxy


class FakeDecisions:
    def __init__(self, gate_verdict="allow", post=None, gate_raises=False, gate_map=None):
        self.gate_verdict = gate_verdict
        self.gate_map = gate_map or {}
        self.post = post or {}
        self.gate_raises = gate_raises
        self.gate_calls: list = []
        self.post_calls: list = []

    def gate(self, name, args, *, user_request="", session_id=None, cwd=None):
        self.gate_calls.append((name, args, session_id, cwd))
        if self.gate_raises:
            raise RuntimeError("lane down")
        verdict = self.gate_map.get(name, self.gate_verdict)
        return {"verdict": verdict, "reasons": [] if verdict == "allow" else ["risk"]}

    def post_result(self, content, *, session_id=None, tool_name="", user_request=""):
        self.post_calls.append((content, tool_name, session_id))
        return self.post.get(content, {"verdict": "allow"})

    def warmup(self):
        return {}

    def close(self):
        pass


def _choice_with_tool_call(name="read_file", args='{"path":"x"}', call_id="c1", content=None):
    return {"index": 0, "finish_reason": "tool_calls", "message": {
        "role": "assistant", "content": content,
        "tool_calls": [{"id": call_id, "type": "function",
                        "function": {"name": name, "arguments": args}}]}}


def test_allowed_tool_call_passes_through_byte_identical():
    dec = FakeDecisions("allow")
    choice = _choice_with_tool_call()
    out = proxy.apply_response_guard(choice, dec, "s", "do it", log=lambda r: None)
    assert out["message"]["tool_calls"] == choice["message"]["tool_calls"]
    assert out["finish_reason"] == "tool_calls"


def test_refused_tool_call_is_removed_and_noted():
    dec = FakeDecisions("deny")
    choice = _choice_with_tool_call(name="send_money")
    out = proxy.apply_response_guard(choice, dec, "s", "do it", log=lambda r: None)
    assert "tool_calls" not in out["message"]                # all refused -> content-only
    assert "fluid refused tool call send_money: deny" in out["message"]["content"]
    assert out["finish_reason"] == "stop"                    # loop ends honestly


def test_tool_result_with_redacted_text_is_replaced_before_forward():
    dec = FakeDecisions(post={"INJECT": {"verdict": "deny", "on_confirmed": "redact",
                                         "redacted_text": "[fluid-decisions withheld 1 unit]",
                                         "units_redacted": 1}})
    messages = [{"role": "assistant", "tool_calls": [{"id": "c1", "function": {"name": "read_file"}}]},
                {"role": "tool", "tool_call_id": "c1", "content": "INJECT"}]
    out = proxy.apply_request_guard(messages, dec, "s", set(), "task", log=lambda r: None)
    assert out[1]["content"] == "[fluid-decisions withheld 1 unit]"
    assert dec.post_calls[0][1] == "read_file"               # tool name resolved from the assistant call


def test_same_tool_result_is_not_rescored_next_request():
    dec = FakeDecisions()
    messages = [{"role": "tool", "tool_call_id": "c1", "content": "ordinary output"}]
    seen: set = set()
    proxy.apply_request_guard(messages, dec, "s", seen, "task", log=lambda r: None)
    proxy.apply_request_guard(messages, dec, "s", seen, "task", log=lambda r: None)
    assert len(dec.post_calls) == 1                          # scored once, remembered by the seen-set


def test_gate_exception_fails_closed_to_refusal():
    dec = FakeDecisions(gate_raises=True)
    choice = _choice_with_tool_call(name="run_shell")
    out = proxy.apply_response_guard(choice, dec, "s", "do it", log=lambda r: None)
    assert "tool_calls" not in out["message"]
    assert "fluid refused tool call run_shell: ask" in out["message"]["content"]
    assert "gate_error" in out["message"]["content"]


def test_streaming_request_yields_a_valid_single_chunk_stream():
    # Fake upstream returns a two-chunk SSE content reply; the proxy must reassemble
    # and re-emit one chunk plus [DONE], with the guard applied.
    sse = ("data: " + json.dumps({"id": "r1", "model": "m",
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": "Hello "}}]}) + "\n\n"
           "data: " + json.dumps({"id": "r1", "model": "m",
            "choices": [{"index": 0, "delta": {"content": "world"}, "finish_reason": "stop"}]}) + "\n\n"
           "data: [DONE]\n\n")

    def upstream(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse.encode(), headers={"content-type": "text/event-stream"})

    server = proxy.ProxyServer(("127.0.0.1", 0), "http://upstream.invalid", "k",
                               FakeDecisions(), log=lambda r: None)
    server.client = httpx.Client(transport=httpx.MockTransport(upstream))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[0], server.server_address[1]
        resp = httpx.post(f"http://{host}:{port}/v1/chat/completions",
                          json={"stream": True, "messages": [{"role": "user", "content": "hi"}]},
                          timeout=10)
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/event-stream"
        lines = [line for line in resp.text.splitlines() if line.startswith("data:")]
        assert lines[-1].strip() == "data: [DONE]"
        chunk = json.loads(lines[0][len("data:"):].strip())
        assert chunk["choices"][0]["delta"]["content"] == "Hello world"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_proxy_refuses_non_loopback_bind():
    import pytest
    with pytest.raises(ValueError):
        proxy.ProxyServer(("0.0.0.0", 9), "http://x", "k", FakeDecisions())


# ---- recovery loop + argument logging (CR-010 follow-up) ----

def _resp_tool(name, call_id="c1", content=None):
    return {"id": "r", "model": "m", "choices": [{"index": 0, "finish_reason": "tool_calls",
            "message": {"role": "assistant", "content": content,
                        "tool_calls": [{"id": call_id, "type": "function",
                                        "function": {"name": name, "arguments": "{}"}}]}}]}


def _resp_content(text):
    return {"id": "r", "model": "m", "choices": [{"index": 0, "finish_reason": "stop",
            "message": {"role": "assistant", "content": text}}]}


def _scripted(responses):
    state = {"i": 0}

    def handler(request):
        i = min(state["i"], len(responses) - 1)
        state["i"] += 1
        return httpx.Response(200, json=responses[i])
    return handler


def _server(decisions, responses, max_refusals=3, log_args=False, logs=None):
    srv = proxy.ProxyServer(("127.0.0.1", 0), "http://up.invalid", "k", decisions,
                            log=(logs.append if logs is not None else (lambda r: None)),
                            max_refusals=max_refusals, log_args=log_args)
    srv.client = httpx.Client(transport=httpx.MockTransport(_scripted(responses)))
    return srv


def test_recovery_loop_returns_the_allowed_retry_with_notes_prepended():
    dec = FakeDecisions(gate_map={"send_money": "deny", "read_file": "allow"})
    logs: list = []
    srv = _server(dec, [_resp_tool("send_money"), _resp_tool("read_file", "c2")], logs=logs)
    try:
        status, resp = srv._guarded_exchange({}, [{"role": "user", "content": "do it"}],
                                             "s", "do it", set(), False)
    finally:
        srv.server_close()
    assert status == 200
    message = resp["choices"][0]["message"]
    assert [c["function"]["name"] for c in message["tool_calls"]] == ["read_file"]   # the retry's allowed call
    assert message["content"].startswith("fluid refused tool call send_money: deny")  # note prepended
    assert sum(1 for r in logs if r.get("event") == "refusal_retry") == 1


def test_recovery_budget_exhausted_returns_content_only():
    dec = FakeDecisions(gate_map={"send_money": "deny"})
    srv = _server(dec, [_resp_tool("send_money")], max_refusals=1)
    try:
        status, resp = srv._guarded_exchange({}, [{"role": "user", "content": "x"}],
                                             "s", "x", set(), False)
    finally:
        srv.server_close()
    choice = resp["choices"][0]
    assert "tool_calls" not in choice["message"]            # no calls survive
    assert choice["finish_reason"] == "stop"
    assert "fluid refused tool call send_money" in choice["message"]["content"]


def test_synthetic_tool_messages_are_marked_seen_and_never_scored():
    dec = FakeDecisions(gate_map={"send_money": "deny", "read_file": "allow"})
    seen: set = set()
    srv = _server(dec, [_resp_tool("send_money"), _resp_tool("read_file", "c2")])
    try:
        srv._guarded_exchange({}, [{"role": "user", "content": "x"}], "s", "x", seen, False)
    finally:
        srv.server_close()
    assert dec.post_calls == []          # the exchange never calls post_result (no re-scoring)
    assert len(seen) == 1                # the one synthetic tool message's hash was marked


def test_proxy_passes_workdir_to_the_gate_as_cwd():
    # --workdir threads through to Decisions.gate as cwd for file-ownership facts (IP-011).
    dec = FakeDecisions("allow")
    srv = proxy.ProxyServer(("127.0.0.1", 0), "http://up.invalid", "k", dec,
                            log=lambda r: None, workdir="/work/project")
    srv.client = httpx.Client(transport=httpx.MockTransport(_scripted([_resp_tool("read_file")])))
    try:
        srv._guarded_exchange({}, [{"role": "user", "content": "do it"}], "s", "do it", set(), False)
    finally:
        srv.server_close()
    assert dec.gate_calls and dec.gate_calls[0][3] == "/work/project"


def test_refused_gate_line_has_sha256_and_optional_masked_excerpt():
    import hashlib
    message = {"role": "assistant", "tool_calls": [{"id": "c1", "type": "function",
               "function": {"name": "run_sql", "arguments": '{"query": "DROP TABLE users"}'}}]}
    # default: sha256 present (over the canonical arguments), no excerpt.
    off: list = []
    proxy.gate_message_calls(message, FakeDecisions("deny"), "s", "t", off.append, log_args=False)
    rec = off[0]
    assert rec["verdict"] == "deny" and len(rec["args_sha256"]) == 64 and "args_excerpt" not in rec
    assert rec["args_sha256"] == hashlib.sha256('{"query":"DROP TABLE users"}'.encode()).hexdigest()
    # with the flag: excerpt present and the dangerous token masked by a gate-pack rule.
    on: list = []
    proxy.gate_message_calls(message, FakeDecisions("deny"), "s", "t", on.append, log_args=True)
    rec = on[0]
    assert "args_excerpt" in rec and "DROP TABLE" not in rec["args_excerpt"] and "***" in rec["args_excerpt"]
