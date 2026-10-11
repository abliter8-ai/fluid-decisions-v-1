# IP-001 phase 0 — contract grounding

2026-10-09. Recorded evidence: [contract-probe.json](../contract-probe.json).

Both explicitly selected maintainer lanes returned HTTP 200 and zero output tokens for a single request containing choice, score and noul, image input, and a batch of two. Omni additionally accepted a one-second 16 kHz WAV; combined image/audio returned HTTP 422. No service configuration was changed.

The exact noul answer is `{"type":"noul","noul":p}`. No confidence field is supplied for noul. The shared question returned 0.503906 on max and 0.127079 on omni. Distributions remain separate. The second repeated text request took 136.1 ms on max and 47.9 ms on omni (client time). These are warm service measurements, not cold-start or universal hardware claims.

Health identifies max as MPS/float16 at revision `da1fe36a861f24690f27f622dca1d8688503d113`; omni as CUDA/float16 at `414f8d6438174f5b2133a9c21a478fc42625e308`. The maintained fleet registry and source were inspected read-only. The supplied skill describes the hosted API; this recorded local multimodal envelope is the implementation contract.

Validation: 10 offline stdlib probe tests passed before live calls; the complete live probe exited 0 with no validation failures. An initial helper dict/set error was fixed and covered before the successful run. Unit fixtures contain actual lane responses. No gate implementation preceded this evidence.

Community serving and phase 1 acceptance remain open and will be recorded in the matching completion report.
