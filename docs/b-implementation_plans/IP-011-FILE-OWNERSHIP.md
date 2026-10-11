---
id: IP-011
title: "File ownership facts: a delete of what the agent made this session is judged on context, a delete of what it did not is asked, the catastrophic forms stay statically denied"
slug: File_Ownership
status: approved
created: 2026-10-11
updated: 2026-10-11
component: packs, policy, ledger, service, hook, proxy, mcp
platform: both
priority: high
linked_cr: "../c-completion_reports/CR-011_File_Ownership.md"
linked_ideas: "IP-006 (facts composed in policy); IP-007 (proportionate taint, read-only axis zeroing); CR-010 (usable proxy, the surface this serves first); the maintainer's own blanket delete rule in a harness, which leaves scratch files behind"
estimated_crs: 1
author: fluid-decisions verifier session
tags: [ownership, delete, facts, ledger, gate, proxy, hook]
---

> **APPROVED — 2026-10-11.** The maintainer said "let's add the file ownership piece" after the explanation below; the four decisions at the end were taken as recommended on that go and are recorded. Lean cadence: no checkpoint, one verification at CR-ready sized to minutes. Branch from the `feat/usable-proxy` tip after the recovery-loop follow-up lands, because the proxy is the first surface that uses this.

# IP-011 — File ownership facts

**The matching CR will be:** `CR-011_File_Ownership.md`

## Why

A harness that blocks every delete is safe and useless in the same breath: the agent cannot remove a scratch directory it made ten minutes ago, so it leaves duplicates and stale files behind, and the operator stops reading the prompts. The gate already separates the irreversible forms (static denies) from everything else (contextual judgement), but it knows nothing about files. It cannot tell a delete of the agent's own scratch output from a delete of a tracked source file, so the model's `destructive` answer, which is high for any `rm`, decides both the same way.

The facts that separate those cases are deterministic and cheap: did this session create the path, is it tracked by git, is it under a scratch root, does it resolve outside the working directory. None needs the model. They are composed in policy, like every fact since IP-006, and the thresholds stay in YAML.

## Objective

After this plan a delete whose every target the session created, or that lies under a scratch root, and that is not tracked by git, is judged as a `mutating` action on the ordinary axes and allows when they are clear. A delete of a tracked file, or of a path the session did not create inside the working directory, lands at `ask` by policy, never softened to allow by the model's mitigator answers. A delete of a path outside the working directory that the session did not create is denied. The catastrophic forms (root, home, `$HOME`, `*`, `..`, a parent of the working directory) stay statically denied regardless of ownership. All of this holds with the lane down, because every ownership fact is computed without it.

## Current state (grounded 2026-10-11, `feat/usable-proxy` at `7861640`)

- `fluid/packs/gate.yaml` 0.6.0: `policy.facts` composes `operation` from `tool_operation` for known tools, else from the model; `static_denials` deny `rm -r`/`rm -rf` in any form, force push and table drops before the model.
- `fluid/policy.py` `compose_gate_facts` is pure and zeroes the action axes for read-only operations (IP-007 §1). `static_denial` runs first in `Decisions.gate`.
- `fluid/ledger.py` records flags, pending results, observed results, withheld inputs and provenance spans per session. It records nothing about files the session wrote.
- Surfaces: library `Decisions.gate(tool_name, tool_input, user_request, session_id)`, the stdin hook and `fluid.hookd`, the MCP server, and `fluid proxy` (CR-010). None passes a working directory to the gate.

## Proposed changes

### 1. Ledger: what this session created

`Ledger.created(session_id, paths, *, tool_name)` appends a `created` record with the resolved absolute paths. `Ledger.created_paths(session_id)` returns the set (bounded by the existing window and TTL). `Decisions.gate` records on an `allow` verdict for a tool whose name is in a new `facts.create_tools` list (`write_file`, `Write`, `create_file`, `mkdir`) using the pack's `path_fields`, and for a command matching a new `facts.create_patterns` list that captures the destination of `mkdir`, `touch`, `git clone`, `cp`, `mv` and a `>` redirection. Nothing else is recorded. The record carries the paths only, not content.

### 2. Ownership facts in the service, before the lane

For every tool call whose tool is in a new `facts.delete_tools` list (`delete_file`, `remove`, `rmdir`) or whose command matches a new `facts.delete_pattern` (`rm`, `rmdir`, `unlink`, `git rm`, `git clean`, `trash`), the service resolves each target against a `cwd` and computes, per target:

- `created_this_session`: the resolved path is, or is under, a `created_paths` entry.
- `under_scratch`: under one of `facts.scratch_roots`, which expands `FLUID_SCRATCH_DIRS` (colon-separated) and the system temporary directory.
- `tracked`: `git ls-files --error-unmatch` from `cwd` exits 0, with a one-second subprocess budget; `None` when `cwd` is not a repository or the call times out.
- `outside_workdir`: the resolved path is not under `cwd`.
- `catastrophic`: the target is `/`, the home directory, `$HOME`, a glob `*` at the top of any of those, `..`, or a parent of `cwd`.

`cwd` comes from a new keyword on `Decisions.gate(..., cwd=None)`; the hook reads it from the hook payload's working directory field, the proxy takes `--workdir`, the MCP tool takes an optional `cwd` argument. With no `cwd` every fact that needs one is `None`.

The facts go into the `facts` record the audit already keeps, under `ownership`.

### 3. Policy: compose the facts, thresholds in YAML

A new pure function `apply_ownership(axes, ownership, policy)` runs after `compose_gate_facts` and before `evaluate_gate`, driven by a new `policy.ownership` block marked `calibration: uncalibrated`:

```yaml
ownership:
  calibration: uncalibrated
  enabled: true
  owned_delete: contextual      # every target created this session or under scratch, none tracked
  tracked_delete: ask           # any target tracked by git
  foreign_delete: deny          # any target outside the working directory the session did not create
  unknown_delete: unchanged     # no cwd or no facts: the model's answers stand
```

- `contextual`: `operation` is set to `mutating` and `destructive` to 0.0, reason `owned_scratch_delete`. The other axes, taint, provenance and constraints apply as they do to any write. The verdict is whatever they give.
- `ask`: the verdict floor is `ask`, reason `delete_of_tracked_path`, and the mitigators (`authorized`, `user_requested`) cannot soften it to allow. The user's request can still be honoured by the person answering the ask.
- `deny`: reason `delete_outside_workdir`.
- `catastrophic` on any target: handled by the static rule in §4, never here.

### 4. Static denies: the catastrophic forms stay, the owned recursive delete does not

The existing `recursive_force_remove` and `recursive_separate_flags` rules currently deny every recursive delete. They get an `exempt_when: owned_targets` field: when every target of the command resolves to `created_this_session` or `under_scratch`, none is `tracked`, and none is `catastrophic`, the rule does not fire and §3 judges the call as an owned delete. A new rule `catastrophic_remove` with no exemption denies the forms listed under `catastrophic` in §2 before anything else. Force push and table drops are untouched. With no `cwd` the exemption never applies, so the hook and MCP behave as today until they pass one.

### 5. Surfaces

- Hook and `fluid.hookd`: pass the payload's working directory as `cwd`.
- `fluid proxy`: `--workdir <path>` (default: the proxy's own working directory), passed as `cwd` on every gate; allowed write and create calls recorded per session.
- MCP `gate`: optional `cwd`.
- CLI `fluid gate --cwd`.

## Out of scope

Deletes of remote resources, database rows and cloud objects. A trash or archive step in place of a delete. Ownership of files created before the session started. Any change to the thresholds in `risk` and `mitigators`.

## Files to create or modify

- `fluid/packs/gate.yaml`: `facts.create_tools`, `facts.create_patterns`, `facts.delete_tools`, `facts.delete_pattern`, `facts.scratch_roots`, `policy.ownership`, the `exempt_when` field and the `catastrophic_remove` rule; version `0.7.0`.
- `fluid/ledger.py`: `created`, `created_paths`.
- `fluid/ownership.py` (new): path resolution, the five facts, the git lookup with its budget.
- `fluid/policy.py`: `apply_ownership`; `static_denial` honours `exempt_when`.
- `fluid/service.py`: `cwd` keyword, facts computed before `static_denial`, creation recorded on allow.
- `fluid/hook.py`, `fluid/hookd.py`, `fluid/proxy.py`, `fluid/mcp_server.py`, `fluid/cli.py`: pass `cwd`.
- `tests/test_ownership.py` (new), additions to `tests/test_policy.py`, `tests/test_proxy.py`.
- `tests/fixtures/ownership/` (new): twelve recorded scenarios.
- `docs/ownership-fixtures.json`: the twelve verdicts with reasons.

## Dependencies

CR-010 and its recovery-loop follow-up on `feat/usable-proxy`. `git` on the path for the tracked fact; its absence yields `None`, not an error.

## Risks

- A creation the ledger did not see (a script that writes files, an editor) leaves the file "not created this session"; the delete then lands at `ask`, which is the safe side. Recorded as a known gap, not a defect.
- Path tricks: symlinks and `..` are resolved with `realpath` before any fact is computed; a target that fails to resolve is treated as foreign.
- The git subprocess on a large repository: one call per target, one-second budget, `None` on timeout; the fact is then unknown and the model's answers stand.
- The scratch root exemption is only as safe as the roots; `FLUID_SCRATCH_DIRS` is the operator's choice and the system temporary directory is the only default.

## Test plan

Unit, no network: the five facts over a temporary directory with a real git repository inside it (tracked, untracked, created-this-session, scratch, outside, symlink out, `..`, parent of cwd); `apply_ownership` for each policy branch; `static_denial` with and without the exemption; the catastrophic rule on each listed form; the ledger `created` round trip; the proxy recording a create and then allowing the matching delete in the same session with a fake `Decisions`. Twelve recorded fixtures give the twelve verdicts in `docs/ownership-fixtures.json`.

Live, opt-in: through `fluid proxy` with the gateway and the text lane, an agent that makes a scratch directory, writes a file in it, deletes the directory (`allow`, reason `owned_scratch_delete`), then is asked to delete `README.md` (`ask`, reason `delete_of_tracked_path`). Minutes.

## Validation criteria

- The twelve fixtures give the twelve expected verdicts; the catastrophic forms are denied with the lane down.
- The IP-007 calibrated arm on the trace harness, replayed with ownership enabled and `cwd` set, gives the same or fewer friction refusals and the same trap catches.
- The live proxy scenario shows the owned delete allowed and the tracked delete asked, in the proxy log.
- Offline suite green, ruff and mypy clean.

## Done criteria

Everything in the validation list checked, the CR names the commit and the fixture file, and the proxy evidence under `docs/ownership/` is sanitised to the privacy rule.

## Constraints for the implementing agent

No thresholds in Python. No new pack. The ownership facts never call the lane. `realpath` before every comparison. The catastrophic rule has no exemption of any kind. Record paths, never content, in the ledger. Nothing installed or configured on the Mac; the live scenario runs in the CR-010 container on the Linux node. Privacy rule on every artifact.

## What follows

Once the proxy has carried real sessions, `scripts/calibrate_lines.py` gets the ownership reasons as a column, and the `owned_delete`/`tracked_delete` choices can be revisited on evidence. A trash-instead-of-delete option is a separate small plan.

## Maintainer decisions (2026-10-11)

1. **Recursive delete of owned scratch targets is allowed through the exemption; the catastrophic forms never are.** Taken as recommended.
2. **A tracked file's delete is `ask`, not `deny`, and the mitigators cannot soften it.** Taken as recommended.
3. **Scratch roots are `FLUID_SCRATCH_DIRS` plus the system temporary directory, nothing else by default.** Taken as recommended.
4. **Branch `feat/ip-011-file-ownership` from the `feat/usable-proxy` tip after the recovery-loop follow-up.** Taken as recommended.
