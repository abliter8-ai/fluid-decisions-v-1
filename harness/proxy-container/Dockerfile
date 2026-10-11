# Run an open-source agent harness through `fluid proxy` in one container.
# Both the proxy and the harness run inside; the proxy binds loopback only.
FROM node:22-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-venv git ca-certificates curl && rm -rf /var/lib/apt/lists/*
# An OpenAI-compatible open-source agent (OpenCode shown here) and its provider.
RUN npm install -g opencode-ai @ai-sdk/openai-compatible
COPY src /src
RUN python3 -m venv /venv && /venv/bin/pip install --no-cache-dir /src
COPY run.sh /run.sh
RUN chmod +x /run.sh
