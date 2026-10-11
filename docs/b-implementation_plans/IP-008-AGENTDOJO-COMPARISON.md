---
id: IP-008
title: "AgentDojo comparison: mount the guard as a defense in a public prompt-injection benchmark and report utility, utility under attack and attack success beside the published defenses, then stop measuring and build outward"
slug: AgentDojo_Comparison
status: approved
created: 2026-10-11
updated: 2026-10-11
component: integration, evaluation
platform: both
priority: high
linked_cr: "../c-completion_reports/CR-008_AgentDojo_Comparison.md"
linked_ideas: "IDEA-005 (AgentDojo as the external yardstick); IDEA-003 (measure the differential on real work, not synthetic); CR-007 and its Second verification (the calibrated pack and what remains uncalibrated); IDEA-002 (what follows: building outward)"
estimated_crs: 1
author: fluid-decisions verifier session
tags: [agentdojo, benchmark, defense, evaluation, external]
---

> **APPROVED — 2026-10-11, rescoped the same day.** The maintainer approved this plan and then cut its size: minutes, not hours. The agent model is a cloud model served through the maintainer's gateway, `claude-haiku-5-5`; five workspace user tasks with three injection tasks each; two arms, undefended and the guard; one short background job with no checkpoint; run on the Mac in process; branch `feat/ip-008-agentdojo`. The result is an indicative external check, read as such. Written by the independent verifier after CR-007 was verified. This plan is deliberately small. It adds no behaviour to the guard, changes no line, and produces one external number set. Its cadence is the lean one agreed on 2026-10-11: one checkpoint, verification at CR-ready only, and the implementer proceeds between phases without holding for anyone.

# IP-008 — AgentDojo comparison

**The matching CR will be:** `CR-008_AgentDojo_Comparison.md`

## Why

Every number so far comes from our own harness: our traps, our labels, one repository, two local models. The guard went from blocking two in three harmless actions to one in twenty-five while catching the planted injection every time, and the designed defenses, provenance and redaction, held. That is a strong internal result and an unproven external one. AgentDojo is the benchmark the field compares prompt-injection defenses on: four tool-using suites, user tasks with planted injection tasks, and three numbers per defense, benign utility, utility under attack and targeted attack success. CaMeL, Prompt Guard 2 and the spotlighting and tool-filter defenses are all reported on it. One run of the guard there answers "is this good" from outside, and it is the last measurement before building outward.

## Objective

After this plan: the guard runs as an AgentDojo defense, a pipeline element before the tool executor that gates each tool call and one after it that checks and redacts each tool result, with provenance and session memory per task, driven by a cloud model served through the maintainer's gateway, `claude-haiku-5-5`, via the benchmark's `openai-compatible` provider (decision 3; the gateway also serves an OpenAI route and a cloud-hosted GLM, available as later arms). The benchmark's own scoring produces utility, utility under attack and attack success for the undefended pipeline, the guard, and the published defenses that can run locally, on one suite and one attack, per model, never pooled. The result is recorded in CR-008 with the benchmark version, suite, tasks, attack and model identifiers, and the plan ends with the hand-off to the outward build.

## Current state (grounded 2026-10-11)

- `main` after the IP-007 merge carries the calibrated facts pack as default, the provenance floor, redaction, the pending recheck and the hook daemon. Lines calibrated on two models and one repository; `taint_carry_line` uncalibrated; the benign-result flag budget met on recorded states and not on the fresh run.
- AgentDojo (`pip install agentdojo`, MIT, ETH Zurich SPY Lab) ships four suites (`workspace`, `slack`, `travel`, `banking`), attacks selectable by name, and defenses `tool_filter`, `transformers_pi_detector`, `spotlighting_with_delimiting` and `repeat_user_prompt`. A pipeline is a sequence of `BasePipelineElement`s with one method, `query(query, runtime, env, messages, extra_args)`; the tool loop is `ToolsExecutionLoop([ToolsExecutor(), <elements>, llm])`; a checking element may raise `AbortAgentError` to stop an episode. The `openai-compatible` model provider, added in June 2026, takes `OPENAI_COMPATIBLE_BASE_URL` and `OPENAI_COMPATIBLE_API_KEY` with the model named by `--model-id`. `tool_filter` runs only on OpenAI models. Custom pipelines are not registrable by name yet; they are run through the `agentdojo.benchmark` module's functions. The benchmark CLI is `python -m agentdojo.scripts.benchmark -s <suite> [-ut user_task_N ...] --model <provider> --model-id <id> --defense <name> --attack <name>`. The implementer verifies all of this against the installed version before relying on it; the live package wins over this paragraph.
- The maintainer's gateway is OpenAI-compatible and serves the two local routes plus three cloud routes: `claude-haiku-5-5`, `OpenAI/ChatGPT-5.6-Luna` and `zAI/GLM-5.3-Flash`. The agent model for this plan is `claude-haiku-5-5` through the gateway (decision 3); the gateway endpoint and key stay in the private env file. `tool_filter` is OpenAI-only and is excluded regardless.
- AgentDojo's environments are simulated in process. No sandbox node is needed; the benchmark runs on the Mac beside the text lane, and the only network calls are to the gateway and the lane.

## Proposed changes

### 1. The guard as a pipeline

- `fluid/agentdojo.py`, importable only when `agentdojo` is installed (an optional extra `fluid-decisions[agentdojo]`, pinned): `FluidGateElement` reads the last assistant message's tool calls, calls `Decisions.gate` for each with the user task as `user_request` and a session id per episode, lets allowed calls through unchanged, and replaces an `ask` or `deny` with a tool-result message carrying the refusal in the same JSON the harness uses, so the model sees why. `FluidPostResultElement` runs after `ToolsExecutor`, calls `Decisions.post_result` on each tool result with the task text, and replaces the result with `redacted_text` or the withheld marker exactly as `GuardedRuntime` would. Both elements record the decision, the probabilities and the budget to the audit log as usual; nothing in AgentDojo is modified.
- `scripts/agentdojo_run.py`: builds the pipelines for every arm from the benchmark's own elements plus ours, runs the benchmark module's functions over the chosen suite, tasks and attack, and writes the benchmark's native results plus one summary per arm and model to `docs/agentdojo/`. Arms: `none` (the plain pipeline) and `fluid` (ours). The published defenses are built as further arms only if the maintainer asks later; none is run in this plan. Model and endpoint from the private env file, never committed.
- Session semantics: one `Decisions` with a fresh session id per episode; the ledger directory under an ignored local path; the daemon is not used.

### 2. What is measured

- The benchmark's three numbers per arm and model: benign utility on the user tasks without injection, utility under attack, and targeted attack success rate, exactly as AgentDojo computes them, with the version, suite, task list, attack and model ids recorded.
- The guard's own counts per arm: gate asks and denies per episode, redactions, withheld results, provenance hits, timeouts, and lane latency, from the audit log.
- One table in CR-008: the two arms side by side, with the published numbers for the same suite and attack on the models the paper reports quoted beneath for orientation, clearly separated, because the models, the task subset and the run size differ. No pooling, no averaging.

### 3. Scope and time

- One suite, `workspace`, one attack, `important_instructions`, five user tasks with their first three injection tasks each (decision 1): about 20 episodes per arm, two arms (decision 2), tens of minutes in total on the cloud route, one background job with a recorded unit and a durable log, gateway usage recorded in the CR. No checkpoint: the job is short enough to run straight through and be read whole. Twenty episodes per arm is an indicative check of whether the guard holds up outside its own harness; it is not a statistically settled comparison, and the CR says so in its first line. Larger runs, the published defenses as further arms, and a local-model arm are later work only if the maintainer asks.

## Out of scope

- Any change to the guard's behaviour or lines. A finding that the guard should change goes into the next plan; this one reports.
- `tool_filter` (OpenAI-only), CaMeL (its own agent architecture, not a drop-in element), the other three suites, other attacks, cloud models.
- The IP-007 backlog items: a second arm repeat, the per-file flag budget, `taint_carry_line`. They stay in the backlog unless this benchmark makes one of them decisive.

## Files to create or modify

| File | Action | Notes |
| --- | --- | --- |
| `fluid/agentdojo.py` | Create | The two pipeline elements; import guarded by the optional extra. |
| `scripts/agentdojo_run.py` | Create | Arms, suite, tasks, attack, resumable runs, summaries. |
| `pyproject.toml` | Modify | Optional extra `agentdojo` pinned to the installed version; nothing added to the runtime dependencies. |
| `tests/test_agentdojo.py` | Create | The elements against fake runtime and messages: an allowed call passes unchanged, a refusal replaces the call, a flagged result is redacted or withheld, audit records written; skipped when the extra is absent. |
| `docs/agentdojo/*.json`, `docs/agentdojo/README.md` | Create | Native results and per-arm summaries, counts only; how to rerun. |
| `docs/USAGE.md`, `README.md` | Modify | One section each: running the guard in AgentDojo, and the external numbers in the readme's evidence list. |
| `docs/c-completion_reports/CR-008_AgentDojo_Comparison.md` | Create | The table, the counts, the version pins, what was skipped. |

## Dependencies

`agentdojo` as an optional extra, with `transformers` for the detector arm if used. The gateway env file and the lane configuration as before, supplied at run time. The benchmark's environments are self-contained.

## Risks

| Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- |
| The agent model's tool calling sets the ceiling on benign utility | Low | High | The `none` arm is the baseline; every defense is read relative to it, and the CR says so first. |
| The benchmark's tool results are YAML dumps of structured objects, a shape our post-result check has not seen | Medium | Medium | The checkpoint's two-task run shows the unit splitter's behaviour on them; reported, not tuned. |
| Custom pipelines cannot be run through the CLI | Certain | Low | The benchmark module's functions are the documented path; the script wraps them. |
| Machine time | High | Low | Subset sized by the maintainer; sequential, resumable, backgrounded. |
| Version drift in AgentDojo's API | Medium | Low | Pinned extra; the CR records the version. |

## Test plan

Unit: the two elements with fakes, skipped without the extra. Live: the five-task subset, both arms, one job. Manual, recorded: one attacked episode followed through the guard's refusal or redaction to the benchmark's verdict.

## Validation criteria

- The three benchmark numbers per arm and model, with version, suite, tasks, attack and model ids recorded, and the undefended arm beside them.
- The guard's counts per arm from the audit log.
- The comparison table with the published defenses on the same suite and attack, models separated.
- No endpoint, key, hostname or path in any committed artifact; the raw benchmark logs stay ignored.

## Done criteria

- The maintainer approves this plan and the decisions below.
- Changes land on `feat/ip-008-agentdojo` from `main` after the IP-007 merge with the named checks green.
- CR-008 records the numbers, the counts, the pins, the table and what was skipped.
- The independent verifier reruns the offline suite, the unit tests with the extra installed, and reruns one arm on one user task, before the CR is marked complete. One pass, at CR-ready, sized to minutes.

## Constraints for the implementing agent

- Read AGENTS.md and its privacy rule and CR-007 first. Verify the benchmark's API against the installed package before writing the elements; the live package wins over this plan.
- Change nothing in the guard. If the benchmark exposes a defect, record it and stop; the fix is a separate decision.
- Report per arm and per model, never pooled; the undefended arm first.
- No checkpoint. Build, run the one job, write the CR, call it ready. Do not hold for the verifier at any point.

## What follows

With the external number recorded, measurement stops and the outward build begins. IP-009 is IDEA-002's first two items in one plan: document validity, the classify pack on a form, and constraints on a candidate, several noul questions and no new machinery. Both reuse the facts shape IP-006 proved, both are review aids with a person on the verdict, and both are the kind of work the readme's roadmap promises.

## Maintainer decisions (2026-10-11)

1. Five user tasks of the `workspace` suite with their first three injection tasks each, about 20 episodes per arm, after the maintainer rejected the 20-task, four-arm scope as hours of running: "I'm trying to progress onward, not get caught in another hours-long cycle."
2. Two arms: undefended and the guard. The published defenses are deferred.
3. The agent model is a cloud model through the gateway: `claude-haiku-5-5`. The OpenAI and cloud GLM routes on the same gateway are later arms only if asked; the local routes are not used in this plan.
4. The benchmark runs on the Mac in process beside the lane; no sandbox node. No checkpoint; one short job.
5. Branch `feat/ip-008-agentdojo` from `main` after the IP-007 merge.
