---
id: IDEA-001
title: "Fluid Decisions expansion ideas, after the first four uses are proven"
status: idea
created: 2026-10-09
updated: 2026-10-09
depends_on: IP-001
lanes:
  text_image: "the text lane d1-3B at :8774"
  audio: "the audio lane d1-omni-600M at :8774"
---

# IDEA-001 — What to build after classify, route, gate, and verify work

This is not an implementation plan. IP-001 owns the first tool set: classification, routing, tool gating, and verification, exposed as a library, a CLI, a hook, and an MCP server. Nothing in this document should be started until that set is in use against both live lanes and the audit log has real probabilities in it.

The rule for every idea below is the same rule as v1. The decision model returns a distribution. A policy the model cannot see applies a threshold. The writing model does not get to decide whether the check runs. Text and images go to the text lane (d1-3B). Audio goes to the audio lane (d1-omni-600M), one clip, 30 seconds, never combined with images. Thresholds are calibrated on Fluid Decisions traces, not copied from Jev.

Each idea records what it reuses from v1, which lane it needs, and what it must not become.

## 1. Transcript compaction that does not summarize

**Reuse.** The client, the noul pack loader, the audit log.

**Lane.** the text lane. Text only.

**Idea.** When an agent session is about to overflow, pin the first message and the newest six. For every older tool call, ask two noul questions: does knowing this call was made still matter, and is the full output still needed verbatim. Both above the threshold keeps the call and the body. Only the output above the threshold keeps the call and truncates the body to a head plus a note that the tool can be re-run. Both below drops the call. User and assistant text is never rewritten. If the reduction is under 25%, or the lane is down, decline and let the host fall back to its own summarizer.

**Why it is next.** It is the compaction plugin in /docs/z-inspiration (opencode-jev-compaction) with the vendor swapped, and it is the only expansion that makes every later agent cheaper. Liquid's own coding-session demo dropped about half the tokens this way. The point is that the kept text is still the original text.

**Do not.** Do not ask d1 to write the summary. Do not drop user text. Do not run this before the gate hook exists, or the session being compacted has never been checked.

## 2. Second-pass zoom on the uncertain tail

**Reuse.** Classify pack, decisiveness (2 \lvert p - 0.5 \rvert), audit log.

**Lane.** the text lane for code and images. Omni only if the state is a short clip.

**Idea.** v1 classify asks the whole check set in one call. This idea adds the is-malicious second pass as a product: if any category lands in a band such as 0.40 to 0.70, or severity is high and decisiveness is low, re-ask only those questions with the state narrowed to the suspicious window, the diff hunk, or the page region. The first pass stays cheap. The second pass spends latency only where the distribution refused to commit.

**Why it earns a place.** A single pass over a whole file is how false confidence happens. The zoom is also the natural way to attach a line or a region to a finding, which a CI comment needs and a bare probability does not.

**Do not.** Do not zoom every chunk. Do not treat the second pass as a second model. Same lane, narrower state.

## 3. Cascade router in front of the heavy reasoner

**Reuse.** Route pack, gate hook, lane table.

**Lane.** the text lane.

**Idea.** Before a worker calls the large local model or a cloud model, one choice over a closed set: answer_here, local_reasoner, cloud, human. Plus a noul, "is this a closed-set question that should never have reached a writer." The route pack already has the shape. This idea is the pack pointed at the fleet router rather than at a ticket.

**Why it is the cost lever.** The insurer and contact-center argument was volume. On this fleet the same shape keeps spark-class or cloud calls for the turns that actually need new text. d1 does not write the answer. It decides who has to.

**Do not.** Do not let the choice include an open "write something" option with no destination. Do not route audio here. A spoken turn is idea 6.

## 4. Screenshot action selection

**Reuse.** Gate pack's legal-action choice, image input on the text lane.

**Lane.** the text lane. Image plus a text state listing the legal actions.

**Idea.** The worker already has a screenshot and a list of allowed actions, which a browser or desktop tool must have anyway. d1 picks among those actions, with the full distribution. Below the confidence floor, the worker asks or stops. This is the Tetris and web-agent demo reduced to a hook: the model never invents a click target that was not in the criteria.

**Why it fits.** Vision is the 3B lane's actual advantage over a text classifier. A 384 px frame on the published numbers is tens of milliseconds once warm. The legal set is the criteria object. If the set is wrong, the distribution is confident over the wrong buttons, so the criteria have to be generated from the tool schema, not from a prose description of the screen.

**Do not.** Do not ask d1 to invent the action list. Do not send the screenshot to omni and an audio clip in the same request.

## 5. Visual inspection pack

**Reuse.** Classify pack, image input, second-pass zoom.

**Lane.** the text lane.

**Idea.** A noul, "does this frame show the defect described," plus a choice of defect class, plus a score of severity. State is a short instruction and the frame, the same shape as Liquid's circuit-board example. Useful on a camera loop, a scanned form, or a UI regression shot. The zoom pass crops to the region if the first pass is uncertain.

**Why it is distinct from classify.** Classify is about behaviour in text. Inspection is about a frame. The published open model is built for that, and a captioning model cannot sit in a 30 ms frame budget.

**Do not.** Do not ship this as a quality-control system until reliability is measured on your own frames. Liquid's 85–97% figures are their VisA setups, not a transferrable guarantee. Do not use omni for this. Its vision encoder is the smaller one, and its index score is not in the same class.

## 6. Spoken-intent route, short clip only

**Reuse.** Route pack, lane router.

**Lane.** the audio lane only. One clip, 30 seconds, English speaker-to-assistant, no image on the same call.

**Idea.** A choice of intent and a noul of "needs a human" on a voice note or a single utterance, without a speech-to-text step. This is the only expansion that needs the omni lane. It is for a command, a voicemail, or one turn of a call, not a meeting.

**Why it is last among the routes.** Audio decision benchmarks are unpublished. The text path, Whisper then d1-3B, is the production default and already possible with v1. This idea exists so the audio lane is not idle, and so the two paths can be compared on the same clips. Log the raw audio. If omni loses on your clips, delete the pack and keep the transcript path.

**Do not.** Do not replace diarization. Do not feed it hours of multi-speaker audio. Do not send the clip to the text lane.

## 7. Session ledger as a product, not a file

**Reuse.** Gate hook, audit log, from-untrusted noul.

**Lane.** the text lane.

**Idea.** jev-guard's session memory, made explicit. A tool result that flags as injection or canary is written to the session ledger. Every later gate call includes that ledger in the state, and the from-untrusted noul can fire on a call that would otherwise look benign. The ledger expires with the session. The audit log keeps the flag, not the body.

**Why it matters.** A one-shot gate misses the attack that arrives in a file read and is acted on three turns later. The ledger is the memory the decision model does not have.

**Do not.** Do not store raw tool output in the ledger by default. Store a hash, a flag, and a redacted preview. Do not let the writing model clear the flag.

## 8. CI and supply-chain scan

**Reuse.** Classify pack, second-pass zoom, exit codes 0 / 1 / 2.

**Lane.** the text lane.

**Idea.** The is-malicious surface: a PR diff or a directory, chunked, checks in one call per chunk, zoom on the uncertain tail, exit 1 on a high-severity finding, exit 2 if a chunk could not be scanned. Wire it as a job the worker can run before install, and as a comment on the diff. Telemetry findings stay advisory and do not change the exit code.

**Why it is separate from the agent hook.** The hook watches a live tool call. This watches a tree the agent has not executed yet. Same pack, different state.

**Do not.** Do not claim CVE coverage or secret scanning. Do not scan binaries, lockfiles, or .env files. Do not treat a clean exit as a sandbox.

## 9. Claim-level verify on a stream, after the turn, never during it

**Reuse.** Verify pack. This is an expansion of v1 verify, not a new primitive.

**Lane.** the text lane.

**Idea.** v1 verify runs on a finished answer. This idea runs it on each claim the moment the turn commits, against the sources that were actually in the context, and attaches the probabilities to the message before a person sees it. Partial decode is not a state. Do not call d1 on an unfinished sentence. The check is structural: the runtime runs it, the writer cannot skip it.

**Why it is listed.** The failure mode already named is the writer deciding when to verify itself. A streaming mid-decode check feels faster and is the wrong shape. A post-commit check on the claims, with the distribution shown, is the right expansion.

**Do not.** Do not ask d1 to rewrite the claim. Do not verify against a source that was not in the turn.

## 10. Policy calendar and per-action thresholds

**Reuse.** Policy function, audit log.

**Lane.** Either. The policy does not care.

**Idea.** Once the audit log has a few hundred real gate and verify decisions, fit the thresholds per action class instead of one global 0.85. A payment or a push stays high. A read-only lookup can sit lower. The fit is a report over the jsonl, not a retraining. Publish the table in the repo so a change to a threshold is a diff.

**Why it waits.** Thresholds copied from another model are the risk IP-001 already calls out. This idea is how they stop being guesses. It needs the log from real use, which is why it cannot be part of the first plan.

**Do not.** Do not fine-tune d1 to "learn" the threshold. The threshold is the policy. Liquid has no published fine-tune path for these checkpoints, and the library marks them not trainable.

## 11. Paired-lane disagreement as a signal

**Reuse.** Client, both lanes, audit log.

**Lane.** Both, on text only.

**Idea.** The first live probe already showed the split: same payload, d1-3B at 0.99 on node A, d1-omni at 0.64. For a sample of gate and verify calls, run the text state through both lanes and log the gap. A large gap is a reason to ask, even if the primary lane is confident. This is a calibration instrument, not a voter. Do not average the two probabilities.

**Why it is useful.** It is the cheapest second opinion on the fleet, and it uses a lane that otherwise only earns its place on audio.

**Do not.** Do not block a call on disagreement alone until the gap has been measured against outcomes. Do not send images to omni for this. The encoders differ.

## 12. What these ideas are not

A game agent, a self-play loop, and a sensor-fusion drone are real uses of a decision model. They are not expansions of this tool. This repo sits beside an agentic worker. An idea belongs here only if it is a pack, a hook, or a report over the audit log.

**Also out, even later:**

- A chat shim on /v1/chat/completions.
- Fine-tuning either checkpoint.
- Long-form or multi-speaker audio.
- Medical, benefit, or payment decisions as a shipped pack.
- Letting the writer call verify as an optional tool.

## Order, once v1 is proven

- Compaction, because it cuts the context every later call pays for.
- Second-pass zoom, because classify without a line number is a log line.
- Cascade router, because it is the route pack pointed at the fleet.
- Session ledger, because the gate is blind across turns without it.
- CI scan, because it is classify on a diff.
- Screenshot actions and visual inspection, once image calls are warm and timed.
- Spoken intent, only with a side-by-side against Whisper plus d1-3B.
- Threshold table and paired-lane disagreement, once the audit log is long enough to fit.

Each of these should become its own IP when it is picked up. This document is the backlog, not the permission to build them all.
