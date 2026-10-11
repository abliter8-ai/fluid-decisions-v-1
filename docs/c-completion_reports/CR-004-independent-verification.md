# IP-004 independent verification

Status: independent implementation verification passed: text confirmation, manual
hook checks, offline checks, the full live module, both-lane evidence inspections
and all 19 hardening checks. Maintainer fixture-label review remains pending.

Implementation reviewed: `1e9d4bf`, plus the wrapper-only hardening timeout
correction in `f4972a8` described below; initial implementation: `9510dfe`.
Plan: `docs/b-implementation_plans/IP-004-QUESTION-SCOPING.md`.
The shipped packs remain unchanged: no scopes adopted, gate `0.3.0`, post-result
window 12,000 characters. This report does not claim a measured improvement shipped.

## Independently executed checks

| Command or scenario | Observed result |
| --- | --- |
| `.venv/bin/python -m pytest -q` | 143 passed, 5 skipped, 3 subtests passed in 52.17 seconds. Live opt-in, lane configuration and timeout overrides were cleared; `PYTHONPATH=.` was set. |
| `.venv/bin/python -m ruff check fluid scripts tests examples` | All checks passed. |
| `.venv/bin/python -m mypy fluid` | No issues in 11 source files. |
| `.venv/bin/python -m build` | Source distribution and wheel built successfully using the normal isolated build environment. |
| Real stdin `pre_tool`, clear read | `allow`; 12 answers, one whole-state group, zero output tokens; client latency 361.9 ms. |
| Real stdin `pre_tool`, pending-session mutation | `ask`; 12 answers, main plus session groups, zero output tokens; client latency 560.1 ms. Reasons include `user_requested_softened_ask`, followed by `session_pending_check` and `session_taint_active`, demonstrating the pending floor. |
| Manual audit readback | Both audit records contain scoping groups and no raw state, tool input, user request or supplied fixture text. The test used an isolated temporary ledger/audit directory. |
| `FD_LIVE=1 PYTHONPATH=. .venv/bin/python -m pytest tests/test_live.py -q -s` | 5 passed in 78.95 seconds. Text warm call 174.2 ms; audio warm call 113.8 ms; both below 1,000 ms. Text gate returned one group and 325 input tokens. The 12k and 24k post-results were checked successfully at 2965.1 and 4694.9 ms. |
| Original `scripts/smoke_hardening.py` invocation | Exited 1 after 59.27 seconds. Aborted at line 186 while waiting for the pending-mutation stdin hook: `subprocess.TimeoutExpired` after the fixed 10-second child timeout. No final 19-check report was produced. |
| Corrected hardening smoke, one authorized rerun | All 19 checks passed; exit 0 in 70.9 seconds. The outer child deadline uses `timeout * 3`, matching the earlier hook invocation. First/probe and warm client budgets remain 8 seconds and 1 second. |

The five offline skips are the opt-in live tests subsequently executed above.
No fixture action was executed. All live checks used isolated audit/ledger storage.
Ignored local logs retain the detailed outputs. The live module ran once; the
smoke had its original failed invocation and one authorized rerun after correction.

## Independent shipped-pack text confirmation

Executed the full fixture set and all four window sizes through the real service:

```sh
FD_LIVE=1 PYTHONPATH=. .venv/bin/python scripts/measure_scoping.py --lane max --scopes none --repeats 1 --timeout 120 --output docs/scoping-measurement-shipped-text.json
```

The original explicit local lane configuration was supplied through the environment.
The run completed all 532 unique records: 34 static calls verified the static path;
all 498 nonstatic calls were complete and model-scored. Every record reports zero
output tokens. All 94 warm unscoped/scoped fixture pairs have identical verdicts
and answer observations. No candidate scope was inferred from the unchanged packs.

| Pack | Agreement, both arms | Unscoped p95 ms | Shipped-arm p95 ms |
| --- | --- | ---: | ---: |
| Gate | 41/45 | 1958.349 | 1979.134 |
| Classify | 8/9 | 1557.929 | 1553.063 |
| Route | 5/9 | 411.378 | 391.062 |
| Verify | 4/4 | 322.325 | 304.055 |
| Post-result, shipped window | 10/23 | 6712.090 | 7779.411 |

There were no verdict or answer differences between the unchanged arms. Every
pack passed the relative latency factor. Seven gate calls per arm exceeded the
shipped one-second warm budget; measurement used its declared extended timeout.
This remains a measured limitation of the unchanged path, not a scoping regression.
Post-result latency varied between identical arms, within the allowed factor.

The overall adoption result is false solely because gate agreement does not rise.
That is expected when both arms use identical unscoped questions. This one-repeat
run confirms reproducibility; the initial and focused five-repeat runs remain the
adoption evidence. It does not establish a new five-repeat latency estimate.
The window comparison reproduced the same detection sets and additional near-miss
false flag on every smaller window, supporting retention of the shipped window.

## Evidence integrity and privacy

Each completed artifact below was independently replayed with `summarize`; the
fresh summary exactly equals its stored summary. Record identities are unique,
static checks pass, nonstatic calls are complete and scored, and all output-token
counts are zero.

| Artifact | Records | Static / scored nonstatic | Bytes |
| --- | ---: | ---: | ---: |
| `docs/scoping-measurement-text.json` | 1596 | 102 / 1494 | 21125715 |
| `docs/scoping-measurement-text-exfiltration.json` | 552 | 12 / 540 | 9662767 |
| `docs/scoping-measurement-shipped-text.json` | 532 | 34 / 498 | 6944928 |
| `docs/scoping-measurement-audio.json` | 1596 | 102 / 1494 | 21144195 |

SHA-256 values, in the same order:

```text
62dbba86289ec511fe7c2eec40ea60857da9abe82508415e584ee3f9328f66f5
47a5269adac248c5cd8a1aa76b49c69d89a7f463a3c23d135a6fbe53db5de7f5
4890898631d4756375fe5aa6558a4cc5bbc0d8ceab5e763901b12ef76ddda001
ace2adcd4b930c1bde246ed91f8a3d85133c0bacbcf560d8e156e795d7d2e183
```

All four artifacts passed private-identifier checks using local identifiers and
hosts from the original local lane configuration, plus generic URL, personal-path,
email, private-address, private-key and bearer-credential patterns. No matches
were found. Identifier values and their mapping were not stored in this report.
The initial 23 implementation files also passed source privacy checks and manual
review of new documentation. Historical content was not cleaned or certified.

The audio evidence records the lane as available and loaded before and after
measurement, with unchanged reported model revision
`414f8d6438174f5b2133a9c21a478fc42625e308`. Audio results remain separate from text
and do not determine adoption.

## Findings and plan alignment

First-pass findings were accepted and corrected: explicit-null scope validation,
failed-request timing, measurement-only post-result budget handling with separate
shipped-budget evidence, explicit window baseline, per-question adoption checks,
and explicit non-adoption of score scopes without a defined decisiveness metric.
Pending fixtures now seed actual pending ledger events. The recorded replay test
and shipped-scope candidate recovery were inspected at `1e9d4bf`.

No implementation defect was established by the completed checks. Rejecting all
scopes and smaller windows follows the approved adoption criteria. Fixture labels
remain implementing-agent labels, with maintainer review still pending; independent
verification does not substitute for that review.

**Resolved validation failure.** The original observed failure was the smoke
wrapper's child-process deadline at `scripts/smoke_hardening.py:186`,
not an observed decision-client lane timeout. The cause has not been established.
The separate manual pending-mutation check and full live module passed, but neither
substituted for the plan-required hardening smoke. The scoped follow-up retained
the original failure and obtained the completed result described below without
loosening shipped decision budgets.

A single isolated diagnostic reproduced the exact 20,000-character pending ledger
entry and hook event with a 60-second outer deadline and unchanged client budgets
(8 seconds first/probe, 1 second warm). It returned `ask`, all 12 answers, and the
explicit pending-session floor in 8346.251 ms. Instrumented request times were
357.861 ms for warmup, 1897.673 ms for the 12k cost probe, 593.340 ms for the 3k
probe, and 756.036 ms for the final two-item gate. Warmup in total took 3019.284 ms;
the child reached gate entry after 2838.377 ms. This diagnostic did not reproduce
the original timeout, so its exact cause remains unproven. It does demonstrate
substantial process/startup overhead and only 1.654 seconds of headroom under the
fixed deadline. The three permitted 8-second warmup/probe request budgets plus
the 1-second gate budget already exceed that 10-second process limit. No client
budget was extended, and no repository source was changed for the diagnostic.

The root agent then changed only the smoke wrapper's pending-child deadline from
10 seconds to `timeout * 3`, consistent with its earlier hook invocation, plus an
explanatory comment. The independent review confirmed this limited diff. One
authorized full smoke rerun passed all 19 checks: taint-pair measurement, benign
read, session assessment, mutation floor, hook context, audited clearance, model
use after clearance, offline carry-over and static injection, long and largest
permitted results, the immediate gate afterward, pending release/floor/carry-over,
and pending recheck/resolution. No runtime/pack or client-budget adjustment was
made to recover a model verdict. This closes the validation blocker; it does not
prove that the original timeout's exact timing cause was reproduced.

Verdict: approve implementation verification and the unchanged-default decision.
No independent verification blocker remains. Only maintainer fixture-label review
remains pending before the full plan's acceptance can be claimed.
