---
id: CR-001
title: Fluid Decisions MVP
slug: Fluid_Decisions
status: complete
date: 2026-10-09
linked_ip: ../b-implementation_plans/IP-001-KICKOFF.md
implementation_commit: 0d19bc9
phase_0_commit: 20bb729
testing: true
context_updated: true
deployment: existing_services_unchanged
---

# IP-001 completion

Implemented the four jobs—classify, route, gate and verify—through a lightweight Python client, CLI, generic stdin hook, runtime wrapper and MCP server. Policies and question packs are versioned YAML. Static denies precede any model call; endpoint and validation failures return `ask`. The runtime executes only an allowed, complete verdict and withholds tool results flagged by the post-result check.

Implementation commit: **`0d19bc9`**. Contract-probe commit: **`20bb729`**. This report records the final scope clarified by the maintainer: build download/serving machinery and instructions for community users; validate live against his existing services. Do not ask his hosts to load a duplicate model.

## Delivered

- Strict System One single/batch client, media routing, first/warm timeout budgets, bounded disconnect retry, full distributions and distinct decisiveness/confidence fields.
- Pure policies for four packs, static command denials, uncertainty-driven classification windows, source-only claim verification and incomplete-route handling.
- `GuardedRuntime`, pre-tool/post-result stdin hook, five MCP tools, session flags and audit JSONL. Default logs exclude raw state. Opt-in state recording adds only a hash, byte count and fully redacted preview.
- Public localhost defaults, environment overrides, explicit maintainer test configuration and separate worker-model credentials.
- Pinned Hugging Face model configuration, resumable download script with revision/path manifests, separate optional HTTP serving adapter and [self-hosting guide](../SELF_HOSTING.md).
- Updated README, [usage guide](../USAGE.md), runtime example and agent instructions. IDEA-001/002 remain backlog; no compaction or extra product packs were added.

## Evidence

| Check | Result |
| --- | --- |
| Ruff | `ruff check fluid scripts tests examples` passed. |
| Mypy | `mypy fluid` passed across ten source files. |
| Offline suite | **65 passed, 2 skipped**; the two skipped tests are explicitly opt-in live tests. Includes real MCP stdio initialization/tool discovery/static denial. |
| Live test module | **2 passed** against the explicitly selected existing max and omni endpoints. |
| Package build | Wheel and source distribution built successfully. |
| Installed wheel | Imported from outside the checkout; four packaged packs and model config loaded. Client/service import did not import torch or Transformers. |
| Serving adapter | Offline validation, single/batch envelope and audio tuple dispatch tests passed with injected models. Separate real audio decoding check passed: stereo 8 kHz to mono 16 kHz float32; empty and 31-second clips rejected. No model loaded. |
| Credentials | Maintainer credential values absent from tracked/proposed files. Weights, environments and model cache remain ignored. |

The exact named commands are in [AGENTS.md](../../AGENTS.md#named-checks). The optional serving dependencies were installed in a separate virtual environment on macOS arm64 / Python 3.14.8. This verifies dependency installation, not new-model execution.

### Existing decision lanes

[Contract evidence](../contract-probe.json) records both live endpoints and their actual revisions, exact answer objects, HTTP results and latency. Both returned choice, score and noul with `output_tokens: 0`; image input and two-item batches passed on both. Omni accepted one second of 16 kHz audio and rejected mixed image/audio with HTTP 422. Max audio was not sent.

The actual noul object is `{"type":"noul","noul":p}`. It has no model-provided confidence field. The shared question scored 0.503906 on max versus 0.127079 on omni. These remain separate results, never averaged. The supplied hosted-API skill differs from the local multimodal envelope; it was left untouched and the recorded contract governs this client.

[Workflow evidence](../lane-workflow-smoke.json) records:

- Benign read: `allow`, full probabilities attached, zero output tokens.
- Benign classification: `allow`; routing fixture: `ask` because the completeness policy found missing information, with destination probabilities retained.
- Contradictory Sunday-opening claim: flagged; no replacement sentence generated.
- Directed injection in tool output: flagged and denied; session state retained without the raw output.
- Second repeated gate calls: **355.7 ms max**, **206.0 ms omni**, client wall time. Both satisfy the measured under-one-second warm-call criterion; this is not a universal hardware guarantee.
- Real stdin pre-tool hook: `allow`, exit 0, all twelve answers attached.
- MCP health: both existing lanes loaded; last warmup **141.9 ms**.

The existing max launchd label was inspected read-only. The omni unit reported active/running at `/etc/systemd/system/d1-omni.service`. Their source/registry were read for grounding; no fleet configuration or lifecycle was changed.

### Generative worker integration

[Worker evidence](../worker-smoke.json) records real HTTP 200 responses from `Abliter8/Qwen-3.8-FlashNext` and `Abliter8/GLM-5.3-Flash-NoThink`. Both produced a `read_file README.md` tool call and finished with `tool_calls`. The runtime invoked the real d1 gate before executing a harmless public fixture; the worker did not request a gate tool. A synthetic `rm -rf` proposal was denied with execution false.

The worker harness allows 8,192 output tokens and records finish reason, usage and reasoning tokens. An empty HTTP 200 is distinguished from a transport failure. No OpenAI/Claude generation was used. Initial local coding-agent request-format/allowlist errors occurred before generation; built-in workers handled implementation after those errors.

## Community setup scope and limits

The download script pins current public revisions: d1-3B `051bcc464b01b9f92942b364d9586b0ef5912432` and omni `02b55d7076f15129e59ab3f94783f32c4b088674`. The existing fleet runs earlier revisions, explicitly captured in the contract report. New revision behavior is not inferred from the fleet measurements.

The agent initially over-expanded community portability into a requirement to repeat model installation. the maintainer corrected that scope. The duplicate download was stopped; **no second model-serving instance was started or loaded**. One pinned d1-3B download had already completed and the omni transfer was interrupted; those extra files remain in ignored `models/`. No model weights were committed or existing files deleted.

The new serving process has offline adapter tests and instructions. Its real model-loading path, and a fresh clean-machine setup on other platforms, are **not claimed live-tested**. The optional smoke script is available to users who already choose to host their own models. This is not an outstanding MVP requirement under the maintainer's clarified acceptance.

Thresholds remain uncalibrated. Static rules are a baseline, not a complete shell interpreter or proof of code safety. The repository still has no selected code licence; upstream model licences are linked separately. No registry release or fleet deployment was performed.

## Context sync

- Matching IP is `complete`, links this CR, and records the corrected acceptance scope.
- AGENTS.md contains actual named checks, the grounded noul contract, the no-duplicate-serving instruction and an evolution entry. CLAUDE.md refers to it as the shared source of truth.
- Public defaults remain localhost; maintainer endpoints require explicit selection.
- No pre-existing known-issues record required a blocker-resolution update.

## IP-002 addendum (2026-10-09)

Independent verification found four hardening findings after IP-001 completion. (1) A single flagged result permanently denied every later call in a session. (2) Results under 200 characters were skipped, leaving a short injection unchecked. (3) The optional serving adapter had only offline validation and had never loaded a real model. (4) The live test made a warm second call but did not assert the one-second budget, and the IP-001 checklist had recorded the serving waiver as completed rather than leaving the original acceptance open. [IP-002](../b-implementation_plans/IP-002-HARDENING.md) addresses these findings; its separately approved bounded adapter run passed and was stopped. The IP-001 serving acceptance remains explicitly unchecked and superseded by IP-002 §3. [CR-002](CR-002_Gate_Hardening.md) records the new implementation, independent checks and adapter evidence separately from the existing fleet evidence above.
