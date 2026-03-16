"""LangGraph node-level instrumentation via OpenTelemetry spans."""

from __future__ import annotations

import functools
import json
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, TypeVar

from loguru import logger
from opentelemetry.trace import StatusCode

from otelmind.instrumentation.tracer import get_tracer

F = TypeVar("F", bound=Callable[..., Any])


class LangGraphInstrumentor:
    """Wraps LangGraph node functions with OpenTelemetry spans.

    Usage::

        instrumentor = LangGraphInstrumentor()

        @instrumentor.instrument_node("classify")
        def classify(state: dict) -> dict:
            ...
    """

    def __init__(self, tracer_name: str = "otelmind.langgraph") -> None:
        self._tracer_name = tracer_name
        self._span_records: list[dict[str, Any]] = []

    @property
    def span_records(self) -> list[dict[str, Any]]:
        """Return collected span records (useful for the collector)."""
        return list(self._span_records)

    def drain_span_records(self) -> list[dict[str, Any]]:
        """Return and clear collected span records."""
        records = list(self._span_records)
        self._span_records.clear()
        return records

    def instrument_node(self, node_name: str) -> Callable[[F], F]:
        """Decorator that wraps a LangGraph node function in an OTel span."""

        def decorator(fn: F) -> F:
            @functools.wraps(fn)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                tracer = get_tracer(self._tracer_name)
                with tracer.start_as_current_span(f"langgraph.node.{node_name}") as span:
                    span_ctx = span.get_span_context()
                    span_id = format(span_ctx.span_id, "032x")
                    trace_id = format(span_ctx.trace_id, "032x")

                    start = time.monotonic()
                    start_dt = datetime.now(UTC)

                    # Capture inputs
                    input_snapshot = _safe_serialize(args[0] if args else kwargs)
                    span.set_attribute("langgraph.node.name", node_name)
                    span.set_attribute("langgraph.node.input", input_snapshot)

                    try:
                        result = fn(*args, **kwargs)

                        # Capture outputs
                        output_snapshot = _safe_serialize(result)
                        span.set_attribute("langgraph.node.output", output_snapshot)

                        # Token usage extraction
                        token_info = _extract_token_usage(result)
                        if token_info:
                            for k, v in token_info.items():
                                span.set_attribute(f"llm.token.{k}", v)

                        span.set_status(StatusCode.OK)
                        status_code = "OK"
                        error_message = None

                    except Exception as exc:
                        span.set_status(StatusCode.ERROR, str(exc))
                        span.record_exception(exc)
                        status_code = "ERROR"
                        error_message = str(exc)
                        raise
                    finally:
                        end = time.monotonic()
                        end_dt = datetime.now(UTC)
                        duration_ms = (end - start) * 1000

                        # Collect the record for later persistence
                        record: dict[str, Any] = {
                            "span_id": span_id,
                            "trace_id": trace_id,
                            "parent_span_id": None,
                            "name": f"langgraph.node.{node_name}",
                            "kind": "INTERNAL",
                            "status_code": status_code,
                            "start_time": start_dt.isoformat(),
                            "end_time": end_dt.isoformat(),
                            "duration_ms": round(duration_ms, 3),
                            "inputs": input_snapshot if isinstance(input_snapshot, str) else None,
                            "outputs": (
                                output_snapshot
                                if status_code == "OK" and isinstance(output_snapshot, str)
                                else None
                            ),
                            "error_message": error_message,
                            "attributes": {
                                "langgraph.node.name": node_name,
                            },
                        }
                        self._span_records.append(record)
                        logger.debug(
                            "Instrumented node={} span={} duration={:.1f}ms",
                            node_name,
                            span_id,
                            duration_ms,
                        )

                return result

            return wrapper  # type: ignore[return-value]

        return decorator

    def instrument_graph(self, graph_builder: Any) -> str:
        """Instrument all nodes registered on a LangGraph StateGraph builder.

        Returns the trace_id that will be used for all spans in this graph execution.
        """
        trace_id = uuid.uuid4().hex
        if hasattr(graph_builder, "nodes"):
            for node_name, node_fn in list(graph_builder.nodes.items()):
                wrapped = self.instrument_node(node_name)(node_fn)
                graph_builder.nodes[node_name] = wrapped
                logger.info("Instrumented LangGraph node: {}", node_name)
        return trace_id


def _safe_serialize(obj: Any, max_len: int = 2000) -> str:
    """JSON-serialise an object, truncating to max_len."""
    try:
        text = json.dumps(obj, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(obj)
    if len(text) > max_len:
        text = text[:max_len] + "...[truncated]"
    return text


def _extract_token_usage(result: Any) -> dict[str, int] | None:
    """Best-effort extraction of token usage from a LangChain-style response."""
    if isinstance(result, dict):
        usage = result.get("usage_metadata") or result.get("token_usage")
        if isinstance(usage, dict):
            return {
                "prompt_tokens": usage.get("input_tokens", usage.get("prompt_tokens", 0)),
                "completion_tokens": usage.get("output_tokens", usage.get("completion_tokens", 0)),
                "total_tokens": usage.get("total_tokens", 0),
            }
    if hasattr(result, "usage_metadata"):
        um = result.usage_metadata
        if isinstance(um, dict):
            return {
                "prompt_tokens": um.get("input_tokens", 0),
                "completion_tokens": um.get("output_tokens", 0),
                "total_tokens": um.get("total_tokens", 0),
            }
    return None
