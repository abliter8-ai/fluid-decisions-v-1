"""Hook daemon tests: socket round trip over a temporary socket, and the
in-process fallback when the daemon is absent (IP-006 section 4). No network."""
from __future__ import annotations

import shutil
import tempfile
import threading
from pathlib import Path

import pytest

from fluid import hook, hookd


@pytest.fixture
def short_socket():
    # AF_UNIX paths are capped (~104 bytes on macOS); pytest's tmp_path is too long,
    # so put the socket under a short temp directory of its own.
    directory = Path(tempfile.mkdtemp(prefix="fd", dir="/tmp"))
    try:
        yield directory / "s.sock"
    finally:
        shutil.rmtree(directory, ignore_errors=True)


class FakeService:
    """Stands in for a warm Decisions; records the events it is asked."""
    def __init__(self):
        self.seen: list[dict] = []
        self.warmed = False
        self.closed = False

    def warmup(self):
        self.warmed = True
        return {"verdict": "allow"}

    def recheck_pending(self, session_id):
        return {"rechecked": 0}

    def gate(self, tool_name, tool_input, **kwargs):
        self.seen.append({"event": "pre_tool", "tool_name": tool_name, "kwargs": kwargs})
        return {"verdict": "allow", "tool": tool_name}

    def post_result(self, result, **kwargs):
        self.seen.append({"event": "post_result", "result": result, "kwargs": kwargs})
        return {"verdict": "deny", "flagged": True, "result": result}

    def close(self):
        self.closed = True


def _serve(path, service):
    server = hookd.serve(path=path, service=service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_socket_round_trip_pre_and_post(short_socket):
    socket_path = short_socket
    service = FakeService()
    server, thread = _serve(socket_path, service)
    try:
        assert service.warmed  # the daemon warms once at startup
        assert Path(socket_path).is_socket()
        pre = hook._via_socket({"event": "pre_tool", "tool_name": "read_file", "tool_input": {"path": "x"},
                                "user_request": "read it", "session_id": "s"}, str(socket_path))
        assert pre["verdict"] == "allow" and pre["tool"] == "read_file"
        post = hook._via_socket({"event": "post_result", "session_id": "s", "result": "payload",
                                 "user_request": "read it"}, str(socket_path))
        assert post["verdict"] == "deny" and post["flagged"] is True
        # user_request rode through to the service on both events.
        assert service.seen[0]["kwargs"].get("user_request") == "read it"
        assert service.seen[1]["kwargs"].get("user_request") == "read it"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_resolve_uses_the_socket_when_present(short_socket, monkeypatch):
    socket_path = short_socket
    service = FakeService()
    server, thread = _serve(socket_path, service)
    monkeypatch.setattr(hook, "_socket_path", lambda: str(socket_path))
    try:
        result = hook.resolve({"event": "pre_tool", "tool_name": "write_file", "tool_input": {},
                               "session_id": "s"})
        assert result["tool"] == "write_file"
        assert any(e["event"] == "pre_tool" for e in service.seen)  # answered by the daemon, not in-process
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_via_socket_returns_none_when_absent(tmp_path):
    # No daemon: a transport failure must degrade to None (caller falls back), never allow.
    assert hook._via_socket({"event": "pre_tool", "tool_name": "x", "tool_input": {}},
                            str(tmp_path / "missing.sock")) is None


def test_resolve_falls_back_in_process_without_a_daemon(tmp_path, monkeypatch):
    monkeypatch.setattr(hook, "_socket_path", lambda: str(tmp_path / "missing.sock"))
    service = FakeService()
    monkeypatch.setattr(hook, "_service", lambda: service)
    result = hook.resolve({"event": "pre_tool", "tool_name": "read", "tool_input": {}, "session_id": "s"})
    assert result["verdict"] == "allow" and result["tool"] == "read"
    assert service.closed  # the in-process service is closed after the one-shot call
