"""Append-only session taint ledger; result text is never persisted."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from fluid import provenance
from fluid.audit import append_record, data_directory
from fluid.policy import shingles


class Ledger:
    def __init__(self, directory: Path | None = None, *, clock: Callable[[], datetime] | None = None):
        self.directory = directory or data_directory() / "sessions"
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _path(self, session_id: str) -> Path:
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("session_id must be a non-empty string")
        return self.directory / (hashlib.sha256(session_id.encode()).hexdigest() + ".jsonl")

    def _now(self) -> datetime:
        value = self.clock()
        if not isinstance(value, datetime):
            raise ValueError("clock must return datetime")
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    def _records(self, session_id: str) -> list[dict[str, Any]]:
        path = self._path(session_id)
        if not path.exists():
            return []
        records: list[dict[str, Any]] = []
        allowed = {"flag", "pending", "resolved", "advance", "cleared_by_operator", "observed",
                   "withheld", "created", None}
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError("ledger record must be an object")
            if record.get("record_type") not in allowed:
                raise ValueError("unknown ledger record type")
            if not isinstance(record.get("timestamp"), str) or self._timestamp(record) is None:
                raise ValueError("ledger record has an invalid timestamp")
            records.append(record)
        return records

    def _append(self, session_id: str, record: dict[str, Any]) -> dict[str, Any]:
        append_record(self._path(session_id), record)
        return record

    @staticmethod
    def _taint_policy(policy: dict[str, Any]) -> dict[str, Any]:
        taint = policy.get("taint", {}) if isinstance(policy, dict) else {}
        required = ("shingle_words", "min_shingle_chars", "window_calls", "ttl_minutes")
        if not isinstance(taint, dict) or any(key not in taint for key in required):
            raise ValueError("taint policy is incomplete")
        if any(isinstance(taint[key], bool) or not isinstance(taint[key], (int, float))
               or not math.isfinite(taint[key]) or taint[key] <= 0 for key in required):
            raise ValueError("taint policy values must be positive and finite")
        if any(int(taint[key]) != taint[key] for key in ("shingle_words", "min_shingle_chars", "window_calls")):
            raise ValueError("taint word and call windows must be integers")
        return taint

    @classmethod
    def _shingle_hashes(cls, text: str, policy: dict[str, Any]) -> list[str]:
        if not isinstance(text, str):
            raise ValueError("taint text must be a string")
        cls._taint_policy(policy)
        return sorted(shingles(text, policy))

    @staticmethod
    def _provenance_hashes(text: str, path: str | None,
                           policy: dict[str, Any]) -> tuple[list[str], list[str]]:
        """Command-span and secret-value hashes for a result (IP-006). The
        provenance sub-policy lives under ``policy['provenance']``; an absent or
        disabled block yields empty lists."""
        prov = policy.get("provenance") if isinstance(policy, dict) else None
        if not isinstance(prov, dict) or not prov.get("enabled", True) or not isinstance(text, str):
            return [], []
        return sorted(provenance.spans(text, prov)), sorted(provenance.secret_values(text, path, prov))

    @staticmethod
    def _answer_fields(answers: dict[str, Any]) -> tuple[float | None, str | None, float | None]:
        injection = answers.get("directed_injection") if isinstance(answers, dict) else None
        probability = injection.get("noul") if isinstance(injection, dict) else None
        if not isinstance(probability, (int, float)) or isinstance(probability, bool):
            probability = None
        decisiveness = injection.get("decisiveness") if isinstance(injection, dict) else None
        if not isinstance(decisiveness, (int, float)) or isinstance(decisiveness, bool):
            decisiveness = 2 * abs(probability - 0.5) if probability is not None else None
        kind_answer = answers.get("kind") if isinstance(answers, dict) else None
        kind = kind_answer.get("choice") if isinstance(kind_answer, dict) else None
        return probability, kind if isinstance(kind, str) else None, decisiveness

    def flag(self, session_id: str, answers: dict[str, Any], *, text: str,
             tool_name: str = "", policy: dict[str, Any],
             static_rule: dict[str, Any] | None = None,
             source_path: str | None = None) -> dict[str, Any]:
        if not isinstance(answers, dict):
            raise ValueError("flag answers are required")
        probability, kind, decisiveness = self._answer_fields(answers)
        if kind is None and isinstance(static_rule, dict) and isinstance(static_rule.get("kind"), str):
            kind = static_rule["kind"]
        raw = text.encode("utf-8")
        spans, secrets = self._provenance_hashes(text, source_path, policy)
        record: dict[str, Any] = {
            "record_type": "flag", "timestamp": self._now().isoformat(),
            "tool": tool_name or "unknown", "directed_injection": probability,
            "kind": kind, "decisiveness": decisiveness,
            "result_sha256": hashlib.sha256(raw).hexdigest(), "result_bytes": len(raw),
            "shingle_hashes": self._shingle_hashes(text, policy),
            "span_hashes": spans, "secret_hashes": secrets, "calls_since_flag": 0,
        }
        if static_rule is not None:
            record["static_rule"] = static_rule
        return self._append(session_id, record)

    def pending(self, session_id: str, *, text: str, tool_name: str = "",
                policy: dict[str, Any], reason: str, source_path: str | None = None) -> dict[str, Any]:
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("pending reason is required")
        if not isinstance(text, str):
            raise ValueError("taint text must be a string")
        raw = text.encode("utf-8")
        spans, secrets = self._provenance_hashes(text, source_path, policy)
        return self._append(session_id, {
            "record_type": "pending", "timestamp": self._now().isoformat(),
            "tool": tool_name or "unknown", "directed_injection": None, "kind": None,
            "decisiveness": None, "result_sha256": hashlib.sha256(raw).hexdigest(),
            "result_bytes": len(raw), "shingle_hashes": self._shingle_hashes(text, policy),
            "span_hashes": spans, "secret_hashes": secrets,
            "calls_since_flag": 0, "check": {"reason": reason},
        })

    def observe(self, session_id: str, *, text: str, tool_name: str = "",
                policy: dict[str, Any], source_path: str | None = None) -> dict[str, Any]:
        """Record a clean released result's command spans and secret values so a
        later call copying one can be gated (IP-006). No result text is stored."""
        if not isinstance(text, str):
            raise ValueError("observed text must be a string")
        spans, secrets = self._provenance_hashes(text, source_path, policy)
        raw = text.encode("utf-8")
        return self._append(session_id, {
            "record_type": "observed", "timestamp": self._now().isoformat(),
            "tool": tool_name or "unknown",
            "path_sha256": hashlib.sha256((source_path or "").encode("utf-8")).hexdigest(),
            "result_sha256": hashlib.sha256(raw).hexdigest(),
            "span_hashes": spans, "secret_hashes": secrets, "calls_since_flag": 0,
        })

    def withheld(self, session_id: str, *, tool_input: dict[str, Any], tool_name: str = "",
                 policy: dict[str, Any]) -> dict[str, Any]:
        """Record the path hashes of a call whose result was withheld, so a later
        re-read of the same path in any encoding is denied (IP-007 section 3). Only
        hashes are stored; no path text."""
        prov = policy.get("provenance", {}) if isinstance(policy, dict) else {}
        hashes = sorted(provenance.path_hashes(tool_input if isinstance(tool_input, dict) else {}, prov))
        return self._append(session_id, {
            "record_type": "withheld", "timestamp": self._now().isoformat(),
            "tool": tool_name or "unknown", "path_hashes": hashes, "calls_since_flag": 0,
        })

    def created(self, session_id: str, paths: Any, *, tool_name: str = "") -> dict[str, Any]:
        """Record the resolved absolute paths this session created on an allowed
        create call (IP-011 §1). Paths only, never content. One record per call."""
        clean = sorted({p for p in paths if isinstance(p, str) and p}) if paths else []
        if not clean:
            return {}
        return self._append(session_id, {
            "record_type": "created", "timestamp": self._now().isoformat(),
            "tool": tool_name or "unknown", "paths": clean, "calls_since_flag": 0})

    def created_paths(self, session_id: str, policy: dict[str, Any]) -> set[str]:
        """The set of paths the session created, bounded by the provenance window
        and TTL and reset by an operator clear (IP-011 §2). Paths only."""
        prov = policy.get("provenance") if isinstance(policy, dict) else None
        window = int(prov.get("window_calls", 200)) if isinstance(prov, dict) else 200
        ttl = float(prov.get("ttl_minutes", 240)) if isinstance(prov, dict) else 240.0
        now = self._now()
        records = self._records(session_id)
        total_advances = sum(1 for r in records if r.get("record_type") == "advance")
        cleared_at = max((i for i, r in enumerate(records)
                          if r.get("record_type") == "cleared_by_operator"), default=-1)
        seen_advances = 0
        paths: set[str] = set()
        for index, record in enumerate(records):
            if record.get("record_type") == "advance":
                seen_advances += 1
                continue
            if index <= cleared_at or record.get("record_type") != "created":
                continue
            age = total_advances - seen_advances
            timestamp = self._timestamp(record)
            age_minutes = (now - timestamp).total_seconds() / 60 if timestamp else 0
            if age > window or age_minutes >= ttl:
                continue
            for path in record.get("paths", []):
                if isinstance(path, str):
                    paths.add(path)
        return paths

    @staticmethod
    def _timestamp(record: dict[str, Any]) -> datetime | None:
        raw = record.get("timestamp")
        if not isinstance(raw, str):
            return None
        try:
            value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    def _replay(self, session_id: str) -> tuple[list[tuple[dict[str, Any], int]],
              list[tuple[dict[str, Any], int]], set[str]]:
        flags: list[tuple[dict[str, Any], int]] = []
        pending: list[tuple[dict[str, Any], int]] = []
        flag_carry: set[str] = set()
        pending_history: list[tuple[str, list[str]]] = []
        legacy_next_id = 0
        for index, record in enumerate(self._records(session_id)):
            kind = record.get("record_type")
            if kind == "cleared_by_operator":
                flags.clear()
                pending.clear()
                continue
            if kind == "advance":
                counters = record.get("counters")
                def bump(items: list[tuple[dict[str, Any], int]]) -> list[tuple[dict[str, Any], int]]:
                    updated: list[tuple[dict[str, Any], int]] = []
                    for item, age in items:
                        candidate = age + 1
                        if isinstance(counters, dict):
                            event_key = str(item.get("event_id", ""))
                            legacy_key = str(item.get("flag_id", ""))
                            if event_key in counters:
                                candidate = int(counters[event_key])
                            elif legacy_key in counters:
                                candidate = int(counters[legacy_key])
                        updated.append((item, max(age, candidate)))
                    return updated
                flags, pending = bump(flags), bump(pending)
                continue
            if kind in {"flag", "pending"} or (kind is None and record.get("untrusted")):
                item = dict(record)
                item.setdefault("tool", "unknown")
                item.setdefault("event_id", str(index))
                item.setdefault("flag_id", str(legacy_next_id))
                legacy_next_id += 1
                if kind is None:
                    probability, item_kind, decisiveness = self._answer_fields(record.get("answers", {}))
                    item.update(directed_injection=probability, kind=item_kind, decisiveness=decisiveness)
                calls = int(item.get("calls_since_flag", 0))
                (flags if kind != "pending" else pending).append((item, calls))
                if kind != "pending":
                    flag_carry.update(value for value in item.get("shingle_hashes", []) if isinstance(value, str))
                else:
                    pending_history.append((str(item.get("event_id")), [value for value in item.get("shingle_hashes", []) if isinstance(value, str)]))
                continue
            if kind == "resolved":
                target = record.get("result_sha256")
                matches = [i for i, (item, _) in enumerate(pending)
                           if item.get("result_sha256") == target]
                if not matches:
                    continue
                matching_ids = {str(pending[index][0].get("event_id")) for index in matches}
                pending = [item for index, item in enumerate(pending) if index not in matches]
                pending_history = [entry for entry in pending_history if entry[0] not in matching_ids]
        carry = set(flag_carry)
        for _, hashes in pending_history:
            carry.update(hashes)
        return flags, pending, carry

    def pending_records(self, session_id: str) -> list[dict[str, Any]]:
        return [{key: value for key, value in record.items() if key != "event_id"}
                for record, _ in reversed(self._replay(session_id)[1])]

    def resolve(self, session_id: str, result_sha256: str, *, outcome: str) -> dict[str, Any]:
        if outcome not in {"scored_clean", "scored_flagged"}:
            raise ValueError("invalid resolution outcome")
        pending = self.pending_records(session_id)
        if not any(record.get("result_sha256") == result_sha256 for record in pending):
            raise ValueError("no unresolved pending record matches result hash")
        return self._append(session_id, {
            "record_type": "resolved", "timestamp": self._now().isoformat(),
            "result_sha256": result_sha256, "outcome": outcome,
        })

    def active_taint(self, session_id: str, policy: dict[str, Any]) -> dict[str, Any]:
        taint = self._taint_policy(policy)
        window, ttl, now = int(taint["window_calls"]), float(taint["ttl_minutes"]), self._now()
        flags, pendings, _ = self._replay(session_id)
        events: list[dict[str, Any]] = []
        pending_events: list[dict[str, Any]] = []
        def active(record: dict[str, Any], calls: int) -> bool:
            timestamp = self._timestamp(record)
            age_minutes = (now - timestamp).total_seconds() / 60 if timestamp else 0
            return calls <= window and age_minutes < ttl
        for record, calls in flags:
            if active(record, calls):
                event = {"tool": record.get("tool", "unknown"), "directed_injection": record.get("directed_injection"),
                         "kind": record.get("kind"), "calls_ago": calls}
                if record.get("static_rule") is not None:
                    event["static_rule"] = record["static_rule"]
                events.append(event)
        for record, calls in pendings:
            if active(record, calls):
                pending_events.append({"tool": record.get("tool", "unknown"),
                                       "check": record.get("check", {}), "calls_ago": calls})
        return {"active_taint": bool(events or pending_events), "untrusted_events": events,
                "pending_events": pending_events}

    def provenance(self, session_id: str, policy: dict[str, Any]) -> dict[str, Any]:
        """Carry sets for the provenance floor (IP-006): command spans and secret
        values from released results within the provenance window, tagged by
        source class (result/pending/flagged). Aged by the ``advance`` records,
        reset by an operator clear. Hashes and metadata only."""
        prov = policy.get("provenance") if isinstance(policy, dict) else None
        if not isinstance(prov, dict) or not prov.get("enabled", True):
            return {"spans": {}, "secrets": {}, "withheld": {}}
        window = int(prov.get("window_calls", 200))
        ttl = float(prov.get("ttl_minutes", 240))
        now = self._now()
        records = self._records(session_id)
        total_advances = sum(1 for r in records if r.get("record_type") == "advance")
        cleared_at = max((i for i, r in enumerate(records)
                          if r.get("record_type") == "cleared_by_operator"), default=-1)
        rank = {"flagged": 3, "pending": 2, "result": 1}
        class_of = {"observed": "result", "flag": "flagged", "pending": "pending"}
        spans: dict[str, dict[str, Any]] = {}
        secrets: dict[str, dict[str, Any]] = {}
        withheld: dict[str, dict[str, Any]] = {}
        seen_advances = 0
        for index, record in enumerate(records):
            kind = record.get("record_type")
            if kind == "advance":
                seen_advances += 1
                continue
            if index <= cleared_at or (kind not in class_of and kind != "withheld"):
                continue
            age = total_advances - seen_advances
            timestamp = self._timestamp(record)
            age_minutes = (now - timestamp).total_seconds() / 60 if timestamp else 0
            if age > window or age_minutes >= ttl:
                continue
            if kind == "withheld":
                meta_w = {"tool": record.get("tool", "unknown"), "calls_since": age}
                for h in record.get("path_hashes", []):
                    if isinstance(h, str) and h not in withheld:
                        withheld[h] = meta_w
                continue
            source_class = class_of[kind]
            meta = {"source_class": source_class, "tool": record.get("tool", "unknown"), "calls_since": age}
            for h in record.get("span_hashes", []):
                if not isinstance(h, str):
                    continue
                if h not in spans or rank[source_class] > rank[spans[h]["source_class"]]:
                    spans[h] = meta
            for h in record.get("secret_hashes", []):
                if isinstance(h, str) and h not in secrets:
                    secrets[h] = meta
        return {"spans": spans, "secrets": secrets, "withheld": withheld}

    def advance(self, session_id: str, policy: dict[str, Any]) -> dict[str, Any]:
        flags, pendings, _ = self._replay(session_id)
        counters = {str(item.get("event_id", item.get("flag_id", ""))): age + 1
                    for item, age in [*flags, *pendings]}
        self._append(session_id, {"record_type": "advance", "timestamp": self._now().isoformat(),
                                   "counters": counters})
        return self.active_taint(session_id, policy)

    def matches(self, session_id: str, texts: list[str], policy: dict[str, Any]) -> bool:
        historical = self._replay(session_id)[2]
        return any(historical.intersection(self._shingle_hashes(text, policy)) for text in texts)

    def clear(self, session_id: str, reason: str) -> dict[str, Any]:
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("clear reason is required")
        return self._append(session_id, {"record_type": "cleared_by_operator",
                                         "timestamp": self._now().isoformat(), "reason": reason})

    def is_untrusted(self, session_id: str) -> bool:
        return bool(self._replay(session_id)[0])
