"""Darwinbox leave-balance adapter."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

from .client import DarwinboxAPIError, DarwinboxClient


@dataclass(frozen=True)
class LeaveBalance:
    leave_type: str
    balance: str
    unit: str = "days"


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
        balance = _first(record, ("balance", "available_balance", "available", "leave_balance", "closing_balance"))
        leave_type = _first(record, ("leave_type", "leave_type_name", "leave_name", "name", "type"))
        if isinstance(leave_type, dict):
            leave_type = _first(leave_type, ("name", "description", "code"))
        if balance is None or leave_type is None or isinstance(balance, (dict, list)):
            continue
        unit = _first(record, ("unit", "leave_unit")) or "days"
        key = (str(leave_type).strip().casefold(), str(balance).strip(), str(unit).strip().casefold())
        if key not in seen:
            seen.add(key)
            found.append(LeaveBalance(str(leave_type).strip(), str(balance).strip(), str(unit).strip()))
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

    async def fetch(self, employee_no: str) -> tuple[list[LeaveBalance], Any]:
        payload = self.client.auth_payload(
            dataset_key=os.getenv("DARWINBOX_LEAVE_DATASET_KEY", "").strip() or None
        )
        field = os.getenv("DARWINBOX_LEAVE_EMPLOYEE_FIELD", os.getenv("DARWINBOX_EMPLOYEE_FIELD", "employee_no")).strip() or "employee_no"
        payload[field] = employee_no
        method = os.getenv("DARWINBOX_LEAVE_BALANCE_METHOD", "POST")
        raw = await self.client.request(url=self.url, method=method, payload=payload)
        balances = parse_leave_balances(raw)
        if not balances:
            raise DarwinboxAPIError("Darwinbox returned no recognizable leave balances")
        return balances, raw
