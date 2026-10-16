"""Shared authenticated Darwinbox HTTP client."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from .diagnostics import record_darwinbox_event

LOGGER = logging.getLogger(__name__)


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
        body_mode: str = "json",
        basic_auth: tuple[str, str] | None = None,
        headers: dict[str, str] | None = None,
        operation: str = "darwinbox",
    ) -> Any:
        method = method.upper().strip()
        if method not in {"GET", "POST"}:
            raise DarwinboxConfigurationError("Darwinbox method must be POST or GET")
        started = time.monotonic()
        LOGGER.info("Darwinbox %s request started", operation)
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as http:
                request_kwargs: dict[str, Any] = {"headers": dict(headers or {})}
                if method == "GET":
                    request_kwargs["params"] = payload
                elif body_mode == "raw_json":
                    # Matches Darwinbox curl examples that use --data without a
                    # JSON content-type header.
                    request_kwargs["content"] = json.dumps(payload or {})
                    request_kwargs["headers"]["Content-Type"] = (
                        "application/x-www-form-urlencoded"
                    )
                elif body_mode == "json":
                    request_kwargs["json"] = payload
                else:
                    raise DarwinboxConfigurationError(
                        "Darwinbox body_mode must be json or raw_json"
                    )
                response = await http.request(
                    method, url, auth=basic_auth, **request_kwargs
                )
                response.raise_for_status()
                result = response.json()
        except httpx.HTTPStatusError as exc:
            LOGGER.warning("Darwinbox %s HTTP status=%s", operation, exc.response.status_code)
            record_darwinbox_event('actions', operation, 'http_error', http_status=exc.response.status_code)
            raise DarwinboxAPIError(
                "Darwinbox is unavailable or returned an invalid response"
            ) from exc
        except httpx.RequestError as exc:
            LOGGER.warning("Darwinbox %s connection error=%s", operation, type(exc).__name__)
            record_darwinbox_event('actions', operation, 'connection_error', exception_type=type(exc).__name__)
            raise DarwinboxAPIError(
                "Darwinbox is unavailable or returned an invalid response"
            ) from exc
        except ValueError as exc:
            LOGGER.warning("Darwinbox %s response is not valid JSON", operation)
            record_darwinbox_event('actions', operation, 'invalid_json')
            raise DarwinboxAPIError(
                "Darwinbox is unavailable or returned an invalid response"
            ) from exc
        if isinstance(result, dict):
            status = result.get("status")
            if status is not None and str(status).casefold() not in {
                "1", "true", "success", "successful", "200"
            }:
                LOGGER.warning("Darwinbox %s API rejected response", operation)
                record_darwinbox_event('actions', operation, 'api_rejected')
                raise DarwinboxAPIError(
                    str(result.get("message") or "Darwinbox rejected the request")
                )
        LOGGER.info("Darwinbox %s request completed in %d ms", operation, int((time.monotonic() - started) * 1000))
        record_darwinbox_event('actions', operation, 'api_success', duration_ms=int((time.monotonic() - started) * 1000))
        return result
