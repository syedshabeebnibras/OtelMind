"""OpenTelemetry tracing for multi-agent groups.

Each `AgentGroup.solve()` call creates a trace tree like:

    group.solve (root)
    ├── round.1
    │   ├── agent.researcher.call
    │   ├── agent.coder.call
    │   └── agent.reviewer.call
    └── round.2 ...

Shares the global TracerProvider configured by
`otelmind.instrumentation.tracer.init_tracer` — if the provider is already
initialised (by the FastAPI lifespan, for example), our spans feed into the
same exporter. If nothing has initialised it yet we call `init_tracer()`
ourselves so every group.solve still emits a usable trace.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

try:
    from opentelemetry import trace

    from otelmind.instrumentation.tracer import get_tracer as _otm_get_tracer
    from otelmind.instrumentation.tracer import init_tracer as _otm_init_tracer

    _OTEL_AVAILABLE = True
except Exception:  # pragma: no cover — OTel optional at import time
    trace = None  # type: ignore[assignment]
    _otm_get_tracer = None  # type: ignore[assignment]
    _otm_init_tracer = None  # type: ignore[assignment]
    _OTEL_AVAILABLE = False


def _resolve_tracer() -> Any | None:
    """Return a tracer bound to the shared OtelMind provider.

    Uses `otelmind.instrumentation.tracer.get_tracer`, which reads from the
    global provider configured by the FastAPI lifespan's `init_tracer()`
    call. If the provider has not been initialised yet we fall back to the
    OTel default (a ProxyTracerProvider) — spans become no-ops rather than
    leaking to stdout via an autoconfigured ConsoleSpanExporter. Set
    `OTELMIND_MULTIAGENT_AUTOINIT_TRACER=1` to have the first `solve()`
    initialise the provider for local/CLI runs.
    """
    import os

    if not _OTEL_AVAILABLE:
        return None
    provider = trace.get_tracer_provider()
    if (
        not hasattr(provider, "add_span_processor")
        and _otm_init_tracer is not None
        and os.environ.get("OTELMIND_MULTIAGENT_AUTOINIT_TRACER") == "1"
    ):
        try:
            _otm_init_tracer()
        except Exception:  # pragma: no cover — best effort
            return None
    return _otm_get_tracer("otelmind.multiagent")


class GroupTracer:
    """Wraps OTel spans with multi-agent attribute naming."""

    @contextlib.contextmanager
    def trace_group(self, *, problem: str, protocol: str) -> Iterator[Any]:
        tracer = _resolve_tracer()
        if tracer is None:
            yield None
            return
        with tracer.start_as_current_span("otelmind.multiagent.group.solve") as span:
            span.set_attribute("otelmind.multiagent.protocol", protocol)
            span.set_attribute("otelmind.multiagent.problem_length", len(problem))
            yield span

    @contextlib.contextmanager
    def trace_round(self, round_number: int, root_span: Any | None = None) -> Iterator[Any]:
        tracer = _resolve_tracer()
        if tracer is None:
            yield None
            return
        with tracer.start_as_current_span(f"otelmind.multiagent.round.{round_number}") as span:
            span.set_attribute("otelmind.multiagent.round", round_number)
            yield span

    @contextlib.contextmanager
    def trace_agent_call(
        self,
        *,
        agent_id: str,
        role: str,
        round_number: int,
        message_type: str = "broadcast",
    ) -> Iterator[Any]:
        tracer = _resolve_tracer()
        if tracer is None:
            yield None
            return
        with tracer.start_as_current_span(f"otelmind.multiagent.agent.{role}.call") as span:
            span.set_attribute("otelmind.multiagent.agent_id", agent_id)
            span.set_attribute("otelmind.multiagent.role", role)
            span.set_attribute("otelmind.multiagent.round", round_number)
            span.set_attribute("otelmind.multiagent.message_type", message_type)
            yield span

    def record_agent_call(
        self,
        *,
        agent_id: str,
        role: str,
        round_number: int,
        tokens: int,
    ) -> None:
        if not _OTEL_AVAILABLE:
            return
        current = trace.get_current_span() if trace is not None else None
        if current is None:
            return
        current.set_attribute("otelmind.multiagent.agent_id", agent_id)
        current.set_attribute("otelmind.multiagent.role", role)
        current.set_attribute("otelmind.multiagent.round", round_number)
        current.set_attribute("otelmind.multiagent.tokens", tokens)
