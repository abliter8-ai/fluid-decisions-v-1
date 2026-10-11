"""Real-trace harness (IP-005).

Pure, importable parts of the harness: the agent loop state machine, the trap
registry and deterministic matchers, label derivation and the trace export. The
only module that reaches a container runtime is :mod:`harness.sandbox`; the entry
point is :mod:`scripts.trace_harness`.

Nothing here loads a model, opens a socket or shells out except ``sandbox``. The
loop and matchers take injected callables so they can be unit-tested with a fake
gateway and a fake executor.
"""
from __future__ import annotations

from harness.loop import AgentLoop, LoopResult, ToolCall, ToolOutcome
from harness.traps import Trap, TrapMatch, derive_labels, load_traps, match_call, match_result

__all__ = [
    "AgentLoop",
    "LoopResult",
    "ToolCall",
    "ToolOutcome",
    "Trap",
    "TrapMatch",
    "derive_labels",
    "load_traps",
    "match_call",
    "match_result",
]
