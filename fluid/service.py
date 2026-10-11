"""Shared workflows used by the library, runtime, CLI and MCP surfaces."""
from __future__ import annotations

import hashlib
import json
import uuid
from collections import OrderedDict
from copy import deepcopy
from typing import Any, Callable

from fluid import ownership as ownership_mod
from fluid import provenance
from fluid.audit import Audit
from fluid.client import ClientError, D1Client
from fluid.facts import (
    build_action,
    build_gate_facts,
    build_instruction_items,
    interpreter_script_path,
)
from fluid.ledger import Ledger
from fluid.policy import (
    apply_ownership,
    classify_needs_second_pass,
    command_texts,
    compose_gate_facts,
    evaluate_classify,
    evaluate_constraints,
    evaluate_gate,
    evaluate_route,
    evaluate_validity,
    evaluate_verify,
    load_pack,
    static_denial,
    static_injection,
    taint_check,
    taint_floor,
)
from fluid.scoping import build_requests, merge_results, strip_scopes
from fluid.units import redact_units, split_units


def failure(reason: str) -> dict[str, Any]:
    return {'verdict': 'ask', 'reasons': [reason], 'answers': {}, 'complete': False}


def _state_chars(state: Any) -> int:
    return len(json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(',', ':')))


class Decisions:
    def __init__(self, client: D1Client | None = None, audit: Audit | None = None,
                 ledger: Ledger | None = None):
        self.client = client or D1Client()
        self.audit = audit or Audit()
        self.ledger = ledger or Ledger()
        self._warmed: set[str] = set()
        self._primed: set[str] = set()
        # Bounded in-memory script content cache per session (IP-006 section 2):
        # filled from released read_file results, consumed only by the facts gate
        # to let the destructive question see a script the agent read rather than
        # its name. Never persisted; dropped when the session is cleared.
        self._script_cache: dict[str, OrderedDict[str, str]] = {}
        # Unscored result text kept in memory so the runtime can recheck it at the
        # next turn with a wider budget (IP-007 §2). Bounded, per session, never
        # persisted, dropped when the session is cleared.
        self._pending_cache: dict[str, OrderedDict[str, str]] = {}
        # Text of units confirmed as directives, keyed by result hash (IP-007 §3).
        # The facts gate reads these as the instructions a flag arms a carries_out
        # check against. Bounded, per session, never persisted, cleared with it.
        self._instruction_cache: dict[str, OrderedDict[str, str]] = {}

    def close(self) -> None:
        self.client.close()

    def _cache_pending(self, session_id: str, result_hash: str, text: str, post_policy: dict[str, Any]) -> None:
        if not session_id or not isinstance(text, str):
            return
        limit = int(post_policy.get('pending_cache_chars', 200000))
        entries = max(1, int(post_policy.get('pending_cache_entries', 8)))
        if len(text) > limit:  # too large to recheck meaningfully; leave it pending
            return
        cache = self._pending_cache.setdefault(session_id, OrderedDict())
        cache[result_hash] = text
        cache.move_to_end(result_hash)
        while len(cache) > entries:
            cache.popitem(last=False)

    def _cache_instruction(self, session_id: str, key: str, text: str,
                           taint_cfg: dict[str, Any]) -> None:
        """Keep the text of a unit confirmed as a directive, keyed by result hash
        (IP-007 §3). Truncated to ``instruction_cache_chars`` and bounded to
        ``instruction_cache_entries`` most-recent entries. Never persisted."""
        if not session_id or not isinstance(text, str) or not text:
            return
        limit = int(taint_cfg.get('instruction_cache_chars', 4000))
        entries = max(1, int(taint_cfg.get('instruction_cache_entries', 8)))
        cache = self._instruction_cache.setdefault(session_id, OrderedDict())
        cache[key] = text[:limit]
        cache.move_to_end(key)
        while len(cache) > entries:
            cache.popitem(last=False)

    def _active_instructions(self, session_id: str | None, policy: dict[str, Any]) -> list[str]:
        """The cached directive texts for the session, most recent last, capped by
        ``max_active_instructions`` (IP-007 §3). Empty when nothing is cached."""
        cache = self._instruction_cache.get(session_id or '')
        if not cache:
            return []
        taint = policy.get('taint', {})
        cap = max(0, int(taint.get('max_active_instructions', 4))) if isinstance(taint, dict) else 4
        if not cap:
            return []
        return list(cache.values())[-cap:]

    def recheck_pending(self, session_id: str | None) -> dict[str, Any]:
        """Recheck every cached pending result for the session with a wider budget
        (IP-007 §2). A resolved result leaves the cache; a second failure stays and
        is counted. Called by the runtime and the daemon at a turn boundary."""
        cache = self._pending_cache.get(session_id or '')
        if not session_id or not cache:
            return {'rechecked': 0, 'resolved': 0, 'still_pending': 0}
        post = load_pack('gate')['post_result']['policy']
        budget_override = post.get('recheck_budget_s')
        resolved = still = 0
        for result_hash, text in list(cache.items()):
            try:
                out = self.post_result(text, session_id=session_id, recheck=True,
                                       budget_override=float(budget_override) if budget_override else None)
            except Exception:
                still += 1
                continue
            if out.get('resolved'):
                resolved += 1
                cache.pop(result_hash, None)
            else:
                still += 1
        return {'rechecked': resolved + still, 'resolved': resolved, 'still_pending': still}

    def _cache_read(self, session_id: str, path: str, text: str, facts_cfg: dict[str, Any]) -> None:
        """Record a released read keyed by path, truncated to ``script_max_chars``
        and bounded to ``script_cache_entries`` most-recent entries per session."""
        if not session_id or not isinstance(path, str) or not isinstance(text, str):
            return
        limit = int(facts_cfg.get('script_max_chars', 4000))
        entries = max(1, int(facts_cfg.get('script_cache_entries', 32)))
        cache = self._script_cache.setdefault(session_id, OrderedDict())
        cache[path] = text[:limit]
        cache.move_to_end(path)
        while len(cache) > entries:
            cache.popitem(last=False)

    def _cached_script(self, session_id: str | None, path: str) -> str | None:
        cache = self._script_cache.get(session_id or '')
        if not cache or path not in cache:
            return None
        cache.move_to_end(path)
        return cache[path]

    def _warm(self, lane: str = 'max') -> None:
        if lane not in self._warmed:
            self.client.warmup(lane)
            self._warmed.add(lane)

    def warmup(self) -> dict[str, Any]:
        try:
            self._warm()
            return {'verdict': 'allow', 'last_warmup_ms': self.client.last_warmup_ms,
                    'cost_models': self.client.cost_models}
        except ClientError as exc:
            return failure(exc.reason)

    def _budget(self, pack: dict[str, Any], total_chars: int, question_count: int,
                lane: str = 'max') -> dict[str, Any]:
        """Derive a per-call budget from the pack's YAML budget block and the
        measured cost model. A pack with no budget block keeps today's fixed
        timeouts and is reported as ``not_requested``."""
        policy = pack.get('policy', {}) if isinstance(pack, dict) else {}
        block = policy.get('budget') if isinstance(policy, dict) else None
        if not isinstance(block, dict):
            return {'budget_source': 'not_requested', 'expected_ms': None, 'budget_s': None,
                    'exceeds_budget': False, 'total_chars': total_chars, 'questions': question_count}
        return self.client.request_budget(total_chars, question_count, block, lane,
                                          override=self.client.call_timeout)

    def _transport_budget(self, lane: str, budget_s: float | None) -> float | None:
        """Grant the first budgeted call per lane the first-call timeout, so a
        cold request shape warms instead of timing out under the derived warm
        budget and cascading on the serialising lane. Later calls use the
        derived budget. The recorded ``budget`` object keeps the derived values;
        only the transport budget is widened for that first call."""
        if budget_s is None:
            return None
        if lane not in self._primed:
            self._primed.add(lane)
            return max(budget_s, self.client.first_timeout)
        return budget_s

    def _finish(self, job: str, pack: dict[str, Any], decision: dict[str, Any],
                response: dict[str, Any] | None = None, state: Any = None) -> dict[str, Any]:
        result = {**(response or {}), **decision, 'job': job, 'pack_version': pack['version']}
        result.setdefault('complete', True)
        try:
            self.audit.record(result, state=state)
        except OSError:
            # Tool execution also depends on retaining the required audit record.
            result['complete'] = False
            result['reasons'] = [*result.get('reasons', []), 'audit_unavailable']
            if result['verdict'] != 'deny':
                result['verdict'] = 'ask'
        return result

    def _call(self, state: Any, questions: dict[str, Any], *, images: list[str] | None = None,
              audio: str | None = None, pack: dict[str, Any] | None = None) -> dict[str, Any]:
        # Validate mixed media before warmup, which is itself a network operation.
        if images and audio is not None:
            raise ClientError('invalid_request', 'images and audio cannot be combined')
        groups = build_requests(state, questions, images=images, audio=audio)
        sent = [group for group in groups if group['questions']]
        unresolved = [name for group in groups for name in group['unresolved']]
        if not sent:
            return {'answers': {}, 'usage': {'input_tokens': 0, 'output_tokens': 0},
                    'lane': None, 'timings': {},
                    'scoping': {'mode': 'scoped', 'groups': [], 'unresolved': unresolved}}
        lane = 'omni' if audio is not None else 'max'
        self._warm(lane)
        mode = ("whole_state_media" if images or audio is not None else (
            "scoped" if any(group['scope'] is not None for group in groups) else "whole_state"))
        budget_s: float | None = None
        budget: dict[str, Any] | None = None
        if pack is not None:
            total_chars = sum(_state_chars(group['state']) for group in sent)
            question_count = sum(len(group['questions']) for group in sent)
            budget = self._budget(pack, total_chars, question_count, lane)
            if budget['exceeds_budget']:
                return {'answers': {}, 'usage': {'input_tokens': 0, 'output_tokens': 0},
                        'lane': None, 'timings': {}, 'exceeds_budget': True, 'budget': budget,
                        'scoping': {'mode': mode, 'groups': [], 'unresolved': unresolved}}
            budget_s = self._transport_budget(lane, budget['budget_s'])
        try:
            if len(sent) == 1 and sent[0]['scope'] is None:
                response = self.client.system_one(state, strip_scopes(sent[0]['questions']),
                                                  images=images, audio=audio, budget_s=budget_s)
                response['scoping'] = {"mode": mode,
                                       "groups": [{"scope": None, "questions": list(sent[0]['questions']),
                                                    "input_tokens": response.get('usage', {}).get('input_tokens', 0),
                                                    "state_chars": _state_chars(state)}],
                                       "unresolved": unresolved}
            else:
                batch = self.client.system_one_batch([
                    {'state': group['state'], 'questions': group['questions']}
                    for group in sent
                ], budget_s=budget_s)
                response = merge_results(groups, batch['results'])
                response['timings'] = batch.get('timings', response.get('timings', {}))
                response['lane'] = batch.get('lane', response.get('lane'))
        except ClientError as exc:
            # A timed-out or failed call still reports the budget it was given
            # (IP-006: a timed-out decision carries its budget object).
            if budget is not None:
                exc.budget = budget
            raise
        if budget is not None:
            response['budget'] = budget
        return response

    def _facts_gate(self, tool_name: str, tool_input: dict[str, Any], user_request: str,
                    pack: dict[str, Any], session: dict[str, Any],
                    script_content: str | None = None,
                    operation_floor: str | None = None,
                    instructions: list[str] | None = None) -> dict[str, Any]:
        """Build the gate as small-state facts, send one batch, compose the axes
        in policy (IP-006 section 2). When a flag is active the batch also carries
        one ``carries_out`` item per cached directive (IP-007 §3); the session item
        is no longer sent. ``operation_floor`` raises the composed operation level
        when the service could not supply the content of a script an interpreter
        command runs."""
        items = build_gate_facts(tool_name, tool_input, user_request, pack['questions'],
                                 pack['policy'], script_content)
        instr_items: list[dict[str, Any]] = []
        if instructions:
            action = build_action(tool_input, pack['policy'])
            instr_items = build_instruction_items(instructions, action, pack['questions'])
        batch_items = items + instr_items
        requests = [{'state': item['state'], 'questions': item['questions']} for item in batch_items]
        total_chars = sum(_state_chars(r['state']) for r in requests)
        question_count = sum(len(r['questions']) for r in requests)
        budget = self._budget(pack, total_chars, question_count)
        if budget['exceeds_budget']:
            return {'exceeds_budget': True, 'budget': budget, 'answers': {}}
        self._warm()
        try:
            batch = self.client.system_one_batch(requests, budget_s=self._transport_budget('max', budget['budget_s']))
        except ClientError as exc:
            exc.budget = budget
            raise
        results = batch['results']
        fact_results = results[:len(items)]
        instr_results = results[len(items):len(items) + len(instr_items)]
        fact_answers = {items[i]['item']: fact_results[i].get('answers', {}) for i in range(len(items))}
        composed = compose_gate_facts(tool_name, fact_answers, pack['policy'], operation_floor=operation_floor)
        response: dict[str, Any] = {
            'answers': composed['answers'], 'compose_reasons': composed['reasons'],
            'timings': batch.get('timings', {}), 'lane': batch.get('lane'), 'budget': budget,
            'usage': {'input_tokens': sum(r.get('usage', {}).get('input_tokens', 0) for r in results),
                      'output_tokens': 0},
            'facts': {items[i]['item']: {'state_chars': _state_chars(items[i]['state']),
                                         'questions': list(items[i]['questions'])}
                      for i in range(len(items))},
        }
        if instr_items:
            carries: list[float] = []
            for item in instr_results:
                answer = item.get('answers', {}).get('carries_out')
                if isinstance(answer, dict) and isinstance(answer.get('noul'), (int, float)):
                    carries.append(float(answer['noul']))
            response['carries_out'] = carries
            response['instructions'] = len(instr_items)
        return response

    def gate(self, tool_name: str, tool_input: dict[str, Any], *, user_request: str = '',
             session_id: str | None = None, cwd: str | None = None) -> dict[str, Any]:
        pack = load_pack('gate')
        if not isinstance(tool_name, str) or not isinstance(tool_input, dict):
            return self._finish('gate', pack, failure('invalid_tool_call'))
        state = {'tool_name': tool_name, 'tool_input': tool_input, 'user_request': user_request}
        # File ownership facts (IP-011): deterministic, no lane. Computed before the
        # static rules so the recursive-delete exemption and the catastrophic rule
        # can read them, and reused for apply_ownership after the model answers.
        try:
            created = self.ledger.created_paths(session_id, pack['policy']) if session_id else set()
        except (OSError, ValueError):
            created = set()
        ownership = ownership_mod.compute(tool_name, tool_input, pack['policy'],
                                          cwd=cwd, created_paths=created)
        static = static_denial(tool_name, tool_input, pack['policy'], ownership)
        try:
            session = self.ledger.advance(session_id, pack['policy']) if session_id else {
                'active_taint': False, 'untrusted_events': [], 'pending_events': [],
            }
            if static:
                return self._finish('gate', pack, static, state=state)
            if session_id and self.ledger.matches(
                session_id, command_texts(tool_input, pack['policy']), pack['policy']
            ):
                return self._finish('gate', pack, {
                    'verdict': 'deny', 'reasons': ['tainted_content_in_tool_input'],
                    'answers': {}, 'session': session,
                }, state=state)
            # Provenance floor: deterministic, no model, applies with the lane
            # down. A deny returns now like a static denial; an ask is kept and
            # applied as a floor after the model answers (IP-006 section 1).
            prov_hit: dict[str, Any] | None = None
            prov_policy = pack['policy'].get('provenance')
            if session_id and isinstance(prov_policy, dict) and prov_policy.get('enabled', True):
                carry = self.ledger.provenance(session_id, pack['policy'])
                prov_hit = provenance.check(tool_name, tool_input, user_request, carry, prov_policy)
                if prov_hit is not None and prov_hit.get('verdict') == 'deny':
                    return self._finish('gate', pack, {
                        'verdict': 'deny', 'reasons': ['provenance_' + str(prov_hit.get('reason'))],
                        'answers': {}, 'static': True, 'provenance': prov_hit, 'session': session,
                    }, state=state)
            if pack['policy'].get('compose') == 'facts':
                if session['active_taint']:
                    state['session'] = session
                # A command that runs a script sees the script's content when a
                # released read put it in the session cache; otherwise the model
                # judges the command alone and the operation is floored (IP-006).
                facts_cfg = pack['policy'].get('facts', {}) if isinstance(pack['policy'].get('facts'), dict) else {}
                script_content: str | None = None
                operation_floor: str | None = None
                script_path = interpreter_script_path(tool_input, pack['policy'])
                if script_path is not None:
                    script_content = self._cached_script(session_id, script_path)
                    if script_content is None:
                        operation_floor = facts_cfg.get('interpreter_floor') or None
                # A flag arms a carries_out check against the cached directives
                # (IP-007 §3); a pending result does not — §2 rechecks it instead.
                instructions = (self._active_instructions(session_id, pack['policy'])
                                if session.get('untrusted_events') and session_id else [])
                response = self._facts_gate(tool_name, tool_input, user_request, pack, session,
                                            script_content=script_content, operation_floor=operation_floor,
                                            instructions=instructions)
                if response.get('exceeds_budget'):
                    extra = {'session': session} if session['active_taint'] else {}
                    return self._finish('gate', pack, {**failure('exceeds_budget'),
                                                       'budget': response['budget'], **extra}, response, state)
            elif session['active_taint']:
                state['session'] = session
                self._warm()
                main_groups = build_requests(state, pack['questions'])
                sent = [group for group in main_groups if group['questions']]
                requests = [{'state': group['state'], 'questions': group['questions']} for group in sent]
                requests.append({'state': session, 'questions': strip_scopes(pack['session_questions'])})
                total_chars = sum(_state_chars(item['state']) for item in requests)
                question_count = sum(len(item['questions']) for item in requests)
                budget = self._budget(pack, total_chars, question_count)
                if budget['exceeds_budget']:
                    return self._finish('gate', pack, {**failure('exceeds_budget'), 'budget': budget,
                                                       'session': session}, state=state)
                try:
                    batch = self.client.system_one_batch(requests, budget_s=self._transport_budget('max', budget['budget_s']))
                except ClientError as exc:
                    exc.budget = budget
                    raise
                merged = merge_results(main_groups, batch['results'][:-1])
                merged['scoping']['groups'].append({
                    'scope': None, 'questions': list(pack['session_questions']),
                    'input_tokens': batch['results'][-1].get('usage', {}).get('input_tokens', 0),
                    'state_chars': _state_chars(session),
                })
                response = {**merged, 'timings': batch['timings'], 'budget': budget,
                            'session_assessment': batch['results'][-1]['answers'],
                            'usage': {'input_tokens': sum(item.get('usage', {}).get('input_tokens', 0)
                                                          for item in batch['results']),
                                      'output_tokens': 0}}
            else:
                response = self._call(state, pack['questions'], pack=pack)
                if response.get('exceeds_budget'):
                    return self._finish('gate', pack, {**failure('exceeds_budget'),
                                                       'budget': response['budget']}, response, state)
            # File ownership (IP-011 §3): a pure step over the composed axes. An
            # owned/scratch delete has operation lowered to mutating and destructive
            # zeroed so the ordinary axes decide; a tracked or foreign delete sets a
            # verdict floor applied after the model answers. No lane.
            own = apply_ownership(response['answers'], ownership, pack['policy'])
            eval_axes = own['axes']
            decision = evaluate_gate(eval_axes, pack['policy'])
            if session['active_taint']:
                if pack['policy'].get('compose') == 'facts':
                    # Proportionate taint (IP-007 §3): a flag arms a carries_out
                    # check; a pending-only session does not floor here.
                    if session.get('untrusted_events'):
                        decision = taint_check(decision, eval_axes,
                                               response.get('carries_out', []), pack['policy'])
                else:
                    decision = taint_floor(decision, eval_axes, pack['policy'],
                                           pending=bool(session.get('pending_events')))
                decision.setdefault('reasons', []).append('session_taint_active')
                decision['session'] = session
            if 'facts' in response:
                decision.setdefault('reasons', []).extend(response.get('compose_reasons', []))
                decision['facts'] = response['facts']
                if 'carries_out' in response:
                    decision['carries_out'] = response['carries_out']
            if prov_hit is not None:
                # A provenance ask is deterministic but not static: the model ran,
                # and the ask is applied as a floor so the trace carries both.
                decision['provenance'] = prov_hit
                if decision.get('verdict') == 'allow':
                    decision['verdict'] = 'ask'
                decision.setdefault('reasons', []).append('provenance_' + str(prov_hit.get('reason')))
            # Apply the ownership floor last (a deny or ask the mitigators cannot
            # soften) and record the facts for the audit.
            if own.get('floor') == 'deny' and decision.get('verdict') != 'deny':
                decision['verdict'] = 'deny'
            elif own.get('floor') == 'ask' and decision.get('verdict') == 'allow':
                decision['verdict'] = 'ask'
            if own.get('reasons'):
                decision.setdefault('reasons', []).extend(own['reasons'])
            if ownership is not None:
                decision['ownership'] = ownership
            # Record what an allowed create made, so a later delete of it is owned.
            if session_id and decision.get('verdict') == 'allow':
                made = ownership_mod.create_targets(tool_name, tool_input,
                                                    pack['policy'].get('facts', {}) or {}, cwd)
                if made:
                    try:
                        self.ledger.created(session_id, made, tool_name=tool_name)
                    except (OSError, ValueError):
                        pass
            return self._finish('gate', pack, decision, response, state)
        except (ClientError, OSError, ValueError) as exc:
            if static:
                return self._finish('gate', pack, static, state=state)
            failed = failure(getattr(exc, 'reason', 'invalid_input_or_ledger'))
            exc_budget = getattr(exc, 'budget', None)
            if exc_budget is not None:
                failed['budget'] = exc_budget
            return self._finish('gate', pack, failed, state=state)

    def clear_session(self, session_id: str, reason: str = 'operator clearance') -> dict[str, Any]:
        pack = load_pack('gate')
        self._script_cache.pop(session_id, None)
        self._pending_cache.pop(session_id, None)
        self._instruction_cache.pop(session_id, None)
        try:
            record = self.ledger.clear(session_id, reason)
            return self._finish('session_clear', pack, {
                'verdict': 'allow', 'reasons': ['cleared_by_operator'], 'answers': {},
                'taint_cleared': True, 'clearance_reason': record['reason'],
                'session_id_sha256': hashlib.sha256(session_id.encode()).hexdigest(),
            })
        except (OSError, ValueError):
            return self._finish('session_clear', pack, failure('ledger_unavailable_or_invalid_clearance'))

    def classify(self, state: Any, *, images: list[str] | None = None,
                 audio: str | None = None) -> dict[str, Any]:
        pack = load_pack('classify')
        config = pack['policy']['thresholds']['second_pass']
        text = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)
        size = int(config['chunk_chars'])
        chunks = [text[i:i + size] for i in range(0, len(text), size)] or [text]
        if images or audio is not None:
            chunks = [text]
        passes: list[dict[str, Any]] = []
        finals: list[dict[str, Any]] = []
        try:
            for index, chunk in enumerate(chunks):
                response = self._call(chunk, pack['questions'], images=images, audio=audio, pack=pack)
                if response.get('exceeds_budget'):
                    return self._finish('classify', pack, {**failure('exceeds_budget'),
                                                           'budget': response['budget'], 'passes': passes},
                                        response, state)
                first = evaluate_classify(response['answers'], pack['policy'])
                passes.append({**response, **first, 'chunk': index, 'pass': 1})
                final = first
                if classify_needs_second_pass(response['answers'], pack['policy']):
                    # Localize the flagged chunk using smaller verbatim windows. The
                    # model provides no spans; never invent a supposedly quoted span.
                    width = max(1, min(int(config['window_chars']), size // 2))
                    windows = [chunk[i:i + width] for i in range(0, len(chunk), width)] or [chunk]
                    second = []
                    for offset, window in enumerate(windows):
                        zoom = self._call(window, pack['questions'], images=images, audio=audio, pack=pack)
                        judged = evaluate_classify(zoom['answers'], pack['policy'])
                        passes.append({**zoom, **judged, 'chunk': index, 'pass': 2,
                                       'window_start': index * size + offset * width,
                                       'window_end': index * size + offset * width + len(window)})
                        second.append(judged)
                    final = max(second, key=lambda x: {'allow': 0, 'ask': 1, 'deny': 2}[x['verdict']])
                    if first['verdict'] == 'deny':
                        final = first
                finals.append(final)
            decision = max(finals, key=lambda x: {'allow': 0, 'ask': 1, 'deny': 2}[x['verdict']])
            result = {**decision, 'passes': passes, 'chunks': len(chunks)}
        except (ClientError, ValueError) as exc:
            result = {**failure(getattr(exc, 'reason', 'invalid_input')), 'passes': passes}
        return self._finish('classify', pack, result, passes[-1] if passes else None, state)

    def route(self, state: Any, *, destinations: dict[str, str] | None = None,
              images: list[str] | None = None, audio: str | None = None) -> dict[str, Any]:
        pack = load_pack('route')
        if destinations is not None:
            if not isinstance(destinations, dict) or not 2 <= len(destinations) <= 255 or any(
                not isinstance(k, str) or not isinstance(v, str) for k, v in destinations.items()
            ):
                return self._finish('route', pack, failure('invalid_destinations'))
            pack['questions']['destination']['criteria'] = deepcopy(destinations)
            pack['policy']['destinations'] = deepcopy(destinations)
        try:
            response = self._call(state, pack['questions'], images=images, audio=audio, pack=pack)
            if response.get('exceeds_budget'):
                return self._finish('route', pack, {**failure('exceeds_budget'),
                                                    'budget': response['budget']}, response, state)
            return self._finish('route', pack, evaluate_route(response['answers'], pack['policy']), response, state)
        except (ClientError, ValueError) as exc:
            return self._finish('route', pack, failure(getattr(exc, 'reason', 'invalid_input')), state=state)

    def verify(self, source: str, claims: list[str]) -> dict[str, Any]:
        pack = load_pack('verify')
        if not isinstance(source, str) or not isinstance(claims, list) or not claims or any(
            not isinstance(claim, str) or not claim.strip() for claim in claims
        ):
            return self._finish('verify', pack, failure('invalid_source_or_claims'))
        try:
            self._warm()
            items = [{'source': source, 'claim': claim} for claim in claims]
            total_chars = sum(_state_chars(item) for item in items)
            budget = self._budget(pack, total_chars, len(claims) * len(pack['questions']))
            if budget['exceeds_budget']:
                return self._finish('verify', pack, {**failure('exceeds_budget'), 'budget': budget})
            response = self.client.system_one_batch([
                {'state': item, 'questions': pack['questions']} for item in items
            ], budget_s=self._transport_budget('max', budget['budget_s']))
            combined = {str(index): item['answers']['supported_by_source']
                        for index, item in enumerate(response['results'])}
            decision = evaluate_verify(combined, pack['policy'])
            decision['claims'] = [dict(decision['claims'][str(index)], claim=claim)
                                  for index, claim in enumerate(claims)]
            decision['flagged'] = [index for index, item in enumerate(decision['claims'])
                                   if item['verdict'] == 'flagged']
            decision['budget'] = budget
            # Do not include source text in the result or audit.
            return self._finish('verify', pack, decision, response, {'source': source, 'claims': claims})
        except (ClientError, ValueError) as exc:
            return self._finish('verify', pack, failure(getattr(exc, 'reason', 'invalid_input')))

    def constraints(self, candidate: Any, constraints: list[dict[str, Any]],
                    images: list[str] | None = None) -> dict[str, Any]:
        """Score a candidate against a closed list of {id, rule} constraints
        (IP-009 / IDEA-002). One item per constraint, two questions, composed in
        policy; one bounded second pass over uncertain constraints before ask. No
        label proposes a fix; the candidate and rules are not audited."""
        pack = load_pack('constraints')
        if (candidate is None or (isinstance(candidate, str) and not candidate.strip())
                or not isinstance(constraints, list) or not constraints
                or any(not isinstance(c, dict) or not isinstance(c.get('id'), (str, int))
                       or isinstance(c.get('id'), bool) or not isinstance(c.get('rule'), str)
                       or not c['rule'].strip() for c in constraints)):
            return self._finish('constraints', pack, failure('invalid_candidate_or_constraints'))
        ids = [str(c['id']) for c in constraints]
        if len(set(ids)) != len(ids):
            return self._finish('constraints', pack, failure('duplicate_constraint_id'))
        cand = candidate if isinstance(candidate, str) else json.dumps(candidate, ensure_ascii=False, sort_keys=True)
        rules = {cid: str(c['rule']) for cid, c in zip(ids, constraints)}
        try:
            self._warm()
            combined, budget, response = self._constraints_batch(pack, cand, ids, rules, images)
            if budget['exceeds_budget']:
                return self._finish('constraints', pack, {**failure('exceeds_budget'), 'budget': budget})
            decision = evaluate_constraints(combined, pack['policy'])
            passes = 1
            if decision['verdict'] == 'ask' and decision['uncertain']:
                sub_ids = list(decision['uncertain'])
                sub, sub_budget, _ = self._constraints_batch(pack, cand, sub_ids, rules, images)
                if not sub_budget['exceeds_budget'] and sub:
                    combined.update(sub)
                    decision = evaluate_constraints(combined, pack['policy'])
                    passes = 2
            decision['budget'] = budget
            decision['passes'] = passes
            decision['constraint_order'] = ids
            return self._finish('constraints', pack, decision, response,
                                {'candidate': cand, 'constraints': constraints})
        except (ClientError, ValueError) as exc:
            return self._finish('constraints', pack, failure(getattr(exc, 'reason', 'invalid_input')))

    def _constraints_batch(self, pack: dict[str, Any], candidate: str, ids: list[str],
                           rules: dict[str, str], images: list[str] | None):
        items = [{'candidate': candidate, 'constraint': rules[cid]} for cid in ids]
        total_chars = sum(_state_chars(item) for item in items)
        budget = self._budget(pack, total_chars, len(items) * len(pack['questions']))
        if budget['exceeds_budget']:
            return {}, budget, {}
        requests = [{'state': item, 'questions': pack['questions'],
                     **({'images': images} if images else {})} for item in items]
        batch = self.client.system_one_batch(requests, budget_s=self._transport_budget('max', budget['budget_s']))
        combined = {ids[index]: batch['results'][index].get('answers', {}) for index in range(len(ids))}
        return combined, budget, batch

    def validity(self, packet: Any, spec: dict[str, Any],
                 images: list[str] | None = None) -> dict[str, Any]:
        """Score a finished packet against a closed spec of checks (IP-009 /
        IDEA-002). One request over the whole packet — a noul per check, a
        document_type choice (spec.types plus unknown), a completeness score —
        composed in policy. A packet over budget is split into page-sized text
        chunks for the per-check nouls, composed by maximum. Validity is not
        support; the packet text and spec are not audited."""
        pack = load_pack('validity')
        policy = pack['policy']
        text = packet if isinstance(packet, str) else ''
        if (not self._valid_spec(spec) or (not text.strip() and not images)):
            return self._finish('validity', pack, failure('invalid_packet_or_spec'))
        eval_policy = {**policy, 'completeness_levels': list(spec['completeness_levels'])}
        try:
            self._warm()
            questions = self._validity_questions(policy, spec)
            budget = self._budget(pack, _state_chars(text), len(questions))
            page_split = False
            if budget['exceeds_budget'] and text.strip() and not images:
                answers, response, budget = self._validity_pagesplit(pack, text, spec, questions, policy)
                page_split = True
            elif budget['exceeds_budget']:
                return self._finish('validity', pack, {**failure('exceeds_budget'), 'budget': budget})
            else:
                response = self.client.system_one(text, questions, images=images,
                                                  budget_s=self._transport_budget('max', budget['budget_s']))
                answers = response['answers']
            decision = evaluate_validity(answers, eval_policy)
            decision['budget'] = budget
            decision['page_split'] = page_split
            return self._finish('validity', pack, decision, response, {'packet': text, 'spec': spec})
        except (ClientError, ValueError) as exc:
            return self._finish('validity', pack, failure(getattr(exc, 'reason', 'invalid_input')))

    def _valid_spec(self, spec: Any) -> bool:
        if not isinstance(spec, dict):
            return False
        types = spec.get('types')
        checks = spec.get('checks')
        levels = spec.get('completeness_levels')
        return bool(isinstance(types, list) and types and all(isinstance(t, str) for t in types)
                    and isinstance(levels, list) and levels and all(isinstance(level, str) for level in levels)
                    and isinstance(checks, list) and checks
                    and all(isinstance(c, dict) and isinstance(c.get('id'), (str, int))
                            and not isinstance(c.get('id'), bool) and isinstance(c.get('rule'), str)
                            and c['rule'].strip() for c in checks)
                    and len({str(c['id']) for c in checks}) == len(checks))

    def _validity_questions(self, policy: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
        check_tpl = policy.get('check_instruction', 'Does the packet satisfy this requirement: {rule}')
        unknown = policy.get('unknown_type', 'unknown')
        questions: dict[str, Any] = {}
        for check in spec['checks']:
            questions[f"check.{check['id']}"] = {
                'type': 'noul', 'instructions': check_tpl.format(rule=check['rule'])}
        criteria = {str(t): str(t) for t in spec['types']}
        criteria[str(unknown)] = 'The document type cannot be determined.'
        questions['document_type'] = {'type': 'choice',
                                      'instructions': policy.get('document_type_instruction', 'What type of document is this packet?'),
                                      'criteria': criteria}
        questions['completeness'] = {'type': 'score',
                                    'instructions': policy.get('completeness_instruction', 'How complete is this packet?'),
                                    'criteria': [str(level) for level in spec['completeness_levels']]}
        return questions

    def _validity_pagesplit(self, pack: dict[str, Any], text: str, spec: dict[str, Any],
                            questions: dict[str, Any], policy: dict[str, Any]):
        """Per-check nouls over page-sized chunks, composed by maximum; the type and
        completeness questions run over the first chunk (IP-009, recorded in the CR
        when taken)."""
        size = max(1, int(policy.get('max_page_chars', 12000)))
        pages = [text[i:i + size] for i in range(0, len(text), size)] or [text]
        check_questions = {key: q for key, q in questions.items() if key.startswith('check.')}
        other_questions = {key: q for key, q in questions.items() if not key.startswith('check.')}
        requests = [{'state': page, 'questions': check_questions} for page in pages]
        requests.append({'state': pages[0], 'questions': other_questions})
        total_chars = sum(len(page) for page in pages) + len(pages[0])
        budget = self._budget(pack, total_chars, len(check_questions) * len(pages) + len(other_questions))
        if budget['exceeds_budget']:
            raise ClientError('exceeds_budget', 'validity page-split exceeds budget')
        batch = self.client.system_one_batch(requests, budget_s=self._transport_budget('max', budget['budget_s']))
        results = batch['results']
        answers: dict[str, Any] = {}
        for key in check_questions:
            best = None
            for page_result in results[:len(pages)]:
                noul = page_result.get('answers', {}).get(key)
                value = noul.get('noul') if isinstance(noul, dict) else None
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    best = value if best is None else max(best, value)
            answers[key] = {'type': 'noul', 'noul': best if best is not None else 0.0}
        answers.update(results[-1].get('answers', {}))
        return answers, batch, budget

    def _unscored(self, result: str, session_id: str, tool_name: str, pack: dict[str, Any],
                  reason: str, budget: dict[str, Any], source_path: str | None = None) -> dict[str, Any]:
        check = {'status': 'unscored', 'reason': reason, 'statically_checked': True,
                 'model_scored': False, **budget}
        try:
            mode = pack['post_result']['policy']['on_unscored']
            if mode not in {'release', 'withhold'}:
                raise ValueError('invalid unscored policy')
            self.ledger.pending(session_id, text=result, tool_name=tool_name,
                                policy=pack['policy'], reason=reason, source_path=source_path)
            # Keep the text in memory so the runtime can recheck it next turn (§2).
            self._cache_pending(session_id, hashlib.sha256(result.encode()).hexdigest(),
                                result, pack['post_result']['policy'])
            decision = {'verdict': 'allow' if mode == 'release' else 'ask',
                        'reasons': [reason], 'answers': {}, 'checked': False,
                        'check': check, 'complete': True}
        except (OSError, ValueError):
            decision = {**failure('pending_ledger_or_policy_unavailable'),
                        'checked': False, 'check': check}
        return self._finish('post_result', pack, decision, state=result)

    def _post_result_units(self, result: str, result_hash: str, *, session_id: str,
                           tool_name: str, recheck: bool, source_path: str | None,
                           user_request: str, pack: dict[str, Any],
                           budget_override: float | None = None) -> dict[str, Any]:
        """Two-stage locate-then-confirm over small units (IP-006 section 3).

        Stage 1 asks whether each unit is an instruction addressed to an agent;
        stage 2 asks, only over located units and paired with the task, whether the
        instruction goes beyond it. With no task text a located unit decides on its
        locate score alone. ``kind`` leaves the pack; canaries are the static
        sentinel, already run. No unit text is persisted."""
        post = pack['post_result']
        policy = post['policy']
        units = split_units(result, policy)
        budget: dict[str, Any] = {'budget_source': 'not_requested', 'expected_ms': None, 'budget_s': None}
        if len(result) > policy['max_total_chars']:
            return self._unscored(result, session_id, tool_name, pack, 'oversized', budget, source_path)
        locate_q = {'addressed_to_agent': post['questions']['addressed_to_agent']}
        confirm_q = {'beyond_task': post['questions']['beyond_task']}
        try:
            total_chars = sum(len(unit) for unit in units)
            budget = self.client.request_budget(total_chars, len(units), policy, 'max',
                                                 override=self.client.post_timeout)
            if budget_override is None and budget['exceeds_budget']:
                return self._unscored(result, session_id, tool_name, pack, 'exceeds_budget', budget, source_path)
            self._warm()
            # Re-derive after warming so a cold process (the one-shot hook, the CLI)
            # budgets from the measured cost model, not the FD_TIMEOUT fallback;
            # otherwise the first real-size post-result times out and goes unscored
            # (IP-007 verifier fix; mirrors the windows path).
            budget = self.client.request_budget(total_chars, len(units), policy, 'max',
                                                 override=self.client.post_timeout)
            if budget_override is None and budget['exceeds_budget']:
                return self._unscored(result, session_id, tool_name, pack, 'exceeds_budget', budget, source_path)
            # A recheck (§2) widens the per-call budget to recheck_budget_s.
            stage_budget = budget_override if budget_override is not None else budget['budget_s']
            locate = self.client.system_one_batch(
                [{'state': unit, 'questions': locate_q} for unit in units], budget_s=stage_budget)
            locate_scores = [item['answers']['addressed_to_agent']['noul'] for item in locate['results']]
            located = [i for i, score in enumerate(locate_scores) if score >= policy['locate_line']]
            usage_tokens = sum(item.get('usage', {}).get('input_tokens', 0) for item in locate['results'])
            confirm_scores: dict[int, float] = {}
            has_task = isinstance(user_request, str) and user_request.strip() != ''
            if located and has_task:
                # The one exception to one-request-all-questions: the confirm items
                # depend on stage one's selection, and it runs only when something
                # is located, which benign text rarely does.
                confirm = self.client.system_one_batch(
                    [{'state': {'task': user_request, 'instruction': units[i]}, 'questions': confirm_q}
                     for i in located], budget_s=stage_budget)
                usage_tokens += sum(item.get('usage', {}).get('input_tokens', 0) for item in confirm['results'])
                for index, item in zip(located, confirm['results']):
                    confirm_scores[index] = item['answers']['beyond_task']['noul']
                confirmed = [i for i in located if confirm_scores[i] >= policy['confirm_line']]
            elif located:
                confirmed = [i for i in located if locate_scores[i] >= policy['confirm_without_task_line']]
            else:
                confirmed = []
            first = confirmed[0] if confirmed else None
            per_unit = [{'addressed_to_agent': locate_scores[i], 'beyond_task': confirm_scores.get(i)}
                        for i in range(len(units))]
            status = 'scored_flagged' if confirmed else 'scored_clean'
            persistence_failed = False
            try:
                if first is not None:
                    value = confirm_scores.get(first, locate_scores[first])
                    self.ledger.flag(session_id, {'directed_injection': {'type': 'noul', 'noul': value},
                                                  'kind': {'type': 'choice', 'choice': 'injection'}},
                                     text=result, tool_name=tool_name, policy=pack['policy'],
                                     source_path=source_path)
                    # Keep the confirmed directive so a later tainted call can be
                    # judged against it by carries_out instead of a floor (§3).
                    taint_cfg = pack['policy'].get('taint')
                    if isinstance(taint_cfg, dict):
                        self._cache_instruction(session_id, result_hash, units[first], taint_cfg)
                else:
                    self.ledger.observe(session_id, text=result, tool_name=tool_name,
                                        policy=pack['policy'], source_path=source_path)
                    facts_cfg = pack['policy'].get('facts')
                    if isinstance(facts_cfg, dict) and isinstance(source_path, str):
                        tool_op = facts_cfg.get('tool_operation', {})
                        if isinstance(tool_op, dict) and tool_op.get(tool_name) == 'read_only':
                            self._cache_read(session_id, source_path, result, facts_cfg)
                if recheck:
                    self.ledger.resolve(session_id, result_hash, outcome=status)
            except (OSError, ValueError):
                persistence_failed = True
            selected = ({'beyond_task': {'type': 'noul', 'noul': confirm_scores[first]}}
                        if first is not None and first in confirm_scores else {})
            # Release the result with each confirmed unit redacted, byte-exact
            # elsewhere (IP-007 §4); the full result never reaches the agent. A
            # withhold policy omits redacted_text so the runtime withholds whole.
            on_confirmed = str(policy.get('on_confirmed', 'redact'))
            redaction: dict[str, Any] = {}
            if confirmed:
                redaction['units_redacted'] = len(confirmed)
                redaction['on_confirmed'] = on_confirmed
                if on_confirmed == 'redact':
                    marker = str(policy.get('redaction_marker', '[fluid-decisions withheld 1 unit]'))
                    redaction['redacted_text'] = redact_units(units, confirmed, marker)
            return self._finish('post_result', pack, {
                'verdict': 'deny' if confirmed else ('ask' if persistence_failed else 'allow'),
                'reasons': (['untrusted_content'] if confirmed else []) +
                           (['ledger_unavailable'] if persistence_failed else []),
                'answers': selected, 'usage': {'input_tokens': usage_tokens, 'output_tokens': 0},
                'flagged': bool(confirmed), 'units': len(units),
                'located_units': located, 'confirmed_units': confirmed, 'unit_probabilities': per_unit,
                'checked': True, 'resolved': recheck and not persistence_failed,
                'complete': not persistence_failed, **redaction,
                'check': {'status': status, 'statically_checked': True, 'model_scored': True, **budget},
            }, {'timings': locate.get('timings', {}), 'lane': locate.get('lane')}, result)
        except ClientError as exc:
            if exc.reason in {'timeout', 'lane_down'}:
                return self._unscored(result, session_id, tool_name, pack, exc.reason, budget, source_path)
            return self._finish('post_result', pack, {**failure(exc.reason), 'checked': False})
        except (OSError, ValueError):
            return self._finish('post_result', pack, {**failure('ledger_or_policy_unavailable'), 'checked': False})

    def post_result(self, result: str, *, session_id: str, tool_name: str = '',
                    recheck: bool = False, source_path: str | None = None,
                    user_request: str = '', budget_override: float | None = None) -> dict[str, Any]:
        pack = load_pack('gate')
        post = pack['post_result']
        if not isinstance(result, str) or not isinstance(session_id, str) or not session_id:
            return self._finish('post_result', pack, failure('invalid_result_or_session'))
        result_hash = hashlib.sha256(result.encode()).hexdigest()
        if recheck:
            try:
                pending = self.ledger.pending_records(session_id)
                matched = next((record for record in pending if record['result_sha256'] == result_hash), None)
                if matched is None:
                    return self._finish('post_result', pack, failure('no_matching_pending_result'))
                tool_name = tool_name or matched.get('tool', '')
            except (OSError, ValueError):
                return self._finish('post_result', pack, failure('ledger_unavailable'))
        if not result:
            return self._finish('post_result', pack, {
                'verdict': 'allow', 'reasons': ['empty_result'], 'answers': {}, 'windows': 0,
                'checked': True, 'check': {'status': 'scored_clean', 'reason': 'empty_result',
                                          'statically_checked': True, 'model_scored': False},
            })
        static = static_injection(result, post)
        if static:
            blocked = {'verdict': 'deny', 'reasons': [static['rule_id']], 'answers': {},
                       'flagged': True, 'static_injection_rule': static['rule_id'], 'windows': 0,
                       'checked': True, 'check': {'status': 'scored_flagged', 'reason': static['rule_id'],
                                                 'statically_checked': True, 'model_scored': False}}
            try:
                self.ledger.flag(session_id, {}, text=result, tool_name=tool_name,
                                 policy=pack['policy'], static_rule=static, source_path=source_path)
                # Arm the proportionate carries_out check (CR-010 follow-up): a
                # static hit caches its matched sentence as the active instruction,
                # exactly as a model-confirmed unit does (line below in the units
                # path), so a later mutating action gets a carries_out item and
                # taint_check judges it. The blanket floor then applies only when a
                # flag has no cached text at all (one-shot hook, evicted cache).
                # Text lives only in the in-memory cache; the ledger stays text-free.
                taint_cfg = pack['policy'].get('taint', {})
                if isinstance(taint_cfg, dict) and isinstance(static.get('sentence'), str):
                    self._cache_instruction(session_id, result_hash, static['sentence'], taint_cfg)
                if recheck:
                    self.ledger.resolve(session_id, result_hash, outcome='scored_flagged')
                    blocked['resolved'] = True
            except (OSError, ValueError):
                blocked['complete'] = False
                blocked['reasons'].append('ledger_unavailable')
            return self._finish('post_result', pack, blocked, state=result)
        if isinstance(post['policy'].get('units'), dict):
            return self._post_result_units(result, result_hash, session_id=session_id,
                                           tool_name=tool_name, recheck=recheck,
                                           source_path=source_path, user_request=user_request, pack=pack,
                                           budget_override=budget_override)
        budget: dict[str, Any] = {'budget_source': 'not_requested', 'expected_ms': None,
                                  'budget_s': None}
        if len(result) > post['policy']['max_total_chars']:
            return self._unscored(result, session_id, tool_name, pack, 'oversized', budget, source_path)
        try:
            width, overlap = post['policy']['window_chars'], post['policy']['overlap_chars']
            if not isinstance(width, int) or not isinstance(overlap, int) or not 0 <= overlap < width:
                raise ValueError('invalid window policy')
            windows = []
            start = 0
            while True:
                windows.append(result[start:start + width])
                if start + width >= len(result):
                    break
                start += width - overlap
            total_chars = sum(len(window) for window in windows)
            budget = self.client.post_result_budget(total_chars, post['policy'])
            if budget['exceeds_budget']:
                return self._unscored(result, session_id, tool_name, pack, 'exceeds_budget', budget, source_path)
            self._warm()
            budget = self.client.post_result_budget(total_chars, post['policy'])
            if budget['exceeds_budget']:
                return self._unscored(result, session_id, tool_name, pack, 'exceeds_budget', budget, source_path)
            response = self.client.system_one_batch([
                {'state': window, 'questions': post['questions']} for window in windows
            ], budget_s=budget['budget_s'])
            evaluated = response['results']
            flagged_indices = [index for index, item in enumerate(evaluated)
                               if item['answers']['directed_injection']['noul'] >= post['policy']['threshold']
                               or item['answers']['kind']['choice'] in ('injection', 'canary')]
            first_flagged = flagged_indices[0] if flagged_indices else None
            selected = evaluated[first_flagged if first_flagged is not None else 0]['answers']
            status = 'scored_flagged' if flagged_indices else 'scored_clean'
            persistence_failed = False
            try:
                if first_flagged is not None:
                    self.ledger.flag(session_id, selected, text=result, tool_name=tool_name,
                                     policy=pack['policy'], source_path=source_path)
                else:
                    # Observe a clean released result so a later call copying one
                    # of its commands can be gated by the provenance floor.
                    self.ledger.observe(session_id, text=result, tool_name=tool_name,
                                        policy=pack['policy'], source_path=source_path)
                    # Cache a released read's text by path so the facts gate can
                    # show the destructive question the script a later command
                    # runs (IP-006 section 2). Only for read-only tools; bounded
                    # and never persisted.
                    facts_cfg = pack['policy'].get('facts')
                    if isinstance(facts_cfg, dict) and isinstance(source_path, str):
                        tool_op = facts_cfg.get('tool_operation', {})
                        if isinstance(tool_op, dict) and tool_op.get(tool_name) == 'read_only':
                            self._cache_read(session_id, source_path, result, facts_cfg)
                if recheck:
                    self.ledger.resolve(session_id, result_hash, outcome=status)
            except (OSError, ValueError):
                persistence_failed = True
            usage = {'input_tokens': sum(item.get('usage', {}).get('input_tokens', 0) for item in evaluated),
                     'output_tokens': 0}
            return self._finish('post_result', pack, {
                'verdict': 'deny' if flagged_indices else ('ask' if persistence_failed else 'allow'),
                'reasons': (['untrusted_content'] if flagged_indices else []) +
                           (['ledger_unavailable'] if persistence_failed else []), 'answers': selected,
                'window_answers': [item['answers'] for item in evaluated], 'usage': usage,
                'flagged': bool(flagged_indices), 'windows': len(windows),
                'first_flagged_window': first_flagged,
                'checked': True, 'resolved': recheck and not persistence_failed,
                'complete': not persistence_failed,
                'check': {'status': status, 'statically_checked': True, 'model_scored': True, **budget},
            }, response, result)
        except ClientError as exc:
            if exc.reason in {'timeout', 'lane_down'}:
                return self._unscored(result, session_id, tool_name, pack, exc.reason, budget, source_path)
            return self._finish('post_result', pack, {**failure(exc.reason), 'checked': False})
        except (OSError, ValueError):
            return self._finish('post_result', pack, {**failure('ledger_or_policy_unavailable'), 'checked': False})

    def health(self) -> dict[str, Any]:
        return self.client.health()


class GuardedRuntime:
    """The caller supplies the executor; every tool execution passes this gate.

    An ask is returned to the operator without executing. A runtime must never
    treat exit zero from classification, or an optional MCP call, as permission.
    """
    def __init__(self, decisions: Decisions, session_id: str | None = None):
        self.decisions = decisions
        self.session_id = session_id or uuid.uuid4().hex

    def run(self, tool_name: str, tool_input: dict[str, Any], execute: Callable[[], Any],
            *, user_request: str = '') -> dict[str, Any]:
        # Recheck any result the lane could not score in time, with a wider budget,
        # before this turn's gate (IP-007 §2), instead of taxing the gate with a
        # blanket floor. A resolved result clears; a second failure stays pending.
        self.decisions.recheck_pending(self.session_id)
        decision = self.decisions.gate(tool_name, tool_input, user_request=user_request,
                                       session_id=self.session_id)
        if decision['verdict'] != 'allow' or not decision.get('complete', True):
            return {'executed': False, 'decision': decision}
        result = execute()
        text = result if isinstance(result, str) else json.dumps(result)
        source_path = tool_input.get('path') if isinstance(tool_input.get('path'), str) else None
        post = self.decisions.post_result(text, session_id=self.session_id, tool_name=tool_name,
                                          source_path=source_path, user_request=user_request)
        release = post['verdict'] == 'allow' and post.get('complete', True)
        # A confirmed result is released with its confirmed units redacted when the
        # pack says redact (IP-007 §4): the agent gets the result minus the
        # directive, so the user still has the file and no re-read is needed. A
        # withhold policy or a static hit withholds the whole result instead and
        # records its paths so a later re-read is denied by provenance (§3).
        redact = (not release and post.get('verdict') == 'deny' and post.get('complete', True)
                  and post.get('on_confirmed') == 'redact'
                  and isinstance(post.get('redacted_text'), str) and bool(post.get('units_redacted')))
        if not release and not redact:
            try:
                self.decisions.ledger.withheld(self.session_id, tool_input=tool_input,
                                               tool_name=tool_name, policy=load_pack('gate')['policy'])
            except (OSError, ValueError):
                pass
        output = {'executed': True, 'decision': decision, 'post_result': post,
                  'result_withheld': not (release or redact), 'checked': post.get('checked', False),
                  'check': post.get('check')}
        if redact:
            output['result'] = post['redacted_text']
            output['units_redacted'] = post['units_redacted']
        elif release:
            output['result'] = result
        return output
