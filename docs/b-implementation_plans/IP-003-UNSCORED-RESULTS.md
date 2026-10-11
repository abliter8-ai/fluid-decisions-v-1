---
id: IP-003
title: "Unscored results: release with pending taint, budget from measured cost, never over-submit"
slug: Unscored_Results
status: complete
created: 2026-10-09
updated: 2026-10-09
component: integration
platform: both
priority: high
linked_cr: "../c-completion_reports/CR-003_Unscored_Results.md"
linked_ideas: "CR-002 second verification findings 1 and 2"
estimated_crs: 1
author: fluid-decisions verifier session
tags: [hardening, post-result, runtime, budget, ledger, timeout]
---

> **COMPLETE — 2026-10-09.** Approved by the maintainer and implemented in `bf6a07d` on `feat/ip-003-unscored-results`, with independent offline, live and manual verification. See [CR-003](../c-completion_reports/CR-003_Unscored_Results.md). Written by the independent verifier after verifying IP-002 at commit `545f3e0`. This plan changes how the runtime treats a result the model did not score; it does not change what counts as a flagged result.

# IP-003 — Unscored results

**The matching CR will be:** `CR-003_Unscored_Results.md`

## Why

IP-002 made every non-empty tool result go through the post-result check. That is correct. It also inherited a budget and a fail mode that were designed for a different situation, and the combination eats ordinary tool results.

The maintainer's diagnosis, confirmed by measurement on the d1-3B lane under the shipped defaults (`FD_FIRST_TIMEOUT=8`, `FD_TIMEOUT=1`):

| Result size | Post-result wall time | Outcome |
| --- | --- | --- |
| 1,000 chars | 258 ms | allow |
| 3,000 chars | 663 ms | allow |
| 6,000 chars | 1,001 ms | allow, at the cap |
| 12,000 chars | client timeout at 1 s | ask, result withheld |
| 24,000 chars | client timeout at 1 s | ask, result withheld |

The chain: `GuardedRuntime.run` executes the tool, then always calls `post_result`. The check is one forward pass over the whole result, in 12,000-character windows batched into one request. No text is generated, but the pass still reads every character, so cost grows with the file. The later-call budget is one second because warmup consumed the eight-second first-call budget, and one second was sized from the 130 to 250 ms gate calls. A source file is a few thousand tokens and does not fit. The client raises a timeout, `post_result` returns `ask`, and the runtime's last line only hands a result back on `allow`. The agent gets `executed: true` and no body. The read happened. A timeout was treated as if the file contained an injection.

A second effect follows from the first. A client timeout does not cancel the server's work, and the lane serialises inference. After a timed-out 24,000-character check, the next benign gate on a different session also timed out and returned `ask`; once the lane drained, the same gate allowed in 385 ms. One oversized result degrades every session sharing the lane for several seconds.

Fail-closed was written for a dangerous action. It is now applied to an ordinary result that took too long to score. A timeout is not evidence of an injection. It is not evidence of safety either, and the plan keeps that distinction.

## Objective

After this plan: a result the model did not score is released to the agent with that fact visible, and the uncertainty is carried forward to the next action through the ledger, where fail-closed belongs. The post-result budget is derived from the lane's measured cost at warmup, not from a short call. The client never submits work it already knows will not fit. The protections IP-002 added stay exactly as strong for a result the model or a static rule actually flagged.

## Current state (grounded 2026-10-09, commit `545f3e0`)

- `fluid/client.py`: `_request` uses `first_timeout` for the first call on a lane and `timeout` after; `warmup` sends one one-question noul, which consumes the first-call budget. No per-call budget override exists.
- `fluid/service.py` `post_result`: empty result returns `allow` with `empty_result`; static injection rules run on the full text before any network call and flag with shingle hashes; results over `max_total_chars` (1,000,000) return `ask` with `result_too_large_to_check`; otherwise windows of `window_chars` (12,000) with `overlap_chars` (500) go as one batch; `ClientError`, `OSError` and `ValueError` return `ask` through `failure()`.
- `GuardedRuntime.run`: sets `result_withheld` to `post['verdict'] != 'allow'` and only includes `result` when the post-result verdict is `allow`. It cannot tell a timeout from a flag.
- `fluid/ledger.py`: record types `flag`, `advance`, `cleared_by_operator`. `flag` stores injection probability, kind, decisiveness, result hash and byte count, shingle hashes, and a call counter. `active_taint` applies `window_calls` and `ttl_minutes`. `matches` uses every historical flag's shingles. Nothing is deleted.
- `fluid/packs/gate.yaml` version `0.2.0`: `post_result.policy` holds `threshold`, `window_chars`, `overlap_chars`, `max_total_chars`; `policy.taint` holds `window_calls`, `ttl_minutes`, `floor_from_level`, `shingle_words`, `min_shingle_chars`. All marked uncalibrated.
- The fleet server has no request cancellation. That is outside this repository and stays so.

## Proposed changes

### 1. Three post-result outcomes instead of two

`post_result` returns one of three outcome classes, named in a new `check` object on the result and in the audit record:

| `check.status` | When | Verdict | Runtime action | Ledger |
| --- | --- | --- | --- | --- |
| `scored_clean` | model scored every window below threshold, no static hit | `allow` | release | none |
| `scored_flagged` | static rule hit, or any window at or above threshold or kind injection/canary | `deny` | withhold | `flag`, as today |
| `unscored` | timeout, lane down, expected cost over the ceiling, or result over `max_total_chars` | `allow` with `checked: false` | release | new `pending` record |

`check.reason` for unscored is one of `timeout`, `lane_down`, `exceeds_budget`, `oversized`. Static injection rules always run on the full text first, whatever its size, because they are cheap and need no lane; a static hit is `scored_flagged` even when the model never ran. A result that is unscored is therefore still statically checked, and the CR must say so in those words.

`post_result.policy.on_unscored` in YAML selects `release` (default) or `withhold`. An operator who wants the old behaviour sets `withhold`. Nothing in Python decides this.

`complete` stays `true` for an unscored release: the runtime did everything it could. The CLI exit code for a released unscored result is 0, with `checked: false` in the JSON. The hook contract gains `checked` and `check` on post-result responses.

### 2. Pending taint

A new ledger record type `pending` is written for every unscored result. It carries the same fields as `flag` except the model fields are `null`, plus `check.reason`, and it carries the shingle hashes of the full result, computed client-side with no model. Consequences:

- **Layer A, carry-over, works on unscored results.** A command that later carries text from an unscored result is denied with `tainted_content_in_tool_input`, lane up or down, exactly as for a flagged result. This is the strongest protection in the system and it needs no model, so an unscored result loses nothing here.
- **Layer C, the floor, treats pending as active.** While a pending record is inside `window_calls` and `ttl_minutes`, a mutating-or-worse operation is floored to `ask` with reason `session_pending_check`. Reads go to the model as normal.
- **Layer B sees it.** The `session` block gains `pending_events` beside `untrusted_events`, so the gate state and the session-only question both see that an unscored result exists.
- **Expiry and clearance** follow the existing rules. `fluid session clear` clears pending context the same way it clears active taint. History is appended, never deleted.

A pending record can be **resolved** by a later successful score of the same result hash: `post_result` accepts an optional `recheck=True` path, used by a new `fluid session recheck --session ID` command, which rescores the most recent pending results under the measured budget and appends a `resolved` record with the real outcome. If the rescore flags, the session is flagged as if it had been scored at the time; carry-over hashes are already in place. Automatic background rechecking is out of scope for this plan; the command exists so an operator or a host runtime can do it.

### 3. Budget from measured cost

`D1Client.warmup` is extended. After the one-question warm call it sends two benign post-result-shaped requests on the same lane: one at `window_chars` characters and one at a quarter of that, both using the pack's post-result questions, both with generic filler text that contains no rule phrase. From the two timings it derives a per-lane cost model `{intercept_ms, ms_per_char}` and stores it on the client. Effects:

- The cold window shape is paid at startup, under the first-call budget, not on the first real file read.
- `health()` reports the cost model beside `last_warmup_ms`, so an operator can see what the lane can afford.
- `post_result` computes `expected_ms = intercept_ms + ms_per_char × total_chars` for the batch and requests a per-call budget of `expected_ms × post_result.policy.budget_margin`, floored at `FD_TIMEOUT` and capped at `post_result.policy.max_budget_s`. Both numbers live in `gate.yaml`, marked uncalibrated; starting points are margin 2.0 and cap 30 s.
- `_request` gains an explicit `budget_s` parameter for this path. Gate, classify, route and verify calls are unchanged.
- `FD_POST_TIMEOUT` is an optional environment override that replaces the derived budget with a fixed value, for operators who prefer a constant.

If warmup cannot measure, because the lane is down at startup, the cost model is absent and `post_result` falls back to `FD_POST_TIMEOUT` if set, else `FD_TIMEOUT`, and records `check.budget_source` so the audit shows which it used.

### 4. Never submit over budget

Before sending a post-result batch, if `expected_ms × budget_margin` exceeds `max_budget_s`, the batch is not sent. The result is unscored with reason `exceeds_budget`, released or withheld per `on_unscored`, and a pending record is written. This is the only client-side protection against a serialising server with no cancellation. The CR records the largest result size this lane can score inside the cap, so the number is known rather than discovered.

A server-side cancellation on client disconnect would remove the cascade at its root. That is a change to the fleet server and to `fluid/serving.py`; it is recorded here as a follow-up for a separate plan and not started.

### 5. Runtime and surfaces

- `GuardedRuntime.run`: `result_withheld` is true only for `scored_flagged`. The output gains `checked` and `check`. An unscored release includes `result`.
- `fluid/hook.py`: no logic change; the post-result response carries the new fields.
- `fluid/cli.py`: `fluid session recheck --session ID`.
- `fluid/mcp_server.py`: no change; the fields pass through.
- `docs/USAGE.md`: the three outcomes, the pending lifecycle, the recheck command, the budget derivation, and a plain statement that a released unscored result was statically checked but not model-scored. `.env.example` gains `FD_POST_TIMEOUT` commented out.
- `README.md`: one sentence in the controls list: an unscored result is released and the uncertainty is carried to the next action.

## Out of scope

- Changing what counts as flagged. Thresholds, static rules, windows and overlap stay as IP-002 left them.
- Background or automatic rechecking. The command exists; scheduling it is the host runtime's business.
- Server-side request cancellation, in the fleet server or in `fluid/serving.py`.
- Calibration. New numbers go in YAML marked uncalibrated. The audit now records unscored outcomes and budget sources, which is calibration input.
- Compaction, new packs, IDEA-003 and IDEA-004.

## Files to create or modify

| File | Action | Notes |
| --- | --- | --- |
| `fluid/packs/gate.yaml` | Modify | Version `0.3.0`. `post_result.policy`: add `on_unscored: release`, `budget_margin: 2.0`, `max_budget_s: 30`. |
| `fluid/client.py` | Modify | Cost model at warmup; `budget_s` parameter on `_request`; `health()` reports the cost model; `FD_POST_TIMEOUT`. |
| `fluid/service.py` | Modify | Three outcomes; `check` object; pending records; expected-cost gate; `recheck`; `session` block gains `pending_events`. |
| `fluid/ledger.py` | Modify | `pending` and `resolved` record types; `active_taint` returns pending events; `matches` includes pending shingles. |
| `fluid/policy.py` | Modify | `taint_floor` accepts pending as active with reason `session_pending_check`. No thresholds. |
| `fluid/cli.py` | Modify | `fluid session recheck`. |
| `fluid/audit.py` | Modify | Record `check`, `checked`, `budget_source`, `expected_ms`, `resolved`. |
| `examples/.env.example` or `.env.example` | Modify | `FD_POST_TIMEOUT` documented, commented out. |
| `docs/USAGE.md`, `README.md` | Modify | As in §5. |
| `tests/test_client.py` | Modify | Cost model derivation from two mocked timings; budget floor and cap; override; absent model fallback with recorded source. |
| `tests/test_service.py` | Modify | Timeout releases with `checked: false` and writes pending with shingles; static hit on an oversized result still flags and withholds; `on_unscored: withhold` restores old behaviour; expected cost over cap never calls the lane; pending floors mutating to ask and leaves reads to the model; carry-over from an unscored result denies with lane down; recheck resolves pending to clean or flagged; expiry and clearance. |
| `tests/test_storage.py` | Modify | Pending and resolved records append-only; no text stored. |
| `tests/test_surfaces.py` | Modify | Recheck CLI path; hook post-result response carries `checked`. |
| `tests/test_live.py` | Modify | Opt-in: warmup reports a cost model; a 12,000-character and a 24,000-character benign read are released with `checked: true` under derived budgets or `checked: false` with a recorded reason, never withheld; the measured sizes and timings print. |
| `scripts/smoke_hardening.py` | Modify | Add the released-read, pending-floor and recheck scenarios; keep the existing eleven. |
| `docs/c-completion_reports/CR-003_Unscored_Results.md` | Create | Evidence, lane labels only, no hostnames or paths. |

## Dependencies

No new dependencies. All of this is stdlib plus the existing client.

## Risks

| Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- |
| An injection in an unscored result reaches the agent | Medium | Medium | Static rules always run on the full text; carry-over denies any command built from it; pending floors mutating actions; recheck resolves it. `on_unscored: withhold` exists for operators who refuse the trade. |
| Cost model is wrong for an unusual shape, such as JSON-heavy or multilingual text | Medium | Low | Margin 2.0 and a 30 s cap; budget source is audited; `FD_POST_TIMEOUT` overrides. |
| Warmup now takes longer | High | Low | It pays the cold window shape once, at startup, which is where IP-001 said it belongs. Record the new warmup time in the CR. |
| Pending taint floors legitimate mutations after a slow read | Medium | Medium | Reads continue; recheck or clearance lifts it; the floor is `ask`, not `deny`. |
| Operators read "released" as "checked" | Medium | Medium | `checked: false` is in the result, the audit and the docs, and the USAGE wording is explicit. |

## Test plan

**Unit, no network.** Everything in the Files table, with mocked `ClientError('timeout')` and `ClientError('lane_down')` on `system_one_batch`, recorded answers for the scored paths, and a fake clock for expiry.

**Live, opt-in `FD_LIVE=1`, lanes explicitly selected, labelled by role not hostname.** Warmup prints the cost model. A 12,000-character read is released and scored within the derived budget; a 24,000-character read is either scored within budget or released unscored with `exceeds_budget`, and the CR records which and why. Immediately after the largest permitted check, a benign gate on another session still allows within its normal budget, because nothing over the cap was submitted. A pending session floors a write to `ask` and allows a read. `fluid session recheck` resolves the pending record. The hardening smoke passes all scenarios.

**Manual.** Point the stdin hook at a session, read a 20,000-character file through the guarded runtime, and confirm the agent receives the body and the next write is asked.

## Validation criteria

- A result the model did not score is never withheld under the default policy; it is released with `checked: false`, a reason, and a pending ledger record carrying its shingle hashes.
- A statically flagged result is withheld regardless of size or lane state.
- A scored flagged result behaves exactly as under IP-002.
- Carry-over denial works from an unscored result with the lane down.
- Pending taint floors mutating operations, leaves reads to the model, expires, and clears, with history appended.
- Warmup derives and reports a per-lane cost model; post-result budgets come from it, floored and capped by YAML; the audit records the budget source and expected cost.
- No batch whose expected cost exceeds the cap is sent; the live test shows a benign gate unaffected right after the largest permitted check.
- `fluid session recheck` resolves pending records.
- All new numbers live in `gate.yaml` marked uncalibrated. Offline suite passes with the network environment cleared. Ruff and mypy pass.
- No hostname, path, name or credential appears in any new file, including evidence JSON.

## Done criteria

- The maintainer approves this plan.
- Changes land on a branch from `feat/ip-002-hardening` with the named checks green.
- CR-003 records what was tested, against which lane role, what was skipped and why, the measured cost model, the largest scorable result size, the new warmup time, and the commit.
- The independent verifier reruns the offline suite, the live module, the hardening smoke and the manual read-then-write scenario before the CR is marked complete.

## Constraints for the implementing agent

- Read IP-001, IP-002, CR-002 and AGENTS.md first, including the privacy rule. No hostnames, paths or names in any artifact, evidence files included.
- Policy stays pure. Thresholds, margins, caps and the `on_unscored` choice live in YAML.
- Do not weaken a scored flag. Withhold stays for `scored_flagged`.
- Append to the ledger; never delete or rewrite records.
- Do not touch the fleet services or `fluid/serving.py` for cancellation; record the follow-up instead.
- Record lane disagreements; never average.

## Implementation clarifications

- The maintainer approved supplying original content to `fluid session recheck --session ID --file PATH`, or stdin when `--file` is omitted. The ledger stores hashes, not recoverable text; the supplied content must match an unresolved pending hash before any model call.
- A successful clean recheck resolves prior pending records for that hash, removing their context and carry-over protection. A flagged recheck creates an ordinary flag. Actual flag history remains protective; clearance and expiry alone retain carry-over hashes as in IP-002.
- `on_unscored: withhold` and ledger/audit failures still withhold. The runtime releases only a complete `allow`; the statement that only flagged results are withheld describes the default healthy-ledger path, not permission to ignore the operator's configured policy or failed persistence.
- Window overlap counts toward estimated batch size. A fixed `FD_POST_TIMEOUT` remains subject to the YAML cap and does not authorize submitting a measured workload known to exceed that smaller override. Warmup's quarter-window ratio is also in YAML (`warmup_probe_fraction`).
