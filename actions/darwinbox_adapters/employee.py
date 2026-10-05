"""Darwinbox employee-directory adapter and synchronization service."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from .client import DarwinboxAPIError, DarwinboxClient, DarwinboxConfigurationError, required
from .store import load_store, upsert_employees


def _fields(env: str, defaults: str) -> tuple[str, ...]:
    return tuple(x.strip() for x in os.getenv(env, defaults).split(",") if x.strip())


def _first(record: Mapping[str, Any], names: Iterable[str]) -> Any:
    normalized = {str(k).casefold().replace("_", ""): v for k, v in record.items()}
    for name in names:
        value = normalized.get(name.casefold().replace("_", ""))
        if value not in (None, ""):
            return value
    return None


def _dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _dicts(child)


def parse_employee_records(payload: Any) -> list[dict[str, Any]]:
    number_fields = _fields(
        "DARWINBOX_EMPLOYEE_NUMBER_FIELDS",
        "employee_no,employee_number,employee_code,employee_id,user_id",
    )
    email_fields = _fields(
        "DARWINBOX_EMPLOYEE_EMAIL_FIELDS",
        "official_email,official_email_id,office_email,email_id,email,work_email",
    )
    name_fields = _fields(
        "DARWINBOX_EMPLOYEE_NAME_FIELDS", "employee_name,full_name,name"
    )
    status_fields = _fields(
        "DARWINBOX_EMPLOYEE_STATUS_FIELDS", "employee_status,status,employment_status"
    )
    result: dict[str, dict[str, Any]] = {}
    for item in _dicts(payload):
        number = _first(item, number_fields)
        if number in (None, "") or isinstance(number, (dict, list)):
            continue
        emails = []
        for field in email_fields:
            email = _first(item, (field,))
            if isinstance(email, str) and "@" in email:
                normalized = email.strip().casefold()
                if normalized and normalized not in emails:
                    emails.append(normalized)
        code = str(number).strip()
        if not code:
            continue
        result[code.casefold()] = {
            "employee_no": code,
            "emails": emails,
            "name": str(_first(item, name_fields) or "").strip() or None,
            "status": str(_first(item, status_fields) or "").strip() or None,
        }
    return list(result.values())


def _cursor_for_request(value: str | None) -> str | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp.astimezone(ZoneInfo("Asia/Kolkata")).strftime("%d-%m-%Y %H:%M:%S")
    except ValueError:
        return None


class EmployeeDirectoryAdapter:
    def __init__(self, client: DarwinboxClient | None = None):
        self.client = client or DarwinboxClient()

    @property
    def url(self) -> str:
        return self.client.endpoint(
            url_env="DARWINBOX_EMPLOYEE_URL",
            path_env="DARWINBOX_EMPLOYEE_PATH",
            default_path="/masterapi/employee",
        )

    def dataset_key(self) -> str:
        return (
            os.getenv("DARWINBOX_EMPLOYEE_DATASET_KEY", "").strip()
            or required("DARWINBOX_DATASET_KEY")
        )

    async def fetch(
        self, *, employee_numbers: list[str] | None = None, changed_since: str | None = None
    ) -> list[dict[str, Any]]:
        payload = self.client.auth_payload(dataset_key=self.dataset_key())
        if employee_numbers:
            payload["employee_no"] = employee_numbers
        if changed_since:
            payload["last_modified_att"] = changed_since
        raw = await self.client.request(url=self.url, method="POST", payload=payload)
        records = parse_employee_records(raw)
        if not records and not changed_since and not employee_numbers:
            raise DarwinboxAPIError("Darwinbox returned no recognizable employee records")
        return records

    async def sync(self, *, full: bool) -> dict[str, Any]:
        started = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        store = load_store()
        changed_since = None if full else _cursor_for_request(store.get("incremental_cursor"))
        records = await self.fetch(changed_since=changed_since)
        if full:
            total, changed = upsert_employees(records, replace=True, cursor=started)
        elif records:
            total, changed = upsert_employees(records, replace=False, cursor=started)
        else:
            # A valid empty changes response advances the cursor without deleting cache.
            current = load_store()
            current["synced_at"] = started
            current["incremental_cursor"] = started
            from .store import save_store
            save_store(current)
            total, changed = len(current.get("employees") or {}), 0
        return {"mode": "full" if full else "incremental", "received": len(records), "stored": total, "changed": changed}

    async def refresh_employee(self, employee_no: str) -> dict[str, Any] | None:
        records = await self.fetch(employee_numbers=[employee_no])
        if records:
            cursor = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            upsert_employees(records, replace=False, cursor=cursor)
            return next((row for row in records if row["employee_no"].casefold() == employee_no.casefold()), None)
        return None
