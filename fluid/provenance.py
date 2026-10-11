"""Model-free provenance floor (IP-006 section 1).

Generalises the IP-002 carry-over layer from flagged results to every result, and
from prose shingles to the things an agent copies: commands and secret values. No
model is in this path; it still applies with the lane down.

The extraction is deterministic and symmetric. From a tool result it pulls
command-like spans (fenced code lines, inline backtick spans, prompt-marked lines)
and their shell segments, and, when the result was read from a secret path, secret
values (``KEY=VALUE`` values and long tokens). From a later tool call it pulls the
same command segments from the command fields and the same secret tokens from
every string field, and asks or denies when one matches something recorded. The
user's own words are exempt and a read-only allowlist is honoured. Only hashes are
compared and stored; no text is retained.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

_WS = re.compile(r"\s+")
_FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
_INLINE = re.compile(r"`([^`\n]+)`")
_PROMPT_LINE = re.compile(r"^[ \t]*[$>%][ \t]+(.+)$", re.MULTILINE)
_SEGMENT = re.compile(r"&&|\|\||[;|]")
_INTERP = re.compile(r"^(?:(?:bash|sh|python3?)\s+|\./)", re.IGNORECASE)
_COMMAND_FIELDS = ("command", "cmd", "argv", "script", "query")
_PATH_FIELDS = ("path", "paths", "file", "filename", "dir", "directory", "target")
_KEY_VALUE = re.compile(r"^[ \t]*[A-Za-z_][A-Za-z0-9_]*[ \t]*=[ \t]*(\S.*?)[ \t]*$", re.MULTILINE)
_TOKEN = re.compile(r"[A-Za-z0-9_./+\-]{4,}")
_PATH_TOKEN_SPLIT = re.compile(r"[\s;&|<>()\"'`]+")


def _norm(text: str) -> str:
    return _WS.sub(" ", text.strip())


def _hash(text: str) -> str:
    return hashlib.sha256(_norm(text).encode("utf-8")).hexdigest()


def _min_span(policy: dict[str, Any]) -> int:
    value = policy.get("min_span_chars", 6)
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 6


def _command_lines(text: str) -> list[str]:
    """Candidate command strings in a block of text: fenced lines, inline spans,
    prompt-marked lines."""
    lines: list[str] = []
    for block in _FENCE.findall(text):
        for line in block.splitlines():
            if line.strip():
                lines.append(line)
    lines.extend(span for span in _INLINE.findall(text) if span.strip())
    lines.extend(_PROMPT_LINE.findall(text))
    return lines


def _strip_interpreter(norm_command: str) -> str:
    return _INTERP.sub("", norm_command, count=1)


def _segments(command: str) -> list[str]:
    parts = [command]
    parts.extend(_SEGMENT.split(command))
    return [p for p in parts if p.strip()]


def _segment_variants(segment: str, minimum: int) -> list[str]:
    """A segment's normalised text and its interpreter-stripped form, so a bare
    ``scripts/cleanup.sh --all`` in a doc matches ``bash scripts/cleanup.sh --all``
    run later, and vice versa."""
    norm = _norm(segment)
    out = [norm] if len(norm) >= minimum else []
    stripped = _strip_interpreter(norm)
    if stripped != norm and len(stripped) >= minimum:
        out.append(stripped)
    return out


def spans(text: str, policy: dict[str, Any]) -> set[str]:
    """Hashes of command-like spans in a result and of their shell segments."""
    if not isinstance(text, str):
        return set()
    minimum = _min_span(policy)
    out: set[str] = set()
    for command in _command_lines(text):
        for segment in _segments(command):
            for variant in _segment_variants(segment, minimum):
                out.add(_hash(variant))
    return out


def _secret_tokens(text: str, policy: dict[str, Any]) -> set[str]:
    minimum = int(policy.get("secret_min_value_chars", 8))
    tokens: set[str] = set()
    for value in _KEY_VALUE.findall(text):
        if len(value.strip()) >= 1:
            tokens.add(value.strip())
    for token in _TOKEN.findall(text):
        if len(token) >= minimum:
            tokens.add(token)
    return tokens


def _matches_secret_path(path: str | None, policy: dict[str, Any]) -> bool:
    """A read path matches one of the configured secret-path patterns (regexes)."""
    if not isinstance(path, str):
        return False
    normal = path[2:] if path.startswith("./") else path
    for pattern in policy.get("secret_paths", []):
        if not isinstance(pattern, str):
            continue
        try:
            if re.search(pattern, normal):
                return True
        except re.error:
            continue
    return False


def secret_values(text: str, path: str | None, policy: dict[str, Any]) -> set[str]:
    """Hashes of secret values in a result read from a configured secret path."""
    if not isinstance(text, str) or not _matches_secret_path(path, policy):
        return set()
    return {_hash(token) for token in _secret_tokens(text, policy)}


def _path_tokens(tool_input: dict[str, Any]) -> set[str]:
    """Path strings a call names: the path fields and any path-like token of its
    command fields (a token with a ``/`` or a leading dot). ``./`` is stripped so
    ``./config/.env`` and ``config/.env`` compare equal. The encoding wrapped
    around the read (base64, rev, od, a Python one-liner) does not change the
    path it names, so a re-read in any encoding still carries the token."""
    out: set[str] = set()

    def add(value: Any) -> None:
        if isinstance(value, str) and value.strip():
            token = value.strip()
            out.add(token[2:] if token.startswith("./") else token)

    for key in _PATH_FIELDS:
        value = tool_input.get(key)
        if isinstance(value, (list, tuple)):
            for item in value:
                add(item)
        else:
            add(value)
    for command in _command_field_strings(tool_input):
        for token in _PATH_TOKEN_SPLIT.split(command):
            if "/" in token or token.startswith("."):
                add(token)
    return out


def path_hashes(tool_input: dict[str, Any], policy: dict[str, Any]) -> set[str]:
    """Hashes of the path tokens of a call, used both to record a withheld call's
    paths and to test a later call for a withheld-path re-read (IP-007 section 3)."""
    if not isinstance(tool_input, dict):
        return set()
    minimum = _min_span(policy)
    return {_hash(token) for token in _path_tokens(tool_input) if len(_norm(token)) >= minimum}


def _command_field_strings(tool_input: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in _COMMAND_FIELDS:
        value = tool_input.get(key)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, (list, tuple)):
            parts = [str(v) for v in value if isinstance(v, (str, int, float)) and not isinstance(v, bool)]
            if parts:
                values.append(" ".join(parts))
    return values


def _all_field_strings(tool_input: dict[str, Any]) -> list[str]:
    out: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            out.append(value)
        elif isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, (list, tuple)):
            for v in value:
                walk(v)

    walk(tool_input)
    return out


def _command_segment_texts(tool_input: dict[str, Any], policy: dict[str, Any]) -> list[tuple[str, str]]:
    """Normalised (text, hash) pairs for a call's command segments."""
    minimum = _min_span(policy)
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for command in _command_field_strings(tool_input):
        for segment in _segments(command):
            for variant in _segment_variants(segment, minimum):
                if variant not in seen:
                    seen.add(variant)
                    out.append((variant, _hash(variant)))
    return out


def command_segments(tool_input: dict[str, Any], policy: dict[str, Any]) -> set[str]:
    """Hashes of a call's command fields, whole command and each shell segment."""
    if not isinstance(tool_input, dict):
        return set()
    return {h for _, h in _command_segment_texts(tool_input, policy)}


def _allowlisted(tool_input: dict[str, Any], policy: dict[str, Any]) -> bool:
    """A call is exempt only when every segment of every command field is a
    read-only entry. Entries are a bare program (``ls``, ``cat``) or a
    ``program subcommand`` pair (``git status``, ``cargo test``); a bare program
    like ``git`` is never allowlisted wholesale."""
    allow = {str(a).lower() for a in policy.get("read_only_allowlist", [])}
    commands = _command_field_strings(tool_input)
    if not commands:
        return False
    for command in commands:
        for segment in _segments(command):
            tokens = _norm(segment).lower().split()
            if not tokens:
                continue
            base = tokens[0].rsplit("/", 1)[-1]
            pair = f"{base} {tokens[1]}" if len(tokens) > 1 else base
            if base in allow or pair in allow:
                continue
            return False
    return True


def check(tool_name: str, tool_input: dict[str, Any], user_request: str,
          carry: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any] | None:
    """A deterministic verdict, or None. ``carry`` maps a class to {hash: meta}:
    ``spans`` by source class (result/pending/flagged) and ``secrets``."""
    if not policy.get("enabled", True) or not isinstance(tool_input, dict):
        return None
    secrets = carry.get("secrets", {})
    if secrets:
        for field in _all_field_strings(tool_input):
            for token_hash in {_hash(t) for t in _secret_tokens(field, policy)}:
                if token_hash in secrets:
                    meta = secrets[token_hash]
                    return {"verdict": policy.get("verdict_on_secret_value", "deny"),
                            "reason": "secret_value_from_result", "static": True,
                            "source_tool": meta.get("tool"), "source_class": meta.get("source_class", "result"),
                            "calls_since": meta.get("calls_since")}
    # A path whose result was withheld cannot be re-read, in any encoding, and the
    # user's own words do not exempt it (IP-007 section 3): the user asked once and
    # the guard withheld it; redaction, not a re-read, is how the user gets it.
    withheld = carry.get("withheld", {})
    if withheld:
        for path_hash in path_hashes(tool_input, policy):
            if path_hash in withheld:
                meta = withheld[path_hash]
                return {"verdict": policy.get("verdict_on_withheld_path", "deny"),
                        "reason": "withheld_path_reread", "static": True,
                        "source_tool": meta.get("tool"), "source_class": "withheld",
                        "calls_since": meta.get("calls_since")}
    user_norm = _norm(user_request) if isinstance(user_request, str) else ""
    if _allowlisted(tool_input, policy):
        return None
    span_carry = carry.get("spans", {})
    hit = None
    for segment_text, segment_hash in _command_segment_texts(tool_input, policy):
        # The user's own words are trusted: a command the user typed is exempt.
        if user_norm and segment_text in user_norm:
            continue
        meta = span_carry.get(segment_hash)
        if meta is None:
            continue
        source_class = meta.get("source_class", "result")
        rank = {"flagged": 3, "pending": 2, "result": 1}.get(source_class, 1)
        if hit is None or rank > hit[0]:
            hit = (rank, segment_hash, meta, source_class)
    if hit is None:
        return None
    _, _, meta, source_class = hit
    verdict_key = {"flagged": "verdict_on_flagged_span", "pending": "verdict_on_pending_span",
                   "result": "verdict_on_result_span"}[source_class]
    reason = {"flagged": "command_from_flagged_result", "pending": "command_from_pending_result",
              "result": "command_from_tool_result"}[source_class]
    verdict = policy.get(verdict_key, "ask" if source_class != "flagged" else "deny")
    return {"verdict": verdict, "reason": reason, "static": verdict == "deny",
            "source_tool": meta.get("tool"), "source_class": source_class,
            "calls_since": meta.get("calls_since")}
