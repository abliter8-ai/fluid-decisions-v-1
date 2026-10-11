"""Pure helpers for selecting the part of a decision state a question sees."""
from __future__ import annotations

import copy
import json
import re
from typing import Any

_PATH = re.compile(r"^[^.\[\]]+(?:\.[^.\[\]]+)*$")


def validate_scopes(scope: Any) -> tuple[str, ...] | None:
    """Validate and normalise a question's optional scope."""
    if scope is None:
        return None
    if not isinstance(scope, list) or not scope or any(
        not isinstance(path, str) or not _PATH.fullmatch(path) for path in scope
    ):
        raise ValueError("scope must be a non-empty list of dotted key paths")
    return tuple(scope)


def _lookup(state: Any, path: str) -> tuple[bool, Any]:
    current = state
    for key in path.split("."):
        if not isinstance(current, dict) or key not in current:
            return False, None
        current = current[key]
    return True, current


def _insert(target: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    current = target
    for key in parts[:-1]:
        existing = current.get(key)
        if not isinstance(existing, dict):
            existing = {}
            current[key] = existing
        current = existing
    current[parts[-1]] = copy.deepcopy(value)


def resolve_scope(state: Any, scope: Any) -> Any:
    """Return a structure-preserving state for *scope*, or None if unresolved."""
    paths = tuple(scope) if isinstance(scope, tuple) else validate_scopes(scope)
    if paths is None:
        return copy.deepcopy(state) if isinstance(state, dict) else state
    if not isinstance(state, dict):
        return None
    selected: dict[str, Any] = {}
    resolved = False
    for path in paths:
        present, value = _lookup(state, path)
        if present:
            _insert(selected, path, value)
            resolved = True
    return selected if resolved else None


def group_questions(questions: dict[str, Any], *, media: bool = False) -> list[dict[str, Any]]:
    """Group question names by identical scope, with whole-state first."""
    groups: list[dict[str, Any]] = []
    positions: dict[tuple[str, ...] | None, dict[str, Any]] = {}
    for name, question in questions.items():
        if not isinstance(question, dict):
            raise ValueError(f"question {name!r} must be an object")
        if "scope" in question and question["scope"] is None:
            raise ValueError("scope must be a non-empty list of dotted key paths")
        scope = None if media else validate_scopes(question.get("scope"))
        group = positions.get(scope)
        if group is None:
            group = {"scope": scope, "names": []}
            positions[scope] = group
            groups.append(group)
        group["names"].append(name)
    return sorted(groups, key=lambda group: group["scope"] is not None)


def strip_scopes(questions: dict[str, Any]) -> dict[str, Any]:
    """Copy questions while removing the implementation-only ``scope`` key."""
    clean = copy.deepcopy(questions)
    for question in clean.values():
        if isinstance(question, dict):
            question.pop("scope", None)
    return clean


def build_requests(
    state: Any,
    questions: dict[str, Any],
    *,
    images: list[str] | None = None,
    audio: str | None = None,
) -> list[dict[str, Any]]:
    """Group questions by scope and return endpoint-ready request metadata."""
    media = bool(images) or audio is not None
    groups: list[dict[str, Any]] = []
    for descriptor in group_questions(questions, media=media):
        effective = descriptor["scope"]
        scoped_state = state if effective is None else resolve_scope(state, effective)
        group = {"state": scoped_state, "scope": list(effective) if effective else None,
                 "questions": {}, "names": [], "unresolved": [], "media": media}
        for name in descriptor["names"]:
            if effective is not None and scoped_state is None:
                group["unresolved"].append(name)
                continue
            question = copy.deepcopy(questions[name])
            question.pop("scope", None)
            group["questions"][name] = question
            group["names"].append(name)
        groups.append(group)
    return groups


def _state_chars(state: Any) -> int:
    return len(json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def merge_results(groups: list[dict[str, Any]], results: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge grouped endpoint responses back to the original question names."""
    sent_groups = [group for group in groups if group.get("questions")]
    if len(sent_groups) != len(results):
        raise ValueError("group and result counts differ")
    answers: dict[str, Any] = {}
    summaries: list[dict[str, Any]] = []
    for group, result in zip(sent_groups, results):
        for name, answer in result.get("answers", {}).items():
            if name in answers:
                raise ValueError(f"duplicate answer name: {name}")
            answers[name] = answer
        summaries.append({
            "scope": group.get("scope"),
            "questions": list(group.get("names", group.get("questions", {}))),
            "input_tokens": int(result.get("usage", {}).get("input_tokens", 0)),
            "state_chars": _state_chars(group.get("state")),
        })
    unresolved = [name for group in groups for name in group.get("unresolved", [])]
    scoped = any(group.get("scope") is not None for group in groups)
    media = any(group.get("media") for group in groups)
    return {"answers": answers, "usage": {
        "input_tokens": sum(item["input_tokens"] for item in summaries), "output_tokens": 0,
    }, "lane": next((item.get("lane") for item in results if item.get("lane")), None),
            "timings": next((item.get("timings", {}) for item in results if item.get("timings") is not None), {}),
            "scoping": {"mode": "whole_state_media" if media else ("scoped" if scoped else "whole_state"),
                        "groups": summaries, "unresolved": unresolved}}
