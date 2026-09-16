"""Distributed tracing for the platform (spec 23 / 1.23).

Every request is wrapped in an OpenTelemetry span carrying the required
attributes — service, operation, duration, status, trace_id (spec 23,
"not full legal documents"). The span payload deliberately NEVER contains
legal-document contents: only routing / timing / outcome metadata.

Design constraints:
- Imports are lazy. If the OpenTelemetry SDK is not installed the tracer
  resolves to OpenTelemetry's no-op tracer and this module degrades to a
  light in-band header tracer (X-Trace-Id) so correlation still works.
- Spans are tied to the existing correlation ID so trace_id and the
  X-Request-ID consumed by log greps agree.
- Instrumentation is opt-in via settings.tracing_enabled; exporters are
  configured through standard OTLP environment / settings.
"""

from __future__ import annotations

import logging
import time

from app.core.config import get_settings_lazy

logger = logging.getLogger(__name__)
settings = get_settings_lazy()

_TRACER = None
_INSTRUMENTED = False


def _ensure_otel():
    """Lazily install the OpenTelemetry tracer provider (idempotent).

    Returns the tracer; the SDK being absent yields OpenTelemetry's no-op
    tracer (all ``start_as_current_span`` calls are free), which keeps every
    call site working even on a minimal install.
    """
    global _TRACER, _INSTRUMENTED
    if _INSTRUMENTED:
        return _TRACER

    _INSTRUMENTED = True
    if not settings.tracing_enabled:
        logger.debug("Tracing disabled; using no-op tracer")
        return None

    try:
        from opentelemetry import trace as otel_trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider

        resource = Resource.create({"service.name": settings.tracing_service_name})
        provider = TracerProvider(resource=resource)

        if settings.tracing_otlp_endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                    OTLPSpanExporter,
                )
                from opentelemetry.sdk.trace.export import BatchSpanProcessor

                exporter = OTLPSpanExporter(endpoint=settings.tracing_otlp_endpoint)
                provider.add_span_processor(BatchSpanProcessor(exporter))
            except Exception as e:
                logger.warning("OTLP exporter unavailable: %s", e)

        otel_trace.set_tracer_provider(provider)
        _TRACER = otel_trace.get_tracer("contractos")
        logger.info("OpenTelemetry tracing enabled for %s", settings.tracing_service_name)
        return _TRACER
    except Exception as e:
        logger.warning(
            "OpenTelemetry unavailable (%s); falling back to no-op tracer", e
        )
        return None


def get_tracer():
    """Return the process tracer (no-op when tracing is off)."""
    return _ensure_otel()


# --- Internal helpers -------------------------------------------------------

_OPERATION_METHOD = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}


def _operation_name(request) -> str:
    """``METHOD /path`` — used as the OTel span name/operation attribute."""
    method = request.method if request.method in _OPERATION_METHOD else "HTTP"
    return f"{method} {request.url.path}"


def _new_trace_id() -> str:
    import uuid

    return uuid.uuid4().hex[:32]


def _span_trace_id(span) -> str:
    """Hex 128-bit trace id for a live span, empty string if unavailable."""
    try:
        return f"{span.get_span_context().trace_id:032x}"
    except Exception:
        return ""


def _status_from_code(code: int):
    from opentelemetry import trace as otel_trace

    if 200 <= code < 400:
        return otel_trace.Status(otel_trace.StatusCode.OK)
    if 400 <= code < 500:
        return otel_trace.Status(otel_trace.StatusCode.UNSET)
    return otel_trace.Status(otel_trace.StatusCode.ERROR)


# --- FastAPI middleware factory ---------------------------------------------

def make_tracing_middleware():
    """Build the tracing middleware for registration in ``main.py``.

    Returns an ASGI middleware callable whose behaviour is decided once at
    startup so per-request hot-path work is minimal. When tracing is
    disabled the middleware still stamps every response with X-Trace-Id
    for correlation, preserving the contract with callers.
    """
    from app.core.resilience import CORRELATION_HEADER

    async def dispatch(request, call_next):
        tracer = _ensure_otel()
        cid = request.headers.get(CORRELATION_HEADER) or _new_trace_id()

        if tracer is None:
            response = await call_next(request)
            response.headers["X-Trace-Id"] = cid
            return response

        import opentelemetry as otel

        operation = _operation_name(request)
        with tracer.start_as_current_span(
            name=operation,
            kind=otel.trace.SpanKind.SERVER,
            attributes={
                "service": settings.tracing_service_name,
                "operation": operation,
                "http.method": request.method,
                "http.route": request.url.path,
                CORRELATION_HEADER: cid,
            },
        ) as span:
            start = time.monotonic()
            try:
                response = await call_next(request)
            except Exception:
                span.set_attribute("duration_ms", round((time.monotonic() - start) * 1000, 2))
                span.set_attribute("http.status_code", 500)
                span.set_status(otel.trace.Status(otel.trace.StatusCode.ERROR))
                raise
            duration_ms = round((time.monotonic() - start) * 1000, 2)
            span.set_attribute("duration_ms", duration_ms)
            span.set_attribute("http.status_code", response.status_code)
            span.set_status(_status_from_code(response.status_code))
            response.headers["X-Trace-Id"] = _span_trace_id(span)
            return response

    return dispatch