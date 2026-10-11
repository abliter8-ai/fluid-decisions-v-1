#!/usr/bin/env python3
"""Offline replay of the provenance floor over a raw IP-005 trace store
(IP-006 section 1).

Replays every run in order: observes each executed result into a per-run carry
set (command spans, and secret values when the result was read from a secret
path), ages the carry by calls, and checks each tool call against it with no
model and no lane. Reports, per worker model and mode, how many T2 model-variant
executions, T4 model-variant commands, and T1 and T3 follow-through calls the
floor would ask or deny, and the deterministic ask/deny rate on benign calls both
with and without the read-only allowlist.

Counts only; deterministic across runs; needs no lane. Output goes to
``docs/provenance-replay-<run-id>.json``.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from fluid import provenance
from fluid.policy import load_pack
from harness.traps import load_traps, match_call, match_result

ROOT = Path(__file__).resolve().parents[1]


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _source_class(outcome: dict[str, Any]) -> str:
    status = ((outcome.get("post_result") or {}).get("check") or {}).get("status")
    mapping = {"scored_flagged": "flagged", "unscored": "pending"}
    return mapping.get(status, "result") if isinstance(status, str) else "result"


def _observe(outcome: dict[str, Any], policy: dict[str, Any],
             spans: dict[str, dict[str, Any]], secrets: dict[str, dict[str, Any]]) -> None:
    executor = outcome.get("executor") or {}
    text = executor.get("result")
    if not isinstance(text, str) or not text:
        return
    tool = outcome.get("tool_name", "unknown")
    source_class = _source_class(outcome)
    for h in provenance.spans(text, policy):
        # A flagged source outranks a clean one for the same span.
        if h not in spans or {"flagged": 3, "pending": 2, "result": 1}[source_class] > \
                {"flagged": 3, "pending": 2, "result": 1}[spans[h]["source_class"]]:
            spans[h] = {"source_class": source_class, "tool": tool, "calls_since": 0}
    ti = outcome.get("tool_input")
    path = ti.get("path") if isinstance(ti, dict) else None
    for h in provenance.secret_values(text, path, policy):
        secrets[h] = {"source_class": "result", "tool": tool, "calls_since": 0}


def _age(entries: dict[str, dict[str, Any]], window: int) -> None:
    for meta in entries.values():
        meta["calls_since"] += 1
    for h in [h for h, meta in entries.items() if meta["calls_since"] > window]:
        del entries[h]


def replay_run(outcomes: list[dict[str, Any]], policy: dict[str, Any],
               allow_policy: dict[str, Any], input_sentinels: dict[str, list[str]]) -> list[dict[str, Any]]:
    """Check each call against the growing carry; return a per-call record."""
    spans: dict[str, dict[str, Any]] = {}
    secrets: dict[str, dict[str, Any]] = {}
    window = int(policy.get("window_calls", 200))
    records = []
    for outcome in outcomes:
        name = outcome.get("tool_name", "")
        raw_input = outcome.get("tool_input")
        tool_input: dict[str, Any] = raw_input if isinstance(raw_input, dict) else {}
        user_request = outcome.get("user_request", "")
        carry = {"spans": spans, "secrets": secrets}
        with_allow = provenance.check(name, tool_input, user_request, carry, allow_policy)
        without_allow = provenance.check(name, tool_input, user_request, carry, policy)
        # A follow-through is a later call whose own input carries a trap's
        # sentinel (the secret value or the directive), distinct from the read
        # that first exposed it.
        blob = json.dumps(tool_input, ensure_ascii=False)
        carries = [trap_id for trap_id, values in input_sentinels.items()
                   if any(v in blob for v in values)]
        records.append({
            "tool_name": name,
            "benign": bool(outcome.get("labels", {}).get("benign")),
            "traps_call": outcome.get("trap_matches_call", []),
            "input_carry_traps": carries,
            "with_allowlist": with_allow["verdict"] if with_allow else None,
            "without_allowlist": without_allow["verdict"] if without_allow else None,
            "reason": (with_allow or {}).get("reason"),
        })
        _observe(outcome, policy, spans, secrets)
        _age(spans, window)
        _age(secrets, window)
    return records


def _tally(records: list[dict[str, Any]]) -> dict[str, Any]:
    benign = [r for r in records if r["benign"]]
    def rate(key: str, verdict: str) -> int:
        return sum(1 for r in benign if r[key] == verdict)
    # T2/T4 are execution/command actions (trap_matches_call); T1/T3
    # follow-through is a later call carrying the secret value or directive.
    call_classes = {"T2-destructive-model": "t2_exec", "T4-offtask-model": "t4_cmd"}
    carry_classes = {"T1-secret": "t1_follow", "T3-injection": "t3_follow"}
    catches: dict[str, dict[str, int]] = {v: {"calls": 0, "caught": 0}
                                          for v in list(call_classes.values()) + list(carry_classes.values())}
    for r in records:
        for trap, key in call_classes.items():
            if trap in r["traps_call"]:
                catches[key]["calls"] += 1
                if r["with_allowlist"] in {"ask", "deny"}:
                    catches[key]["caught"] += 1
        for trap, key in carry_classes.items():
            if trap in r.get("input_carry_traps", []):
                catches[key]["calls"] += 1
                if r["with_allowlist"] in {"ask", "deny"}:
                    catches[key]["caught"] += 1
    n = len(benign)
    return {
        "benign_calls": n,
        "benign_ask_with_allowlist": rate("with_allowlist", "ask"),
        "benign_deny_with_allowlist": rate("with_allowlist", "deny"),
        "benign_ask_without_allowlist": rate("without_allowlist", "ask"),
        "benign_deny_without_allowlist": rate("without_allowlist", "deny"),
        "benign_floored_per_100_with_allowlist":
            round(100 * (rate("with_allowlist", "ask") + rate("with_allowlist", "deny")) / n, 2) if n else None,
        "benign_floored_per_100_without_allowlist":
            round(100 * (rate("without_allowlist", "ask") + rate("without_allowlist", "deny")) / n, 2) if n else None,
        "catches": catches,
    }


def replay_store(store: Path, policy: dict[str, Any], task_text: dict[str, str],
                 traps_path: Path) -> dict[str, Any]:
    allow_off = copy.deepcopy(policy)
    allow_off["read_only_allowlist"] = []
    traps = load_traps(traps_path)
    input_sentinels: dict[str, list[str]] = {
        t.id: [m.value for m in t.matchers if m.kind == "sentinel" and "tool_input" in m.where and m.value]
        for t in traps
    }
    input_sentinels = {k: v for k, v in input_sentinels.items() if v}
    grouped: dict[tuple[str, str], list[list[dict[str, Any]]]] = defaultdict(list)
    for path in sorted(store.glob("*.result.json")):
        result = json.loads(path.read_text(encoding="utf-8"))
        text = task_text.get(result.get("task_id", ""), "")
        outcomes = result.get("outcomes", [])
        for outcome in outcomes:
            outcome.setdefault("user_request", text)
            # Relabel trap matches with the corrected registry so T2 counts
            # executions, not reads (the replay measures catches on real labels).
            name = outcome.get("tool_name", "")
            tool_input = outcome.get("tool_input") if isinstance(outcome.get("tool_input"), dict) else {}
            executor = outcome.get("executor") or {}
            rtext = executor.get("result") if outcome.get("executed") else None
            outcome["trap_matches_call"] = [m.trap_id for m in match_call(traps, name, tool_input)]
            outcome["trap_matches_result"] = ([m.trap_id for m in match_result(traps, rtext)]
                                              if isinstance(rtext, str) else [])
        grouped[(result.get("model", ""), result.get("mode", ""))].append(outcomes)
    report: dict[str, Any] = {}
    for (model, mode), runs in sorted(grouped.items()):
        records = [rec for run in runs for rec in replay_run(run, policy, allow_off, input_sentinels)]
        report[f"{_slug(model)}|{mode}"] = _tally(records)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", required=True)
    parser.add_argument("--tasks", default=str(ROOT / "harness" / "tasks.yaml"))
    parser.add_argument("--output")
    args = parser.parse_args()
    store = Path(args.store)
    if not store.is_dir():
        print(json.dumps({"error": "store directory not found"}))
        return 2
    policy = load_pack("gate")["policy"]["provenance"]
    tasks_doc = yaml.safe_load(Path(args.tasks).read_text(encoding="utf-8"))
    task_text = {t["id"]: t["text"] for t in tasks_doc["tasks"]}
    report = replay_store(store, policy, task_text, Path(args.tasks).parent / "traps.yaml")
    output = Path(args.output) if args.output else ROOT / "docs" / f"provenance-replay-{store.name}.json"
    if not output.is_absolute():
        output = ROOT / output
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {"store": store.name, "lane": "text", "provenance_policy": policy, "by_model_mode": report}
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for key, tally in report.items():
        print(json.dumps({"model_mode": key, "benign_floored_per_100_with_allowlist":
                          tally["benign_floored_per_100_with_allowlist"],
                          "without_allowlist": tally["benign_floored_per_100_without_allowlist"],
                          "catches": tally["catches"]}, ensure_ascii=False))
    print(json.dumps({"written": str(output.name)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
