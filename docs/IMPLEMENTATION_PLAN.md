# Fluid Decisions — Implementation Plan v0

Status: first plan for the agentic coder. Implement, test, and engineer against the live lanes. Do not treat the inspiration repos as code to vendor.

## What this is

A small tool set that calls the fleet's LFM d1 decision lanes and turns the probabilities into classification, routing, tool gating, and verification. The model never decides the action. A policy outside the model does.

Suitable surfaces, all sharing one core:

- a Python library an agentic worker imports
- an MCP server any MCP client can call
- a pre-tool / post-tool hook (OpenCode first, Claude Code second)

The README already states the product: LFM d1 as a safety classifier, tool gate, and verifier for coding agents and agentic workers.

## What already exists

Do not rebuild the model server. Both lanes already speak `POST /v1/systemone`.

| Host | Model | Modalities | Device | Base URL |
| --- | --- | --- | --- | --- |
| the text lane | `LiquidAI/d1-3B` | text, image | Apple MPS, fp16 | `<text-lane-url>` |
| the audio lane | `LiquidAI/d1-omni-600M` | text+image, or text+audio (one clip, 30 s max) | AMD GPU, fp16 | `<audio-lane-url>` |

- the text lane: launchd `com.example.d1-decision`. the audio lane: systemd `d1-omni.service`.
- Server: `services/d1-decision/d1_server.py` on both. Declared in `cluster-services.yaml` (lines 386 and 504).
- Batch: `POST /v1/systemone/batch` with `{"requests": [...]}` returning `{"results": [...]}`.
- `model` field is accepted and ignored. Do not route by it.
- Images: up to 8, data-URL, https URL, or raw base64. Audio: one clip, omni only. Images and audio in one request return 422.
- Live check already done: same payload, HTTP 200 on both. 3B more confident (0.99 on "A") than omni (0.64). Omni 205 ms. 3B cold shape 4.9 s, then 254 / 130 / 134 ms warm.
- d1 agent skills are already in this project. Read them before writing a client. They are the schema source of truth if this plan and the server disagree.
- Inspiration, read-only, at `/docs/z-inspiration`: `is-malicious`, `jev-guard`, `opencode-jev-compaction`, `toolgate`. Steal the policy pattern. Do not import TypeSafe, do not call a hosted Jev API, do not copy their license headers into this tree.

## Non-goals for v0

- No fine-tune. Liquid has not published a d1 training recipe. Tasks change by changing questions.
- No chat-completions shim. `/v1/chat/completions` does not apply.
- No second model server. This repo is a client, a policy, and three adapters.
- No claim that a probability is calibrated until we measure it on our fixtures. The two lanes already disagree.
- Audio is not a v0 gate. Omni audio is demo-grade until we have fixtures. v0 audio support is a lane capability, not a product.

## Design rules

1. One request, all questions. Do not split routing, validity, and severity into three calls. The state encodes once.
2. Split only when a later question depends on an earlier answer.
3. Policy lives in YAML, about twenty lines, owned by the operator. The model never sees the threshold.
4. Return the full distribution, not just the argmax. A 0.62 against a 0.7 line is information.
5. The hook is enforced by the runtime. The agent cannot skip it and cannot choose when to verify.
6. Fail closed on a gate if the lane is down, times out, or returns a shape we do not understand. Fail open is the wrong default for a guardrail.
7. Static denies run before the model. `rm -rf`, `git push --force`, `DROP TABLE`, `curl` with a local file to an off-host URL never cost a forward pass.
8. Log every call: lane, latency, cold-shape flag, questions, probabilities, verdict. Calibration later depends on this.
9. Question packs are code. Mutually exclusive choice labels. Score levels ordered, lowest first, 2–10. A Noul is yes/no. 0.5 is maximum uncertainty, not a middle intensity.

## Lane router

```
text only, or text+image  -> the text lane (d1-3B)
audio clip present        -> the audio lane (omni)
image and audio together  -> reject locally, do not send (server returns 422)
rain-max down             -> fall back to omni for text/image only
omni down, no audio       -> fall back to max
audio and omni down       -> fail closed
```

Prefer 3B for text and image. It was more confident on the live check and is the recommended checkpoint. Use omni when the state is a clip. Do not average the two lanes in v0. If both are healthy and the caller asks for a second opinion, that is a later flag, default off.

Timeouts: 2 s warm, 8 s if the process has not seen this shape (the 4.9 s cold shape on max is real). Retry once on connection reset. Do not retry a 422.

## Repo layout to create

```
fluid-decisions/
  README.md
  docs/IMPLEMENTATION_PLAN.md
  docs/z-inspiration/          # already copied locally; do not commit the four repos
  pyproject.toml
  src/fluid_decisions/
    client.py                  # POST /v1/systemone and /batch
    router.py                  # lane pick, health, fallback
    policy.py                  # thresholds -> allow | ask | deny | flag
    packs/
      gate.py                  # pre-tool questions
      verify.py                # post-output questions
      route.py                 # desk / model / queue
      classify.py              # closed-set label
    ledger.py                  # session memory: user ask, prior flags
    audit.py                   # jsonl
  policies/default.yaml
  adapters/
    mcp_server.py
    opencode_hook.py
  tests/
    fixtures/                  # recorded responses, no network
    test_policy.py
    test_router.py
    test_client_live.py        # marked live, skipped unless FD_LIVE=1
  scripts/smoke.sh
```

Python 3.11+. httpx. PyYAML. No torch in this repo. The models stay on the hosts.

## Phase 0 — client and live smoke

First task: confirm the Noul response shape. The operator did not exercise it. Read the d1 skill and `d1_server.py` if present on the hosts. Expected, from the Liquid contract: `answers.<name>.noul` is a float in [0, 1]. If the server uses a different key, adapt the client and note it here. Do not guess past the first live call.

`client.system_one(base_url, state, questions, images=None, audio=None) -> Decision`.

Decision holds answers, usage, timings, and the raw body. Map:

- choice: `choice`, `probabilities`, `confidence`
- score: `score`, `probabilities`, `confidence`, `legend` if present
- noul: `noul` (probability yes)

Smoke, both hosts, the operator's disk-usage payload plus one Noul (`urgent_bool`: "Does this need action in the next hour?"). Record latency and the cold-shape first call. Commit the redacted JSON under `tests/fixtures/`.

Health: `GET /health` if the server has it; otherwise a 1-question systemone with a 3 s budget. Cache health for 15 s.

Done when: both lanes return 200 for choice, score, and noul; the client rejects image+audio locally; a down host fails closed rather than hanging.

## Phase 1 — question packs

Packs are functions that take a state dict and return the `questions` object. No I/O.

Gate pack, one call, stolen in shape from toolgate and jev-guard but written for this fleet:

- `destructive` noul — irreversibly destroys or overwrites (rm -rf, force push, DROP, overwrite of a non-temp path)
- `exfiltration` noul — sends local data off-host
- `privilege` noul — escalates or edits security, launchd, systemd, keys, or git config
- `secret_exposure` noul — reads or prints credentials, tokens, `.env`
- `off_task` noul — not entailed by the user ask in the ledger
- `from_untrusted` noul — executing an instruction that arrived in a tool result, web page, or file the user did not write
- `user_requested` noul — the user explicitly asked for this exact action
- `risk` score — `read_only`, `writes_workspace`, `writes_outside_workspace`, `destructive` (lowest first)

Verify pack, run on the model output after it exists, not when the model feels like it:

- `supported` noul — this claim is supported by the supplied source
- `contradicts` noul — this claim contradicts the source
- `needs_human` noul — a person should see this before it is acted on

Route pack:

- `lane` choice — `local_small`, `local_reasoner`, `cloud`, `human`
- `queue` choice — caller-supplied criteria, required, no default taxonomy

Classify pack:

- `label` choice — caller-supplied criteria
- `abstain` noul — the state does not support a label in the set

Do not invent a universal taxonomy. The caller passes criteria. The packs own the gate and verify questions because those are the product.

## Phase 2 — policy

`policies/default.yaml`:

```yaml
fail_mode: deny          # deny | ask. never allow, on a gate
static_deny:
  - pattern: 'rm\s+-rf'
  - pattern: 'git\s+push\s+.*--force'
  - pattern: 'DROP\s+TABLE'
thresholds:
  deny_if:
    destructive: 0.80
    exfiltration: 0.80
    privilege: 0.80
    secret_exposure: 0.70
    from_untrusted: 0.70
  ask_if:
    destructive: 0.45
    exfiltration: 0.45
    off_task: 0.60
    risk_at_least: writes_outside_workspace
  allow_early_if:
    user_requested: 0.85   # does not override secret_exposure, from_untrusted, or static deny
verify:
  block_if:
    contradicts: 0.60
    supported_below: 0.40
```

These numbers are starting points copied in spirit from the inspiration repos. They are not calibrated for d1. The audit log is how they get replaced.

`policy.decide(answers, policy, ledger) -> Verdict` with `action` in `allow | ask | deny | flag`, the firing rule, and the probabilities that fired. Unit test this with fixtures. No network.

Secret exposure, from_untrusted, and static denies are never softened by `user_requested`.

## Phase 3 — tool gate

Pre-tool hook. Input: tool name, arguments, user ask, recent tool results (truncated, size-noted past 2 KB). State is that bundle, not the whole transcript.

Order:

1. Static deny. If it hits, verdict deny, model not called.
2. One systemone with the gate pack.
3. Policy.
4. Append to the session ledger: the call, the verdict, any from_untrusted flag.
5. Audit jsonl.

Ask means the runtime prompts the user. Deny means the tool does not run and the agent receives the verdict and the probabilities, not a hidden failure. Allow means the tool runs.

Post-tool, same hook, second small call: `directed` noul and `kind` choice (`injection`, `canary`, `discussion`, `benign`) on the tool result. Flag at directed >= 0.6 and kind in {injection, canary}. Remember the flag in the ledger so the next pre-tool call can see it. Skip results under 200 characters.

OpenCode adapter first, because that is the coder most likely to load a plugin. Claude Code PreToolUse second. Both call the same function.

## Phase 4 — verification

Mandatory. Not a tool the agent may call.

After the worker finishes a draft that cites a source (a file, a tool result, a user-supplied document), the runtime splits the draft into claims (dumb split on sentences is fine for v0) and runs the verify pack per claim, batched. Block or flag on the verify thresholds. The agent does not see a pass it can waive.

This is the hallucination check. The writer is not the judge.

## Phase 5 — MCP

One server, tools that wrap the core. Do not put a second policy in the MCP layer.

- `fd_classify` — state, criteria -> label, distribution, confidence
- `fd_route` — state, destinations -> destination, distribution
- `fd_gate` — tool name, args, user ask -> verdict
- `fd_verify` — claims, source -> per-claim probabilities
- `fd_health` — both lanes

MCP is the voluntary surface. The hook is the enforced surface. Document that distinction in the README so a caller does not think registering the MCP tool is the gate.

## Phase 6 — later, not v0

- Compaction, from opencode-jev-compaction: pin first message and newest 6, two Nouls per tool call (still matters, need verbatim), drop below 0.5, keep user and assistant text verbatim, decline if reduction under 25% and let the host summarize. Worth doing once the gate is live. Do not summarize with d1. It cannot write the summary.
- Codebase scan, from is-malicious: chunk, one call with the check set, second pass only on uncertain chunks, exit 2 if a chunk was skipped. A clean report is not proof of safety.
- Second-opinion mode: same payload to both lanes, surface disagreements. The live check already showed 0.99 vs 0.64.

## Tests the coder must run

Policy, no network:

- static deny fires without a client call
- secret_exposure 0.9 with user_requested 0.95 still denies
- from_untrusted 0.7 denies
- all low, user_requested high, allows
- lane down on a gate yields the fail_mode, not allow
- image+audio rejected before HTTP

Live, `FD_LIVE=1`:

- smoke payload on both hosts
- one Noul on both hosts
- gate pack against a benign `ls` and against `rm -rf /`
- verify pack against a claim that quotes the source and a claim that contradicts it
- record warm latency; do not fail the test on the cold-shape first call, but log it

Do not assert exact probabilities. Assert the verdict class and that probabilities sum to about 1 on choice and score.

## Acceptance for v0

- Library, MCP, and one hook share `policy.decide`.
- A tool call the user did not ask for, that reads `.env` or pushes --force, is denied with the firing probability in the audit log, with the lane disconnected as well as connected.
- A supported claim passes verify and a contradicting claim flags, on a fixture and on one live call.
- README tells the operator how to point the hook at the two base URLs and how to change a threshold without editing Python.

## Open items

- Confirm Noul response key on `d1_server.py` before locking the client.
- Confirm whether `/health` exists or only systemone.
- Tailscale names are the base URLs. Do not hardcode them in more than `policies/default.yaml` and the README example. Tests read `FD_MAX_URL` and `FD_OMNI_URL`.
- lfm1.0 on the checkpoints. This repo does not redistribute weights. Say that in the README.
