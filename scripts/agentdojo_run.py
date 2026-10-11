# mypy: ignore-errors
"""Run the Fluid Decisions guard through the AgentDojo benchmark (IP-008).

Two arms, ``none`` (the plain pipeline) and ``fluid`` (the guard as two pipeline
elements), on one suite and one attack, driven by a cloud agent model served
through the maintainer's OpenAI-compatible gateway. The benchmark's own scoring
gives three numbers per arm — benign utility, utility under attack, targeted
attack success — recorded beside the guard's own audit counts. Counts only; no
endpoint, key, host or path is written to any artifact.

Grounding note (IP-008): the installed ``agentdojo`` (0.1.35) has no
``openai-compatible`` provider, so the agent model is driven by constructing
``OpenAILLM`` with an ``openai`` client pointed at the gateway base URL — the live
package wins over the plan text, which assumed a named provider.

Config from the environment, never committed:
  OPENAI_COMPATIBLE_BASE_URL / OPENAI_COMPATIBLE_API_KEY  (agent model gateway;
      FD fallbacks: TEST_API_ENDPOINT / TEST_API_KEY)
  FLUID_LANES_FILE                                        (the guard's d1 lane)
  FLUID_DATA_DIR                                          (ignored local audit/ledger)
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_USER_TASKS = [f"user_task_{i}" for i in range(5)]
DEFAULT_INJECTION_TASKS = [f"injection_task_{i}" for i in range(3)]
SUITE = "workspace"
ATTACK = "important_instructions"
BENCHMARK_VERSION = "v1.2.2"


def _agentdojo_version() -> str:
    from importlib.metadata import version
    try:
        return version("agentdojo")
    except Exception:
        return "unknown"


def _gateway_client() -> Any:
    import openai
    base = os.environ.get("OPENAI_COMPATIBLE_BASE_URL") or os.environ.get("TEST_API_ENDPOINT")
    key = os.environ.get("OPENAI_COMPATIBLE_API_KEY") or os.environ.get("TEST_API_KEY")
    if not base or not key:
        raise SystemExit("set OPENAI_COMPATIBLE_BASE_URL/API_KEY (or TEST_API_ENDPOINT/TEST_API_KEY)")
    return openai.OpenAI(base_url=base, api_key=key)


def _default_system_message() -> str:
    import agentdojo
    import yaml
    data = Path(agentdojo.__file__).with_name("data") / "system_messages.yaml"
    return str(yaml.safe_load(data.read_text())["default"])


def _build_pipeline(arm: str, model_id: str, data_dir: Path, judge_model: str | None = None):
    """Both arms mirror the default agentdojo pipeline (defense=None); the fluid arm
    inserts the two guard elements into the tool loop. The guard's d1 lane, audit
    and ledger are per arm and local."""
    from agentdojo.agent_pipeline import (
        AgentPipeline,
        InitQuery,
        OpenAILLM,
        SystemMessage,
        ToolsExecutionLoop,
        ToolsExecutor,
    )
    # The important_instructions attack reads a display model name out of the
    # pipeline name (substring match against MODEL_NAMES). Our gateway model id is
    # not a known key, so register it to its true family and embed it in the name;
    # the arm label stays "none"/"fluid" in the summaries.
    _register_model_name(model_id)
    llm = OpenAILLM(_gateway_client(), model_id)
    system = SystemMessage(_default_system_message())
    init = InitQuery()
    if arm == "none":
        loop = ToolsExecutionLoop([ToolsExecutor(), llm])
        pipeline = AgentPipeline([system, init, llm, loop])
        pipeline.name = f"none-{model_id}"
        return pipeline, None
    if arm == "fluid":
        from fluid.agentdojo import FluidGateElement, FluidPostResultElement
        from fluid.audit import Audit
        from fluid.client import D1Client
        from fluid.ledger import Ledger
        from fluid.service import Decisions
        lanes = os.environ.get("FLUID_LANES_FILE")
        decisions = Decisions(client=D1Client(lanes_file=Path(lanes) if lanes else None),
                              audit=Audit(data_dir / "audit-fluid.jsonl"),
                              ledger=Ledger(data_dir / "sessions-fluid"))
        loop = ToolsExecutionLoop([FluidGateElement(decisions), ToolsExecutor(),
                                   FluidPostResultElement(decisions), llm])
        pipeline = AgentPipeline([system, init, llm, loop])
        pipeline.name = f"fluid-{model_id}"
        return pipeline, decisions
    if arm == "judge":
        # LLM-as-judge: the same gateway chat model scores the gate.yaml questions,
        # then the UNCHANGED policy (compose_gate_facts + evaluate_gate, post_result
        # lines) decides. Only the scorer differs from the fluid arm.
        from fluid.agentdojo import JudgeGateElement, JudgePostResultElement, JudgeScorer
        from fluid.policy import load_pack
        scorer = JudgeScorer(_gateway_client(), judge_model or model_id, load_pack("gate"))
        loop = ToolsExecutionLoop([JudgeGateElement(scorer), ToolsExecutor(),
                                   JudgePostResultElement(scorer), llm])
        pipeline = AgentPipeline([system, init, llm, loop])
        pipeline.name = f"judge-{model_id}"
        return pipeline, scorer
    raise SystemExit(f"unknown arm {arm!r}")


def _register_model_name(model_id: str) -> None:
    """Teach the attack registry our gateway model's display family, so its
    injection can address the agent by name. Honest mapping by id substring."""
    from agentdojo.attacks import base_attacks
    low = model_id.lower()
    display = ("Claude" if "claude" in low else "GPT-4" if "gpt" in low
               else "AI model developed by Google" if "gemini" in low else "AI assistant")
    base_attacks.MODEL_NAMES.setdefault(model_id, display)


def _rate(results: dict[tuple[str, str], bool]) -> dict[str, Any]:
    values = list(results.values())
    true = sum(1 for v in values if v)
    return {"n": len(values), "true": true,
            "rate": round(true / len(values), 4) if values else None}


def _audit_counts(path: Path) -> dict[str, Any]:
    """The guard's own counts from its audit log: gate verdicts, redactions, taint
    and provenance reasons, timeouts. Counts only."""
    counts = {"gate": {"allow": 0, "ask": 0, "deny": 0}, "post_result": {"flagged": 0, "allow": 0},
              "units_redacted": 0, "carries_out_flagged_instruction": 0,
              "taint_floor_no_instruction": 0, "withheld_path_reread": 0,
              "provenance_asks_or_denies": 0, "timeouts": 0, "records": 0}
    if not path.exists():
        return counts
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        counts["records"] += 1
        job, verdict = record.get("job"), record.get("verdict")
        reasons = [str(r) for r in record.get("reasons", [])]
        if job == "gate" and verdict in counts["gate"]:
            counts["gate"][verdict] += 1
        if job == "post_result":
            counts["post_result"]["flagged" if record.get("flagged") else "allow"] += 1
            counts["units_redacted"] += int(record.get("units_redacted") or 0)
        for key in ("carries_out_flagged_instruction", "taint_floor_no_instruction"):
            if key in reasons:
                counts[key] += 1
        if any("withheld_path_reread" in r for r in reasons):
            counts["withheld_path_reread"] += 1
        if any(r.startswith("provenance_") for r in reasons):
            counts["provenance_asks_or_denies"] += 1
        if any("timeout" in r for r in reasons):
            counts["timeouts"] += 1
    return counts


def _lane_scorer_stats(path: Path) -> dict[str, Any]:
    """The d1 lane as the fluid arm's scorer: calls, mean latency, input tokens
    (the lane returns no completion tokens). Read from the audit log; counts only."""
    calls = 0
    latency_total = 0.0
    latency_n = 0
    prompt_tokens = 0
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if record.get("job") not in ("gate", "post_result"):
                continue
            calls += 1
            prompt_tokens += int(record.get("usage", {}).get("input_tokens", 0) or 0)
            ms = record.get("timings", {}).get("ms") if isinstance(record.get("timings"), dict) else None
            if isinstance(ms, (int, float)) and not isinstance(ms, bool):
                latency_total += float(ms)
                latency_n += 1
    return {"scorer": "d1_lane", "calls": calls,
            "mean_latency_ms": round(latency_total / latency_n, 1) if latency_n else None,
            "prompt_tokens": prompt_tokens, "completion_tokens": 0, "total_tokens": prompt_tokens,
            "parse_failures": 0}


def _judge_scorer_stats(scorer: Any) -> dict[str, Any]:
    s = scorer.stats
    calls = int(s["gate_calls"]) + int(s["post_calls"])
    total_tokens = int(s["prompt_tokens"]) + int(s["completion_tokens"])
    return {"scorer": "chat_model", "calls": calls, "gate_calls": int(s["gate_calls"]),
            "post_calls": int(s["post_calls"]), "parse_failures": int(s["parse_failures"]),
            "mean_latency_ms": round(float(s["latency_ms_total"]) / calls, 1) if calls else None,
            "prompt_tokens": int(s["prompt_tokens"]), "completion_tokens": int(s["completion_tokens"]),
            "total_tokens": total_tokens}


def _scorer_stats(arm: str, guard: Any, data_dir: Path) -> dict[str, Any]:
    if arm == "judge" and guard is not None:
        return _judge_scorer_stats(guard)
    if arm == "fluid":
        return _lane_scorer_stats(data_dir / "audit-fluid.jsonl")
    return {"scorer": "none", "calls": 0, "mean_latency_ms": None,
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "parse_failures": 0}


def run_arm(arm: str, args: argparse.Namespace, out_dir: Path) -> dict[str, Any]:
    from agentdojo.attacks import load_attack
    from agentdojo.benchmark import (
        benchmark_suite_with_injections,
        benchmark_suite_without_injections,
        get_suite,
    )
    from agentdojo.logging import OutputLogger
    data_dir = Path(os.environ.get("FLUID_DATA_DIR", ROOT / ".cache" / "agentdojo-data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    logdir = Path(args.logdir) / arm
    logdir.mkdir(parents=True, exist_ok=True)
    suite = get_suite(BENCHMARK_VERSION, args.suite)
    pipeline, guard = _build_pipeline(arm, args.model_id, data_dir,
                                      judge_model=getattr(args, "judge_model", None))
    user_tasks = list(args.user_tasks)
    injection_tasks = list(args.injection_tasks)
    try:
        # The benchmark's TraceLogger reads its logdir from the active logger on the
        # stack; without a context it is a NullLogger with none. OutputLogger writes
        # the per-episode traces that also make the run resumable.
        with OutputLogger(str(logdir), live=None):
            benign = benchmark_suite_without_injections(
                pipeline, suite, logdir, args.force_rerun, user_tasks=user_tasks,
                benchmark_version=BENCHMARK_VERSION)
            attack = load_attack(args.attack, suite, pipeline)
            attacked = benchmark_suite_with_injections(
                pipeline, suite, attack, logdir, args.force_rerun, user_tasks=user_tasks,
                injection_tasks=injection_tasks, benchmark_version=BENCHMARK_VERSION)
    finally:
        if guard is not None and hasattr(guard, "close"):
            guard.close()
    summary = {
        "arm": arm, "suite": args.suite, "attack": args.attack,
        "benchmark_version": BENCHMARK_VERSION, "model_id": args.model_id,
        "judge_model": (getattr(args, "judge_model", None) or args.model_id) if arm == "judge" else None,
        "agentdojo_version": _agentdojo_version(),
        "user_tasks": user_tasks, "injection_tasks": injection_tasks,
        "benign_utility": _rate(benign["utility_results"]),
        "utility_under_attack": _rate(attacked["utility_results"]),
        "attack_success": _rate(attacked["security_results"]),
        "guard_counts": _audit_counts(data_dir / f"audit-{arm}.jsonl") if arm == "fluid" else None,
        "scorer_stats": _scorer_stats(arm, guard, data_dir),
        "note": "indicative external check on a small task subset, not a settled comparison",
    }
    out = out_dir / f"summary-{arm}.json"
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"arm": arm, "benign_utility": summary["benign_utility"]["rate"],
                      "utility_under_attack": summary["utility_under_attack"]["rate"],
                      "attack_success": summary["attack_success"]["rate"], "written": str(out.name)}))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arms", default="none,fluid")
    parser.add_argument("--suite", default=SUITE)
    parser.add_argument("--attack", default=ATTACK)
    parser.add_argument("--model-id", default="claude-haiku-5-5")
    parser.add_argument("--judge-model", default=None,
                        help="scorer model for the judge arm; defaults to --model-id. Set to a "
                             "faster model to run the judge arm as a fast-judge-vs-lane variant.")
    parser.add_argument("--user-tasks", nargs="*", default=DEFAULT_USER_TASKS)
    parser.add_argument("--injection-tasks", nargs="*", default=DEFAULT_INJECTION_TASKS)
    parser.add_argument("--logdir", default=str(ROOT / ".cache" / "agentdojo-runs"))
    parser.add_argument("--out", default=str(ROOT / "docs" / "agentdojo"))
    parser.add_argument("--force-rerun", action="store_true")
    parser.add_argument("--smoke", action="store_true",
                        help="one user task and one injection task, to check the config cheaply")
    args = parser.parse_args()
    if args.smoke:
        args.user_tasks = args.user_tasks[:1]
        args.injection_tasks = args.injection_tasks[:1]
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    summaries = [run_arm(arm.strip(), args, out_dir) for arm in args.arms.split(",") if arm.strip()]
    table = {"suite": args.suite, "attack": args.attack, "model_id": args.model_id,
             "benchmark_version": BENCHMARK_VERSION, "arms": {s["arm"]: {
                 "benign_utility": s["benign_utility"]["rate"],
                 "utility_under_attack": s["utility_under_attack"]["rate"],
                 "attack_success": s["attack_success"]["rate"],
                 "scorer": s["scorer_stats"]["scorer"],
                 "scorer_model": s.get("judge_model"),
                 "scorer_calls": s["scorer_stats"]["calls"],
                 "scorer_mean_latency_ms": s["scorer_stats"]["mean_latency_ms"],
                 "scorer_total_tokens": s["scorer_stats"]["total_tokens"],
                 "scorer_parse_failures": s["scorer_stats"].get("parse_failures", 0)}
                 for s in summaries}}
    (out_dir / "comparison.json").write_text(json.dumps(table, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"comparison": table}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
