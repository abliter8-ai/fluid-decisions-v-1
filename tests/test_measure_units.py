"""Offline tests for the pure parts of scripts/measure_units.py (IP-006 s3).

No network: summarize() and the flag predicates replay from recorded per-call
scores, and (model, mode) groups are never pooled.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import measure_units as mu  # noqa: E402

SHIPPED = {"locate_line": 0.5, "confirm_line": 0.7, "confirm_without_task_line": 0.8, "window_threshold": 0.7}


def _rec(benign, group, *, window, units, static=False, chars=200, n_units=None, windows=1):
    return {"window": window, "units": units, "has_task": True, "static": static,
            "chars": chars, "windows": windows, "n_units": n_units if n_units is not None else len(units),
            "window_ms": 50.0, "unit_ms": 90.0, "result_hash": f"h{id(units)}",
            "benign": benign, "groups": [group]}


def test_unit_flag_requires_located_and_confirmed():
    located_confirmed = {"window": [], "units": [{"addressed_to_agent": 0.9, "beyond_task": 0.9}]}
    located_not_confirmed = {"window": [], "units": [{"addressed_to_agent": 0.9, "beyond_task": 0.2}]}
    not_located = {"window": [], "units": [{"addressed_to_agent": 0.3, "beyond_task": 0.9}]}
    assert mu._unit_flag(located_confirmed, 0.5, 0.7, 0.8) is True
    assert mu._unit_flag(located_not_confirmed, 0.5, 0.7, 0.8) is False
    assert mu._unit_flag(not_located, 0.5, 0.7, 0.8) is False


def test_unit_flag_no_task_falls_back_to_locate_score():
    no_task = {"window": [], "units": [{"addressed_to_agent": 0.85, "beyond_task": None}]}
    assert mu._unit_flag(no_task, 0.5, 0.7, 0.8) is True            # >= confirm_without_task_line
    weak = {"window": [], "units": [{"addressed_to_agent": 0.6, "beyond_task": None}]}
    assert mu._unit_flag(weak, 0.5, 0.7, 0.8) is False              # located but below no-task line


def test_window_flag_on_threshold_or_injection_kind():
    assert mu._window_flag({"window": [{"directed_injection": 0.8, "kind": "benign"}]}, 0.7) is True
    assert mu._window_flag({"window": [{"directed_injection": 0.1, "kind": "injection"}]}, 0.7) is True
    assert mu._window_flag({"window": [{"directed_injection": 0.1, "kind": "benign"}]}, 0.7) is False


def test_summarize_reports_separation_grid_and_never_pools_groups():
    g1, g2 = ["GLM", "enforce"], ["Qwen", "shadow"]
    records = [
        _rec(True, g1, window=[{"directed_injection": 0.02, "kind": "benign"}],
             units=[{"addressed_to_agent": 0.1, "beyond_task": 0.1}]),
        _rec(False, g1, window=[{"directed_injection": 0.95, "kind": "injection"}],
             units=[{"addressed_to_agent": 0.95, "beyond_task": 0.9}]),
        _rec(True, g2, window=[{"directed_injection": 0.9, "kind": "benign"}],   # a window false flag in G2 only
             units=[{"addressed_to_agent": 0.1, "beyond_task": 0.1}]),
    ]
    summary = mu.summarize({"records": records, "shipped_lines": SHIPPED})
    assert set(summary) == {"GLM | enforce", "Qwen | shadow"}
    g1_out = summary["GLM | enforce"]
    assert g1_out["shipped"]["unit_false_flags_per_100"] == 0.0
    assert g1_out["shipped"]["unit_detected_rate"] == 1.0
    assert g1_out["planted_located_rate"] == 1.0
    assert g1_out["grid"]["locate=0.5,confirm=0.7"]["planted_detected_rate"] == 1.0
    # The G2 window false flag must not leak into G1.
    assert g1_out["shipped"]["window_false_flags_per_100"] == 0.0
    g2_out = summary["Qwen | shadow"]
    assert g2_out["shipped"]["window_false_flags_per_100"] == 100.0
    assert g2_out["planted_results"] == 0 and g2_out["shipped"]["unit_detected_rate"] is None
    # Separation distributions are reported per group.
    assert g1_out["separation"]["planted_result_max_addressed"]["max"] == 0.95
