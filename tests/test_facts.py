"""Unit tests for the pure gate-facts builder (IP-006 section 2)."""
from __future__ import annotations

from fluid import facts

POLICY = {
    "facts": {
        "command_fields": ["command", "cmd", "argv", "script", "query"],
        "path_fields": ["path", "paths", "file"],
        "content_fields": ["content", "text", "body"],
        "constraint_cues": [r'\bdo not\b', r"\bdon't\b", r'\bonly\b', r'\bbefore\b', r'\bafter\b',
                            r'\bthen\b', r'\bwithout\b', r'\bask\b', r'\bmust not\b', r'\bnever\b'],
        "max_constraints": 4,
        "max_sentence_chars": 240,
        "script_max_chars": 4000,
    }
}
QUESTIONS = {
    "action": {"destructive": {"type": "noul", "item": "action", "instructions": "?"},
               "operation": {"type": "score", "item": "action", "criteria": ["read_only", "mutating"], "instructions": "?"}},
    "task_action": {"target_in_task": {"type": "noul", "item": "task_action", "instructions": "?"},
                    "authorized": {"type": "noul", "item": "task_action", "instructions": "?"}},
    "constraint": {"violates": {"type": "noul", "item": "constraint", "instructions": "?"}},
}


def test_build_action_summarises_content_and_keeps_command_and_path():
    action = facts.build_action({"path": "src/x.rs", "content": "a" * 5000, "command": "cargo test"}, POLICY)
    assert action["path"] == "src/x.rs"
    assert action["command"] == "cargo test"
    assert action["content"] == {"chars": 5000}  # body summarised, not carried


def test_constraint_sentences_selected_by_cues_capped_and_truncated():
    task = ("Update the README. Do not touch git state. Only edit one file. "
            "Ask before choosing a target. Run the tests normally.")
    sentences = facts.constraint_sentences(task, POLICY)
    assert any("do not touch git state" in s.lower() for s in sentences)
    assert any("only edit one file" in s.lower() for s in sentences)
    assert any("ask before choosing" in s.lower() for s in sentences)
    assert all("run the tests normally" not in s.lower() for s in sentences)  # no cue
    assert len(sentences) <= 4


def test_cues_are_word_bounded_ask_does_not_match_task():
    # "task" contains "ask" but must not trigger the \bask\b cue (IP-007 §0).
    assert facts.constraint_sentences("Finish the task and commit.", POLICY) == []
    # ordering cues after/then are enumerated.
    assert facts.constraint_sentences("Stage the files, then stop.", POLICY) == ["Stage the files, then stop."]


def test_build_gate_facts_emits_action_task_and_constraint_items():
    task = "Rename the helper. Do not change its call sites. Only touch the color crate."
    items = facts.build_gate_facts("write_file", {"path": "crates/color/src/lib.rs", "content": "x" * 3000},
                                   task, QUESTIONS, POLICY)
    kinds = [i["item"] for i in items]
    assert kinds[0] == "action" and kinds[1] == "task_action"
    assert sum(k.startswith("constraint.") for k in kinds) == 2
    # The action state never carries the content body.
    assert items[0]["state"]["action"]["content"] == {"chars": 3000}
    # The task_action state pairs the task with the action, nothing else.
    assert set(items[1]["state"]) == {"task", "action"}
    # Each constraint item pairs one instruction sentence with the action.
    constraint = next(i for i in items if i["item"].startswith("constraint."))
    assert set(constraint["state"]) == {"instruction", "action"}
    assert list(constraint["questions"]) == ["violates"]
    # The grouping key is stripped from the questions sent to the lane.
    assert "item" not in items[0]["questions"]["destructive"]


def test_script_content_is_included_and_truncated_when_given():
    items = facts.build_gate_facts("run_shell", {"command": "bash scripts/cleanup.sh"}, "clean up",
                                   QUESTIONS, POLICY, script_content="rm -rf build\n" * 1000)
    assert "script" in items[0]["state"] and len(items[0]["state"]["script"]) <= 4000


def test_no_constraint_sentences_yields_only_action_and_task_action():
    items = facts.build_gate_facts("read_file", {"path": "README.md"}, "read the readme", QUESTIONS, POLICY)
    assert [i["item"] for i in items] == ["action", "task_action"]


# ---- composition (fluid.policy.compose_gate_facts) ----

COMPOSE_POLICY = {
    "risk_order": ["read_only", "mutating", "destructive", "irreversible"],
    "facts": {"tool_operation": {"read_file": "read_only", "list_dir": "read_only",
                                 "write_file": "mutating", "run_tests": "mutating"}},
}


def _noul(p):
    return {"type": "noul", "noul": p}


def test_compose_off_task_is_zero_for_read_only_regardless_of_target():
    from fluid.policy import compose_gate_facts
    answers = {"action": {"destructive": _noul(0.02)},
               "task_action": {"target_in_task": _noul(0.0), "authorized": _noul(0.9)}}
    composed = compose_gate_facts("read_file", answers, COMPOSE_POLICY)
    assert composed["answers"]["off_task"]["noul"] == 0.0  # read-only is never off-task
    assert composed["answers"]["operation"]["score"] == 0  # read_only from tool_operation
    assert composed["answers"]["destructive"]["noul"] == 0.0  # read_only_axes_zeroed (IP-007)
    assert composed["answers"]["authorized"] == _noul(0.9)  # not an action axis, untouched


def test_compose_off_task_from_target_when_mutating():
    from fluid.policy import compose_gate_facts
    answers = {"action": {}, "task_action": {"target_in_task": _noul(0.2)}}
    composed = compose_gate_facts("write_file", answers, COMPOSE_POLICY)
    assert abs(composed["answers"]["off_task"]["noul"] - 0.8) < 1e-9
    assert composed["answers"]["operation"]["score"] == 1  # mutating


def test_compose_read_only_zeroes_the_action_axes_but_not_exposure():
    from fluid.policy import compose_gate_facts
    # read_file is read_only in tool_operation; the model flagged every axis high.
    answers = {"action": {"destructive": _noul(0.9), "privilege": _noul(0.9),
                          "exfiltration": _noul(0.9), "secret_exposure": _noul(0.9)},
               "task_action": {"target_in_task": _noul(0.1), "unresolved_choice": _noul(0.9)},
               "constraint.0": {"violates": _noul(0.9)}}
    composed = compose_gate_facts("read_file", answers, COMPOSE_POLICY)
    a = composed["answers"]
    assert a["destructive"]["noul"] == 0.0 and a["privilege"]["noul"] == 0.0
    assert a["unresolved_choice"]["noul"] == 0.0 and a["violates_constraint"]["noul"] == 0.0
    assert a["off_task"]["noul"] == 0.0
    assert "read_only_axes_zeroed" in composed["reasons"]
    # Exposure axes are untouched: reading a secret is still an exposure.
    assert a["secret_exposure"] == _noul(0.9) and a["exfiltration"] == _noul(0.9)


def test_compose_mutating_keeps_the_action_axes():
    from fluid.policy import compose_gate_facts
    answers = {"action": {"destructive": _noul(0.8)}, "task_action": {"target_in_task": _noul(0.9)}}
    composed = compose_gate_facts("write_file", answers, COMPOSE_POLICY)  # mutating
    assert composed["answers"]["destructive"]["noul"] == 0.8
    assert "read_only_axes_zeroed" not in composed["reasons"]


def test_compose_read_only_by_model_score_for_shell(monkeypatch):
    from fluid.policy import compose_gate_facts
    read_only_op = {"type": "score", "score": 0, "legend": {"0": "read_only", "1": "mutating",
                    "2": "destructive", "3": "irreversible"},
                    "probabilities": {"0": 1.0, "1": 0.0, "2": 0.0, "3": 0.0}}
    answers = {"action": {"operation": read_only_op, "destructive": _noul(0.9)}, "task_action": {}}
    composed = compose_gate_facts("run_shell", answers, COMPOSE_POLICY)  # unknown tool -> model score
    assert composed["answers"]["destructive"]["noul"] == 0.0
    assert "read_only_axes_zeroed" in composed["reasons"]


def test_compose_violates_constraint_is_max_or_zero_with_reason():
    from fluid.policy import compose_gate_facts
    answers = {"action": {}, "task_action": {"target_in_task": _noul(1.0)},
               "constraint.0": {"violates": _noul(0.1)}, "constraint.1": {"violates": _noul(0.76)}}
    composed = compose_gate_facts("write_file", answers, COMPOSE_POLICY)
    assert abs(composed["answers"]["violates_constraint"]["noul"] - 0.76) < 1e-9
    none = compose_gate_facts("write_file", {"action": {}, "task_action": {}}, COMPOSE_POLICY)
    assert none["answers"]["violates_constraint"]["noul"] == 0.0
    assert "no_constraint_sentences" in none["reasons"]


# ---- interpreter detection and the operation floor ----

INTERP_POLICY = {
    "facts": {
        "command_fields": ["command", "cmd"],
        "interpreter_pattern": r'(?:^|[;&|]\s*|\bbash\s+|\bsh\s+|\bpython3?\s+|\./)([^\s;&|]+\.(?:sh|py|rb|js|pl))',
    }
}


def test_interpreter_script_path_matches_execution_not_reads():
    assert facts.interpreter_script_path({"command": "bash scripts/cleanup.sh"}, INTERP_POLICY) == "scripts/cleanup.sh"
    assert facts.interpreter_script_path({"command": "python3 scripts/cleanup.py --purge"}, INTERP_POLICY) == "scripts/cleanup.py"
    # Captured verbatim; a cache lookup that misses on the "./" form just floors, which is safe.
    assert facts.interpreter_script_path({"cmd": "./deploy.sh"}, INTERP_POLICY) == "./deploy.sh"
    # Reading a script with cat is benign exploration, not an interpreter run.
    assert facts.interpreter_script_path({"command": "cat scripts/cleanup.sh"}, INTERP_POLICY) is None
    assert facts.interpreter_script_path({"command": "cargo test"}, INTERP_POLICY) is None
    assert facts.interpreter_script_path({"path": "scripts/cleanup.sh"}, INTERP_POLICY) is None  # not a command field


def test_compose_operation_floor_raises_but_never_lowers():
    from fluid.policy import compose_gate_facts
    read_only = {"type": "score", "score": 0, "legend": {"0": "read_only", "1": "mutating", "2": "destructive", "3": "irreversible"},
                 "probabilities": {"0": 1.0, "1": 0.0, "2": 0.0, "3": 0.0}}
    destructive = {**read_only, "score": 2, "probabilities": {"0": 0.0, "1": 0.0, "2": 1.0, "3": 0.0}}
    # Model said read-only; the floor raises it to mutating and records the reason.
    floored = compose_gate_facts("run_shell", {"action": {"operation": read_only}, "task_action": {}},
                                 COMPOSE_POLICY, operation_floor="mutating")
    assert floored["answers"]["operation"]["score"] == 1
    assert "interpreter_floor" in floored["reasons"]
    # Model already said destructive; the floor must not lower it.
    kept = compose_gate_facts("run_shell", {"action": {"operation": destructive}, "task_action": {}},
                              COMPOSE_POLICY, operation_floor="mutating")
    assert kept["answers"]["operation"]["score"] == 2
    assert "interpreter_floor" not in kept["reasons"]
