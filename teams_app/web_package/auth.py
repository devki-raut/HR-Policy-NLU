"""Microsoft Entra token validation for the EmployeeAssist web API."""
from __future__ import annotations

import os
import time
from typing import Any

import httpx
import jwt
from fastapi import HTTPException, Request

from actions.identity_store import save_verified_identity

_jwks: dict[str, Any] = {"expires": 0, "keys": []}


def client_id() -> str:
    return (os.getenv("ENTRA_CLIENT_ID") or os.getenv("MICROSOFT_APP_ID") or "").strip()


def tenant_id() -> str:
    return os.getenv("ENTRA_TENANT_ID", "organizations").strip() or "organizations"


def auth_required() -> bool:
    return os.getenv("ENTRA_AUTH_REQUIRED", "false").strip().casefold() in {"1", "true", "yes"}


async def _keys() -> list[dict[str, Any]]:
    if _jwks["keys"] and _jwks["expires"] > time.time():
        return _jwks["keys"]
    async with httpx.AsyncClient(timeout=15) as http:
        response = await http.get("https://login.microsoftonline.com/common/discovery/v2.0/keys")
        response.raise_for_status()
        keys = response.json().get("keys", [])
    _jwks.update(keys=keys, expires=time.time() + 3600)
    return keys


async def verify_microsoft_token(token: str) -> dict[str, Any]:
    app_id = client_id()
    if not app_id:
        raise HTTPException(503, "Microsoft authentication is not configured")
    try:
        header = jwt.get_unverified_header(token)
        key_data = next(item for item in await _keys() if item.get("kid") == header.get("kid"))
        claims = jwt.decode(
            token,
            jwt.PyJWK.from_dict(key_data).key,
            algorithms=["RS256"],
            audience=app_id,
            options={"verify_iss": False},
        )
    except (StopIteration, httpx.HTTPError, jwt.PyJWTError, ValueError) as exc:
        raise HTTPException(401, "Invalid or expired Microsoft sign-in token") from exc
    tid = str(claims.get("tid") or "")
    oid = str(claims.get("oid") or claims.get("sub") or "")
    configured_tenant = tenant_id()
    if configured_tenant not in {"common", "organizations"} and tid != configured_tenant:
        raise HTTPException(403, "Microsoft account is outside the configured organization")
    if not tid or not oid:
        raise HTTPException(401, "Microsoft token does not contain a user identity")
    issuer = str(claims.get("iss") or "")
    if issuer not in {f"https://login.microsoftonline.com/{tid}/v2.0", f"https://sts.windows.net/{tid}/"}:
        raise HTTPException(401, "Microsoft token issuer is invalid")
    identity = {
        "aad_object_id": oid,
        "tenant_id": tid,
        "email": claims.get("email") or claims.get("preferred_username") or claims.get("upn"),
        "user_principal_name": claims.get("upn") or claims.get("preferred_username"),
        "display_name": claims.get("name"),
    }
    return save_verified_identity(identity, "web_microsoft_token") or identity


async def request_identity(request: Request) -> dict[str, Any] | None:
    authorization = request.headers.get("authorization", "")
    if authorization.casefold().startswith("bearer "):
        return await verify_microsoft_token(authorization.split(None, 1)[1].strip())
    if auth_required():
        raise HTTPException(401, "Microsoft sign-in is required before chatting")
    return None
