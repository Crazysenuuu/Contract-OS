from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.api.v1.external_party import router as external_party_router
from app.api.v1.websocket import router as websocket_router
from app.core.config import get_settings_lazy
from app.services.session_heartbeat import track_last_seen

settings = get_settings_lazy()

app = FastAPI(
    title=settings.app_name,
    description="Contract Lifecycle Management Platform",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
async def session_heartbeat_middleware(request, call_next):
    response = await call_next(request)
    await track_last_seen(request.headers.get("authorization"))
    return response


@app.get("/health")
async def health_check():
    return {"status": "ok"}
