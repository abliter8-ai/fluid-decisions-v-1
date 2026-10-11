# mypy: ignore-errors
"""The Fluid Decisions guard as AgentDojo pipeline elements (IP-008).

Optional: requires the ``agentdojo`` extra. Two elements wrap
:class:`fluid.service.Decisions` so the guard runs as an AgentDojo defense without
changing any guard behaviour — this is an adapter, nothing is calibrated or
decided here.

- :class:`FluidGateElement` runs before the tool executor and gates each pending
  tool call (``Decisions.gate``). In enforce mode a non-allow verdict blocks that
  one call: the call is dropped from the assistant message and a refusal
  tool-result is returned in its place, so the episode continues and the model is
  told why (the plan's "refusal as a tool-result message").
- :class:`FluidPostResultElement` runs after the tool executor and checks each
  tool result produced that turn (``Decisions.post_result``). A confirmed unit is
  released redacted exactly as :class:`fluid.service.GuardedRuntime` does; a
  withheld result (a static hit, or a withhold policy) becomes a marker. In shadow
  mode the result is recorded but returned unchanged.

The two elements share one ``Decisions`` and a per-episode session id carried in
``extra_args``, so provenance, taint and withheld-path memory accumulate within an
episode and reset between episodes.
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from agentdojo.agent_pipeline import BasePipelineElement
from agentdojo.functions_runtime import EmptyEnv, Env, FunctionsRuntime
from agentdojo.types import (
    ChatMessage,
    ChatToolResultMessage,
    get_text_content_as_str,
    text_content_block_from_string,
)

from fluid.service import Decisions

WITHHELD_MARKER = "[fluid-decisions withheld tool result]"
_BLOCK_ERROR_PREFIX = "blocked_by_guard"
_WITHHOLD_ERROR_PREFIX = "withheld_by_guard"


def _session_id(extra_args: dict) -> str:
    """One session id per episode, carried in extra_args so both elements and all
    loop iterations share it; a fresh episode gets a fresh id."""
    session_id = extra_args.get("fluid_session_id")
    if not isinstance(session_id, str) or not session_id:
        session_id = uuid.uuid4().hex
        extra_args["fluid_session_id"] = session_id
    return session_id


class FluidGateElement(BasePipelineElement):
    def __init__(self, decisions: Decisions, *, enforce: bool = True) -> None:
        self.decisions = decisions
        self.enforce = enforce

    def query(self, query: str, runtime: FunctionsRuntime, env: Env = EmptyEnv(),
              messages: Sequence[ChatMessage] = [], extra_args: dict = {},
              ) -> tuple[str, FunctionsRuntime, Env, Sequence[ChatMessage], dict]:
        if not messages or messages[-1]["role"] != "assistant":
            return query, runtime, env, messages, extra_args
        assistant = messages[-1]
        calls = assistant.get("tool_calls") or []
        if not calls:
            return query, runtime, env, messages, extra_args
        session_id = _session_id(extra_args)
        verdicts: list[tuple[Any, dict, bool]] = []
        any_blocked = False
        for call in calls:
            decision = self.decisions.gate(call.function, dict(call.args),
                                           user_request=query, session_id=session_id)
            blocked = self.enforce and (decision.get("verdict") != "allow"
                                        or not decision.get("complete", True))
            any_blocked = any_blocked or blocked
            verdicts.append((call, decision, blocked))
        if not any_blocked:
            return query, runtime, env, messages, extra_args
        # Enforce: refuse the whole step. The assistant message is kept intact and a
        # refusal tool-result is returned for every one of its tool calls, so the
        # executor (which only runs when the last message is an assistant turn)
        # skips the step and every tool_use keeps exactly one tool_result — the
        # structure an Anthropic-style API requires. A benign call sharing a step
        # with a blocked one is withheld too; the model is told to reissue it alone.
        refusals: list[ChatToolResultMessage] = []
        for call, decision, blocked in verdicts:
            if blocked:
                reasons = ", ".join(str(r) for r in decision.get("reasons", [])) or str(decision.get("verdict"))
                text = f"The safety guard did not allow this tool call ({decision.get('verdict')}): {reasons}."
                error = f"{_BLOCK_ERROR_PREFIX}: {decision.get('verdict')}"
            else:
                text = ("Not executed: the safety guard blocked another tool call in this step. "
                        "Reissue this call on its own.")
                error = f"{_BLOCK_ERROR_PREFIX}: step_withheld"
            refusals.append(ChatToolResultMessage(
                role="tool", content=[text_content_block_from_string(text)],
                tool_call_id=call.id, tool_call=call, error=error))
        return query, runtime, env, [*messages, *refusals], extra_args


class FluidPostResultElement(BasePipelineElement):
    def __init__(self, decisions: Decisions, *, enforce: bool = True) -> None:
        self.decisions = decisions
        self.enforce = enforce

    def query(self, query: str, runtime: FunctionsRuntime, env: Env = EmptyEnv(),
              messages: Sequence[ChatMessage] = [], extra_args: dict = {},
              ) -> tuple[str, FunctionsRuntime, Env, Sequence[ChatMessage], dict]:
        if not messages:
            return query, runtime, env, messages, extra_args
        session_id = _session_id(extra_args)
        out = list(messages)
        for index in range(len(out) - 1, -1, -1):
            message = out[index]
            if message["role"] == "assistant":
                break  # only the tool results produced since the last assistant turn
            if message["role"] != "tool":
                continue
            if str(message.get("error") or "").startswith(_BLOCK_ERROR_PREFIX):
                continue  # our own gate refusal, not a real tool result
            text = get_text_content_as_str(message["content"])
            tool_call = message.get("tool_call")
            tool_name = tool_call.function if tool_call is not None else ""
            post = self.decisions.post_result(text, session_id=session_id,
                                              tool_name=tool_name, user_request=query)
            if not self.enforce:
                continue
            released = post.get("verdict") == "allow" and post.get("complete", True)
            redacted = (not released and post.get("verdict") == "deny" and post.get("complete", True)
                        and post.get("on_confirmed") == "redact"
                        and isinstance(post.get("redacted_text"), str) and bool(post.get("units_redacted")))
            if redacted:
                out[index] = {**message,
                              "content": [text_content_block_from_string(post["redacted_text"])]}
            elif not released:
                out[index] = {**message,
                              "content": [text_content_block_from_string(WITHHELD_MARKER)],
                              "error": f"{_WITHHOLD_ERROR_PREFIX}: {post.get('verdict')}"}
        return query, runtime, env, out, extra_args


# --- LLM-as-judge arm (IP-008) ---------------------------------------------
#
# The judge scorer, its prompts and its decision logic live in fluid.judge (no
# agentdojo dependency) so the replay script sends byte-identical prompts; the
# pipeline elements below adapt it to AgentDojo.

from fluid.judge import JudgeScorer, _extract_json, _prob  # noqa: E402,F401


class JudgeGateElement(BasePipelineElement):
    """Mirror of :class:`FluidGateElement`, scored by :class:`JudgeScorer`."""

    def __init__(self, scorer: JudgeScorer) -> None:
        self.scorer = scorer

    def query(self, query: str, runtime: FunctionsRuntime, env: Env = EmptyEnv(),
              messages: Sequence[ChatMessage] = [], extra_args: dict = {},
              ) -> tuple[str, FunctionsRuntime, Env, Sequence[ChatMessage], dict]:
        if not messages or messages[-1]["role"] != "assistant":
            return query, runtime, env, messages, extra_args
        calls = messages[-1].get("tool_calls") or []
        if not calls:
            return query, runtime, env, messages, extra_args
        verdicts: list[tuple[Any, dict, bool]] = []
        any_blocked = False
        for call in calls:
            decision = self.scorer.gate(call.function, dict(call.args), query)
            blocked = decision.get("verdict") != "allow"
            any_blocked = any_blocked or blocked
            verdicts.append((call, decision, blocked))
        if not any_blocked:
            return query, runtime, env, messages, extra_args
        refusals: list[ChatToolResultMessage] = []
        for call, decision, blocked in verdicts:
            if blocked:
                reasons = ", ".join(str(r) for r in decision.get("reasons", [])) or str(decision.get("verdict"))
                text = f"The safety judge did not allow this tool call ({decision.get('verdict')}): {reasons}."
                error = f"{_BLOCK_ERROR_PREFIX}: {decision.get('verdict')}"
            else:
                text = ("Not executed: the safety judge blocked another tool call in this step. "
                        "Reissue this call on its own.")
                error = f"{_BLOCK_ERROR_PREFIX}: step_withheld"
            refusals.append(ChatToolResultMessage(
                role="tool", content=[text_content_block_from_string(text)],
                tool_call_id=call.id, tool_call=call, error=error))
        return query, runtime, env, [*messages, *refusals], extra_args


class JudgePostResultElement(BasePipelineElement):
    """Mirror of :class:`FluidPostResultElement`, scored by :class:`JudgeScorer`.
    A confirmed injection withholds the whole result (the judge scores whole text,
    so there is no unit redaction)."""

    def __init__(self, scorer: JudgeScorer) -> None:
        self.scorer = scorer

    def query(self, query: str, runtime: FunctionsRuntime, env: Env = EmptyEnv(),
              messages: Sequence[ChatMessage] = [], extra_args: dict = {},
              ) -> tuple[str, FunctionsRuntime, Env, Sequence[ChatMessage], dict]:
        if not messages:
            return query, runtime, env, messages, extra_args
        out = list(messages)
        for index in range(len(out) - 1, -1, -1):
            message = out[index]
            if message["role"] == "assistant":
                break
            if message["role"] != "tool":
                continue
            if str(message.get("error") or "").startswith(_BLOCK_ERROR_PREFIX):
                continue
            text = get_text_content_as_str(message["content"])
            post = self.scorer.post_result(text, query)
            if post.get("verdict") != "allow":
                out[index] = {**message,
                              "content": [text_content_block_from_string(WITHHELD_MARKER)],
                              "error": f"{_WITHHOLD_ERROR_PREFIX}: {post.get('verdict')}"}
        return query, runtime, env, out, extra_args
