"""Gate state built as small-state facts, composed in policy (IP-006 section 2).

The gate packet question ("is this step off-task?") asks the model to reason over
the whole state at once, which the traces showed it does poorly. Instead the gate
asks small facts over small states and composes the risk axes in policy:

- an **action** item: ``{tool_name, action}`` where ``action`` is the command and
  path fields of the tool input, content fields replaced by a character count, so
  a large ``write_file`` body does not travel. Carries the call-intrinsic axes.
- a **task_action** item: ``{task, action}``, carrying the task-relative axes as
  two-item facts (target in task, authorized, user requested, unresolved choice).
- one **constraint** item per constraint sentence enumerated from the task text,
  ``{instruction, action}``, carrying ``violates``.

Everything here is pure: code enumerates the items and the sentences, the model
judges each small item, and :func:`fluid.policy.compose_gate_facts` folds the
answers into the axis dictionary ``evaluate_gate`` already takes.
"""
from __future__ import annotations

import re
from typing import Any

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;:\n])\s+")


def _config(policy: dict[str, Any]) -> dict[str, Any]:
    facts = policy.get("facts")
    return facts if isinstance(facts, dict) else {}


def build_action(tool_input: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    """The action view: command and path fields kept, content fields summarised
    by length so large bodies do not travel into the state."""
    config = _config(policy)
    keep = set(config.get("command_fields", [])) | set(config.get("path_fields", []))
    content_fields = set(config.get("content_fields", []))
    action: dict[str, Any] = {}
    if not isinstance(tool_input, dict):
        return action
    for key, value in tool_input.items():
        if key in content_fields:
            action[key] = {"chars": len(value) if isinstance(value, str) else len(str(value))}
        elif not keep or key in keep:
            action[key] = value
    return action


def constraint_sentences(user_request: str, policy: dict[str, Any]) -> list[str]:
    """Task sentences that read as constraints, by configured cues; capped and
    truncated. Code enumerates; the model judges each against the action. Cues are
    word-bounded regular expressions (IP-007), so ``\\bask\\b`` does not match
    ``task``; a cue that will not compile is skipped."""
    config = _config(policy)
    patterns = []
    for cue in config.get("constraint_cues", []):
        try:
            patterns.append(re.compile(str(cue), re.IGNORECASE))
        except re.error:
            continue
    cap = int(config.get("max_constraints", 4))
    max_chars = int(config.get("max_sentence_chars", 240))
    if not isinstance(user_request, str) or not patterns:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for raw in _SENTENCE_SPLIT.split(user_request):
        sentence = raw.strip()
        if not sentence:
            continue
        if any(pattern.search(sentence) for pattern in patterns):
            trimmed = sentence[:max_chars]
            if trimmed not in seen:
                seen.add(trimmed)
                out.append(trimmed)
        if len(out) >= cap:
            break
    return out


def build_gate_facts(tool_name: str, tool_input: dict[str, Any], user_request: str,
                     questions: dict[str, Any], policy: dict[str, Any],
                     script_content: str | None = None) -> list[dict[str, Any]]:
    """Return the batch items: one action item, one task_action item, and one
    constraint item per constraint sentence. ``questions`` is keyed by item name
    (``action``, ``task_action``, ``constraint``) in the facts pack."""
    config = _config(policy)
    action = build_action(tool_input, policy)
    action_state: dict[str, Any] = {"tool_name": tool_name, "action": action}
    if script_content is not None:
        limit = int(config.get("script_max_chars", 4000))
        action_state["script"] = script_content[:limit]
    items: list[dict[str, Any]] = [
        {"item": "action", "state": action_state, "questions": _questions(questions, "action")},
        {"item": "task_action", "state": {"task": user_request, "action": action},
         "questions": _questions(questions, "task_action")},
    ]
    for index, sentence in enumerate(constraint_sentences(user_request, policy)):
        items.append({"item": f"constraint.{index}",
                      "state": {"instruction": sentence, "action": action},
                      "questions": _questions(questions, "constraint")})
    return [item for item in items if item["questions"]]


def build_instruction_items(instructions: list[str], action: dict[str, Any],
                            questions: dict[str, Any]) -> list[dict[str, Any]]:
    """One item per active flagged instruction (IP-007 section 3): state
    ``{instruction: <unit text>, action}`` carrying the ``carries_out`` noul. The
    service supplies the instruction texts from its per-session instruction cache,
    already capped. Mirrors the constraint item shape; the model judges each one
    against the action and :func:`fluid.policy.taint_check` reads the answers."""
    group = _questions(questions, "instruction")
    if not group:
        return []
    items: list[dict[str, Any]] = []
    for index, text in enumerate(instructions):
        if not isinstance(text, str) or not text:
            continue
        items.append({"item": f"instruction.{index}",
                      "state": {"instruction": text, "action": action},
                      "questions": group})
    return items


def interpreter_script_path(tool_input: dict[str, Any], policy: dict[str, Any]) -> str | None:
    """The script path a command invokes via an interpreter or a direct ``./``
    call, matched by the facts ``interpreter_pattern`` over the command fields;
    ``None`` when the command runs no script. The service uses this to decide
    whether to attach cached script content or floor the operation level."""
    config = _config(policy)
    pattern = config.get("interpreter_pattern")
    if not isinstance(pattern, str) or not isinstance(tool_input, dict):
        return None
    try:
        regex = re.compile(pattern)
    except re.error:
        return None
    for field in config.get("command_fields", []):
        value = tool_input.get(field)
        if not isinstance(value, str):
            continue
        match = regex.search(value)
        if match and match.groups():
            return match.group(1)
    return None


def _questions(questions: dict[str, Any], item: str) -> dict[str, Any]:
    """The questions declared for an item, stripped of the ``item`` grouping key."""
    group = questions.get(item)
    if not isinstance(group, dict):
        return {}
    clean: dict[str, Any] = {}
    for name, question in group.items():
        if isinstance(question, dict):
            clean[name] = {k: v for k, v in question.items() if k != "item"}
    return clean
