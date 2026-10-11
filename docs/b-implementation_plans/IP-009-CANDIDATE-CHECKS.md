---
id: IP-009
title: "Building outward: two checks over a candidate the caller already has, constraints on a candidate and document validity, as new jobs on the same policy layer"
slug: Candidate_Checks
status: approved
created: 2026-10-11
updated: 2026-10-11
component: packs, service, cli, mcp
platform: both
priority: high
linked_cr: "../c-completion_reports/CR-009_Candidate_Checks.md"
linked_ideas: "IDEA-002 (constraints on a candidate; document verification, validity half); IDEA-003 and IP-006 (small states, facts composed in policy); readme roadmap (IDEA-002 directions)"
estimated_crs: 1
author: fluid-decisions verifier session
tags: [constraints, validity, documents, packs, cli, mcp, outward]
---

> **APPROVED — 2026-10-11.** The maintainer gave a general go with one constraint, short and bounded from the start; the four decisions below were taken as recommended on that go to avoid another round trip and are recorded as such. The first outward plan. It adds two jobs that score a candidate the caller already has against rules written down before the call, exactly as IDEA-002 describes them, on the policy layer that IP-001 to IP-007 built and proved. It adds no solver, no label that acts, and no change to the gate. Lean cadence: no checkpoint, one verification at CR-ready sized to minutes.

# IP-009 — Candidate checks

**The matching CR will be:** `CR-009_Candidate_Checks.md`

## Why

The readme promises the IDEA-002 directions once the MVP is proven, and it is. IDEA-002's own order starts with document validity, the classify pack on a form, and constraints on a candidate, several noul questions and no new machinery. Both are review aids that return probabilities to a policy the model cannot see, and both fit what IP-006 measured about this model: it answers decisively when the thing the question is about is the whole state, and a composite judgement is a policy, not a question. Each constraint is one fact; each validity rule is one fact; the verdict is composed in code.

## Objective

After this plan: `fluid constraints` takes one candidate and a closed list of constraints and returns, per constraint, the probability it holds, the failing constraints by id, a severity for each failure, and a verdict in `pass`, `ask`, `no_feasible_candidate`. `fluid validity` takes a finished packet as text or up to eight page images and a closed specification of checks and returns, per check, the probability it holds, the failing checks by id, a document-type choice with an explicit unknown, a completeness score, and a verdict in `valid`, `invalid`, `ask`, `abstain`. Both are library calls, CLI subcommands and MCP tools on the same `Decisions` service, with the same budgets, audit and privacy rules as the four existing jobs. Support of claims stays the existing `verify` job and is never merged with validity.

## Current state (grounded 2026-10-11, `main` at `f6d9ac0`)

- Four jobs on one service: `classify`, `route`, `gate`, `verify`, each a pack under `fluid/packs/` with `questions` and a `policy` block, a pure `evaluate_*` in `fluid/policy.py`, a method on `Decisions`, a CLI subcommand and an MCP tool. Every call is budgeted from the cost model; images go to the text lane, up to eight; the audit stores verdicts, probabilities, pack version and timings, never state.
- The facts path (IP-006) builds one batched request from several small items and composes the axes in policy; `fluid.facts` and `compose_gate_facts` are the pattern to copy, not the code to reuse.
- `verify` already does claim support against a source: one item per claim, one noul, flagged below a line. That is the shape constraints need and the job validity must stay separate from.

## Proposed changes

### 1. Constraints on a candidate (`fluid/packs/constraints.yaml`)

- Input: `candidate`, any JSON or text the caller already has, and `constraints`, a list of `{id, rule}` where `rule` is one sentence written before the call. Optional `images`, a scan of the roster or load sheet, to the text lane.
- Request: one item per constraint, state `{candidate, constraint: rule}`, two questions: `held` (noul, "Is this constraint satisfied by the candidate?") and `severity` (score over levels the pack declares, lowest first, asked over every item so the batch stays one request). One batched request; cost is the candidate paid once per constraint plus two questions each, which the cost model budgets as today.
- Policy, pure, `evaluate_constraints`: a constraint fails when `held` is below `fail_line`; it is uncertain when `held` sits inside the `uncertain` band or its decisiveness is below `min_decisiveness`; `pass` when every constraint holds at or above `pass_line`; `no_feasible_candidate` when any fails, listing the failing ids first-failing-first in the caller's order with their severity; `ask` otherwise. No label proposes a fix; IDEA-002's rule. Lines in YAML, uncalibrated.
- Second pass: when the only uncertain constraints are inside the band, one more batch over those items alone with the same questions, as classify's second pass does, before `ask` is returned. Bounded to one.
- Surfaces: `Decisions.constraints(candidate, constraints, images=None)`, `fluid constraints --candidate/--file --constraints <json>`, MCP `check_constraints`.

### 2. Document validity (`fluid/packs/validity.yaml`)

- Input: `packet` as text, or `images` of up to eight pages, or both, plus `spec`: `{types: [...], checks: [{id, rule}], completeness_levels: [...]}`, all written before the call. The caller says the packet is finished; a partial stream is refused as `invalid_input` by the caller's own flag, never scored.
- Request: one state, the packet, because every check is a fact about the whole packet: one noul per check (`holds`), one `document_type` choice over `spec.types` plus the always-present option `unknown`, one `completeness` score over `spec.completeness_levels`. One request. A packet over the lane's prompt limit is split into page-sized items only for the per-check nouls, each page answering "does this page satisfy the check", composed by maximum in policy, and the CR says when that path was taken.
- Policy, pure, `evaluate_validity`: `abstain` when `document_type` chooses `unknown` or its top probability is below `type_line`; otherwise `invalid` when any check is below `fail_line`, listing the failing ids; `ask` when any check is in the uncertain band or decisiveness is low; `valid` otherwise, with the completeness level attached. A `valid` verdict says the form is filled, nothing more, and the result carries the sentence `validity is not support` so no caller reads it as truth. Lines in YAML, uncalibrated.
- Surfaces: `Decisions.validity(packet, spec, images=None)`, `fluid validity --file/--image --spec <json>`, MCP `check_validity`. The support half is a call to `verify`, documented beside it, not wrapped.

### 3. Examples, docs and evidence

- `examples/constraints/`: one fictional driver-and-load candidate with six constraints (hours cap, rest, vehicle class, delivery window, licence, weight), and one that fails two of them. `examples/validity/`: one fictional one-page application form as text and as an image, with a spec of five checks and three types. All fictional, no names of real people, places or organisations.
- `docs/USAGE.md` gains the two jobs; the readme's roadmap row for IDEA-002 moves these two to shipped and keeps the other three as roadmap.
- Evidence: one live run of each example on the text lane, text and image, recorded as counts and probabilities in `docs/candidate-checks-smoke.json`, with latency and cost per call. No harness, no arms.

## Out of scope

Least-worst route, employment rules, entitlement; any solver; any label that acts; calibration of the new lines (uncalibrated like every other first version); changes to the gate or post-result; the audio lane.

## Files to create or modify

| File | Action | Notes |
| --- | --- | --- |
| `fluid/packs/constraints.yaml`, `fluid/packs/validity.yaml` | Create | The two packs, questions and uncalibrated policy blocks; the only new packs. |
| `fluid/policy.py` | Modify | `evaluate_constraints`, `evaluate_validity`, pure. |
| `fluid/service.py` | Modify | `constraints()` and `validity()` on `Decisions`, budgeted, audited; the page-split path for large packets. |
| `fluid/cli.py`, `fluid/mcp_server.py` | Modify | Two subcommands, two tools, exit codes as the existing jobs. |
| `fluid/audit.py` | Modify | New field names. |
| `tests/test_constraints.py`, `tests/test_validity.py`, `tests/test_cli.py`, `tests/test_mcp.py` | Create, Modify | Recorded answers, no network: every verdict branch, the second pass, the unknown-type abstention, the page-split composition, surfaces. |
| `tests/test_live.py` | Modify | Two opt-in tests: each example once on the text lane. |
| `examples/constraints/*`, `examples/validity/*` | Create | Fictional inputs. |
| `docs/USAGE.md`, `README.md`, `docs/candidate-checks-smoke.json` | Modify, Create | Usage, roadmap row, evidence. |
| `docs/c-completion_reports/CR-009_Candidate_Checks.md` | Create | What shipped, the smoke, what was skipped. |

## Dependencies

None new. The text lane and the lane configuration as before.

## Risks

| Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- |
| A rule sentence the model cannot evaluate from the candidate alone, such as a rule that needs a lookup | Medium | Medium | The rule text and every fact it needs must be in the state; the usage doc says so, and `ask` is the verdict when decisiveness is low. |
| A caller reads `valid` as genuine or true | Medium | High | The result carries the sentence; the doc repeats IDEA-002's warning; support is a separate job. |
| Large packets as images cost seconds | Medium | Low | Up to eight pages, budgeted from the cost model, measured in the smoke. |
| Lines are guesses | Certain | Low | Marked uncalibrated; calibration waits for real traces, as every other pack did. |

## Test plan

Unit, no network: everything in the files table against recorded answers. Live, opt-in: the two examples on the text lane, text and image. Manual, recorded: one failing candidate and one invalid packet read end to end. No checkpoint.

## Validation criteria

- Both jobs return the documented result shape on every verdict branch, with per-item probabilities and decisiveness.
- A candidate that fails a constraint returns `no_feasible_candidate` with the failing ids and severities; no result ever proposes a change.
- An unknown document type abstains; a partial packet is refused, not scored.
- The smoke runs each example once live with latency and cost recorded.
- No identifier in any committed artifact; examples fictional.

## Done criteria

- The maintainer approves this plan and the decisions below.
- Changes land on `feat/ip-009-candidate-checks` from `main` with the named checks green.
- CR-009 records what shipped, the smoke and what was skipped.
- The independent verifier reruns the offline suite and the two live examples once, sized to minutes, before the CR is marked complete.

## Constraints for the implementing agent

- Read AGENTS.md, IDEA-002 and IP-006 first. Copy the facts pattern; do not reuse the gate.
- Policy pure, every number in YAML marked uncalibrated, no label that acts, no solver.
- One batched request per job plus at most one second pass.
- No checkpoint. Build, run the smoke, write the CR, call it ready.

## What follows

Least-worst route is next when a caller can hand in a costed shortlist. Employment rules and entitlement wait for calibrated lines and a review procedure, as IDEA-002 says.

## Maintainer decisions (2026-10-11)

Taken as recommended on the maintainer's general go for the plan, short and bounded:

1. Example domains: a fictional driver-and-load candidate for constraints and a fictional one-page application form for validity.
2. Surfaces: library, CLI and MCP for both jobs.
3. Page images for validity included, measured once in the smoke.
4. Branch `feat/ip-009-candidate-checks` from `main` at `f6d9ac0` or later.

Live work in this plan is the two-example smoke on the text lane, minutes, nothing paid.
