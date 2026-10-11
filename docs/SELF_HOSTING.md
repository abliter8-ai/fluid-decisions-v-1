# Download and serve your own d1 models

Fluid Decisions needs a d1 System One HTTP endpoint. It does not require the maintainer's hosts, gateway, tailnet or API key. Use an existing compatible endpoint, or use the download and serving tools below on hardware you choose.

The client, hook and CLI install only HTTP/YAML dependencies. Model loading is a separate optional process. The model names, ports, immutable revisions and licence links live in [config/models.yaml](../config/models.yaml).

## Install from the repository

Python 3.11 or later is required. From your checkout:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install '.[serve,mcp]'
```

For a worker using an already-served model, install `'.[mcp]'` instead. Omit the MCP extra if you only need the CLI or Python library. On Windows, the virtual environment executables are under `.venv\Scripts\`.

## Download

Start with d1-3B for text and images:

```sh
.venv/bin/python scripts/download_models.py d1-3B --local-dir models
```

This downloads the weights, tokenizer, configuration and upstream Python code into `models/d1-3B`, and writes `models/models-manifest.json`. Downloading does not import that code or start a service. Existing complete files are reused; interrupted transfers can be resumed by repeating the command.

Optional short audio support:

```sh
.venv/bin/python scripts/download_models.py d1-omni-600M --local-dir models
```

Use `both` to download both models in one command. `--cache-dir PATH` chooses the Hugging Face cache, `--manifest PATH` selects the output manifest, and `--revision REF` overrides the configured revision. A branch or tag is resolved to its immutable SHA before download. A manifest describes that invocation; use separate manifest filenames if retaining records of several separate downloads.

If Xet transfers stall in your environment, the Hugging Face client supports the standard HTTP download path:

```sh
HF_HUB_DISABLE_XET=1 .venv/bin/python scripts/download_models.py d1-3B --local-dir models
```

No key is required by this project's configuration. If your Hugging Face setup supplies authentication, its existing environment/cache is used; the script does not write tokens to its manifest.

The models carry separate upstream terms: [d1-3B licence](https://huggingface.co/LiquidAI/d1-3B/blob/main/LICENSE) and [d1-omni-600M licence](https://huggingface.co/LiquidAI/d1-omni-600M/blob/main/LICENSE). The server loads their custom Python code using `trust_remote_code=True`. The pinned download includes that code; review the source and terms before loading it. Weights are not distributed in this repository.

## Serve

If you already have a compatible d1 service, skip this section and configure its URL below. A second model process is unnecessary.

On a machine where you want to host the model, keep this process running:

```sh
.venv/bin/python scripts/serve_model.py \
  --model d1-3B --model-path models/d1-3B
```

The default address is `http://127.0.0.1:8774`. `--model-path` also accepts an existing local snapshot; it does not copy the weights. The server reports a revision from the downloaded metadata or a Hugging Face snapshot directory name; an arbitrary unlabelled path reports no verified revision.

For audio, start the optional omni server on the host you choose:

```sh
.venv/bin/python scripts/serve_model.py \
  --model d1-omni-600M --model-path models/d1-omni-600M
```

Omni defaults to port `8775`. Change addresses with `--host` and `--port`. The adapter supplies no authentication and binds to loopback by default; a remote deployment needs an operator-managed access layer.

`--device` accepts `auto`, `cpu`, `mps` or `cuda`. Auto selects an available accelerator, otherwise CPU. `--dtype` accepts `auto`, `float16`, `float32` or `bfloat16`; auto selects float16 on accelerators and float32 on CPU. These are implementation choices, not a claim that every platform has been validated. For an appropriate platform-specific PyTorch build, consult the [official PyTorch installation instructions](https://pytorch.org/get-started/locally/).

The server exposes:

- `GET /health`: model, revision, loaded state, device, dtype and modalities.
- `POST /v1/systemone`: one state and a named set of questions.
- `POST /v1/systemone/batch`: `{"requests": [...]}`, returning `{"results": [...]}`.

Answers retain their distributions and zero-output-token usage. The server accepts up to eight images, converts audio to 16 kHz mono, rejects clips longer than 30 seconds, and rejects images plus audio in one request. It serializes model execution. Stop the foreground process with Ctrl-C when you no longer need it.

## Configure and use the worker tools

From the same checkout in another terminal:

```sh
export FD_MAX_URL=http://127.0.0.1:8774
export FD_OMNI_URL=http://127.0.0.1:8775
.venv/bin/fluid health
.venv/bin/fluid classify --state '"A normal refund request."'
.venv/bin/fluid route --state '"Please refund my duplicate charge."'
.venv/bin/fluid gate --tool read_file --input '{"path":"README.md"}' \
  --user-request 'Read the README'
.venv/bin/fluid verify --source 'The office is closed Sunday.' \
  --claim 'The office opens Sunday.'
```

The contradictory claim should be flagged, with no replacement sentence. Exit codes are `0` allow/clean, `1` deny/finding, and `2` ask/incomplete. Probabilities remain visible; the thresholds are uncalibrated and model decisions can be wrong.

Default first-call timeout is eight seconds; later calls use one second. For slower hardware or newly encountered shapes, explicitly increase the budgets, for example `FD_FIRST_TIMEOUT=30` and `FD_TIMEOUT=30`. Timeouts always return `ask`, never permission to execute.

The [local lane example](../examples/lanes.local.yaml) can be selected with `FLUID_LANES_FILE`. To disable audio, set its `omni` entry to `null` in your own lane file. Core text/image use does not need omni. A generative worker's model endpoint and credentials are separate from these d1 endpoints.

For mandatory tool dispatch, use [the runtime wrapper or stdin hook](USAGE.md#runtime-hook-and-mandatory-enforcement). A static denial can be checked without any model running:

```sh
printf '%s\n' '{"event":"pre_tool","tool_name":"shell","tool_input":{"command":"rm -rf /example"},"session_id":"demo"}' \
  | .venv/bin/python -m fluid.hook
```

This only evaluates JSON; the example command is never executed.

An optional smoke tool checks services you have already chosen to run locally. It neither downloads models nor starts a server:

```sh
FD_LIVE=1 .venv/bin/python scripts/smoke_self_hosted.py \
  --max-url http://127.0.0.1:8774 --output /tmp/fluid-local-smoke.json
```

Add `--omni-url http://127.0.0.1:8775` for audio checks. IP-002 explicitly approved one bounded adapter run using the existing d1-3B snapshot on port 8776. That process was stopped after validation; users with an existing endpoint still do not need another server.

## Validation status

| Component | Evidence |
| --- | --- |
| Client, packs, hook and worker integration | Live against the existing MPS d1-3B and ROCm omni services; real Qwen and GLM tool calls were gated. |
| HTTP serving adapter, d1-3B | Real model load and text/image/batch/tool workflows passed on macOS 26.5 arm64, Python 3.14.8, MPS float16, revision `051bcc464b01b9f92942b364d9586b0ef5912432`. Answer shapes matched the fleet contract; image plus audio returned 422. |
| HTTP serving adapter, omni | Offline batch/audio dispatch and decoding tests; no local omni model load. Existing ROCm omni lane remains live-tested. |
| Dependency installation | Core, MCP and optional serving extras installed in separate macOS arm64 / Python 3.14.8 virtual environments. |
| Download script | Offline revision/manifest tests; the pinned d1-3B snapshot downloaded during IP-001 was reused for IP-002. No additional downloads. |
| Other clean-machine/platform installations | Instructions provided; not claimed tested. |

The optional environment installed Transformers 5.19.0, PyTorch 2.14.1, torchvision 0.29.1, Hugging Face Hub 2.2.0, Pillow 12.3.0 and soundfile 0.14.0. Exact declared versions are in [pyproject.toml](../pyproject.toml). Existing lane performance is recorded in the [completion report](c-completion_reports/CR-001_Fluid_Decisions.md); it is not a universal hardware requirement or throughput guarantee.

The [IP-002 adapter run](self-hosted-run.json) loaded in 22.656 seconds and measured a 130.7 ms warm request. The [workflow smoke](self-hosted-smoke.json) and [contract probe](self-hosted-contract.json) passed. This used the current checkout and an existing optional-dependency environment, with Hugging Face offline mode enabled. It validates the adapter on this platform; it does not claim a fresh-machine installation. The incomplete local omni snapshot was not downloaded or served. Fleet service files and process identities were unchanged, and port 8776 was closed at completion.
