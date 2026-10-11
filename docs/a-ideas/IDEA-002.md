---
id: IDEA-002
title: "Constraints, documents, least-worst routes, employment rules, entitlement"
status: idea
created: 2026-10-09
updated: 2026-10-09
depends_on: IP-001
lanes:
  text_image: the text lane d1-3B at :8774
  audio: the audio lane d1-omni-600M at :8774
---

# IDEA-002 — Checks over a candidate, not solvers

Status: ideas, not a plan. Do not start these until v0 is accepted: library, hook, and MCP share one policy; a static deny and a lane-down both fail closed; verify flags a contradicting claim on a live call.

These five are the gate and the verify pack pointed at a different state. The decision model does not solve the problem. It scores a candidate you already have against rules written down before the call. The policy, which the model cannot see, turns the probabilities into pass, flag, or refuse.

Employment rules and entitlement are flaggers. They do not hire, fire, grant, or refuse. A wrong allow there is an action taken without a reviewer, and the two lanes have already disagreed on a trivial payload (d1-3B at 0.99, omni at 0.64).

Each idea says what it needs from v0, which lane it uses, and what would make it a bad bet.

## Constraints on a candidate

d1 does not search a constraint space. The caller hands one candidate and a closed set of constraints. One request asks a noul per constraint: hours cap held, rest rule held, vehicle class matches the load, window still open. A choice names the first failing constraint. A score rates how badly it fails, levels lowest first. A second pass runs only if a noul sits in the uncertain band.

Needs: the pack loader, decisiveness \(2\lvert p-0.5\rvert\), and the rule that several questions share one state. Lane: the text lane. Text, or text plus a scan of the roster.

Bad bet if: someone expects the model to propose a new assignment. If no candidate passes, the verdict is `no_feasible_candidate`. A solver or a person builds the next one. Do not add a label for "fix it."

## Document verification

Two checks, two packs. Do not merge them.

Validity is a closed set over the packet: right form, date inside the window, required field present, name on page one matches page two. Choice for the failing field, score for completeness. A scan goes to the text lane as an image, up to eight pages. Support is the v0 verify pack: each claim against the source, a noul for "is this supported," flag below the line. The model does not rewrite the document.

Needs: verify pack for support, classify pack for validity. Lane: the text lane. Run on the finished packet. A partial OCR stream is an incomplete state.

Bad bet if: a clean validity score is treated as proof the claims are true. Validity says the form is filled. Support says the sentences match the source. Neither says the source is true. An unknown document type should trigger abstention, and a clean check does not prove a document is genuine.

## Least-worst route

d1 will not compute a route. A router or a small enumerator produces three to ten feasible paths, each already costed: time, tolls, rest breaks, missed window. The state is that table. Hard constraints are noul questions and run first. A path that breaks the driving-hours rule is out before a score is allowed to prefer it. Over the survivors, a score ranks acceptable to unacceptable, and a choice picks the least-worst label. "Least worst" has to be in the criteria, or the model will pick the one that sounds fastest. If the top two scores sit close, the policy asks instead of dispatching.

Needs: the route pack, plus a caller that already has the shortlist. Lane: the text lane. Text. A map image is optional and must not be paired with audio.

Bad bet if: the shortlist is missing the cost columns, or the criteria say "best" without saying best against what. This is a dispatch decision over a set a solver already made, not an optimiser. Do not use the probability as the cost function; compare the supplied costs and explicit policy trade-offs.

## Employment rules checker

Each rule you are willing to enforce is a noul, and the criteria cite the rule in a sentence. Did the rota breach the daily rest the policy names? Is this deduction one the contract text allows? Does this absence need the document the policy names? A score marks severity. The policy maps that to flag or escalate, with the firing probability attached. A person owns the action. Low decisiveness is an ask, not a pass.

Needs: the gate pack's shape, an audit log with the rule version, and a reviewer on every flag. Lane: the text lane. No audio.

Bad bet if: the criteria include "fire," "withhold pay," or "dismiss." Those labels do not belong in the pack. Also a bad bet if the rule text is not in the state. The model must not be asked to recall the statute.

## Entitlement verifier

Same machine, stricter policy. The state is the packet and the rule text you supply. Nouls are "does the packet contain the named evidence" and "does the stated fact match the rule as written." A choice names the missing item. The verdict set is `complete`, `incomplete`, `conflict`. Not `granted`. Not `refused`. Conflict and anything under the line go to a person. Missing facts force `incomplete` or `conflict`, never a pass. The audit log keeps the probabilities and the rule version, not a prose justification the model never produced.

Needs: v0 verify, calibrated thresholds from real traces, and a human on the verdict. Lane: the text lane. This one does not get an autonomous hook.

Bad bet if: it is wired to pay, refuse, or write back to a case system. It is also a bad bet on omni. The lanes disagree, and this is the check where a confident wrong answer costs the most.

## Order

1. Document validity, because it is the classify pack on a form.
2. Constraints on a candidate, because it is several noul questions and no new machinery.
3. Least-worst route, once a caller can hand in a costed shortlist.
4. Employment rules, only as a flagger, after the audit log has real gate traces.
5. Entitlement, last, and only with a person on the verdict.

Each of these should become its own IP when it is picked up. This document is the backlog, not the permission to build them.

## What not to add

A solver. A route optimiser. A label that hires, fires, grants, or refuses. A check that runs on a partial document. A call per rule when the rules fit in one state. A threshold the model can see or set.
