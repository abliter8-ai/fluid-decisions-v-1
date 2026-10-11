#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fluid.serving import run_server


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--model", default="d1-3B")
    p.add_argument("--model-path", type=Path)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--device", choices=["auto", "cpu", "mps", "cuda"], default="auto")
    p.add_argument("--dtype", choices=["auto", "float16", "float32", "bfloat16"], default="auto")
    p.add_argument("--config", type=Path)
    a = p.parse_args()
    run_server(
        model=a.model,
        model_path=a.model_path,
        host=a.host,
        port=a.port,
        device=a.device,
        dtype=a.dtype,
        config_path=a.config,
    )


if __name__ == "__main__":
    main()
