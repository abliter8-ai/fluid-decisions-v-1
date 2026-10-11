# IDEA-001 — Expansion after the gate is proven

Status: ideas, not a plan. Do not start these until v0 is accepted: library, hook, and MCP share one policy; a static deny and a lane-down both fail closed; verify flags a contradicting claim on a live call.

The point of waiting is calibration. The two lanes already disagree (d1-3B at 0.99, omni at 0.64 on the same payload). An expansion that trusts a number we have not measured on our own traces will fail quietly.

Each idea below says what it needs from v0, which lane it uses, and what would make it a bad bet.

## Already deferred, do these first

These were Phase 6 of the implementation plan. They are the nearest expansions because the question packs already exist.

### Session compaction

Prune tool traffic, do not summarize. Pin the first message and the newest six. Two Nouls per unpinned tool call: does knowing the call was made still matter, and is the full output still needed verbatim. Both above 0.5 keeps the body. Output-only keeps the call and truncates. Both below drops it. User and assistant text stays verbatim. If the reduction is under 25%, decline and let the host summarizer run.

Needs: the audit log, so we can see what got dropped and whether the agent later re-fetched it. Lane: the text lane. d1 cannot write the summary. That is why the fallback exists.

Bad bet if: the agent is mid-debug and the dropped tool output was the only copy of a traceback. The size-note must say the tool can be re-run.

### Codebase scan

Chunk source, CI, and build files. One call per chunk with the check set (credential theft, unexpected upload, obfuscation, suspicious install step). Second pass only on chunks that come back uncertain, zoomed to a line window. Exit 2 if a chunk could not be scanned. A clean report is not proof of safety.

Needs: the classify pack and the second-pass pattern. Lane: the text lane, text. Ship as a CLI and as a pre-install skill, same as is-malicious, pointed at our lanes.

Bad bet if: someone treats exit 0 as permission to run a postinstall hook. It does not sandbox anything.

### Second opinion

Same payload to both lanes, surface disagreements, do not average them. The live check is already the demo. Useful as a calibration tool before it is a product: when 3B says 0.99 and omni says 0.64, the item goes to a human and into the fixture set.

Needs: both lanes healthy, text or text+image only. Audio cannot be second-opinioned. 3B has no audio head.

## Coding-agent expansions

### Skill and instruction-file check

At session start, hash `AGENTS.md`, `CLAUDE.md`, and any skill the agent is about to load. One call: exfiltration, covert execution, instruction override, unrelated side effect. Cache the verdict by content hash. Recompute only when the file or the threshold changes.

Needs: the gate pack and the ledger. Lane: the text lane. This is the jev-guard instruction check, aimed at our own agent files.

### Diff gate

Before a commit or a push, the state is the diff, not the tool call. Nouls: secrets added, destructive migration, test file deleted, lockfile changed without a manifest change. Static rules still catch `--force` and `DROP TABLE` before the model.

Needs: the hook already wrapping the git tool. Lane: the text lane. Fits the PR-diff mode from is-malicious.

### Route the coder, not the tool

A Choice over `{local_small, local_reasoner, cloud, human}` before a generation, using the task text and a size note for the repo. The decision model does not answer the task. It picks who should.

Needs: the route pack, plus a record of whether the chosen lane actually finished. Otherwise the router never gets corrected.

## Verification expansions

### Claim check on a stream, after the draft exists

Not during decode. A partial sentence is an incomplete state, and re-encoding every chunk burns the reason to use d1. When the draft is finished, split into claims, batch the verify pack, block on contradicts or on supported below the line. The writer does not get a waive.

Needs: Phase 4. The expansion is batching claims and attaching the firing probability to the sentence in the UI, not a new model call shape.

### Screenshot versus spec

State is the spec text plus up to 8 screenshots. Noul: does this screen match the spec. Choice: which acceptance criterion failed. This is the web-agent pattern, pointed at a PR preview rather than a live browser.

Needs: image path on the text lane, proven on a handful of our own UI fixtures before it gates a merge.

## Fleet and ops

### Node and queue routing

The disk-usage payload already used as a smoke test is the shape. State is a JSON health blob. Choice: which node. Score: severity, lowest first. Noul: page a human. Run it on a timer, not inside a chat session.

Needs: nothing from the agent hook. It is a separate cron client of the same library. Lane: the text lane. Do not page on a single cold-shape timeout.

### Audio command routing, only after a fixture set

Omni takes one clip, 30 seconds, text+audio, never with an image. Liquid's own audio training was English speaker-to-assistant requests. The honest v1 is a closed command set: `{status, restart, read_log, no_command}`, plus a Noul for "this clip is not a command." Whisper-then-3B remains the production path for anything longer or messier, with the raw clip kept so the omni path can be compared later.

Needs: ten to twenty labeled clips from this fleet. No published audio benchmark exists to hide behind.

## Explicitly later, and why

- Game or sim policy head. A frame and a closed action set fit the model, and the latency can sit inside a loop. It does not teach us anything about the gate. Park it.
- Fine-tune. No published recipe. The license allows it. The product does not need it until a zero-shot pack misses on our fixtures by a margin a question rewrite cannot fix.
- Replacing a 35B prompt-classifier in one step. Run the same fixtures through both and compare top-label agreement and calibration error first. d1 may tie. It may not.
- Benefit decisions, diagnosis, or anything where the legal answer is not a set we wrote down before the call. Out of scope for this repo.

## What not to add

A chat template. A token stream. A summarizer. A threshold the model can see or set. An expansion that calls the lane once per question instead of once per state.
