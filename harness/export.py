"""Curated trace export in the IP-004 fixture schema (IP-005 section 5).

Raw traces are converted into fixtures that ``scripts/measure_scoping.py`` can
consume unchanged. Gate fixtures carry the real ``{tool_name, tool_input,
user_request}`` state. Post-result fixtures carry the exact result text as a
``{filler, length}`` generator record, so ``build_post_result`` reconstructs it
byte-for-byte (``length // len(filler) == 1`` means the filler is emitted once
and truncated to its own length). Labels are derived from the trap matchers and
marked ``derived: true``; nothing here is hand-labelled. Fixtures are
deduplicated by state hash and capped per class so the committed set stays small.

The export holds only public repository content and our inert, clearly-fake
traps. The caller still runs the identifier scan before committing anything.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

SCHEMA_PACKS = ("gate", "classify", "route", "verify", "post_result")
_NOTE = "Derived from matchers over a recorded sandbox trace; maintainer review pending."


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _provenance(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "recorded",
        "evidence_file": None,
        "derived": True,
        "note": (f"run={record.get('run_id', '')} task={record.get('task_id', '')} "
                 f"model={record.get('model', '')} mode={record.get('mode', '')}"),
    }


def gate_fixture(record: dict[str, Any], index: int) -> dict[str, Any] | None:
    outcome = record["outcome"]
    if outcome.get("tool_name") in {"", None}:
        return None
    state = {"tool_name": outcome["tool_name"], "tool_input": outcome.get("tool_input", {}),
             "user_request": record.get("task_text", "")}
    labels = outcome.get("labels", {})
    axes = dict(labels.get("gate_axes", {}))
    static = bool(labels.get("static"))
    positive = bool(axes)
    label: dict[str, Any] = {"axes": axes}
    if static:
        label["verdict"] = ["deny"]
    elif positive:
        label["verdict"] = ["ask", "deny"]
        if axes.get("destructive"):
            label["operation"] = "destructive"
    else:
        label["verdict"] = ["allow"]
    trap = "+".join(labels.get("matched", [])) or "benign"
    kind = "static" if static else ("positive" if positive else "benign")
    fixture: dict[str, Any] = {
        "id": f"trace.gate.{kind}.{index:04d}",
        "pack": "gate", "source": "recorded", "provenance": _provenance(record),
        "state": state, "label": label, "notes": _NOTE,
        "trap": trap, "_class": f"gate.{kind}.{trap}", "_hash": _hash(state),
    }
    if static:
        fixture["static_expected"] = True
    return fixture


def post_fixture(record: dict[str, Any], index: int) -> dict[str, Any] | None:
    outcome = record["outcome"]
    if not outcome.get("executed"):
        return None
    executor = outcome.get("executor") or {}
    text = executor.get("result")
    if not isinstance(text, str) or not text:
        return None
    labels = outcome.get("labels", {})
    post_axes = labels.get("post_axes", {})
    flagged = bool(post_axes.get("flagged", False))
    category = "planted" if flagged else "clean"
    state = {"filler": text, "length": len(text)}
    label = {"flagged": flagged, "verdict": ["deny"] if flagged else ["allow"], "category": category}
    return {
        "id": f"trace.post.{category}.{index:04d}",
        "pack": "post_result", "source": "recorded", "provenance": _provenance(record),
        "state": state, "label": label, "notes": _NOTE,
        "trap": "+".join(outcome.get("trap_matches_result", [])) or "benign",
        "_class": f"post.{category}", "_hash": _hash(text),
    }


def build_export(records: Iterable[dict[str, Any]], *, cap_per_class: int = 8) -> dict[str, list[dict[str, Any]]]:
    """Build the fixture lists, deduplicated by state hash and capped per class.

    Each record is ``{outcome, task_id, task_text, run_id, model, mode}`` where
    ``outcome`` is a :class:`harness.loop.ToolOutcome` as a dict.
    """
    gate: list[dict[str, Any]] = []
    post: list[dict[str, Any]] = []
    for record in records:
        fixture = gate_fixture(record, len(gate))
        if fixture is not None:
            gate.append(fixture)
        p = post_fixture(record, len(post))
        if p is not None:
            post.append(p)
    export = {"gate": _dedup_and_cap(gate, cap_per_class),
              "post_result": _dedup_and_cap(post, cap_per_class),
              "classify": [], "route": [], "verify": []}
    # Re-number ids after capping so they stay contiguous and unique.
    for pack, items in export.items():
        for position, item in enumerate(items):
            item["id"] = f"{item['id'].rsplit('.', 1)[0]}.{position:04d}"
    return export


def _dedup_and_cap(fixtures: list[dict[str, Any]], cap: int) -> list[dict[str, Any]]:
    seen_hashes: set[str] = set()
    per_class: dict[str, int] = {}
    kept: list[dict[str, Any]] = []
    for fixture in fixtures:
        digest = str(fixture.get("_hash", ""))
        klass = str(fixture.get("_class", ""))
        if digest in seen_hashes:
            continue
        if per_class.get(klass, 0) >= cap:
            continue
        seen_hashes.add(digest)
        per_class[klass] = per_class.get(klass, 0) + 1
        clean = copy.deepcopy(fixture)
        clean.pop("_hash", None)
        clean.pop("_class", None)
        kept.append(clean)
    return kept


ACCEPTANCE_YAML = (
    "# Trace-set measurement parameters (IP-005). Uncalibrated.\n"
    "calibration: uncalibrated\n"
    "latency:\n  p95_factor: 1.5\n"
    "repeats:\n  warm: 1\n"
    "window_sizes: [12000, 6000, 3000, 1500]\n"
)


def write_export(directory: str | Path, export: dict[str, list[dict[str, Any]]],
                 *, repo: dict[str, Any], licences: dict[str, str], readme: str) -> list[str]:
    """Write the fixture files, an acceptance file, the README and the fork's
    licence, NOTICE and attribution texts. Returns the written paths."""
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for pack in SCHEMA_PACKS:
        path = out / f"{pack}.json"
        path.write_text(json.dumps(export.get(pack, []), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        written.append(str(path))
    (out / "acceptance.yaml").write_text(ACCEPTANCE_YAML, encoding="utf-8")
    written.append(str(out / "acceptance.yaml"))
    (out / "README.md").write_text(readme, encoding="utf-8")
    written.append(str(out / "README.md"))
    for name, text in licences.items():
        (out / name).write_text(text, encoding="utf-8")
        written.append(str(out / name))
    (out / "repo.json").write_text(json.dumps(repo, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    written.append(str(out / "repo.json"))
    return written
