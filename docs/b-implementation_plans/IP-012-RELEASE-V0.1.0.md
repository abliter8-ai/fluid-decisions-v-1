---
id: IP-012
title: "Release v0.1.0: sanitise the tree and the history, document the proxy as the usable surface, tag and publish a package"
slug: Release_v0.1.0
status: approved
created: 2026-10-11
updated: 2026-10-11
component: docs, harness, packaging, repository history
platform: both
priority: high
linked_cr: "../c-completion_reports/CR-012_Release_v0.1.0.md"
linked_ideas: "CR-010 (the proxy, the usable surface); IP-011 (file ownership); CR-008 judge arm (the comparison a reader will ask for); AGENTS.md privacy rule"
estimated_crs: 1
author: fluid-decisions verifier session
tags: [release, privacy, history, packaging, readme, tag]
---

> **APPROVED — 2026-10-11.** The maintainer took the decisions: a **new public repository with new history** (amended from the in-place rewrite once the release was confirmed as public), tag and repository release with the index deferred, CI included, judge section and IP-011 landed first. The release is a tag and a built package on the repository, not a calibrated product. The thresholds stay marked uncalibrated and the release notes say so. The history rewrite and the force push in §3 happen only on the maintainer's explicit word in the implementing session at the moment of pushing; approval of this plan authorises the preparation, the verification and the backup, not the push.

# IP-012 — Release v0.1.0

**The matching CR will be:** `CR-012_Release_v0.1.0.md`

## Why

The proxy made the tool usable: an unmodified open-source harness on a local model, with injected instructions withheld, the follow-on exfiltration refused and the task completed. A reader cannot find that out from the README, which does not mention the proxy, and cannot build the container, whose recipe lives outside the repository. The repository also still carries identifying details from the first days, in four evidence files, three documents, one commit message and the history behind them. A release is the point at which those stop being tolerable.

## Objective

After this plan: `main` and its history contain none of the inventoried identifiers; the README's quickstart shows install, proxy and harness in three commands; the container recipe is in the repository; a changelog exists; an annotated tag `v0.1.0` points at the sanitised `main`; a built source distribution and wheel install cleanly in a fresh environment and are attached to a repository release whose notes come from the changelog. Nothing is calibrated that was not calibrated before, and the notes say which numbers the thresholds rest on.

## Current state (grounded 2026-10-11, `main` at `272d63d` locally, `origin/main` at `7861640`)

Inventory of disclosures, by pattern class, with values kept in the private scan and never in this document:

| Class | Current tree | History (added lines / commits) |
|---|---|---|
| Text-lane and audio-lane hostnames | `docs/contract-probe.json`, `docs/hardening-smoke.json`, `docs/ip002-verification.json` | 21 / 7 |
| Gateway hostname | `docs/contract-probe.json`, `docs/ip002-verification.json` | 9 / 7 |
| Fleet node names and tailnet domain | the three files above plus `docs/self-hosted-run.json` | 89 / 10 |
| Maintainer's first name | `IP-001-KICKOFF.md`, `CR-001_Fluid_Decisions.md`, `CR-002_Gate_Hardening.md`; one commit message | 39 / 6 |
| Local username and home-directory paths | `docs/self-hosted-run.json` | 7 / 3 |

No email address, full username or secret value was found in the tree, the history or the commit messages. `examples/lanes.maintainer.yaml` is already a placeholder in the tree; its earlier real contents are in history and fall under the hostname class.

Packaging: `pyproject.toml` at version `0.1.0`, Python 3.11 or later, entry points `fluid` and `fluid-mcp`, a licence file, no changelog, no tags, no CI workflow. `harness/Dockerfile` exists for the trace harness; the proxy container recipe is on the Linux node outside the repository. `git-filter-repo` is not installed; the `gh` CLI is authenticated.

Branches to land before the release: `feat/usable-proxy` (CR-010 and two follow-ups, verified), `feat/ip-008-judge-arm` (in progress), `feat/ip-011-file-ownership` (in progress).

## Proposed changes

### 1. Land everything first

Merge the three branches into `main` in order, each after its verification, and push `main` normally. The rewrite in §3 must run once over a `main` that already contains everything, or it runs twice.

### 2. Sanitise the tree and finish the documents (one commit on `main`)

- **Evidence files.** In the four JSONs replace each lane hostname with `text-lane-host` or `audio-lane-host`, the gateway hostname with `gateway-host`, fleet node names with `node-N`, the tailnet domain with `tailnet.example`, and home-directory paths with `<repo>`. Measurements, status codes and timings stay byte-identical. Each file gets a top-level `"redacted": true` field. Do not regenerate them.
- **Documents.** Replace the first name with "the maintainer" in IP-001-KICKOFF, CR-001 and CR-002, including the IP-001 frontmatter if it carries it.
- **README.** A "Use it with a harness" section near the quickstart: install from the wheel or the repository, start `fluid proxy` with `TEST_API_ENDPOINT`-style upstream and `FD_MAX_URL` lane variables named, point an OpenAI-compatible harness at the loopback address with any key, with OpenCode's provider stanza as the worked example. One paragraph on what the proxy does to tool calls and tool results, pointing at CR-010 for the evidence. Roadmap updated to say what v0.1.0 contains and that calibration is the next line.
- **Container recipe.** `harness/proxy-container/Dockerfile` and `run.sh` brought in from the node, with the environment variable names only, the `--network host` note, and a README line on why.
- **Changelog.** `CHANGELOG.md` with a `v0.1.0` entry: the gate, post-result, hook, hook daemon, MCP, constraints and validity jobs, the proxy, file ownership, the measured numbers from IP-006, IP-007, CR-008 and CR-010 with their caveats, and the statement that thresholds are uncalibrated.
- **CI.** One workflow running the offline suite, ruff and mypy on push and pull request, Python 3.11 and 3.12. No live tests, no secrets.

### 3. New repository, new history

The release is public, so the published history must contain nothing from the old one. A rewrite leaves the old objects on the hosting provider and depends on the replacement list being complete; a fresh history does not. The existing repository stays private as the archive with its full history.

1. On the sanitised `main` from §2, run the disclosure scan over the tree; it must report zero for every class in the inventory table.
2. Author-identity check: nine existing commits carry a personal email address as author. The new history has one author, the neutral identity used on verification commits, so no author rewriting is needed.
3. Create the new repository with the `gh` CLI, private at creation, under the maintainer's organisation account, with the same name or one the maintainer gives.
4. In a fresh clone of the sanitised tree under the scratch directory: `git checkout --orphan` a new `main`, one initial commit of the whole tree with the neutral author and committer identity and a message naming v0.1.0, no trailer that carries an account or session link.
5. Scan the new repository's full history (`git log --all -p` and `--format=%B` and `--format=%an %ae %cn %ce`) with the same patterns plus the author class; all zero.
6. Push `main` to the new repository. No force push exists in this plan; the old remote is never rewritten.
7. Making the new repository public is the maintainer's action in the hosting UI, after he has seen the scan output and the tag.

The private archive keeps the plans, reports and evidence with their lineage; the public repository carries the same documents in one commit, so nothing a reader needs is lost.

### 4. Tag, build and publish

1. Annotated tag `v0.1.0` on the new repository's `main`, message from the changelog entry.
2. In a fresh virtual environment: `python -m build` for the source distribution and the wheel.
3. In another fresh virtual environment: install the wheel, run `fluid --help`, `fluid health` with the lane variables unset (expected to report the lane as unreachable, not crash), and the hook with a static-deny fixture on stdin (expected deny with no lane).
4. A repository release `v0.1.0` on the new repository from the tag, with both artifacts attached and the changelog entry as notes.
5. Package index publication is deferred; the name's availability is checked and recorded in the CR for the next release.

## Out of scope

Calibration. Any threshold change. A package index publication. The numeric pre-pass for constraints. Making the repository public.

## Files to create or modify

- `README.md`, `CHANGELOG.md` (new), `.github/workflows/offline.yml` (new).
- `harness/proxy-container/Dockerfile`, `harness/proxy-container/run.sh` (new).
- `docs/contract-probe.json`, `docs/hardening-smoke.json`, `docs/ip002-verification.json`, `docs/self-hosted-run.json`.
- `docs/b-implementation_plans/IP-001-KICKOFF.md`, `docs/c-completion_reports/CR-001_Fluid_Decisions.md`, `docs/c-completion_reports/CR-002_Gate_Hardening.md`.
- `docs/c-completion_reports/CR-012_Release_v0.1.0.md`.
- Private, never committed: the replacement files and the pre-rewrite bundle.

## Dependencies

CR-008 judge section, IP-011 and the proxy branch merged. `build` in a fresh environment. The authenticated `gh` CLI.

## Risks

- **Two repositories drift.** The private archive is frozen at the release; later work happens in the public repository, and the archive is only history.
- **A placeholder collides with real text.** The replacement file maps exact strings; the verification scan is pattern-based and independent of the mapping, so a miss shows up as a non-zero count.
- **An evidence file loses meaning.** Hostnames become role names; numbers are untouched; the `redacted` flag says what happened.
- **Author identities.** Covered by the single neutral-author commit and the author scan in §3.
- **The tag points at an unsanitised commit.** The tag is created only after the §3 scan reports zero.

## Test plan

Offline suite, ruff and mypy green on the sanitised `main`. The §3 scan at zero. The wheel install checks in §4. The CI workflow green on the tag.

## Validation criteria

- Scan over the rewritten history and tree: zero for every class, counts recorded.
- `pip install` of the wheel in a fresh environment and the three checks in §4 pass.
- Release page shows the tag, both artifacts and the notes.
- README quickstart commands run as written against a loopback proxy.

## Done criteria

Everything in the validation list checked, the CR names the tag, the release, the before and after scan counts, and the location (by role, not path) of the rollback bundle.

## Constraints for the implementing agent

No identifier values in any repository artifact, including the CR's description of what was removed. The replacement files never enter the repository. No force push anywhere. The new repository is created private; only the maintainer makes it public. Nothing installed or configured on the Mac outside the repository's virtual environments and the scratch directory. The bundle is made before the rewrite, every time.

## What follows

Calibration from proxy sessions (IP-007's grid gets ownership and judge columns), then the numeric pre-pass, then a v0.2.0 that is allowed to call its thresholds calibrated.

## Maintainer decisions (2026-10-11)

1. **History: a new repository with a single-commit history from the sanitised tree; the existing repository stays private as the archive.** Amended 2026-10-11 when the maintainer confirmed the release is public. No force push anywhere.
2. **Package: tag plus repository release with attached artifacts.** Index publication deferred.
3. **CI workflow included**, offline suite only, no secrets.
4. **Order: the judge section and IP-011 land first**, then the release.
