# IP-004 measurement recovery

Status: all four measurement runs and automated checks are complete. Mechanism commit:
`1e9d4bf`; final smoke-wrapper correction: `f4972a8`; completed evidence: `a5a2a5e`.
Branch: `feat/ip-004-question-scoping`.
Both tested scope combinations and every smaller text-lane window failed adoption.
Shipped packs remain unchanged. Only maintainer fixture-label review remains pending.
Preserve the completed evidence; the recovery
commands below are retained for genuinely interrupted runs, not instructions to repeat
completed measurements.
No shipped scope or window change is authorised by a passing mechanism test alone;
the measured acceptance rules in IP-004 decide adoption.

Run from the repository root with `PYTHONPATH=.`. Supply the existing private lane
configuration through `FLUID_LANES_FILE` or the two endpoint environment variables.
Never copy their values into this document, evidence, or a command log.

```sh
FD_LIVE=1 PYTHONPATH=. .venv/bin/python scripts/measure_scoping.py --lane max --timeout 120 --output docs/scoping-measurement-text.json
FD_LIVE=1 PYTHONPATH=. .venv/bin/python scripts/measure_scoping.py --lane omni --timeout 120 --output docs/scoping-measurement-audio.json
```

Run lanes sequentially. Expected output is progress JSON followed by an evidence
file with `status: complete`, all per-call records, segment health/cost metadata,
and a reproducible summary. Local progress logs are `.cache/ip004-text.log` and
`.cache/ip004-audio.log`; process metadata is `.cache/ip004-processes.json`.
These local files are ignored and must not be committed. Each completed call
atomically checkpoints the corresponding evidence file. To recover an interrupted
run, repeat the same command with `--resume`; do not change fixtures, packs, overlay,
window sizes, repeat count, or timeout. A resumed run adds a segment and retains all
previous records. The timeout is measurement-only; evidence retains the actual
shipped first/warm limits and original post-result cost-budget verdict.

Offline summary replay:

```sh
PYTHONPATH=. .venv/bin/python scripts/measure_scoping.py --replay docs/scoping-measurement-text.json
```

Commit both evidence files before any pack change. Evaluate per-question regression
and decisiveness criteria as well as pack agreement/latency. Retain rejected scopes
unshipped. Record every regression and the window decision in CR-004. Then rerun
against shipped scopes with `--scopes none` and a new output filename. Independent
verification must include the offline suite, live module, text measurement, manual
stdin hook/audit/session-floor checks, and evidence privacy inspection. Fixture
labels remain implementing-agent labels until maintainer review is recorded.

## Focused subset follow-up

The initial gate comparison tied verdict agreement and exceeded the latency factor.
Offline answer recomposition is recorded separately in
`docs/scoping-subset-screen.json`; it is not live evidence. It identifies the single
`exfiltration` scope as the smallest candidate predicted to improve agreement.
A durable focused runner completed after the initial text run finished. The audio
comparison uses a separate host and ran concurrently with this text follow-up.
No other measurement or test should contend for either occupied lane. Its local
progress log is `.cache/ip004-exfiltration.log` and process metadata is
`.cache/ip004-focused-process.json`. It runs 552 gate calls (first pass plus five
warm repetitions, both arms), using the identical gate fixture file. Other pack
fixture lists are empty in this auxiliary corpus; their initial full-set evidence
and final full-set confirmation remain separate requirements.

Recreate the ignored focused corpus by copying `gate.json` and `acceptance.yaml`
from `tests/fixtures/scoping/`, writing empty JSON lists for classify, route, verify
and post_result, and using this candidate overlay:

```yaml
calibration: uncalibrated
scopes:
  gate:
    exfiltration: [tool_name, tool_input]
```

The focused command was:

```sh
FD_LIVE=1 PYTHONPATH=. .venv/bin/python scripts/measure_scoping.py --lane max --fixtures .cache/ip004-exfiltration-fixtures --timeout 120 --output docs/scoping-measurement-text-exfiltration.json
```

Use `--resume` only with the same inputs if interrupted. No scope is adopted from
the offline screen; focused live latency and a final full-set shipped rerun decide.

The completed focused run improved gate agreement from 205/225 to 210/225, but p95
rose from 1,671.371 ms to 2,741.256 ms, exceeding the 1.5 factor. It is rejected.

## Independent shipped confirmation

The independent verifier completed all fixtures with one first pass and one warm
repetition (532 records). This confirms the unchanged shipped configuration; the
five-repeat initial and focused evidence remain the adoption basis.

```sh
FD_LIVE=1 PYTHONPATH=. .venv/bin/python scripts/measure_scoping.py --lane max --scopes none --repeats 1 --timeout 120 --output docs/scoping-measurement-shipped-text.json
```

Local log: `.cache/ip004-independent-text.log`. Recovery metadata:
`.cache/ip004-independent-process.json`. Resume with identical arguments plus
`--resume` if interrupted. The expected gate-improvement criterion is false because
both arms retain the original pack. It is not a failed transport or mechanism check.

After this text run, the verifier completed the stdin-hook/audit/session checks and
the named offline checks: 143 tests passed, five live tests skipped, three subtests
passed, and Ruff, mypy and both builds passed. All 94 warm fixture pairs had identical
verdicts and observations. The bounded follow-through process finished; its results
and local log references are in `.cache/ip004-independent-status.json`.

The initial runner also finished both lanes with exit code zero. All four evidence
files passed independent privacy/replay inspection. The final live module passed all
five tests; the corrected hardening smoke passed all 19 checks. Its first attempt
exceeded a fixed 10-second child-process deadline; the report preserves that failure,
the focused diagnostic and the wrapper-only correction. Client budgets stayed at
eight seconds for first calls and one second for warm calls.

The final offline rerun after that correction passed 143 tests with five live skips
and three subtests in 42.01 seconds. Ruff, mypy and source/wheel builds also passed.
No measurement or verification runner remains active. Final reports distinguish
rejected adoption from implementation verification; acceptance awaits label review.
