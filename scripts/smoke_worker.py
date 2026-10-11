#!/usr/bin/env python3
"""Opt-in smoke test for local worker-model tool-call integration."""
from __future__ import annotations

import argparse
import datetime
import json
import os
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from fluid.audit import Audit
from fluid.client import D1Client
from fluid.ledger import Ledger
from fluid.service import Decisions, GuardedRuntime

PUBLIC_FIXTURE_README = "Fluid Decisions public worker smoke fixture."
TOOL = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Read one public project file.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    },
}


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip("\"'")
        if key.strip():
            values[key.strip()] = value
    return values


def redact_url(value: str) -> str:
    parsed = urlsplit(value)
    return urlunsplit((parsed.scheme, parsed.hostname or "", parsed.path, "", ""))


def select_models(catalog: dict[str, Any]) -> list[str]:
    entries = catalog.get("data", catalog.get("models", [])) if isinstance(catalog, dict) else []
    candidates: dict[str, list[str]] = {"qwen": [], "glm": []}
    for entry in entries if isinstance(entries, list) else []:
        model_id = entry.get("id", entry.get("name")) if isinstance(entry, dict) else entry
        if not isinstance(model_id, str):
            continue
        lowered = model_id.lower()
        for family in candidates:
            if family in lowered:
                candidates[family].append(model_id)
                break
    return [sorted(values, key=lambda value: ("nothink" not in value.lower(), value.lower()))[0] for values in candidates.values() if values]


def parse_chat_response(response: dict[str, Any]) -> dict[str, Any]:
    choices = response.get("choices") if isinstance(response, dict) else None
    first = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first.get("message"), dict) else {}
    calls = message.get("tool_calls") if isinstance(message.get("tool_calls"), list) else []
    usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    safe_usage = {key: value for key, value in usage.items() if key in {"prompt_tokens", "completion_tokens", "total_tokens", "reasoning_tokens"} and isinstance(value, (int, float))}
    details = usage.get("completion_tokens_details")
    if isinstance(details, dict) and isinstance(details.get("reasoning_tokens"), (int, float)):
        safe_usage["reasoning_tokens"] = details["reasoning_tokens"]
    finish_reason = first.get("finish_reason")
    text = message.get("content")
    return {
        "finish_reason": finish_reason,
        "usage": safe_usage,
        "tool_calls": calls,
        "empty_completion": not calls and not isinstance(text, str) or not calls and not text,
    }


def run_fixture(runtime: GuardedRuntime, tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    return runtime.run(tool_name, tool_input, lambda: PUBLIC_FIXTURE_README, user_request="Read the public README fixture.")


def smoke(env_file: Path, output: Path, lanes_file: Path) -> int:
    env = load_env(env_file)
    endpoint = env.get("TEST_API_ENDPOINT", "").rstrip("/")
    key = env.get("TEST_API_KEY", "")
    if not endpoint or not key:
        raise SystemExit("env file must define TEST_API_ENDPOINT and TEST_API_KEY")
    headers = {"Authorization": f"Bearer {key}"}
    report: dict[str, Any] = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        "lanes_file_selected": lanes_file.exists(),
        "models": [],
    }
    if not lanes_file.exists():
        report["catalog_error"] = {"reason": "explicit d1 lanes file not found"}
    with tempfile.TemporaryDirectory(prefix="fluid-worker-smoke-") as scratch:
        decisions = Decisions(client=D1Client(lanes_file=lanes_file), audit=Audit(Path(scratch) / "audit.jsonl"), ledger=Ledger(Path(scratch) / "sessions")) if lanes_file.exists() else None
        runtime = GuardedRuntime(decisions, session_id="worker-smoke") if decisions else None
        if runtime:
            report["static_deny"] = run_fixture(runtime, "shell", {"command": "rm -rf /tmp/fluid-worker-smoke"})
        client = httpx.Client(headers=headers, trust_env=False, timeout=180.0)
        try:
            catalog_response = client.get(endpoint + "/models")
            if catalog_response.status_code != 200:
                report["catalog_error"] = {"http_status": catalog_response.status_code, "reason": "model catalog unavailable"}
            else:
                try:
                    model_ids = select_models(catalog_response.json())
                except (ValueError, TypeError):
                    model_ids = []
                for model_id in model_ids:
                    item: dict[str, Any] = {"model": model_id, "max_tokens": 8192, "thinking_route": "max_tokens_8192"}
                    body = {
                        "model": model_id,
                        "messages": [
                            {"role": "system", "content": "Use the read_file tool directly. Do not emit reasoning."},
                            {"role": "user", "content": "Call read_file README.md to inspect the sample project."},
                        ],
                        "tools": [TOOL],
                        "tool_choice": {"type": "function", "function": {"name": "read_file"}},
                        "max_tokens": 8192,
                    }
                    try:
                        response = client.post(endpoint + "/chat/completions", json=body)
                        item["http_status"] = response.status_code
                        if response.status_code != 200:
                            item["error"] = {"reason": "worker request failed", "http_status": response.status_code}
                            report["models"].append(item)
                            continue
                        parsed = parse_chat_response(response.json())
                        item.update({key: parsed[key] for key in ("finish_reason", "usage", "empty_completion")})
                        calls = parsed["tool_calls"]
                        item["parsed_tool_calls"] = len(calls)
                        if calls:
                            call = calls[0] if isinstance(calls[0], dict) else {}
                            function = call.get("function") if isinstance(call.get("function"), dict) else {}
                            try:
                                arguments = json.loads(function.get("arguments", "{}"))
                            except (TypeError, json.JSONDecodeError):
                                arguments = {}
                            item["tool"] = {"name": function.get("name"), "arguments": {"path": arguments.get("path")} if isinstance(arguments, dict) else {}}
                            if function.get("name") == "read_file" and isinstance(arguments, dict) and arguments.get("path") == "README.md":
                                item["runtime"] = run_fixture(runtime, "read_file", {"path": "README.md"}) if runtime else {"executed": False, "reason": "d1 lanes unavailable"}
                            else:
                                item["runtime"] = {"executed": False, "reason": "tool call did not match public fixture"}
                        else:
                            item["runtime"] = {"executed": False, "reason": "empty completion; finish_reason recorded"}
                    except (httpx.TimeoutException, httpx.HTTPError, ValueError):
                        item["error"] = {"reason": "worker request transport or JSON failure"}
                    report["models"].append(item)
        except (httpx.TimeoutException, httpx.HTTPError):
            report["catalog_error"] = {"reason": "model catalog transport failure"}
        finally:
            client.close()
            if decisions:
                decisions.close()
    if not report.get("models") and "catalog_error" not in report:
        report["catalog_error"] = {"reason": "no supported Qwen or GLM model in catalog"}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {output}")
    failed = "catalog_error" in report or not report.get("models") or any("error" in item or item.get("empty_completion") or item.get("runtime", {}).get("executed") is False for item in report["models"])
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env.test", type=Path)
    parser.add_argument("--lanes-file", default="examples/lanes.local.yaml", type=Path)
    parser.add_argument("--output", default="docs/worker-smoke.json", type=Path)
    args = parser.parse_args(argv)
    if os.environ.get("FD_LIVE") != "1":
        parser.error("live worker smoke is opt-in; set FD_LIVE=1")
    return smoke(args.env_file, args.output, args.lanes_file)


if __name__ == "__main__":
    raise SystemExit(main())
