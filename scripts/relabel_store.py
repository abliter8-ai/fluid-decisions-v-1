#!/usr/bin/env python3
"""Re-derive trap labels and the harness summaries from a raw IP-005 trace store
with the corrected IP-006 registry, offline (IP-006 section 0).

CR-005's trap-catch counted reads of the cleanup scripts as T2 attempts and its
benign post-result flags include the harness's own fixture marker. This recomputes
``trap_matches_call``, ``trap_matches_result`` and the derived labels for every
recorded tool call with the corrected ``harness/traps.yaml`` (execution-only T2,
T3 follow-through), then writes the per-model, per-mode summaries beside the
originals so CR-006 can show the relabelled baseline like for like.

No lane, no container, deterministic. Counts only; no state or result text is
written.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from harness.summary import summarize
from harness.traps import Trap, derive_labels, load_traps, match_call, match_result

ROOT = Path(__file__).resolve().parents[1]


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def relabel_outcome(outcome: dict[str, Any], traps: list[Trap]) -> dict[str, Any]:
    """Recompute an outcome's trap matches and derived labels in place (pure)."""
    name = outcome.get("tool_name", "")
    raw_input = outcome.get("tool_input")
    tool_input: dict[str, Any] = raw_input if isinstance(raw_input, dict) else {}
    result = None
    if outcome.get("executed"):
        executor = outcome.get("executor") or {}
        value = executor.get("result")
        result = value if isinstance(value, str) else None
    outcome["trap_matches_call"] = [m.trap_id for m in match_call(traps, name, tool_input)]
    outcome["trap_matches_result"] = ([m.trap_id for m in match_result(traps, result)]
                                      if isinstance(result, str) else [])
    outcome["labels"] = derive_labels(traps, name, tool_input, result)
    return outcome


def relabel_store(store: Path, traps: list[Trap]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Load every result file, relabel its outcomes, group by (model, mode)."""
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for path in sorted(store.glob("*.result.json")):
        result = json.loads(path.read_text(encoding="utf-8"))
        for outcome in result.get("outcomes", []):
            relabel_outcome(outcome, traps)
        grouped[(result.get("model", ""), result.get("mode", ""))].append(result)
    return grouped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", required=True, help="raw trace store dir (…/<run-id>)")
    parser.add_argument("--traps", default=str(ROOT / "harness" / "traps.yaml"))
    parser.add_argument("--out-dir", default=str(ROOT / "docs"))
    args = parser.parse_args()
    store = Path(args.store)
    if not store.is_dir():
        print(json.dumps({"error": "store directory not found"}))
        return 2
    traps = load_traps(args.traps)
    trap_ids = [t.id for t in traps]
    grouped = relabel_store(store, traps)
    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for (model, mode), results in sorted(grouped.items()):
        summary = summarize(results, model=model, mode=mode, trap_ids=trap_ids)
        path = out_dir / f"trace-harness-relabelled-{_slug(model)}-{mode}.json"
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        written.append(path.name)
        tb = summary["timeouts_and_budgets"]
        line = {"model": _slug(model), "mode": mode,
                "gate_timeouts": tb.get("gate_timeouts"), "gate_timeouts_per_100": tb.get("gate_timeouts_per_100")}
        if mode == "enforce":
            fr = summary["friction"]
            line.update(blocked_per_100=fr["blocked_per_100"],
                        blocked_per_100_excluding_infrastructure=fr["blocked_per_100_excluding_infrastructure"],
                        infrastructure_asks=fr["infrastructure_asks"])
            line["post_result_flags"] = summary["post_result_flags"]
        if mode == "shadow":
            line["trap_catch"] = {t: v for t, v in summary["trap_catch"].items() if not v["never_attempted"]}
            line["post_result_flags"] = summary["post_result_flags"]
        print(json.dumps(line, ensure_ascii=False))
    print(json.dumps({"written": written}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
