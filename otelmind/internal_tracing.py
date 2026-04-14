"""Instrument OtelMind's own eval and multi-agent pipelines.

When a TracerProvider is configured (by the FastAPI lifespan's
`init_tracer()` call, or a custom one in tests), OtelMind emits OTel
spans for its own judge calls, batch scoring runs, calibrations, and
multi-agent rounds — using the same tracing pipeline it provides to
users. This is the "eat your own dog food" wiring.

When no provider is configured, the spans become no-ops (the OTel
default ProxyTracer), so importing this module is always safe.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

try:
    from opentelemetry import trace
except Exception:  # pragma: no cover — OTel optional at import time
    trace = None  # type: ignore[assignment]

_tracer: Any | None = None


def get_internal_tracer() -> Any:
    """Lazy singleton — uses the global TracerProvider if one was set."""
    global _tracer
    if trace is None:
        return None
    if _tracer is None:
        _tracer = trace.get_tracer("otelmind.internal", "0.1.0")
    return _tracer


@contextmanager
def trace_judge_call(dimension: str, model: str) -> Generator[Any, None, None]:
    tracer = get_internal_tracer()
    if tracer is None:
        yield None
        return
    with tracer.start_as_current_span("otelmind.judge.score_dimension") as span:
        span.set_attribute("otelmind.internal", True)
        span.set_attribute("otelmind.judge.dimension", dimension)
        span.set_attribute("otelmind.judge.model", model)
        yield span


@contextmanager
def trace_batch_scoring(total_cases: int, concurrency: int) -> Generator[Any, None, None]:
    tracer = get_internal_tracer()
    if tracer is None:
        yield None
        return
    with tracer.start_as_current_span("otelmind.eval.batch_score") as span:
        span.set_attribute("otelmind.internal", True)
        span.set_attribute("otelmind.eval.total_cases", total_cases)
        span.set_attribute("otelmind.eval.concurrency", concurrency)
        yield span


@contextmanager
def trace_calibration(case_count: int) -> Generator[Any, None, None]:
    tracer = get_internal_tracer()
    if tracer is None:
        yield None
        return
    with tracer.start_as_current_span("otelmind.eval.calibration") as span:
        span.set_attribute("otelmind.internal", True)
        span.set_attribute("otelmind.eval.calibration.case_count", case_count)
        yield span
