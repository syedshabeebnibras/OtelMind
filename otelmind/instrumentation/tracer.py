"""OpenTelemetry tracer initialization for OtelMind."""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SpanExporter,
)

from otelmind.config import settings

_provider: TracerProvider | None = None


def init_tracer(
    *,
    exporter: SpanExporter | None = None,
    service_name: str | None = None,
) -> TracerProvider:
    """Initialise the global OpenTelemetry TracerProvider.

    Parameters
    ----------
    exporter:
        Custom span exporter.  Falls back to console exporter if not given.
    service_name:
        Override the service name from settings.

    Returns
    -------
    The configured TracerProvider.
    """
    global _provider

    resource = Resource.create({"service.name": service_name or settings.otel_service_name})
    _provider = TracerProvider(resource=resource)

    span_exporter = exporter or ConsoleSpanExporter()
    _provider.add_span_processor(BatchSpanProcessor(span_exporter))

    trace.set_tracer_provider(_provider)
    return _provider


def get_tracer(name: str = "otelmind") -> trace.Tracer:
    """Return a tracer from the current provider."""
    provider = trace.get_tracer_provider()
    return provider.get_tracer(name)


def shutdown_tracer() -> None:
    """Gracefully shut down the global tracer provider."""
    global _provider
    if _provider is not None:
        _provider.shutdown()
        _provider = None
