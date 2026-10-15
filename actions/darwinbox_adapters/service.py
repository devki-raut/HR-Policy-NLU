"""Identity resolution and safe Darwinbox employee-code enrollment."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .client import DarwinboxAPIError, DarwinboxConfigurationError
from .employee import EmployeeDirectoryAdapter
from .leave import LeaveBalanceAdapter
from .store import (
    bind_identity,
    employee_by_code,
    employee_by_identity,
    is_pending,
    mark_pending,
    store_self_reported_employee,
    verified_email,
)


@dataclass(frozen=True)
class EmployeeResolution:
    employee_no: str | None
    status: str


def _static_mapping() -> dict[str, str]:
    mapping: dict[str, Any] = {}
    raw = os.getenv("DARWINBOX_EMPLOYEE_MAP_JSON", "{}").strip() or "{}"
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            mapping.update(parsed)
    except json.JSONDecodeError as exc:
        raise DarwinboxConfigurationError("DARWINBOX_EMPLOYEE_MAP_JSON is invalid JSON") from exc
    from .report import directory_path
    configured_path = directory_path()
    path = str(configured_path) if os.getenv('DARWINBOX_EMPLOYEE_MAP_FILE','').strip() or configured_path.exists() else ''
    if path:
        try:
            parsed = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                mapping.update(parsed)
        except (OSError, json.JSONDecodeError) as exc:
            raise DarwinboxConfigurationError("DARWINBOX_EMPLOYEE_MAP_FILE is unreadable or invalid") from exc
    result = {}
    for key, value in mapping.items():
        if isinstance(value, dict):
            value = (
                value.get("darwinbox_code") or value.get("api_code")
                or value.get("code") or value.get("employee_no")
                or value.get("employee_id")
            )
        if value not in (None, ""):
            result[str(key).strip().casefold()] = str(value).strip()
    return result


def resolve_cached_employee_id(sender_id: str, metadata: Mapping[str, Any] | None = None) -> str | None:
    metadata = metadata or {}
    identities = [
        metadata.get("aad_object_id"), metadata.get("email"),
        metadata.get("user_principal_name"), sender_id,
    ]
    mapping = _static_mapping()
    for identity in identities:
        value = mapping.get(str(identity or "").strip().casefold())
        if value:
            return value
    employee = employee_by_identity(sender_id, metadata)
    return str(employee["employee_no"]) if employee else None


async def resolve_employee_id(
    sender_id: str, metadata: Mapping[str, Any] | None = None
) -> EmployeeResolution:
    metadata = metadata or {}
    cached = resolve_cached_employee_id(sender_id, metadata)
    if cached:
        return EmployeeResolution(cached, "cached")
    adapter = EmployeeDirectoryAdapter()
    try:
        await adapter.sync(full=False)
        cached = resolve_cached_employee_id(sender_id, metadata)
        if cached:
            return EmployeeResolution(cached, "incremental_refresh")
        if os.getenv("DARWINBOX_FULL_REFRESH_ON_MISS", "true").casefold() in {"1", "true", "yes"}:
            await adapter.sync(full=True)
            cached = resolve_cached_employee_id(sender_id, metadata)
            if cached:
                return EmployeeResolution(cached, "full_refresh")
    except DarwinboxConfigurationError:
        if verified_email(metadata):
            mark_pending(sender_id, metadata)
            return EmployeeResolution(None, "manual_code_required")
        return EmployeeResolution(None, "not_configured")
    except (DarwinboxAPIError, OSError, RuntimeError, ValueError):
        if verified_email(metadata):
            mark_pending(sender_id, metadata)
            return EmployeeResolution(None, "manual_code_required")
        return EmployeeResolution(None, "refresh_failed")
    mark_pending(sender_id, metadata)
    return EmployeeResolution(None, "not_found")


_CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{1,39}$")
_CODE_IN_PHRASE = re.compile(
    r"(?:employee\s*)?(?:code|number|id)\s*(?:is|:|=)?\s*([A-Za-z0-9][A-Za-z0-9._/-]{1,39})",
    re.IGNORECASE,
)


def extract_employee_code(text: str) -> str | None:
    value = str(text or "").strip()
    match = _CODE_IN_PHRASE.search(value)
    candidate = match.group(1) if match else value
    candidate = candidate.strip().rstrip(".,;:!?")
    return candidate if _CODE.fullmatch(candidate) else None


def awaiting_employee_code(sender_id: str, metadata: Mapping[str, Any]) -> bool:
    return bool(metadata.get("channel") == "msteams" and is_pending(sender_id, metadata))


def _active(record: Mapping[str, Any]) -> bool:
    value = str(record.get("status") or "").strip().casefold()
    return value not in {"inactive", "deactivated", "terminated", "separated", "0", "false"}


async def remember_employee_code(
    sender_id: str, metadata: Mapping[str, Any], employee_no: str
) -> EmployeeResolution:
    if metadata.get("channel") != "msteams":
        return EmployeeResolution(None, "authenticated_teams_required")
    email = verified_email(metadata)
    if not email:
        return EmployeeResolution(None, "verified_email_required")
    employee = employee_by_code(employee_no)
    if not employee:
        try:
            employee = await EmployeeDirectoryAdapter().refresh_employee(employee_no)
        except DarwinboxConfigurationError:
            # Employee Core is optional for this fallback. Confirm that the code
            # is accepted by the leave API before storing it for the Teams email.
            try:
                await LeaveBalanceAdapter().fetch(employee_no)
            except DarwinboxConfigurationError:
                return EmployeeResolution(None, "not_configured")
            except DarwinboxAPIError:
                return EmployeeResolution(None, "leave_verification_failed")
            employee = store_self_reported_employee(email, employee_no)
        except (DarwinboxAPIError, OSError, RuntimeError, ValueError):
            try:
                await LeaveBalanceAdapter().fetch(employee_no)
            except (DarwinboxConfigurationError, DarwinboxAPIError):
                return EmployeeResolution(None, "leave_verification_failed")
            employee = store_self_reported_employee(email, employee_no)
    if not employee:
        return EmployeeResolution(None, "employee_not_found")
    if not _active(employee):
        return EmployeeResolution(None, "employee_inactive")
    employee_emails = {str(value).casefold() for value in employee.get("emails") or []}
    if not employee_emails:
        return EmployeeResolution(None, "employee_email_missing")
    if email not in employee_emails:
        return EmployeeResolution(None, "identity_mismatch")
    bind_identity(sender_id, metadata, str(employee["employee_no"]))
    return EmployeeResolution(str(employee["employee_no"]), "bound")
