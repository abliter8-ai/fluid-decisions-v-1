#!/usr/bin/env python3
"""Real-trace harness entry point (IP-005).

The only place that touches the gateway, the decision lanes and the container.
It drives a local worker model through the agent loop on the sandboxed
repository, gating every tool call through ``GuardedRuntime`` via the decision
service, and records one JSONL line per tool call into an ignored local trace
store. A separate ``--export`` step turns a completed run into IP-004 fixtures.

Run requires ``FD_LIVE=1`` and an explicit gateway env file and lane file. The
gateway endpoint and key never appear in any trace, summary or export; the model
identifier and the public repository identity are the only external names.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx
import yaml

from fluid.audit import Audit
from fluid.client import D1Client
from fluid.ledger import Ledger
from fluid.service import Decisions
from harness.export import build_export, write_export
from harness.loop import DEFAULT_TOOLS, AgentLoop
from harness.summary import summarize
from harness.traps import load_traps

ROOT = Path(__file__).resolve().parents[1]


def load_env(path: Path) -> dict[str, str]:
    """Parse a KEY=VALUE env file. The values never enter a trace or summary."""
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip():
            values[key.strip()] = value.strip().strip("\"'")
    return values
DEFAULT_MODELS = ["Abliter8/GLM-5.3-Flash-NoThink", "Abliter8/Qwen-3.8-FlashNext"]


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def make_gateway(client: httpx.Client, endpoint: str, model: str, *, retries: int = 6):
    """A gateway callable for one model: messages + tools -> parsed assistant reply.

    A transient worker-gateway blip (RemoteProtocolError, ReadError, ConnectError,
    timeout or a 5xx) is retried with an increasing backoff, so one flake no longer
    throws away a whole task run. The backoff is sized to outlast an unrelated
    gateway container recreate (grace plus startup, about 26 s observed): six
    attempts with 3/6/9/12/15 s waits cover roughly 45 s. A failure that persists
    past that still raises, and the loop records it as a gateway error for that
    turn. Only the worker gateway is retried here; the decision lane is separate."""
    def gateway(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        body = {"model": model, "messages": messages, "tools": tools,
                "tool_choice": "auto", "temperature": 0, "max_tokens": 8192}
        for attempt in range(retries):
            try:
                response = client.post(endpoint + "/chat/completions", json=body)
                if response.status_code >= 500:
                    raise httpx.HTTPStatusError("server error", request=response.request, response=response)
                response.raise_for_status()
                break
            except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError,
                    httpx.TimeoutException, httpx.HTTPStatusError):
                if attempt + 1 >= retries:
                    raise
                time.sleep(min(15.0, 3.0 * (attempt + 1)))
        payload = response.json()
        choice = (payload.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        maybe_calls = message.get("tool_calls")
        raw_calls = maybe_calls if isinstance(maybe_calls, list) else []
        parsed = []
        for call in raw_calls:
            if not isinstance(call, dict):
                continue
            function = call.get("function") or {}
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except (TypeError, json.JSONDecodeError):
                arguments = {}
            parsed.append({"id": call.get("id") or uuid.uuid4().hex,
                           "name": function.get("name", ""),
                           "input": arguments if isinstance(arguments, dict) else {}})
        usage = payload.get("usage") or {}
        safe_usage = {k: usage[k] for k in ("prompt_tokens", "completion_tokens") if isinstance(usage.get(k), (int, float))}
        details = usage.get("completion_tokens_details")
        if isinstance(details, dict) and isinstance(details.get("reasoning_tokens"), (int, float)):
            safe_usage["reasoning_tokens"] = details["reasoning_tokens"]
        return {"content": message.get("content"), "tool_calls": parsed,
                "raw_tool_calls": raw_calls, "finish_reason": choice.get("finish_reason"),
                "usage": safe_usage}
    return gateway


def write_summary(out_root: Path, model: str, mode: str, evidence: dict[str, Any],
                  gate_pack: str, publish_dir: Path | None = None) -> dict[str, str]:
    """Stamp the gate pack and write the summary under the run directory, whatever
    --output is; copy it into ``publish_dir`` only for a reported run (plan section
    5). ``publish_dir`` is a per-run directory (e.g. ``docs/arm-calibrated``): the
    summary keeps its ``summary-<model>-<mode>.json`` name, so publishing never
    overwrites another report's top-level ``docs/trace-harness-*`` evidence."""
    evidence["gate_pack"] = gate_pack
    summary_path = out_root / f"summary-{_slug(model)}-{mode}.json"
    summary_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    written = {"summary_written": str(summary_path)}
    if publish_dir is not None:
        target = publish_dir if publish_dir.is_absolute() else ROOT / publish_dir
        target.mkdir(parents=True, exist_ok=True)
        published = target / f"summary-{_slug(model)}-{mode}.json"
        shutil.copyfile(summary_path, published)
        try:
            written["summary_published"] = str(published.relative_to(ROOT))
        except ValueError:
            written["summary_published"] = str(published)
    return written


def _guard(lanes_file: Path, data_dir: Path) -> Decisions:
    client = D1Client(lanes_file=lanes_file)
    return Decisions(client=client, audit=Audit(data_dir / "audit.jsonl"),
                     ledger=Ledger(data_dir / "sessions"))


def run(args: argparse.Namespace) -> int:
    if os.environ.get("FD_LIVE") != "1":
        print(json.dumps({"error": "live harness is opt-in; set FD_LIVE=1"}))
        return 2
    from harness.sandbox import Sandbox, node_health

    if not args.ssh:
        print(json.dumps({"error": "a sandbox node is required; pass --ssh <alias> or set FD_SANDBOX_SSH"}))
        return 2
    env = load_env(Path(args.env_file))
    endpoint = env.get("TEST_API_ENDPOINT", "").rstrip("/")
    key = env.get("TEST_API_KEY", "")
    if not endpoint or not key:
        print(json.dumps({"error": "env file must define TEST_API_ENDPOINT and TEST_API_KEY"}))
        return 2
    tasks_doc = yaml.safe_load(Path(args.tasks).read_text(encoding="utf-8"))
    tasks = tasks_doc["tasks"]
    max_turns = int(tasks_doc.get("max_turns", 25))
    max_wall_s = float(tasks_doc.get("max_wall_s", 600))
    traps = load_traps(ROOT / "harness" / "traps.yaml")
    trap_ids = [t.id for t in traps]
    models = args.models.split(",") if args.models else DEFAULT_MODELS
    modes = ["enforce", "shadow"] if args.mode == "both" else [args.mode]
    # --gate-pack selects the pack for the whole arm (baseline gate-packet vs the
    # the frozen flat baseline gate-packet). Setting FD_GATE_PACK before warmup makes
    # every gate and post_result use it, and warmup probes its own post shape.
    gate_pack = "gate"
    if args.gate_pack:
        if not re.fullmatch(r"gate(-[a-z0-9]+)*", args.gate_pack):
            print(json.dumps({"error": f"invalid --gate-pack: {args.gate_pack!r}"}))
            return 2
        os.environ["FD_GATE_PACK"] = args.gate_pack
        gate_pack = args.gate_pack
    run_id = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    # Resume the most recent existing run directory under --output (one that has a
    # manifest), rather than starting a new timestamped directory each time.
    out_root = Path(args.output) / run_id
    if args.resume:
        existing = sorted(p for p in Path(args.output).glob("*") if (p / "manifest.jsonl").exists())
        if existing:
            out_root = existing[-1]
            run_id = out_root.name
    out_root.mkdir(parents=True, exist_ok=True)
    manifest_path = out_root / "manifest.jsonl"
    done: set[str] = set()
    if args.resume and manifest_path.exists():
        for line in manifest_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                done.add(json.loads(line)["combo"])

    http = httpx.Client(headers={"Authorization": f"Bearer {key}"}, trust_env=False, timeout=180.0)
    collected: dict[tuple[str, str], list[dict[str, Any]]] = {}
    lanes_file = Path(args.lanes_file)
    data_dir = out_root / "fluid-data"
    data_dir.mkdir(parents=True, exist_ok=True)
    guard = _guard(lanes_file, data_dir)
    try:
        warm = guard.warmup()
        print(json.dumps({"warmup": warm.get("verdict"), "cost_model": guard.client.cost_models.get("max")}))
        for model in models:
            gateway = make_gateway(http, endpoint, model)
            for mode in modes:
                results: list[dict[str, Any]] = []
                for task in tasks:
                    for repeat in range(args.repeats):
                        combo = f"{_slug(model)}|{mode}|{task['id']}|{repeat}"
                        trace_file = out_root / f"{_slug(model)}-{mode}-{task['id']}-{repeat}.jsonl"
                        if combo in done and trace_file.exists():
                            results.append(json.loads((out_root / f"{_slug(model)}-{mode}-{task['id']}-{repeat}.result.json").read_text()))
                            continue
                        container = f"fd-ip006-{_slug(model)[:12]}-{mode}-{_slug(task['id'])[:16]}-{repeat}-{uuid.uuid4().hex[:6]}"
                        sandbox = Sandbox(ssh_host=args.ssh, image=args.image, container=container,
                                          memory=args.memory, cpus=args.cpus)
                        health_before = node_health(args.ssh)
                        session_id = f"{run_id}-{combo}"
                        sink = trace_file.open("w", encoding="utf-8")

                        def write_outcome(outcome: Any, _sink: Any = sink) -> None:
                            _sink.write(json.dumps(asdict(outcome), ensure_ascii=False) + "\n")
                            _sink.flush()
                        try:
                            sandbox.start()
                            isolated = sandbox.verify_no_network()
                            loop = AgentLoop(
                                guard=guard, gateway=gateway, executor=sandbox.execute,
                                traps=traps, task=task, mode=mode, model=model,
                                session_id=session_id, repeat=repeat, tools=DEFAULT_TOOLS,
                                max_turns=max_turns, max_wall_s=max_wall_s,
                                on_outcome=write_outcome,
                            )
                            result = loop.run()
                        finally:
                            sandbox.stop()
                            sink.close()
                        health_after = node_health(args.ssh)
                        result_dict = asdict(result)
                        result_dict.update(network_isolated=isolated, container=container,
                                           health_before=health_before, health_after=health_after,
                                           gate_pack=gate_pack)
                        (out_root / f"{_slug(model)}-{mode}-{task['id']}-{repeat}.result.json").write_text(
                            json.dumps(result_dict, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
                        results.append(result_dict)
                        with manifest_path.open("a", encoding="utf-8") as mf:
                            mf.write(json.dumps({"combo": combo, "terminated": result.terminated,
                                                 "turns": result.turns, "network_isolated": isolated,
                                                 "gate_pack": gate_pack}) + "\n")
                        print(json.dumps({"combo": combo, "terminated": result.terminated,
                                          "turns": result.turns, "network_isolated": isolated}, ensure_ascii=False), flush=True)
                collected[(model, mode)] = results
                evidence = summarize(results, model=model, mode=mode, trap_ids=trap_ids)
                # Summaries land under the run directory, whatever --output is;
                # --publish-summaries copies them into docs/ for a reported run.
                publish_dir = Path(args.publish_summaries) if args.publish_summaries else None
                written = write_summary(out_root, model, mode, evidence, gate_pack, publish_dir)
                print(json.dumps(written))
    finally:
        http.close()
        guard.close()
    print(json.dumps({"run_id": run_id, "traces": str(out_root)}))
    return 0


def export(args: argparse.Namespace) -> int:
    """Offline: turn a completed run's traces into the IP-004 fixture schema."""
    source = Path(args.export)
    records: list[dict[str, Any]] = []
    tasks_doc = yaml.safe_load((ROOT / "harness" / "tasks.yaml").read_text(encoding="utf-8"))
    task_text = {t["id"]: t["text"] for t in tasks_doc["tasks"]}
    for result_file in sorted(source.glob("*.result.json")):
        result = json.loads(result_file.read_text(encoding="utf-8"))
        for outcome in result.get("outcomes", []):
            records.append({"outcome": outcome, "task_id": result.get("task_id"),
                            "task_text": task_text.get(result.get("task_id"), ""),
                            "run_id": source.name, "model": result.get("model"),
                            "mode": result.get("mode")})
    bundle = build_export(records, cap_per_class=args.cap)
    repo = yaml.safe_load((ROOT / "harness" / "repo.yaml").read_text(encoding="utf-8"))
    licences = {}
    if args.licence_dir:
        for name in ("LICENSE-MIT", "LICENSE-APACHE", "NOTICE", "ATTRIBUTION.md"):
            path = Path(args.licence_dir) / name
            if path.exists():
                licences[name] = path.read_text(encoding="utf-8")
    readme = _export_readme(repo, bundle)
    written = write_export(args.export_to, bundle, repo=repo, licences=licences, readme=readme)
    print(json.dumps({"gate": len(bundle["gate"]), "post_result": len(bundle["post_result"]),
                      "written": len(written), "out": args.export_to}))
    if not licences:
        print(json.dumps({"warning": "no licence texts copied; pass --licence-dir pointing at the fork checkout"}))
    return 0


def _export_readme(repo: dict[str, Any], bundle: dict[str, list[dict[str, Any]]]) -> str:
    r = repo.get("repository", {})
    return (
        "# Recorded trace fixtures (IP-005)\n\n"
        "These fixtures were derived by matchers from real sandbox traces, not hand-labelled.\n"
        "Every fixture is `source: recorded` with `provenance.derived: true`. They hold only\n"
        f"public content from {r.get('url', 'the disposable repository')} at commit "
        f"`{str(r.get('commit', ''))[:12]}` and our inert, clearly-fake traps.\n\n"
        f"- gate fixtures: {len(bundle['gate'])}\n- post_result fixtures: {len(bundle['post_result'])}\n\n"
        "The fork is dual-licensed MIT OR Apache-2.0; its licence texts, NOTICE and\n"
        "attribution are committed beside these fixtures. Run them through the existing\n"
        "measurement script with `--fixtures <this dir> --scopes none`.\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env.test")
    parser.add_argument("--lanes-file", default=os.environ.get("FLUID_LANES_FILE", "examples/lanes.local.yaml"),
                        help="lane config; defaults to FLUID_LANES_FILE or the public localhost example. "
                             "examples/lanes.maintainer.yaml is a placeholder, not a live config")
    parser.add_argument("--tasks", default=str(ROOT / "harness" / "tasks.yaml"))
    parser.add_argument("--models", help="comma-separated gateway model ids")
    parser.add_argument("--mode", choices=("enforce", "shadow", "both"), default="both")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--output", default=str(ROOT / ".cache" / "traces"))
    parser.add_argument("--ssh", default=os.environ.get("FD_SANDBOX_SSH"))
    parser.add_argument("--image", default="fluid-ip006-sandbox:latest")
    parser.add_argument("--memory", default="4g")
    parser.add_argument("--cpus", default="4")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--gate-pack", default=None,
                        help="gate pack for the run: gate (default, the facts pack) or gate-packet (the frozen flat baseline)")
    parser.add_argument("--publish-summaries", nargs="?", const="docs/arm", default=None,
                        metavar="DIR",
                        help="also copy each run summary into this directory for a reported run "
                             "(default docs/arm); never overwrites top-level docs/trace-harness-* names")
    parser.add_argument("--export", help="a completed run directory to export from (offline)")
    parser.add_argument("--export-to", default=str(ROOT / "tests" / "fixtures" / "traces"))
    parser.add_argument("--licence-dir", help="a checkout of the fork to copy licence/NOTICE/attribution from")
    parser.add_argument("--cap", type=int, default=8, help="fixtures kept per class in the export")
    args = parser.parse_args()
    if args.export:
        return export(args)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
