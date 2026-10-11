"""Offline tests for the pure summarize() of scripts/measure_facts.py (IP-006 s2).

No network and no lane: summarize() must replay from recorded per-call evidence
and never pool separate (model, mode) groups.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import measure_facts as mf  # noqa: E402

G1 = ["Abliter8/GLM-5.3-Flash-NoThink", "enforce"]
G2 = ["Abliter8/Qwen-3.8-FlashNext", "shadow"]


def _obs(question, agreement_half, decisiveness):
    return {"question": question, "item": "main",
            "answer": {"type": "noul", "noul": 0.5, "decisiveness": decisiveness},
            "label": True, "agreement_half": agreement_half, "agreement_lines": {},
            "decisiveness": decisiveness}


def _rec(arm, group, benign, matched, observations, *, verdict_agreement=True,
         verdict="allow", answers=None, client_ms=100.0):
    return {"arm": arm, "groups": [group], "benign": benign, "matched": matched,
            "observations": observations, "verdict": verdict,
            "verdict_agreement": verdict_agreement, "complete": True, "model_scored": True,
            "timings": {"ms": 90.0, "client_ms": client_ms, "wall_ms": client_ms},
            "input_tokens": 50, "chars": 300, "questions": 12, "answers": answers or {},
            "state_hash": "h", "tool_name": "t"}


def test_summarize_separates_groups_and_detects_facts_regression():
    records = [
        # G1: packet reads destructive confidently right; facts reads it worse.
        _rec("packet", G1, True, [], [_obs("destructive", True, 0.8)]),
        _rec("facts", G1, True, [], [_obs("destructive", False, 0.2)]),
        # G1: one trap call, denied, scored on destructive.
        _rec("packet", G1, False, ["T2-destructive-static"], [_obs("destructive", True, 0.9)],
             verdict="deny", answers={"destructive": {"type": "noul", "noul": 0.9}}),
        _rec("facts", G1, False, ["T2-destructive-static"], [_obs("destructive", True, 0.9)],
             verdict="deny", answers={"destructive": {"type": "noul", "noul": 0.9}}),
        # G2: only packet records — a different group, must not pool into G1.
        _rec("packet", G2, True, [], [_obs("destructive", True, 0.7)]),
    ]
    summary = mf.summarize({"records": records, "arms": ["packet", "facts"]})
    assert set(summary) == {"Abliter8/GLM-5.3-Flash-NoThink | enforce",
                            "Abliter8/Qwen-3.8-FlashNext | shadow"}
    g1 = summary["Abliter8/GLM-5.3-Flash-NoThink | enforce"]
    # The facts arm lost ground on destructive against the packet baseline.
    assert set(g1["regressions"]["destructive"]["metrics"]) == {"agreement_half", "decisiveness"}
    # Benign vs trap agreement are reported separately, not merged.
    assert g1["arms"]["packet"]["benign_calls"] == 1
    assert g1["arms"]["packet"]["trap_calls"] == 1
    assert g1["arms"]["facts"]["trap_verdict_agreement"]["rate"] == 1.0
    assert g1["arms"]["packet"]["trap_scores"][0]["axes"]["destructive"] == 0.9
    # G2 has no facts arm, so there is nothing to regress and it stays isolated.
    g2 = summary["Abliter8/Qwen-3.8-FlashNext | shadow"]
    assert "facts" not in g2["arms"]
    assert g2["regressions"] == {}
    assert g2["arms"]["packet"]["calls"] == 1


def test_resolve_phrasings_keyword_and_apply_to_the_pack():
    from fluid.policy import load_pack
    # The keyword selects the built-in §5 destructive criteria pair.
    phrasings = mf._resolve_phrasings("destructive")
    assert set(phrasings) == {"action.destructive"}
    assert "cannot be undone" in phrasings["action.destructive"]
    assert mf._resolve_phrasings(None) is None
    # Applied, it replaces only the destructive instruction, nothing else.
    pack = load_pack("gate")
    base = pack["questions"]["action"]["destructive"]["instructions"]
    out = mf._apply_phrasings(pack, phrasings)
    assert out["questions"]["action"]["destructive"]["instructions"] == phrasings["action.destructive"]
    assert out["questions"]["action"]["exfiltration"]["instructions"] == \
        pack["questions"]["action"]["exfiltration"]["instructions"]
    assert base != phrasings["action.destructive"]
