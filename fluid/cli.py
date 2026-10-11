"""Command line surfaces for Fluid Decisions.

The CLI deliberately has a small JSON contract: input and output are JSON, while
the exit status communicates the decision class to shell hooks.
"""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import sys
from pathlib import Path
from typing import Any

from .service import Decisions


def _service() -> Any:
    return Decisions()


def _json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _read_json(value: str | None, filename: str | None, *, stdin: Any = None) -> Any:
    if value is not None and filename is not None:
        raise ValueError("use only one of --state and --file")
    if filename:
        text = Path(filename).read_text(encoding="utf-8")
    elif value is not None:
        text = value
    else:
        text = (sys.stdin if stdin is None else stdin).read()
    if not text.strip():
        raise ValueError("JSON input is required")
    return json.loads(text)


def _media(paths: list[str], kind: str) -> list[str]:
    result = []
    for filename in paths:
        content = Path(filename).read_bytes()
        mime = mimetypes.guess_type(filename)[0] or ("image/png" if kind == "image" else "audio/wav")
        result.append(f"data:{mime};base64,{base64.b64encode(content).decode('ascii')}")
    return result


def _result_exit(result: Any, command: str) -> int:
    if not isinstance(result, dict):
        return 2
    if result.get("complete") is False:
        return 2
    verdict = result.get("verdict")
    if command == "verify":
        findings = result.get("findings", result.get("claims", [])) or []
        if verdict in {"deny", "high"} or any(
            isinstance(item, dict) and item.get("verdict") in {"deny", "high", "flagged"}
            for item in findings
        ):
            return 1
    if verdict in {"deny", "high", "no_feasible_candidate", "invalid"}:
        return 1
    if verdict in {"ask", "incomplete", "abstain"}:
        return 2
    if result.get("error") or result.get("incomplete"):
        return 2
    if command == "health" and any(
        isinstance(result.get(lane), dict) and result[lane].get("error") for lane in ("max",)
    ):
        return 2
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fluid")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("classify", "route"):
        command = sub.add_parser(name)
        command.add_argument("--state")
        command.add_argument("--file")
        command.add_argument("--destinations", help="JSON object for route")
        command.add_argument("--image", action="append", default=[])
        command.add_argument("--audio")
    gate = sub.add_parser("gate")
    gate.add_argument("--tool", required=True)
    gate.add_argument("--input", required=True, help="tool input JSON")
    gate.add_argument("--user-request", default="")
    gate.add_argument("--session")
    gate.add_argument("--cwd", help="working directory for file-ownership facts (IP-011)")
    verify = sub.add_parser("verify")
    verify.add_argument("--source")
    verify.add_argument("--source-file")
    verify.add_argument("--claim", action="append", required=True)
    constraints = sub.add_parser("constraints")
    constraints.add_argument("--candidate")
    constraints.add_argument("--file", help="candidate JSON or text from a file")
    constraints.add_argument("--constraints", required=True, help="JSON list of {id, rule}")
    constraints.add_argument("--image", action="append", default=[])
    validity = sub.add_parser("validity")
    validity.add_argument("--packet")
    validity.add_argument("--file", help="packet text from a file")
    validity.add_argument("--image", action="append", default=[])
    validity.add_argument("--spec", required=True, help="JSON {types, checks:[{id,rule}], completeness_levels}")
    session = sub.add_parser("session")
    session_sub = session.add_subparsers(dest="session_command", required=True)
    clear = session_sub.add_parser("clear")
    clear.add_argument("--session", required=True)
    clear.add_argument("--reason", default="operator clearance")
    recheck = session_sub.add_parser("recheck")
    recheck.add_argument("--session", required=True)
    recheck.add_argument("--file", help="Original result text; stdin if omitted")
    proxy = sub.add_parser("proxy")
    proxy.add_argument("--listen", default="127.0.0.1:8765", help="loopback host:port only")
    proxy.add_argument("--upstream", default=None, help="gateway base URL; defaults to TEST_API_ENDPOINT")
    proxy.add_argument("--max-refusals", type=int, default=3,
                       help="recovery-loop passes per request before stopping")
    proxy.add_argument("--log-args", action="store_true",
                       help="add a masked args_excerpt to refused-call log lines")
    proxy.add_argument("--workdir", default=None,
                       help="working directory passed to the gate as cwd for file-ownership facts (IP-011)")
    sub.add_parser("health")
    return parser


def _run_proxy(args: argparse.Namespace) -> int:
    from fluid import proxy as proxy_module
    host, _, port = args.listen.rpartition(":")
    host = host or "127.0.0.1"
    if host not in proxy_module.LOOPBACK_HOSTS:
        _json({"verdict": "ask", "error": {"reason": "invalid_input", "message": "proxy binds loopback only"}})
        return 2
    upstream = args.upstream or os.environ.get("TEST_API_ENDPOINT")
    if not upstream:
        _json({"verdict": "ask", "error": {"reason": "invalid_input",
                                           "message": "--upstream or TEST_API_ENDPOINT is required"}})
        return 2
    log_args = bool(getattr(args, "log_args", False)) or os.environ.get("FD_PROXY_LOG_ARGS") == "1"
    workdir = getattr(args, "workdir", None) or os.environ.get("FD_PROXY_WORKDIR")
    return proxy_module.run((host, int(port)), upstream, os.environ.get("TEST_API_KEY", ""),
                            max_refusals=int(getattr(args, "max_refusals", 3)), log_args=log_args,
                            workdir=workdir)


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(argv)
        if args.command == "proxy":
            return _run_proxy(args)
        service = _service()
        try:
            if args.command in {"classify", "route"}:
                state = _read_json(args.state, args.file)
                images = _media(args.image, "image") if args.image else None
                audio = _media([args.audio], "audio")[0] if args.audio else None
                if args.command == "classify":
                    result = service.classify(state, images=images, audio=audio)
                else:
                    destinations = json.loads(args.destinations) if args.destinations else None
                    result = service.route(state, destinations=destinations, images=images, audio=audio)
            elif args.command == "gate":
                result = service.gate(args.tool, json.loads(args.input), user_request=args.user_request,
                                      session_id=args.session, cwd=args.cwd)
            elif args.command == "verify":
                source = args.source or (Path(args.source_file).read_text(encoding="utf-8") if args.source_file else None)
                if source is None:
                    raise ValueError("--source or --source-file is required")
                result = service.verify(source, args.claim)
            elif args.command == "constraints":
                raw = args.candidate if args.candidate is not None else (
                    Path(args.file).read_text(encoding="utf-8") if args.file else None)
                if raw is None:
                    raise ValueError("--candidate or --file is required")
                try:
                    candidate: Any = json.loads(raw)
                except json.JSONDecodeError:
                    candidate = raw
                images = _media(args.image, "image") if args.image else None
                result = service.constraints(candidate, json.loads(args.constraints), images=images)
            elif args.command == "validity":
                packet = args.packet if args.packet is not None else (
                    Path(args.file).read_text(encoding="utf-8") if args.file else "")
                images = _media(args.image, "image") if args.image else None
                result = service.validity(packet, json.loads(args.spec), images=images)
            elif args.command == "session" and args.session_command == "clear":
                result = service.clear_session(args.session, reason=args.reason)
            elif args.command == "session" and args.session_command == "recheck":
                if args.file:
                    text = Path(args.file).read_bytes().decode('utf-8')
                elif hasattr(sys.stdin, 'buffer'):
                    text = sys.stdin.buffer.read().decode('utf-8')
                else:
                    text = sys.stdin.read()
                result = service.post_result(text, session_id=args.session, recheck=True)
            else:
                result = service.health()
        finally:
            service.close()
        _json(result)
        return _result_exit(result, args.command)
    except Exception as exc:
        _json({"verdict": "ask", "error": {"reason": "invalid_input", "message": str(exc)}})
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
