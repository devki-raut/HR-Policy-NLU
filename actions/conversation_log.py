"""Durable, append-only audit log for policy answers."""
from __future__ import annotations

import fcntl
import json
import os
from actions.runtime_paths import runtime_log_paths, runtime_path
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "artifacts" / "logs" / "conversations.jsonl"


def _log_path() -> Path:
    return runtime_path('logs/conversations.jsonl', DEFAULT_PATH, 'CONVERSATION_LOG_PATH', ROOT)


def _log_paths() -> tuple[Path, ...]:
    return runtime_log_paths('logs/conversations.jsonl', DEFAULT_PATH, 'CONVERSATION_LOG_PATH', ROOT)


def append_conversation(record: Mapping[str, Any]) -> str:
    """Append one locked and fsynced JSON object, then return its event ID."""
    event_id = str(record.get("event_id") or uuid4())
    payload = {
        "schema_version": 1,
        "event_id": event_id,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        **dict(record),
    }
    line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    for path in _log_paths():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return event_id
