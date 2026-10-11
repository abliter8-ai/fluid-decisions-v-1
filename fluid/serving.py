"""Optional local HTTP adapter for pinned Liquid d1 snapshots."""

from __future__ import annotations

import base64
import contextlib
import io
import json
import re
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MAX_BODY = 48 * 1024 * 1024


def _models(path: Path | None = None) -> dict[str, dict]:
    try:
        import yaml
    except ImportError as e:
        raise RuntimeError("PyYAML is required for serving") from e
    default = ROOT / 'config' / 'models.yaml'
    if not default.exists():
        default = Path(sys.prefix) / 'share' / 'fluid-decisions' / 'models.yaml'
    return yaml.safe_load((path or default).read_text())["models"]


class RequestError(Exception):
    def __init__(self, message: str, status: int = 422):
        self.message, self.status = message, status


def _bytes(value: Any) -> bytes:
    if not isinstance(value, str):
        raise RequestError("media must be a data URL, base64 string, or URL")
    if value.startswith("data:"):
        try:
            return base64.b64decode(value.split(",", 1)[1], validate=True)
        except Exception as e:
            raise RequestError("invalid media base64") from e
    if value.startswith(("http://", "https://")):
        with urllib.request.urlopen(urllib.request.Request(value), timeout=10) as r:
            raw = r.read(MAX_BODY + 1)
        if len(raw) > MAX_BODY:
            raise RequestError("media is too large")
        return raw
    try:
        return base64.b64decode(value, validate=True)
    except Exception as e:
        raise RequestError("invalid media base64") from e


def _validate_question(name: str, q: Any) -> None:
    if not isinstance(q, dict) or q.get("type") not in {"choice", "score", "noul"}:
        raise RequestError(f"question {name!r} has invalid type")
    if not isinstance(q.get("instructions"), str) or not q['instructions'].strip():
        raise RequestError(f"question {name!r} needs instructions")
    if q["type"] == "choice" and (not isinstance(q.get("criteria"), dict)
        or not 2 <= len(q['criteria']) <= 255
        or any(not isinstance(k, str) or not isinstance(v, str) for k, v in q['criteria'].items())):
        raise RequestError(f"question {name!r} needs choice criteria")
    if q["type"] == "score" and (not isinstance(q.get("criteria"), list)
        or not 2 <= len(q['criteria']) <= 10
        or any(not isinstance(v, str) for v in q['criteria'])):
        raise RequestError(f"question {name!r} needs score criteria")


def validate(body: dict, modalities: list[str]) -> None:
    if (
        not isinstance(body, dict)
        or not isinstance(body.get("questions"), dict)
        or not body["questions"]
    ):
        raise RequestError("questions must be a non-empty object")
    for name, q in body["questions"].items():
        _validate_question(name, q)
    images, audio = body.get("images"), body.get("audio")
    if images and audio is not None:
        raise RequestError('images and audio cannot be combined')
    if body.get('state') is None and not images and audio is None:
        raise RequestError('state or media is required')
    if images is not None:
        if not isinstance(images, list) or len(images) > 8:
            raise RequestError("images must be a list of at most 8")
        if "image" not in modalities:
            raise RequestError("model does not support images")
    if audio is not None:
        if images:
            raise RequestError("images and audio cannot be combined")
        if "audio" not in modalities:
            raise RequestError("model does not support audio")


def _audio(value: str):
    try:
        import numpy as np
        import soundfile as sf
    except ImportError as e:
        raise RequestError("soundfile and numpy are required for audio", 500) from e
    try:
        data, rate = sf.read(io.BytesIO(_bytes(value)), dtype="float32", always_2d=True)
    except Exception as e:
        raise RequestError("invalid audio", 422) from e
    if len(data) == 0 or rate <= 0 or not np.isfinite(data).all():
        raise RequestError('audio must contain finite nonempty samples')
    if len(data) / rate > 30:
        raise RequestError("audio must be at most 30 seconds")
    mono = data.mean(axis=1)
    if rate != 16000:
        positions = np.arange(0, len(mono), rate / 16000)
        mono = np.interp(positions, np.arange(len(mono)), mono).astype("float32")
    return mono


class DecisionService:
    def __init__(
        self,
        model=None,
        *,
        model_name="d1-3B",
        model_path=None,
        device="auto",
        dtype="auto",
        config_path=None,
    ):
        self.config = _models(config_path)[model_name]
        self.model_name = model_name
        self.model_path = model_path
        self.device_name, self.dtype_name, self.model = device, dtype, model
        self.revision = self.config["revision"]
        if model_path:
            path = Path(model_path).resolve()
            metadata = path / '.fluid-model.json'
            if metadata.exists():
                provenance = json.loads(metadata.read_text())
                if provenance.get('repo') != self.config['repo']:
                    raise ValueError('snapshot model does not match selected model')
                self.revision = provenance.get('revision')
            else:
                self.revision = path.name if re.fullmatch(r'[a-f0-9]{40}', path.name) else None
        self.lock = threading.Lock()
        if model is None:
            self.model = self._load(self.config, model_path, device, dtype)
            self.device_name = str(next(self.model.parameters()).device)
            self.dtype_name = str(next(self.model.parameters()).dtype).removeprefix("torch.")

    @staticmethod
    def _load(config, path, device, dtype):
        try:
            import torch
        except ImportError as e:
            raise RuntimeError("serving requires torch") from e
        try:
            from transformers import AutoModel
        except ImportError as e:
            raise RuntimeError("serving requires transformers") from e
        target = (
            device
            if device != "auto"
            else (
                "mps"
                if torch.backends.mps.is_available()
                else "cuda"
                if torch.cuda.is_available()
                else "cpu"
            )
        )
        if dtype == "auto":
            dtype = "float16" if target in {"mps", "cuda"} else "float32"
        model = AutoModel.from_pretrained(
            str(path or config['repo']), revision=None if path else config['revision'],
            trust_remote_code=True, dtype=getattr(torch, dtype),
            local_files_only=bool(path),
        )
        model.to(target).eval()
        return model

    @property
    def health(self):
        return {
            "status": "ok",
            "loaded": self.model is not None,
            "model": self.model_name,
            "revision": self.revision,
            "device": self.device_name,
            "dtype": self.dtype_name,
            "modalities": self.config["modalities"],
        }

    def _inputs(self, body):
        out = {"state": body.get("state"), "questions": body["questions"]}
        if body.get("images") is not None:
            try:
                from PIL import Image

                out["images"] = [
                    Image.open(io.BytesIO(_bytes(x))).convert("RGB") for x in body["images"]
                ]
            except ImportError as e:
                raise RequestError("Pillow is required for images", 500) from e
        if body.get("audio") is not None:
            out["audio"] = _audio(body["audio"])
        return out

    def invoke(self, body: dict) -> dict:
        batch = self.batch({'requests': [body]})
        return {'model': self.model_name, **batch['results'][0], 'timings': batch['timings']}

    def batch(self, body: dict) -> dict:
        requests = body.get("requests") if isinstance(body, dict) else None
        if not isinstance(requests, list) or not requests:
            raise RequestError("requests must be a non-empty list")
        for request in requests:
            validate(request, self.config["modalities"])
        inputs = [self._inputs(r) for r in requests]
        fields = ('state', 'questions', 'images', 'audio') if 'audio' in self.config['modalities'] else ('state', 'questions', 'images')
        args = [tuple(item.get(k) for k in fields) for item in inputs]
        started = time.perf_counter()
        # Injected offline fake models do not pull in the optional torch stack.
        torch = sys.modules.get('torch')
        inference = torch.inference_mode() if torch else contextlib.nullcontext()
        with self.lock, inference:
            results = self.model.system_one_batch(args)
        if not isinstance(results, list) or len(results) != len(requests):
            raise RequestError("model returned an invalid batch", 500)
        for result in results:
            if (not isinstance(result, dict) or not isinstance(result.get('answers'), dict)
                or not isinstance(result.get('usage'), dict) or result['usage'].get('output_tokens') != 0):
                raise RequestError('model returned invalid answers or usage', 500)
        return {
            "model": self.model_name,
            "results": results,
            "timings": {"ms": round((time.perf_counter() - started) * 1000, 1)},
        }


def make_handler(service: DecisionService):
    class Handler(BaseHTTPRequestHandler):
        def send_json(self, status, value):
            raw = json.dumps(value, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            self.send_json(200, service.health) if self.path == "/health" else self.send_json(
                404, {"error": "not found"}
            )

        def do_POST(self):
            try:
                n = int(self.headers.get("Content-Length", "-1"))
                if n < 0 or n > MAX_BODY:
                    raise RequestError("request body too large")
                body = json.loads(self.rfile.read(n))
                out = (
                    service.batch(body)
                    if self.path == "/v1/systemone/batch"
                    else service.invoke(body)
                    if self.path == "/v1/systemone"
                    else None
                )
                self.send_json(404, {"error": "not found"}) if out is None else self.send_json(
                    200, out
                )
            except RequestError as e:
                self.send_json(e.status, {"error": e.message})
            except (ValueError, json.JSONDecodeError):
                self.send_json(422, {"error": "invalid JSON"})
            except Exception:
                self.send_json(500, {"error": 'model execution failed; inspect the serving console'})
                import traceback
                traceback.print_exc()

        def log_message(self, *args):
            pass

    return Handler


def run_server(
    *,
    model="d1-3B",
    model_path=None,
    host="127.0.0.1",
    port=None,
    device="auto",
    dtype="auto",
    config_path=None,
):
    service = DecisionService(
        model_name=model, model_path=model_path, device=device, dtype=dtype, config_path=config_path
    )
    print(json.dumps({'event': 'loaded', **service.health}), flush=True)
    ThreadingHTTPServer(
        (host, port or service.config.get("port", 8774)), make_handler(service)
    ).serve_forever()
