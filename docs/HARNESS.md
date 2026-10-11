# Real-trace harness (IP-005)

The harness produces the first real decision traces: a local worker model is run
through an agent loop on a pinned public repository, inside a container with no
network, no credentials and no host mounts, with every tool call passing through
`GuardedRuntime`. Traps are planted with deterministic matchers that derive
labels by construction. It changes no threshold, pack or axis; its traces are the
input to later calibration.

Everything here is opt-in and isolated. The model never has network access and
never sees a host path. A statically denied command never runs in either mode.

## Layout

| Path | What it is |
| --- | --- |
| `harness/loop.py` | Pure agent-loop state machine; injected gateway and executor. |
| `harness/traps.py` | Trap registry loader and deterministic matchers (path, command, sentinel). |
| `harness/summary.py` | Per-model, per-mode evidence (friction, trap catch, latency, sessions). |
| `harness/export.py` | Curated export in the IP-004 fixture schema. |
| `harness/sandbox.py` | The only module that shells out, over one SSH connection, to the container. |
| `harness/tasks.yaml` | The task set. |
| `harness/traps.yaml` | The trap registry with matchers and derived labels. |
| `harness/repo.yaml` | The pinned public repository, its licence, NOTICE and attribution. |
| `harness/traps/` | The planted files and the build-time apply script. |
| `harness/Dockerfile`, `scripts/build_sandbox.sh` | The sandbox image build on the node. |
| `scripts/trace_harness.py` | The entry point; the only place that touches the gateway, the lanes and the container. |

## Modes

- **enforce**: the gate's verdict blocks. An `ask` or `deny` returns a structured
  refusal to the model; a withheld result returns a refusal. Model verdicts are
  enforced.
- **shadow**: only static denials, deterministic carry-over denials and
  fail-closed infrastructure errors block. Model verdicts and withholding are
  recorded, but the call executes and the result is returned, so the agent's
  behaviour is not changed by the gate. This is where trap catch is measured.

## Build the sandbox image

The image is built once on a Linux node that runs Docker. The node alias and the
image tag are supplied by the operator; nothing is hard-coded. The fork is fetched
at the pinned commit, its dependencies are vendored while the network is available
at build time, and a few small crates are warm-built. The first build of this
workspace is long; its duration is printed and recorded in the completion report.

```sh
FD_SANDBOX_SSH=<ssh-alias> scripts/build_sandbox.sh --tag fluid-ip005-sandbox:latest
```

## Run the harness

Runs require `FD_LIVE=1`, an explicit gateway env file (`TEST_API_ENDPOINT`,
`TEST_API_KEY`), an explicit lane file for the decision service, and the sandbox
node alias. The gateway endpoint and key never appear in any trace, summary or
export.

```sh
FD_LIVE=1 FD_SANDBOX_SSH=<ssh-alias> FLUID_LANES_FILE=<path-to-real-lanes-file> \
  PYTHONPATH=. .venv/bin/python scripts/trace_harness.py \
  --env-file <path-to-env> \
  --models Abliter8/GLM-5.3-Flash-NoThink,Abliter8/Qwen-3.8-FlashNext \
  --mode both --repeats 2 --image fluid-ip006-sandbox:latest
```

Raw traces land in the ignored `.cache/traces/<run-id>/`, one JSONL line per tool
call, plus a per-run `*.result.json` and a `manifest.jsonl`. A run checkpoints
after every tool call and resumes with `--resume`. Summary evidence per model and
mode, lane roles only, is written to `<run-id>/summary-<model-slug>-<mode>.json`;
add `--publish-summaries` to also copy each one into `docs/trace-harness-<model-slug>-<mode>.json`
for a reported run.

`--gate-pack <name>` selects the gate pack for a whole arm and records it in every
result, the manifest and the summary (IP-006 section 5). As of IP-007 the default
`gate` pack is the facts pack, so the two measured arms are the frozen baseline
`--gate-pack gate-packet` (the flat gate and window post-result) and the default
`--gate-pack gate` (the facts gate, the two-stage unit post-result, proportionate
taint and redaction). Omitting the flag uses `gate`.

## Read the traces

Each trace line is one `ToolOutcome`: the tool call, the gate decision (verdict,
reasons, probabilities, `budget`, timings, session), whether it executed and why
not, the executor result, the post-result record, whether the result was released,
redacted or would be withheld, the traps matched on the call, on the result and on
the released text (the IP-007 §4 sentinel that a released result carried no
directive), and the derived labels. The per-(model, mode) summary adds
`taint_activity` (carries_out, blanket-floor, pending and withheld-re-read rows,
split benign/trap) and `redaction_activity` (redactions, units and the released-text
directive check). The model identifier is kept; the gateway endpoint and key are
never written. The sandbox holds only public content and our inert, clearly-fake
traps, so a trace holds nothing private. Run the identifier scan before exporting.

## Export to the fixture schema

```sh
PYTHONPATH=. .venv/bin/python scripts/trace_harness.py \
  --export .cache/traces/<run-id> --export-to tests/fixtures/traces \
  --licence-dir <a-checkout-of-the-fork>
```

The export converts traces into the IP-004 fixture schema with `source: recorded`
and `provenance.derived: true`, deduplicated by state hash and capped per class.
Gate fixtures carry the real tool-call state; post-result fixtures carry the exact
result text as a `{filler, length}` record so `scripts/measure_scoping.py` can
reconstruct it and run over the set unchanged with `--fixtures tests/fixtures/traces
--scopes none`. The fork's licence texts, NOTICE and attribution are written beside
the fixtures.

## Safety

- `docker run --rm --network none` with memory, CPU and PID limits, no host
  mounts, no environment beyond the image, no credentials, no GPU, non-root user.
- Static denials are enforced in every mode; a statically denied command never
  executes anywhere.
- Paths are confined to the repository working directory; an absolute or `..`
  path is refused before anything runs. Untrusted paths are shell-quoted once and
  never placed in an SSH or docker argv.
- The harness never runs a tool on the host, never mounts a host path, never
  passes a credential into the image, and never opens the container's network.
