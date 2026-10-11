"""fluid proxy (CR-010): an OpenAI-compatible guard in the path between an
open-source agent harness and the maintainer's worker gateway.

Loopback only. The harness points at the proxy as its model endpoint and talks to
it exactly as it would to the gateway. The proxy forwards to the gateway and
applies the guard on the way through:

- request path (harness -> proxy -> gateway): every unseen ``role: tool`` message
  is checked by ``Decisions.post_result``; a confirmed unit is replaced by its
  redacted text, a withheld result by a fixed marker, before the request is
  forwarded.
- response path (gateway -> proxy -> harness): every ``tool_call`` the model
  returns is checked by ``Decisions.gate``; an ``ask`` or ``deny`` call is removed
  and a refusal note is appended to the assistant content.

The model never decides the action; policy code does, exactly as the hook and
GuardedRuntime do. No guard behaviour is changed here — this is a transport
adapter. Fail closed: a lane error or a gate exception refuses the tool call; an
upstream error is returned unchanged.
"""
from __future__ import annotations

import hashlib
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, cast

import httpx

from fluid.service import Decisions

DEFAULT_PORT = 8765
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _log_stderr(record: dict[str, Any]) -> None:
    sys.stderr.write(json.dumps(record, ensure_ascii=False) + "\n")
    sys.stderr.flush()


def _text(content: Any) -> str:
    """The text of an OpenAI message content, which may be a string or a list of
    typed parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content
                       if isinstance(part, dict) and part.get("type") == "text")
    return "" if content is None else str(content)


def last_user_request(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            return _text(message.get("content"))
    return ""


def session_id_for(messages: list[dict[str, Any]], header: str | None) -> str:
    """X-Fluid-Session when present, else the sha256 of the first user message
    truncated to 16 hex, so taint carries across the turns of one conversation."""
    if isinstance(header, str) and header.strip():
        return header.strip()[:64]
    for message in messages:
        if isinstance(message, dict) and message.get("role") == "user":
            return hashlib.sha256(_text(message.get("content")).encode()).hexdigest()[:16]
    return hashlib.sha256(b"").hexdigest()[:16]


def _tool_name(messages: list[dict[str, Any]], tool_call_id: Any) -> str:
    for message in messages:
        if isinstance(message, dict) and message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                if isinstance(call, dict) and call.get("id") == tool_call_id:
                    return str((call.get("function") or {}).get("name", ""))
    return ""


def apply_request_guard(messages: list[dict[str, Any]], decisions: Any, session_id: str,
                        seen: set[str], user_request: str,
                        log: Callable[[dict[str, Any]], None] = _log_stderr) -> list[dict[str, Any]]:
    """Check each unseen tool-result message and redact or withhold it in place.
    The per-session seen-set stops a result being re-scored on later turns."""
    out: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, dict) and message.get("role") == "tool":
            content = _text(message.get("content"))
            digest = hashlib.sha256(content.encode()).hexdigest()
            if digest not in seen:
                seen.add(digest)
                name = _tool_name(messages, message.get("tool_call_id"))
                try:
                    post = decisions.post_result(content, session_id=session_id,
                                                 tool_name=name, user_request=user_request)
                except Exception as exc:  # fail closed: surface, do not pass raw
                    post = {"verdict": "ask", "reasons": [f"post_result_error:{type(exc).__name__}"]}
                if isinstance(post.get("redacted_text"), str) and post.get("units_redacted"):
                    message = {**message, "content": post["redacted_text"]}
                    log({"event": "redaction", "session": session_id, "tool": name,
                         "units_redacted": post.get("units_redacted")})
                elif post.get("verdict") == "deny":
                    reason = ",".join(str(r) for r in post.get("reasons", [])) or "deny"
                    message = {**message, "content": f"[withheld by fluid: {reason}]"}
                    log({"event": "redaction", "session": session_id, "tool": name,
                         "withheld": True, "reason": reason})
        out.append(message)
    return out


def _canonical_args(args: dict[str, Any]) -> str:
    try:
        return json.dumps(args, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError):
        return str(args)


_MASK_PATTERNS: list[Any] | None = None


def _mask_patterns() -> list[Any]:
    """The gate pack's static-rule and secret-path regexes, compiled once, used to
    mask secret-looking tokens out of a logged argument excerpt."""
    global _MASK_PATTERNS
    if _MASK_PATTERNS is not None:
        return _MASK_PATTERNS
    import re

    from fluid.policy import load_pack
    raw: list[str] = []
    try:
        policy = load_pack("gate")["policy"]
        for rule in policy.get("static_denials", {}).get("rules", []):
            if isinstance(rule, dict) and isinstance(rule.get("pattern"), str):
                raw.append(rule["pattern"])
        for pattern in policy.get("provenance", {}).get("secret_paths", []):
            if isinstance(pattern, str):
                raw.append(pattern)
    except Exception:
        raw = []
    compiled = []
    for pattern in raw:
        try:
            compiled.append(re.compile(pattern, re.IGNORECASE))
        except re.error:
            continue
    _MASK_PATTERNS = compiled
    return compiled


def _args_excerpt(args: dict[str, Any], limit: int = 160) -> str:
    excerpt = _canonical_args(args)[:limit]
    for pattern in _mask_patterns():
        excerpt = pattern.sub("***", excerpt)
    return excerpt


def gate_message_calls(message: dict[str, Any], decisions: Any, session_id: str, user_request: str,
                       log: Callable[[dict[str, Any]], None] = _log_stderr,
                       log_args: bool = False, workdir: str | None = None) -> tuple[list[Any], list[dict[str, Any]]]:
    """Gate each tool call in an assistant message. Returns (kept_calls, refusals),
    each refusal a dict with the call, name, verdict, reasons and the note. A gate
    exception fails closed to ask. An ask/deny gate line carries args_sha256 and,
    when log_args, a masked args_excerpt."""
    import hashlib as _hashlib
    kept: list[Any] = []
    refusals: list[dict[str, Any]] = []
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {} if isinstance(call, dict) else {}
        name = str(function.get("name", ""))
        try:
            args = json.loads(function.get("arguments") or "{}")
            if not isinstance(args, dict):
                args = {"_arguments": args}
        except (ValueError, TypeError):
            args = {}
        try:
            decision = decisions.gate(name, args, user_request=user_request,
                                      session_id=session_id, cwd=workdir)
            verdict = decision.get("verdict")
            reasons = [str(r) for r in decision.get("reasons", [])]
        except Exception as exc:  # fail closed
            verdict, reasons = "ask", [f"gate_error:{type(exc).__name__}"]
        record: dict[str, Any] = {"event": "gate", "session": session_id, "tool": name, "verdict": verdict}
        if verdict == "allow":
            kept.append(call)
        else:
            record["reasons"] = reasons
            record["args_sha256"] = _hashlib.sha256(_canonical_args(args).encode()).hexdigest()
            if log_args:
                record["args_excerpt"] = _args_excerpt(args)
            refusals.append({"call": call, "name": name, "verdict": verdict, "reasons": reasons,
                             "note": f"fluid refused tool call {name}: {verdict} ({', '.join(reasons)})"})
        log(record)
    return kept, refusals


def apply_response_guard(choice: dict[str, Any], decisions: Any, session_id: str,
                         user_request: str,
                         log: Callable[[dict[str, Any]], None] = _log_stderr,
                         log_args: bool = False) -> dict[str, Any]:
    """Gate each tool call the model returned; drop a refused call and append its
    note. If every call is refused the message becomes content-only and the choice
    finishes with ``stop``. (The recovery loop in handle_chat uses
    gate_message_calls directly; this shapes a single choice.)"""
    message_obj = choice.get("message")
    message: dict[str, Any] = message_obj if isinstance(message_obj, dict) else {}
    if not (message.get("tool_calls") or []):
        return choice
    kept, refusals = gate_message_calls(message, decisions, session_id, user_request, log, log_args)
    new_message = dict(message)
    if kept:
        new_message["tool_calls"] = kept
    else:
        new_message.pop("tool_calls", None)
    if refusals:
        base = _text(message.get("content")) or ""
        new_message["content"] = base + ("\n" if base else "") + "\n".join(r["note"] for r in refusals)
    new_choice = dict(choice)
    new_choice["message"] = new_message
    if not kept:
        new_choice["finish_reason"] = "stop"
    return new_choice


def reassemble_stream(raw: bytes) -> dict[str, Any]:
    """Collapse an upstream SSE chat-completions stream into one response object
    (choices[].message), so the guard can be applied once over the whole message."""
    message: dict[str, Any] = {"role": "assistant", "content": ""}
    tool_calls: dict[int, dict[str, Any]] = {}
    template: dict[str, Any] = {"id": "", "object": "chat.completion", "model": "", "choices": []}
    finish_reason = "stop"
    for line in raw.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if payload == "[DONE]" or not payload:
            continue
        try:
            chunk = json.loads(payload)
        except ValueError:
            continue
        for key in ("id", "model"):
            if chunk.get(key):
                template[key] = chunk[key]
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            if isinstance(delta.get("content"), str):
                message["content"] += delta["content"]
            for call in delta.get("tool_calls") or []:
                index = call.get("index", 0)
                slot = tool_calls.setdefault(index, {"id": "", "type": "function",
                                                     "function": {"name": "", "arguments": ""}})
                if call.get("id"):
                    slot["id"] = call["id"]
                fn = call.get("function") or {}
                if fn.get("name"):
                    slot["function"]["name"] += fn["name"]
                if isinstance(fn.get("arguments"), str):
                    slot["function"]["arguments"] += fn["arguments"]
            if choice.get("finish_reason"):
                finish_reason = str(choice["finish_reason"])
    if tool_calls:
        message["tool_calls"] = [tool_calls[i] for i in sorted(tool_calls)]
    if not message["content"] and "tool_calls" in message:
        message["content"] = None
    template["choices"] = [{"index": 0, "message": message, "finish_reason": finish_reason}]
    return template


def _single_chunk_stream(response: dict[str, Any]) -> bytes:
    """Re-emit a guarded response as one SSE chunk plus [DONE]."""
    choice = (response.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    delta: dict[str, Any] = {"role": "assistant"}
    if message.get("content") is not None:
        delta["content"] = message.get("content")
    if message.get("tool_calls"):
        delta["tool_calls"] = message["tool_calls"]
    chunk = {"id": response.get("id", ""), "object": "chat.completion.chunk",
             "model": response.get("model", ""),
             "choices": [{"index": 0, "delta": delta, "finish_reason": choice.get("finish_reason", "stop")}]}
    return (f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n" + "data: [DONE]\n\n").encode("utf-8")


def _message_of(choice: dict[str, Any]) -> dict[str, Any]:
    message = choice.get("message")
    return message if isinstance(message, dict) else {}


def _normalise_upstream(upstream: str, path: str) -> str:
    base = upstream.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3].rstrip("/")
    return base + path


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: Any) -> None:  # the proxy logs its own JSON lines
        return

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json_error(self, status: int, message: str) -> None:
        self._send(status, json.dumps({"error": {"message": message}}).encode(), "application/json")

    def do_GET(self) -> None:
        if self.path.rstrip("/") == "/v1/models":
            self._passthrough_get()
        else:
            self._json_error(404, "not found")

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/v1/chat/completions":
            self._json_error(404, "not found")
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw) if raw else {}
            assert isinstance(body, dict)
        except (ValueError, AssertionError):
            self._json_error(400, "invalid JSON body")
            return
        cast("ProxyServer", self.server).handle_chat(self, body)

    def _passthrough_get(self) -> None:
        server = cast("ProxyServer", self.server)
        url = _normalise_upstream(server.upstream, "/v1/models")
        try:
            resp = server.client.get(url, headers=server.auth_headers())
        except httpx.HTTPError as exc:
            self._json_error(502, f"upstream error: {type(exc).__name__}")
            return
        self._send(resp.status_code, resp.content, resp.headers.get("content-type", "application/json"))


class ProxyServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, listen: tuple[str, int], upstream: str, api_key: str,
                 decisions: Decisions, log: Callable[[dict[str, Any]], None] = _log_stderr,
                 max_refusals: int = 3, log_args: bool = False, workdir: str | None = None):
        if listen[0] not in LOOPBACK_HOSTS:
            raise ValueError("fluid proxy binds loopback only")
        super().__init__(listen, _Handler)
        self.upstream = upstream
        self.api_key = api_key
        self.decisions = decisions
        self.log = log
        self.max_refusals = max_refusals
        self.log_args = log_args
        self.workdir = workdir  # passed to the gate as cwd for file-ownership facts (IP-011)
        self.client = httpx.Client(timeout=httpx.Timeout(120.0))
        self._seen: dict[str, set[str]] = {}

    def auth_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _upstream_message(self, body: dict[str, Any], messages: list[dict[str, Any]],
                          stream: bool) -> tuple[int, Any]:
        """One upstream call. Returns (200, response-dict) on success, or
        (status, raw-bytes) on an upstream error for pass-through."""
        url = _normalise_upstream(self.upstream, "/v1/chat/completions")
        forward = {**body, "messages": messages}
        if stream:
            forward["stream"] = True
            with self.client.stream("POST", url, json=forward, headers=self.auth_headers()) as resp:
                raw = resp.read()
                if resp.status_code >= 400:
                    return resp.status_code, raw
                return 200, reassemble_stream(raw)
        forward.pop("stream", None)
        resp = self.client.post(url, json=forward, headers=self.auth_headers())
        if resp.status_code >= 400:
            return resp.status_code, resp.content
        return 200, resp.json()

    def handle_chat(self, handler: _Handler, body: dict[str, Any]) -> None:
        raw_messages = body.get("messages")
        messages: list[dict[str, Any]] = raw_messages if isinstance(raw_messages, list) else []
        session_id = session_id_for(messages, handler.headers.get("X-Fluid-Session"))
        user_request = last_user_request(messages)
        seen = self._seen.setdefault(session_id, set())
        guarded_messages = apply_request_guard(messages, self.decisions, session_id, seen, user_request, self.log)
        body = {**body, "messages": guarded_messages}
        stream = bool(body.get("stream"))
        try:
            status, response = self._guarded_exchange(body, guarded_messages, session_id,
                                                       user_request, seen, stream)
        except httpx.HTTPError as exc:
            handler._json_error(502, f"upstream error: {type(exc).__name__}")
            return
        if status >= 400:  # upstream error passed straight through
            handler._send(status, response, "application/json")
            return
        if stream:
            handler._send(200, _single_chunk_stream(response), "text/event-stream")
        else:
            handler._send(200, json.dumps(response, ensure_ascii=False).encode(), "application/json")

    def _guarded_exchange(self, body: dict[str, Any], messages: list[dict[str, Any]], session_id: str,
                          user_request: str, seen: set[str], stream: bool) -> tuple[int, Any]:
        """Forward, gate the returned tool calls, and — when any are refused — loop:
        hand the model synthetic tool results for the refused calls and ask again,
        up to max_refusals, so the harness can recover instead of stopping. The
        accumulated refusal notes are prepended to whatever is finally returned."""
        status, response = self._upstream_message(body, messages, stream)
        if status >= 400:
            return status, response
        notes: list[str] = []
        convo = list(messages)
        for attempt in range(self.max_refusals + 1):
            choices = response.get("choices")
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                return 200, response
            choice = choices[0]
            message = _message_of(choice)
            kept, refusals = gate_message_calls(message, self.decisions, session_id, user_request,
                                                self.log, self.log_args, workdir=self.workdir)
            if not refusals:
                return 200, self._finalise(response, choice, message, kept, notes)
            notes.extend(r["note"] for r in refusals)
            if attempt >= self.max_refusals:
                break
            self.log({"event": "refusal_retry", "session": session_id, "pass": attempt + 1})
            assistant = {"role": "assistant", "content": message.get("content"),
                         "tool_calls": [r["call"] for r in refusals]}
            synthetic = []
            for r in refusals:
                content = (f"{r['note']}. This action was not executed. "
                           "Continue the user's task another way, without this action.")
                seen.add(hashlib.sha256(content.encode()).hexdigest())  # proxy's own text, never scored
                synthetic.append({"role": "tool", "tool_call_id": (r["call"] or {}).get("id"),
                                  "content": content})
            convo = [*convo, assistant, *synthetic]
            status, response = self._upstream_message(body, convo, stream)
            if status >= 400:
                return status, response
        choice = response["choices"][0]
        message = _message_of(choice)
        return 200, self._finalise(response, choice, message, [], notes, force_stop=True)

    def _finalise(self, response: dict[str, Any], choice: dict[str, Any], message: dict[str, Any],
                  kept: list[Any], notes: list[str], force_stop: bool = False) -> dict[str, Any]:
        new_message = dict(message)
        if kept and not force_stop:
            new_message["tool_calls"] = kept
        else:
            new_message.pop("tool_calls", None)
        if notes:
            base = _text(message.get("content")) or ""
            new_message["content"] = "\n".join(notes) + (("\n" + base) if base else "")
        new_choice = dict(choice)
        new_choice["message"] = new_message
        if force_stop or not kept:
            new_choice["finish_reason"] = "stop"
        new_response = dict(response)
        new_response["choices"] = [new_choice, *response.get("choices", [])[1:]]
        return new_response


def run(listen: tuple[str, int], upstream: str, api_key: str,
        max_refusals: int = 3, log_args: bool = False, workdir: str | None = None) -> int:
    decisions = Decisions()
    try:
        try:
            decisions.warmup()
        except Exception as exc:  # a cold lane still serves static denials; log and go on
            _log_stderr({"event": "warmup_error", "reason": type(exc).__name__})
        server = ProxyServer(listen, upstream, api_key, decisions,
                             max_refusals=max_refusals, log_args=log_args, workdir=workdir)
        _log_stderr({"event": "listening", "host": listen[0], "port": listen[1]})
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        decisions.close()
    return 0
