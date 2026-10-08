"""Darwinbox leave-balance adapter."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

from .client import (
    DarwinboxAPIError,
    DarwinboxClient,
    DarwinboxConfigurationError,
)


@dataclass(frozen=True)
class LeaveBalance:
    leave_type: str
    balance: str | None = None
    unit: str = "days"
    employee_name: str | None = None
    employee_no: str | None = None
    leave_id: str | None = None
    leave_code: str | None = None
    accrued_so_far_this_year: str | None = None
    previous_balance: str | None = None
    adjustment_balance: str | None = None
    yearly_allotment: str | None = None
    taken: str | None = None
    utilized_leaves_this_year: str | None = None
    already_taken: str | None = None
    applied_unpaid: str | None = None
    system_unpaid: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "leave_name": self.leave_type,
            "available_balance": self.balance,
            "unit": self.unit,
            "employee_name": self.employee_name,
            "employee_no": self.employee_no,
            "leave_id": self.leave_id,
            "leave_code": self.leave_code,
            "accrued_so_far_this_year": self.accrued_so_far_this_year,
            "previous_balance": self.previous_balance,
            "adjustment_balance": self.adjustment_balance,
            "yearly_allotment": self.yearly_allotment,
            "taken": self.taken,
            "utilized_leaves_this_year": self.utilized_leaves_this_year,
            "already_taken": self.already_taken,
            "applied_unpaid": self.applied_unpaid,
            "system_unpaid": self.system_unpaid,
        }


def _first(record: Mapping[str, Any], names: tuple[str, ...]) -> Any:
    normalized = {str(k).casefold().replace("_", ""): v for k, v in record.items()}
    for name in names:
        value = normalized.get(name.casefold().replace("_", ""))
        if value not in (None, ""):
            return value
    return None


def _records(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _records(child)
    elif isinstance(value, list):
        for child in value:
            yield from _records(child)


def parse_leave_balances(payload: Any) -> list[LeaveBalance]:
    found: list[LeaveBalance] = []
    seen = set()
    for record in _records(payload):
        balance = _first(record, (
            "balance",
            "available_balance",
            "currently_available_balance",
            # Field spelling returned by Darwinbox's leave-balance endpoint.
            "currently_availabel_balance",
            "available",
            "leave_balance",
            "closing_balance",
        ))
        leave_type = _first(record, ("leave_type", "leave_type_name", "leave_name", "name", "type"))
        if isinstance(leave_type, dict):
            leave_type = _first(leave_type, ("name", "description", "code"))
        if leave_type is None or isinstance(balance, (dict, list)):
            continue
        metric_names = (
            "accrued_so_far_this_year", "previous_balance", "adjustment_balance",
            "yearly_allotment", "taken", "utilized_leaves_this_year",
            "already_taken", "applied_unpaid", "system_unpaid",
        )
        metrics = {name: _first(record, (name,)) for name in metric_names}
        if balance is None and not any(value is not None for value in metrics.values()):
            continue
        unit = _first(record, ("unit", "leave_unit")) or "days"
        def text(value: Any) -> str | None:
            return None if value in (None, "") else str(value).strip()

        key = (str(leave_type).strip().casefold(), text(balance), str(unit).strip().casefold())
        if key not in seen:
            seen.add(key)
            found.append(LeaveBalance(
                leave_type=str(leave_type).strip(),
                balance=text(balance),
                unit=str(unit).strip(),
                employee_name=text(_first(record, ("employee_name",))),
                employee_no=text(_first(record, ("employee_no", "employee_number"))),
                leave_id=text(_first(record, ("leave_id",))),
                leave_code=text(_first(record, ("leave_code",))),
                **{name: text(value) for name, value in metrics.items()},
            ))
    return found


class LeaveBalanceAdapter:
    def __init__(self, client: DarwinboxClient | None = None):
        self.client = client or DarwinboxClient()

    @property
    def url(self) -> str:
        # The leave-balance path is tenant-provisioned; an absolute legacy URL remains supported.
        return self.client.endpoint(
            url_env="DARWINBOX_LEAVE_BALANCE_URL",
            path_env="DARWINBOX_LEAVE_BALANCE_PATH",
            default_path="/YOUR-PROVISIONED-LEAVE-BALANCE-ENDPOINT",
        )

    def payload(self, employee_no: str) -> dict[str, Any]:
        api_key = os.getenv("DARWINBOX_LEAVE_API_KEY", "").strip()
        if api_key:
            configured_names = os.getenv(
                "DARWINBOX_LEAVE_NAMES",
                "Comp off, ESOL_ Privilege Leave, ESOL_ Casual Leave, "
                "Emergys_Bereavement Leave, Leave Without Pay"
            )
            leave_names = [
                name.strip() for name in configured_names.split(",") if name.strip()
            ]
            payload: dict[str, Any] = {
                "api_key": api_key,
                "ignore_rounding": os.getenv(
                    "DARWINBOX_LEAVE_IGNORE_ROUNDING", "1"
                ).strip() or "1",
                "employee_nos": [employee_no],
            }
            if leave_names:
                payload["leave_names"] = leave_names
            return payload

        payload = self.client.auth_payload(
            dataset_key=os.getenv("DARWINBOX_LEAVE_DATASET_KEY", "").strip() or None
        )
        field = os.getenv(
            "DARWINBOX_LEAVE_EMPLOYEE_FIELD",
            os.getenv("DARWINBOX_EMPLOYEE_FIELD", "employee_no"),
        ).strip() or "employee_no"
        payload[field] = employee_no
        return payload

    async def fetch(self, employee_no: str) -> tuple[list[LeaveBalance], Any]:
        method = os.getenv("DARWINBOX_LEAVE_BALANCE_METHOD", "POST")
        username = os.getenv("DARWINBOX_LEAVE_USERNAME", "").strip()
        password = os.getenv("DARWINBOX_LEAVE_PASSWORD", "").strip()
        if bool(username) != bool(password):
            raise DarwinboxConfigurationError(
                "DARWINBOX_LEAVE_USERNAME and DARWINBOX_LEAVE_PASSWORD must both be configured"
            )
        raw = await self.client.request(
            url=self.url,
            method=method,
            payload=self.payload(employee_no),
            body_mode="json",
            basic_auth=(username, password) if username else None,
        )
        balances = parse_leave_balances(raw)
        if not balances:
            raise DarwinboxAPIError("Darwinbox returned no recognizable leave balances")
        return balances, raw
