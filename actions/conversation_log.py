"""Durable, append-only audit log for policy answers."""
from __future__ import annotations

import fcntl
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "artifacts" / "logs" / "conversations.jsonl"


def _log_path() -> Path:
    configured = os.getenv("CONVERSATION_LOG_PATH")
    if configured:
        return Path(configured).expanduser()
    if os.getenv("WEBSITE_INSTANCE_ID"):
        return Path("/home/data/EmployeeAssist/logs/conversations.jsonl")
    return DEFAULT_PATH


def append_conversation(record: Mapping[str, Any]) -> str:
    """Append one locked and fsynced JSON object, then return its event ID."""
    event_id = str(record.get("event_id") or uuid4())
    payload = {
        "schema_version": 1,
        "event_id": event_id,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        **dict(record),
    }
    path = _log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return event_id
