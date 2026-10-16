#!/usr/bin/env python3
"""Build upload-ready /app and /tmp/employeeassist overlays."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
RELEASES = ROOT / "artifacts/releases"
VERSION = json.loads((ROOT / "teams_app/app_package/manifest.json").read_text())["version"]
UPLOAD_ROOT = RELEASES
TARGET = UPLOAD_ROOT / VERSION
APP = TARGET / "app"
RUNTIME = TARGET / "tmp/employeeassist"
ROOT_FILES = (
    ".dockerignore", "Dockerfile", "Dockerfile.multi", "Makefile", "README.md",
    "config.yml", "domain.yml", "endpoints.yml", "nginx.conf",
    "requirements.txt", "requirements-jupyter.txt", "setup.py",
)
SOURCE_DIRS = ("actions", "data", "deploy", "scripts", "teams_app", "tests")
EXCLUDED_NAMES = {"__pycache__", ".git", ".rasa", "employee_directory.json"}
LOG_NAMES = ("actions", "rasa", "web", "bot", "darwinbox", "deployment", "jupyter",
             "darwinbox-sync", "employee-report-sync", "ticket_mail")
ENVIRONMENT_TEMPLATE = """# Copy this file to .env for local use, or configure the same names in the host.
# Replace every <...> placeholder. Never commit or distribute the completed .env.
PUBLIC_BASE_URL=https://dockerpoc-webapp01.azurewebsites.net/employeeassist/
DATA_ROOT=/tmp
WEB_PORT=8608
ENTRA_AUTH_REQUIRED=true
ENTRA_TENANT_ID=<directory-tenant-uuid>
MICROSOFT_TENANT_ID=<same-directory-tenant-uuid>
ENTRA_CLIENT_ID=9e65e729-0e91-4a00-a03d-e8b9d3e9e841
MICROSOFT_APP_ID=9e65e729-0e91-4a00-a03d-e8b9d3e9e841
MICROSOFT_APP_PASSWORD=<client-secret-value>
ENTRA_CLIENT_SECRET=<client-secret-value>
ENTRA_API_RESOURCE=api://dockerpoc-webapp01.azurewebsites.net/botid-9e65e729-0e91-4a00-a03d-e8b9d3e9e841
ENTRA_REDIRECT_URI=https://dockerpoc-webapp01.azurewebsites.net/employeeassist
TEAMS_MAIL_OAUTH_CONNECTION=<azure-bot-oauth-connection-name>
TEAMS_SUPERADMIN_EMAILS=<admin@example.com>
TEAMS_SUPERADMIN_OBJECT_IDS=<admin-entra-object-uuid>
TEAMS_BROADCAST_TENANT_ID=<directory-tenant-uuid>
TEAMS_BROADCAST_DB=teams/broadcast.sqlite3
JUPYTER_TOKEN=<long-random-token>
DARWINBOX_EMPLOYEE_MAP_FILE=data/employee_directory.json
DARWINBOX_BASE_URL=https://your-tenant.darwinbox.in
DARWINBOX_LEAVE_BALANCE_PATH=/leavesactionapi/leavebalance
DARWINBOX_LEAVE_API_KEY=<leave-api-key>
DARWINBOX_LEAVE_USERNAME=<basic-auth-username>
DARWINBOX_LEAVE_PASSWORD=<basic-auth-password>
DARWINBOX_HOLIDAY_PATH=/leavesactionapi/holidaylist
DARWINBOX_HOLIDAY_API_KEY=<holiday-api-key>
DARWINBOX_REPORT_API_KEY=<report-api-key>
DARWINBOX_REPORT_ID=<report-id>
DARWINBOX_REPORT_SYNC_HOUR=16
DARWINBOX_REPORT_SYNC_MINUTE=30
FAQ_EMBEDDING_THRESHOLD=0.75
FAQ_EMBEDDING_MARGIN=0.01
RASA_URL=http://127.0.0.1:5005
STORAGE_BACKEND=local
"""
HANDOFF = f"""# EmployeeAssist {VERSION} deployment handoff

This directory is self-contained. It includes the unchanged Dockerfiles,
Makefile, dependencies, application source, data, deployment helpers and the
trained Rasa model. Do not copy files from the source checkout.

## Environment

Copy `ENVIRONMENT.example` to `.env` for local deployment and replace every
`<...>` placeholder. For Azure, configure the same names as application
settings. Never publish the completed `.env`.

## Build and run

```bash
docker build --target deployment -f Dockerfile -t employeeassist:{VERSION} .
docker run --rm --name employeeassist --env-file .env -p 8610:8610 employeeassist:{VERSION}
```

For an existing container, copy this `app` directory to `/app` and copy the
sibling `tmp/employeeassist` directory to `/tmp/employeeassist`. Preserve the
layout and application-user ownership, then restart the complete container.

Validate with `python deploy/check_deployment.py` and
`python scripts/check_darwinbox_config.py`. Cloud logs are written under
`/tmp/employeeassist/logs` and mirrored under `/app/artifacts/logs`.
"""


def create_log_files(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name in LOG_NAMES:
        (directory / f"{name}.log").touch(mode=0o600)
    (directory / "conversations.jsonl").touch(mode=0o600)


def create_runtime_data(root: Path, employee_relative: str, database_relative: str) -> None:
    directory = root / employee_relative
    directory.parent.mkdir(parents=True, exist_ok=True)
    directory.write_text(json.dumps({"guest": {
        "code": "EII0769", "darwinbox_code": "Ell0769", "name": "Guest",
        "email": "guest", "company": "Emergys Solutions Private Limited",
    }}, indent=2) + "\n")
    directory.chmod(0o600)
    database = root / database_relative
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE IF NOT EXISTS drafts (id TEXT PRIMARY KEY, owner TEXT, subject TEXT, body TEXT, status TEXT, created REAL, to_list TEXT NOT NULL DEFAULT 'helpdesk@emergys.com', cc_list TEXT NOT NULL DEFAULT '')")
        db.execute("CREATE TABLE IF NOT EXISTS pending_auth (owner TEXT PRIMARY KEY, draft_id TEXT, connection TEXT, exchange_id TEXT, expires REAL, status TEXT)")
    database.chmod(0o600)


def create_state_layout(root: Path, *, cloud: bool) -> None:
    artifact_root = root if cloud else root / "artifacts"
    for name in ("control", "identity", "jupyter", "logs", "private"):
        (artifact_root / name).mkdir(parents=True, exist_ok=True)
    for name in ("config", "data", "runtime", "ipython"):
        (artifact_root / "jupyter" / name).mkdir(parents=True, exist_ok=True)
    (artifact_root / "identity/microsoft_identities.json").write_text(
        json.dumps({"version": 1, "identities": {}}, indent=2) + "\n"
    )
    (artifact_root / "private/darwinbox_employees.json").write_text(json.dumps({
        "version": 1, "synced_at": None, "incremental_cursor": None,
        "employees": {}, "identity_bindings": {}, "pending_identity": {},
    }, indent=2) + "\n")


def permitted(path: Path) -> bool:
    relative = path.relative_to(ROOT)
    return path.suffix.casefold() != ".zip" and not any(
        part in EXCLUDED_NAMES or part.startswith(".env") for part in relative.parts
    )


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def clear_release_preserving_environment() -> None:
    """Refresh a handoff without reading, replacing or deleting app/.env."""
    if not TARGET.exists():
        return
    for child in TARGET.iterdir():
        if child == APP:
            for app_child in APP.iterdir():
                if app_child.name == ".env":
                    continue
                _remove(app_child)
        else:
            _remove(child)


def copy_source() -> Path:
    clear_release_preserving_environment()
    APP.mkdir(parents=True, exist_ok=True)
    for name in ROOT_FILES:
        shutil.copy2(ROOT / name, APP / name)
    (APP / "ENVIRONMENT.example").write_text(ENVIRONMENT_TEMPLATE)
    (APP / "DEPLOYMENT-HANDOFF.md").write_text(HANDOFF)
    for directory in SOURCE_DIRS:
        for source in (ROOT / directory).rglob("*"):
            if source.is_file() and not source.is_symlink() and permitted(source):
                destination = APP / source.relative_to(ROOT)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
    models = sorted((ROOT / "models").glob("*.tar.gz"), key=lambda item: item.stat().st_mtime)
    if not models:
        raise FileNotFoundError("No trained Rasa model was found")
    model = models[-1]
    (APP / "models").mkdir()
    shutil.copy2(model, APP / "models" / model.name)
    create_log_files(ROOT / "artifacts/logs")
    create_log_files(APP / "artifacts/logs")
    create_state_layout(APP, cloud=False)
    create_runtime_data(APP, "data/employee_directory.json", "artifacts/private/teams_ticket_mail.sqlite3")
    for report in (ROOT / "artifacts").glob("it-*-evaluation-*.json"):
        shutil.copy2(report, APP / "artifacts" / report.name)
    for report in (ROOT / "artifacts").glob("it-*-deployment-test-*.json"):
        shutil.copy2(report, APP / "artifacts" / report.name)
    return model


def create_runtime_overlay() -> None:
    for directory in ("artifacts", "control", "data", "identity", "jupyter", "logs", "private"):
        (RUNTIME / directory).mkdir(parents=True, exist_ok=True)
    create_log_files(RUNTIME / "logs")
    create_state_layout(RUNTIME, cloud=True)
    create_runtime_data(RUNTIME, "darwinbox/employee_directory.json", "private/teams_ticket_mail.sqlite3")
    # Keep the explicitly configured /tmp/.../data path ready as well.
    configured_directory = RUNTIME / "data/employee_directory.json"
    shutil.copy2(RUNTIME / "darwinbox/employee_directory.json", configured_directory)


def manifest(model: Path) -> None:
    hashes = {}
    for path in sorted(TARGET.rglob("*")):
        if path.is_file() and not any(part == ".env" or part.startswith(".env.") for part in path.relative_to(TARGET).parts):
            hashes[path.relative_to(TARGET).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    (TARGET / "UPLOAD-MANIFEST.json").write_text(json.dumps({
        "app_target": "/app",
        "runtime_target": "/tmp/employeeassist",
        "release_version": VERSION,
        "model": model.name,
        "excluded": [
            "environment files", "real employee-directory contents",
            "runtime identities and private data",
        ],
        "unhashed_local_files": ["/app/.env when supplied by the deployment team"],
        "generated_placeholders": [
            "/app/data/employee_directory.json",
            "/app/artifacts/private/teams_ticket_mail.sqlite3",
            "/tmp/employeeassist/darwinbox/employee_directory.json",
            "/tmp/employeeassist/private/teams_ticket_mail.sqlite3",
            "/app/artifacts/identity/microsoft_identities.json",
            "/app/artifacts/private/darwinbox_employees.json",
            "/tmp/employeeassist/identity/microsoft_identities.json",
            "/tmp/employeeassist/darwinbox/employees.json",
        ],
        "sha256": hashes,
    }, indent=2) + "\n")


def main() -> None:
    RELEASES.mkdir(parents=True, exist_ok=True)
    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    model = copy_source()
    create_runtime_overlay()
    manifest(model)
    print(json.dumps({
        "app_overlay": str(APP),
        "runtime_overlay": str(RUNTIME),
        "model": model.name,
        "app_files": sum(path.is_file() for path in APP.rglob("*")),
        "runtime_files": sum(path.is_file() for path in RUNTIME.rglob("*")),
    }, indent=2))


if __name__ == "__main__":
    main()
