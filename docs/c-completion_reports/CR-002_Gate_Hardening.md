---
id: CR-002
title: "Gate hardening and adapter validation"
slug: Gate_Hardening
status: complete
date: 2026-10-09
linked_ip: ../b-implementation_plans/IP-002-HARDENING.md
implementation_commit: 5a1e485
testing: true
context_updated: true
deployment: temporary_adapter_stopped_fleet_unchanged
---

# IP-002 completion

Implementation commit **`5a1e485`**, on `feat/ip-002-hardening`. Graded taint replaces the permanent session-wide denial, every non-empty result is checked, and the optional serving adapter has passed a real model run. The independent verifier reran the offline suite, both live lanes and the hardening scenarios before this report was marked complete. [Verification record](../ip002-verification.json).

## Behaviour delivered

- **Carry-over protection:** command-bearing fields are compared with hashed eight-word shingles from flagged results before calling the model. Matches deny even when the lane is unavailable. Only hashes and metadata are retained; flagged text is not stored.
- **Recoverable session taint:** the gate sees active event metadata. A mutating-or-worse top operation class has a minimum `ask` verdict; existing denials and unsoftenable risks are preserved. Calls 1–20 after a flag remain active; call 21 or the 60-minute TTL ends active context and the floor. All values live in uncalibrated YAML.
- **Audited clearance:** `fluid session clear --session ID --reason TEXT` appends history and an audit event. It clears active context and the floor. Exact carry-over protection remains for the session's lifetime, including after clearance and expiry.
- **Complete result coverage:** only the empty string skips inference. Canonical injection patterns deny and flag before any network call. Other results use 12,000-character windows with 500-character overlap in one batch; nothing is truncated. Above 1,000,000 characters the result is withheld with `ask`. Model, ledger and audit failure paths remain fail-closed.
- **Visible evidence:** full answer distributions remain available. Audits include window count, first flagged window, static hits, clearance and the dedicated session assessment.

## Requested layer-B measurement

**Same `cat README.md` call, d1-3B: `from_untrusted` = 0.112795 without taint → 0.160266 with active taint; dedicated session-only question = 0.672332.** The independent run reproduced these numbers. [Paired responses and complete synthetic state](../hardening-smoke.json).

The change of 0.047471 is weak evidence that the nested session fact affects action provenance. Following the maintainer's steering, an active gate now batches the complete gate state with a separate question over the session block alone. Its answer appears as `session_assessment` in the result and audit. The session-only probability describes history, not whether the current action was caused by that history; it is not substituted for `from_untrusted` and does not restore a blanket deny. The deterministic carry-over rule and mutating-operation floor remain the enforceable safeguards. One paired synthetic example is not calibration or a general accuracy claim.

## Validation

| Check | Implementer | Independent verifier |
| --- | --- | --- |
| Offline suite, live endpoint environment cleared | 85 passed, 3 skipped | 85 passed, 3 skipped |
| Ruff: `.venv/bin/python -m ruff check fluid scripts tests examples` | Passed | Passed |
| Mypy: `.venv/bin/python -m mypy fluid` | Passed, ten files | Passed, ten files |
| Live module: `.venv/bin/python -m pytest -q -s tests/test_live.py` | 3 passed | 3 passed |
| Warm call, max / omni, default 1,000 ms assertion | 133.8 / 30.2 ms | 210.2 / 53.1 ms |
| IP-002 hardening smoke | All 11 checks passed | All 11 checks passed |
| `.venv/bin/python -m build` | Wheel and source distribution built | Build not repeated |
| Staged credential-value scan and `git diff --check` | Passed | Code and acceptance evidence inspected |

Live tests explicitly selected the existing `the text lane` d1-3B and `the audio lane` omni endpoints; default public endpoints remain localhost. No generative worker model was needed for IP-002. IP-001's Qwen and GLM worker evidence remains separate. Lane probabilities and timings are never averaged.

The reproducible hardening command is `FD_LIVE=1 .venv/bin/python scripts/smoke_hardening.py --max-url URL --output REPORT.json`. It executes synthetic decisions, never the proposed shell commands. Its [recorded run](../hardening-smoke.json) proves:

- An active-taint benign read returns `allow` with answers attached and session context visible.
- The mutation returns `ask` with `session_taint_floor:mutating`; the same mutation returns `allow` after audited CLI clearance.
- The stdin hook includes `session_taint_active` in its reasons.
- Carry-over text is denied with an unreachable lane, including after clearance.
- A 45-character injection is flagged with the lane down.
- A 30,000-character benign result is allowed after all three windows are checked.

The large result took **4,876.4 ms** (independent run **4,846.0 ms**) with the smoke's explicit **30-second timeout**. The normal one-second client budget would fail closed for this observed workload; users checking large results must configure a suitable budget. The under-one-second assertion concerns the small repeated warm-call fixture, not arbitrary payloads. Unit coverage also includes expiry boundaries, preserved history, late-window injection, over-cap withholding and lane failure during post-result checking.

## Bounded serving run

the maintainer's IP-002 approval explicitly covered §3. The already-downloaded snapshot was loaded once on loopback port 8776; Hugging Face and Transformers offline modes were enabled. No download, permanent service, fleet restart or local omni process was performed.

| Detail | Observed result |
| --- | --- |
| Platform | macOS 26.5 arm64; 64 GiB host; Python 3.14.8 |
| Model | d1-3B `051bcc464b01b9f92942b364d9586b0ef5912432`, MPS float16 |
| Dependencies | Transformers 5.19.0; torch 2.14.1; torchvision 0.29.1; huggingface-hub 2.2.0; Pillow 12.3.0; soundfile 0.14.0 |
| Process-start to listening | 22.656 seconds |
| Repeated warm contract request | 130.7 ms |
| Process RSS (`ps`, KiB) | 787,520 after loading; 992,768 after workflows. This excludes separately accounted MPS allocations and is **not total model memory**. |
| Total bounded process lifetime | 36.072 seconds |
| Preconditions | Port initially closed; both existing d1 lanes healthy; `vm_stat` and memory-pressure output captured; oMLX process identity captured. |
| Shutdown | Test process terminated; port 8776 closed; both d1 lanes healthy. Unit/source/registry hashes and max, omni and oMLX process identities unchanged. |

The [workflow smoke](../self-hosted-smoke.json) passed classify, route, gate, verify, post-result, static denial, guarded runtime, batch, image and hook checks. The [contract probe](../self-hosted-contract.json) retained identical typed answer shapes and zero output tokens; mixed image/audio returned HTTP 422. The [run record](../self-hosted-run.json) contains before/after evidence, dependency versions and the exact command. No serving-code repair was needed.

The run used checkout imports and the existing `.venv-selfhost` optional dependencies. It proves this adapter on this platform, not a fresh-machine installation. Local omni was skipped because its snapshot is incomplete and additional download was outside scope. The Python resource tracker emitted a semaphore-cleanup warning on shutdown; the child exited and the port closed. The independent verifier inspected the adapter evidence without loading another copy.

## Context and limits

README, self-hosting and usage guides now reflect the tested adapter and hardening behaviour. AGENTS records the completed, bounded serving exception. IP-001 retains its original serving acceptance text as explicitly unchecked and waived, with adapter validation moved to IP-002; CR-001 links the four verification findings to this report.

All thresholds remain uncalibrated. Shingles cover exact normalized carry-over, not paraphrases; regexes are a baseline, not a complete detector. No new pack, compaction, fine-tuning, fleet deployment or registry release was added. New idea documents created concurrently were left untouched. The independent verifier found no in-scope runtime failures; its documentation suggestions were accepted and incorporated.

## Second verification (designated verifier session, 2026-10-09, commit `545f3e0`)

Reran independently with the network environment cleared for the offline suite and the maintainer lanes explicitly selected for live checks: ruff and mypy clean; 85 passed, 3 skipped; live module 3 passed (max warm 130.7 ms, omni 32.4 ms); `scripts/smoke_hardening.py` all 11 checks passed; layer B reproduced at `from_untrusted` 0.113 without taint, 0.160 with taint, session-only question 0.672. Port 8776 closed, no serving process left, both fleet lanes healthy, launchd unit and fleet server source unchanged by mtime and by the hashes in `self-hosted-run.json`. No flagged text in the audit or ledger records. No secrets in tracked files. No new thresholds in Python.

Two findings outside the plan's acceptance list, both measured against the d1-3B host under the shipped defaults (`FD_FIRST_TIMEOUT=8`, `FD_TIMEOUT=1`):

1. **Default budget withholds ordinary tool results.** Post-result wall time was 258 ms at 1,000 characters, 663 ms at 3,000, 1,001 ms at 6,000, and a client timeout at 12,000 and above. A guarded 12,000-character `read_file` executed, then had its result withheld with `timeout`. IP-002 made every result checked, which is correct, but it means the hook as configured returns nothing to the agent for any read above roughly 5,000 characters. The CR notes this; the shipped defaults and `.env.example` do not. This needs a per-job budget (post-result budget scaled by window count, or a separate `FD_POST_TIMEOUT`) or a different default, and it is the first item for the next plan.
2. **Abandoned requests cascade.** A client timeout does not cancel the server's work; the fleet server serialises inference. A 24,000-character post-result that timed out client-side left the lane busy, and the next benign gate on a different session timed out and returned `ask`. After the lane drained the same gate returned `allow` in 385 ms. Fail-closed, so safe, but one oversized result degrades every session sharing the lane for several seconds. Mitigations to plan: do not send a post-result batch whose expected cost exceeds the budget, or chunk the batch across calls, or raise the budget to the measured cost.

Verdict: IP-002 delivered what it specified and the evidence is real. The two findings are operational defaults, not plan violations, and should go into IP-003 before the hook is recommended to a community user.
