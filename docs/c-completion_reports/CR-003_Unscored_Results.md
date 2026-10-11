---
id: CR-003
title: Unscored results and measured post-result budgets
slug: Unscored_Results
status: complete
date: 2026-10-09
linked_ip: ../b-implementation_plans/IP-003-UNSCORED-RESULTS.md
implementation_commit: bf6a07d
testing: true
context_updated: true
deployment: existing_services_unchanged
---

# IP-003 completion evidence

Implemented in **`bf6a07d`** on `feat/ip-003-unscored-results`, based on `feat/ip-002-hardening`. Results that could not be model-scored now carry visible uncertainty and pending protection into later actions. Scored flags retain withholding. No model download, serving process, fleet change, dependency change or server-side cancellation was added.

## Delivered behaviour

| Outcome | Default runtime result | Ledger consequence |
| --- | --- | --- |
| `scored_clean` | Release with `checked: true` | None, or resolve matching pending records during recheck |
| `scored_flagged` | Withhold with `deny` | Flag and retain carry-over hashes |
| `unscored` | Release with `checked: false`, `complete: true` and a reason | Append pending metadata and full-result shingle hashes |

**Every unscored result is statically checked but not model-scored.** Static rules inspect the full result first, including oversized results and unavailable-lane cases. Reasons for unscored results are `timeout`, `lane_down`, `exceeds_budget` and `oversized`. The YAML `on_unscored: withhold` option retains withholding for operators who choose it. A failed ledger or audit write still prevents release. Malformed input and invalid model responses remain incomplete errors. A model/static flag is never turned into an unscored release.

Pending metadata appears in both gate state and the session-only assessment. A mutating-or-worse operation receives the `session_pending_check` floor; reads remain subject to ordinary model and policy evaluation. Carry-over matching runs with the lane down. Expiry and clearance retain append-only history and lifetime carry-over protection, as in IP-002.

The maintainer explicitly approved original text supplied by `fluid session recheck --session ID --file PATH`, or stdin. Hashes cannot recover text. Recheck validates the supplied SHA-256 against unresolved pending records before inference, preserving UTF-8 line endings for both input routes. A clean result resolves all prior pending records for that hash and removes their context and carry-over hashes; a flagged result creates an ordinary flag. A future pending record for the same hash is independent. No raw content is stored and no background rechecking runs.

## Budget derivation

Warmup uses the existing small question plus full-window and quarter-window probes with the post-result questions. Each probe uses the first-call budget. The client stores a per-lane nonnegative intercept and per-character slope, reports warmup time and measurement failures, and estimates batch cost using all submitted window characters, including overlap.

The version `0.3.0` gate pack holds `budget_margin: 2.0`, `max_budget_s: 30`, `warmup_probe_fraction: 0.25` and `on_unscored: release`, all marked uncalibrated. Derived budgets retain the normal timeout as a floor. `FD_POST_TIMEOUT` provides a fixed budget subject to the cap; a known cost exceeding either the cap or a smaller override is rejected before submission. When measurement is unavailable, the client uses the override or normal timeout and records that source. Audits retain `check`, `checked`, expected cost, budget and resolution metadata.

## Measured evidence

[Implementation smoke](../unscored-smoke.json) contains 19 passing scenarios with lane labels only. The final run uses one warmup so the recorded cost model is the model used for every recorded budget.

| Text-lane measurement | Observed result |
| --- | --- |
| Warmup | 2,354.8 ms |
| Cost model | Intercept 84.466667 ms; slope 0.136177778 ms/character |
| Largest raw result permitted by that model and policy | 105,029 characters, including overlap when estimating the batch |
| Largest result actually scored in that run | 105,029 characters; 18,140.8 ms; derived budget 29.999765 seconds |
| Immediately following benign gate | `allow`, 352.2 ms under the normal one-second budget |
| Forced unscored read | 20,000 characters released; `checked: false`, reason `exceeds_budget` |
| Next mutation through stdin hook | `ask`, reason includes `session_pending_check` |
| Original-content CLI recheck | `checked: true`, `resolved: true`, `allow`; pending floor removed |

The separate live module scored and released 12,000 characters in **2,120.4 ms** and 24,000 in **4,278.1 ms**. Its independently measured cost model was 79.866667 ms plus 0.138244444 ms/character. Small repeated warm requests measured **131.7 ms on the text lane** and **31.8 ms on the audio lane**. These are different measurements, retained separately rather than averaged.

The smoke also preserves the original hardening scenarios: active flagged-session read evaluation, session assessment, mutating floor, hook context, audited clearance, model-only behaviour after clearance, offline carry-over denial, 45-character static injection denial with the lane down, and full 30,000-character checking. New scenarios cover pending carry-over denial, actual CLI recheck and the immediate post-batch gate. The forced-unscored scenario uses a deliberately tiny override to reject the batch before sending it; it does not provoke a server backlog by abandoning a long request.

## Checks and independent verification

| Check | Implementer result |
| --- | --- |
| `.venv/bin/python -m ruff check fluid scripts tests examples` | Passed |
| `.venv/bin/python -m mypy fluid` | Passed, ten source files |
| Offline `.venv/bin/python -m pytest -q` with live endpoint configuration cleared | 110 passed, 4 skipped; 3 subtests passed |
| `.venv/bin/python -m build` | Wheel and source distribution built |
| Opt-in live module | 4 passed |
| Extended hardening smoke | 19 checks passed |
| Recheck input preservation | File and stdin CRLF tests passed |
| Privacy | New/changed owned artifacts scanned for personal identifiers, private infrastructure values and credentials; new evidence records lane roles only |

The [independent verifier](../ip003-verification.json) reran and passed all named offline checks and build, all four live tests, all 19 smoke checks, and the actual file-read/stdin-recheck scenario before completion. It found no in-scope runtime failures. Its separate text-lane measurements were:

- Live module: 12,000 characters scored in 1,986.0 ms; 24,000 in 4,224.5 ms. Cost model 89.333333 ms plus 0.135988889 ms/character. Small warm calls: text 129.1 ms; audio 30.3 ms.
- Extended smoke: warmup 2,349.0 ms; cost model 82.833333 ms plus 0.136622222 ms/character. The modeled maximum of 104,685 characters was actually scored in 18,339.8 ms, within a 29.999861-second budget. The next gate allowed in 359.3 ms.
- Manual check: an actual 20,000-byte fixture was read through `GuardedRuntime` and released unscored under an explicit tiny override. A pending benign read **allowed**. A write through the stdin hook **asked** with `session_pending_check`. Recheck through stdin preserved the exact original CRLF bytes, returned `checked: true` and `resolved: true`, cleared the pending record and its carry-over hashes, and retained no raw text in the ledger. File-based recheck passed separately.

## Context and limitations

README, usage, environment example and agent instructions describe unscored releases, operator withholding, pending lifecycle, hash-verified recheck and measured budgets. The matching IP is complete and AGENTS has an evolution entry; there is no existing known-issues record requiring a resolved-blocker update. The previously requested privacy rules in AGENTS and CLAUDE are retained. The 23 owned files passed the identifier/credential scan, and new commits use a neutral automation identity without changing local Git configuration. Prior unrelated working-tree edits and idea documents are preserved. This change does not claim that historical repository disclosures have been scrubbed.

These measurements exercise benign text on the existing text lane. The maximum above is the largest permitted and tested for this measured model and 30-second policy, not a hardware ceiling or a guarantee for other token distributions, languages, hardware or contention. The model estimate was lower than actual elapsed time for the largest batch; the margin covered that difference. A client timeout still does not cancel server work. Requests already in flight can therefore still cause contention; server-side cancellation remains a separate follow-up. The audio lane's existing live contract was checked, but post-result text budgeting was qualified on the text lane. No threshold calibration, new pack or backlog feature was added.

## Second verification (designated verifier session, 2026-10-09, commit `79e1997`)

Reran independently. Ruff and mypy clean. Offline suite with the network environment cleared: 110 passed, 4 skipped, 3 subtests. Live module 4 passed; text lane warm call 130.7 ms, audio lane 31.2 ms; measured cost model 87.4 ms plus 0.1375 ms per character; 12,000 and 24,000 characters scored and released in 2,048.7 ms and 4,278.3 ms. Extended hardening smoke 19 of 19. No identifiers or credentials in the changed files.

End-to-end under the shipped defaults, no `FD_POST_TIMEOUT`, which is the scenario that opened this plan:

| Step | Result |
| --- | --- |
| Warmup | 2,410.6 ms; cost model 84.1 ms plus 0.1391 ms per character |
| Guarded 12,000-character read | executed, body returned, `checked: true`, `scored_clean`, derived budget 3.51 s, 2,267 ms wall |
| Guarded 24,000-character read | executed, body returned, `checked: true`, `scored_clean`, derived budget 7.12 s, 4,369 ms wall |
| Benign gate immediately after the 24,000-character check | `allow`, 350 ms; no cascade |
| Forced unscored 20,000-character read (tiny fixed override) | executed, body returned, `checked: false`, `exceeds_budget`, statically checked |
| Next write through the stdin hook | `ask`, `session_pending_check` |
| Next read through the stdin hook | `allow`, answers attached |
| Command carrying text from the unscored result, lane down | `deny`, `tainted_content_in_tool_input`, no model answers |
| Recheck with the original text on stdin | `allow`, `checked: true`, `resolved: true`, exit 0 |
| Write after recheck | `allow`, no pending reason |
| Ledger and audit | no result text; record types advance, pending, resolved; audit shows the four post-result outcomes |

Verdict: IP-003 delivered what it specified. The read that was eaten under IP-002 is now returned, scored, and the uncertainty path works end to end. The two CR-002 findings are closed. Remaining open item, already recorded above: a client timeout still does not cancel server work, which is a fleet-server and `fluid/serving.py` follow-up outside this plan.
