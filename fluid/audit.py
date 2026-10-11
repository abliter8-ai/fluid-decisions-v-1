"""Append-only decision records; state bodies are never persisted."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def data_directory() -> Path:
    return Path(os.environ.get('FLUID_DATA_DIR', '~/.fluid-decisions')).expanduser()


def append_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    raw = (json.dumps(record, allow_nan=False, separators=(',', ':')) + '\n').encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        # A single append avoids interleaving between concurrent short records.
        if os.write(fd, raw) != len(raw):
            raise OSError('incomplete audit append')
    finally:
        os.close(fd)


class Audit:
    def __init__(self, path: Path | None = None):
        self.path = path or data_directory() / 'audit.jsonl'

    def record(self, decision: dict[str, Any], *, state: Any = None) -> None:
        fields = ('job', 'lane', 'pack_version', 'verdict', 'answers', 'timings',
                  'usage', 'reasons', 'passes', 'flagged', 'complete', 'taint_cleared',
                  'clearance_reason', 'session_id_sha256', 'static_injection_rule',
                  'windows', 'first_flagged_window', 'window_answers', 'session_assessment',
                  'check', 'checked', 'budget_source', 'expected_ms', 'resolved', 'scoping',
                  'budget', 'provenance',
                  # IP-007 counts and probabilities (never raw text): the carries_out
                  # scores, the redaction count, and the unit indices and scores.
                  'carries_out', 'units_redacted', 'units', 'located_units',
                  'confirmed_units', 'unit_probabilities',
                  # IP-009 candidate checks: per-item statuses and labels, no candidate
                  # or packet text (that stays in the never-recorded state arg).
                  'constraints', 'checks', 'failing', 'uncertain', 'passes',
                  'constraint_order', 'document_type', 'document_type_top',
                  'completeness', 'page_split', 'note',
                  # IP-011 file ownership: the per-target facts (booleans and the
                  # tracked tri-state), the resolved cwd. No file content.
                  'ownership')
        record = {key: decision[key] for key in fields if key in decision}
        record['timestamp'] = datetime.now(timezone.utc).isoformat()
        if os.environ.get('FLUID_AUDIT_STATE') == '1':
            raw = json.dumps(state, sort_keys=True, ensure_ascii=False).encode()
            record['state'] = {'sha256': hashlib.sha256(raw).hexdigest(),
                               'preview': '[state content redacted]', 'bytes': len(raw)}
        append_record(self.path, record)
