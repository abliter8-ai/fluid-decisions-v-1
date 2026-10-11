"""A small local-only worker that routes one read_file call through the gate.

This example intentionally has no shell execution, network client, or text
generation. Replace ``read_file`` with the host runtime's executor while
preserving the GuardedRuntime boundary around every tool dispatch.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from fluid.service import Decisions, GuardedRuntime


def read_file(path: str) -> str:
    """Read a local UTF-8 file through Python's file API."""
    return Path(path).read_text(encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a local read_file call through Fluid Decisions.")
    parser.add_argument("--path", default="README.md", help="local UTF-8 file to read")
    parser.add_argument("--user-request", default="Read the requested local file")
    parser.add_argument("--session-id", default="example-runtime")
    args = parser.parse_args()

    decisions = Decisions()
    try:
        runtime = GuardedRuntime(decisions, session_id=args.session_id)
        result: dict[str, Any] = runtime.run(
            "read_file",
            {"path": args.path},
            lambda: read_file(args.path),
            user_request=args.user_request,
        )
    finally:
        decisions.close()

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("executed") and result.get("result_withheld") is False else 2


if __name__ == "__main__":
    raise SystemExit(main())
