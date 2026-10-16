"""Credential-free Darwinbox diagnostics shared by actions and chat front ends."""
from __future__ import annotations

import fcntl
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from actions.runtime_paths import runtime_log_paths, runtime_path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = ROOT / 'artifacts/logs/darwinbox.log'
FIELDS = {'configured', 'missing_settings', 'setting_names', 'auth_mode',
          'http_status', 'exception_type', 'duration_ms', 'channel'}


def diagnostic_path() -> Path:
    return runtime_path('logs/darwinbox.log', DEFAULT_PATH, 'DARWINBOX_DIAGNOSTIC_LOG_PATH', ROOT)


def diagnostic_paths() -> tuple[Path, ...]:
    return runtime_log_paths('logs/darwinbox.log', DEFAULT_PATH, 'DARWINBOX_DIAGNOSTIC_LOG_PATH', ROOT)


def setting_names(error: Exception) -> list[str]:
    """Keep only setting names from a configuration error, never its values."""
    return sorted(set(re.findall(r'\bDARWINBOX_[A-Z0-9_]+\b', str(error))))


def record_darwinbox_event(component: str, operation: str, outcome: str, **details) -> None:
    if component not in {'actions', 'bot', 'web'} or operation not in {'leave_balance', 'holiday_list'}:
        return
    event = {
        'timestamp': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'component': component, 'operation': operation, 'outcome': outcome,
    }
    event.update({key: value for key, value in details.items() if key in FIELDS})
    try:
        line = json.dumps(event, separators=(',', ':')) + '\n'
        for path in diagnostic_paths():
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'a', encoding='utf-8') as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                handle.write(line)
                handle.flush()
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        # Logging must never turn a user request into a failure.
        pass
