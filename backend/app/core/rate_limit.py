"""Global API rate limiting (spec §33 / §54: "rate limiting").

A lightweight in-process sliding-window limiter applied as ASGI
middleware.  Per-identity (user id from the token when present, else
client IP) with per-path-class budgets:

- auth endpoints:  20 req / min   (brute-force resistance)
- default API:    240 req / min   (interactive clients)
- exports/bulk:    12 req / min   (expensive operations)

The limiter is deliberately simple; a multi-replica deployment should
back this with Redis (swap ``_Window.store``).  Exceeding a budget
returns 429 with ``Retry-After``.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

# --- Budgets: (window_seconds, max_requests) --------------------------------
# Defaults are overridable via env (RATE_LIMIT_AUTH_PER_MIN, etc.) so
# deployments and test rigs can tune them without code changes. The auth
# floor stays meaningful for brute-force resistance while tolerating
# legitimate bursts behind a shared NAT (offices, CI runners, e2e suites,
# which log in once per test from one IP).
import os


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, "")))
    except (TypeError, ValueError):
        return default


_RULES: list[tuple[str, tuple[int, int]]] = [
    ("/api/v1/auth/", (60, _env_int("RATE_LIMIT_AUTH_PER_MIN", 60))),
    # Unauthenticated public compliance surfaces (DMCA intake, one-click
    # email opt-out): tight budgets so they can't be used to spam the
    # designated agent or hammer the preferences table.
    (
        "/api/v1/legal/dmca/",
        (60, _env_int("RATE_LIMIT_DMCA_PER_MIN", 5)),
    ),
    (
        "/api/v1/email/opt-out",
        (60, _env_int("RATE_LIMIT_OPTOUT_PER_MIN", 10)),
    ),
    ("/api/v1/bulk/", (60, _env_int("RATE_LIMIT_BULK_PER_MIN", 12))),
    (
        "/api/v1/analytics/export",
        (60, _env_int("RATE_LIMIT_EXPORT_PER_MIN", 12)),
    ),
    (
        "/api/v1/repository/download",
        (60, _env_int("RATE_LIMIT_DOWNLOAD_PER_MIN", 30)),
    ),
    # Guest (token-based) verification & counter-signing surface. These
    # routes sit outside /api/v1 and carry no Authorization header, so the
    # identity is the client IP — keep brute-force attempts on
    # /review/{token}/verify-id*, accept/reject/sign tight (spec 3.20).
    (
        "/review/",
        (60, _env_int("RATE_LIMIT_REVIEW_PER_MIN", 30)),
    ),
]
_DEFAULT_RULE = (60, _env_int("RATE_LIMIT_DEFAULT_PER_MIN", 240))

# Paths exempt from throttling (health probes, metrics scraping).
_EXEMPT_PREFIXES = ("/health", "/api/v1/health", "/metrics", "/api/v1/metrics/prometheus")


class _Window:
    """Process-local sliding windows keyed by (identity, path_class)."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def hit(self, key: str, window: int, limit: int) -> tuple[bool, int]:
        """Record a hit; return (allowed, retry_after_seconds)."""
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            retry_after = max(1, int(window - (now - q[0])) + 1)
            return False, retry_after
        q.append(now)
        return True, 0


def _identity(request: Request) -> str:
    """Prefer the authenticated user; fall back to client IP."""
    # The auth middleware later resolves the full user; here we only need a
    # stable identity. The Authorization token itself is high-entropy and
    # stable per-session — but hashing avoids storing raw credentials.
    auth = request.headers.get("authorization", "")
    if auth:
        import hashlib

        return "t:" + hashlib.sha256(auth.encode()).hexdigest()[:16]
    client = request.client.host if request.client else "unknown"
    return "ip:" + client


def _rule_for(path: str) -> tuple[str, tuple[int, int]]:
    for prefix, rule in _RULES:
        if path.startswith(prefix):
            return prefix, rule
    return "default", _DEFAULT_RULE


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, enabled: bool = True) -> None:
        super().__init__(app)
        self.enabled = enabled
        self._window = _Window()

    async def dispatch(self, request: Request, call_next):
        if not self.enabled:
            return await call_next(request)

        path = request.url.path
        if any(path.startswith(p) for p in _EXEMPT_PREFIXES):
            return await call_next(request)

        path_class, (window, limit) = _rule_for(path)
        key = f"{_identity(request)}:{path_class}"
        allowed, retry_after = self._window.hit(key, window, limit)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests; slow down"},
                headers={"Retry-After": str(retry_after)},
            )
        return await call_next(request)
