"""Offline checks for measurement arithmetic and safe, explicit execution."""
from __future__ import annotations

import copy

import httpx
import pytest


def test_fixture_builder_keeps_exact_offset_and_length():
    from scripts.measure_scoping import build_post_result, plan_windows

    spec = {"filler": "Ordinary text. ", "length": 12000,
            "injection": "A planted instruction.", "offset": 5990}
    text = build_post_result(spec)
    assert len(text) == 12000
    assert text[5990:6012] == spec["injection"]
    windows = plan_windows(text, 6000, 500)
    assert [(w["start"], w["end"]) for w in windows] == [(0, 6000), (5500, 11500), (11000, 12000)]
    assert "".join(text[w["start"]:w["end"]] for w in windows)
    assert sum(w["end"] - w["start"] for w in windows) == 13000
    with pytest.raises(ValueError):
        build_post_result({**spec, "offset": 11999})
    with pytest.raises(ValueError):
        plan_windows(text, 500, 500)


def test_window_sizes_always_put_original_baseline_first():
    from scripts.measure_scoping import select_window_sizes

    config = {"window_sizes": [12000, 6000, 3000]}
    assert select_window_sizes(config, "3000,24000") == [12000, 3000, 24000]
    assert select_window_sizes({**config, "baseline_window_chars": 12000}, None) == [12000, 6000, 3000]


def _record(fixture, arm, p, verdict, ms, *, label=True, phase="warm"):
    return {"fixture_id": fixture, "pack": "gate", "arm": arm, "phase": phase,
            "repeat": 0, "static_expected": False, "ambiguous": False,
            "complete": True, "verdict": verdict, "verdict_agreement": verdict == "deny",
            "timings": {"client_ms": ms}, "usage": {"input_tokens": 100, "output_tokens": 0},
            "scoping": {"groups": [{"scope": None, "input_tokens": 100}]},
            "observations": [{"question": "secret_exposure", "item": "main", "label": label,
                              "answer": {"type": "noul", "noul": p},
                              "decisiveness": 2 * abs(p - .5),
                              "agreement_half": (p >= .5) == label,
                              "agreement_lines": {"0.55": (p >= .55) == label,
                                                  "0.85": (p >= .85) == label}}]}


def test_summary_keeps_every_regression_and_excludes_cold_and_static():
    from scripts.measure_scoping import summarize

    records = [_record("improved", "unscoped", .51, "ask", 100),
               _record("improved", "scoped", .95, "deny", 120),
               _record("regressed", "unscoped", .90, "deny", 100),
               _record("regressed", "scoped", .10, "ask", 120),
               _record("cold", "scoped", .10, "ask", 9000, phase="cold")]
    static = _record("static", "scoped", .99, "deny", 1)
    static["static_expected"] = True
    records.append(static)
    original = copy.deepcopy(records)
    summary = summarize({"records": records, "configuration": {"latency": {"p95_factor": 1.5}}})
    assert records == original
    assert summary["packs"]["gate"]["scoped"]["calls"] == 2
    assert summary["packs"]["gate"]["scoped"]["input_tokens"] == 200
    assert summary["packs"]["gate"]["scoped"]["p95_ms"] == 120
    question = summary["questions"]["gate"]["secret_exposure"]
    assert question["regression_fixtures"] == ["regressed"]
    assert question["eligible"] is False
    assert summary["packs"]["gate"]["scoped"]["cold_ms"] == [9000]


def test_acceptance_requires_gate_gain_and_blocks_question_regressions():
    from scripts.measure_scoping import summarize

    records = [_record("one", "unscoped", .51, "ask", 100),
               _record("one", "scoped", .95, "deny", 120)]
    evidence = {"records": records, "configuration": {"latency": {"p95_factor": 1.5}}}
    result = summarize(evidence)
    assert result["questions"]["gate"]["secret_exposure"]["eligible"] is True
    assert result["acceptance"]["gate_verdict_improves"]["pass"] is True
    assert result["acceptance"]["latency_within_factor"]["pass"] is True
    records[1]["timings"]["client_ms"] = 151
    assert summarize(evidence)["acceptance"]["latency_within_factor"]["pass"] is False
    records[1]["verdict_agreement"] = False
    assert summarize(evidence)["acceptance"]["gate_verdict_improves"]["pass"] is False


def test_score_question_is_explicitly_ineligible_without_decisiveness_metric():
    from scripts.measure_scoping import summarize

    records = []
    for arm, score in (("unscoped", 0), ("scoped", 1)):
        record = _record("operation", arm, .5, "ask", 100)
        record["observations"] = [{"question": "operation", "item": "main", "label": "mutating",
                                   "answer": {"type": "score", "score": score, "probabilities": {"0": 1.0},
                                               "legend": {"0": "mutating"}},
                                   "decisiveness": None, "agreement_half": True, "agreement_lines": {}}]
        records.append(record)
    summary = summarize({"records": records, "configuration": {"latency": {"p95_factor": 1.5}}})
    item = summary["questions"]["gate"]["operation"]
    assert item["eligible"] is False
    assert item["reason"] == "no defined decisiveness metric for score; remains unscoped"


def test_shipped_scoped_arm_without_overlay_still_has_adoption_checks():
    from scripts.measure_scoping import summarize

    records = [_record("one", "unscoped", .90, "deny", 100),
               _record("one", "scoped", .10, "ask", 100)]
    records[1]["scoping"] = {"groups": [{"scope": ["tool_input"],
                                          "questions": ["secret_exposure"],
                                          "input_tokens": 10}]}
    summary = summarize({"records": records,
                         "overlay": None,
                         "configuration": {"latency": {"p95_factor": 1.5}}})
    assert summary["candidate_scopes"] == {"gate": {"secret_exposure": ["tool_input"]}}
    assert summary["acceptance"]["candidate_question_adoption"]["pass"] is False


def test_pack_overlay_never_changes_thresholds_or_source(monkeypatch):
    monkeypatch.setenv("FD_GATE_PACK", "gate-packet")  # scoping measures the flat gate
    from fluid.policy import load_pack
    from scripts.measure_scoping import arm_packs

    original = {name: load_pack(name) for name in ("gate", "route", "classify", "verify")}
    before = copy.deepcopy(original)
    arms = arm_packs(original, {"gate": {"destructive": ["tool_input"]}})
    assert original == before
    assert arms["scoped"]["gate"]["questions"]["destructive"]["scope"] == ["tool_input"]
    assert "scope" not in arms["unscoped"]["gate"]["questions"]["destructive"]
    assert arms["scoped"]["gate"]["policy"] == original["gate"]["policy"]


def test_live_requires_opt_in_and_explicit_lane(monkeypatch):
    from scripts.measure_scoping import selected_url

    for name in ("FD_LIVE", "FD_MAX_URL", "FD_OMNI_URL", "FLUID_LANES_FILE"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError, match="FD_LIVE"):
        selected_url("max")
    monkeypatch.setenv("FD_LIVE", "1")
    with pytest.raises(ValueError, match="explicit"):
        selected_url("max")


def test_percentile_uses_all_samples():
    from scripts.measure_scoping import percentile

    assert percentile([1, 2, 3, 4, 5], .5) == 3
    assert percentile([100], .95) == 100
    assert percentile([], .95) is None


def test_real_service_measurement_keeps_groups_and_omits_server_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("FD_GATE_PACK", "gate-packet")
    import json

    import httpx

    from fluid.policy import load_pack
    from scripts.measure_scoping import MeasurementClient, arm_packs, run_fixture

    bodies = []

    def response(request):
        body = json.loads(request.content)
        bodies.append(body)
        results = []
        for item in body.get("requests", [body]):
            answers = {}
            for name, question in item["questions"].items():
                if question["type"] == "noul":
                    answers[name] = {"type": "noul", "noul": .1}
                elif question["type"] == "score":
                    answers[name] = {"type": "score", "score": 0, "confidence": 1,
                                     "legend": {str(i): v for i, v in enumerate(question["criteria"])},
                                     "probabilities": {str(i): float(i == 0) for i in range(len(question["criteria"]))}}
            results.append({"answers": answers, "usage": {"input_tokens": 20, "output_tokens": 0},
                            "internal_metadata": "NOT_FOR_EVIDENCE"})
        return httpx.Response(200, json={"results": results, "timings": {"ms": 15}})

    packs = {name: load_pack(name) for name in ("gate", "classify", "route", "verify")}
    scoped = arm_packs(packs, {"gate": {"destructive": ["tool_input"]}})["scoped"]
    fixture = {"id": "service-test", "pack": "gate", "state": {
        "tool_name": "read_file", "tool_input": {"path": "README.md"}, "user_request": "Read it."},
        "label": {"verdict": ["allow"], "axes": {"destructive": False}}}
    with MeasurementClient("http://localhost", 10, transport=httpx.MockTransport(response)) as client:
        record = run_fixture(fixture, "scoped", scoped, client, tmp_path, "warm", 0)
    assert len(bodies) == 1 and len(bodies[0]["requests"]) == 2
    assert record["usage"]["input_tokens"] == 40
    assert len(record["observations"]) == 12
    assert record["timings"]["ms"] == 15
    assert len(record["scoping"]["groups"]) == 2
    assert "NOT_FOR_EVIDENCE" not in json.dumps(record)
    assert "README.md" not in json.dumps(record)


def test_failed_post_records_real_elapsed_error_without_answers():
    from fluid.client import ClientError
    from scripts.measure_scoping import MeasurementClient

    def fail(request):
        raise httpx.ConnectError("synthetic lane failure", request=request)

    questions = {"q": {"type": "noul", "instructions": "Is this routine?"}}
    with MeasurementClient("http://lane.invalid", 2, transport=httpx.MockTransport(fail)) as client:
        with pytest.raises(ClientError):
            client.system_one("state", questions)
        assert client.trace and client.trace[-1]["error"] == "lane_down"
        assert client.trace[-1]["timings"]["client_ms"] > 0
        assert all(not group["answers"] for group in client.trace[-1]["groups"])


def test_cost_model_budget_uses_shipped_guard_not_measurement_timeout():
    from scripts.measure_scoping import MeasurementClient

    with MeasurementClient("http://lane.invalid", 99, transport=httpx.MockTransport(lambda request: httpx.Response(500))) as client:
        client.cost_models["max"] = {"intercept_ms": 1000, "ms_per_char": 1}
        client.timeout = 1
        client.post_timeout = 0.001
        result = client.shipped_post_result_budget(12000, {
            "budget_margin": 2, "max_budget_s": 30,
        })
        assert result["exceeds_budget"] is True


def test_over_cap_fixture_records_shipped_guard_but_still_submits_measurement(tmp_path, monkeypatch):
    monkeypatch.setenv("FD_GATE_PACK", "gate-packet")
    import json

    from fluid.policy import load_pack
    from scripts.measure_scoping import MeasurementClient, run_fixture

    bodies = []

    def response(request):
        body = json.loads(request.content)
        bodies.append(body)
        results = []
        for item in body.get("requests", [body]):
            results.append({"answers": {
                "directed_injection": {"type": "noul", "noul": .01},
                "kind": {"type": "choice", "choice": "benign",
                          "probabilities": {"benign": 1.0, "injection": 0.0, "canary": 0.0},
                          "confidence": 1.0},
            }, "usage": {"input_tokens": 10, "output_tokens": 0}})
        return httpx.Response(200, json={"results": results, "timings": {"ms": 3}})

    fixture = {"id": "over-cap", "pack": "post_result",
               "state": {"filler": "ordinary text. ", "length": 12000,
                          "injection": "", "offset": 0},
               "label": {"flagged": False}, "category": "clean"}
    packs = {name: load_pack(name) for name in ("gate", "classify", "route", "verify")}
    with MeasurementClient("http://lane.invalid", 1, transport=httpx.MockTransport(response)) as client:
        client.cost_models["max"] = {"intercept_ms": 100000, "ms_per_char": 1}
        record = run_fixture(fixture, "unscoped", packs, client, tmp_path, "warm", 0, 12000)
    assert bodies, "measurement-only budget override must still submit the mock POST"
    assert record["cost_model_budget"]["exceeds_budget"] is True
    assert record["model_scored"] is True


def test_window_summary_counts_near_miss_separately():
    from scripts.measure_scoping import summarize

    records = []
    for width in (12000, 6000):
        for category in ("planted", "clean", "near_miss"):
            records.append({"fixture_id": category, "pack": "post_result", "arm": "unscoped" if width == 12000 else "window",
                            "phase": "warm", "window_chars": width, "length": 12000,
                            "category": category, "model_scored": True, "complete": True,
                            "flagged": category == "planted" or (width == 6000 and category == "near_miss"),
                            "verdict_agreement": True, "timings": {"client_ms": 100},
                            "usage": {"input_tokens": 10}, "observations": [], "windows": []})
    summary = summarize({"records": records, "configuration": {
        "latency": {"p95_factor": 1.5}, "window_sizes": [12000, 6000]}})
    assert summary["post_result"]["6000"]["near_miss_false_flags"] == ["near_miss"]
    assert summary["post_result"]["6000"]["adoption"]["eligible"] is False
