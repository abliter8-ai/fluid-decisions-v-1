"""File-ownership facts for delete gating (IP-011). Deterministic, no lane.

Given a delete call and a working directory, resolve each target and compute five
facts per target: ``created_this_session``, ``under_scratch``, ``tracked`` (git),
``outside_workdir`` and ``catastrophic``. :func:`fluid.policy.apply_ownership`
composes these into the gate axes via the ``policy.ownership`` block; the facts
never call the model. ``git`` is the only external process, with a one-second
budget per repository probe; its absence or a timeout yields ``tracked = None``
(unknown), never an error. Paths are resolved with ``realpath`` before any
comparison, so symlinks and ``..`` cannot escape a check; a target that fails to
resolve is treated as outside the working directory (foreign)."""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Any

_GIT_BUDGET_S = 1.0
_DELETE_VERBS = ("rm", "rmdir", "unlink", "trash", "trash-put")
_SEGMENT = re.compile(r"&&|\|\||[;&|\n]")
# Literal catastrophic rm targets that need no working directory to recognise.
_CATASTROPHIC_TOKENS = {"/", "*", "..", "~", "~/", ".", "./", "$HOME", "${HOME}",
                        "/*", "~/*", "$HOME/*", "${HOME}/*", "./*", "../*", "../.."}


def _facts(policy: dict[str, Any]) -> dict[str, Any]:
    facts = policy.get("facts") if isinstance(policy, dict) else None
    return facts if isinstance(facts, dict) else {}


def _realpath_dir(path: str | None) -> Path | None:
    if not isinstance(path, str) or not path:
        return None
    try:
        return Path(os.path.realpath(os.path.expanduser(path)))
    except (OSError, ValueError):
        return None


def scratch_roots(facts: dict[str, Any]) -> list[Path]:
    """Resolve the configured scratch roots. ``$FLUID_SCRATCH_DIRS`` expands to its
    colon-separated entries and ``$TMPDIR`` to the system temporary directory
    (decision 3: those two sources only, by default)."""
    roots: list[Path] = []
    for token in facts.get("scratch_roots", []) or []:
        if not isinstance(token, str):
            continue
        if token == "$FLUID_SCRATCH_DIRS":
            for part in os.environ.get("FLUID_SCRATCH_DIRS", "").split(":"):
                resolved = _realpath_dir(part.strip())
                if resolved is not None:
                    roots.append(resolved)
        elif token == "$TMPDIR":
            resolved = _realpath_dir(tempfile.gettempdir())
            if resolved is not None:
                roots.append(resolved)
        else:
            resolved = _realpath_dir(token)
            if resolved is not None:
                roots.append(resolved)
    return roots


def resolve_target(raw: str, cwd: Path | None) -> Path | None:
    token = raw.strip().strip('"').strip("'")
    if not token:
        return None
    try:
        expanded = Path(os.path.expanduser(token))
        if not expanded.is_absolute():
            expanded = (cwd or Path.cwd()) / expanded
        return Path(os.path.realpath(str(expanded)))
    except (OSError, ValueError):
        return None


def _is_ancestor_or_equal(path: Path, maybe_descendant: Path) -> bool:
    return path == maybe_descendant or path in maybe_descendant.parents


def is_catastrophic(raw: str, resolved: Path | None, cwd: Path | None, home: Path) -> bool:
    token = raw.strip().strip('"').strip("'")
    if token in _CATASTROPHIC_TOKENS:
        return True
    if resolved is not None:
        if str(resolved) == os.sep or resolved == home:
            return True
        if cwd is not None and _is_ancestor_or_equal(resolved, cwd):
            return True  # a parent of (or) the working directory
    return False


def _path_field_values(tool_input: dict[str, Any], path_fields: list[str]) -> list[str]:
    fields = set(path_fields)
    values: list[str] = []

    def walk(value: Any, key: str | None = None) -> None:
        if key in fields:
            if isinstance(value, str):
                values.append(value)
            elif isinstance(value, (list, tuple)):
                values.extend(str(item) for item in value if isinstance(item, str))
            return
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                walk(child_value, str(child_key))

    walk(tool_input)
    return values


def _command_values(tool_input: dict[str, Any], facts: dict[str, Any]) -> list[str]:
    fields = set(facts.get("command_fields", []))
    values: list[str] = []

    def walk(value: Any, key: str | None = None) -> None:
        if key in fields:
            if isinstance(value, str):
                values.append(value)
            elif isinstance(value, (list, tuple)):
                parts = [str(item) for item in value if isinstance(item, (str, int, float))
                         and not isinstance(item, bool)]
                if parts:
                    values.append(" ".join(parts))
            return
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                walk(child_value, str(child_key))

    walk(tool_input)
    return values


def _segments(command: str) -> list[list[str]]:
    out: list[list[str]] = []
    for segment in _SEGMENT.split(command):
        segment = segment.strip()
        if not segment:
            continue
        try:
            tokens = shlex.split(segment, comments=False, posix=True)
        except ValueError:
            tokens = segment.split()
        # drop leading environment assignments and sudo
        i = 0
        while i < len(tokens) and (tokens[i] == "sudo" or ("=" in tokens[i] and not tokens[i].startswith("-"))):
            i += 1
        if i < len(tokens):
            out.append(tokens[i:])
    return out


def _nonflags(tokens: list[str]) -> list[str]:
    return [t for t in tokens if not t.startswith("-")]


def _command_delete_targets(command: str) -> list[str]:
    targets: list[str] = []
    for tokens in _segments(command):
        verb = tokens[0].rsplit("/", 1)[-1]
        rest = tokens[1:]
        if verb in _DELETE_VERBS:
            targets.extend(_nonflags(rest))
        elif verb == "git" and rest:
            sub = rest[0]
            if sub == "rm":
                targets.extend(_nonflags(rest[1:]))
            elif sub == "clean":
                paths = _nonflags(rest[1:])
                targets.extend(paths or ["."])  # a bare git clean targets the work dir
    return targets


def delete_targets(tool_name: str, tool_input: dict[str, Any], facts: dict[str, Any]) -> list[str]:
    targets: list[str] = []
    if tool_name in (facts.get("delete_tools", []) or []):
        targets.extend(_path_field_values(tool_input, facts.get("path_fields", [])))
    pattern = facts.get("delete_pattern")
    for command in _command_values(tool_input, facts):
        if isinstance(pattern, str):
            try:
                if not re.search(pattern, command, re.IGNORECASE):
                    continue
            except re.error:
                pass
        targets.extend(_command_delete_targets(command))
    return targets


def _command_create_targets(command: str) -> list[str]:
    targets: list[str] = []
    for tokens in _segments(command):
        for index, token in enumerate(tokens):
            if token in (">", ">>") and index + 1 < len(tokens):
                targets.append(tokens[index + 1])
            elif token.startswith(">>") and len(token) > 2:
                targets.append(token[2:])
            elif token.startswith(">") and len(token) > 1 and not token.startswith(">>"):
                targets.append(token[1:])
        verb = tokens[0].rsplit("/", 1)[-1]
        rest = _nonflags(tokens[1:])
        if verb in ("mkdir", "touch") and rest:
            targets.extend(rest)
        elif verb in ("cp", "mv") and len(rest) >= 2:
            targets.append(rest[-1])  # the destination is what is created
        elif verb == "git" and tokens[1:2] == ["clone"]:
            clone_args = _nonflags(tokens[2:])
            if len(clone_args) >= 2:
                targets.append(clone_args[-1])
            elif clone_args:
                name = clone_args[0].rstrip("/").rsplit("/", 1)[-1]
                targets.append(name[:-4] if name.endswith(".git") else name)
    return targets


def create_targets(tool_name: str, tool_input: dict[str, Any], facts: dict[str, Any],
                   cwd: str | None) -> list[str]:
    """Resolved absolute paths a create call makes, for the ledger. Paths only."""
    cwd_path = _realpath_dir(cwd)
    raw: list[str] = []
    if tool_name in (facts.get("create_tools", []) or []):
        raw.extend(_path_field_values(tool_input, facts.get("path_fields", [])))
    patterns = facts.get("create_patterns")
    for command in _command_values(tool_input, facts):
        if isinstance(patterns, str):
            try:
                if not re.search(patterns, command, re.IGNORECASE):
                    continue
            except re.error:
                pass
        raw.extend(_command_create_targets(command))
    resolved: list[str] = []
    for token in raw:
        path = resolve_target(token, cwd_path)
        if path is not None:
            resolved.append(str(path))
    return resolved


def _tracked_repo(cwd: Path) -> bool | None:
    try:
        proc = subprocess.run(["git", "-C", str(cwd), "rev-parse", "--is-inside-work-tree"],
                              capture_output=True, timeout=_GIT_BUDGET_S)
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return None
    return proc.returncode == 0


def _tracked(resolved: Path | None, cwd: Path | None, in_repo: bool | None) -> bool | None:
    if resolved is None or cwd is None or in_repo is not True:
        return None
    try:
        proc = subprocess.run(["git", "-C", str(cwd), "ls-files", "--error-unmatch", "--", str(resolved)],
                              capture_output=True, timeout=_GIT_BUDGET_S)
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return None
    return proc.returncode == 0


def _created_here(resolved: Path | None, created: list[Path]) -> bool:
    if resolved is None:
        return False
    return any(_is_ancestor_or_equal(entry, resolved) for entry in created)


def _under_scratch(resolved: Path | None, roots: list[Path]) -> bool:
    if resolved is None:
        return False
    return any(_is_ancestor_or_equal(root, resolved) for root in roots)


def _outside_workdir(resolved: Path | None, cwd: Path | None) -> bool:
    if cwd is None:
        return False  # with no cwd the fact is unknown; apply_ownership treats it so
    if resolved is None:
        return True  # unresolvable -> treated as foreign
    return not _is_ancestor_or_equal(cwd, resolved)


def compute(tool_name: str, tool_input: dict[str, Any], policy: dict[str, Any], *,
            cwd: str | None, created_paths: Any) -> dict[str, Any] | None:
    """Ownership facts for a delete call, or ``None`` when the call is not a delete.
    ``created_paths`` is the session's created-path set (strings)."""
    if not isinstance(tool_input, dict):
        return None
    facts = _facts(policy)
    raw_targets = delete_targets(tool_name, tool_input, facts)
    if not raw_targets:
        return None
    cwd_path = _realpath_dir(cwd)
    roots = scratch_roots(facts)
    home = _realpath_dir(os.path.expanduser("~")) or Path(os.path.expanduser("~"))
    created = [p for p in (_realpath_dir(str(c)) for c in (created_paths or [])) if p is not None]
    in_repo = _tracked_repo(cwd_path) if cwd_path is not None else None
    targets: list[dict[str, Any]] = []
    for token in raw_targets:
        resolved = resolve_target(token, cwd_path)
        targets.append({
            "created_this_session": _created_here(resolved, created),
            "under_scratch": _under_scratch(resolved, roots),
            "tracked": _tracked(resolved, cwd_path, in_repo),
            "outside_workdir": _outside_workdir(resolved, cwd_path),
            "catastrophic": is_catastrophic(token, resolved, cwd_path, home),
        })
    return {"enabled": True, "cwd": str(cwd_path) if cwd_path is not None else None,
            "targets": targets}


def owned_targets(ownership: dict[str, Any] | None) -> bool:
    """True when every delete target was created this session or lies under a
    scratch root, none is tracked by git, and none is catastrophic — the condition
    that exempts the recursive static rules (IP-011 §4). Never true without a
    working directory: with no ``cwd`` the exemption does not apply, so the hook
    and MCP keep the static deny until they pass one (a scratch root such as the
    system temp directory does not by itself make a target owned)."""
    if not isinstance(ownership, dict) or not ownership.get("enabled"):
        return False
    if ownership.get("cwd") is None:
        return False
    targets = ownership.get("targets")
    if not isinstance(targets, list) or not targets:
        return False
    for target in targets:
        if not isinstance(target, dict):
            return False
        if target.get("catastrophic") or target.get("tracked") is True:
            return False
        if not (target.get("created_this_session") or target.get("under_scratch")):
            return False
    return True


def any_catastrophic(ownership: dict[str, Any] | None) -> bool:
    if not isinstance(ownership, dict):
        return False
    targets = ownership.get("targets")
    return isinstance(targets, list) and any(
        isinstance(t, dict) and t.get("catastrophic") for t in targets)
