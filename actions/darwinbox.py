"""Compatibility facade for modular Darwinbox adapters."""
from __future__ import annotations

import os
import time  # Compatibility patch point; shared module used by DarwinboxClient.
from typing import Any, Mapping

from .darwinbox_adapters import (
    DarwinboxAPIError,
    DarwinboxClient,
    DarwinboxConfigurationError,
    EmployeeDirectoryAdapter,
    LeaveBalance,
    LeaveBalanceAdapter,
    parse_employee_records,
    parse_leave_balances,
)
from .darwinbox_adapters.service import (
    EmployeeResolution,
    awaiting_employee_code,
    extract_employee_code,
    remember_employee_code,
    resolve_cached_employee_id,
    resolve_employee_id as resolve_employee,
)


def _request_payload(employee_id: str) -> dict[str, Any]:
    """Legacy helper retained for existing tests and integrations."""
    payload = DarwinboxClient().auth_payload(
        dataset_key=os.getenv("DARWINBOX_LEAVE_DATASET_KEY", "").strip() or None
    )
    field = os.getenv(
        "DARWINBOX_LEAVE_EMPLOYEE_FIELD",
        os.getenv("DARWINBOX_EMPLOYEE_FIELD", "employee_no"),
    ).strip() or "employee_no"
    payload[field] = employee_id
    return payload


def resolve_employee_id(sender_id: str, metadata: Mapping[str, Any] | None = None) -> str | None:
    """Synchronous cache-only compatibility lookup."""
    return resolve_cached_employee_id(sender_id, metadata)


async def fetch_leave_balance(employee_id: str) -> tuple[list[LeaveBalance], Any]:
    return await LeaveBalanceAdapter().fetch(employee_id)


__all__ = [
    "DarwinboxAPIError", "DarwinboxConfigurationError", "EmployeeDirectoryAdapter",
    "EmployeeResolution", "LeaveBalance", "awaiting_employee_code",
    "extract_employee_code", "fetch_leave_balance", "parse_employee_records",
    "parse_leave_balances", "remember_employee_code", "resolve_employee",
    "resolve_employee_id",
]
