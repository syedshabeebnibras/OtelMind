"""OpenTelemetry tracing for multi-agent groups.

Each `AgentGroup.solve()` call creates a trace tree like:

    group.solve (root)
    ├── round.1
    │   ├── agent.researcher.call
    │   ├── agent.coder.call
    │   └── agent.reviewer.call
    └── round.2 ...

Integrates with `otelmind.instrumentation.tracer.setup_tracer`: we use the
global tracer provider, so span attributes show up in the existing collector.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

try:
    from opentelemetry import trace

    _TRACER = trace.get_tracer("otelmind.multiagent")
except Exception:  # pragma: no cover — OTel optional at import time
    trace = None  # type: ignore[assignment]
    _TRACER = None


class GroupTracer:
    """Wraps OTel spans with multi-agent attribute naming."""

    @contextlib.contextmanager
    def trace_group(self, *, problem: str, protocol: str) -> Iterator[Any]:
        if _TRACER is None:
            yield None
            return
        with _TRACER.start_as_current_span("otelmind.multiagent.group.solve") as span:
            span.set_attribute("otelmind.multiagent.protocol", protocol)
            span.set_attribute("otelmind.multiagent.problem_length", len(problem))
            yield span

    @contextlib.contextmanager
    def trace_round(self, round_number: int, root_span: Any | None = None) -> Iterator[Any]:
        if _TRACER is None:
            yield None
            return
        with _TRACER.start_as_current_span(f"otelmind.multiagent.round.{round_number}") as span:
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
        if _TRACER is None:
            yield None
            return
        with _TRACER.start_as_current_span(f"otelmind.multiagent.agent.{role}.call") as span:
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
        if _TRACER is None:
            return
        current = trace.get_current_span() if trace is not None else None
        if current is None:
            return
        current.set_attribute("otelmind.multiagent.agent_id", agent_id)
        current.set_attribute("otelmind.multiagent.role", role)
        current.set_attribute("otelmind.multiagent.round", round_number)
        current.set_attribute("otelmind.multiagent.tokens", tokens)
