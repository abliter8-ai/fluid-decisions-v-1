---
id: IP-007
title: "Proportionate taint and calibration: a flag arms a targeted check instead of a blanket floor, pending results recheck themselves, a withheld path cannot be re-read, a result is released with the located unit redacted, read-only operations cannot trip action axes, the facts pack becomes the default, and every line is calibrated against a false-flag budget the maintainer sets"
slug: Proportionate_Taint_And_Calibration
status: approved
created: 2026-10-11
updated: 2026-10-11
component: policy, service, ledger, runtime, packs, harness
platform: both
priority: high
linked_cr: "../c-completion_reports/CR-007_Proportionate_Taint_And_Calibration.md"
linked_ideas: "CR-006 residuals and its Second verification (friction decomposition, withheld-file loop, three pack files, cue gap, export, lanes disclosure); the facts-arm taint analysis of 2026-10-10 (what produced the 75 blocked benign calls); IDEA-005 (design rules); IP-006 (provenance floor, facts, units, daemon)"
estimated_crs: 1
author: fluid-decisions verifier session
tags: [taint, ledger, post-result, redaction, provenance, composition, calibration, packs, privacy]
---

> **APPROVED — 2026-10-11.** The maintainer approved this plan with the decisions recorded in the final section: a false-flag budget of 5 benign results flagged and 10 benign calls asked per 100, results released with the confirmed unit redacted and static-rule hits withheld whole, withheld-path re-reads denied, the lanes file remediated in the working tree as the branch's first commit with history and the push left separate, the calibrated facts pack made the default with the packet pack as the frozen fallback, the confirmation arm on GLM only in both modes, and the branch `feat/ip-007-proportionate-taint` from `main` at `b64fa09`. Written by the independent verifier after CR-006 was verified and merged at `b64fa09`, from the two IP-006 arm stores and the committed evidence. This plan changes how the guard behaves after it has seen something: today a flag taxes the next twenty calls and a timed-out check taxes the next twenty, whether or not the action has anything to do with what was seen. It replaces both with targeted checks, deterministic where a fact is deterministic and one small-state question where it is not, and it ends with the first calibrated lines, chosen against a false-flag budget the maintainer sets rather than by hand.

# IP-007 — Proportionate taint and calibration

**The matching CR will be:** `CR-007_Proportionate_Taint_And_Calibration.md`

## Why

IP-006 settled the shape of the gate and the post-result check: small facts over small states, a model-free provenance floor, and the ship rule passed on both models. What it also showed, in the facts arm, is where the remaining friction comes from, and most of it is not the model's judgement.

- Taint was active on 82 per cent of gate calls. The reason string `session_taint_active` rides on every decision while active, so it reads as the top cause and is not one. Of the 75 blocked benign calls in enforce, 16 were `session_pending_check` from results the lane could not score in time (37 post-result timeouts at a median of about 6,000 characters), and 6 were the blanket `taint_floor` on mutating actions after a flag. Twenty-two blocks for reasons unrelated to the action.
- Of the 49 model blocks, 25 were `secret_exposure`, and most of those were the agent re-reading a withheld file through `base64`, `rev`, `strings`, `od` and a Python one-liner. The guard was right to stop them and the labels could not see it. In the verifier's own enforce run the notes task never finished: twelve withheld re-reads to the turn cap, because withholding a whole result leaves the agent unable to do what the user asked.
- Read-only calls were blocked on axes that cannot apply to a read: a file called `cleanup` read at `destructive` 0.71, a candidate file read at `unresolved_choice` 0.79, a file read before the one the task named first at `violates_constraint` 0.81. The composer already zeroes `off_task` for read-only operations; the other four axes were left as they were.
- The directive was located 13 of 13 times and confirmed 11 of 13 at lines nobody has calibrated. Benign results flagged at 4 to 28 per 100 with the shipped lines, by causes the report names. Every line in every pack is still marked uncalibrated, and the maintainer deferred the default flip until the read-only gap is closed and the lines are chosen.
- Three gate pack files where the plan named two; the constraint cues match substrings and lack the ordering cues after and then; the fixture export still comes from IP-005; and the lanes example file is tracked with real hostnames and sits on the remote.

The design rule from IDEA-005 applies to the session as it did to the packet: a flag is a fact about one piece of text, and the only question worth asking later is whether a given action carries out that text. That is a two-item state, the verify-pack shape, which scores decisively. Everything else about taint can be code.

## Objective

After this plan: a flagged result arms a targeted check. The located unit is kept in session memory, never on disk, and a later action that changes something is asked one fact, whether it carries out that instruction; unrelated actions pass. A result the lane could not score in time is rechecked by the runtime at the next turn with a wider budget instead of taxing the next twenty calls. A path whose result was withheld cannot be re-read in any encoding, deterministically. A result with a confirmed directive is released with that unit redacted rather than withheld whole, so the user's task can still be done. Read-only operations cannot trip the five action axes. The facts pack is the default and the packet pack the frozen fallback, two files. The constraint cues are word-bounded and include ordering. The export comes from the IP-006 arm. The lanes example file carries placeholders. And the lines of the facts pack and the post-result check are the first calibrated ones, chosen on the recorded traces against a false-flag budget the maintainer sets, labelled as calibrated on two models and one repository.

## Current state (grounded 2026-10-11, `main` at `b64fa09`)

- IP-006 merged. Offline suite 236 passed, 6 skipped, 3 subtests; ruff and mypy clean. `main` is 41 commits ahead of `origin/main` and unpushed.
- Packs: `gate.yaml` is the packet pack 0.4.0 with the provenance block and `floor_s` 2.0 and is the default; `gate-packet.yaml` is its frozen copy; `gate-facts.yaml` is the 0.5.0 candidate selected by `FD_GATE_PACK=gate-facts`. Lines: risk ask 0.55 and deny 0.85 for every risk axis, hard 0.70, mitigators 0.80 and 0.85; post-result `locate_line` 0.50, `confirm_line` 0.70, `confirm_without_task_line` 0.80, units 40 to 600 characters, at most 64. Taint: window 20 calls, 60 minutes, `floor_from_level: mutating`. Provenance: window 200 calls, 240 minutes. Constraint cues: twelve substrings including `before` but not `after` or `then`, cap 4.
- `fluid.policy.compose_gate_facts` zeroes `off_task` when the operation is read-only (`off_task_skipped_read_only`) and emits `no_constraint_sentences` when no cue matched; the other axes pass through unchanged. `taint_floor` raises allow to ask on mutating-or-worse operations while any flag or pending record is active, with `session_pending_check` when the active record is pending.
- `Decisions.post_result` in units mode returns `flagged`, `units`, `located_units`, `confirmed_units` and `unit_probabilities`; `GuardedRuntime.run` releases the result only when the verdict is allow, otherwise `result_withheld` and the agent gets a refusal. An unscored result (timeout, oversize, over budget) writes a `pending` ledger record; `post_result(..., recheck=True)` resolves it when called with the original text, and nothing in the runtime, the hook, the daemon or the harness calls it.
- Ledger records: `flag`, `pending`, `resolved`, `advance`, `cleared_by_operator`, `observed`. Text is never persisted; `observed` carries span and secret hashes. The gate's session batch still asks `session_untrusted` over the session record when taint is active.
- The hook daemon holds one warm `Decisions` with the per-session content cache from IP-006; the one-shot hook has no memory between calls.
- Evidence to calibrate on, in the ignored stores: the IP-006 facts arm (48 runs, 1,000 gate calls with every fact answer, 530 scored results with per-unit probabilities) and the packet arm, both relabelled with the corrected registry; the IP-005 store for the provenance replay.
- `examples/lanes.maintainer.yaml` is tracked with the real lane hostnames since the IP-001 commit and is present on `origin/main`.

## Proposed changes

### 0. Housekeeping that ships before the behaviour changes

- **Lanes example file.** `examples/lanes.maintainer.yaml` becomes a placeholder with the same shape and `<text-lane-url>` and `<audio-lane-url>` values; the real file moves to an ignored path outside `examples/`, named by `FLUID_LANES_FILE` in the maintainer's shell and in the private env file, with the path added to `.gitignore`; `docs/USAGE.md` and the handoffs say so. The working tree is clean after this; history is not rewritten by this plan and the report says so.
- **Two gate pack files.** `gate.yaml` becomes the facts pack, version `0.6.0` after the changes below; `gate-packet.yaml` stays the frozen 0.4.0 fallback selected by `FD_GATE_PACK=gate-packet`; `gate-facts.yaml` is removed. Tests that exercise the flat packet gate load `gate-packet` explicitly. Nothing else changes about how a pack is chosen.
- **Export.** `tests/fixtures/traces/` is re-exported from the IP-006 facts arm with the corrected labels, same schema, same licence files, identifier scan before commit.
- **Constraint cues.** Word-bounded regular expressions in YAML, adding `after` and `then`; `ask` no longer matches `task`. The cap stays 4; the sentence enumeration is otherwise unchanged.

### 1. Read-only operations cannot trip the action axes

`compose_gate_facts` extends the rule it already applies to `off_task`: when the operation level is `read_only`, from `tool_operation` for tools whose effect code knows and from the model's `operation` score for shell and unknown tools, the composed `destructive`, `privilege`, `unresolved_choice` and `violates_constraint` are 0.0 with reason `read_only_axes_zeroed`. `secret_exposure` and `exfiltration` are untouched, because reading a secret is an exposure and sending one is exfiltration. The per-item answers stay in `facts` for the audit, so nothing the model said is lost. Measured offline before anything runs: the facts arm's 1,000 recorded fact answers are recomposed with the new rule and the verdicts compared, so the friction change and the absence of any trap regression are known numbers without a lane.

### 2. Pending results recheck themselves

- The runtime keeps the text of a result that went unscored in session memory, bounded by `pending_cache_entries` and `pending_cache_chars` in YAML, never persisted. Before the next gate call in the same session, `GuardedRuntime` rechecks every cached pending result through `post_result(..., recheck=True)` under `recheck_budget_s` (the first-call timeout, in YAML), which resolves the ledger record on success; a second failure leaves it pending and is counted. The daemon does the same between requests; the one-shot hook cannot and keeps today's behaviour.
- The harness loop calls the runtime's recheck at every turn boundary, so pending asks in the confirmation arm measure the design, not the lane's bad minute.
- The post-result budget margin stays 2.0; the measurement reports the unscored rate per 100 and the pending-driven asks beside it, so the margin can be set in the calibration pass if the recheck is not enough.

### 3. A flag arms a targeted check

- **The located unit is kept.** When the post-result check confirms a unit, the runtime and the daemon keep that unit's text in the session content cache, keyed by the flag's result hash, bounded by `instruction_cache_entries` and `instruction_cache_chars`. The ledger record is unchanged and still carries hashes only.
- **One fact instead of a floor.** While a flag is active and the action's operation level is mutating or worse, the facts gate adds one item per cached active instruction, capped by `max_active_instructions`: state `{instruction: <unit text>, action}`, one noul `carries_out` ("Does this action carry out this instruction?"). Policy replaces `taint_floor` with `taint_check`: ask with reason `carries_out_flagged_instruction` when any `carries_out` is at or above `taint_carry_line` (YAML, uncalibrated until §5), otherwise the action passes with the probabilities recorded. When no instruction text is available, because the hook is one-shot or the cache evicted it, today's blanket floor applies and the reason says `taint_floor_no_instruction`. Deny stays where it is: a command whose span came from a flagged result is denied by provenance already.
- **A withheld path cannot be re-read.** A new ledger record `withheld` carries the hashes of the path fields and path-like command tokens of a call whose result was withheld. `provenance.check` denies a later call that names one of them in any field, reason `withheld_path_reread`, model-free, whatever the encoding wrapped around it (decision 3: deny, not ask). The user's own words do not exempt this one: the user asked for the file once and the guard withheld it; §4 is what gives the user the file.
- **The session record leaves the model's state.** The `session_untrusted` question and the session item are dropped from the gate request; the session's effect is policy only. One composite question fewer per tainted call.
- Measured offline where it can be: the withheld-path rule is replayed over both arm stores and counts every evasion re-read it would have denied and every benign call it would have touched. The `carries_out` fact needs the lane and is measured in the confirmation arm.

### 4. Release with the located unit redacted

- `post_result` in units mode returns `redacted_text` alongside the verdict: the result with each confirmed unit replaced by a fixed marker (`[fluid-decisions withheld 1 unit]`), byte-exact elsewhere. `on_confirmed` in YAML chooses `redact` or `withhold`; the default is `redact` (decision 2). `GuardedRuntime.run` releases `redacted_text` when the policy says redact and records `units_redacted`; the full result never reaches the agent; the ledger flag and taint behave exactly as before. A static-rule hit keeps `withhold`, because a static rule marks a sentence, not a unit (decision 2).
- The hook and the daemon return `redacted_text` in the same JSON contract; the MCP surface too.
- Measured in the confirmation arm: completion of the notes task, withheld re-reads per run, and whether any redacted result still carried the directive's sentence (it must not, checked by the T3 sentinel over released text).

### 5. Calibration against a false-flag budget

- The maintainer's budgets in YAML: `false_flag_budget.results_per_100: 5` and `false_flag_budget.calls_per_100: 10` (decision 1). `scripts/calibrate_lines.py` sweeps the recorded traces, the facts arm relabelled plus the IP-006 facts and units measurements, over a grid of candidate lines per axis and for `locate_line`, `confirm_line` and `taint_carry_line`, and reports for every point the benign flags and asks per 100 and the catch of every attempted trap, per model, never pooled. It picks the point with the highest catch inside both budgets, ties broken toward fewer asks, and writes the chosen lines into `gate.yaml` with `calibration: two-models-one-repository` and the grid into `docs/calibration-grid.json`. A line is never moved by hand; a line the grid cannot place inside the budget stays uncalibrated and the CR says which.
- One phrasing pair, recorded: `destructive` gains noul criteria text (the API's `true` and `false` descriptions, "deletes or discards data or files, or cannot be undone" against "edits, creates or reads something that can be undone"), measured on the facts arm's recorded states beside the bare question before either is chosen. The in-place edit that scored 0.91 is the case it is for.
- Per-axis ask and deny lines replace the single pair only if the grid shows an axis needs its own; otherwise the shared pair is kept. The report says which.

### 6. Measurement and reporting

- Order: §0, then the offline measurements (§1 recomposition, §3 withheld-path replay, §5 grid), then the confirmation arm. Nothing live before its offline measurement is in the CR.
- Confirmation arm: `gate.yaml` 0.6.0 with everything above and the calibrated lines, on the remediated `fluid-ip006-sandbox` image, the same twelve tasks, one repeat, GLM only in both modes, 24 runs (decision 6); the IP-006 facts arm's GLM runs on the same image are the baseline.
- Reported per model and mode, never pooled: friction decomposed as CR-006 did, with `taint_check` asks, `taint_floor_no_instruction` asks and pending asks as their own rows; task completion; withheld re-reads and `withheld_path_reread` denials; redactions and a check that no released text carried the directive; T3 located and confirmed; benign flags and asks per 100 against the budgets; every trap's catch against the IP-006 facts arm; gate and post-result timeouts; latency by state size.
- Ship rule, decided by the numbers: the calibrated 0.6.0 pack stays the default only if, against the IP-006 facts arm, friction per 100 is lower, no trap's catch is lower, and the budgets hold, on the model the arm ran; the second model's check against the calibrated lines is a recorded gap for IP-008's run. Otherwise `gate-packet` returns to the default and the CR names the criterion.

## Out of scope

- The AgentDojo comparison. It is IP-008 and takes the calibrated pack this plan produces.
- History rewriting for the lanes file, any push, the audio lane, new packs, compaction, fine-tuning.
- Changing the provenance floor's rules, the facts items or the unit splitter beyond what is named above.

## Files to create or modify

| File | Action | Notes |
| --- | --- | --- |
| `examples/lanes.maintainer.yaml`, `.gitignore`, `docs/USAGE.md`, `docs/h-handoffs/*` | Modify | Placeholder values; ignored real path named by `FLUID_LANES_FILE`. |
| `fluid/packs/gate.yaml` | Modify | Becomes the facts pack 0.6.0: read-only zeroing, taint check, pending and instruction caches, redaction, cues, budgets, calibrated lines and label. |
| `fluid/packs/gate-facts.yaml` | Delete | Folded into `gate.yaml`. |
| `fluid/packs/gate-packet.yaml` | Keep | Frozen 0.4.0 fallback. |
| `fluid/policy.py` | Modify | `compose_gate_facts` read-only rule; `taint_check` replacing `taint_floor`; redaction of confirmed units; word-bounded cues. |
| `fluid/facts.py` | Modify | Instruction items for active flags. |
| `fluid/units.py` | Modify | Byte-exact redaction helper. |
| `fluid/ledger.py` | Modify | `withheld` records; replay; `withheld_paths()`. |
| `fluid/provenance.py` | Modify | Withheld-path check. |
| `fluid/service.py` | Modify | Pending and instruction caches; recheck before the gate; session item removed; `redacted_text`; `units_redacted`. |
| `fluid/hook.py`, `fluid/hookd.py`, `fluid/mcp_server.py`, `fluid/cli.py` | Modify | `redacted_text` in the contract; daemon recheck between requests. |
| `fluid/audit.py` | Modify | New fields. |
| `harness/loop.py`, `harness/summary.py`, `scripts/trace_harness.py` | Modify | Recheck at turn boundaries; new friction rows; redaction and re-read counts; released-text sentinel check. |
| `scripts/recompose_facts.py`, `scripts/replay_withheld.py`, `scripts/calibrate_lines.py` | Create | The three offline measurements. |
| `scripts/measure_facts.py` | Modify | The one phrasing pair for `destructive`. |
| `tests/test_policy.py`, `tests/test_service.py`, `tests/test_ledger.py`, `tests/test_provenance.py`, `tests/test_units.py`, `tests/test_hook.py`, `tests/test_harness.py`, `tests/test_calibration.py` | Modify, Create | See the test plan. |
| `tests/fixtures/traces/` | Modify | Re-export from the IP-006 facts arm. |
| `docs/calibration-grid.json`, `docs/recompose-facts.json`, `docs/withheld-replay.json`, `docs/arm-calibrated/*` | Create | Evidence, counts and probabilities only. |
| `docs/USAGE.md`, `docs/HARNESS.md`, `README.md`, `AGENTS.md` | Modify | Behaviour after a flag, redaction, rechecks, the two pack files, the calibration label. |
| `docs/c-completion_reports/CR-007_Proportionate_Taint_And_Calibration.md` | Create | Per-section evidence, the grid, the ship decision, the commit. |

## Dependencies

No new runtime dependency. The arm stores from IP-006 must still be present locally for the offline measurements. The node, image, gateway env file and lane configuration are as in IP-006, supplied at run time.

## Risks

| Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- |
| `carries_out` scores high on legitimate follow-ups to a flagged file, such as editing the README the task asked for | Medium | Medium | It asks, never denies; measured per model in the arm; the line is calibrated in §5 against the budget. |
| Redaction leaves a fragment of the directive outside the confirmed unit | Low | High | The released text is checked against the sentinel in the arm; units are byte-covering; a located-but-unconfirmed neighbour is reported. |
| Withheld-path denial blocks a read the user legitimately repeats | Low | Low | With redaction the first read is released, so re-reads are rare; the replay counts benign touches. |
| Rechecks add lane load at the worst moment | Medium | Low | One recheck per pending result per turn, wider budget, counted; a second failure stays pending. |
| Calibrating on the traces that produced the design overfits | High | Medium | Labelled as calibrated on two models and one repository; the confirmation arm is a fresh run; IP-008 measures on an external benchmark. |
| The default flip churns the flat-gate tests | Certain | Low | Tests name `gate-packet` explicitly; the packet pack is byte-identical to today's default. |
| Instruction text in memory | Low | Medium | Bounded caches, per session, never persisted, cleared with the session; the daemon's socket is 0600. |

## Test plan

**Unit, no network.** Read-only zeroing for every tool and for shell by level; `taint_check` with and without instruction text, caps, the fallback floor; `withheld` records, replay, ageing, denial in every encoding of a path; redaction byte-exactness and marker placement, static hits withheld; pending cache bounds and recheck resolution with a fake client, second failure counted; word-bounded cues; the calibration script on a synthetic grid with a known optimum and a point outside the budget; harness recheck at turn boundaries and the new summary rows; hook and daemon contract with `redacted_text`.

**Live, opt-in, lanes explicitly selected.** One test: a flagged notes-style result followed by a `write_file` that carries out the directive asks on `carries_out`, and an unrelated `cargo test` does not. The phrasing pair for `destructive`. The confirmation arm.

**Manual, recorded in the CR.** One notes task followed end to end: read, locate, redact, release, the task's own write passing, an evasion re-read denied.

## Validation criteria

- Recomposition on the facts arm's recorded answers: friction lower on both models, every trap's catch unchanged, per model (this measurement covers both models offline even though the live arm is GLM only).
- Withheld-path replay on both arm stores: every evasion re-read in the stores denied; benign touches reported per 100.
- Confirmation arm (GLM, both modes): the ship rule's three criteria stated with their numbers; no released text carries the directive sentence; pending asks and taint asks on their own rows; the notes task completes at least once in enforce.
- Every chosen line comes from the grid inside the budgets or stays uncalibrated with the reason; the pack label says `two-models-one-repository`.
- Two gate pack files; the placeholder lanes file; the re-exported fixtures; zero identifiers in any committed artifact; raw stores ignored.

## Done criteria

- The maintainer approves this plan and the decisions below.
- Changes land on `feat/ip-007-proportionate-taint` from `main` at `b64fa09` or later with the named checks green.
- CR-007 records the offline measurements, the grid and the chosen lines, the confirmation arm against the IP-006 facts arm, the ship decision, what was skipped and why, and the commit.
- The independent verifier reruns the offline suite, the live module, the three offline measurements, one harness run on the calibrated pack, and inspects the evidence and the export for identifiers before the CR is marked complete.

## Constraints for the implementing agent

- Read AGENTS.md and its privacy rule, IP-006, CR-006 including both verification sections, and IDEA-005 first.
- Policy stays pure; every number, cue, cap, budget and line is YAML. The calibration script writes lines; a person does not.
- No instruction text or result text is persisted anywhere; the caches are memory, bounded, per session.
- Measure offline before live, in the order of §6. Record per model and per lane; never pool.
- Static denials, provenance denials and the withheld-path rule apply in every mode and with the lane down. Model verdicts and `taint_check` asks are enforced only in enforce mode.
- Scan every evidence file, export and summary for identifiers before committing; the lanes file change is the first commit on the branch.

## Maintainer decisions (2026-10-11)

Recorded after the draft was reviewed:

1. The false-flag budget is 5 benign results flagged per 100 and 10 benign calls asked per 100.
2. A result with a confirmed unit is released with that unit redacted; a static-rule hit is withheld whole.
3. A re-read of a withheld path is denied.
4. The lanes example file is remediated in the working tree as the branch's first commit; history is not rewritten by this plan and the push of `main` stays a separate decision.
5. The calibrated facts pack becomes the default; the packet pack is the frozen fallback; the third pack file is removed.
6. The confirmation arm runs on GLM only, both modes, one repeat, to halve node time; both models are still covered by the offline recomposition and the calibration grid.
7. The branch is `feat/ip-007-proportionate-taint` from `main` at `b64fa09` or later.
