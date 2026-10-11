# IP-005 live-evidence handoff

Status: the budget change and the whole harness are implemented, committed and
offline-green (ruff, mypy, 178 offline tests, build). The live evidence runs are
long and serial on one node and one gateway; this handoff is how to run and
resume them. Nothing live is marked done until it completes.

Run from the repository root with `PYTHONPATH=.`. Supply the gateway env file and
the lane file at run time; never copy their values into a repository artifact. The
sandbox node alias is supplied through `FD_SANDBOX_SSH` or `--ssh`.

## 1. Build the sandbox image (long, once)

```sh
FD_SANDBOX_SSH=<ssh-alias> scripts/build_sandbox.sh --tag fluid-ip005-sandbox:latest
```

The context (Dockerfile and trap layer) is tarred to the node and built there.
The first build vendors the full Rust workspace and warm-builds the small crates;
it is long. Its duration is printed and goes in CR-005. Background it with a
durable log; a measured unit is recorded before any background launch, per the
long-run gate. If the build is interrupted, rerun the same command; docker reuses
completed layers.

## 2. Smoke (short)

```sh
FD_LIVE=1 FD_SANDBOX_SSH=<ssh-alias> FLUID_LANES_FILE=<path-to-real-lanes-file> \
  PYTHONPATH=. .venv/bin/python scripts/trace_harness.py --env-file <env> \
  --models Abliter8/GLM-5.3-Flash-NoThink --mode enforce --repeats 1 \
  --tasks harness/tasks.yaml --image fluid-ip005-sandbox:latest
```

Narrow to two tasks by trimming a copy of `harness/tasks.yaml`. Expect progress
JSON per combo and one summary file. Traces land in `.cache/traces/<run-id>/`.

## 3. Full evidence runs (long)

```sh
FD_LIVE=1 FD_SANDBOX_SSH=<ssh-alias> FLUID_LANES_FILE=<path-to-real-lanes-file> \
  PYTHONPATH=. .venv/bin/python scripts/trace_harness.py --env-file <env> \
  --models Abliter8/GLM-5.3-Flash-NoThink,Abliter8/Qwen-3.8-FlashNext \
  --mode both --repeats 2 --image fluid-ip005-sandbox:latest
```

Runs are sequential; the node hosts other services, so do not parallelise. The
harness checkpoints after every tool call and resumes with `--resume` pointed at
the same `--output` run directory. Lane and node health are recorded per run.
Summaries are written to `docs/trace-harness-<model-slug>-<mode>.json`.

## 4. Export and measure

```sh
PYTHONPATH=. .venv/bin/python scripts/trace_harness.py \
  --export .cache/traces/<run-id> --export-to tests/fixtures/traces \
  --licence-dir <a-checkout-of-the-fork>

FD_LIVE=1 PYTHONPATH=. .venv/bin/python scripts/measure_scoping.py \
  --lane max --fixtures tests/fixtures/traces --scopes none --repeats 1 \
  --timeout 120 --output docs/scoping-measurement-traces.json
```

Run the identifier scan over the export before committing it. The raw
`.cache/traces/` store stays ignored.

## 5. Finish CR-005

Record the warmup time with the extra probe, the per-trap tables (attempted,
caught, static vs model), the blocked-benign list, timeout/unscored rates,
latency by state size, session events and task completion, per model, never
pooled; the image build duration; what was skipped and why; and the commit. Then
the independent verifier reruns the offline suite, the live module, one harness
run per mode, and inspects the traces and export for identifiers before CR-005 is
marked complete.
