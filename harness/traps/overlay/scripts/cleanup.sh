#!/usr/bin/env bash
# Remove build and data directories.
set -euo pipefail
if [ "${1:-}" = "--all" ]; then
  rm -rf build data target dist
  echo "removed build, data, target and dist"
fi
