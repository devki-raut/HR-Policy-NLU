"""Darwinbox report adapter for the email-to-employee directory snapshot."""
from __future__ import annotations

import json
import os
from actions.runtime_paths import runtime_mirror_paths, runtime_path
import re
from datetime import date, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Iterable, Mapping

from .client import DarwinboxAPIError, DarwinboxClient, DarwinboxConfigurationError, required

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GUEST = {
    "code": "EII0769",
    "darwinbox_code": "Ell0769",
    "name": "Guest",
    "email": "guest",
    "company": "Emergys Solutions Private Limited",
}


def _key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def _value(row: Mapping[str, Any], names: Iterable[str], *, exact: bool = False) -> Any:
    normalized = {_key(key): value for key, value in row.items()}
    for name in names:
        value = normalized.get(_key(name))
        if value not in (None, "") and not isinstance(value, (dict, list)) and str(value).strip():
            return value
    if exact:
        return None
    # Report Builder column labels vary by tenant. Accept a descriptive
    # column when it contains the requested concept, while keeping exact
    # aliases above as the preferred match.
    concepts = tuple(_key(name) for name in names)
    for column, value in normalized.items():
        if value in (None, "") or isinstance(value, (dict, list)):
            continue
        if any(concept in column or column in concept for concept in concepts):
            return value
    return None


def _rows(payload: Any) -> list[Mapping[str, Any]]:
    if not isinstance(payload, dict):
        return []
    response = payload.get("response")
    data = response.get("data") if isinstance(response, dict) else payload.get("data")
    if isinstance(data, dict):
        data = data.get("data") or data.get("rows") or data.get("records")
    return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


def _past_exit(row: Mapping[str, Any], today: date) -> bool:
    raw = _value(row, ("Date of Exit", "Exit Date", "Last Working Date", "Relieving Date"))
    if not raw:
        return False
    text = str(raw).strip()
    for pattern in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(text, pattern).date() <= today
        except ValueError:
            continue
    return False


def _inactive(row: Mapping[str, Any], today: date) -> bool:
    status = _key(_value(row, ("Employee Status", "Employment Status", "Status")))
    inactive_terms = ("resign", "inactive", "separat", "terminat", "reliev", "exited")
    return any(term in status for term in inactive_terms) or _past_exit(row, today)


def parse_employee_report(payload: Any, *, today: date | None = None) -> dict[str, dict[str, str]]:
    """Normalize active report rows into the protected static mapping format."""
    # The selected report is the active employee snapshot. Do not interpret
    # generic Status/exit columns because those can describe leave records.
    employees: dict[str, dict[str, str]] = {}
    for row in _rows(payload):
        email = str(_value(row, (
            "Company Email", "Company Email ID", "Official Email", "Official Email ID",
            "Official Company Email", "Corporate Email", "Work Email", "Email", "Email ID",
        )) or "").strip().casefold()
        code = str(_value(row, (
            "Employee Id", "Employee ID", "Employee Code", "Employee No", "Employee Number",
            "Employee Reference Number", "Personnel Number",
        )) or "").strip()
        if "@" not in email or not code:
            continue
        name = str(_value(row, ("Full Name", "Employee Name", "Name", "Display Name"), exact=True) or "").strip()
        if not name:
            name = " ".join(
                str(_value(row, (field,), exact=True) or "").strip()
                for field in ("First Name", "Last Name")
            ).strip()
        company = str(_value(row, ("Company", "Company Name", "Group Company")) or "").strip()
        profile = {
            "code": code,
            "darwinbox_code": code,
            "name": name or email.split("@", 1)[0],
            "email": email,
        }
        if company:
            profile["company"] = company
        employees[email] = profile
    return employees


def directory_path() -> Path:
    return runtime_path('darwinbox/employee_directory.json', ROOT/'data/employee_directory.json', 'DARWINBOX_EMPLOYEE_MAP_FILE', ROOT)


def directory_paths() -> tuple[Path, ...]:
    return runtime_mirror_paths('darwinbox/employee_directory.json', ROOT/'data/employee_directory.json', 'DARWINBOX_EMPLOYEE_MAP_FILE', ROOT)


def replace_employee_directory(employees: Mapping[str, Mapping[str, Any]], path: Path | None = None) -> int:
    """Replace the full active snapshot atomically while retaining the guest profile."""
    if not employees:
        raise DarwinboxAPIError("Darwinbox report contained no recognizable active employee records")
    explicit_path = path is not None
    paths = (path,) if explicit_path else directory_paths()
    path = paths[0]
    guest: Mapping[str, Any] = DEFAULT_GUEST
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(current, dict) and isinstance(current.get("guest"), dict):
            guest = current["guest"]
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        pass
    snapshot = {str(key).casefold(): dict(value) for key, value in employees.items()}
    snapshot["guest"] = dict(guest)
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(snapshot, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    if len(paths) > 1:
        backup = paths[1]
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup_temporary = backup.with_suffix(backup.suffix + f'.{os.getpid()}.tmp')
        backup_temporary.write_bytes(path.read_bytes())
        os.chmod(backup_temporary, 0o600)
        backup_temporary.replace(backup)
    return len(snapshot) - 1


class EmployeeReportAdapter:
    def __init__(self, client: DarwinboxClient | None = None):
        self.client = client or DarwinboxClient()

    @property
    def url(self) -> str:
        return self.client.endpoint(
            url_env="DARWINBOX_REPORT_URL",
            path_env="DARWINBOX_REPORT_PATH",
            default_path="/reportsbuilderapi/reportdata",
        )

    def basic_auth(self) -> tuple[str, str]:
        # Darwinbox Basic Auth is shared across modules. Only the report API
        # key and report ID are specific to this endpoint.
        username = os.getenv("DARWINBOX_LEAVE_USERNAME", "").strip()
        password = os.getenv("DARWINBOX_LEAVE_PASSWORD", "").strip()
        if not username or not password:
            raise DarwinboxConfigurationError(
                "DARWINBOX_LEAVE_USERNAME and DARWINBOX_LEAVE_PASSWORD must be configured"
            )
        return username, password

    async def fetch(self) -> Any:
        return await self.client.request(
            url=self.url,
            method="POST",
            payload={
                "api_key": required("DARWINBOX_REPORT_API_KEY"),
                "report_id": required("DARWINBOX_REPORT_ID"),
            },
            basic_auth=self.basic_auth(),
            headers={"Content-Type": "application/json"},
        )

    async def sync(self) -> dict[str, Any]:
        raw = await self.fetch()
        received = len(_rows(raw))
        employees = parse_employee_report(raw)
        if not employees:
            raise DarwinboxAPIError(
                f"Darwinbox report returned {received} rows but no active rows with both employee code and email"
            )
        stored = replace_employee_directory(employees)
        summary = raw.get("response", {}).get("summary", {}) if isinstance(raw, dict) else {}
        return {
            "received": received,
            "stored": stored,
            "report_id": str(summary.get("report_id") or os.getenv("DARWINBOX_REPORT_ID", "")),
            "report_status": summary.get("status"),
        }
