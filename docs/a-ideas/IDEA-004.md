---
id: IDEA-004
title: "Quantized d1: squeeze the memory envelope without losing calibration"
status: idea
created: 2026-10-09
updated: 2026-10-09
depends_on: IP-001, IP-002 §3
lanes:
  reference: the d1-3B host d1-3B bf16 at :8774 (fleet, transformers on MPS)
  candidates: llama-server on the d1-3B host, loopback, spare ports, one at a time
---

# IDEA-004 — Quantized d1

Status: idea with grounded facts, no measurements yet. Proposed by the maintainer on 2026-10-09: if the serving adapter is reloaded for IP-002 §3 anyway, compare Q8 and Q4_K_M variants against the bf16 reference and see how far the memory envelope can be squeezed before quality or accuracy moves.

## What is true today (checked 2026-10-09)

**The reference costs 7.3 GB.** The fleet d1-3B on the d1-3B host is the bf16 safetensors checkpoint (5.82 GB on disk, 707 tensors, all BF16) loaded through Transformers 5.19 on MPS as float16. Physical footprint of the serving process is 7.3 GB, peak 9.2 GB. The omni process on the omni host sits at 1.9 GB resident.

**Liquid publishes quantized variants themselves.** No conversion step is needed for the first pass:

| Repo | Files | Size | Route |
| --- | --- | --- | --- |
| `LiquidAI/d1-3B-GGUF` | `Q8_0` 2.87 GB, `Q4_K_M` 1.67 GB, `F16`/`BF16` 5.40 GB; `mmproj` for images at Q8_0 0.58 GB or F16 0.85 GB | | llama.cpp |
| `LiquidAI/d1-omni-600M-GGUF` | `Q8_0` 0.41 GB, `F16`/`BF16` 0.76 GB; `mmproj` Q8_0 0.26 GB. **No Q4 published.** | | llama.cpp |
| `LiquidAI/d1-3B-w8a8` | torchao INT8 weights and activations, 3.8 GB on disk | | Transformers + torchao, **NVIDIA Ampere or newer only**. The card says to use plain d1-3B on CPUs, Apple silicon and AMD. |

Community repos also exist (MLX 4-bit and 8-bit, AutoRound W4A16, ONNX, CoreML, imatrix GGUFs). They are unverified third-party conversions and are not candidates for the first pass.

**llama-server speaks the same contract.** The official GGUF card starts `llama-server -hf LiquidAI/d1-3B-GGUF:Q8_0` and posts to `/v1/systemone`. Upstream llama.cpp added native decision-model serving in early October 2026 (`<arch>.decision.type` in the GGUF, `/v1/systemone` in the server). The Homebrew build on the d1-3B host, 0.6.0 build 11429, has the `systemone` route compiled into `libllama-server-impl.dylib` and `decision.type` in `libllama-common`. Whether it handles d1's `lfm2_vl` architecture, the batch route, and images through `mmproj` is the first thing to test; it was not exercised.

**What this means for the comparison.** The fluid-decisions client, policy, hook and packs do not change. A quantized lane is a URL. The adapter in `fluid/serving.py` is not involved in the GGUF arms at all; llama-server is the serving process. That also makes this the cheapest possible community path: no PyTorch, no custom-code execution, a 1.7 GB or 2.9 GB download.

**How d1 reads an answer, and why that matters for quantization.** The runner takes the log-softmax at a single answer slot in one pass (`runner.py`) and the API turns those logits into the noul probability, the choice distribution and the expected score level. Quantization error lands directly on those probabilities. The policy thresholds sit on the probabilities, so the quantity to measure is probability drift and verdict agreement, not just argmax accuracy. A Q4 model that picks the same option but moves a noul from 0.72 to 0.66 crosses a 0.70 line without ever being "wrong" on a benchmark.

## The comparison

Arms, all on the d1-3B host, loopback, one candidate loaded at a time, the fleet lane left as it is:

| Arm | Serving | Expected resident memory |
| --- | --- | --- |
| A, reference | fleet d1-3B bf16 on `:8774` | 7.3 GB, measured |
| B | llama-server, `d1-3B-GGUF:Q8_0` plus `mmproj` Q8_0 | about 3.5 GB plus context |
| C | llama-server, `d1-3B-GGUF:Q4_K_M` plus `mmproj` Q8_0 | about 2.3 GB plus context |
| D, optional | llama-server, `d1-3B-GGUF:F16` | about 6.3 GB; isolates llama.cpp's own numerics from quantization |
| E, optional | llama-server, `d1-omni-600M-GGUF:Q8_0` | about 0.7 GB; audio support in llama-server to be confirmed first |

Measure, per arm:

- **Contract.** Re-run `scripts/probe_contract.py` against the arm. Shapes, `output_tokens: 0`, batch of two, image via `mmproj`, and 422 on image plus audio must all hold. Any contract difference is recorded, not patched around.
- **Memory.** Physical footprint of the process after warm-up and after the full fixture set, plus peak.
- **Latency.** Warm single-question, the 12-question gate pack, and a 2-item batch, median of 20 after warm-up. The card's 65 ms figure is not a target.
- **Probability drift.** Over a fixture set of recorded states and packs, the absolute difference per answer against arm A: noul probability, each choice option's probability, each score level's probability, and score value. Report the distribution, not the mean alone. Flag any answer where drift crosses a pack threshold (0.40, 0.55, 0.70, 0.85 in the current uncalibrated packs).
- **Verdict agreement.** Run the four pure policies on each arm's answers and count verdict changes against arm A, per pack and per threshold. This is the number that decides whether an arm is acceptable, because it is what the hook would have done differently.
- **Decisiveness drift.** For nouls, the change in `2|p − 0.5|`. Quantization tends to flatten distributions; a drop here means more `ask` verdicts, which is a cost even when no verdict flips.
- **Calibration.** On any fixture subset with human labels (IDEA-003's packet set once it exists), expected calibration error per arm. Without labels, drift and agreement stand in for it and the report says so.

The fixture set is recorded, not synthetic: the Phase 0 probe payloads, the states in `docs/lane-workflow-smoke.json`, the IDEA-003 packets, and gate states drawn from the audit log of real sessions. At least a few hundred decisions across all four packs. States are hashed in the report; the raw fixture file stays out of git if it contains anything from a real session.

## Decision rule to propose, before the numbers exist

An arm is acceptable for the community default if, against the bf16 reference on the fixture set, verdict agreement is at or above a line the maintainer sets, no unsoftenable axis flips from fired to not fired, and decisiveness does not drop by more than a line the maintainer sets. Both lines go in the report as uncalibrated choices, the same way the pack thresholds do. If Q8_0 passes and Q4_K_M does not, the self-hosting guide offers Q8_0 as the low-memory default and documents Q4_K_M as untested for gating. If both pass, Q4_K_M becomes the smallest documented path and the guide says what was measured.

## What it needs

- IP-002 §3 approved and done, so there is one recorded adapter run to compare against and the memory-pressure checks on the d1-3B host are already written down.
- A free port and about 4 GB of headroom on the d1-3B host for one candidate at a time. Nothing is loaded alongside the fleet lane permanently.
- A download of the official GGUF files into the ignored `models/` directory with their revisions recorded, through the existing download script extended to accept a GGUF repo, or by `llama-server -hf` with the cache path recorded.
- A small comparison script under `scripts/` that takes two lane URLs and a fixture file and writes the drift and agreement tables. Pure Python, stdlib plus the existing client.
- The llama.cpp build on the d1-3B host confirmed to serve `lfm2_vl` decision GGUFs with `mmproj`. If build 11429 refuses the model, the Homebrew formula is upgraded in a separate step with the maintainer's go-ahead, since other things on this host may use it.

## Out of scope

- Converting or quantizing weights ourselves. The official GGUFs and w8a8 exist; third-party conversions are not candidates until the official ones are measured.
- The w8a8 arm on this fleet. It needs INT8 tensor cores on NVIDIA Ampere or newer. It becomes relevant only if a Spark node is ever used as a d1 lane, and that is a separate idea.
- Replacing the fleet lanes. The fleet keeps bf16 until a measured arm earns a change, and that change is its own plan.
- KV-cache quantization. d1 runs one prefill and no decode, so the cache is short-lived and the usual KV tricks buy little here.
- Omni Q4. Liquid did not publish one, and audio through llama-server is unconfirmed.

## Bad bet if

- Someone reads argmax agreement as the result. The policy acts on probabilities and thresholds; a quant that agrees on every pick and drifts every noul by 0.05 can change a third of the verdicts near a line.
- The fixture set is tiny or synthetic. Drift is concentrated in the uncertain band, which synthetic states rarely hit.
- The community guide quietly makes Q4_K_M the default because it is smallest. The guide offers what was measured, with the numbers.
