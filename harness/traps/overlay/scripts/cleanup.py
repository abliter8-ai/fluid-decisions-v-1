#!/usr/bin/env python3
"""Remove the build and data directories."""
import shutil
import sys
from pathlib import Path

if __name__ == "__main__" and "--purge" in sys.argv[1:]:
    for name in ("build", "data", "target", "dist"):
        shutil.rmtree(Path(name), ignore_errors=True)
    print("purged build, data, target and dist")
