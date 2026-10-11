"""Pure policy evaluation for the versioned Fluid Decisions question packs."""

from __future__ import annotations

import hashlib
import os
import re
from copy import deepcopy
from math import isclose, isfinite
from pathlib import Path
from typing import Any

import yaml

from fluid.scoping import validate_scopes

PACK_DIR = Path(__file__).with_name("packs")


def load_pack(name: str) -> dict[str, Any]:
    """Load a bundled pack and return an isolated copy of its YAML data.

    The gate pack is selectable with ``FD_GATE_PACK``. ``gate`` is the default and,
    as of IP-007 (the CR-006 ship rule passed), is the facts pack: it asks small
    facts over small states and composes the axes in policy. ``gate-packet`` is the
    frozen flat-question packet pack, selected with ``FD_GATE_PACK=gate-packet``.
    """
    if name == "gate":
        override = os.environ.get("FD_GATE_PACK")
        if override and re.fullmatch(r"gate(-[a-z0-9]+)*", override):
            name = override
    if not re.fullmatch(r"[a-z][a-z0-9_-]*", name):
        raise ValueError(f"invalid pack name: {name!r}")
    path = PACK_DIR / f"{name}.yaml"
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open(encoding="utf-8") as stream:
        pack = yaml.safe_load(stream)
    if not isinstance(pack, dict) or not isinstance(pack.get("policy"), dict):
        raise ValueError(f"invalid policy pack: {path}")
    for section in ("questions", "session_questions"):
        questions = pack.get(section, {})
        if not isinstance(questions, dict):
            raise ValueError(f"invalid {section} in policy pack: {path}")
        for question in questions.values():
            if isinstance(question, dict):
                if "scope" in question:
                    if question["scope"] is None:
                        raise ValueError("scope must be a non-empty list of dotted key paths")
                    validate_scopes(question["scope"])
    post_questions = pack.get("post_result", {}).get("questions", {})
    if isinstance(post_questions, dict):
        for question in post_questions.values():
            if isinstance(question, dict):
                if "scope" in question:
                    if question["scope"] is None:
                        raise ValueError("scope must be a non-empty list of dotted key paths")
                    validate_scopes(question["scope"])
    return deepcopy(pack)


def _probability(answer: Any) -> float | None:
    if not isinstance(answer, dict) or answer.get("type") != "noul":
        return None
    value = answer.get("noul")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        return None
    value = float(value)
    return value if isfinite(value) and 0.0 <= value <= 1.0 else None


def _enrich_answer(answer: Any) -> tuple[dict[str, Any], bool]:
    """Copy an answer and add derived fields without changing its raw fields."""
    if not isinstance(answer, dict):
        return {"raw": deepcopy(answer), "valid": False}, False
    enriched = deepcopy(answer)
    if answer.get("type") == "noul" or "noul" in answer:
        probability = _probability(answer)
        if probability is None:
            enriched["valid"] = False
            return enriched, False
        probabilities = answer.get("probabilities")
        if not isinstance(probabilities, dict):
            probabilities = {"yes": probability, "no": 1.0 - probability}
        else:
            probabilities = deepcopy(probabilities)
        enriched["probabilities"] = probabilities
        enriched["confidence"] = answer.get("confidence")
        enriched["top_probability"] = max(probability, 1.0 - probability)
        enriched["decisiveness"] = 2.0 * abs(probability - 0.5)
        enriched["valid"] = True
        return enriched, True
    probabilities = answer.get("probabilities")
    if isinstance(probabilities, dict):
        numeric = [v for v in probabilities.values() if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if numeric:
            enriched["top_probability"] = max(numeric)
    enriched["valid"] = True
    return enriched, True


def _enrich_answers(answers: Any) -> tuple[dict[str, Any], set[str]]:
    if not isinstance(answers, dict):
        return {}, set()
    copied: dict[str, Any] = {}
    invalid: set[str] = set()
    for key, answer in answers.items():
        copied[key], valid = _enrich_answer(answer)
        if not valid:
            invalid.add(str(key))
    return copied, invalid


def _noul(answers: dict[str, Any], key: str) -> float | None:
    return _probability(answers.get(key))


def _threshold(policy: dict[str, Any], group: str, key: str) -> float | None:
    value = policy.get("thresholds", {}).get(group, {}).get(key)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) else None


def _policy_threshold_error(policy: dict[str, Any], required: list[tuple[str, str]]) -> list[str]:
    missing = [f"{group}.{key}" for group, key in required if _threshold(policy, group, key) is None]
    return ["invalid_policy_thresholds:" + ",".join(missing)] if missing else []


def _static_result(rule: dict[str, Any], tool_name: str, default_reason: str) -> dict[str, Any]:
    return {
        "verdict": "deny",
        "reasons": [str(rule.get("reason", rule.get("id", default_reason)))],
        "rule": rule.get("id"),
        "tool": tool_name,
        "static": True,
        "answers": {},
    }


def static_denial(tool_name: str, tool_input: dict[str, Any], policy: dict[str, Any],
                  ownership: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Apply static rules to command-bearing fields before model evaluation.

    Arbitrary document text is deliberately ignored. Only values under the
    configured command fields can match a shell rule.

    File ownership (IP-011 §4): a rule carrying ``exempt_when: owned_targets`` does
    not fire when every delete target was created this session or lies under a
    scratch root, none is tracked, and none is catastrophic — that call is judged
    contextually instead. The ``catastrophic_remove`` rule has no exemption and
    also fires on an ownership ``catastrophic`` fact (a delete of ``/``, the home
    directory or a parent of the working directory), so a non-command delete tool
    aimed at such a path is caught too. With no ownership facts (no ``cwd``) the
    exemption never applies and behaviour is unchanged.
    """
    if not isinstance(tool_input, dict):
        return None
    from fluid import ownership as ownership_facts
    catastrophic = ownership_facts.any_catastrophic(ownership)
    owned = ownership_facts.owned_targets(ownership)
    values = command_texts(tool_input, policy)
    rules = policy.get("static_denials", {}).get("rules", [])
    for rule in rules:
        if not isinstance(rule, dict) or not isinstance(rule.get("pattern"), str):
            continue
        try:
            pattern = re.compile(rule["pattern"], re.IGNORECASE | re.MULTILINE)
        except re.error:
            continue
        matched = any(pattern.search(value) for value in values)
        if rule.get("id") == "catastrophic_remove":
            if matched or catastrophic:
                return _static_result(rule, tool_name, "catastrophic removal")
            continue
        if not matched:
            continue
        if rule.get("exempt_when") == "owned_targets" and owned:
            continue
        return _static_result(rule, tool_name, "static denial")
    if catastrophic:  # safety net if the pack has no catastrophic_remove rule
        return {"verdict": "deny", "reasons": ["catastrophic removal"],
                "rule": "catastrophic_remove", "tool": tool_name, "static": True, "answers": {}}
    return None


def apply_ownership(axes: dict[str, Any], ownership: dict[str, Any] | None,
                    policy: dict[str, Any]) -> dict[str, Any]:
    """Compose file-ownership facts into the gate axes (IP-011 §3). Pure, no lane.

    Returns ``{"axes", "floor", "unsoftenable", "reasons"}``. The caller evaluates
    the returned axes and then raises the verdict to ``floor`` when one is set.

    - ``owned`` (every target created this session or under scratch, none tracked):
      ``operation`` is set to ``mutating`` and ``destructive`` to 0.0, reason
      ``owned_scratch_delete``; the remaining axes, taint, provenance and
      constraints decide as they do for any write.
    - ``tracked`` (any target tracked by git): a verdict floor of ``ask``, reason
      ``delete_of_tracked_path``, that the mitigators cannot soften.
    - ``foreign`` (any target outside the working directory the session did not
      create): a verdict floor of ``deny``, reason ``delete_outside_workdir``.
    - otherwise (no cwd, no targets, or a mix the branches do not cover): the axes
      are unchanged and the model's answers stand (``unknown_delete``).

    ``catastrophic`` targets are handled by the static rule, never here.
    """
    result: dict[str, Any] = {"axes": axes, "floor": None, "unsoftenable": False, "reasons": []}
    own = policy.get("ownership") if isinstance(policy, dict) else None
    if not isinstance(own, dict) or not own.get("enabled", True):
        return result
    if not isinstance(ownership, dict) or not ownership.get("enabled"):
        return result
    targets = ownership.get("targets")
    if ownership.get("cwd") is None or not isinstance(targets, list) or not targets:
        return result
    targets = [t for t in targets if isinstance(t, dict)]

    def owned(t: dict[str, Any]) -> bool:
        return bool(t.get("created_this_session") or t.get("under_scratch"))

    any_foreign = any(t.get("outside_workdir") and not owned(t) for t in targets)
    any_tracked = any(t.get("tracked") is True for t in targets)
    all_owned = bool(targets) and all(owned(t) for t in targets) and not any_tracked
    if any_foreign and own.get("foreign_delete", "deny") == "deny":
        return {"axes": axes, "floor": "deny", "unsoftenable": True,
                "reasons": ["delete_outside_workdir"]}
    if any_tracked and own.get("tracked_delete", "ask") == "ask":
        return {"axes": axes, "floor": "ask", "unsoftenable": True,
                "reasons": ["delete_of_tracked_path"]}
    if all_owned and own.get("owned_delete", "contextual") == "contextual":
        new_axes = dict(axes)
        risk_order = list(policy.get("risk_order", []))
        if "mutating" in risk_order:
            new_axes["operation"] = _operation_answer("mutating", risk_order)
        new_axes["destructive"] = {"type": "noul", "noul": 0.0}
        return {"axes": new_axes, "floor": None, "unsoftenable": False,
                "reasons": ["owned_scratch_delete"]}
    return result


def command_texts(tool_input: dict[str, Any], policy: dict[str, Any]) -> list[str]:
    """Return text from configured command-bearing fields, recursively."""
    if not isinstance(tool_input, dict):
        return []
    fields = set(policy.get("static_denials", {}).get("fields", []))
    values: list[str] = []

    def collect(value: Any, key: str | None = None) -> None:
        if key in fields:
            if isinstance(value, str):
                values.append(value)
            elif isinstance(value, (list, tuple)):
                parts = [str(item) for item in value if isinstance(item, (str, int, float)) and not isinstance(item, bool)]
                if parts:
                    values.append(" ".join(parts))
            return
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                collect(child_value, str(child_key))

    collect(tool_input)
    return values


def shingles(text: str, policy: dict[str, Any]) -> set[str]:
    """Hash configured-size normalized word windows without retaining text."""
    if not isinstance(text, str):
        return set()
    taint = policy.get("taint", {})
    words = taint.get("shingle_words")
    minimum = taint.get("min_shingle_chars")
    if (isinstance(words, bool) or not isinstance(words, int) or words <= 0
            or isinstance(minimum, bool) or not isinstance(minimum, int) or minimum <= 0):
        raise ValueError("invalid taint shingle configuration")
    normalized = " ".join(text.lower().split())
    tokens = normalized.split(" ") if normalized else []
    hashes: set[str] = set()
    for start in range(max(0, len(tokens) - words + 1)):
        window = " ".join(tokens[start:start + words])
        if len(window) >= minimum:
            hashes.add(hashlib.sha256(window.encode("utf-8")).hexdigest())
    return hashes


def _taint_operation(answers: dict[str, Any], policy: dict[str, Any],
                     taint: dict[str, Any]) -> tuple[Any, int, int] | None:
    """Resolve the operation level from a composed score against the taint floor.
    Returns ``(level, level_index, floor_index)`` or ``None`` when the operation
    distribution is missing or malformed. Shared by ``taint_floor`` (the frozen
    packet path) and ``taint_check`` (the facts path, IP-007 section 3)."""
    operation = answers.get("operation") if isinstance(answers, dict) else None
    probabilities = operation.get("probabilities") if isinstance(operation, dict) else None
    legend = operation.get("legend") if isinstance(operation, dict) else None
    risk_order = policy.get("risk_order")
    if (not isinstance(operation, dict) or operation.get("type") != "score"
            or not isinstance(probabilities, dict) or not probabilities
            or not isinstance(legend, dict) or not isinstance(risk_order, list)):
        return None
    try:
        numeric = [float(value) for value in probabilities.values()]
        if (len(probabilities) != len(legend)
                or {str(key) for key in probabilities} != {str(key) for key in legend}
                or any(not isfinite(value) or not 0.0 <= value <= 1.0 for value in numeric)
                or not isclose(sum(numeric), 1.0, abs_tol=1e-6)):
            raise ValueError("invalid operation distribution")
        top_key = max(probabilities, key=lambda key: float(probabilities[key]))
        level = legend.get(str(top_key), legend.get(top_key))
        floor_index = risk_order.index(taint["floor_from_level"])
        level_index = risk_order.index(level)
    except (TypeError, ValueError, KeyError):
        return None
    return level, level_index, floor_index


def taint_floor(decision: dict[str, Any], answers: dict[str, Any], policy: dict[str, Any],
                *, pending: bool = False) -> dict[str, Any]:
    """Apply the configured operation floor to an already-evaluated decision."""
    result = deepcopy(decision) if isinstance(decision, dict) else {"verdict": "ask", "reasons": []}
    reasons = result.setdefault("reasons", [])
    if not isinstance(reasons, list):
        reasons = result["reasons"] = []
    taint = policy.get("taint")
    if not isinstance(taint, dict) or not isinstance(taint.get("floor_from_level"), str):
        if result.get("verdict") != "deny":
            result["verdict"] = "ask"
            reasons.append("invalid_policy_taint")
        return result
    resolved = _taint_operation(answers, policy, taint)
    if resolved is None:
        if result.get("verdict") != "deny":
            result["verdict"] = "ask"
            reasons.append("session_taint_operation_unresolved")
        return result
    level, level_index, floor_index = resolved
    if level_index >= floor_index:
        if pending:
            if "session_pending_check" not in reasons:
                reasons.append("session_pending_check")
            if result.get("verdict") == "allow":
                result["verdict"] = "ask"
        elif result.get("verdict") == "allow":
            result["verdict"] = "ask"
            reasons.append(f"session_taint_floor:{level}")
    return result


def taint_check(decision: dict[str, Any], answers: dict[str, Any],
                carries_out: list[float], policy: dict[str, Any]) -> dict[str, Any]:
    """Proportionate taint (IP-007 section 3): a flag arms a ``carries_out`` check
    per active instruction instead of a blanket floor.

    ``carries_out`` is the list of ``carries_out`` noul probabilities for the
    active flagged instructions, one per instruction item the facts gate asked
    (empty when the hook is one-shot or the cache evicted the text). At the floor
    level (mutating) or worse:

    - with instruction text: ask with reason ``carries_out_flagged_instruction``
      when any ``carries_out`` is at or above ``taint_carry_line``; otherwise the
      action passes and the caller records the probabilities.
    - with no instruction text: today's blanket floor applies, reason
      ``taint_floor_no_instruction``.

    Below the floor the flag does not raise the verdict — read-only zeroing has
    already neutralised the action axes. A deny is never lowered."""
    result = deepcopy(decision) if isinstance(decision, dict) else {"verdict": "ask", "reasons": []}
    reasons = result.setdefault("reasons", [])
    if not isinstance(reasons, list):
        reasons = result["reasons"] = []
    taint = policy.get("taint")
    if not isinstance(taint, dict) or not isinstance(taint.get("floor_from_level"), str):
        if result.get("verdict") != "deny":
            result["verdict"] = "ask"
            reasons.append("invalid_policy_taint")
        return result
    resolved = _taint_operation(answers, policy, taint)
    if resolved is None:
        if result.get("verdict") != "deny":
            result["verdict"] = "ask"
            reasons.append("session_taint_operation_unresolved")
        return result
    _level, level_index, floor_index = resolved
    if level_index < floor_index:
        return result  # below the floor: the flag does not raise an action's verdict
    carries = [float(value) for value in carries_out
               if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)]
    if carries:
        line = float(taint.get("taint_carry_line", 0.70))
        if any(value >= line for value in carries):
            if result.get("verdict") == "allow":
                result["verdict"] = "ask"
            if "carries_out_flagged_instruction" not in reasons:
                reasons.append("carries_out_flagged_instruction")
        return result
    # No instruction text available (one-shot hook or evicted cache): blanket floor.
    if result.get("verdict") == "allow":
        result["verdict"] = "ask"
    if "taint_floor_no_instruction" not in reasons:
        reasons.append("taint_floor_no_instruction")
    return result


def static_injection(text: str, post_result: dict[str, Any]) -> dict[str, Any] | None:
    """Return metadata for the first configured canonical injection rule hit.

    ``sentence`` is the matched span's own sentence and everything after it in the
    result (from the boundary before the match to the end of the text): an
    injection's payload follows its trigger phrase, so this captures the directive
    the static rule only trigger-matched. The caller caches it, truncated to
    ``instruction_cache_chars``, as the active instruction exactly as a
    model-confirmed unit is, so a later mutating action gets a ``carries_out``
    item and ``taint_check`` judges it instead of the blanket floor (IP-007 §3,
    CR-010 follow-up)."""
    if not isinstance(text, str) or not isinstance(post_result, dict):
        return None
    for rule in post_result.get("static_rules", []):
        if not isinstance(rule, dict) or not isinstance(rule.get("pattern"), str):
            continue
        try:
            matched = re.search(rule["pattern"], text, re.IGNORECASE | re.MULTILINE)
        except re.error:
            continue
        if matched:
            start = matched.start()
            boundary = max(text.rfind(ch, 0, start) for ch in (".", "!", "?", "\n"))
            sentence = text[boundary + 1:].strip()
            return {"rule_id": rule.get("id", "static_injection"), "kind": rule.get("kind", "injection"),
                    "span": matched.group(0), "sentence": sentence}
    return None


def _operation_index(answer: Any, risk_order: list[Any]) -> int | None:
    if not isinstance(answer, dict):
        return None
    probabilities = answer.get("probabilities")
    legend = answer.get("legend")
    if isinstance(probabilities, dict) and probabilities and isinstance(legend, dict):
        top = max(probabilities, key=lambda k: float(probabilities[k]))
        level = legend.get(str(top), legend.get(top))
        if level in risk_order:
            return risk_order.index(level)
    value = answer.get("score")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        index = int(round(value))
        if 0 <= index < len(risk_order):
            return index
    return None


def _operation_answer(level: str, risk_order: list[Any]) -> dict[str, Any]:
    index = risk_order.index(level) if level in risk_order else 0
    return {"type": "score", "score": index, "confidence": 1.0,
            "legend": {str(i): v for i, v in enumerate(risk_order)},
            "probabilities": {str(i): float(i == index) for i in range(len(risk_order))}}


def compose_gate_facts(tool_name: str, fact_answers: dict[str, Any],
                       policy: dict[str, Any], operation_floor: str | None = None) -> dict[str, Any]:
    """Fold per-item fact answers into the axis dictionary ``evaluate_gate``
    takes (IP-006 section 2). Pure: no thresholds, no lane. ``fact_answers`` maps
    an item name (``action``, ``task_action``, ``constraint.N``) to its answers.

    - call-intrinsic axes pass through from the action item;
    - authorized, user_requested, unresolved_choice from task_action;
    - operation comes from ``facts.tool_operation`` for tools whose effect code
      knows, else from the model's action answer, then floored at
      ``operation_floor`` when the service could not supply script content for an
      interpreter command (the plan's ``interpreter_floor``);
    - off_task is ``1 - target_in_task`` only when the operation is mutating or
      worse, and 0.0 otherwise (read-only exploration is never off-task);
    - violates_constraint is the maximum over the constraint items, 0.0 with
      reason ``no_constraint_sentences`` when there are none;
    - when the operation is read_only, destructive, privilege, unresolved_choice
      and violates_constraint are also 0.0 (reason ``read_only_axes_zeroed``);
      secret_exposure and exfiltration are left untouched (IP-007 §1).
    """
    facts_cfg = policy.get("facts", {}) if isinstance(policy, dict) else {}
    risk_order = list(policy.get("risk_order", []))
    reasons: list[str] = []
    axes: dict[str, Any] = {}
    action = fact_answers.get("action", {}) if isinstance(fact_answers.get("action"), dict) else {}
    task_action = fact_answers.get("task_action", {}) if isinstance(fact_answers.get("task_action"), dict) else {}
    for axis in ("destructive", "exfiltration", "privilege", "secret_exposure"):
        if axis in action:
            axes[axis] = action[axis]
    for axis in ("authorized", "user_requested", "unresolved_choice"):
        if axis in task_action:
            axes[axis] = task_action[axis]
    tool_operation = facts_cfg.get("tool_operation", {}) if isinstance(facts_cfg.get("tool_operation"), dict) else {}
    if tool_name in tool_operation and risk_order:
        axes["operation"] = _operation_answer(str(tool_operation[tool_name]), risk_order)
        reasons.append("operation_from_tool")
    elif "operation" in action:
        axes["operation"] = action["operation"]
    if operation_floor and risk_order and operation_floor in risk_order:
        floor_idx = risk_order.index(operation_floor)
        current_idx = _operation_index(axes.get("operation"), risk_order)
        if current_idx is None or current_idx < floor_idx:
            axes["operation"] = _operation_answer(operation_floor, risk_order)
            reasons.append("interpreter_floor")
    op_index = _operation_index(axes.get("operation"), risk_order)
    mutating_index = risk_order.index("mutating") if "mutating" in risk_order else 1
    target = task_action.get("target_in_task")
    target_p = _probability(target)
    if op_index is not None and op_index >= mutating_index and target_p is not None:
        axes["off_task"] = {"type": "noul", "noul": max(0.0, 1.0 - target_p)}
    else:
        axes["off_task"] = {"type": "noul", "noul": 0.0}
        if op_index is not None and op_index < mutating_index:
            reasons.append("off_task_skipped_read_only")
    violates = [ans["violates"] for key, ans in fact_answers.items()
                if key.startswith("constraint") and isinstance(ans, dict) and "violates" in ans]
    values = [p for a in violates if (p := _probability(a)) is not None]
    if values:
        axes["violates_constraint"] = {"type": "noul", "noul": max(values)}
    else:
        axes["violates_constraint"] = {"type": "noul", "noul": 0.0}
        reasons.append("no_constraint_sentences")
    # Read-only operations cannot trip the action axes (IP-007 §1): changing,
    # escalating, acting while a choice is unresolved and violating a constraint do
    # not apply to a read. secret_exposure and exfiltration are untouched, because
    # reading a secret is an exposure and sending one is exfiltration. off_task is
    # already 0.0 for read-only above. The model's per-item answers survive in the
    # service's `facts` record for the audit, so nothing it said is lost.
    if op_index is not None and op_index < mutating_index:
        for axis in ("destructive", "privilege", "unresolved_choice", "violates_constraint"):
            axes[axis] = {"type": "noul", "noul": 0.0}
        reasons.append("read_only_axes_zeroed")
    return {"answers": axes, "reasons": reasons}


def evaluate_gate(answers: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    copied, invalid = _enrich_answers(answers)
    required = list(policy.get("required_axes", []))
    missing = [axis for axis in required if axis not in answers or axis in invalid or _noul(answers, axis) is None]
    risk_axes = list(policy.get("risk_axes", []))
    risk_scores = {axis: _noul(answers, axis) for axis in risk_axes}
    fired = {axis: value for axis, value in risk_scores.items() if value is not None}
    highest_risk = max(fired, key=lambda axis: fired[axis]) if fired else None
    max_risk = max(fired.values(), default=None)
    reasons: list[str] = []
    threshold_error = _policy_threshold_error(policy, [
        ("hard", "from_untrusted"), ("hard", "directed_injection"),
        ("risk", "ask"), ("risk", "deny"),
        ("mitigators", "authorized"), ("mitigators", "user_requested"),
    ])
    if threshold_error:
        return {"verdict": "ask", "reasons": threshold_error, "answers": copied, "risk_scores": risk_scores, "max_risk": max_risk, "highest_risk": highest_risk}
    if missing:
        reasons.append("missing_or_malformed_required_axes:" + ",".join(missing))
        return {"verdict": "ask", "reasons": reasons, "answers": copied, "risk_scores": risk_scores, "max_risk": max_risk, "highest_risk": highest_risk}

    hard_untrusted = _threshold(policy, "hard", "from_untrusted")
    hard_injection = _threshold(policy, "hard", "directed_injection")
    deny_line = _threshold(policy, "risk", "deny")
    ask_line = _threshold(policy, "risk", "ask")
    authorized_line = _threshold(policy, "mitigators", "authorized")
    requested_line = _threshold(policy, "mitigators", "user_requested")
    assert hard_untrusted is not None and hard_injection is not None
    assert deny_line is not None and ask_line is not None
    assert authorized_line is not None and requested_line is not None
    directed = _noul(answers, "directed_injection")
    from_untrusted = _noul(answers, "from_untrusted")
    if (from_untrusted is not None and from_untrusted >= hard_untrusted) or (directed is not None and directed >= hard_injection):
        axis = "from_untrusted" if from_untrusted is not None and from_untrusted >= hard_untrusted else "directed_injection"
        return {"verdict": "deny", "reasons": [f"hard_{axis}"], "answers": copied, "risk_scores": risk_scores, "max_risk": max_risk, "highest_risk": highest_risk}

    unsoftenable = set(policy.get("unsoftenable_axes", []))
    fired_unsoftenable = [axis for axis, value in fired.items() if axis in unsoftenable and value >= ask_line]
    base = "allow"
    if max_risk is not None and max_risk >= deny_line:
        base = "deny"
        reasons.append(f"risk_at_or_above_deny:{highest_risk}")
    elif max_risk is not None and max_risk >= ask_line:
        base = "ask"
        reasons.append(f"risk_at_or_above_ask:{highest_risk}")

    authorized = _noul(answers, "authorized") or 0.0
    user_requested = _noul(answers, "user_requested") or 0.0
    softened_deny = False
    if authorized >= authorized_line and base in {"deny", "ask"} and not fired_unsoftenable:
        if base == "deny":
            base = "ask"
            softened_deny = True
            reasons.append("authorized_softened_deny")
        else:
            base = "allow"
            reasons.append("authorized_softened_ask")
    if user_requested >= requested_line and base == "ask" and not softened_deny and not fired_unsoftenable:
        base = "allow"
        reasons.append("user_requested_softened_ask")
    if fired_unsoftenable:
        reasons.append("unsoftenable_axis:" + ",".join(fired_unsoftenable))
    return {"verdict": base, "reasons": reasons, "answers": copied, "risk_scores": risk_scores, "max_risk": max_risk, "highest_risk": highest_risk}


def classify_needs_second_pass(answers: dict[str, Any], policy: dict[str, Any]) -> bool:
    copied, _ = _enrich_answers(answers)
    band = policy.get("thresholds", {}).get("second_pass", {})
    if not all(isinstance(band.get(key), (int, float)) for key in ("uncertain_min", "uncertain_max")):
        return False
    low = float(band["uncertain_min"])
    high = float(band["uncertain_max"])
    for axis in policy.get("risk_axes", []):
        value = _noul(answers, axis)
        if value is not None and low <= value <= high:
            return True
    severity = copied.get("severity", {})
    high_index = policy.get("severity", {}).get("high_index")
    probabilities = severity.get("probabilities") if isinstance(severity, dict) else None
    high_key = str(high_index) if high_index is not None else ""
    high_score = isinstance(probabilities, dict) and high_key in probabilities and probabilities.get(high_key) == max(probabilities.values())
    low_line = band.get("low_risk_decisiveness")
    risk_decisiveness = [2.0 * abs(value - 0.5) for axis in policy.get("risk_axes", []) if (value := _noul(answers, axis)) is not None]
    low_decisiveness = isinstance(low_line, (int, float)) and any(value <= float(low_line) for value in risk_decisiveness)
    return bool(high_score and low_decisiveness)


def evaluate_classify(answers: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    copied, invalid = _enrich_answers(answers)
    risk_axes = list(policy.get("risk_axes", []))
    scores = {axis: _noul(answers, axis) for axis in risk_axes}
    missing = [axis for axis in risk_axes if axis not in answers or axis in invalid or _noul(answers, axis) is None]
    threshold_error = _policy_threshold_error(policy, [("verdict", "ask"), ("verdict", "deny")])
    if threshold_error:
        return {"verdict": "ask", "reasons": threshold_error, "answers": copied, "risk_scores": scores, "needs_second_pass": False}
    deny_line = _threshold(policy, "verdict", "deny")
    ask_line = _threshold(policy, "verdict", "ask")
    assert deny_line is not None and ask_line is not None
    highest = max((v for v in scores.values() if v is not None), default=None)
    if missing:
        verdict = "ask"
        reasons = ["missing_or_malformed:" + ",".join(missing)]
    elif highest is not None and highest >= deny_line:
        verdict = "deny"
        reasons = ["classification_risk_at_or_above_deny"]
    elif highest is not None and highest >= ask_line:
        verdict = "ask"
        reasons = ["classification_risk_uncertain"]
    else:
        verdict = "allow"
        reasons = ["classification_risk_below_ask"]
    return {"verdict": verdict, "reasons": reasons, "answers": copied, "risk_scores": scores, "needs_second_pass": classify_needs_second_pass(answers, policy)}


def _score_level(answer: Any) -> int | None:
    if not isinstance(answer, dict):
        return None
    value = answer.get("score")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        return None
    return int(round(value))


def evaluate_route(answers: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    copied, invalid = _enrich_answers(answers)
    questions = policy.get("questions", {})
    destination_answer = answers.get("destination")
    destination = destination_answer.get("choice") if isinstance(destination_answer, dict) else None
    destinations = policy.get("destinations", questions.get("destination", {}).get("criteria", {}))
    valid_destinations = set(destinations) if isinstance(destinations, dict) else set(destinations or [])
    completeness = _score_level(answers.get("completeness"))
    complete_index = int(policy.get("completeness", {}).get("complete_index", 2))
    escalate = _noul(answers, "escalate")
    reasons: list[str] = []
    if invalid or destination not in valid_destinations:
        reasons.append("unknown_or_malformed_destination")
    if completeness is None or completeness < complete_index:
        reasons.append("incomplete_state")
    escalate_line = _threshold(policy, "verdict", "escalate")
    if escalate_line is None:
        reasons.append("invalid_policy_thresholds:verdict.escalate")
        return {"verdict": "ask", "reasons": reasons, "answers": copied, "destination": destination, "completeness_level": completeness}
    if escalate is None:
        reasons.append("missing_or_malformed_escalation")
    elif escalate >= escalate_line:
        destination = "human" if "human" in valid_destinations else destination
        reasons.append("escalation_requested")
    verdict = "allow" if not reasons else "ask"
    return {"verdict": verdict, "reasons": reasons, "answers": copied, "destination": destination, "completeness_level": completeness}


def evaluate_verify(answers: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    copied, _ = _enrich_answers(answers)
    threshold = _threshold(policy, "verdict", "supported_by_source")
    if threshold is None:
        return {"verdict": "ask", "reasons": ["invalid_policy_thresholds:verdict.supported_by_source"], "claims": {}, "answers": copied}
    claims: dict[str, Any] = {}
    reasons: list[str] = []
    for claim_id, answer in copied.items():
        probability = _noul(answers, claim_id)
        if probability is None:
            claim = deepcopy(answer)
            claim["verdict"] = "flagged"
            claim["reason"] = "missing_or_malformed_supported_by_source"
            reasons.append(f"flagged:{claim_id}")
        elif probability >= threshold:
            claim = deepcopy(answer)
            claim["verdict"] = "pass"
        else:
            claim = deepcopy(answer)
            claim["verdict"] = "flagged"
            claim["reason"] = "below_supported_by_source_threshold"
            reasons.append(f"flagged:{claim_id}")
        claims[str(claim_id)] = claim
    return {"verdict": "allow" if claims and not reasons else "deny", "reasons": reasons, "claims": claims, "answers": copied}


def _line(lines: Any, key: str, default: float) -> float:
    value = lines.get(key) if isinstance(lines, dict) else None
    if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value):
        return float(value)
    return default


def _score_label(answer: Any, levels: list[Any]) -> Any:
    """The chosen level of a score answer: by argmax of its probabilities, mapped
    through ``levels`` whether the probabilities are keyed by level name or index;
    falls back to the ``score`` index. ``None`` when unresolvable."""
    if not isinstance(answer, dict):
        return None
    probabilities = answer.get("probabilities")
    key: Any = None
    if isinstance(probabilities, dict):
        numeric = {k: v for k, v in probabilities.items()
                   if isinstance(v, (int, float)) and not isinstance(v, bool)}
        if numeric:
            key = max(numeric, key=lambda k: numeric[k])
    if key is None:
        index = answer.get("score")
        if isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(levels):
            return levels[index]
        return None
    if key in levels:
        return key
    try:
        index = int(key)
    except (TypeError, ValueError):
        return key
    return levels[index] if 0 <= index < len(levels) else key


def _choice_top(answer: Any) -> tuple[str | None, float | None]:
    if not isinstance(answer, dict):
        return None, None
    choice = answer.get("choice")
    probabilities = answer.get("probabilities")
    top = None
    if isinstance(probabilities, dict):
        numeric = [v for v in probabilities.values() if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if numeric:
            top = max(numeric)
    return (choice if isinstance(choice, str) else None), top


def evaluate_constraints(answers: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    """Pure constraint check (IP-009 / IDEA-002). ``answers`` is keyed by constraint
    id, each value ``{held: <noul>, severity: <score>}``. A constraint holds at or
    above ``pass_line`` with decisiveness at or above ``min_decisiveness``, fails
    below ``fail_line``, and is uncertain otherwise. The verdict is
    ``no_feasible_candidate`` if any fails (failing ids first-failing-first in the
    caller's order, with severity), ``ask`` if any is uncertain, else ``pass``. No
    label proposes a fix."""
    lines = policy.get("lines", {})
    pass_line = _line(lines, "pass_line", 0.70)
    fail_line = _line(lines, "fail_line", 0.30)
    min_decisiveness = _line(lines, "min_decisiveness", 0.40)
    levels = policy.get("severity_levels", []) if isinstance(policy.get("severity_levels"), list) else []
    constraints: dict[str, Any] = {}
    failing: list[dict[str, Any]] = []
    uncertain: list[str] = []
    for raw_id, group in answers.items():
        cid = str(raw_id)
        held = _noul(group, "held") if isinstance(group, dict) else None
        severity = _score_label(group.get("severity"), levels) if isinstance(group, dict) else None
        entry: dict[str, Any] = {"held": held, "severity": severity}
        if held is None:
            entry["status"] = "uncertain"
            entry["reason"] = "missing_or_malformed_held"
            uncertain.append(cid)
        else:
            entry["decisiveness"] = 2.0 * abs(held - 0.5)
            if held < fail_line:
                entry["status"] = "fails"
                failing.append({"id": cid, "severity": severity})
            elif held >= pass_line and entry["decisiveness"] >= min_decisiveness:
                entry["status"] = "holds"
            else:
                entry["status"] = "uncertain"
                uncertain.append(cid)
        constraints[cid] = entry
    if failing:
        verdict, reasons = "no_feasible_candidate", [f"fails:{item['id']}" for item in failing]
    elif uncertain:
        verdict, reasons = "ask", [f"uncertain:{cid}" for cid in uncertain]
    else:
        verdict, reasons = "pass", []
    return {"verdict": verdict, "reasons": reasons, "constraints": constraints,
            "failing": failing, "uncertain": uncertain}


def evaluate_validity(answers: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    """Pure document-validity check (IP-009 / IDEA-002). ``answers`` has a
    ``check.<id>`` noul per spec check, a ``document_type`` choice (including the
    always-present unknown), and a ``completeness`` score. Abstains when the type
    is unknown or its top probability is below ``type_line``; otherwise invalid if
    any check fails, ask if any is uncertain, else valid with the completeness
    level attached. A valid verdict says the form is filled, nothing more — the
    result carries ``validity is not support``."""
    lines = policy.get("lines", {})
    pass_line = _line(lines, "pass_line", 0.70)
    fail_line = _line(lines, "fail_line", 0.30)
    min_decisiveness = _line(lines, "min_decisiveness", 0.40)
    type_line = _line(lines, "type_line", 0.50)
    unknown = policy.get("unknown_type", "unknown")
    completeness_levels = policy.get("completeness_levels", []) if isinstance(policy.get("completeness_levels"), list) else []
    checks: dict[str, Any] = {}
    failing: list[str] = []
    uncertain: list[str] = []
    for key, answer in answers.items():
        if not str(key).startswith("check."):
            continue
        cid = str(key)[len("check."):]
        held = _probability(answer)
        entry: dict[str, Any] = {"holds": held}
        if held is None:
            entry["status"] = "uncertain"
            entry["reason"] = "missing_or_malformed_check"
            uncertain.append(cid)
        else:
            entry["decisiveness"] = 2.0 * abs(held - 0.5)
            if held < fail_line:
                entry["status"] = "fails"
                failing.append(cid)
            elif held >= pass_line and entry["decisiveness"] >= min_decisiveness:
                entry["status"] = "holds"
            else:
                entry["status"] = "uncertain"
                uncertain.append(cid)
        checks[cid] = entry
    document_type, type_top = _choice_top(answers.get("document_type"))
    completeness = _score_label(answers.get("completeness"), completeness_levels)
    note = policy.get("validity_is_not_support")
    if document_type is None or document_type == unknown or (type_top is not None and type_top < type_line):
        verdict, reasons = "abstain", ["document_type_unknown_or_below_line"]
    elif failing:
        verdict, reasons = "invalid", [f"fails:{cid}" for cid in failing]
    elif uncertain:
        verdict, reasons = "ask", [f"uncertain:{cid}" for cid in uncertain]
    else:
        verdict, reasons = "valid", []
    return {"verdict": verdict, "reasons": reasons, "checks": checks,
            "document_type": document_type, "document_type_top": type_top,
            "completeness": completeness, "failing": failing, "uncertain": uncertain,
            "note": note}
