"""Shared authenticated Darwinbox HTTP client."""
from __future__ import annotations

import hashlib
import os
import time
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx


class DarwinboxConfigurationError(RuntimeError):
    pass


class DarwinboxAPIError(RuntimeError):
    pass


def required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise DarwinboxConfigurationError(f"{name} is not configured")
    return value


class DarwinboxClient:
    """Build signed requests for independently configured Darwinbox modules."""

    def __init__(self, timeout: float | None = None):
        self.timeout = timeout or float(os.getenv("DARWINBOX_TIMEOUT_SECONDS", "15"))

    def endpoint(self, *, url_env: str, path_env: str, default_path: str) -> str:
        explicit = os.getenv(url_env, "").strip()
        if explicit:
            return explicit
        base = os.getenv("DARWINBOX_BASE_URL", "").strip()
        if not base:
            legacy = (
                os.getenv("DARWINBOX_EMPLOYEE_URL", "").strip()
                or os.getenv("DARWINBOX_LEAVE_BALANCE_URL", "").strip()
            )
            parsed = urlsplit(legacy)
            if parsed.scheme and parsed.netloc:
                base = f"{parsed.scheme}://{parsed.netloc}"
        if not base:
            raise DarwinboxConfigurationError("DARWINBOX_BASE_URL is not configured")
        base = base.rstrip("/") + "/"
        path = os.getenv(path_env, default_path).strip() or default_path
        return urljoin(base, path.lstrip("/"))

    def auth_payload(self, *, dataset_key: str | None = None) -> dict[str, Any]:
        email = required("DARWINBOX_ADMIN_EMAIL")
        secret = required("DARWINBOX_SECRET_KEY")
        timestamp = str(int(time.time()))
        payload: dict[str, Any] = {
            "Uid": required("DARWINBOX_UID"),
            "timestamp": timestamp,
            "hash": hashlib.sha512(
                f"{email}{secret}{timestamp}".encode("utf-8")
            ).hexdigest(),
        }
        if dataset_key:
            payload["datasetKey"] = dataset_key
        return payload

    async def request(
        self,
        *,
        url: str,
        method: str = "POST",
        payload: dict[str, Any] | None = None,
    ) -> Any:
        method = method.upper().strip()
        if method not in {"GET", "POST"}:
            raise DarwinboxConfigurationError("Darwinbox method must be POST or GET")
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as http:
                response = await http.request(
                    method,
                    url,
                    json=payload if method == "POST" else None,
                    params=payload if method == "GET" else None,
                )
                response.raise_for_status()
                result = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise DarwinboxAPIError(
                "Darwinbox is unavailable or returned an invalid response"
            ) from exc
        if isinstance(result, dict):
            status = result.get("status")
            if status is not None and str(status).casefold() not in {
                "1", "true", "success", "successful", "200"
            }:
                raise DarwinboxAPIError(
                    str(result.get("message") or "Darwinbox rejected the request")
                )
        return result
