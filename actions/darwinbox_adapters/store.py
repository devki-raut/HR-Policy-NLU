"""Locked persistent employee directory and verified Teams identity bindings."""
from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[2]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def employee_store_path() -> Path:
    configured = os.getenv("DARWINBOX_EMPLOYEE_STORE_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()
    if os.getenv("WEBSITE_INSTANCE_ID"):
        return Path("/home/data/EmployeeAssist/darwinbox/employees.json")
    return ROOT / "artifacts" / "private" / "darwinbox_employees.json"


def empty_store() -> dict[str, Any]:
    return {
        "version": 1,
        "synced_at": None,
        "incremental_cursor": None,
        "employees": {},
        "identity_bindings": {},
        "pending_identity": {},
    }


@contextmanager
def _locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def load_store(path: Path | None = None) -> dict[str, Any]:
    path = path or employee_store_path()
    with _locked(path):
        if not path.exists():
            return empty_store()
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("Darwinbox employee store is unreadable") from exc
        if not isinstance(value, dict) or value.get("version") != 1:
            raise RuntimeError("Unsupported Darwinbox employee store format")
        base = empty_store()
        base.update(value)
        return base


def save_store(value: Mapping[str, Any], path: Path | None = None) -> None:
    path = path or employee_store_path()
    with _locked(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(dict(value), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)


def identity_keys(sender_id: str, metadata: Mapping[str, Any] | None) -> list[str]:
    metadata = metadata or {}
    values = [
        ("aad", metadata.get("aad_object_id")),
        ("email", metadata.get("email")),
        ("upn", metadata.get("user_principal_name")),
        ("teams", metadata.get("teams_user_id")),
    ]
    # A sender ID is trusted for binding only on the authenticated Teams channel.
    if metadata.get("channel") == "msteams":
        values.append(("sender", sender_id))
    found = []
    for prefix, raw in values:
        value = str(raw or "").strip().casefold()
        if value:
            key = f"{prefix}:{value}"
            if key not in found:
                found.append(key)
    return found


def verified_email(metadata: Mapping[str, Any] | None) -> str | None:
    metadata = metadata or {}
    if metadata.get("channel") != "msteams":
        return None
    value = metadata.get("email") or metadata.get("user_principal_name")
    value = str(value or "").strip().casefold()
    return value or None


def upsert_employees(
    records: Iterable[Mapping[str, Any]], *, replace: bool, cursor: str
) -> tuple[int, int]:
    store = load_store()
    previous = dict(store.get("employees") or {})
    employees = {} if replace else dict(previous)
    changed = 0
    for record in records:
        code = str(record.get("employee_no") or "").strip()
        if not code:
            continue
        key = code.casefold()
        normalized = dict(record)
        normalized["employee_no"] = code
        prior = previous.get(key)
        prior_content = {k: v for k, v in (prior or {}).items() if k != "updated_at"}
        if prior_content != normalized:
            changed += 1
            normalized["updated_at"] = utc_now()
        elif prior:
            normalized["updated_at"] = prior.get("updated_at")
        employees[key] = normalized
    if replace:
        changed += len(set(previous) - set(employees))
    if replace and not employees:
        raise ValueError("Refusing to replace employee store with an empty directory")
    store["employees"] = employees
    store["synced_at"] = utc_now()
    store["incremental_cursor"] = cursor
    save_store(store)
    return len(employees), changed


def store_self_reported_employee(email: str, employee_no: str) -> dict[str, Any]:
    """Persist a Teams-email mapping after the leave API accepts the code."""
    store = load_store()
    employees = dict(store.get("employees") or {})
    record = {
        "employee_no": employee_no,
        "emails": [email.casefold()],
        "name": None,
        "status": "Active",
        "source": "teams_self_reported",
        "verified_by": "leave_balance_api",
        "updated_at": utc_now(),
    }
    employees[employee_no.casefold()] = record
    store["employees"] = employees
    save_store(store)
    return record


def employee_by_code(code: str) -> dict[str, Any] | None:
    return (load_store().get("employees") or {}).get(str(code).strip().casefold())


def employee_by_identity(sender_id: str, metadata: Mapping[str, Any] | None) -> dict[str, Any] | None:
    store = load_store()
    employees = store.get("employees") or {}
    bindings = store.get("identity_bindings") or {}
    for key in identity_keys(sender_id, metadata):
        code = bindings.get(key)
        if code and str(code).casefold() in employees:
            return employees[str(code).casefold()]
    email = verified_email(metadata)
    if email:
        matches = [row for row in employees.values() if email in (row.get("emails") or [])]
        if len(matches) == 1:
            return matches[0]
    return None


def bind_identity(sender_id: str, metadata: Mapping[str, Any], employee_no: str) -> None:
    store = load_store()
    employee = (store.get("employees") or {}).get(employee_no.casefold())
    if not employee:
        raise ValueError("Employee does not exist in the synchronized directory")
    bindings = dict(store.get("identity_bindings") or {})
    for key in identity_keys(sender_id, metadata):
        bindings[key] = employee["employee_no"]
    store["identity_bindings"] = bindings
    pending = dict(store.get("pending_identity") or {})
    for key in identity_keys(sender_id, metadata):
        pending.pop(key, None)
    store["pending_identity"] = pending
    save_store(store)


def mark_pending(sender_id: str, metadata: Mapping[str, Any]) -> None:
    store = load_store()
    pending = dict(store.get("pending_identity") or {})
    for key in identity_keys(sender_id, metadata):
        pending[key] = utc_now()
    store["pending_identity"] = pending
    save_store(store)


def is_pending(sender_id: str, metadata: Mapping[str, Any]) -> bool:
    pending = load_store().get("pending_identity") or {}
    return any(key in pending for key in identity_keys(sender_id, metadata))
