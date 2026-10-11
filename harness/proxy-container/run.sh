#!/usr/bin/env bash
# Start `fluid proxy` and point an unmodified OpenAI-compatible harness at it.
# Config comes from the environment, never baked into the image:
#   TEST_API_ENDPOINT  upstream OpenAI-compatible gateway base URL
#   TEST_API_KEY       upstream key
#   FD_MAX_URL         the d1 text lane (so the guard can score)
#   FD_AGENT_MODEL     the agent model id served by the gateway
#   PROXY_PORT         loopback port for the proxy (default 8765)
#   TASK               the instruction to run
#   WORKDIR            the repository the agent works in (for file-ownership facts)
set -u
PORT="${PROXY_PORT:-8765}"
WORKDIR="${WORKDIR:-/work/repo}"
OUT="${OUT:-/work}"
mkdir -p "$OUT" "$WORKDIR"
# The proxy forwards to the gateway and applies the guard; --workdir feeds the
# file-ownership facts; --log-args records a masked excerpt of refused calls.
/venv/bin/python -m fluid.cli proxy --listen "127.0.0.1:${PORT}" \
  --workdir "$WORKDIR" --log-args 2> "$OUT/run.jsonl" &
for _ in $(seq 1 40); do grep -q '"event": "listening"' "$OUT/run.jsonl" 2>/dev/null && break; sleep 1; done
# Point the harness at the in-container proxy as an OpenAI-compatible provider.
cat > "$WORKDIR/opencode.json" <<JSON
{
  "\$schema": "https://opencode.ai/config.json",
  "provider": {
    "fluid": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Fluid Proxy",
      "options": { "baseURL": "http://127.0.0.1:${PORT}/v1", "apiKey": "local-proxy" },
      "models": { "${FD_AGENT_MODEL:?set FD_AGENT_MODEL}": { "name": "agent via fluid" } }
    }
  }
}
JSON
cd "$WORKDIR"
opencode run "${TASK:?set TASK}" --model "fluid/${FD_AGENT_MODEL}" --dir "$WORKDIR" --auto > "$OUT/harness.txt" 2>&1
echo "[run] opencode exit $?" >&2
