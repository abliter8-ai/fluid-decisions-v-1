#!/usr/bin/env python3
"""Probe the HTTP contract exposed by both d1 decision lanes.

The probe is intentionally stdlib-only and opt-in: it will not contact a lane
unless ``FD_LIVE=1`` is set.  Every HTTP result keeps its status, elapsed time,
parsed response object, and validation errors so the output is evidence rather
than a pass/fail summary.
"""

from __future__ import annotations

import argparse
import base64
import datetime
import io
import json
import math
import os
import struct
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAX_URL = "http://127.0.0.1:8774"
DEFAULT_OMNI_URL = "http://127.0.0.1:8775"
QUESTION_IDS = {"decision", "severity", "safe"}


def lane_urls() -> dict[str, str]:
    """Return public-safe defaults, with explicit environment overrides."""
    return {
        "max": os.environ.get("FD_MAX_URL", DEFAULT_MAX_URL).rstrip("/"),
        "omni": os.environ.get("FD_OMNI_URL", DEFAULT_OMNI_URL).rstrip("/"),
    }


def data_url(raw: bytes, content_type: str) -> str:
    return f"data:{content_type};base64,{base64.b64encode(raw).decode('ascii')}"


def audio_wav() -> bytes:
    """Return deterministic one-second, 16 kHz, mono PCM16 silence."""
    samples = b"\x00\x00" * 16000
    out = io.BytesIO()
    out.write(b"RIFF")
    out.write(struct.pack("<I", 36 + len(samples)))
    out.write(b"WAVEfmt ")
    out.write(struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16))
    out.write(b"data")
    out.write(struct.pack("<I", len(samples)))
    out.write(samples)
    return out.getvalue()


def shared_request() -> dict[str, Any]:
    return {
        "model": "d1",
        "state": "A customer reports a delayed refund and asks whether the issue is urgent.",
        "questions": {
            "decision": {
                "type": "choice",
                "instructions": "Which team should handle this?",
                "criteria": {"billing": "Payments and refunds", "support": "General customer support"},
            },
            "severity": {
                "type": "score",
                "instructions": "How severe is the issue?",
                "criteria": ["low", "medium", "high"],
            },
            "safe": {"type": "noul", "instructions": "Is this a routine customer support request?"},
        },
    }


def _read_response(response: Any) -> tuple[int, dict[str, Any] | None]:
    status = getattr(response, "status", None)
    if status is None:
        status = response.getcode()
    try:
        raw = response.read()
        return status, json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return status, None


def request_json(url: str, method: str = "GET", body: dict[str, Any] | None = None, timeout: float = 10.0) -> dict[str, Any]:
    payload = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=payload, method=method, headers={"Accept": "application/json"})
    if payload is not None:
        request.add_header("Content-Type", "application/json")
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status, parsed = _read_response(response)
            result: dict[str, Any] = {"status": status, "elapsed_ms": round((time.perf_counter() - started) * 1000, 1), "response": parsed}
    except urllib.error.HTTPError as exc:
        status, parsed = _read_response(exc)
        result = {"status": status, "elapsed_ms": round((time.perf_counter() - started) * 1000, 1), "response": parsed}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        result = {"status": None, "elapsed_ms": round((time.perf_counter() - started) * 1000, 1), "response": None, "error": str(exc)}
    result["validation_errors"] = []
    return result


def validate_typed_response(response: dict[str, Any] | None, expected_ids: set[str] | dict[str, str]) -> list[str]:
    errors: list[str] = []
    if not isinstance(response, dict):
        return ["response is not a JSON object"]
    answers = response.get("answers")
    if not isinstance(answers, dict):
        errors.append("answers is missing or not an object")
    else:
        expected_names = set(expected_ids)
        missing = expected_names - set(answers)
        errors.extend(f"missing answer: {name}" for name in sorted(missing))
        expected_types = expected_ids if isinstance(expected_ids, dict) else {}
        for name in expected_types:
            if name in answers and (not isinstance(answers[name], dict) or answers[name].get("type") != expected_types[name]):
                errors.append(f"answer {name!r} has unexpected type")
        for name, answer in answers.items():
            if not isinstance(answer, dict):
                errors.append(f"answer {name!r} is not an object")
                continue
            answer_type = answer.get("type")
            if answer_type == "noul":
                value = answer.get("noul")
                if not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
                    errors.append(f"noul answer {name!r} is not a finite probability")
            if answer_type in {"choice", "score"}:
                probabilities = answer.get("probabilities")
                if not isinstance(probabilities, dict) or not probabilities:
                    errors.append(f"{answer_type} answer {name!r} has no probability distribution")
                else:
                    values = list(probabilities.values())
                    if any(not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1 for value in values):
                        errors.append(f"{answer_type} answer {name!r} has non-finite probability values")
                    elif not math.isclose(sum(values), 1.0, abs_tol=0.02):
                        errors.append(f"{answer_type} answer {name!r} probability distribution does not sum to 1")
            if answer_type == "noul" and "noul" not in answer:
                errors.append(f"noul answer {name!r} has no noul field")
    usage = response.get("usage")
    if not isinstance(usage, dict) or usage.get("output_tokens") != 0:
        errors.append("usage.output_tokens is not 0")
    return errors


def required_result_errors(result: dict[str, Any]) -> list[str]:
    status = result.get("status")
    if status is None:
        return ["required request did not receive an HTTP response"]
    if status != 200:
        return [f"required request returned HTTP {status}"]
    return []


def _typed_result(url: str, body: dict[str, Any], timeout: float) -> dict[str, Any]:
    result = request_json(url.rstrip("/") + "/v1/systemone", "POST", body, timeout)
    errors = required_result_errors(result)
    if result["status"] == 200:
        errors.extend(validate_typed_response(result["response"], {name: question["type"] for name, question in body["questions"].items()}))
    result["validation_errors"] = sorted(set(errors))
    return result


def _batch_result(url: str, requests: list[dict[str, Any]], timeout: float) -> dict[str, Any]:
    result = request_json(url.rstrip("/") + "/v1/systemone/batch", "POST", {"requests": requests}, timeout)
    errors: list[str] = required_result_errors(result)
    if result["status"] == 200:
        payload = result["response"]
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list) or len(payload["results"]) != len(requests):
            errors.append("batch results length does not match request count")
        else:
            for index, item in enumerate(payload["results"]):
                errors.extend(f"result {index}: {error}" for error in validate_typed_response(item, {name: question["type"] for name, question in requests[index]["questions"].items()}))
    result["validation_errors"] = errors
    return result


def sanitize_health(result: dict[str, Any]) -> dict[str, Any]:
    clean = json.loads(json.dumps(result))
    response = clean.get("response")
    if isinstance(response, dict):
        checkpoint = response.get("checkpoint")
        home = str(Path.home()).rstrip("/")
        if isinstance(checkpoint, str) and (checkpoint == home or checkpoint.startswith(home + "/")):
            response["checkpoint"] = "[redacted]"
    return clean


def new_document(urls: dict[str, str]) -> dict[str, Any]:
    return {
        "probe": "fluid-decisions phase 0 contract probe",
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        "urls": urls,
        "lanes": {lane: {"health": None, "shared": [], "image": None, "audio": None, "mixed_media": None, "batch": None, "validation_failures": []} for lane in urls},
        "lane_differences": [],
    }


def _noul_values(result: dict[str, Any]) -> dict[str, Any]:
    answers = (result.get("response") or {}).get("answers", {})
    return {key: value.get("noul") for key, value in answers.items() if isinstance(value, dict) and isinstance(value.get("noul"), (int, float))}


def add_lane_differences(document: dict[str, Any]) -> None:
    max_results = document["lanes"]["max"].get("shared", [])
    omni_results = document["lanes"]["omni"].get("shared", [])
    for index, (left, right) in enumerate(zip(max_results, omni_results)):
        left_values, right_values = _noul_values(left), _noul_values(right)
        for key in sorted(set(left_values) & set(right_values)):
            document["lane_differences"].append({"operation": "shared", "repeat": index, "question": key, "max": left_values[key], "omni": right_values[key], "absolute_difference": round(abs(left_values[key] - right_values[key]), 6)})


def write_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def probe_lane(name: str, url: str, timeout: float, image: str) -> dict[str, Any]:
    health = sanitize_health(request_json(url + "/health", timeout=timeout))
    health["validation_errors"] = required_result_errors(health)
    lane: dict[str, Any] = {"health": health, "shared": [], "image": None, "audio": None, "mixed_media": None, "batch": None, "validation_failures": []}
    shared = shared_request()
    for _ in range(4):
        result = _typed_result(url, shared, timeout)
        lane["shared"].append(result)
    lane["image"] = _typed_result(url, {**shared, "state": "Inspect the supplied image.", "images": [image]}, timeout)
    if name == "omni":
        audio = data_url(audio_wav(), "audio/wav")
        lane["audio"] = _typed_result(url, {**shared, "state": "Listen to the supplied audio.", "audio": audio}, timeout)
        lane["mixed_media"] = request_json(url + "/v1/systemone", "POST", {**shared, "images": [image], "audio": audio}, timeout)
        lane["mixed_media"]["validation_errors"] = [] if lane["mixed_media"]["status"] == 422 else ["mixed image+audio request did not return HTTP 422"]
    else:
        lane["audio"] = {"status": None, "skipped": True, "reason": "audio is exercised on the omni lane only"}
    lane["batch"] = _batch_result(url, [shared, {**shared, "state": "A second independent request."}], timeout)
    for operation, value in lane.items():
        values = value if operation == "shared" else [value]
        for result in values:
            if isinstance(result, dict) and result.get("validation_errors"):
                lane["validation_failures"].append({"operation": operation, "errors": result["validation_errors"]})
    return lane


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-url", default=None)
    parser.add_argument("--omni-url", default=None)
    parser.add_argument("--output", default="docs/contract-probe.json")
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args(argv)
    if os.environ.get("FD_LIVE") != "1":
        parser.error("live probing is opt-in; set FD_LIVE=1")
    urls = lane_urls()
    if args.max_url:
        urls["max"] = args.max_url.rstrip("/")
    if args.omni_url:
        urls["omni"] = args.omni_url.rstrip("/")
    image_path = REPO_ROOT / "assets" / "liquid-decisions.png"
    if not image_path.exists():
        parser.error(f"probe image not found: {image_path}")
    document = new_document(urls)
    image = data_url(image_path.read_bytes(), "image/png")
    for name, url in urls.items():
        document["lanes"][name] = probe_lane(name, url, args.timeout, image)
        fixture = REPO_ROOT / "tests" / "fixtures" / f"{name}_answers.json"
        successful = next((item for item in document["lanes"][name]["shared"] if item.get("status") == 200 and not item.get("validation_errors")), None)
        if successful and successful.get("response") is not None:
            write_json(fixture, successful["response"])
    add_lane_differences(document)
    output = Path(args.output)
    if not output.is_absolute():
        output = Path.cwd() / output
    write_json(output, document)
    print(f"wrote {output}")
    return 0 if not any(lane["validation_failures"] for lane in document["lanes"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
