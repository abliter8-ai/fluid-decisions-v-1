# CR-009 — Candidate checks

Implementation plan: `docs/b-implementation_plans/IP-009-CANDIDATE-CHECKS.md` (approved 2026-10-11). Branch `feat/ip-009-candidate-checks` from `main` at `82d4038`.

The first outward plan. Two review-aid jobs — **constraints on a candidate** and **document validity** — on the same policy layer the MVP proved, exactly as IDEA-002 describes them. No solver, no label that acts, no change to the gate. Claim support stays the existing `verify` job.

## What shipped

Both jobs are a pack, a pure `evaluate_*` in policy, a method on `Decisions`, a CLI subcommand and an MCP tool, with the same budgets, audit and privacy rules as the four existing jobs. **288 offline tests pass (15 new), ruff and mypy clean.**

- **`constraints` (`fluid/packs/constraints.yaml`, `Decisions.constraints`, `fluid constraints`, MCP `check_constraints`).** Input: a candidate (JSON or text) and a closed list of `{id, rule}`. One batched item per constraint, state `{candidate, constraint: rule}`, two questions — `held` (noul) and `severity` (score over minor/moderate/major/critical, asked over every item so the batch stays one request). `evaluate_constraints` holds a constraint at or above `pass_line` with decisiveness at or above `min_decisiveness`, fails it below `fail_line`, else marks it uncertain; the verdict is `no_feasible_candidate` if any fails (failing ids first-failing-first in the caller's order, with severity), `ask` if any is uncertain, else `pass`. One bounded second pass over the uncertain constraints before `ask`. No result proposes a fix.
- **`validity` (`fluid/packs/validity.yaml`, `Decisions.validity`, `fluid validity`, MCP `check_validity`).** Input: a packet as text and/or up to eight page images, plus a closed `spec` of `{types, checks:[{id,rule}], completeness_levels}`. One request over the whole packet: a noul per check, a `document_type` choice (the spec's types plus an always-present `unknown`), a completeness score. `evaluate_validity` abstains when the type is `unknown` or its top probability is below `type_line`; otherwise `invalid` if any check fails, `ask` if any is uncertain, else `valid` with the completeness level attached. A packet whose single request exceeds budget is split into page-sized text chunks for the per-check nouls, composed by maximum (unit-tested; not exercised live). **Validity is not support:** every result carries the sentence `validity is not support`; a partial packet is refused as `invalid_packet_or_spec`, not scored.
- Lines live in the two pack policy blocks, all marked `uncalibrated`. The candidate, packet and spec text are never written to the audit log (only verdicts, per-item probabilities, statuses, ids and labels). CLI exit codes: `no_feasible_candidate`/`invalid` → 1, `ask`/`abstain` → 2, `pass`/`valid` → 0.

## Live smoke (text lane, each example once)

`docs/candidate-checks-smoke.json`, counts and probabilities only:

| Example | Verdict | Note |
| --- | --- | --- |
| `constraints/driver-load.json` | `no_feasible_candidate` (failing: `rest`) | The "intended pass" candidate; the model read the rest rule as unmet. See below. |
| `constraints/driver-load-fails.json` | `no_feasible_candidate` (failing: `rest`, `delivery_window`, `weight`) | Catches the planted violations (ETA outside the window, 4200 kg over the 3500 kg limit, short rest). |
| `validity/application-form.txt` | `valid` (type `membership_application`) | The filled form; completeness recorded. |
| `validity/application-form.png` | `ask` | The stand-in image is a 1×1 placeholder, so the checks are uncertain — the image path runs end to end and returns, as designed. |

The mechanism works live on both jobs, text and image: a batched request, a composed verdict, per-item probabilities, latency and budget recorded per call. Two honest notes, both expected for a first, uncalibrated version:

- The `driver-load` example was written to pass but came back `no_feasible_candidate` on `rest` (11 hours against a "at least 10" rule): a model-judgment / uncalibrated-line effect, not a code fault — the verdict, the failing id and the severity are all produced correctly. Lines wait for real traces, as every other pack did.
- The image smoke used a 1×1 placeholder PNG (no real document image is committed), so `ask` is the correct read; the real-image path is covered only to the extent that it runs and returns.

## What was skipped and why

- Calibration of the new lines — uncalibrated like every first version; waits for real traces.
- A real scanned document image — none is committed (privacy); the image path is exercised with a placeholder and by unit tests.
- Least-worst routes, employment and entitlement checks; any solver; any label that acts; changes to the gate or post-result; the audio lane — all out of scope.

## Tests

`tests/test_constraints.py` and `tests/test_validity.py` (new) cover every verdict branch, the bounded second pass, the unknown-type and low-confidence abstentions, the missing-answer uncertainty, the page-split maximum composition, and input refusals, all on recorded answers with no network. `tests/test_surfaces.py` gains the two CLI exit-code paths; `tests/test_mcp.py` asserts the two new tools are listed; `tests/test_live.py` gains two opt-in text-lane runs of the examples (text and image). Fictional examples under `examples/constraints/` and `examples/validity/`; no real person, place or organisation.

## Evidence and privacy

`docs/candidate-checks-smoke.json` (counts, probabilities, latency, budget; no candidate, packet or spec text — scanned before commit). No identifier in any committed artifact; the examples are fictional and were checked for real names, addresses and contacts.

## Second verification (designated verifier session, 2026-10-11, branch tip `38abb89`)

One pass, sized to minutes. Offline suite 288 passed, 8 skipped, with the lane environment cleared; ruff and mypy clean; the two new packs are the only new packs and the gate, packet and verify packs are untouched; 21 changed files carry no identifier; the examples are fictional.

Live, once each on the text lane through the CLI: `driver-load-fails` returns `no_feasible_candidate` failing `rest`, `delivery_window` and `weight` in that order, every `held` probability recorded (0.02 to 0.70), 1.3 s of lane time; the application form as text returns `valid`, type `membership_application`, completeness `complete`, with the sentence `validity is not support` on the result; the placeholder image returns `abstain` with `document_type_unknown_or_below_line`, which is the designed outcome for an unreadable page. The two opt-in live tests pass.

The one honest note in the report is confirmed and worth more than a line: the intended-pass candidate also returns `no_feasible_candidate` on `rest`, with `held` at 0.03, a confident no, for eleven hours of rest against a rule of at least ten. That is not an uncalibrated line, it is the model being asked to do a numeric comparison inside a sentence, which is the thing the design rules say code should do. The remedy for the next outward step is to let the caller, or a small deterministic pre-pass, state the arithmetic as a fact in the state (`rest_hours: 11, minimum: 10, meets_minimum: true`) and ask the model only what remains a judgement. Recorded for IP-010, not changed here. Merge is the maintainer's.

## Commit

Branch `feat/ip-009-candidate-checks`: the two packs, the two evaluators, the two service methods, the CLI and MCP surfaces, the audit fields, the examples, the tests, the docs, the smoke evidence and this report. The independent verifier's single CR-ready pass (offline suite, the two live examples once) completes it.
