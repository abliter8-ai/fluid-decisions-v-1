#!/usr/bin/env python3
"""Download pinned d1 snapshots without importing or executing model code."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "models.yaml"


def config(path: Path) -> dict:
    try:
        import yaml
    except ImportError as e:
        raise SystemExit("PyYAML is required for model downloads") from e
    return yaml.safe_load(path.read_text())


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("model", choices=["d1-3B", "d1-omni-600M", "both"])
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    p.add_argument("--cache-dir", type=Path)
    p.add_argument("--local-dir", type=Path, help="copy snapshots into this directory")
    p.add_argument("--revision", help="override the configured immutable revision")
    p.add_argument("--manifest", type=Path, help="write JSON manifest here")
    a = p.parse_args(argv)
    try:
        from huggingface_hub import HfApi, snapshot_download
    except ImportError as e:
        raise SystemExit("huggingface-hub is required; install the serving extra") from e
    models = config(a.config)["models"]
    names = list(models) if a.model == "both" else [a.model]
    records = []
    for name in names:
        item = models[name]
        requested = a.revision or item["revision"]
        revision = requested
        if len(requested) != 40 or any(c not in "0123456789abcdef" for c in requested.lower()):
            revision = HfApi().model_info(item["repo"], revision=requested).sha
        target = (a.local_dir / name) if a.local_dir else None
        kwargs = {"repo_id": item["repo"], "revision": revision, "local_files_only": False}
        if a.cache_dir:
            kwargs["cache_dir"] = str(a.cache_dir)
        if target:
            kwargs["local_dir"] = str(target)
        path = snapshot_download(**kwargs)
        if target:
            (Path(path) / '.fluid-model.json').write_text(json.dumps({
                'repo': item['repo'], 'revision': revision,
            }, indent=2) + '\n')
        records.append(
            {
                "name": name,
                "repo": item["repo"],
                "revision": revision,
                "snapshot": str(path),
                "files": sorted(
                    str(x.relative_to(path)) for x in Path(path).rglob("*") if x.is_file()
                ),
            }
        )
    out = {"generated_at": datetime.now(timezone.utc).isoformat(), "models": records}
    destination = a.manifest or ((a.local_dir or Path(".")) / "models-manifest.json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
