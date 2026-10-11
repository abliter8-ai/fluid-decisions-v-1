# AGENTS.md

You are the implementing agent for fluid-decisions. Build v0 yourself. Do not wait for a human to approve each file. Stop only for a real blocker: a lane you cannot reach, a contract the server does not match, or a decision that would change the product.

## Privacy — required for every repository artifact

- Do not write the maintainer's name, personal aliases, usernames, email addresses, hostnames, device names, private IP addresses, tailnet names, internal domains or URLs, account identifiers, or identifying filesystem paths into repository code or documentation. This includes these instruction files, comments, examples, configuration defaults, tests, fixtures, plans, completion reports, generated evidence, logs, screenshots and their metadata.
- Treat this rule as applying whether the repository is public or private. Access to local configuration, a tool response, an earlier document or private conversation is permission to use it for the task, not to copy identifying details into the repository. Existing disclosures are not examples to follow.
- Use `the maintainer`, `text lane` and `audio lane` in prose; use repository-relative paths or generic placeholders in examples. Keep actual endpoints and machine paths in environment variables or ignored local configuration. Refer to `FD_MAX_URL`, `FD_OMNI_URL` and `FLUID_LANES_FILE` by name, never by their private values. Public upstream model IDs and project-relative filenames may be retained.
- Sanitize generated evidence before writing a repository artifact. Inspect nested JSON, health responses, command lines, stack traces, process listings, service labels and temporary paths. Keep necessary raw captures outside the repository or in a verified ignored local directory. Preserve measured results and label redactions; never fabricate evidence. Do not commit a mapping back to the real identities.
- Before committing or publishing, inspect the added lines and complete new artifacts for identifying details, as well as secrets; use privately available identifiers for the check without echoing them into reports. Check commit messages and publication metadata too. Fix any disclosure before proceeding. A credential-only scan is insufficient. Describe any remaining historical exposure without repeating its values or claiming history was cleaned.

## What this repo is

A community tool with a client, a policy, adapters, and an optional self-hosting setup. d1 is not a chat model. It answers typed questions about a state in one forward pass and returns probabilities. The client and worker adapters call HTTP endpoints; only the separate optional serving process loads weights.

The maintainer's cluster, models, and gateway are the development and test environment. A community user must be able to clone the repo, download models from Hugging Face with included scripts, follow included serving instructions, and use Fluid Decisions without access to that infrastructure. Keep public defaults on localhost or user-configured endpoints. Ship the download scripts, serving setup, and user documentation inside this Git repository.

The model never decides the action. Policy code does. The hook is the surface that matters, because the runtime owns it. MCP is for a worker that cannot wrap the tool call. An agent can skip a tool. It cannot skip a hook.
**The repo is 'fluid-decisions'.**

For maintainer testing, use the separately supplied local `.env.test` for the API gateway endpoint and virtual key. Pass its location locally via `--env-file`; never record its absolute path or values, commit it, or require it for community use.

Prioritise testing on Qwen and Abliter8/GLM* models as these are local models - use OpenAI Codex or Anthropic Claude models sparingly for comparison only.

The project readme is `README.md` at the repository root.

## Read before you write

1. `skills/liquid-d1/SKILL.md` and `skills/liquid-d1/references/api.md`. If they disagree with a live response, the live response wins. Update the skill note in the completion report. Do not rewrite the skill to match a guess.
2. `docs/b-implementation_plans/IP-001-KICKOFF.md` and the approved follow-ups `IP-002-HARDENING.md` and `IP-003-UNSCORED-RESULTS.md`. These define the implementation scope. `docs/a-ideas/` is backlog; do not start an idea without an approved plan.
3. The maintainer's parent workspace holds read-only copies in `docs/z-inspiration/`; they are not distributed with this repo. Steal the pattern: policy outside the model, static rules first, session ledger, post-result injection check, compaction that keeps text verbatim. Do not import a TypeSafe client. Do not commit those trees. Do not copy Jev thresholds.

## Maintainer test lanes

| Endpoint configuration | Model | Use |
|------|--------|-----|
| `FD_MAX_URL` | `LiquidAI/d1-3B` | text, text+image. Default. |
| `FD_OMNI_URL` | `LiquidAI/d1-omni-600M` | audio, one clip, 30 s. Never with an image. |

These private endpoints are an opt-in test configuration, not public defaults. Override with `FD_MAX_URL` and `FD_OMNI_URL`, or `FLUID_LANES_FILE`. The `model` field is ignored by the existing servers. Do not route by it. Users may run d1-3B alone for text/images and add omni for audio.

Images and audio on one request return 422. Reject that in the client before send.

A small warm call has measured about 130–250 ms. A new shape on the text lane has been 4.9 s. Warm the persistent client at process start before a hook blocks a tool. Gate calls retain the first/warm timeout split; post-result calls use the measured cost model, YAML margin and cap, or the explicit `FD_POST_TIMEOUT` override.

## Selectable gate packs and the hook daemon (IP-006/IP-007)

As of IP-007 the default gate is the **facts pack** `gate.yaml` (0.6.0): the facts gate, the two-stage unit post-result, read-only axis zeroing, proportionate `carries_out` taint, the withheld-path re-read denial, pending recheck, and confirmed-unit redaction. `FD_GATE_PACK` selects a different pack when the service loads `gate`: `gate-packet` is the frozen flat 0.4.0 packet, byte-identical to the pre-IP-007 default, kept as the harness baseline and the fallback. (`gate-facts.yaml` is gone — folded into `gate.yaml`.) A model-free provenance floor and a per-pack budget `floor_s` are always on in both packs. Pack thresholds and post-result lines are calibrated against `false_flag_budget` by `scripts/calibrate_lines.py` (`calibration: two-models-one-repository`); `taint_carry_line` stays uncalibrated until the confirmation arm. The persistent hook daemon is `python -m fluid.hookd`, serving the hook's JSON contract over a Unix socket at `FLUID_HOOK_SOCKET` (default `${FLUID_DATA_DIR}/hookd.sock`); `fluid.hook` falls back in-process when the socket is absent. Refer to these by name, never to private socket or data-dir values.

## How to work

Build in the order the kickoff plan gives. Probe, then client, then policy, then packs, then hook, then MCP. Ship community download/serving scripts and instructions, with offline adapter tests. The maintainer already serves both models: do not download or serve duplicate copies for acceptance. Do not start from the MCP schema.

- Phase 0 is recorded in `docs/contract-probe.json` from both lanes (2026-10-09). Noul is `{"type":"noul","noul":p}`. Preserve the captured fixtures; live responses remain the source of truth.
- Policy is a pure function. Unit tests use recorded answers and do not touch the network.
- Static denies (`rm -rf`, `git push --force`, `DROP TABLE`) run before the model, and still run if the lane is down.
- Gate fail mode is ask, not allow. Secret exposure, constraint violations, and unresolved choices are never softened because the user asked for the action.
- A post-result timeout is unscored, not a flag. Apply YAML `on_unscored`, retain pending hashes before release, and expose `checked: false`. Static/model flags still withhold; failed ledger or audit persistence still withholds. Recheck requires original content supplied by file or stdin and verified against a pending hash.
- Return the full distribution. A noul at 0.62 against a 0.70 line must be visible. Confidence and the top probability are different fields. For a noul, also expose decisiveness \(2 \lvert p - 0.5 \rvert\).
- One request, all questions. Do not make one call per question.
- Audit log stores verdicts, probabilities, pack version, lane, and latency. Not the raw state, unless `FLUID_AUDIT_STATE=1`.
- When a phase lands, write a short report in `docs/c-completion_reports/`. Say what was tested, against which lane, and what was skipped.

You may add files the plan did not name if a test needs them. You may not add a pack the plan did not name.

## Tests

Unit tests run with no network and must pass before you call a lane.

Live tests are opt-in: `FD_LIVE=1`. If the tailnet names do not resolve, skip them and say so in the report. Do not mock a live test and call it live.

Against the lanes, prove at least:

- HTTP 200, `output_tokens` 0, on both hosts, for choice, score, and noul.
- Image on the text lane. Audio under 30 s on the audio lane. Image plus audio is 422.
- A batch of two.
- Second call after warmup under 1 s. Do not assert the 65 ms card figure.
- A static-deny fixture returns deny with the lane unreachable.
- Verify flags a claim that contradicts the source and does not write a replacement sentence.

The two lanes disagree. d1-3B has returned 0.99 where omni returned 0.64 on the same payload. Record that. Do not average them. Do not fail a test because confidences differ.

## Do not

- Do not modify the existing fleet `d1_server.py`, launchd unit, systemd unit, or `cluster-services.yaml`. A portable optional server in this repo is in scope; it must use upstream model APIs and pass the same contract probes.
- Do not add a `/v1/chat/completions` shim.
- Do not fine-tune. There is no published recipe, and v0 does not need one.
- Do not summarize. Compaction is phase 2, and it keeps text verbatim.
- Do not send audio to the text lane.
- Do not commit `docs/z-inspiration/`.
- Do not treat a clean classify exit as permission to run a postinstall hook.
- Do not put thresholds in Python. They live in policy YAML, marked uncalibrated until the audit log has real traces.
- Do not page, push, or delete anything on the fleet as part of a test. The gate tests use fixtures and recorded tool calls, not live destructive commands.

## Done

v0 is done when the kickoff plan's validation list is checked or explicitly waived, the unit tests pass, the live smoke has been run or explicitly skipped, and the completion report names the commit. Community portability requires repository download/serving machinery, instructions and localhost/user-owned endpoint configuration. Use the maintainer's existing services for routine tests. IP-002 authorized one bounded adapter run, now recorded in `docs/self-hosted-run.json`; it does not authorize future duplicate serving or downloads.

## Named checks

From the Git root, after installing `.[dev,mcp]` in `.venv`:

```sh
.venv/bin/python -m ruff check fluid scripts tests examples harness
.venv/bin/python -m mypy fluid
.venv/bin/python -m pytest -q
.venv/bin/python -m build
```

The `harness` package (IP-005) is ruff-checked with the rest and is independently mypy-clean; the named mypy check remains `fluid`. The real-trace harness is opt-in (`FD_LIVE=1`) and runs tools only inside a no-network container on a Linux node; see [docs/HARNESS.md](docs/HARNESS.md).

Live checks require `FD_LIVE=1` and explicitly configured `FD_MAX_URL` / `FD_OMNI_URL`: `.venv/bin/python -m pytest tests/test_live.py -q`. Worker smoke takes an explicit `--env-file` and lane file; never commit credential values. Give thinking Qwen/GLM routes a sufficient token budget (the smoke uses 8192) or select an available NoThink route. An empty HTTP 200 completion requires checking finish reason and reasoning usage.

## Evolution Log

- 2026-10-09 — IP-003 separates scored flags from unscored results. Pending hashes protect later actions; exact-content recheck appends resolution. Post-result budgets derive from warmup measurements with YAML limits. New evidence uses lane roles only and excludes identifying infrastructure details.
- 2026-10-09 — IP-001 added the lightweight HTTP client, pure YAML policy, four jobs, runtime wrapper, CLI, hook, MCP, audit/ledger, and optional public model download/serving tools. Existing fleet services remain the live test targets. A new serving process was explicitly excluded from maintainer acceptance; no duplicate instance was started.
- 2026-10-09 — IP-002 added graded taint, audited clearance, complete post-result windowing and warm-call assertions. Weak nested-session influence was measured; a dedicated session question is batched with the gate and audited. The separately approved adapter run used the existing snapshot, passed, and stopped. See CR-002 for independent verification and platform limits.
- 2026-10-10 — IP-004 adds optional question scopes and completed both lane measurements, independent replay/privacy, the live module, the corrected 19-check smoke and final named checks. No scope or window was adopted: shipped packs remain unscoped at their existing versions and the 12,000-character window remains. The maintainer deferred the fixture-label review to a later remediation on 2026-10-10 and merged the branch; the designated verifier's pass is recorded in CR-004.
- 2026-10-10 — IP-005 budgets every model call from the measured cost model, not just post-result. The client gains `request_budget` with a per-question warmup term (`ms_per_question`) and an optional `FD_CALL_TIMEOUT` override; gate, classify, route and verify carry a `budget` object and decline to send a call whose expected cost exceeds the YAML cap, returning `ask` with reason `exceeds_budget`. Static denials still run first. Pack versions: gate `0.4.0`; classify, route, verify `0.2.0`; new `policy.budget` blocks are uncalibrated. The repository adds the `harness` package: a sandboxed agent loop on a pinned public repository with planted, inert, clearly-fake traps, run inside a no-network container on a Linux node, producing real decision traces and a curated export in the IP-004 fixture schema. The harness changes no threshold, pack or axis.
