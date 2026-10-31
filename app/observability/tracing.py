"""Small OpenTelemetry adapter with a no-op default.

The application records operation boundaries and safe dimensions only.  Full
prompts, tool arguments, API keys and document contents are intentionally not
accepted as span attributes.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

try:  # pragma: no cover - the fallback is exercised when optional deps are absent
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor,
        ConsoleSpanExporter,
        SimpleSpanProcessor,
    )
    from opentelemetry.trace import Status, StatusCode

    _OTEL_AVAILABLE = True
except ImportError:  # pragma: no cover
    trace = None  # type: ignore[assignment]
    Resource = TracerProvider = BatchSpanProcessor = ConsoleSpanExporter = SimpleSpanProcessor = None  # type: ignore[assignment]
    Status = StatusCode = None  # type: ignore[assignment]
    _OTEL_AVAILABLE = False

try:  # pragma: no cover - the exporter is optional at runtime
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
except ImportError:  # pragma: no cover
    OTLPSpanExporter = None  # type: ignore[assignment,misc]

_configured = False


def _enabled() -> bool:
    return os.getenv("OTEL_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def _parse_headers(raw: str) -> dict[str, str]:
    """Parse ``key=value,key2=value2`` without logging header values."""

    headers: dict[str, str] = {}
    for item in raw.split(","):
        key, separator, value = item.partition("=")
        if separator and key.strip():
            headers[key.strip()] = value.strip()
    return headers


def configure_tracing() -> None:
    """Install a provider only when explicitly enabled by the deployment."""

    global _configured
    if _configured or not _OTEL_AVAILABLE or not _enabled():
        return
    resource = Resource.create(
        {"service.name": os.getenv("OTEL_SERVICE_NAME", "enterprise-data-knowledge-workbench")}
    )
    provider = TracerProvider(resource=resource)
    if os.getenv("OTEL_CONSOLE_EXPORT", "false").strip().lower() in {"1", "true", "yes", "on"}:
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if otlp_endpoint and BatchSpanProcessor is not None and OTLPSpanExporter is not None:
        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(
                    endpoint=otlp_endpoint,
                    headers=_parse_headers(os.getenv("OTEL_EXPORTER_OTLP_HEADERS", "")),
                )
            )
        )
    trace.set_tracer_provider(provider)
    _configured = True


def tracer(name: str = "enterprise-data-knowledge-workbench") -> Any:
    if not _OTEL_AVAILABLE or trace is None:
        return None
    return trace.get_tracer(name)


@asynccontextmanager
async def span(name: str, attributes: dict[str, Any] | None = None) -> AsyncIterator[Any]:
    """Yield a span or ``None`` while preserving application behavior."""

    configure_tracing()
    current_tracer = tracer()
    if current_tracer is None:
        yield None
        return
    with current_tracer.start_as_current_span(name) as current_span:
        for key, value in (attributes or {}).items():
            if value is not None and isinstance(value, (str, int, float, bool)):
                current_span.set_attribute(key, value)
        try:
            yield current_span
        except Exception as exc:
            if Status is not None and StatusCode is not None:
                current_span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            raise


def set_span_status(current_span: Any, status: str, **attributes: Any) -> None:
    """Attach a bounded operational outcome without storing user content."""

    if current_span is None:
        return
    if status == "error" and Status is not None and StatusCode is not None:
        current_span.set_status(Status(StatusCode.ERROR))
    elif Status is not None and StatusCode is not None:
        current_span.set_status(Status(StatusCode.OK))
    for key, value in attributes.items():
        if value is not None and isinstance(value, (str, int, float, bool)):
            current_span.set_attribute(key, value)
