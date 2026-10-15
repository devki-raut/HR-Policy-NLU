"""Durable mapping of verified Microsoft identities for future HRIS lookup."""
from __future__ import annotations

import fcntl
import json
import os
import logging
from actions.runtime_paths import runtime_path
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]


def _path() -> Path:
    return runtime_path('identity/microsoft_identities.json', ROOT/'artifacts/identity/microsoft_identities.json', 'IDENTITY_STORE_PATH', ROOT)


def save_verified_identity(identity: Mapping[str, Any], source: str) -> dict[str, Any]:
    """Upsert a verified Entra/Teams identity without storing access tokens."""
    object_id = str(identity.get("aad_object_id") or identity.get("oid") or "").strip()
    tenant_id = str(identity.get("tenant_id") or identity.get("tid") or "").strip()
    if not object_id or not tenant_id:
        return {}
    record = {
        "aad_object_id": object_id,
        "tenant_id": tenant_id,
        "email": identity.get("email") or identity.get("preferred_username"),
        "user_principal_name": identity.get("user_principal_name") or identity.get("preferred_username"),
        "display_name": identity.get("display_name") or identity.get("name"),
        "teams_user_id": identity.get("teams_user_id"),
        "source": source,
        "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    path = _path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handle.seek(0)
            try:
                payload = json.load(handle)
            except (json.JSONDecodeError, ValueError):
                payload = {"version": 1, "identities": {}}
            payload.setdefault("identities", {})[f"{tenant_id}:{object_id}"] = record
            handle.seek(0)
            handle.truncate()
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        # Persistence is independent of the cryptographically verified identity.
        logging.getLogger(__name__).warning('Verified identity could not be persisted; continuing authenticated request.')
    return record
