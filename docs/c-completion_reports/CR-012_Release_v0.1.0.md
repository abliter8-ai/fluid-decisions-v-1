# CR-012 — Release v0.1.0

Implementation plan: `docs/b-implementation_plans/IP-012-RELEASE-V0.1.0.md` (approved 2026-10-11, amended the same day). The amendment replaced the in-place history rewrite and force-push with a **new public repository built from a fresh single-commit history**; the existing repository stays private as the archive; no `git-filter-repo` and no force-push anywhere. Making the new repository public is the maintainer's own action in the GitHub UI after reviewing the scan and the tag — not performed here.

## What was done

### §1 Land everything (archive repository)
The three feature branches were integrated onto `main` linearly and pushed to the existing private repository normally (fast-forward): `feat/usable-proxy` (proxy, recovery loop, static-withhold fix), `feat/ip-008-judge-arm` (the judge arm), `feat/ip-011-file-ownership` (file ownership), each verified, plus the verifier's IDEA-006/IP-011/IP-012 doc commits. Offline suite **330 passed, 8 skipped**; ruff and mypy clean on the integrated tree.

### §2 Sanitise the tree and finish the documents (one commit on the archive `main`)
- Four evidence JSONs: lane and gateway hostnames → `text-lane-host` / `audio-lane-host` / `gateway-host`, fleet node names → `node-N`, the tailnet domain → `tailnet.example`, an IP → a TEST-NET address, home paths → `<repo>`; measurements, status codes and timings byte-identical; each file gained `"redacted": true`.
- Three early documents (IP-001-KICKOFF, CR-001, CR-002) and one residual example sentence in CR-005: the maintainer's first name and the literal example paths replaced with role language.
- README gained a "Use it with a harness" section (install, `fluid proxy`, loopback, the OpenCode provider stanza, a pointer to CR-010) and a v0.1.0 roadmap note; `CHANGELOG.md` added for v0.1.0 with the uncalibrated-thresholds statement; the offline CI workflow (`.github/workflows/offline.yml`, Python 3.11/3.12, ruff + mypy + pytest, no secrets); and the `harness/proxy-container/` recipe (env-var names only, the `--network host` note).

### §3 New repository with a fresh single commit
- The sanitised tracked tree was exported with `git archive` and committed as **one orphan commit** in a fresh repository, author and committer the neutral identity `Repository Automation <automation@example.invalid>`, message naming v0.1.0, no session-link trailer.
- The new repository `abliter8-ai/fluid-decisions-v-1` was created **private** and the single commit pushed. It remains private; the public flip is the maintainer's.

### §4 Tag, build, publish (private release)
- Annotated tag `v0.1.0` on the single commit.
- `python -m build` in a fresh virtual environment produced `fluid_decisions-0.1.0-py3-none-any.whl` and `fluid_decisions-0.1.0.tar.gz`.
- In a second fresh virtual environment the wheel installed and the three checks passed: `fluid --help` (exit 0); `fluid health` graceful both ways (reports `ok` when a local lane is up, `lane_down` with no crash when the lane is unreachable); the hook on a static-deny fixture returned `deny` / `catastrophic_remove` / `static: true` with no lane.
- Release `v0.1.0` created on the new repository with both artifacts attached and the changelog entry as notes: `https://github.com/abliter8-ai/fluid-decisions-v-1/releases/tag/v0.1.0`.
- Package index publication deferred; the name `fluid-decisions` is available on PyPI (checked, HTTP 404), recorded for the next release.

## Scan counts (values kept in the private scan, never here)

Pattern scan over added diff lines, commit messages and author/committer fields (`%an %ae %cn %ce`).

| Class | Before (archive full history) | After (new repository full history) |
|---|---|---|
| Lane and gateway hostnames | 21 | 0 |
| Fleet node names | 92 | 0 |
| Tailnet domain | 21 | 0 |
| Maintainer first name | 41 diff + 1 message | 0 |
| Home-directory paths | 18 | 0 |
| Personal email in author/committer | 16 (author fields) | 0 |
| **Total** | **210** | **0** |

The author-field class is why the amendment matters: the archive's early commits carry a personal email as author, which no in-tree edit removes; the new repository's single commit is authored by the neutral identity, so the after count is zero including authors. The after scan was run over the new repository's complete history.

## Archive and rollback

The existing private repository `abliter8-ai/fluid-decisions` is the archive; it retains the full development history (including the identifiers in its history, by design — it stays private). No bundle was needed because nothing was rewritten or force-pushed; the archive is itself the rollback. Both the archive `main` and the new repository can be re-derived from each other's sanitised tree.

## Validation (IP-012 amended criteria)

- Scan over the new repository's whole history and tree: **zero for every class**, counts above. ✓
- `pip install` of the wheel in a fresh environment and the three checks pass. ✓
- Release page shows the tag and both artifacts with the changelog notes. ✓
- README quickstart/harness commands are written against a loopback proxy with env-var names only. ✓
- Not performed here: flipping the new repository to public (the maintainer's UI click after review).

## Commit

This report on the archive `main`. The release artifacts and the single-commit history are on `abliter8-ai/fluid-decisions-v-1`. The verifier verifies by cloning the new repository fresh, re-running the scan over its whole history, and installing the released wheel.

## Second verification (designated verifier, 2026-10-11)

Verified against the new repository at its tag `v0.1.0` (one commit, `760808e`) and the release page.

- **Fresh clone** of the new repository, still private at the time of verification. One commit, one author and committer, the neutral identity.
- **Full-history scan** of that clone with the inventory's pattern classes plus personal email addresses and the author and committer fields, over every added line, every commit message and the tracked tree: zero in every class, total zero.
- **Release assets**: the wheel and the source distribution are attached to `v0.1.0` with the changelog as notes.
- **Install test** in a fresh virtual environment from the downloaded wheel: `fluid --help` exits 0; `fluid health` with the lanes pointed at an unreachable address reports `lane_down` for both lanes and exits cleanly; the hook with a static-deny fixture on stdin and the lane unreachable returns `deny` with `catastrophic_remove`, static, no model call. Pack version 0.7.0.
- **Published tree sweep**: no inspiration directory, no real lanes file (the placeholder only), no private key, API key or bearer token pattern. The trap overlay's fixture env holds a fake value by design. `.env.example` carries localhost defaults and variable names only.
- **README** carries the proxy quickstart; **CHANGELOG** states the thresholds are uncalibrated.
- **Not done by anyone yet, by design:** making the new repository public. That is the maintainer's action in the hosting interface.

Nothing was installed or configured on the Mac outside fresh virtual environments in the scratch directory.
