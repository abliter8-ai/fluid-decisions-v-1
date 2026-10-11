# AgentDojo comparison (IP-008)

Counts-only evidence from running the Fluid Decisions guard through the AgentDojo
benchmark. No endpoint, key, host or path is recorded here. See
`docs/c-completion_reports/CR-008_AgentDojo_Comparison.md` for the reading.

- `summary-none.json` — the undefended pipeline: benign utility, utility under
  attack, targeted attack success, with episode counts.
- `summary-fluid.json` — the guard as two pipeline elements, plus the guard's own
  audit counts (gate verdicts, redactions, taint and provenance reasons, timeouts).
- `comparison.json` — the three numbers per arm, side by side.

## Rerun

Install the extra and supply the gateway and lane at run time (never committed):

```sh
python3 -m pip install -e '.[agentdojo]'
OPENAI_COMPATIBLE_BASE_URL=<gateway>  OPENAI_COMPATIBLE_API_KEY=<key> \
  FLUID_LANES_FILE=<lanes-file>  FLUID_DATA_DIR=<ignored-local-dir> \
  PYTHONPATH=. python scripts/agentdojo_run.py \
    --arms none,fluid --suite workspace --attack important_instructions \
    --model-id claude-haiku-5-5 \
    --user-tasks user_task_0 user_task_1 user_task_2 user_task_3 user_task_4 \
    --injection-tasks injection_task_0 injection_task_1 injection_task_2 \
    --out docs/agentdojo
```

`--smoke` runs one user task and one injection task to check the config cheaply.
The run is resumable from its per-episode trace cache under `--logdir`; re-running
skips completed episodes. `FD_* ` fallbacks (`TEST_API_ENDPOINT` / `TEST_API_KEY`)
are accepted for the gateway.
