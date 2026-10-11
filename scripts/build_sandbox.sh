#!/usr/bin/env bash
# Build the IP-006 sandbox image on a Linux node (IP-005 section 2; remediated in IP-006).
#
# Runs from the repository root on the maintainer's machine. It copies the build
# context (the Dockerfile and the trap layer) to the node over SSH and builds the
# image there, because the fork is far too large to build on the Mac and the node
# already runs Docker. The node alias and image tag are supplied by the operator;
# no host or path is hard-coded here.
#
#   FD_SANDBOX_SSH=<ssh-alias> scripts/build_sandbox.sh [--tag TAG] [--context-dir DIR]
#
# Network is used only during this build. The resulting image runs with no
# network. The first build of this workspace is long; its duration is printed.
set -euo pipefail

TAG="fluid-ip006-sandbox:latest"
REMOTE_DIR="/tmp/fd-ip006-build"
SSH_HOST="${FD_SANDBOX_SSH:-}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

while [ $# -gt 0 ]; do
  case "$1" in
    --tag) TAG="$2"; shift 2 ;;
    --ssh) SSH_HOST="$2"; shift 2 ;;
    --context-dir) REMOTE_DIR="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [ -z "$SSH_HOST" ]; then
  echo "set FD_SANDBOX_SSH or pass --ssh <alias> (the Linux node)" >&2
  exit 2
fi

FORK_URL="$(grep -E '^[[:space:]]*url:' "$ROOT/harness/repo.yaml" | head -1 | sed -E 's/.*url:[[:space:]]*//')"
FORK_COMMIT="$(grep -E '^[[:space:]]*commit:' "$ROOT/harness/repo.yaml" | head -1 | sed -E 's/.*commit:[[:space:]]*//')"

if [ -z "$FORK_URL" ] || [ -z "$FORK_COMMIT" ]; then
  echo "could not read repository url/commit from harness/repo.yaml" >&2
  exit 2
fi

echo "building $TAG on node from $FORK_URL @ ${FORK_COMMIT:0:12}"

CTRL="/tmp/fd-ip006-ssh-%C"
SSH=(ssh -o BatchMode=yes -o ControlMaster=auto -o "ControlPath=$CTRL" -o ControlPersist=300 "$SSH_HOST")

"${SSH[@]}" "rm -rf '$REMOTE_DIR' && mkdir -p '$REMOTE_DIR/traps'"
# Copy only the Dockerfile and the trap layer; nothing else from the repo.
tar -C "$ROOT/harness" -cf - Dockerfile traps \
  | "${SSH[@]}" "tar -C '$REMOTE_DIR' -xf -"

START=$("${SSH[@]}" date +%s)
"${SSH[@]}" "cd '$REMOTE_DIR' && docker build --network default \
  --build-arg FORK_URL='$FORK_URL' --build-arg FORK_COMMIT='$FORK_COMMIT' \
  -t '$TAG' -f Dockerfile ." 2>&1
END=$("${SSH[@]}" date +%s)

echo "image $TAG built in $((END - START)) s"
"${SSH[@]}" "docker image inspect '$TAG' --format 'size={{.Size}} created={{.Created}}'"
