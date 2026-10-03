from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.api.v1.external_party import router as external_party_router
from app.api.v1.websocket import router as websocket_router
from app.core.config import get_settings_lazy
from app.services.session_heartbeat import track_last_seen

settings = get_settings_lazy()

# /docs and /redoc enumerate every route, schema and dependency. Useful in
# development and in contract tests; in production they are an invitation to
# map the attack surface, so they are opt-in via EXPOSE_API_DOCS.
_docs_enabled = settings.expose_api_docs

app = FastAPI(
    title=settings.app_name,
    description="Contract Lifecycle Management Platform",
    version="0.1.0",
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)

# Cross-origin access for the Next.js frontend. Origins are stripped and
# blanks dropped so a trailing comma in CORS_ORIGINS cannot inject an
# empty-string origin (which Starlette treats as a literal allowed origin,
# not a wildcard, but still matches nothing useful).
_cors_origins = [o.strip() for o in (settings.cors_origins or "").split(",") if o.strip()]

if _cors_origins:
    # allow_credentials=True makes browsers reject a wildcard origin, so the
    # wildcard is stripped here rather than passed through to fail at runtime.
    _credential_origins = [o for o in _cors_origins if o != "*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_credential_origins or _cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
elif settings.environment == "production":
    # No CORS_ORIGINS in production is almost always a misconfiguration: the
    # SPA and the API are served from different origins. Failing loudly here
    # beats a silently browser-blocked frontend.
    import warnings

    warnings.warn(
        "CORS_ORIGINS is empty in production; browser clients on another "
        "origin will be blocked by the same-origin policy.",
        RuntimeWarning,
        stacklevel=1,
    )

# Reject requests whose Host header is not one we serve. Empty
# ALLOWED_HOSTS disables the check (localhost development, where the host is
# whatever the developer typed); every internet-facing deployment must set it.
_allowed_hosts = [h.strip() for h in (settings.allowed_hosts or "").split(",") if h.strip()]
if _allowed_hosts:
    from starlette.middleware.trustedhost import TrustedHostMiddleware

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=_allowed_hosts)


from app.core.tracing import make_tracing_middleware  # noqa: E402  (after app init)

app.middleware("http")(make_tracing_middleware())

# Global API rate limiting (spec §33 / §54).
from app.core.rate_limit import RateLimitMiddleware  # noqa: E402

app.add_middleware(
    RateLimitMiddleware,
    # Disabled under automated test rigs: a Playwright suite logs in once per
    # test (with hydration retries) from a single IP and would exhaust the
    # per-IP auth budget within the first minute. Production/staging/development
    # keep full protection.
    enabled=settings.environment not in ("test", "testing"),
)


@app.middleware("http")
async def correlation_id_middleware(request, call_next):
    """Propagate correlation IDs (spec 1.23.17).

    Adopts an inbound X-Request-ID or mints one, binds it to the request
    context for structured logs, and echoes it on the response so clients
    and log greps can be correlated end to end.
    """
    from app.core.resilience import CORRELATION_HEADER, correlation_scope, ensure_correlation_id

    cid = ensure_correlation_id(request.headers.get(CORRELATION_HEADER))
    with correlation_scope(cid):
        response = await call_next(request)
    response.headers[CORRELATION_HEADER] = cid
    return response


@app.middleware("http")
async def security_headers_middleware(request, call_next):
    """Emit security headers on every response (spec 1.22)."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=(), interest-cohort=()"
    )
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )
    # HSTS only when served over TLS.
    if settings.environment in ("production", "staging"):
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
    # API responses must not be cached by shared caches.
    if request.url.path.startswith("/api/") or request.url.path.startswith("/ws/"):
        response.headers["Cache-Control"] = "no-store"
    return response

app.include_router(api_router)

# External party review endpoints (token-based, no /api/v1 prefix)
app.include_router(external_party_router)

# WebSocket real-time notifications (JWT via query param)
app.include_router(websocket_router)


@app.on_event("startup")
async def _validate_startup_configuration():
    """Refuse to serve traffic on a configuration that cannot work.

    A production deployment with a placeholder JWT secret, no SendGrid key
    or the mock e-signature provider boots fine and fails in the worst way:
    forged tokens, silently dropped mail, contracts nobody signed. Raising
    here turns all of those into an immediate, obvious crash on the first
    deploy instead of a slow production incident.
    """
    from app.core.startup_validation import validate_or_die

    validate_or_die()


@app.on_event("startup")
async def _start_ws_fanout():
    """Cross-process WebSocket fanout (spec 1.14.21-22).

    Subscribes this API process to Redis ``notify:*`` channels so pushes
    published by the outbox dispatcher (Celery worker) or by other API
    replicas reach the browsers connected to *this* process. No-op without
    Redis; a subscriber error reconnects with backoff.
    """
    from app.services.ws_fanout import start_fanout

    start_fanout()


@app.on_event("shutdown")
async def _stop_ws_fanout():
    from app.services.ws_fanout import stop_fanout

    await stop_fanout()


@app.middleware("http")
async def https_redirect_middleware(request, call_next):
    """Force HTTPS on anything not already behind a TLS-terminating proxy.

    When a load balancer terminates TLS the original scheme survives in
    ``X-Forwarded-Proto``; the hop count is compared against
    ``FORWARDED_HOP_COUNT`` (default 1) so a client cannot spoof the header
    and downgrade itself out of the redirect.
    """
    import os

    if settings.environment not in ("production", "staging"):
        return await call_next(request)

    try:
        hops = int(os.environ.get("FORWARDED_HOP_COUNT", "1"))
    except ValueError:
        hops = 1

    forwarded = request.headers.get("x-forwarded-proto", "")
    chain = [p.strip().lower() for p in forwarded.split(",") if p.strip()]
    client_reachable_scheme = chain[-hops] if len(chain) >= hops else (chain[0] if chain else request.url.scheme)

    if client_reachable_scheme != "https":
        from fastapi.responses import RedirectResponse

        target = request.url.replace(scheme="https")
        return RedirectResponse(str(target), status_code=301)

    return await call_next(request)


@app.middleware("http")
async def session_heartbeat_middleware(request, call_next):
    response = await call_next(request)
    await track_last_seen(request.headers.get("authorization"))
    return response


@app.get("/health")
async def health_check():
    return {"status": "ok"}
