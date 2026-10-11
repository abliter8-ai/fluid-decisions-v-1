"""Small HTTP client for the d1 System One decision endpoint."""
from __future__ import annotations

import copy
import math
import os
import time
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

import httpx
import yaml


def _flatten_questions(questions: dict[str, Any]) -> dict[str, Any]:
    """Flatten a gate question set to a flat ``{name: question}`` map, accepting
    both the packet pack's flat questions and the facts pack's item-keyed groups,
    so the warmup per-question probe is independent of the gate pack shape."""
    flat: dict[str, Any] = {}
    for key, value in questions.items():
        if isinstance(value, dict) and value.get("type"):
            flat[key] = {k: v for k, v in value.items() if k != "item"}
        elif isinstance(value, dict):
            for name, question in value.items():
                if isinstance(question, dict) and question.get("type"):
                    flat[name] = {k: v for k, v in question.items() if k != "item"}
    return flat


class ClientError(Exception):
    def __init__(self, reason: str, message: str = "client error"):
        self.reason = reason
        # Populated by the service when a budgeted call fails, so a timed-out
        # decision can still report the budget it was given (IP-006).
        self.budget: dict[str, Any] | None = None
        super().__init__(message)


def _probabilities(value: Any, label: str) -> None:
    if not isinstance(value, dict) or not value:
        raise ClientError("invalid_response", f"invalid response: {label} probability distribution")
    numbers = list(value.values())
    if any(not isinstance(number, (int, float)) or isinstance(number, bool) or not math.isfinite(number) or not 0 <= number <= 1 for number in numbers):
        raise ClientError("invalid_response", f"invalid response: {label} probability distribution")
    if not math.isclose(sum(numbers), 1.0, abs_tol=1e-4):
        raise ClientError("invalid_response", f"invalid response: {label} probability distribution")


def _confidence(answer: dict[str, Any], label: str) -> None:
    value = answer.get("confidence")
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ClientError("invalid_response", f"invalid response: confidence for {label}")


def validate_response(response: Any, questions: dict[str, dict[str, Any]]) -> None:
    if not isinstance(response, dict):
        raise ClientError("invalid_response", "invalid response: expected object")
    answers = response.get("answers")
    usage = response.get("usage")
    if not isinstance(answers, dict) or not isinstance(usage, dict) or usage.get("output_tokens") != 0:
        raise ClientError("invalid_response", "invalid response: answers or usage")
    if set(answers) != set(questions):
        raise ClientError("invalid_response", "invalid response: answer set does not match questions")
    for name, question in questions.items():
        answer = answers.get(name)
        expected = question.get("type")
        if expected not in {"noul", "choice", "score"}:
            raise ClientError("invalid_response", f"invalid response: unsupported question type for {name}")
        if not isinstance(answer, dict) or answer.get("type") != expected:
            raise ClientError("invalid_response", f"invalid response: answer type for {name}")
        if expected == "noul":
            value = answer.get("noul")
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ClientError("invalid_response", f"invalid response: noul answer for {name}")
        elif expected == "choice":
            probabilities = answer.get("probabilities")
            _probabilities(probabilities, name)
            _confidence(answer, name)
            criteria = question.get("criteria")
            probability_map = cast(dict[str, Any], probabilities)
            if not isinstance(criteria, dict) or set(probability_map) != set(criteria):
                raise ClientError("invalid_response", f"invalid response: choice criteria for {name}")
            if answer.get("choice") not in probability_map:
                raise ClientError("invalid_response", f"invalid response: choice answer for {name}")
        elif expected == "score":
            probabilities = answer.get("probabilities")
            _probabilities(probabilities, name)
            probability_map = cast(dict[str, Any], probabilities)
            _confidence(answer, name)
            criteria = question.get("criteria")
            criteria_list = cast(list[Any], criteria) if isinstance(criteria, list) else []
            expected_keys = {str(index) for index in range(len(criteria_list))}
            if not expected_keys or set(probability_map) != expected_keys:
                raise ClientError("invalid_response", f"invalid response: score criteria for {name}")
            if not isinstance(answer.get("score"), (int, float)) or isinstance(answer.get("score"), bool) or not math.isfinite(answer["score"]) or not 0 <= answer["score"] <= len(criteria_list) - 1:
                raise ClientError("invalid_response", f"invalid response: score answer for {name}")
            legend = answer.get("legend")
            if not isinstance(legend, dict) or set(legend) != expected_keys or any(legend[str(index)] != criteria_list[index] for index in range(len(criteria_list))):
                raise ClientError("invalid_response", f"invalid response: score legend for {name}")


def enrich_response(response: dict[str, Any], lane: str, client_ms: float, questions: dict[str, dict[str, Any]]) -> dict[str, Any]:
    result = copy.deepcopy(response)
    for name, answer in result["answers"].items():
        if answer["type"] == "noul":
            answer["decisiveness"] = 2 * abs(answer["noul"] - 0.5)
        elif answer["type"] in {"choice", "score"}:
            answer["top_probability"] = max(answer["probabilities"].values())
    result["lane"] = lane
    timings = result.get("timings")
    result["timings"] = dict(timings) if isinstance(timings, dict) else {}
    result["timings"]["client_ms"] = round(client_ms, 1)
    return result


def _default_urls() -> dict[str, str]:
    path = Path(__file__).with_name("lanes.yaml")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    configured = cast(dict[str, Any], raw.get("lanes", raw))
    return {lane: str(cast(dict[str, Any], configured[lane])["url"]).rstrip("/") for lane in ("max", "omni") if isinstance(configured.get(lane), dict) and configured[lane].get("url")}


def _valid_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _connection_reset(error: BaseException) -> bool:
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, OSError) and getattr(current, "errno", None) in {54, 104}:
            return True
        current = current.__cause__ or current.__context__
    return False


class D1Client:
    def __init__(self, lanes_file: str | os.PathLike[str] | None = None, transport: httpx.BaseTransport | None = None):
        selected = lanes_file or os.environ.get("FLUID_LANES_FILE")
        if selected:
            self.urls = {}
            path = Path(selected)
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            configured = cast(dict[str, Any], raw.get("lanes", raw))
            for lane in ("max", "omni"):
                if isinstance(configured.get(lane), dict) and configured[lane].get("url"):
                    self.urls[lane] = str(configured[lane]["url"]).rstrip("/")
        else:
            self.urls = _default_urls()
        for lane in ("max", "omni"):
            override = os.environ.get("FD_MAX_URL" if lane == "max" else "FD_OMNI_URL")
            if override is not None:
                if not override.strip():
                    self.urls.pop(lane, None)
                else:
                    self.urls[lane] = override.rstrip("/")
        if any(not _valid_url(url) for url in self.urls.values()):
            raise ValueError("lane URLs must be absolute HTTP(S) URLs")
        self.first_timeout = float(os.environ.get("FD_FIRST_TIMEOUT", "8"))
        self.timeout = float(os.environ.get("FD_TIMEOUT", "1"))
        if not math.isfinite(self.first_timeout) or not math.isfinite(self.timeout) or self.first_timeout <= 0 or self.timeout <= 0:
            raise ValueError("client timeouts must be finite and greater than zero")
        post_timeout = os.environ.get("FD_POST_TIMEOUT")
        self.post_timeout: float | None = None
        if post_timeout is not None:
            self.post_timeout = float(post_timeout)
            if not math.isfinite(self.post_timeout) or self.post_timeout <= 0:
                raise ValueError("FD_POST_TIMEOUT must be finite and greater than zero")
        call_timeout = os.environ.get("FD_CALL_TIMEOUT")
        self.call_timeout: float | None = None
        if call_timeout is not None:
            self.call_timeout = float(call_timeout)
            if not math.isfinite(self.call_timeout) or self.call_timeout <= 0:
                raise ValueError("FD_CALL_TIMEOUT must be finite and greater than zero")
        self._seen: set[str] = set()
        self.last_warmup_ms: float | None = None
        self.cost_models: dict[str, dict[str, float]] = {}
        self.warmup_errors: dict[str, str] = {}
        self._http = httpx.Client(transport=transport, trust_env=False)

    def __enter__(self) -> "D1Client":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def _lane(self, lane: str | None, images: Any = None, audio: Any = None) -> str:
        if images and audio is not None:
            raise ClientError("invalid_response", "images and audio cannot be combined")
        selected = lane or ("omni" if audio is not None else "max")
        if selected not in self.urls:
            raise ClientError("lane_down", "requested lane is unavailable")
        if audio is not None and selected != "omni":
            raise ClientError("invalid_response", "audio requires the omni lane")
        return selected

    def _request(self, lane: str, method: str, path: str, json_body: dict[str, Any] | None = None, *, consume_budget: bool = True, budget_s: float | None = None) -> tuple[dict[str, Any], float]:
        if budget_s is not None and (not math.isfinite(budget_s) or budget_s <= 0):
            raise ValueError("budget_s must be finite and greater than zero")
        budget = budget_s if budget_s is not None else ((self.first_timeout if lane not in self._seen else self.timeout) if consume_budget else self.timeout)
        if consume_budget:
            self._seen.add(lane)
        started = time.perf_counter()
        attempts = 0
        while True:
            remaining = budget - (time.perf_counter() - started)
            if remaining <= 0:
                raise ClientError("timeout", "decision request timed out")
            try:
                response = self._http.request(method, self.urls[lane] + path, json=json_body, timeout=remaining)
                break
            except httpx.TimeoutException as exc:
                raise ClientError("timeout", "decision request timed out") from exc
            except httpx.RemoteProtocolError as exc:
                if attempts:
                    raise ClientError("lane_down", "decision lane disconnected") from exc
                attempts += 1
            except httpx.ReadError as exc:
                if not _connection_reset(exc) or attempts:
                    raise ClientError("lane_down", "decision lane disconnected") from exc
                attempts += 1
            except httpx.ConnectError as exc:
                raise ClientError("lane_down", "decision lane is unavailable") from exc
            except httpx.HTTPError as exc:
                raise ClientError("lane_down", "decision lane request failed") from exc
        elapsed = (time.perf_counter() - started) * 1000
        if response.status_code != 200:
            raise ClientError("http_error", f"decision lane returned HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ClientError("invalid_response", "invalid response: JSON object expected") from exc
        if not isinstance(payload, dict):
            raise ClientError("invalid_response", "invalid response: JSON object expected")
        return payload, elapsed

    def system_one(self, state: Any, questions: dict[str, dict[str, Any]], *, images: list[str] | None = None, audio: str | None = None, lane: str | None = None, budget_s: float | None = None) -> dict[str, Any]:
        selected = self._lane(lane, images, audio)
        body: dict[str, Any] = {"model": "d1", "state": state, "questions": questions}
        if images is not None:
            body["images"] = images
        if audio is not None:
            body["audio"] = audio
        payload, elapsed = self._request(selected, "POST", "/v1/systemone", body, budget_s=budget_s)
        validate_response(payload, questions)
        return enrich_response(payload, selected, elapsed, questions)

    def system_one_batch(self, requests: list[dict[str, Any]], *, lane: str | None = None, budget_s: float | None = None) -> dict[str, Any]:
        if not isinstance(requests, list) or not requests:
            raise ClientError("invalid_response", "requests must be a non-empty list")
        if any(not isinstance(item, dict) or not isinstance(item.get("questions"), dict) or not item["questions"] for item in requests):
            raise ClientError("invalid_response", "batch requests must contain question objects")
        has_audio = any(item.get("audio") is not None for item in requests)
        has_text = any(item.get("audio") is None for item in requests)
        if any(item.get("images") and item.get("audio") is not None for item in requests):
            raise ClientError("invalid_response", "images and audio cannot be combined")
        if lane is None and has_audio and has_text:
            raise ClientError("invalid_response", "mixed audio and text batches require an explicit omni lane")
        selected = self._lane(lane, None, "audio" if has_audio else None)
        body_requests = [dict(item) for item in requests]
        payload, elapsed = self._request(selected, "POST", "/v1/systemone/batch", {"requests": body_requests}, budget_s=budget_s)
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list) or len(results) != len(requests):
            raise ClientError("invalid_response", "invalid response: batch results")
        enriched = []
        for request, result in zip(requests, results):
            questions = request.get("questions")
            if not isinstance(questions, dict):
                raise ClientError("invalid_response", "invalid response: batch question set")
            validate_response(result, questions)
            enriched.append(enrich_response(result, selected, elapsed, questions))
        return {"results": enriched, "timings": {**(payload.get("timings") or {}), "client_ms": round(elapsed, 1)}, "lane": selected}

    def warmup(self, lane: str = "max") -> dict[str, Any]:
        started = time.perf_counter()
        self.warmup_errors.pop(lane, None)
        self.cost_models.pop(lane, None)
        try:
            result = self.system_one("Warm-up state.", {"warmup": {"type": "noul", "instructions": "Is this a warm-up?"}}, lane=lane)
        except ClientError as exc:
            self.warmup_errors[lane] = exc.reason
            self.last_warmup_ms = round((time.perf_counter() - started) * 1000, 1)
            raise
        try:
            from .policy import load_pack
            from .scoping import strip_scopes
            post = load_pack("gate")["post_result"]
            policy = post["policy"]
            questions = post["questions"]
            # Probe lengths: the explicit warmup_probe_chars (IP-006 units pack) or
            # the window width and its fraction (the packet pack). Two points fit
            # the char cost line.
            probe_chars = policy.get("warmup_probe_chars")
            if isinstance(probe_chars, (list, tuple)) and len(probe_chars) >= 2:
                lengths = [max(1, int(value)) for value in probe_chars[:2]]
            else:
                width = int(policy["window_chars"])
                lengths = [width, max(1, int(width * policy["warmup_probe_fraction"]))]
            quarter = lengths[-1]
            filler = "This is ordinary benign result text used for latency measurement. "
            probe_timings: list[tuple[int, float]] = []
            for length in lengths:
                probe = self.system_one_batch(
                    [{"state": (filler * ((length // len(filler)) + 1))[:length], "questions": questions}],
                    lane=lane,
                    budget_s=self.first_timeout,
                )
                # system_one_batch reports one elapsed time for the request.
                probe_timings.append((length, float(probe["timings"]["client_ms"])))
            (x1, y1), (x2, y2) = probe_timings[0], probe_timings[-1]
            slope = max(0.0, (y1 - y2) / (x1 - x2)) if x1 != x2 else 0.0
            intercept = max(0.0, y1 - slope * x1, y2 - slope * x2)
            model = {"intercept_ms": intercept, "ms_per_char": slope, "ms_per_question": 0.0}
            # One extra probe isolates the marginal per-question cost: the same
            # quarter-window state asked the gate pack's full question set,
            # compared with the two post-result questions already probed at that
            # length. A failed gate probe leaves ms_per_question at 0.0 rather
            # than discarding the char-based model.
            try:
                gate_questions = strip_scopes(_flatten_questions(load_pack("gate")["questions"]))
                gate_probe = self.system_one_batch(
                    [{"state": (filler * ((quarter // len(filler)) + 1))[:quarter], "questions": gate_questions}],
                    lane=lane,
                    budget_s=self.first_timeout,
                )
                gate_ms = float(gate_probe["timings"]["client_ms"])
                span = len(gate_questions) - len(questions)
                if span > 0:
                    model["ms_per_question"] = max(0.0, (gate_ms - y2) / span)
            except (ClientError, OSError, ValueError, KeyError, TypeError) as probe_exc:
                self.warmup_errors[lane] = "per_question_probe:" + getattr(probe_exc, "reason", type(probe_exc).__name__)
            self.cost_models[lane] = model
        except (ClientError, OSError, ValueError, KeyError, TypeError) as exc:
            self.cost_models.pop(lane, None)
            self.warmup_errors[lane] = getattr(exc, "reason", type(exc).__name__)
        self.last_warmup_ms = round((time.perf_counter() - started) * 1000, 1)
        return result

    def request_budget(self, total_chars: int, question_count: int, policy: dict[str, Any],
                       lane: str = "max", *, override: float | None = None) -> dict[str, Any]:
        """Derive a per-call budget from the measured cost model and a YAML policy.

        Expected cost is ``intercept_ms + ms_per_char * total_chars +
        ms_per_question * question_count``. ``override`` is an optional fixed
        budget (from ``FD_CALL_TIMEOUT`` or ``FD_POST_TIMEOUT``); it is still
        capped by the policy. When no cost model exists the budget falls back to
        the fixed timeout and ``budget_source`` records that. A known over-cap
        cost (or a fixed override smaller than the required budget) sets
        ``exceeds_budget`` so the caller can decline to send."""
        if not isinstance(total_chars, int) or isinstance(total_chars, bool) or total_chars < 0:
            raise ValueError("total_chars must be a non-negative integer")
        if not isinstance(question_count, int) or isinstance(question_count, bool) or question_count < 0:
            raise ValueError("question_count must be a non-negative integer")
        try:
            margin = float(policy["budget_margin"])
            maximum = float(policy["max_budget_s"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("budget policy is invalid") from exc
        if not math.isfinite(margin) or margin <= 0 or not math.isfinite(maximum) or maximum <= 0:
            raise ValueError("budget policy must be finite and greater than zero")
        # The derived-budget floor is the YAML floor_s when the pack sets one
        # (IP-006); FD_TIMEOUT is only the no-cost-model fallback.
        floor = self.timeout
        floor_s = policy.get("floor_s")
        if isinstance(floor_s, (int, float)) and not isinstance(floor_s, bool) and math.isfinite(floor_s) and floor_s > 0:
            floor = float(floor_s)
        model = self.cost_models.get(lane)
        expected_ms = None
        required_s = None
        if model is not None:
            per_question = float(model.get("ms_per_question", 0.0))
            expected_ms = max(0.0, model["intercept_ms"] + model["ms_per_char"] * total_chars
                              + per_question * question_count)
            required_s = margin * expected_ms / 1000.0
        if override is not None:
            source = "override"
            budget_s = min(override, maximum)
        elif required_s is not None:
            source = "measured"
            budget_s = min(max(floor, required_s), maximum)
        else:
            source = "fallback"
            budget_s = min(override if override is not None else self.timeout, maximum)
        exceeds = required_s is not None and (required_s > maximum or (override is not None and override < required_s))
        return {"budget_s": budget_s, "budget_source": source, "expected_ms": expected_ms,
                "exceeds_budget": exceeds, "total_chars": total_chars, "questions": question_count}

    def post_result_budget(self, total_chars: int, policy: dict[str, Any], lane: str = "max") -> dict[str, Any]:
        """Thin wrapper preserving IP-003 behaviour: post-result questions carry
        no per-question term, and ``FD_POST_TIMEOUT`` is the fixed override."""
        return self.request_budget(total_chars, 0, policy, lane, override=self.post_timeout)

    def health(self) -> dict[str, Any]:
        result: dict[str, Any] = {"last_warmup_ms": self.last_warmup_ms, "cost_models": copy.deepcopy(self.cost_models), "warmup_errors": dict(self.warmup_errors)}
        for lane in self.urls:
            try:
                payload, elapsed = self._request(lane, "GET", "/health", consume_budget=False)
                result[lane] = {**payload, "lane": lane, "timings": {"client_ms": round(elapsed, 1)}}
            except ClientError as exc:
                result[lane] = {"lane": lane, "error": {"reason": exc.reason, "message": str(exc)}}
        return result
