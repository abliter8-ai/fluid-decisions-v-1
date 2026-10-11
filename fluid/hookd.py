"""Persistent Unix-socket hook daemon holding one warm Decisions (IP-006 section 4).

A long-lived process answers ``pre_tool`` and ``post_result`` events over a
Unix-domain socket with exactly the stdin hook's JSON contract, so a runtime hook
pays client start-up and warmup once rather than on every tool call. No TCP
listener is ever opened; the socket is created mode 0600 under ``FLUID_DATA_DIR``
unless ``FLUID_HOOK_SOCKET`` overrides the path. An operator or the example
launchd and systemd units start it; this plan installs neither.
"""
from __future__ import annotations

import contextlib
import http.server
import json
import os
import signal
import socketserver
from pathlib import Path
from typing import Any

from fluid.audit import data_directory
from fluid.cli import _service
from fluid.hook import handle


def socket_path() -> Path:
    override = os.environ.get("FLUID_HOOK_SOCKET")
    return Path(override).expanduser() if override else data_directory() / "hookd.sock"


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            length = 0
        body = self.rfile.read(length) if length > 0 else b""
        try:
            event = json.loads(body)
        except Exception:
            self._send({"verdict": "ask", "error": {"reason": "invalid_input", "message": "invalid JSON"}})
            return
        self._send(handle(event, self.server.service))  # type: ignore[attr-defined]

    def _send(self, result: dict[str, Any]) -> None:
        payload = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args: Any) -> None:  # the daemon keeps no request log
        return


class HookServer(socketserver.UnixStreamServer):
    """Serial Unix-socket server over one warm Decisions, one request at a time."""

    def __init__(self, path: Path, service: Any):
        self.service = service
        super().__init__(str(path), _Handler)


def serve(path: Path | None = None, service: Any | None = None) -> HookServer:
    """Create the server on ``path`` with a warm service. A stale socket is
    removed first; the socket is set 0600 and no TCP port is bound."""
    path = path or socket_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        if path.is_socket() or path.exists():
            path.unlink()
    service = service or _service()
    with contextlib.suppress(Exception):
        service.warmup()  # a cold daemon still serves; the first call warms instead
    server = HookServer(path, service)
    os.chmod(path, 0o600)
    return server


def main() -> int:  # pragma: no cover - exercised via serve() in tests
    server = serve()

    def _stop(signum: int, frame: Any) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _stop)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        service = getattr(server, "service", None)
        if service is not None:
            with contextlib.suppress(Exception):
                service.close()
        with contextlib.suppress(OSError):
            Path(str(server.server_address)).unlink()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
