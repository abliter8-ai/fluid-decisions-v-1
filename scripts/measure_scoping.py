#!/usr/bin/env python3
"""Measure question scopes using synthetic decisions; never execute fixture actions."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import statistics
import tempfile
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import yaml

from fluid.audit import Audit
from fluid.client import ClientError, D1Client
from fluid.ledger import Ledger
from fluid.policy import load_pack
from fluid.scoping import strip_scopes
from fluid.service import Decisions

ROOT = Path(__file__).resolve().parents[1]
PACKS = ("gate", "classify", "route", "verify")
FIXTURE_PACKS = (*PACKS, "post_result")


def build_post_result(spec: dict[str, Any]) -> str:
    """Replace filler at an exact character offset, preserving the target length."""
    length, filler = spec["length"], spec["filler"]
    if isinstance(length, bool) or not isinstance(length, int) or length < 0:
        raise ValueError("fixture length must be a nonnegative integer")
    if not isinstance(filler, str) or not filler:
        raise ValueError("fixture filler must be nonempty text")
    text = (filler * (length // len(filler) + 1))[:length]
    injection = spec.get("injection", "")
    if injection:
        offset = spec["offset"]
        if not isinstance(offset, int) or not 0 <= offset <= length - len(injection):
            raise ValueError("planted text must fit at its declared offset")
        text = text[:offset] + injection + text[offset + len(injection):]
    return text


def plan_windows(text: str, width: int, overlap: int) -> list[dict[str, int]]:
    if not isinstance(width, int) or not isinstance(overlap, int) or not 0 <= overlap < width:
        raise ValueError("invalid window size or overlap")
    windows = []
    start = 0
    while start < len(text):
        end = min(start + width, len(text))
        windows.append({"start": start, "end": end})
        if end == len(text):
            break
        start += width - overlap
    return windows


def select_window_sizes(configuration: dict[str, Any], requested: str | None = None) -> list[int]:
    """Parse requested widths while always retaining the original baseline first."""
    configured = configuration.get("window_sizes") or []
    baseline = int(configuration.get("baseline_window_chars", configured[0]))
    parsed = [int(value.strip()) for value in requested.split(",")] if requested else [int(value) for value in configured]
    return [baseline, *[value for value in parsed if value != baseline]]


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower, upper = math.floor(position), math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def arm_packs(packs: dict[str, Any], overlay: dict[str, Any] | None) -> dict[str, Any]:
    """The baseline strips all scopes; None compares it to the shipped scopes."""
    baseline, scoped = copy.deepcopy(packs), copy.deepcopy(packs)
    for pack in baseline.values():
        pack["questions"] = strip_scopes(pack["questions"])
    if overlay is not None:
        scoped = copy.deepcopy(baseline)
        for pack_name, questions in overlay.items():
            if pack_name not in scoped:
                raise ValueError("scope overlay names an unknown pack")
            for name, scope in questions.items():
                if name not in scoped[pack_name]["questions"]:
                    raise ValueError("scope overlay names an unknown question")
                scoped[pack_name]["questions"][name]["scope"] = copy.deepcopy(scope)
    return {"unscoped": baseline, "scoped": scoped}


def selected_url(lane: str) -> str:
    if os.environ.get("FD_LIVE") != "1":
        raise ValueError("live measurement requires FD_LIVE=1")
    variable = "FD_MAX_URL" if lane == "max" else "FD_OMNI_URL"
    if not os.environ.get(variable) and not os.environ.get("FLUID_LANES_FILE"):
        raise ValueError("measurement needs explicit lane endpoint configuration")
    with D1Client() as client:
        if lane not in client.urls:
            raise ValueError("explicitly selected lane is unavailable")
        return client.urls[lane]


def _mean(values: list[float]) -> float | None:
    return statistics.mean(values) if values else None


def _rate(values: list[bool]) -> dict[str, Any]:
    return {"correct": sum(values), "count": len(values), "rate": _mean(values)}


def _axis_metrics(observations: list[dict[str, Any]]) -> dict[str, Any]:
    half = [o["agreement_half"] for o in observations if o.get("agreement_half") is not None]
    lines: dict[str, list[bool]] = defaultdict(list)
    for observation in observations:
        for line, agreement in observation.get("agreement_lines", {}).items():
            if agreement is not None:
                lines[line].append(agreement)
    decisive = [o["decisiveness"] for o in observations if o.get("decisiveness") is not None]
    return {"agreement_half": _rate(half),
            "agreement_lines": {line: _rate(values) for line, values in sorted(lines.items())},
            "mean_decisiveness": _mean(decisive),
            "median_decisiveness": statistics.median(decisive) if decisive else None}


def evaluate_acceptance(summary: dict[str, Any], configuration: dict[str, Any]) -> dict[str, Any]:
    packs = summary["packs"]
    comparisons, latency = {}, {}
    factor = configuration["latency"]["p95_factor"]
    for name, arms in packs.items():
        if "unscoped" not in arms or "scoped" not in arms:
            continue
        old, new = arms["unscoped"], arms["scoped"]
        old_rate, new_rate = old["verdict_agreement"]["rate"], new["verdict_agreement"]["rate"]
        comparisons[name] = {"unscoped": old_rate, "scoped": new_rate,
                             "pass": old_rate is not None and new_rate is not None and new_rate >= old_rate}
        a, b = old["p95_ms"], new["p95_ms"]
        latency[name] = {"unscoped_ms": a, "scoped_ms": b, "factor": factor,
                         "pass": a is not None and b is not None and b <= a * factor}
    gate = comparisons.get("gate", {})
    gate_gain = (gate.get("unscoped") is not None and gate.get("scoped") is not None
                 and gate["scoped"] > gate["unscoped"])
    complete = all(arm["incomplete_calls"] == 0 for arms in packs.values() for arm in arms.values())
    checks = {
        "no_pack_verdict_regression": {"pass": bool(comparisons) and all(x["pass"] for x in comparisons.values()), "packs": comparisons},
        "gate_verdict_improves": {"pass": gate_gain, **{k: v for k, v in gate.items() if k != "pass"}},
        "latency_within_factor": {"pass": bool(latency) and all(x["pass"] for x in latency.values()), "packs": latency},
        "all_calls_complete": {"pass": complete},
    }
    candidates = summary.get("candidate_scopes", {})
    eligible: dict[str, Any] = {}
    for pack_name, names in candidates.items():
        for question in names:
            item = summary.get("questions", {}).get(pack_name, {}).get(question)
            eligible[f"{pack_name}.{question}"] = item or {"eligible": False, "reason": "no measurement record"}
    checks["candidate_question_adoption"] = {
        "pass": not eligible or all(item.get("eligible") is True for item in eligible.values()),
        "questions": eligible,
    }
    checks["overall"] = {"pass": all(item["pass"] for item in checks.values())}
    return checks


def summarize(evidence: dict[str, Any]) -> dict[str, Any]:
    """Pure, offline-replayable arithmetic; never pool separate lane evidence."""
    records = evidence.get("records", [])
    warm = [r for r in records if r["phase"] == "warm" and not r.get("static_expected")]
    pack_summary: dict[str, Any] = {}
    questions: dict[str, Any] = {}
    for pack in sorted({r["pack"] for r in warm}):
        pack_summary[pack] = {}
        pack_records = [r for r in warm if r["pack"] == pack and r["arm"] in {"unscoped", "scoped"}]
        for arm in ("unscoped", "scoped"):
            selected = [r for r in pack_records if r["arm"] == arm]
            if not selected:
                continue
            times = [r["timings"]["client_ms"] for r in selected]
            agreement = [r["verdict_agreement"] for r in selected
                         if r.get("verdict_agreement") is not None and not r.get("ambiguous")]
            observations = [o for r in selected for o in r.get("observations", [])]
            group_tokens: dict[str, int] = defaultdict(int)
            for record in selected:
                for group in record.get("scoping", {}).get("groups", []):
                    key = json.dumps(group.get("scope"), sort_keys=True)
                    group_tokens[key] += group.get("input_tokens", 0)
            pack_summary[pack][arm] = {
                "calls": len(selected), "incomplete_calls": sum(not r.get("complete", True) or not r.get("model_scored", True) for r in selected),
                "verdict_agreement": _rate(agreement), "p50_ms": percentile(times, .5),
                "p95_ms": percentile(times, .95),
                "cold_ms": [r["timings"]["client_ms"] for r in records
                            if r["phase"] == "cold" and r["pack"] == pack and r["arm"] == arm and not r.get("static_expected")],
                "input_tokens": sum(r.get("usage", {}).get("input_tokens", 0) for r in selected),
                "group_input_tokens": dict(group_tokens),
                "over_warm_budget": sum(r.get("over_warm_budget", False) for r in selected),
                **_axis_metrics(observations),
                "axes": {name: _axis_metrics([o for o in observations if o["question"] == name])
                         for name in sorted({o["question"] for o in observations})},
            }
        questions[pack] = {}
        names = sorted({o["question"] for r in pack_records for o in r.get("observations", [])})
        for name in names:
            by_arm = {arm: [o for r in pack_records if r["arm"] == arm
                            for o in r.get("observations", []) if o["question"] == name]
                      for arm in ("unscoped", "scoped")}
            metrics = {arm: _axis_metrics(obs) for arm, obs in by_arm.items()}
            regressions = []
            items = sorted({(r["fixture_id"], o.get("item", "main")) for r in pack_records
                            for o in r.get("observations", []) if o["question"] == name})
            for fixture, item in items:
                compared = {}
                for arm in ("unscoped", "scoped"):
                    obs = [o for r in pack_records if r["fixture_id"] == fixture and r["arm"] == arm
                           for o in r.get("observations", []) if o["question"] == name and o.get("item", "main") == item]
                    compared[arm] = _axis_metrics(obs)
                old, new = compared["unscoped"], compared["scoped"]
                drops = []
                for metric in ("agreement_half",):
                    a, b = old[metric]["rate"], new[metric]["rate"]
                    if a is not None and (b is None or b < a):
                        drops.append(metric)
                for line, value in old["agreement_lines"].items():
                    a = value["rate"]
                    b = new["agreement_lines"].get(line, {}).get("rate")
                    if a is not None and (b is None or b < a):
                        drops.append("line:" + line)
                if drops:
                    regressions.append({"fixture_id": fixture, "item": item, "metrics": drops})
            a = metrics["unscoped"]["mean_decisiveness"]
            b = metrics["scoped"]["mean_decisiveness"]
            da = metrics["unscoped"]["agreement_half"]["rate"]
            db = metrics["scoped"]["agreement_half"]["rate"]
            value_type = next((o.get("answer", {}).get("type") for obs in by_arm.values() for o in obs
                               if o.get("answer", {}).get("type")), None)
            questions[pack][name] = {
                **metrics, "agreement_delta": db - da if da is not None and db is not None else None,
                "decisiveness_delta": b - a if a is not None and b is not None else None,
                "regressions": regressions,
                "regression_fixtures": sorted({r["fixture_id"] for r in regressions}),
                "eligible": (not regressions and value_type != "score" and a is not None and b is not None
                             and b > a and metrics["unscoped"]["agreement_half"]["count"] > 0),
            }
            if all(o.get("answer", {}).get("type") == "score" for o in by_arm["unscoped"] + by_arm["scoped"]):
                questions[pack][name]["eligible"] = False
                questions[pack][name]["reason"] = "no defined decisiveness metric for score; remains unscoped"
    candidate_scopes = evidence.get("overlay")
    if candidate_scopes is None:
        # A final shipped rerun uses --scopes none to compare the shipped
        # scoped arm with its stripped baseline. Recover candidate names from
        # the actual scoped request metadata so adoption checks still apply.
        derived: dict[str, dict[str, Any]] = defaultdict(dict)
        for record in records:
            if record.get("arm") != "scoped":
                continue
            for group in record.get("scoping", {}).get("groups", []):
                if group.get("scope") is not None:
                    for name in group.get("questions", []):
                        derived[record["pack"]][name] = group["scope"]
        candidate_scopes = dict(derived)
    result = {"packs": pack_summary, "questions": questions,
              "candidate_scopes": candidate_scopes,
              "post_result": _summarize_windows(warm, evidence.get("configuration", {})),
              "static_calls": sum(bool(r.get("static_expected")) for r in records),
              "static_failures": sorted({r["fixture_id"] for r in records
                                         if r.get("static_expected") and r.get("static_verified") is False})}
    result["acceptance"] = evaluate_acceptance(result, evidence["configuration"])
    return result


def _summarize_windows(records: list[dict[str, Any]], configuration: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    post = [r for r in records if r["pack"] == "post_result" and r["arm"] != "scoped"]
    for width in sorted({r["window_chars"] for r in post}):
        selected = [r for r in post if r["window_chars"] == width]
        fixture_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in selected:
            fixture_groups[record["fixture_id"]].append(record)
        planted = {k: v for k, v in fixture_groups.items() if v[0].get("category") == "planted"}
        clean = {k: v for k, v in fixture_groups.items() if v[0].get("category") == "clean"}
        near = {k: v for k, v in fixture_groups.items() if v[0].get("category") == "near_miss"}
        decisive = [2 * abs(w["directed_injection"] - .5) for r in selected
                    for w in r.get("windows", []) if w.get("holds_injection") and w.get("directed_injection") is not None
                    and r.get("category") == "planted"]
        output[str(width)] = {
            "planted_count": len(planted),
            "detected": sorted(k for k, v in planted.items() if all(r.get("flagged") and r.get("model_scored") for r in v)),
            "clean_false_flags": sorted(k for k, v in clean.items() if any(r.get("flagged") for r in v)),
            "near_miss_false_flags": sorted(k for k, v in near.items() if any(r.get("flagged") for r in v)),
            "mean_injection_window_decisiveness": _mean(decisive),
            "p95_ms_by_length": {str(n): percentile([r["timings"]["client_ms"] for r in selected if r["length"] == n], .95)
                                 for n in sorted({r["length"] for r in selected})},
            "submitted_characters": sum(r.get("submitted_characters", 0) for r in selected),
            "input_tokens": sum(r.get("usage", {}).get("input_tokens", 0) for r in selected),
            "exceeds_budget_calls": sum(r.get("cost_model_budget", {}).get("exceeds_budget", False) for r in selected),
            "unscored_calls": sum(not r.get("model_scored") for r in selected),
        }
    widths = configuration.get("window_sizes", [])
    baseline_width = str(configuration.get("baseline_window_chars", widths[0] if widths else ""))
    baseline = output.get(baseline_width)
    if baseline:
        for width, item in output.items():
            if baseline is None:
                continue
            latency_ok = all(item["p95_ms_by_length"].get(n, math.inf) <= value * configuration["latency"]["p95_factor"]
                             for n, value in baseline["p95_ms_by_length"].items())
            checks = {"detection_not_lower": len(item["detected"]) >= len(baseline["detected"]),
                      "clean_flags_not_higher": len(item["clean_false_flags"]) <= len(baseline["clean_false_flags"]),
                      "near_miss_flags_not_higher": len(item["near_miss_false_flags"]) <= len(baseline["near_miss_false_flags"]),
                      "latency_within_factor": latency_ok, "all_scored": item["unscored_calls"] == 0}
            item["adoption"] = {**checks, "eligible": width != baseline_width and all(checks.values())}
    return output


def _answer(answer: dict[str, Any]) -> dict[str, Any]:
    """Keep typed fields only; never persist arbitrary server metadata."""
    value = {key: copy.deepcopy(answer[key]) for key in
             ("type", "noul", "choice", "score", "legend", "probabilities", "confidence") if key in answer}
    if value.get("type") == "noul":
        p = value["noul"]
        value.update(probabilities={"yes": p, "no": 1 - p}, decisiveness=2 * abs(p - .5))
    elif value.get("probabilities"):
        value["top_probability"] = max(value["probabilities"].values())
        # Confidence is kept separately; decisiveness is defined only for a noul.
    return value


def _lines(pack: dict[str, Any], question: str) -> list[float]:
    policy = pack["policy"]
    thresholds = policy.get("thresholds", {})
    lines = []
    if question in policy.get("risk_axes", []):
        lines.extend(thresholds.get("risk", thresholds.get("verdict", {})).values())
    for group in ("hard", "mitigators", "verdict"):
        if question in thresholds.get(group, {}):
            lines.append(thresholds[group][question])
    return sorted({float(line) for line in lines if isinstance(line, (int, float))})


def _observation(question: str, answer: dict[str, Any], label: Any,
                 pack: dict[str, Any], item: str = "main") -> dict[str, Any]:
    value = _answer(answer)
    half, lines = None, {}
    if isinstance(label, bool) and value.get("type") == "noul":
        half = (value["noul"] >= .5) == label
        lines = {str(line): (value["noul"] >= line) == label for line in _lines(pack, question)}
    elif isinstance(label, str):
        if value.get("type") == "choice":
            half = value.get("choice") == label
        elif value.get("type") == "score" and value.get("probabilities"):
            top = max(value["probabilities"], key=value["probabilities"].get)
            half = value.get("legend", {}).get(top) == label
    return {"question": question, "item": item, "answer": value, "label": label,
            "agreement_half": half, "agreement_lines": lines,
            "decisiveness": value.get("decisiveness")}


class MeasurementClient(D1Client):
    """Use one explicitly selected endpoint; capture only numeric/typed evidence.

    The service performs text decisions via its max role even when the selected
    measurement endpoint is omni. No audio is sent and no model field routes it.
    The transport timeout is extended without changing shipped budget estimates.
    """

    def __init__(self, url: str, timeout: float, **kwargs: Any):
        super().__init__(**kwargs)
        self.urls = {"max": url}
        self.measurement_timeout = timeout
        self.post_timeout = None
        self.trace: list[dict[str, Any]] = []

    def _request(self, lane: str, method: str, path: str,
                 json_body: dict[str, Any] | None = None, *, consume_budget: bool = True,
                 budget_s: float | None = None) -> tuple[dict[str, Any], float]:
        started = time.perf_counter()
        try:
            payload, elapsed = super()._request(lane, method, path, json_body,
                                               consume_budget=consume_budget,
                                               budget_s=self.measurement_timeout)
        except ClientError as exc:
            if method == "POST" and json_body is not None:
                elapsed = (time.perf_counter() - started) * 1000
                requests = json_body.get("requests", [json_body])
                self.trace.append({"groups": [{
                    "questions": list(request.get("questions", {})),
                    "state_chars": len(json.dumps(request.get("state"), ensure_ascii=False,
                                                   sort_keys=True, separators=(",", ":"))),
                    "state_type": type(request.get("state")).__name__,
                    "input_tokens": 0, "output_tokens": 0, "answers": {},
                } for request in requests],
                    "timings": {"ms": None, "client_ms": elapsed},
                    "error": getattr(exc, "reason", "client_error")})
            raise
        if method == "POST" and json_body is not None:
            requests = json_body.get("requests", [json_body])
            results = payload.get("results", [payload])
            groups = []
            for request, response in zip(requests, results):
                groups.append({
                    "questions": list(request["questions"]),
                    "state_chars": len(json.dumps(request.get("state"), ensure_ascii=False,
                                                  sort_keys=True, separators=(",", ":"))),
                    "state_type": type(request.get("state")).__name__,
                    "input_tokens": response.get("usage", {}).get("input_tokens", 0),
                    "output_tokens": response.get("usage", {}).get("output_tokens"),
                    "answers": {name: _answer(answer) for name, answer in response.get("answers", {}).items()},
                })
            self.trace.append({"groups": groups,
                               "timings": {"ms": payload.get("timings", {}).get("ms"),
                                           "client_ms": elapsed}})
        return payload, elapsed

    def shipped_post_result_budget(self, total_chars: int, policy: dict[str, Any]) -> dict[str, Any]:
        """Evaluate the shipped guard, independently of measurement transport timeout."""
        return D1Client.post_result_budget(self, total_chars, policy)

    def post_result_budget(self, total_chars: int, policy: dict[str, Any], lane: str = "max") -> dict[str, Any]:
        """Use the measurement timeout for the request while allowing the service call.

        ``run_fixture`` records the real shipped guard separately through
        ``shipped_post_result_budget``. This method only prevents the long
        measurement transport timeout from turning the fixture into an
        unscored service path.
        """
        budget = D1Client.post_result_budget(self, total_chars, policy, lane)
        return {**budget, "budget_s": self.measurement_timeout,
                "budget_source": "measurement_only", "exceeds_budget": False}


def _seed_session(ledger: Ledger, session_id: str, session: dict[str, Any], pack: dict[str, Any]) -> None:
    # The marker is never reused in a proposed call. The real ledger builds the
    # session block and applies the same floors as a runtime invocation.
    marker = "Synthetic fixture provenance marker contains distinct harmless descriptive words."
    for index, event in enumerate(session.get("untrusted_events", [])):
        answers = {"directed_injection": {"type": "noul", "noul": event["directed_injection"]},
                   "kind": {"type": "choice", "choice": event["kind"]}}
        ledger.flag(session_id, answers, text=marker + str(index),
                    tool_name=event.get("tool", "fixture_reader"), policy=pack["policy"])
    for index, event in enumerate(session.get("pending_events", [])):
        ledger.pending(session_id, text=marker + str(index),
                       tool_name=event.get("tool", "fixture_reader"), policy=pack["policy"],
                       reason=event.get("reason", "exceeds_budget"))


def run_fixture(fixture: dict[str, Any], arm: str, packs: dict[str, Any], client: MeasurementClient,
                directory: Path, phase: str, repeat: int, width: int | None = None) -> dict[str, Any]:
    pack_name = fixture["pack"]
    effective = copy.deepcopy(packs)
    if width is not None:
        effective["gate"]["post_result"]["policy"]["window_chars"] = width
    ledger = Ledger(directory / "sessions")
    service = Decisions(client=client, ledger=ledger, audit=Audit(directory / "audit.jsonl"))
    service._warmed.add("max")
    session_id = "fixture-session"
    if fixture.get("session"):
        _seed_session(ledger, session_id, fixture["session"], effective["gate"])
    client.trace = []
    state = fixture["state"]
    started = time.perf_counter()
    cost = None
    text = ""
    with patch("fluid.service.load_pack", side_effect=lambda name: copy.deepcopy(effective[name])):
        if pack_name == "gate":
            result = service.gate(**state, session_id=session_id)
        elif pack_name == "classify":
            result = service.classify(state)
        elif pack_name == "route":
            result = service.route(state, destinations=fixture.get("destinations"))
        elif pack_name == "verify":
            result = service.verify(state["source"], state["claims"])
        elif pack_name == "post_result":
            text = build_post_result(state)
            post = effective["gate"]["post_result"]["policy"]
            planned = plan_windows(text, post["window_chars"], post["overlap_chars"])
            cost = client.shipped_post_result_budget(sum(w["end"] - w["start"] for w in planned), post)
            result = service.post_result(text, session_id=session_id, tool_name="fixture_reader")
        else:
            raise ValueError("unknown fixture pack")
    elapsed = (time.perf_counter() - started) * 1000
    label = fixture["label"]
    pack = effective["gate" if pack_name == "post_result" else pack_name]
    observations = []
    if pack_name == "verify":
        for index, item in enumerate(result.get("results", [])):
            expected = label.get("claims", [])[index]
            observations.append(_observation("supported_by_source", item["answers"]["supported_by_source"],
                                             expected == "pass", pack, str(index)))
    elif pack_name == "post_result":
        for index, item in enumerate(result.get("window_answers", [])):
            # Detection is labelled per full result; a clean window in a planted
            # fixture does not inherit the planted fixture's positive axis label.
            observations.append(_observation("directed_injection", item["directed_injection"],
                                             None, pack, str(index)))
    else:
        axis_labels = label.get("axes", {})
        for name, answer in result.get("answers", {}).items():
            expected = axis_labels.get(name, label.get(name))
            observations.append(_observation(name, answer, expected, pack))
    groups = [g for call in client.trace for g in call["groups"]]
    scoping = copy.deepcopy(result.get("scoping", {}))
    if pack_name == "classify":
        scoping = {"mode": result.get("scoping", {}).get("mode", "whole_state"),
                   "groups": [group for step in result.get("passes", [])
                              for group in step.get("scoping", {}).get("groups", [])],
                   "unresolved": sorted({name for step in result.get("passes", [])
                                         for name in step.get("scoping", {}).get("unresolved", [])})}
    expected_verdicts = label.get("verdict", [])
    agreement = result["verdict"] in expected_verdicts if expected_verdicts and not label.get("ambiguous") else None
    if pack_name == "verify" and label.get("claims"):
        agreement = [c["verdict"] for c in result.get("claims", [])] == label["claims"]
    if pack_name == "post_result":
        agreement = result.get("checked") is True and bool(result.get("flagged")) == label["flagged"]
    server_times = [call["timings"]["ms"] for call in client.trace if call["timings"].get("ms") is not None]
    record = {
        "fixture_id": fixture["id"], "pack": pack_name, "arm": arm, "phase": phase, "repeat": repeat,
        "static_expected": fixture.get("static_expected", False), "ambiguous": label.get("ambiguous", False),
        "verdict": result["verdict"], "reasons": result.get("reasons", []),
        "complete": result.get("complete", True), "model_scored": bool(observations), "verdict_agreement": agreement,
        "answers": {name: _answer(value) for name, value in result.get("answers", {}).items()},
        "observations": observations, "scoping": scoping,
        "timings": {"ms": sum(server_times) if server_times else None,
                    "client_ms": sum(call["timings"]["client_ms"] for call in client.trace), "wall_ms": elapsed},
        "usage": {"input_tokens": sum(g["input_tokens"] for g in groups),
                  "output_tokens": sum(g.get("output_tokens") or 0 for g in groups)},
        "calls": copy.deepcopy(client.trace), "over_warm_budget": elapsed > client.timeout * 1000,
    }
    if fixture.get("static_expected"):
        record["static_verified"] = not client.trace and result["verdict"] == "deny"
    if pack_name == "post_result":
        offset = state.get("offset")
        windows = plan_windows(text, width or post["window_chars"], post["overlap_chars"])
        for index, window in enumerate(windows):
            answer = result.get("window_answers", [])
            window.update(index=index, holds_injection=offset is not None and window["start"] <= offset
                          and window["end"] >= offset + len(state.get("injection", "")),
                          overlaps_injection=offset is not None and window["start"] < offset + len(state.get("injection", ""))
                          and window["end"] > offset,
                          directed_injection=answer[index]["directed_injection"]["noul"] if index < len(answer) else None,
                          kind=answer[index]["kind"]["choice"] if index < len(answer) else None)
        record.update(window_chars=width or post["window_chars"], length=len(text),
                      category=fixture.get("category", label.get("category", "planted" if label["flagged"] else "clean")),
                      windows=windows, flagged=result.get("flagged", False),
                      model_scored=result.get("check", {}).get("model_scored", False),
                      submitted_characters=sum(w["end"] - w["start"] for w in windows) if client.trace else 0,
                      cost_model_budget=cost, check=result.get("check", {}))
    return record


def _health(client: MeasurementClient) -> dict[str, Any]:
    health = client.health().get("max", {})
    if health.get("error"):
        return {"available": False, "reason": health["error"]["reason"]}
    revision = health.get("revision", health.get("model_revision"))
    return {"available": True, "loaded": health.get("loaded"), "model_revision": revision}


def _write(path: Path, evidence: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(evidence, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def run_measurement(args: argparse.Namespace) -> dict[str, Any]:
    url = selected_url(args.lane)
    fixtures_dir = Path(args.fixtures)
    configuration = yaml.safe_load((fixtures_dir / "acceptance.yaml").read_text())
    widths = select_window_sizes(configuration, args.window_sizes)
    baseline_width = widths[0]
    repeats = args.repeats if args.repeats is not None else configuration["repeats"]["warm"]
    if repeats < 1 or args.timeout <= 0 or not math.isfinite(args.timeout):
        raise ValueError("repeats and timeout must be positive")
    fixtures = [fixture for name in FIXTURE_PACKS
                for fixture in json.loads((fixtures_dir / (name + ".json")).read_text())]
    packs = {name: load_pack(name) for name in PACKS}
    shipped_width = packs["gate"]["post_result"]["policy"]["window_chars"]
    overlap = packs["gate"]["post_result"]["policy"]["overlap_chars"]
    if not widths or any(width <= overlap for width in widths):
        raise ValueError("window sizes must exceed the configured overlap")
    overlay_path = None if args.scopes == "none" else Path(args.scopes or fixtures_dir / "candidate_scopes.yaml")
    overlay = yaml.safe_load(overlay_path.read_text()) if overlay_path else None
    if overlay and "scopes" in overlay:
        overlay = overlay["scopes"]
    arms = arm_packs(packs, overlay)
    output = Path(args.output)
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
              for path in [*(fixtures_dir / (name + ".json") for name in FIXTURE_PACKS), fixtures_dir / "acceptance.yaml"]}
    overlay_hash = hashlib.sha256(overlay_path.read_bytes()).hexdigest() if overlay_path else None
    metadata = {"lane": "text" if args.lane == "max" else "audio", "fixture_sha256": hashes,
                "overlay_sha256": overlay_hash, "overlay": overlay,
                "pack_versions": {name: pack["version"] for name, pack in packs.items()},
                "protected_gate_axes": sorted(set(packs["gate"]["policy"]["unsoftenable_axes"])
                                               | set(packs["gate"]["policy"]["thresholds"]["hard"])),
                "pack_sha256": {name: hashlib.sha256(json.dumps(pack, sort_keys=True).encode()).hexdigest() for name, pack in packs.items()},
                "configuration": {**configuration, "window_sizes": widths,
                                   "baseline_window_chars": baseline_width, "repeats": {"warm": repeats}},
                "baseline_window_chars": baseline_width, "shipped_window_chars": shipped_width,
                "measurement_timeout_s": args.timeout}
    if args.resume and output.exists():
        evidence = json.loads(output.read_text())
        if any(evidence.get(key) != value for key, value in metadata.items()):
            raise ValueError("checkpoint differs from fixture, pack, overlay or measurement configuration")
    else:
        if output.exists():
            raise ValueError("output already exists; use --resume or a different output")
        evidence = {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
                    **metadata, "status": "running", "records": [], "segments": [],
                    "limitations": ["Synthetic-heavy mechanism measurement; not threshold calibration.",
                                    "First-call records follow service warmup; no model process was restarted.",
                                    "Labels are implementing-agent labels; maintainer review status is in fixture README."]}
    completed = {(r["fixture_id"], r["arm"], r["phase"], r["repeat"], r.get("window_chars")) for r in evidence["records"]}
    # Every first fixture/arm invocation is retained separately. Shape signatures
    # identify which was the first observed request shape within each segment.
    jobs = []
    for phase, repetitions in (("cold", 1), ("warm", repeats)):
        for fixture in fixtures:
            for repeat in range(repetitions):
                for arm in ("unscoped", "scoped"):
                    width = shipped_width if fixture["pack"] == "post_result" else None
                    jobs.append((fixture, arm, phase, repeat, width))
                if fixture["pack"] == "post_result":
                    for width in widths:
                        if width == shipped_width:
                            continue
                        jobs.append((fixture, "window", phase, repeat, width))
    with tempfile.TemporaryDirectory(prefix="fluid-scoping-") as temporary:
        with patch.dict(os.environ, {"FLUID_DATA_DIR": temporary, "FLUID_AUDIT_STATE": "0"}):
            with MeasurementClient(url, args.timeout) as client:
                before = _health(client)
                if not before["available"]:
                    evidence.update(status="skipped", skip_reason=before["reason"])
                    _write(output, evidence)
                    return evidence
                warm_service = Decisions(client=client)
                warmup = warm_service.warmup()
                segment = {"started_at": datetime.now(timezone.utc).isoformat(), "health_before": before,
                           "warmup_ms": client.last_warmup_ms, "cost_model": client.cost_models.get("max"),
                           "warmup_errors": list(client.warmup_errors.values()),
                           "first_timeout_s": client.first_timeout, "warm_timeout_s": client.timeout}
                evidence["segments"].append(segment)
                if warmup["verdict"] != "allow":
                    evidence.update(status="skipped", skip_reason="warmup_failed")
                    _write(output, evidence)
                    return evidence
                evidence["status"] = "running"
                seen_shapes: set[str] = set()
                for index, (fixture, arm, phase, repeat, width) in enumerate(jobs):
                    identity = (fixture["id"], arm, phase, repeat, width)
                    if identity in completed:
                        continue
                    record = run_fixture(fixture, arm, arms["unscoped" if arm == "window" else arm], client,
                                         Path(temporary) / str(index), phase, repeat, width)
                    shape = [{"questions": group["questions"], "state_chars": group["state_chars"],
                              "state_type": group["state_type"]} for call in record["calls"] for group in call["groups"]]
                    fingerprint = hashlib.sha256(json.dumps([arm, shape], sort_keys=True).encode()).hexdigest()
                    record.update(segment=len(evidence["segments"]) - 1, shape_sha256=fingerprint,
                                  first_observed_shape=fingerprint not in seen_shapes)
                    seen_shapes.add(fingerprint)
                    evidence["records"].append(record)
                    _write(output, evidence)
                    print(json.dumps({"completed": len(evidence["records"]), "total": len(jobs),
                                      "pack": fixture["pack"], "fixture": fixture["id"], "phase": phase,
                                      "arm": arm, "window": width, "verdict": record["verdict"],
                                      "client_ms": round(record["timings"]["client_ms"], 1)}), flush=True)
                segment["health_after"] = _health(client)
                evidence.update(status="complete", finished_at=datetime.now(timezone.utc).isoformat())
                evidence["summary"] = summarize(evidence)
                _write(output, evidence)
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lane", choices=("max", "omni"))
    parser.add_argument("--fixtures", default=str(ROOT / "tests/fixtures/scoping"))
    parser.add_argument("--scopes", help="candidate overlay path; none compares stripped baseline to shipped scopes")
    parser.add_argument("--window-sizes", help="comma-separated sizes; defaults to acceptance.yaml")
    parser.add_argument("--repeats", type=int)
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument("--output")
    parser.add_argument("--resume", action="store_true", help="resume an identical checkpoint; append segment metadata")
    parser.add_argument("--replay", help="recompute summary offline; requires no lane or FD_LIVE")
    args = parser.parse_args()
    try:
        if args.replay:
            summary = summarize(json.loads(Path(args.replay).read_text()))
            if args.output:
                _write(Path(args.output), summary)
            else:
                print(json.dumps(summary, indent=2, allow_nan=False))
            return 0
        if not args.lane or not args.output:
            parser.error("live measurement requires --lane and --output")
        result = run_measurement(args)
        return 0 if result["status"] == "complete" else 2
    except (ClientError, ValueError, OSError) as exc:
        # Connection/configuration exceptions may contain private paths. Keep
        # stderr categorical; durable evidence contains the completed records.
        print(json.dumps({"error": getattr(exc, "reason", type(exc).__name__),
                          "message": "Measurement could not finish; inspect configuration or resume checkpoint."}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
