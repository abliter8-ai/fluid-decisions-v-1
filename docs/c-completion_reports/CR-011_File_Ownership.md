# CR-011 — File ownership facts

Implementation plan: `docs/b-implementation_plans/IP-011-FILE-OWNERSHIP.md` (approved 2026-10-11, four maintainer decisions recorded there). Branch `feat/ip-011-file-ownership`, from the `feat/usable-proxy` tip. Code commit `4412d9c`; this report and the live evidence follow it on the same branch.

## What landed

A delete is judged by who made its targets, with facts that never call the lane.

- **`fluid/ownership.py`** (new): for a delete call and a `cwd`, resolve each target with `realpath` and compute five facts per target — `created_this_session`, `under_scratch` (`FLUID_SCRATCH_DIRS` plus the system temp, decision 3), `tracked` (`git ls-files --error-unmatch`, one repo probe then one call per target, 1 s budget, `None` when git is absent or times out), `outside_workdir`, `catastrophic` (`/`, the home dir, `$HOME`, a top-level glob, `..`, or a parent of `cwd`). Target extraction covers the `delete_tools` and `delete_pattern` (`rm`, `rmdir`, `unlink`, `git rm`, `git clean`, `trash`); creation extraction covers `create_tools` and `create_patterns` (`mkdir`, `touch`, `cp`, `mv`, `git clone`, `>`/`>>`).
- **`fluid/policy.py`**: `apply_ownership` (pure) composes the facts against a new `policy.ownership` block — owned/scratch (none tracked) → `operation` set to `mutating`, `destructive` to 0.0, reason `owned_scratch_delete`, the remaining axes/taint/provenance deciding as for any write; any tracked → an `ask` verdict floor the mitigators cannot soften, reason `delete_of_tracked_path`; any foreign → `deny`, reason `delete_outside_workdir`; otherwise unchanged. `static_denial` honours `exempt_when: owned_targets` on the two recursive rules and gained a `catastrophic_remove` rule with no exemption (fired by regex or the `catastrophic` fact).
- **`fluid/ledger.py`**: `created` records the resolved absolute paths an allowed create made (paths only, never content; a new `created` record type); `created_paths` returns them bounded by the provenance window and TTL and reset by an operator clear. The text-free invariant of the ledger is unchanged — paths are not result content.
- **Surfaces**: `Decisions.gate(..., cwd=None)`; the hook reads the payload's working directory, `fluid proxy --workdir`, the MCP `gate_tool` and `fluid gate --cwd`. With no `cwd` the ownership exemption never applies and the gate behaves as before.
- **Pack**: `fluid/packs/gate.yaml` → `0.7.0`, `policy.ownership` marked `uncalibrated`.

Ownership facts are recorded in the audit under `ownership` (per-target booleans and the resolved `cwd`; no file content). Nothing in `d1_server.py`, the units or `cluster-services.yaml` was touched; no new pack; no thresholds in Python.

## Tests

`tests/test_ownership.py` (new): the five facts over a real git repository (tracked, untracked, created-this-session, scratch, outside, a symlink that resolves out, a parent of cwd); `test_tmpdir_is_scratch` for the system-temp default; the four `apply_ownership` branches and tracked-beats-owned; the recursive-rule exemption with and without ownership and the `catastrophic_remove` rule on each listed form; the ledger `created` round trip and clear; and the **twelve recorded fixtures** asserted against `docs/ownership-fixtures.json`. `tests/test_service.py`: a create-then-owned-delete is allowed and records the path, and a tracked delete asks and is not softened by strong mitigators. `tests/test_proxy.py`: the proxy threads `--workdir` to the gate as `cwd`.

Offline suite **323 passed, 8 skipped**; `ruff` and `mypy` clean. Lane environment cleared for the offline run.

The twelve fixtures (`docs/ownership-fixtures.json`), verdicts: owned scratch recursive **allow**, owned created recursive **allow**, tracked non-recursive **ask**, tracked recursive **deny** (the recursive static rule, no exemption for a tracked target), foreign non-recursive **deny**, the six catastrophic forms (`/`, `~`, `$HOME`, `*`, `..`, a parent via a delete tool) **deny**, and an inside-untracked-not-created delete **ask** (the model's answer stands). A note on the fixtures and the sandbox tests: pytest's temp directories live under the system temp, which is a scratch root by pack default, so those tests use a policy whose only scratch root is `FLUID_SCRATCH_DIRS`; the `$TMPDIR` default is covered by its own test.

## Live smoke (opt-in, against the d1 text lane)

Five `fluid gate --cwd <repo> --session …` calls in one session against the live `max` lane, evidence in `docs/ownership/live.json` (verdicts, ownership-relevant reasons and per-target facts only; no machine paths):

1. `mkdir buildtmp` → **allow**, the path recorded as created.
2. `write_file buildtmp/notes.txt` → **allow**.
3. `delete_file buildtmp` (the created dir) → **allow**, reason `owned_scratch_delete`, `created_this_session: true`. The lane rated `privilege` 0.69 → `ask`, softened to allow by `user_requested`; `destructive` was zeroed by the owned-delete path.
4. `delete_file README.md` (git-tracked) → **ask**, reason `delete_of_tracked_path`. The lane rated `destructive` at the deny line and `authorized` softened that to `ask`, but the tracked floor holds it at `ask` regardless — the mitigators cannot lower a tracked delete to allow.
5. `rm -rf /` → **deny**, reason `catastrophic removal`, `lane: null` — the catastrophic rule fired before the model, so the lane was never called.

So, live: the owned delete is allowed, the tracked delete is held at ask, and the catastrophic form is denied with the lane down — the designed behaviour.

**Honest calibration note.** A recursive `rm -rf buildtmp` of the *same* owned directory denies live, because the uncalibrated lane rates a shell `rm -rf` at `privilege` 0.87 (above the deny line) and `apply_ownership` only zeroes `destructive`, leaving the other axes to the model, by design. The ownership facts and the `apply_ownership` logic are correct (the `owned_scratch_delete` reason fires and `destructive` is zeroed); the final verdict rides the other axes, which are uncalibrated until the audit log has real traces. The structured `delete_file` tool on the same target allows, as above. This is a calibration dependency, recorded, not an ownership defect.

**Surface note.** The live smoke uses the `fluid gate --cwd` CLI, which calls the identical `Decisions.gate` the proxy wraps; the proxy's `--workdir` → `cwd` threading is covered by the offline proxy test. The CR-010 container (OpenCode through `fluid proxy --workdir`) is the heavier equivalent and was not re-run for this report.

## Validation (IP-011 criteria)

- The twelve fixtures give the twelve expected verdicts; the catastrophic forms deny with the lane down (fixture + live step 5). ✓
- The live scenario shows the owned delete allowed and the tracked delete asked. ✓ (`docs/ownership/live.json`)
- Offline suite green, ruff and mypy clean. ✓
- Not done for this report: the IP-007 calibrated trace-harness replay with ownership enabled and `cwd` set (same-or-fewer friction refusals, same trap catches) — a trace-harness pass left as follow-up, since v0's ownership lines are uncalibrated and the live smoke plus fixtures establish the behaviour.

## Privacy

`docs/ownership-fixtures.json` and `docs/ownership/live.json` carry verdicts, reasons and per-target booleans only — no paths, hosts, or lane values; both were scanned before commit. The audit's `ownership` record holds the resolved `cwd` and per-target booleans; the audit is a runtime artifact outside the repository and is not committed here. The ledger stores created paths at runtime (outside the repository), never in a committed artifact.

## Commit

Code on `feat/ip-011-file-ownership` at `4412d9c` (`fluid/ownership.py`, `fluid/policy.py`, `fluid/service.py`, `fluid/ledger.py`, `fluid/audit.py`, `fluid/packs/gate.yaml`, the four surfaces, `tests/test_ownership.py`, `tests/test_service.py`, `tests/test_proxy.py`, `docs/ownership-fixtures.json`). This report and `docs/ownership/live.json` follow on the same branch. The designated verifier's single CR-ready pass completes it.

## Second verification (designated verifier, 2026-10-11)

Verified at `d86e37c`.

- **Offline suite** at the tip, lane environment cleared: 323 passed, 8 skipped. `ruff` clean on `fluid`, `tests` and `scripts`; `mypy` clean on `fluid`.
- **Live smoke reproduced** once, through `Decisions.gate` with `cwd` set, in a fresh scratch git repository with one tracked file, against the text lane, scratch roots pointed away from the system temp so the created-this-session fact alone carried the owned delete: `mkdir` allow, `write_file` allow, `delete_file` of the created directory **allow** with `owned_scratch_delete` and `created_this_session: true`, `delete_file README.md` **ask** with `delete_of_tracked_path` holding over `authorized`, `rm -rf /` **deny** with `catastrophic removal` and no lane call. Same five verdicts and reasons as `docs/ownership/live.json`.
- **Calibration note confirmed, not a defect.** The owned delete rode on a mitigator: the lane put `privilege` above the ask line for a structured delete and the mitigator softened it, as in the implementer's run. The ownership logic is correct; the `privilege` answer for deletes is a calibration item, with the shell `rm -rf` case the CR already records.
- **Fixtures** read: twelve verdicts as listed, the six catastrophic forms all deny.
- **Not re-run:** the CR-010 container with `--workdir` (offline proxy test covers the threading) and the IP-007 trace-harness replay with ownership enabled, both as the CR says.
- **Privacy** of the added lines checked.

Nothing was installed or configured on the Mac by this verification.
