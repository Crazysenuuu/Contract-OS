"""Generic read-only REST/HTTP connector (spec 3.15.43 read-only by default).

A real transport connector that drives JSON APIs. It is provider-agnostic —
a workspace configures ``base_url`` + bearer credential + resource path and
the monitoring query selects fields/filters. No provider-specific mock or
fake source lives in production code (3.15.11).

Data minimization (3.15.44): the query may declare ``fields`` and
``filters``; the connector requests only those.

Fails closed: transport/timeout/5xx → ``ConnectorUnavailable``; 401/403 →
``ConnectorAuthError``. Neither ever fabricates an observation.
"""

from __future__ import annotations

import asyncio
import email.utils
import time
from datetime import timezone
from typing import Any

import httpx

from app.monitoring.connectors.base import Connector, ExternalObservation
from app.monitoring.credentials import credential_header_token
from app.monitoring.exceptions import ConnectorAuthError, ConnectorUnavailable
from app.monitoring.observations import ensure_aware_utc

# Cap how long a provider's Retry-After may stall the worker. A hostile or
# misconfigured provider must not be able to freeze the sweep indefinitely.
_RATE_LIMIT_MAX_WAIT = 5.0
_RATE_LIMIT_DEFAULT_WAIT = 1.0


def _parse_retry_after(raw: str | None, now: float | None = None) -> float:
    """Seconds to wait from ``Retry-After`` (delta-seconds or RFC 7231 date)."""
    if not raw:
        return _RATE_LIMIT_DEFAULT_WAIT
    raw = raw.strip()
    try:
        return float(raw)
    except ValueError:
        pass
    try:
        parsed = email.utils.parsedate_to_datetime(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        now = now if now is not None else time.time()
        return max(0.0, parsed.timestamp() - now)
    except Exception:  # noqa: BLE001 - unparseable header falls back to default
        return _RATE_LIMIT_DEFAULT_WAIT


class HTTPRESTConnector(Connector):
    """Read-only JSON REST connector (GET on a configurable resource path)."""

    provider_key = "rest_api"

    def __init__(self, *, credentials, configuration: dict) -> None:
        super().__init__(credentials=credentials, configuration=configuration)
        base_url = (configuration.get("base_url") or "").rstrip("/")
        if not base_url:
            raise ConnectorUnavailable("rest_api connector requires configuration.base_url")
        self.base_url = base_url
        self.timeout = float(configuration.get("timeout_seconds") or 15.0)
        self.max_retries = int(configuration.get("max_retries") or 2)
        self._headers = dict(configuration.get("headers") or {})
        self._token = None
        if configuration.get("auth") in (None, "bearer") or configuration.get("bearer"):
            try:
                self._token = credential_header_token(credentials)
            except Exception:
                # A connector without bearer credentials is created; auth
                # failures surface at fetch/validate time as auth errors.
                self._token = None

    def _authorized_headers(self) -> dict:
        headers = dict(self._headers)
        if self._token:
            headers.setdefault("Authorization", f"Bearer {self._token}")
        return headers

    async def _request(self, method: str, url: str, *, params: dict | None = None) -> httpx.Response:
        last_exc: Exception | None = None
        attempt = 0
        while attempt <= self.max_retries:
            attempt += 1
            started = time.monotonic()
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.request(
                        method,
                        url,
                        params=params or {},
                        headers=self._authorized_headers(),
                    )
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt > self.max_retries:
                    raise ConnectorUnavailable(f"REST source unreachable: {exc}") from exc
                continue
            if response.status_code in (401, 403):
                raise ConnectorAuthError(
                    f"REST source rejected credentials (HTTP {response.status_code})"
                )
            if response.status_code == 429:
                # Rate-limit handling (spec 3.15.62): honor Retry-After and
                # retry within the retry budget; expiring the budget fails
                # closed as ConnectorUnavailable (INCONCLUSIVE upstream) —
                # a rate-limited source is not silently treated as healthy.
                last_exc = ConnectorUnavailable("REST source rate limited (HTTP 429)")
                if attempt > self.max_retries:
                    raise last_exc
                wait = min(
                    _parse_retry_after(response.headers.get("retry-after")),
                    _RATE_LIMIT_MAX_WAIT,
                )
                await asyncio.sleep(wait)
                continue
            if response.status_code == 404:
                return response
            if response.status_code >= 500:
                last_exc = ConnectorUnavailable(
                    f"REST source returned HTTP {response.status_code} ({time.monotonic() - started:.2f}s)"
                )
                if attempt > self.max_retries:
                    raise last_exc
                continue
            if response.status_code >= 400:
                raise ConnectorUnavailable(
                    f"REST source returned HTTP {response.status_code}"
                )
            return response
        raise last_exc  # type: ignore[misc]

    async def validate_connection(self) -> bool:
        try:
            probe = self.configuration.get("health_path") or self.configuration.get("path") or ""
            url = f"{self.base_url}/{probe.lstrip('/')}"
            response = await self._request("GET", url)
            return response.status_code < 400
        except (ConnectorAuthError, ConnectorUnavailable):
            return False

    async def fetch(self, query: dict) -> list[ExternalObservation]:
        resource = (query.get("resource") or "").strip()
        if not resource:
            raise ConnectorUnavailable("monitoring query missing 'resource'")

        fields = query.get("fields") or []
        filters = query.get("filters") or []
        limit = query.get("limit")

        params: dict[str, Any] = {}
        select_field = (query.get("contributes_to") or "resource")
        acceptable = {"contributes_to"}
        for extra in acceptable:
            if extra in query:
                params.pop(extra, None)

        for field in fields:
            if isinstance(field, str):
                params.setdefault("fields", []).append(field)
        for flt in filters:
            f_field = flt.get("field")
            f_operator = flt.get("operator")
            f_value = flt.get("value")
            if f_field and f_operator == "equals":
                params[f_field] = str(f_value)
        if limit:
            params.setdefault("limit", limit)

        path = query.get("path") or resource
        url = f"{self.base_url}/{path.lstrip('/') if path else ''}"
        response = await self._request("GET", url, params=params)
        if response.status_code == 404:
            return []

        try:
            data = response.json()
        except ValueError as exc:
            raise ConnectorUnavailable("REST source returned non-JSON payload") from exc

        items = _extract_items(data, select_field)
        id_field = self.configuration.get("id_field") or "id"
        observed_field = self.configuration.get("observed_at_field") or "observed_at"
        results: list[ExternalObservation] = []
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            external_id = str(item.get(id_field) or f"{resource}:{idx}")
            results.append(
                ExternalObservation(
                    external_id=external_id,
                    observed_at=ensure_aware_utc(item.get(observed_field)),
                    resource_type=resource,
                    payload=item,
                    source_reference={
                        "provider_key": self.provider_key,
                        "resource": resource,
                        "external_id": external_id,
                    },
                )
            )
        return results

    async def close(self) -> None:
        return None


def _extract_items(data: Any, select_field: str) -> list[Any]:
    """Handle the common envelope shapes: list, {items: []}, {data: []}."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("items", "data", "results", select_field):
            value = data.get(key)
            if isinstance(value, list):
                return value
        return [data]
    return []