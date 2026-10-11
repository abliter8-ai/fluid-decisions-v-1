# Changelog

All notable changes to this project are documented here. This project is pre-1.0;
the policy thresholds are **uncalibrated** and marked so in the packs until an audit
log of real sessions exists to calibrate them.

## Unreleased

### Fixed
- **`fluid proxy` forwarded raw injected text after the first turn.** A harness
  resends its own raw history on every request; the proxy remembered which tool
  results it had scored but skipped them on later requests, so a redaction or
  withhold applied only to the first forward. The per-session record now maps
  each result to its replacement and substitutes it on every request.
- **`fluid proxy` forwarded raw text on an `ask` or a scoring error.** Only a
  `deny` withheld a tool result; an `ask` from post-result (a ledger failure, or
  an operator's withhold policy for unscored results) and the fail-closed path on
  a post-result exception both passed the raw text through. Both now withhold,
  with reasons `post_result_ask:<reasons>` and `post_result_error:<type>`.
- **CI failed at test collection on every push.** Bare `pytest` could not import
  `scripts` and `harness`; `pythonpath = ["."]` in the pytest configuration makes
  `pytest` and `python -m pytest` agree. Behind that, the workflow set the lane
  variables to empty strings, which the client treats as "disable this lane", so
  four offline client tests failed; the workflow now leaves them unset.

## v0.1.0 — 2026-10-11

First tagged release: a client, a policy layer and adapters in front of two d1
decision lanes. The model never decides the action; policy code does.

### Jobs and surfaces
- **Gate** — small typed facts over a tool call, composed in policy into axes and a
  verdict (allow / ask / deny). Static denials (`rm -rf`, force push, `DROP TABLE`,
  catastrophic removals) run before the model and with the lane down.
- **Post-result** — locates and confirms injected instructions in tool results over
  small units; a confirmed unit is redacted in place, a static hit withholds the
  result. A static hit now also arms the proportionate taint check.
- **Session ledger, taint and provenance** — append-only, text-free; proportionate
  taint arms a per-instruction `carries_out` check instead of a blanket floor.
- **Candidate checks** — constraints and document-validity jobs (IP-009).
- **File ownership** (IP-011) — a delete is judged by who made the targets:
  created-this-session or under a scratch root is contextual, git-tracked is an ask
  floor, outside the working directory is denied, the catastrophic forms are always
  denied. Facts are deterministic and never call the lane.
- **Surfaces** — library `Decisions`, the stdin hook and hook daemon, the MCP server,
  the CLI, and `fluid proxy`: a loopback OpenAI-compatible server that interposes the
  guard in front of an unmodified agent harness (request-path post-result, response-
  path gate, a refusal recovery loop and masked refused-argument logging).

### Measured, indicative, uncalibrated
- **CR-010** — through `fluid proxy`, an unmodified open-source agent on a local model
  had an injected tool result withheld, the follow-on secret-exfiltration refused, and
  the benign task completed, in one run.
- **CR-008** (AgentDojo `workspace` / `important_instructions`, abliterated local agent)
  — the guard cut targeted attack success far below the undefended baseline while
  keeping or improving task utility; an independent chat-model judge matched the guard
  on blocking but did not preserve utility and cost roughly an order of magnitude more.
  Small task slices, indicative, not a settled ranking.
- **IP-006 / IP-007** — the facts gate and proportionate taint; lines are starting
  points, not calibrated.

These numbers rest on small slices and uncalibrated thresholds. v0.2.0 is where
calibration from real proxy sessions is allowed to call the thresholds calibrated.
