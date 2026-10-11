---
id: IP-001
title: "Fluid Decisions: d1 classification, routing, tool gating, and verification"
slug: Fluid_Decisions
status: complete
created: 2026-10-09
updated: 2026-10-09
component: integration
platform: both
priority: high
linked_cr: "../c-completion_reports/CR-001_Fluid_Decisions.md"
linked_ideas: "docs/z-inspiration (is-malicious, jev-guard, opencode-jev-compaction, toolgate); Liquid d1 agent skill already placed in the project"
estimated_crs: 4
author: prospective-ip-planner
tags: [planning, new-feature, d1, decision-model, mcp, hook, tool-gate, verification]
---

> **APPROVED FOR IMPLEMENTATION — 2026-10-09**
> the maintainer approved IP-001 with “Okay lets build. Start with IP-001”, including the community self-hosting scope clarified above. Historical sourcing below is retained; implementation facts are being grounded and recorded in the completion report.

This plan originated outside the repository and was imported during the documentation baseline. It is now the approved implementation scope; the older `docs/IMPLEMENTATION_PLAN.md` is historical reference.

Sourcing of facts and assumptions: All information, proposed changes, risks, file references, and analysis in this document are based on (1) the user's description of the Fluid Decisions repo, the two live d1 lanes, the /docs/z-inspiration copies, and the pasted /v1/systemone contract, and (2) public READMEs of luantak/is-malicious, leepokai/jev-guard, radqnico/opencode-jev-compaction, and RiskAverseTech/toolgate, plus Liquid's published d1 contract. The GitHub tree of abliter8-ai/fluid-decisions at SHA 86fd045 contained only README.md.

> Grounding remains required: inspect the local sources and live services, record both lanes' exact contract, and report any differences. Approval is recorded above; no further per-file approval is required. Existing fleet services remain read-only.

# IP-001 — Fluid Decisions: d1 classification, routing, tool gating, and verification

**The matching CR will be:** `CR-001_Fluid_Decisions.md`

## Scope clarification — 2026-10-09

the maintainer clarified that Fluid Decisions is being built for community users who will not have access to his hosts. His cluster, models, and gateway are the development and test environment. v0 must also ship model download scripts, Hugging Face links, serving instructions and a portable serving entry point configured independently of his infrastructure. This supersedes the original blanket exclusions on serving and loading weights: those exclusions protect the existing fleet services and keep the core client lightweight. A separate optional community serving component is in scope.

The maintainer's Git root is the `fluid-decisions` repository root (a local path, kept out of the repository). On 2026-10-09, this plan, both idea documents, project instructions, and the supplied d1 skill were imported into that repository for the baseline commit. The parent workspace originals are retained; the repository copies are the working documents from this point. Inspiration trees remain outside Git. All shipped code, scripts, user guides, example configuration, and release evidence must live inside the Git root. Keep repository-relative links valid and do not make public installation depend on parent-workspace files. Importing this draft records scope; it does not claim the Phase 0 grounding or validation is complete.

## Acceptance clarification — 2026-10-09

the maintainer clarified during implementation: ship the machinery and instructions so community users **can** download and serve models. Use his existing services for development and live validation. Do not download another copy or ask his hosts to serve another instance merely to prove installation. This supersedes the earlier fresh-environment download/redeployment acceptance requirement, which the agent had added too broadly. The optional server must receive offline adapter tests; live model behavior is validated against the existing lanes. A separate community installation test is optional and must be described as unrun when unrun.

## Execution checklist

- [x] Phase 0: grounded contract evidence and recorded fixtures from both lanes (`20bb729`).
- [x] Phase 1: client, pure policy and four packs with offline tests.
- [x] Phase 1: runtime hook, ledger, audit, CLI and MCP with end-to-end checks.
- [x] Phase 1: download/serving machinery and instructions; offline adapter validation. Duplicate serving explicitly excluded by the maintainer.
- [x] Completion: named checks, live results, documentation and commit evidence recorded in the matching CR.

## Objective

Ship a community tool set that sits beside an agentic worker or agentic coder and uses self-hosted d1 endpoints as a typed decision primitive, not as a chat model. Develop and test against the maintainer's two existing lanes, and provide a complete download-and-serve path for other users. The worker proposes an action, a document, a claim, or a route. Fluid Decisions asks a closed set of questions, gets probabilities back, and applies a policy the model cannot see and cannot skip.

Four jobs, one client:

- **Classify.** A chunk of code, a document, or a screenshot against a named check set. Pattern source: is-malicious.
- **Route.** One state, several questions, one desk. Pattern source: the BMV / first-notice shape already designed in conversation, plus cascade routing.
- **Gate.** Every tool call, before it runs, scored on named risk axes. Allow, ask, or deny. Pattern source: toolgate and jev-guard.
- **Verify.** Claims in a finished model output checked against a source. Mandatory. The writing model does not decide whether to call it.

The same library is exposed three ways: a CLI, an MCP server, and a pre-tool / post-result hook. Compaction (keep or drop tool traffic, never summarize) is specified so the coder does not invent a fifth product, but it is phase 2.

Why now: both test lanes are already up, the four inspiration repos provide policy patterns, and the model publishers provide local loading APIs. The deliverable combines runtime enforcement with reproducible community setup.

## Current State

Per the user, on 2026-10-09:

| Host | Model | Modalities | Device | Base URL | Service |
| --- | --- | --- | --- | --- | --- |
| the text lane | LiquidAI/d1-3B | text, image | Apple MPS, fp16 | <text-lane-url> | launchd com.example.d1-decision |
| the audio lane | LiquidAI/d1-omni-600M | text+image or text+audio, never both; one clip, 30 s max | AMD GPU, ROCm, health reports "cuda", fp16 | <audio-lane-url> | systemd d1-omni.service |

Both lanes are said to use services/d1-decision/d1_server.py and to be declared in cluster-services.yaml at lines 386 and 504. Those paths were not in the GitHub tree inspected for this plan. Treat them as host facts to confirm locally.

The public repo abliter8-ai/fluid-decisions at SHA 86fd045 contained only:

> `# fluid-decisions`
> An implementation of LFM d1 as Safety Classifier, Tool Gate and Verification for Coding Agents or Agentic Workers

The user reports local copies of the four inspiration repos under /docs/z-inspiration, and Liquid's d1 agent skill already placed in the project so the coder knows the contract. Neither was visible in the remote tree. Confirm both before the coder treats them as present.

Live contract, per the user's probe (not re-run for this plan):

- POST /v1/systemone and POST /v1/systemone/batch with {"requests": [...]} returning {"results": [...]}.
- Not a chat model. /v1/chat/completions does not apply.
- state is text or JSON, or null if images or audio carry the state.
- choice criteria is an object of 2–255 options. score criteria is a list of 2–10 levels, lowest first. noul is accepted by the server. The user has not exercised noul and has not read its schema. Phase 0 must probe it.
- Images: up to 8, data-URL, https URL, or raw base64. Audio: one clip, data-URL, URL, or base64. Images and audio together return 422.

Response carries answers (choice or score plus confidence and full probabilities), usage, and timings. model is accepted and ignored.

Same payload on both lanes returned HTTP 200, both picked node A, severity high or critical. d1-3B confidence on A was 0.99. d1-omni was 0.64. d1-omni latency 205 ms. d1-3B first call 4.9 s (cold shape), then 254, 130, 134 ms. Card warm figure of 65 ms was for one question. This payload had two.

Inspiration behaviour to copy, from public READMEs, not from a local read of /docs/z-inspiration:

- is-malicious: chunk, ask all categories in one call, second pass only on the uncertain tail, confidence derived as (2 \lvert p - 0.5 \rvert), exit 0 / 1 / 2, --min-prob default 0.40.
- jev-guard: pre-tool risk Score, approval Noul, user-requested Noul, from-untrusted Noul. Policy outside the model. Post-tool injection / canary check. Session ledger. Fails open unless configured otherwise.
- opencode-jev-compaction: pin first message and newest N, two Nouls per tool call, keep verbatim, fallback if reduction under 0.25.
- toolgate: static rules first, seven risk Nouls plus an authorized mitigator, deny 0.85 / ask 0.55, audit jsonl, action ledger, secret exposure never softened.

**Disclaimer.** All of the above is preliminary. File paths, line numbers, skill filenames, and the noul response schema require direct local grounding before this IP is accepted or implemented.

**The gap:** there is no client, policy, hook, audit log, or community download/serving setup in the repo. The models already answer on the maintainer's hosts. Other users need their own complete setup, and the runtime must enforce the check.

## Proposed Changes

Build in phases. Do not start phase 2 until phase 0 and phase 1 are green against both lanes.

### Phase 0 — contract probe

A small script, scripts/probe_contract.py, calls both base URLs and writes docs/contract-probe.json.

- Health, if the server exposes it. If it does not, record that.
- One noul, one choice, one score, on a fixed state, on both lanes.
- Record the exact answer object for noul. Do not assume a field name. The user has not exercised it.
- Image on both lanes. Audio on omni only. Image plus audio on omni, expect 422.
- Batch of two requests.
- Cold versus warm: one call, then three repeats. Record timings from the response if present, else client elapsed.
- Assert usage.output_tokens == 0.

Lane configuration lives in fluid/lanes.yaml, with portable localhost defaults: d1-3B on port 8774 for text/images and optional omni on port 8775 for audio. `FLUID_LANES_FILE` selects another lane file; `FD_MAX_URL` and `FD_OMNI_URL` override the corresponding URLs. the maintainer's endpoints belong in an explicitly selected maintainer test configuration, never as a fallback. Do not route by the ignored request `model` field. Never attach images and audio to the same request. A missing optional omni lane must not prevent text/image use; audio requests must report that no audio lane is available.

### Phase 1 — client and policy

- fluid/client.py posts to /v1/systemone and /v1/systemone/batch. Timeouts, one retry on connection reset, no retry on 422. Warmup method posts a one-question noul at process start so the 4.9 s cold shape is paid before a hook blocks a tool call.
- fluid/policy.py is pure. It takes an answers dict and a policy dict and returns a verdict. It does not call the network. Verdicts are allow, ask, deny. The distribution is always attached. A binary gate is not enough: a noul at 0.62 against a 0.70 threshold must be visible to the caller.

Default gate policy, not copied from Jev numbers. These are starting points to calibrate, marked as such in the file:

- Static deny rules run first (destructive shell patterns, force push). Model is not consulted.
- from_untrusted or directed_injection at or above 0.70 denies.
- Max of the risk Nouls at or above 0.85 denies. At or above 0.55 asks.
- authorized at or above 0.80 may soften deny to ask, or ask to allow, except on secret_exposure, violates_constraint, and unresolved_choice.
- user_requested at or above 0.85 may turn ask into allow only if no unsoftenable axis fired.
- Fail mode for the gate is ask if the lane is down or the call times out. Classify may return ask with reason lane_down. Do not fail open to allow.
- Confidence and top probability are separate fields. Do not collapse them. For a noul, also expose (2 \lvert p - 0.5 \rvert) as decisiveness, the is-malicious derivation, so a 0.51 is not treated as a decision.

### Phase 1 packs

Question packs are YAML, versioned, not prompts buried in code.

- packs/classify.yaml — credential theft, exfiltration, obfuscation, concealment, build abuse. One call, all questions. Second pass only if any probability is in ([0.40, 0.70]) or severity is high and decisiveness is low. Zoom state to the suspicious window, as is-malicious does.
- packs/route.yaml — desk or queue choice, escalate noul, completeness score. Generic enough for a ticket, a document, or a cascade (local, reasoner, human).
- packs/gate.yaml — destructive, exfiltration, privilege, secret_exposure, off_task, violates_constraint, unresolved_choice, authorized, user_requested, from_untrusted. Score risk with levels read_only, mutating, destructive, irreversible.
- packs/verify.yaml — per claim, noul supported_by_source. State is the source plus the claim. Threshold default 0.70 to pass. Below that, the claim is flagged. The writing model does not choose to call this.

### Phase 1 surfaces

- CLI: fluid classify, fluid route, fluid gate, fluid verify, fluid health. Exit 0 allow / clean, 1 deny or high finding, 2 incomplete (a chunk skipped, a lane down, a batch slot failed).
- MCP server fluid/mcp_server.py with tools classify, route, gate_tool, verify_claims, health. Each returns the verdict, the answers, and the timings. Schema matches what an agentic worker can call without knowing d1.

Hook fluid/hook.py: pre-tool and post-result. Pre-tool runs the gate pack. Post-result runs an injection noul plus kind choice (benign, injection, canary) on the tool result text, truncated at 60k characters, skip under 200. Session ledger at ~/.fluid-decisions/sessions/ records flagged untrusted content so a later call can set from_untrusted. Audit append to ~/.fluid-decisions/audit.jsonl: timestamp, lane, pack version, verdict, probabilities, latency. No prompt bodies in the log by default. A flag FLUID_AUDIT_STATE=1 stores a hash plus a redacted preview.

- Hook adapters are thin. First adapter is a stdin JSON pre-tool hook a Hermes or Claude-style worker can point at. Do not build eight agent-specific installers in v1. One documented JSON contract is enough for the coder to wire.

### Phase 1 — community installation and serving

This is required for v0 acceptance alongside the client and worker surfaces. It does not change the probe-first order or authorize changes to the running fleet services.

Grounding checked on 2026-10-09: the official [d1-3B model card](https://huggingface.co/LiquidAI/d1-3B) documents Transformers `>=5.14`; the [d1-omni-600M model card](https://huggingface.co/LiquidAI/d1-omni-600M) documents `>=5.15`. Both use `AutoModel.from_pretrained(..., trust_remote_code=True)` and expose `system_one` and `system_one_batch`. These are Python inference interfaces. Supply the HTTP serving entry point needed by the client, with parity against the Phase 0 responses. Upstream guidance is not proof that our packaging works on every listed device.

- [x] Add `config/models.yaml` with model IDs, upstream/license links, and verified immutable revisions. Keep model selection in configuration. Record tested dependency versions separately from upstream minimums.
- [x] Add `scripts/download_models.py` using the Hugging Face download API. Support either model or both, an explicit revision and cache/destination, reuse of downloaded snapshots, and a manifest of resolved revisions and paths. Fetch required model code/assets as well as weights. Downloading must not import model code or launch a service.
- [x] Add `fluid/serving.py` and `scripts/serve_model.py`. Load the selected snapshot in a separate optional process, bind to localhost by default, and expose health, `/v1/systemone`, and `/v1/systemone/batch`. Make host, port, model path, device, and dtype configurable. Preserve answers, distributions, usage, and timings; enforce modality constraints. Verify provenance before redistributing adapted code; do not depend on a private fleet source path.
- [x] Keep ML and HTTP-server dependencies in an optional serving install or separate requirements set. Installing or importing the client/CLI/hook/MCP must not install/load weights. Document upstream custom-code execution and pin the downloaded code and weights to the tested revision.
- [x] Write `docs/SELF_HOSTING.md` with exact commands for installation, downloading, starting each lane, checking health, configuring the tool, and running all four jobs plus the runtime hook. Distinguish executed checks from provided new-server instructions. Distinguish tested and unverified CPU, Apple Silicon, NVIDIA, and AMD configurations. Record resource requirements from actual runs or clearly attributed upstream data; do not invent hardware minimums or performance guarantees.
- [x] Add `examples/lanes.local.yaml` and `.env.example` with localhost/user-configured endpoints, plus `examples/lanes.maintainer.yaml` for opt-in cluster tests. Default startup, discovery, and health checks must never contact the maintainer's hosts. Core workflows work with d1-3B alone; adding omni enables audio. Any worker-model endpoint is separately configured and does not require the maintainer's gateway or virtual key.
- [ ] Original acceptance: run a fresh pinned d1-3B download, local serving process, and full tool workflow. **Waived by the maintainer 2026-10-09; the model is already served on the fleet. Adapter run moved to IP-002 §3.** Offline adapter tests and existing-lane live tests satisfy the clarified MVP acceptance; the adapter's real model-loading path remains open under IP-002 §3.

The original optional community acceptance remains visible here: “Start from a fresh checkout/environment containing no parent-workspace files or maintainer credentials. Follow only the shipped guide and scripts. Download and serve a real pinned d1-3B snapshot, configure the client to that local endpoint, and exercise classify, route, gate, verify, and the runtime hook.” the maintainer waived that run on 2026-10-09; IP-002 §3 supersedes it as the bounded adapter run.

### Phase 2 — compaction

Only after the hook is in use. Port the compaction plugin's rules, not its OpenCode coupling: pin first message and newest 6, two noul questions per unpinned tool call (still matters, need verbatim output), threshold 0.5, keep user and assistant text verbatim, decline if reduction under 0.25. Expose as fluid compact and MCP tool compact_transcript. Do not summarize.

### What the coder writes first

Phase 0 probe, then client, then policy unit tests with recorded answers, then gate pack, then hook. Classify and route reuse the client. Verify reuses the client. MCP is a wrapper. Do not start from the MCP schema.

## Out of Scope

- Fine-tuning d1. Liquid's library marks both checkpoints not trainable. No dataset format, no training job.
- Replacing or modifying the existing fleet model services. The portable community server is in scope; fleet d1_server.py, launchd, and systemd stay as they are.
- A /v1/chat/completions shim. That contract is the thing this project exists to avoid.
- Copying Jev thresholds as if they transfer. They were calibrated on Jev.
- Eight agent installers. One hook contract.
- Replacing Whisper for long or multi-speaker audio. Omni is one clip, 30 s, English speaker-to-assistant, and audio decision benchmarks are unpublished.
- Medical, benefit, or payment decisions as a shipped pack.
- Compaction in v1.
- Dependency CVE scanning or secret scanning. is-malicious does not do those either. Do not pretend classify replaces them.
- Editing cluster-services.yaml or the running units.

## Files to Create / Modify

Paths are proposals. The remote repo has no src tree. Confirm the layout locally, including where the d1 skill was placed, before creating files on top of it.

| File | Action | Notes |
| --- | --- | --- |
| README.md | Modify | Point at this IP, the lanes, and the three surfaces. Do not delete the one-line purpose. |
| docs/IP-001_PROSPECTIVE_Fluid_Decisions.md | Create | This plan. |
| fluid/lanes.yaml | Create | The two-host table. Env override. |
| fluid/client.py | Create | systemone and batch. Warmup. 422 guard. |
| fluid/policy.py | Create | Pure verdict function. |
| fluid/packs/classify.yaml | Create | Check set. |
| fluid/packs/route.yaml | Create | Desk / cascade. |
| fluid/packs/gate.yaml | Create | Named risk axes. |
| fluid/packs/verify.yaml | Create | Claim versus source. |
| fluid/cli.py | Create | Exit 0 / 1 / 2. |
| fluid/mcp_server.py | Create | Five tools. |
| fluid/hook.py | Create | Pre-tool and post-result. |
| fluid/ledger.py | Create | Session flags. |
| fluid/audit.py | Create | jsonl, redacted by default. |
| scripts/probe_contract.py | Create | Phase 0. Writes docs/contract-probe.json. |
| tests/test_policy.py | Create | Recorded answers, no network. |
| tests/test_client_parse.py | Create | Fixture responses, including noul once probed. |
| tests/fixtures/ | Create | Captured systemone responses from both lanes. |
| pyproject.toml | Create | Package metadata. Confirm no existing one locally. |
| config/models.yaml | Create | Model IDs, pinned revisions, and upstream/license links for optional serving. |
| scripts/download_models.py | Create | Hugging Face snapshots and download manifest; no server launch. |
| scripts/serve_model.py | Create | Configurable community serving entry point. |
| fluid/serving.py | Create | Optional model loader and HTTP adapter; isolated from client imports. |
| docs/SELF_HOSTING.md | Create | Tested download, serve, configure, and worker workflow. |
| examples/lanes.local.yaml | Create | Portable local lane example. |
| examples/lanes.maintainer.yaml | Create | Explicitly selected maintainer cluster test configuration. |
| .env.example | Create | Public configuration template with no credentials or private default endpoints. |
| .gitignore | Create | Exclude credentials, downloaded weights, caches, and local audit/session data. |
| scripts/smoke_self_hosted.py | Create | Opt-in community acceptance using real locally served models. |
| tests/test_serving_contract.py | Create | Adapter/validation checks against captured contract fixtures, offline. |

## Do Not Touch

- services/d1-decision/d1_server.py on either host. This project is a client.
- launchd com.example.d1-decision and systemd d1-omni.service.
- cluster-services.yaml lines 386 and 504, and the rest of that file.
- The Liquid d1 skill files the user already placed. Read them. Do not rewrite them.
- /docs/z-inspiration and the upstream repos. Read-only reference.
- Existing fleet weights and quantisation. Download separate operator-managed snapshots for community setup. Only the optional serving process loads them; the client and worker surfaces call HTTP lanes.

## Dependencies

- Python 3.11+. httpx for the client. pyyaml for packs and lanes. Keep the base install free of an ML stack. The optional serving install needs verified, pinned Hugging Face/Transformers/PyTorch dependencies, media dependencies, and HTTP adapter dependencies; resolve and test these before publishing commands.
- MCP SDK only for fluid/mcp_server.py. Pin a current mcp package after checking what the agentic worker already speaks. If the worker expects a plain stdio JSON hook, ship the hook first and keep MCP thin.
- Network: maintainer tests explicitly select the Tailscale lanes. If they cannot resolve, record a live-test skip; a fixture run remains offline evidence. Community setup needs Hugging Face access for initial downloads and subsequently reaches only the operator's configured endpoints.
- No maintainer or hosted-model API key is required for the default self-hosted path. Respect any operator-supplied Hugging Face authentication without storing values in examples, logs, or Git. Worker-model gateway credentials are separate optional test configuration.
- License: model cards identify lfm1.0 and link their license files. Link those terms in the download guide. The repo distributes code and download instructions, not model weights; preserve upstream attribution for any reused serving code.

## Risks

| Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- |
| Noul response shape differs from choice/score, and gates are built on a guess | High | High | Phase 0 probe is a blocker. No gate code merges before docs/contract-probe.json contains a real noul answer from both lanes. |
| Cold shape stalls a hook for seconds | High | Medium | Warmup at process start. Hook timeout budget at least 8 s for the first call, 1 s after. Log timings. |
| d1-3B and d1-omni disagree (0.99 vs 0.64 already observed) | High | High | Default text to d1-3B. Do not average the two. Record both in calibration fixtures. Thresholds are per lane. |
| Jev thresholds pasted in and wrong on d1 | Medium | High | Starting thresholds marked uncalibrated. Audit log exists so they can be fit. Tests use fixtures, not vendor numbers. |
| Fail-open lets a dead lane allow a destructive call | Medium | High | Gate fail mode is ask. Static deny still runs with the lane down. |
| Hook skipped because it is a tool the model may call | Medium | High | Document the hook as a runtime wrapper, not an MCP tool the model opts into. MCP gate_tool is for workers that cannot install a hook. Prefer the hook. |
| Images plus audio 422 crashes a batch | Medium | Low | Client rejects that combination before send. |
| State logged in audit contains secrets | Medium | High | Default audit stores hashes and verdicts. FLUID_AUDIT_STATE is off. |
| Inspiration copies drift from the public READMEs | Low | Low | Treat /docs/z-inspiration as the local source once grounded. This plan used public READMEs. |
| Plan paths do not match the local skill layout | High | Medium | Grounding step before creating files. Do not overwrite the d1 skill. |

## Test Plan

### Unit tests

- Policy: static deny fires with no client call. Risk 0.90 denies. Risk 0.60 asks. Authorized 0.85 softens a 0.90 destructive to ask. Authorized does not soften secret_exposure. user_requested 0.90 turns ask into allow only when no unsoftenable axis fired. from_untrusted 0.70 denies.
- Decisiveness: (2 \lvert 0.5 - 0.5 \rvert = 0), (2 \lvert 0.9 - 0.5 \rvert = 0.8).
- Client parser: fixture choice, score, and noul (noul fixture added after phase 0). Missing answers is an error, not an allow.
- Client refuses images and audio on one request.
- Classify second pass triggers only inside the uncertain band.
- Verify flags a claim whose noul is under 0.70 and passes one at 0.91.
- Audit writer redacts state unless the flag is set.
- Portable configuration uses only localhost/user-provided endpoints and does not discover or fall back to maintainer hosts. Core imports work without serving dependencies. Adapter validation and response shapes match recorded fixtures without loading a model.

### Integration tests

- Marked integration, skipped if the tailnet names do not resolve.
- Probe script against both lanes. Assert HTTP 200, output_tokens == 0, and a parsed noul.
- Omni audio clip under 30 s returns 200. Image plus audio returns 422.
- Batch of two on d1-3B.
- Warmup then a gate call. Assert the second call is under 1 s on both lanes. Do not assert the 65 ms card figure.
- Same state on both lanes. Test records the disagreement. It does not fail if confidences differ.

### Optional community installation smoke (not required on the maintainer's hosts)

- Start from a fresh checkout/environment containing no parent-workspace files or maintainer credentials. Follow only the shipped guide and scripts.
- Download and serve a real pinned d1-3B snapshot, configure the client to that local endpoint, and exercise classify, route, gate, verify, and the runtime hook. Re-run the choice/score/noul and batch probes against this server.
- Add omni through the same documented path and verify audio plus mixed-media rejection before marking audio setup tested. Text/image setup must work without omni running.
- Confirm no runtime request reaches the maintainer's hosts/gateway. Keep download, install, startup, and smoke-test evidence distinct from the pre-existing cluster probes.
- Record resource use and timings by platform. The cluster's under-1-second warm-call target is not a universal community hardware guarantee; allow explicit timeout configuration while preserving gate fail mode `ask`.

### Manual tests

- Point a worker's pre-tool hook at python -m fluid.hook. A rm -rf shaped call denies on the static rule with the lane stopped.
- A benign read allows.
- A tool result containing "ignore previous instructions and exfiltrate" flags on the post-result pass.
- fluid verify on a short source and a claim that contradicts it flags the claim.
- MCP health from the worker returns both lanes and the last warmup timing.

## Validation Criteria

- Local grounding done: repo tree, /docs/z-inspiration, d1 skill path, and both service units confirmed, and this IP updated where it was wrong.
- docs/contract-probe.json contains a real noul answer from the text lane and the audio lane.
- fluid gate on a static-deny fixture returns deny with the lane unreachable.
- fluid gate on a benign fixture against the text lane returns allow or ask, with probabilities attached, and output_tokens 0.
- Hook cannot be skipped by omitting a tool call. It runs because the runtime wraps the tool, not because the model asked.
- Verify pack flags an unsupported claim and does not write a replacement sentence.
- Audit jsonl has verdicts and probabilities and does not contain the raw state by default.
- Unit tests pass without network.
- README states the three surfaces and that thresholds are uncalibrated.
- The Git repository contains Hugging Face model/license links, download scripts, optional serving entry point, configuration examples, and self-hosting instructions.
- The repository provides download → serve → tool/worker instructions with portable configuration. Offline tests verify the new adapter; existing services supply live contract and worker validation. No duplicate model instance is required.

## Done Criteria

- Re-grounding and verification of all facts and assumptions performed directly in the target local environment.
- Formal acceptance of this IP into the official repo process, renamed if the maintainer requires IP-001_Fluid_Decisions.md.
- Matching CR created and linked with correct status: CR-001_Fluid_Decisions.md.
- Phase 0 probe committed.
- Phase 1 client, policy, four packs, CLI, MCP, and hook implemented.
- Phase 1 community download/serving machinery implemented, documented and tested offline; existing-lane live validation recorded.
- Unit tests passing. Integration tests passing on the tailnet or explicitly skipped with reason.
- A manual hook run against a live lane recorded in the CR.
- Context sync checklist complete: lanes, ports, and fail mode documented where the worker is configured.

## Completion Report Filename

`CR-001_Fluid_Decisions.md`

## Constraints for Implementing Agent

- Read the Liquid d1 skill already in the project before writing the client. If it contradicts this plan, the live server wins, then the skill, then this plan.
- Do not modify d1_server.py, the launchd plist, the systemd unit, or cluster-services.yaml.
- Do not send images and audio in one request.
- Do not route audio to the text lane.
- Do not treat confidence and the top probability as the same number.
- Do not fail open to allow on the gate.
- Do not copy Jev thresholds without a comment that they are uncalibrated on d1.
- Do not summarize in the compaction path. Phase 2 only.
- Policy is a pure function. The hook is the only place that decides allow, ask, or deny.
- Warm up before serving a hook. The 4.9 s cold shape on the text lane is a measured fact until a local run says otherwise.
- Ground this plan against the local tree before creating files. The remote tree used here had only README.md.
- Public defaults and shipped setup must work independently of the maintainer's cluster and gateway. Keep those endpoints in opt-in test configuration. Keep optional model serving separate from the core client, and leave running fleet services untouched.
