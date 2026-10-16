"""Create writable runtime files and restore cloud primaries from project backups."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sqlite3

from actions.runtime_paths import PROJECT_ROOT, runtime_log_paths, runtime_mirror_paths, runtime_root

LOG_NAMES = ("actions", "rasa", "web", "bot", "darwinbox", "deployment", "jupyter",
             "darwinbox-sync", "employee-report-sync", "ticket_mail")
DIRECTORY_DEFAULT = PROJECT_ROOT / "data/employee_directory.json"
TICKET_DB_DEFAULT = PROJECT_ROOT / "artifacts/private/teams_ticket_mail.sqlite3"
CONVERSATION_DEFAULT = PROJECT_ROOT / "artifacts/logs/conversations.jsonl"
IDENTITY_DEFAULT = PROJECT_ROOT / "artifacts/identity/microsoft_identities.json"
EMPLOYEE_STORE_DEFAULT = PROJECT_ROOT / "artifacts/private/darwinbox_employees.json"
SKIP_SUFFIXES = (".lock", ".pid", ".sock", ".sqlite3", ".sqlite3-wal", ".sqlite3-shm")


def employee_directory_paths() -> tuple[Path, ...]:
    return runtime_mirror_paths(
        "darwinbox/employee_directory.json", DIRECTORY_DEFAULT,
        "DARWINBOX_EMPLOYEE_MAP_FILE", PROJECT_ROOT,
    )


def ticket_database_paths(project_root: Path | None = None) -> tuple[Path, ...]:
    project_root = project_root or PROJECT_ROOT
    return runtime_mirror_paths(
        "private/teams_ticket_mail.sqlite3", project_root/"artifacts/private/teams_ticket_mail.sqlite3",
        "TEAMS_TICKET_DB", project_root,
    )


def identity_paths() -> tuple[Path, ...]:
    return runtime_mirror_paths(
        "identity/microsoft_identities.json", IDENTITY_DEFAULT,
        "IDENTITY_STORE_PATH", PROJECT_ROOT,
    )


def employee_store_paths() -> tuple[Path, ...]:
    return runtime_mirror_paths(
        "darwinbox/employees.json", EMPLOYEE_STORE_DEFAULT,
        "DARWINBOX_EMPLOYEE_STORE_PATH", PROJECT_ROOT,
    )


def mirror_file(primary: Path, backup: Path) -> None:
    backup.parent.mkdir(parents=True, exist_ok=True)
    temporary = backup.with_suffix(backup.suffix + f".{os.getpid()}.tmp")
    try:
        shutil.copy2(primary, temporary)
        os.chmod(temporary, 0o600)
        os.replace(temporary, backup)
    finally:
        temporary.unlink(missing_ok=True)


def _copy_tree(source: Path, target: Path, *, overwrite: bool) -> None:
    if not source.is_dir():
        return
    target.mkdir(parents=True, exist_ok=True)
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        if "runtime" in relative.parts or path.is_symlink():
            continue
        destination = target / relative
        if path.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        elif not path.name.endswith(SKIP_SUFFIXES) and (overwrite or not destination.exists()):
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)


def sync_runtime_backups() -> None:
    """Snapshot durable cloud state; never copy process-control/runtime files."""
    root = runtime_root()
    if root is None:
        return
    _copy_tree(root / "identity", PROJECT_ROOT / "artifacts/identity", overwrite=True)
    _copy_tree(root / "jupyter", PROJECT_ROOT / "artifacts/jupyter", overwrite=True)
    _copy_tree(root / "private", PROJECT_ROOT / "artifacts/private", overwrite=True)


def _restore_or_create(primary: Path, backup: Path | None, content: bytes) -> None:
    primary.parent.mkdir(parents=True, exist_ok=True)
    created = False
    if not primary.exists():
        if backup is not None and backup.is_file():
            shutil.copy2(backup, primary)
        else:
            primary.write_bytes(content)
        created = True
    if created:
        os.chmod(primary, 0o600)
    if backup is not None:
        backup.parent.mkdir(parents=True, exist_ok=True)
        if not backup.exists():
            shutil.copy2(primary, backup)
            os.chmod(backup, 0o600)


def _initialize_ticket_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE IF NOT EXISTS drafts (id TEXT PRIMARY KEY, owner TEXT, subject TEXT, body TEXT, status TEXT, created REAL)")
        columns = {row[1] for row in db.execute("PRAGMA table_info(drafts)")}
        if "to_list" not in columns:
            db.execute("ALTER TABLE drafts ADD COLUMN to_list TEXT NOT NULL DEFAULT 'helpdesk@emergys.com'")
        if "cc_list" not in columns:
            db.execute("ALTER TABLE drafts ADD COLUMN cc_list TEXT NOT NULL DEFAULT ''")
        db.execute("CREATE TABLE IF NOT EXISTS pending_auth (owner TEXT PRIMARY KEY, draft_id TEXT, connection TEXT, exchange_id TEXT, expires REAL, status TEXT)")
    os.chmod(path, 0o600)


def backup_sqlite(primary: Path, backup: Path) -> None:
    backup.parent.mkdir(parents=True, exist_ok=True)
    temporary = backup.with_suffix(backup.suffix + f".{os.getpid()}.tmp")
    try:
        with sqlite3.connect(primary) as source, sqlite3.connect(temporary) as destination:
            source.backup(destination)
        os.chmod(temporary, 0o600)
        os.replace(temporary, backup)
    finally:
        temporary.unlink(missing_ok=True)


def bootstrap_runtime_files() -> None:
    root = runtime_root()
    if root is not None:
        for name in ("control", "identity", "jupyter", "logs", "private"):
            (root / name).mkdir(parents=True, exist_ok=True)
            (PROJECT_ROOT / "artifacts" / name).mkdir(parents=True, exist_ok=True)
        _copy_tree(PROJECT_ROOT / "artifacts/identity", root / "identity", overwrite=False)
        _copy_tree(PROJECT_ROOT / "artifacts/jupyter", root / "jupyter", overwrite=False)
        _copy_tree(PROJECT_ROOT / "artifacts/private", root / "private", overwrite=False)

    directory_paths = employee_directory_paths()
    empty_directory = json.dumps({"guest": {
        "code": "EII0769", "darwinbox_code": "Ell0769", "name": "Guest",
        "email": "guest", "company": "Emergys Solutions Private Limited",
    }}, indent=2).encode() + b"\n"
    _restore_or_create(directory_paths[0], directory_paths[1] if len(directory_paths) > 1 else None, empty_directory)

    database_paths = ticket_database_paths()
    _restore_or_create(database_paths[0], database_paths[1] if len(database_paths) > 1 else None, b"")
    _initialize_ticket_database(database_paths[0])
    if len(database_paths) > 1:
        backup_sqlite(database_paths[0], database_paths[1])

    empty_identities = json.dumps({"version": 1, "identities": {}}, indent=2).encode() + b"\n"
    paths = identity_paths()
    _restore_or_create(paths[0], paths[1] if len(paths) > 1 else None, empty_identities)

    empty_employees = json.dumps({
        "version": 1, "synced_at": None, "incremental_cursor": None,
        "employees": {}, "identity_bindings": {}, "pending_identity": {},
    }, indent=2).encode() + b"\n"
    paths = employee_store_paths()
    _restore_or_create(paths[0], paths[1] if len(paths) > 1 else None, empty_employees)

    for name in LOG_NAMES:
        paths = runtime_log_paths(f"logs/{name}.log", PROJECT_ROOT/f"artifacts/logs/{name}.log")
        _restore_or_create(paths[0], paths[1] if len(paths) > 1 else None, b"")
    paths = runtime_log_paths("logs/conversations.jsonl", CONVERSATION_DEFAULT, "CONVERSATION_LOG_PATH", PROJECT_ROOT)
    _restore_or_create(paths[0], paths[1] if len(paths) > 1 else None, b"")
