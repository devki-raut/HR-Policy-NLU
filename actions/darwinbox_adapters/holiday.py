"""Darwinbox holiday-list adapter."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping

from .client import DarwinboxAPIError, DarwinboxClient, DarwinboxConfigurationError, required


@dataclass(frozen=True)
class Holiday:
    name: str
    date: date
    day: str | None = None
    optional: bool | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "date": self.date.isoformat(),
            "day": self.day or self.date.strftime("%A"),
            "optional": self.optional,
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


def _date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d-%b-%Y", "%d %B %Y"):
        try:
            return datetime.strptime(text[:20], fmt).date()
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def parse_holidays(payload: Any) -> list[Holiday]:
    found: dict[tuple[date, str], Holiday] = {}
    for record in _records(payload):
        holiday_date = _date(_first(record, (
            "holiday_date", "date", "holidaydate", "from_date", "start_date",
        )))
        name = _first(record, (
            "holiday_name", "name", "title", "occasion", "description",
        ))
        if not holiday_date or not isinstance(name, str) or not name.strip():
            continue
        optional_value = _first(record, ("optional", "is_optional", "holiday_type"))
        optional = None
        if optional_value is not None:
            optional = str(optional_value).strip().casefold() in {
                "1", "true", "yes", "optional", "restricted",
            }
        holiday = Holiday(
            name=name.strip(), date=holiday_date,
            day=str(_first(record, ("day", "weekday", "day_name")) or "").strip() or None,
            optional=optional,
        )
        found[(holiday.date, holiday.name.casefold())] = holiday
    return sorted(found.values(), key=lambda item: (item.date, item.name.casefold()))


class HolidayListAdapter:
    def __init__(self, client: DarwinboxClient | None = None):
        self.client = client or DarwinboxClient()

    @property
    def url(self) -> str:
        return self.client.endpoint(
            url_env="DARWINBOX_HOLIDAY_URL",
            path_env="DARWINBOX_HOLIDAY_PATH",
            default_path="/leavesactionapi/holidaylist",
        )

    async def fetch(self, employee_no: str, year: int) -> tuple[list[Holiday], Any]:
        api_key = required("DARWINBOX_HOLIDAY_API_KEY")
        username = (
            os.getenv("DARWINBOX_HOLIDAY_USERNAME", "").strip()
            or os.getenv("DARWINBOX_LEAVE_USERNAME", "").strip()
        )
        password = (
            os.getenv("DARWINBOX_HOLIDAY_PASSWORD", "").strip()
            or os.getenv("DARWINBOX_LEAVE_PASSWORD", "").strip()
        )
        if not username or not password:
            raise DarwinboxConfigurationError(
                "Darwinbox holiday Basic Auth is not configured"
            )
        raw = await self.client.request(
            url=self.url,
            method="POST",
            payload={
                "api_key": api_key,
                "year": str(year),
                "employee_no": employee_no,
            },
            body_mode="json",
            basic_auth=(username, password),
        )
        holidays = parse_holidays(raw)
        if not holidays:
            raise DarwinboxAPIError("Darwinbox returned no recognizable holidays")
        return holidays, raw
