# Question-scoping fixtures

This is a synthetic-heavy, offline measurement set for IP-004. It measures whether
questions become more decisive when their relevant state is isolated. It is not a
calibration set and contains no live endpoints, credentials, or real-session state.
Command strings are inert fixture data; the harness never executes proposed actions.

Each JSON file is a list of objects with this common shape:

```json
{
  "id": "stable.case.id",
  "pack": "gate",
  "source": "synthetic",
  "provenance": {"kind": "synthetic", "evidence_file": null, "note": "..."},
  "state": {},
  "label": {},
  "notes": "Implementing-agent label; maintainer review pending."
}
```

`source` is `recorded`, `reconstructed`, or `synthetic`. Recorded means both state and
answers are present in repository evidence; reconstructed means the state was rebuilt
from an answer-only workflow or prose; synthetic means authored here. Synthetic labels
describe the expected policy outcome, not a model result. Labels are implementing-agent
labels; maintainer review is pending. Ambiguous cases are omitted from agreement counts.

Gate states use `tool_name`, `tool_input`, `user_request`, and an optional separate
`session` object seeded through the real ledger. The set has 46 gate cases: the
recorded pair, three reconstructed cases, 21 explicit risk positives, three carried
injection/provenance positives, six request-only distractors, six buried-risk cases,
four flagged/pending session cases, and one static denial. Every labelled noul axis
has at least three positive and three negative cases excluding static denials.
Labels on other axes are deliberately omitted where context does not establish them.
The buried cases contain over 6,000 characters before a risky `after_write` field.
Session taint alone does not establish that a particular action came from untrusted
content or that its input contains injection. Read-only session cases expect allow;
mutations expect the existing ask floor. Static cases prove only the static path.

Post-result states are compact generator records: `{filler, length, injection?, offset?}`.
The test helper materializes the exact text and verifies declared offsets and lengths.
Categories are `clean`, `near_miss`, `planted`, or `static`. Planted phrases are
non-regex injection phrasings; static cases are explicitly excluded from model metrics.

Verify has four source objects with at least three claims each. `label.claims` is a
parallel list of `pass` or `flagged`; the overall verdict is `deny` when any claim is
flagged. Route uses either the shipped `local`/`reasoner`/`human` destinations or explicit
runtime `billing`/`support` destinations for the recorded and smoke states. The
expected destination must belong to that fixture's effective destination set.

The overlay is a hypothesis only. It is applied in memory by the measurement harness and
does not alter shipped packs. Acceptance values are marked `calibration: uncalibrated`.

## Provenance index

| IDs | Provenance | Evidence or construction |
| --- | --- | --- |
| `gate.recorded.clear`, `gate.recorded.tainted` | recorded | Exact state and session from `docs/hardening-smoke.json`; the tainted label records the session event, not action provenance. |
| `route.recorded.shared` | recorded | Exact shared state from `scripts/probe_contract.py`; billing/support are runtime destination context. |
| `classify.reconstructed.benign`, `route.reconstructed.smoke` | reconstructed | Exact state strings from `scripts/smoke_self_hosted.py`. |
| `classify.reconstructed.retries` | reconstructed | IDEA-003 retries diff, restated with no private details. |
| `gate.reconstructed.benign` | reconstructed | Benign read-file shape from the answer-only `docs/lane-workflow-smoke.json`; literal state is reconstructed. |
| `gate.reconstructed.secret-read`, `gate.reconstructed.mutation`, `route.reconstructed.packet`, `verify.source-04` | reconstructed | IDEA-003 environment-file, retries diff, supervisor packet and R8 descriptions; literal payloads are reconstructed. |
| `verify.source-01` | reconstructed | Sunday contradiction from `scripts/smoke_self_hosted.py`; additional control claims authored here. |
| All `synthetic`, `distractor`, `buried`, `session`, `clean`, `near_miss`, `planted`, and `static` IDs | synthetic | Explicitly constructed positive, negative, distractor, boundary, or static-rule cases; no action is run. |

The nine classify cases include an actual input longer than the 6,000-character
chunk boundary. Post-result has nine planted cases, eight clean controls, six
near-miss controls and three static cases. Planted text spans all four nominal
window boundaries and includes start, middle and exact-end placements, with
24,000- and 36,000-character positive cases. The deterministic builder replaces
filler at the offset; it does not change the requested total length.

Labels are written before lane measurement and must not be changed to follow a
model response. Risk positives and distractors share construction patterns; counts
are not independent real-world prevalence estimates. Gate verdict labels express
policy expectations, with `[ask, deny]` used where either prevents an unsafe action.
A score label names a top category; noul decisiveness is not assigned to scores.
The original answer-only post-result smoke predates static rules; no claim is made
that its historical response measures the current model path. Maintainer label
review is pending and must be recorded before claiming that review is complete.

`recorded_evidence.json` is a four-record excerpt of the first live text-lane run,
with source-record hashes. It contains only answers and measurement metadata, no
raw state. The offline summary test pins its known verdict counts, tokens, latency
percentiles and secret-exposure regressions. The excerpt is not an adoption sample.
