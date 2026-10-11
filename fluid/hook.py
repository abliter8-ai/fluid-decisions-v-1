"""Stdin hook adapter for pre-tool and post-result events.

Tries the persistent daemon on the Unix socket first and falls back to an
in-process service when the socket is absent or refuses (IP-006 section 4). A
missing daemon degrades to today's in-process behaviour, never to allow.
"""
from __future__ import annotations

import json
import os
import socket
import sys
from typing import Any

from .cli import _result_exit, _service


def _ask(reason: str, message: str) -> dict[str, Any]:
    return {"verdict": "ask", "error": {"reason": reason, "message": message}}


def _socket_path() -> str | None:
    override = os.environ.get("FLUID_HOOK_SOCKET")
    if override:
        return override
    from .audit import data_directory
    return str(data_directory() / "hookd.sock")


def _socket_timeout() -> float:
    raw = os.environ.get("FLUID_HOOK_TIMEOUT")
    try:
        value = float(raw) if raw is not None else 15.0
    except ValueError:
        return 15.0
    return value if value > 0 else 15.0


def _via_socket(event: Any, path: str) -> dict[str, Any] | None:
    """Answer through the daemon over ``path``; return ``None`` on any failure so
    the caller falls back in-process. Never returns allow on a transport error."""
    try:
        body = json.dumps(event, ensure_ascii=False).encode("utf-8")
        request = (b"POST / HTTP/1.0\r\nContent-Type: application/json\r\nContent-Length: "
                   + str(len(body)).encode("ascii") + b"\r\n\r\n" + body)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(_socket_timeout())
            sock.connect(path)
            sock.sendall(request)
            chunks: list[bytes] = []
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
        payload = b"".join(chunks).partition(b"\r\n\r\n")[2]
        result = json.loads(payload)
        return result if isinstance(result, dict) else None
    except Exception:
        return None


def resolve(event: Any) -> dict[str, Any]:
    """Socket first, then in-process. The daemon and the in-process path share the
    exact JSON contract, so the fallback is transparent to the runtime."""
    path = _socket_path()
    if path and os.path.exists(path):
        result = _via_socket(event, path)
        if isinstance(result, dict):
            return result
    service = None
    try:
        service = _service()
        return handle(event, service)
    except Exception as exc:
        return _ask("service_failure", str(exc))
    finally:
        if service is not None:
            service.close()


def handle(event: Any, service: Any | None = None) -> dict[str, Any]:
    if not isinstance(event, dict):
        return _ask("invalid_input", "hook event must be a JSON object")
    kind = event.get("event")
    try:
        if kind == "pre_tool":
            if not isinstance(event.get("tool_name"), str) or not isinstance(event.get("tool_input"), dict):
                return _ask("invalid_input", "pre_tool requires tool_name and object tool_input")
            svc = service or _service()
            # A warm service (the daemon) rechecks any pending result before the
            # gate (IP-007 §2); the one-shot hook's fresh service has nothing cached,
            # so this is a no-op for it.
            svc.recheck_pending(event.get("session_id"))
            # The payload's working directory (Claude Code sends `cwd`) feeds the
            # file-ownership facts (IP-011); absent, the ownership exemption never
            # applies and the gate behaves as before.
            cwd = event.get("cwd") or event.get("working_directory")
            return svc.gate(
                event["tool_name"], event["tool_input"], user_request=event.get("user_request", ""),
                session_id=event.get("session_id"), cwd=cwd if isinstance(cwd, str) else None
            )
        if kind == "post_result":
            if not isinstance(event.get("result"), str) or not isinstance(event.get("session_id"), str):
                return _ask("invalid_input", "post_result requires session_id and string result")
            return (service or _service()).post_result(
                event["result"], session_id=event["session_id"], tool_name=event.get("tool_name", ""),
                user_request=event.get("user_request", ""))
        return _ask("invalid_input", "event must be pre_tool or post_result")
    except Exception as exc:
        return _ask("service_failure", str(exc))


def main() -> int:
    try:
        event = json.loads(sys.stdin.read())
    except Exception as exc:
        result = _ask("invalid_input", f"invalid JSON: {exc}")
        print(json.dumps(result, separators=(",", ":")))
        return 2
    result = resolve(event)
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return _result_exit(result, 'hook')


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
