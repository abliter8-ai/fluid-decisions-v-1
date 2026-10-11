# Using Fluid Decisions

Fluid Decisions sends typed questions to a configured d1 HTTP endpoint and applies the bundled policy in Python. The endpoint returns probabilities; it does not choose whether a tool may run. The four bundled packs are versioned YAML files under `fluid/packs/` and are marked `uncalibrated` until they have been fit to real audit traces.

## Question scoping (IP-004)

Gate, classify and route questions may optionally declare a non-empty list of dotted
object paths in a `scope` key. The paths select the part of the state that question sees;
list indices and bare-value scopes are not supported. Verify retains its existing
per-claim states, and post-result retains its window states. For example, a hypothetical
custom gate pack could use:

```yaml
questions:
  operation:
    type: score
    criteria: [read_only, mutating, destructive, irreversible]
    scope: [tool_name, tool_input]
  user_requested:
    type: noul
    scope: [tool_name, tool_input, user_request]
```

This example describes the configuration shape only; it does not claim that the bundled
packs currently ship with these scopes. A question without `scope` sees the complete
state. When no question has a scope, the existing request shape is preserved.
Questions with identical scopes share one batch item. With scopes present, all groups
are sent in one batch and their answers merge under the original question names. The
result may include a `scoping` summary with each group's scope, question names,
input tokens and serialized state length, plus `unresolved` question names. Its mode
is `whole_state`, `scoped` or `whole_state_media`.

Missing paths are skipped. If none exists, or the state is not an object, that question
is omitted from the request and reported under `scoping.unresolved`. Existing
required-answer checks then return `ask`. Classify currently sends string chunks,
so object-path scopes cannot resolve against those chunks.
When a request carries an image or audio clip, media bypasses grouping and all questions
use the whole state so media is not duplicated across batch items.

The shipped packs remain unscoped because neither tested combination met all adoption
criteria. Pack versions, thresholds and the 12,000-character window remain unchanged.

## Configure the decision lanes

The public defaults are local endpoints: d1-3B at `http://127.0.0.1:8774` for text and image requests, and the optional omni lane at `http://127.0.0.1:8775` for audio. Configure an operator-owned endpoint with `FD_MAX_URL` and `FD_OMNI_URL`, or select a complete lane file with `FLUID_LANES_FILE`. These settings are read by `fluid.client.D1Client`; they do not discover or fall back to maintainer hosts. `examples/lanes.local.yaml` is the public localhost lane file; `examples/lanes.maintainer.yaml` is a **placeholder** with `<text-lane-url>`/`<audio-lane-url>` and carries no real endpoints — keep real lane URLs in a local file outside version control (for example `.fluid-decisions/lanes.maintainer.yaml`, which `.gitignore` excludes) and point `FLUID_LANES_FILE` at it.

The first request to a lane uses the first-call timeout, eight seconds by default. After that lane has been seen, the timeout is one second by default. Set `FD_FIRST_TIMEOUT` and `FD_TIMEOUT` when the operator's hardware needs different budgets. `fluid.service.Decisions.warmup()` pays the first-call cost explicitly, and `fluid.mcp_server` attempts that warmup before starting its stdio server.

For example, with a locally served d1-3B endpoint:

```sh
export FD_MAX_URL=http://127.0.0.1:8774
export FD_OMNI_URL=http://127.0.0.1:8775
export FD_FIRST_TIMEOUT=8
export FD_TIMEOUT=1
```

## CLI

Install the package in the environment that owns the worker, then use `fluid` or `python -m fluid.cli`. `--state` is a JSON value, so a plain text state needs JSON quoting (`'"text"'`); `--file` reads a file containing one JSON value. Do not pass both.

```sh
# Classify a JSON state. Add --image PATH once per image, or --audio PATH for one clip.
fluid classify --state '{"path":"build.py","text":"read the token"}'

# Route to an operator-owned set of destinations.
fluid route --state '"A support ticket"' \
  --destinations '{"local":"fast local queue","reasoner":"reasoning queue","human":"review queue"}'

# Gate one tool input before it is dispatched.
fluid gate --tool read_file --input '{"path":"README.md"}' \
  --user-request 'Read the project README' --session session-123

# Verify one or more claims against one source.
fluid verify --source 'The service starts on localhost.' \
  --claim 'The service starts on localhost.' \
  --claim 'The service always runs remotely.'

# Score a candidate the caller already has against a closed list of constraints.
fluid constraints --file examples/constraints/driver-load.json \
  --constraints "$(python -c 'import json;print(json.dumps(json.load(open("examples/constraints/driver-load.json"))["constraints"]))')" \
  --candidate "$(python -c 'import json;print(json.dumps(json.load(open("examples/constraints/driver-load.json"))["candidate"]))')"

# Check a finished packet against a closed spec of validity checks.
fluid validity --file examples/validity/application-form.txt \
  --spec "$(cat examples/validity/application-form.spec.json)"

fluid health
```

The CLI writes one JSON result to stdout. Exit status `0` means allow (including an explicitly unscored release), `1` means deny or a high/flagged finding (including `constraints` → `no_feasible_candidate` and `validity` → `invalid`), and `2` means ask, incomplete input or another input/service error (including `validity` → `abstain`). An `ask` result is not permission to continue. Check the post-result `checked` field before describing any release as scored.

### Candidate checks (IP-009 / IDEA-002)

Two review aids that score a candidate the caller already has against rules written down before the call, and return probabilities to a policy the model does not see. Neither proposes a change, and neither is a judgement of truth.

- **`fluid constraints`** takes a `--candidate` (JSON or text) or `--file`, and `--constraints`, a JSON list of `{id, rule}` where each rule is one sentence. It returns, per constraint, the probability it holds and a severity, the failing constraints by id (first-failing-first, in the caller's order) with their severity, and a verdict in `pass`, `no_feasible_candidate`, `ask`. A `--image` scan may be attached. No result proposes a fix.
- **`fluid validity`** takes a `--packet` or `--file` (text) and/or `--image` (up to eight pages), and `--spec`, a JSON `{types, checks: [{id, rule}], completeness_levels}`. It returns, per check, the probability it holds, the failing checks by id, a `document_type` (with an explicit `unknown`), a completeness level, and a verdict in `valid`, `invalid`, `ask`, `abstain`. **Validity is not support:** a `valid` verdict means the form is filled, nothing more; the result carries the sentence `validity is not support`, and claim support stays the separate `verify` job. A packet over the lane's prompt budget is split into page-sized chunks for the per-check questions, composed by maximum.

The MCP tools are `check_constraints` and `check_validity`; the library calls are `Decisions.constraints(candidate, constraints, images=None)` and `Decisions.validity(packet, spec, images=None)`. The candidate, packet and spec are never written to the audit log. Every rule must carry, in its own text and the candidate, all the facts it needs; a rule that needs an outside lookup returns `ask`. Lines are uncalibrated.

## Runtime hook and mandatory enforcement

The stdin hook is a one-shot adapter. A pre-tool event contains the tool name and object input:

```json
{"event":"pre_tool","tool_name":"read_file","tool_input":{"path":"README.md"},"user_request":"Read the README","session_id":"session-123"}
```

Send it to `python -m fluid.hook`:

```sh
printf '%s\n' '{"event":"pre_tool","tool_name":"read_file","tool_input":{"path":"README.md"},"user_request":"Read the README","session_id":"session-123"}' \
  | python -m fluid.hook
```

After the runtime has executed an allowed tool, send its text result back as:

```json
{"event":"post_result","session_id":"session-123","tool_name":"read_file","result":"the tool's text result"}
```

Every non-empty tool result receives the static injection checks over its full text, even when it is oversized or the lane is unavailable. Model scoring uses overlapping windows; no tail is silently dropped. A directed-injection noul at or above the YAML threshold (`0.70` in the current uncalibrated pack), or a `kind` of `injection` or `canary`, flags the session. The post-result response distinguishes three outcomes:

| `check.status` | Default behaviour | Ledger |
| --- | --- | --- |
| `scored_clean` | `allow`, `checked: true`, release | No taint |
| `scored_flagged` | `deny`, `checked: true`, withhold | Flag with carry-over hashes |
| `unscored` | `allow`, `checked: false`, release | Pending with carry-over hashes |

An **unscored result was statically checked but not model-scored**. Its `check.reason` is `timeout`, `lane_down`, `exceeds_budget` or `oversized`. Release is not evidence that the content is safe. `check.model_scored` distinguishes an actual model decision from a static hit or the `empty_result` shortcut. An empty string is allowed without inference. A released unscored result has `complete: true` and CLI exit 0; `checked: false` remains visible in the hook JSON, runtime output and audit. Operators can set `post_result.policy.on_unscored: withhold` in `fluid/packs/gate.yaml` to retain the older withholding policy. Failure to retain pending or audit evidence still withholds; malformed input and invalid model responses do not gain release permission.

Taint is graded and recoverable. While active, the model sees an untrusted-events summary in the gate state, mutating operations have the configured verdict floor (`ask`), and a command carrying an exact shingle from a flagged result is denied before the model. The gate batch also asks a dedicated question over the session summary alone, exposed as `session_assessment` in the result and audit. That answer assesses the history; the action-specific `from_untrusted` remains a separate probability. Taint context expires by the configured call window or TTL. Carry-over shingles remain protective for the life of the session, including after clearance. Clear active context without deleting history with:

```sh
fluid session clear --session session-123 --reason 'reviewed false positive'
```

Pending results follow the same expiry and clearance rules. They appear in `session.pending_events`; pending mutations receive reason `session_pending_check`. Reads still receive ordinary model and policy evaluation. Carrying an exact normalized phrase from either a flagged or pending result into a command denies before the model. No raw result text is stored in the ledger.

Clearance is appended to the ledger and audit log. It removes active context and the operation floor, retaining history and carry-over protection. Gate failures still return `ask`; post-result timeouts and unavailable lanes use the explicit unscored policy above.

Recheck by supplying the original text, which must match an unresolved pending SHA-256 before a model call:

```sh
fluid session recheck --session session-123 --file original-result.txt
# Or provide the original text on stdin:
fluid session recheck --session session-123 < original-result.txt
```

The Python equivalent is `decisions.post_result(text, session_id=session_id, recheck=True)`. One invocation resolves prior pending records for the matching content hash. A clean score removes their active context and carry-over hashes; a flagged score creates an ordinary flag. A later pending event for the same content is independent. If another timeout occurs, the result remains pending. Recheck does not recover text from hashes or run in the background. Files must contain the exact original UTF-8 text, including line endings.

Warmup measures two benign post-result-shaped probes at a full window and a quarter window, using the first-call timeout. Health reports the per-lane `cost_models` (`intercept_ms`, `ms_per_char`), `warmup_errors` and `last_warmup_ms`. For a result batch, expected cost is the intercept plus slope times the sum of window lengths, including overlap. The derived budget applies YAML `budget_margin: 2.0`, the `FD_TIMEOUT` floor, and YAML `max_budget_s: 30` cap. These values remain uncalibrated. If measurement is unavailable, the budget falls back to `FD_TIMEOUT`. `FD_POST_TIMEOUT` selects a fixed budget, still capped by YAML. A known cost above the cap or a smaller fixed override is rejected before submission with `exceeds_budget`. The audit's `check` object records `budget_source`, `expected_ms` and `budget_s`.

The estimate is workload-dependent; it does not cancel a request already running on the server or guarantee that every possible result fits. The runtime should warm its persistent client once at startup so cost probing does not sit in each tool's critical path.

### Budgets on every model call (IP-005)

Every model call, not only post-result, is budgeted from the measured cost model. Warmup adds one probe that measures the marginal per-question cost, stored as `ms_per_question` beside `intercept_ms` and `ms_per_char`; expected cost for a request is `intercept_ms + ms_per_char × total_chars + ms_per_question × questions`, where `total_chars` is the serialized length of the states the request sends. Gate, classify, route and verify each carry a `policy.budget` block in their pack (`budget_margin`, `max_budget_s`, uncalibrated) and return a `budget` object on the result and in the audit: `budget_source` (`measured`, `override`, `fallback` or `not_requested`), `expected_ms`, `budget_s`, `total_chars` and `questions`. When the derived budget would exceed the pack's `max_budget_s` cap, the call is not sent: the job returns `ask` with reason `exceeds_budget`. Static denials run before the budget is consulted, so a statically denied command is still denied without a model call. `FD_CALL_TIMEOUT` sets a fixed budget for these calls, still capped by YAML, mirroring `FD_POST_TIMEOUT` for post-result. With no cost model the budget falls back to `FD_TIMEOUT` and `budget_source` records `fallback`. The fixed warm timeout remains the floor; a call that previously timed out on a large tool input now completes inside its derived budget.

The hook is only an adapter. A runtime must enforce its response: `allow` may dispatch, while `ask` and `deny` must not execute the tool. The supplied `GuardedRuntime` in `fluid.service` wraps this rule and also withholds a result when the post-result check is not allowed. Every tool dispatch must go through that wrapper or an equivalent host-owned integration. An optional MCP call is voluntary and cannot enforce a worker that bypasses it. There is no direct Claude-native installer in this repository; connect the generic stdin contract from the runtime that owns tool execution.

### The facts gate and its taint behaviours (IP-006/IP-007)

As of IP-007 the **facts pack is the default** (`gate.yaml`, version 0.6.0). The frozen flat packet pack is kept as a fallback and selected explicitly with `FD_GATE_PACK=gate-packet`; it is byte-identical to the pre-IP-007 default.

- **Model-free provenance floor (always on in every pack).** Before the model runs — and still when the lane is down — the gate hashes the command-like spans and secret-like values that arrived in earlier released tool results, and denies or asks a later tool call that carries one, with the user's own words exempt. It is deterministic, stores only hashes, and runs after the static denials. Its policy is the `provenance` block in the gate pack (window, TTL, secret-path patterns, a read-only program/subcommand allowlist, and the per-class verdicts).
- **Facts gate (default).** Instead of one twelve-question state, the gate asks small facts over small states in one batch — an action item, a task/action item, and one item per enumerated constraint sentence — and `fluid.policy.compose_gate_facts` folds them into the same axis dictionary `evaluate_gate` takes. A large `write_file` body is replaced by a character count. A command that runs a script sees the script's text when a released `read_file` put it in the per-session content cache; otherwise the operation is floored at `mutating`.
- **Read-only operations cannot trip the action axes (IP-007 §1).** When the composed operation level is read-only, `compose_gate_facts` zeroes `destructive`, `privilege`, `unresolved_choice` and `violates_constraint` (reason `read_only_axes_zeroed`); `secret_exposure` and `exfiltration` are left intact, because reading a secret file is read-only but still exposes it.
- **Proportionate taint (IP-007 §3).** A confirmed injection flags the session. On a later mutating-or-worse call while a flag is active, the gate adds one `carries_out` fact per cached directive and asks only when a `carries_out` crosses `taint_carry_line` (reason `carries_out_flagged_instruction`); otherwise the call passes with the probabilities recorded. With no cached directive text (a one-shot hook or an evicted cache) the old blanket floor applies (`taint_floor_no_instruction`). A read-only call under a flag is not floored. A pending (unscored) result does not floor; it is rechecked (§2) instead.
- **A withheld path cannot be re-read (IP-007 §3).** When a result is withheld, the gate records hashes of its path fields and path-like command tokens; a later call naming one of them in any field is denied model-free (`withheld_path_reread`), in any encoding, and the user's own words do not exempt it.
- **Two-stage post-result and redaction (IP-006 §3 / IP-007 §4).** The result is split into paragraph-sized units with full byte coverage; stage one asks whether each unit is an instruction addressed to an agent, and stage two asks, only over located units and paired with the task, whether the instruction goes beyond it. With no task text a located unit decides on its locate score alone. When a unit is confirmed, `post_result` returns `redacted_text` — the result with each confirmed unit replaced by `[fluid-decisions withheld 1 unit]`, byte-exact elsewhere — and the runtime releases that in place of the full result (`on_confirmed: redact`, the default), so the user keeps the file minus the directive. A static-rule hit still withholds the whole result (it marks a sentence, not a unit). Canaries remain the `canary_token` static sentinel.
- **Pending recheck (IP-007 §2).** A result the lane could not score in time is released with a pending ledger record and kept in a bounded, per-session, memory-only cache. At the next turn the runtime rechecks it with a wider budget (`recheck_budget_s`); a resolved result clears, a second failure stays pending and is counted. The hook, the daemon and the agent loop all recheck at the turn boundary.

The pack's thresholds and post-result lines are calibrated against the maintainer's false-flag budget (`false_flag_budget` in the pack); `scripts/calibrate_lines.py` writes them and marks `calibration: two-models-one-repository`. `taint_carry_line` stays uncalibrated until the confirmation arm supplies `carries_out` scores. A line is never moved by hand.

Budgets add an explicit `floor_s` to each pack's budget block (the derived-budget floor, replacing the implicit `FD_TIMEOUT`). The persistent hook daemon `python -m fluid.hookd` holds one warm service and serves the hook's exact JSON contract over a Unix socket at `FLUID_HOOK_SOCKET` (default `${FLUID_DATA_DIR}/hookd.sock`, mode 0600, no TCP). `fluid.hook` tries that socket first and falls back in-process when it is absent, so a missing daemon degrades to today's behaviour, never to allow. The socket path must fit the operating system's Unix-socket limit (104 bytes on macOS); a path over the limit fails to bind and the hook falls back in-process. Example launchd and systemd units are under `examples/`.

`examples/runtime_worker.py` shows this integration with a Python `read_file` executor. It calls the configured d1 endpoint to gate the read; it invokes no shell or generative worker model:

```sh
python examples/runtime_worker.py --path README.md \
  --user-request 'Read the README for this task' --session-id demo
```

The example reads only a local file through Python's file API. It does not turn a path into a shell command.

## Static rules, packs, and uncertainty

Gate static rules run before any client request. The current gate pack denies command-bearing `command`, `cmd`, `argv`, `script`, and `query` values matching recursive removal, force pushes (including `--force-with-lease`), or `DROP TABLE`. Arbitrary document fields such as `content` are not scanned for shell text. Static denial therefore still works when the configured lane is unavailable.

The packs currently contain these questions:

| Pack | Questions and outcome |
| --- | --- |
| `classify` | Credential theft, exfiltration, obfuscation, concealment, build abuse, and severity. Uncertain nouls can request a verbatim focused second pass. |
| `route` | Destination, escalation, and completeness. Unknown destinations or incomplete state return `ask`. Runtime destinations can replace the default choices. |
| `gate` | Seven risk axes, authorization and request mitigators, untrusted-content and injection checks, operation impact, plus post-result kind. Missing or malformed required axes return `ask`. |
| `verify` | Claim support by a supplied source. Claims below the YAML support threshold are flagged; no replacement sentence is generated. |

Noul results retain the raw probability and expose a derived `{yes, no}` distribution, separate confidence, top probability, and decisiveness (`2 * abs(noul - 0.5)`). A probability such as `0.62` remains visible even when the policy returns `ask`. The gate fail mode is `ask`; policy code never converts an unavailable lane into `allow`.

## Audit and local session data

Audit records are append-only JSONL under `${FLUID_DATA_DIR:-~/.fluid-decisions}/audit.jsonl`. They include the job, lane, pack version, verdict, answers, timings, usage, reasons, and completion fields, plus IP-007 counts and probabilities (the `carries_out` scores, the `units_redacted` count, and the unit indices and per-unit scores). No raw instruction or result text is ever recorded — `redacted_text` is returned to the runtime but never audited. Raw state is omitted by default. If `FLUID_AUDIT_STATE=1` is explicitly enabled, the record adds the state byte count and a SHA-256 hash plus the literal preview `[state content redacted]`; it never writes the state body.

The session ledger is under `${FLUID_DATA_DIR:-~/.fluid-decisions}/sessions/`. Its hashed filenames contain append-only flag metadata, injection probability and decisiveness, result SHA-256 and byte count, shingle hashes, call counters and clearance records. Raw tool results are never stored. Keep this directory private; the implementation creates its directories with owner-only permissions.

## MCP

MCP is an optional worker integration. Install the extra and start the stdio server with:

```sh
python -m pip install -e '.[mcp]'
python -m fluid.mcp_server
```

The server exposes `classify`, `route`, `gate_tool`, `post_result`, `verify_claims`, and `health`. `gate_tool` uses the same policy service, but the worker must still respect its verdict. `post_result` returns `redacted_text` when a unit is confirmed (IP-007 §4), so a worker that cannot wrap the hook releases that in place of the full result. A worker that owns mandatory tool dispatch should use the hook or `GuardedRuntime` instead of relying on an optional tool call.

Each worker has its own configuration format. This localhost-only JSON fragment shows the process and environment values to adapt to a user-owned MCP client:

```json
{
  "mcpServers": {
    "fluid-decisions": {
      "command": "python3",
      "args": ["-m", "fluid.mcp_server"],
      "env": {
        "FD_MAX_URL": "http://127.0.0.1:8774",
        "FD_OMNI_URL": "http://127.0.0.1:8775",
        "FLUID_DATA_DIR": "/tmp/fluid-decisions-data"
      }
    }
  }
}
```

Use a private data directory appropriate to the worker instead of sharing `/tmp` for a multi-user deployment. The example contains no maintainer endpoint or credential.

## Checks used in this repository

From the repository root, the focused policy and static checks are:

```sh
.venv/bin/python -m pytest tests/test_policy.py
.venv/bin/ruff check fluid/policy.py tests/test_policy.py
.venv/bin/mypy fluid/policy.py
```

The full offline suite is:

```sh
.venv/bin/python -m pytest
```

Live lane checks are opt-in with `FD_LIVE=1`; they require endpoints configured by the operator and are separate from these offline checks.
