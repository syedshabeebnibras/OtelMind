"""OtelMind telemetry bridge — instruments the agent and sends spans to the live API.

This module wraps each LangGraph node to capture span data (timing, inputs,
outputs, token usage, errors) and POSTs it to the OtelMind /api/v1/ingest
endpoint on the deployed Koyeb instance.
"""

from __future__ import annotations

import functools
import json
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx

OTELMIND_INGEST_URL = "https://lively-yolane-shabeebselfprojects-4bc070a2.koyeb.app/api/v1/ingest"


class OtelMindTelemetry:
    """Captures span data from LangGraph nodes and sends to OtelMind API."""

    def __init__(self, service_name: str = "research-agent", ingest_url: str | None = None) -> None:
        self.service_name = service_name
        self.ingest_url = ingest_url or OTELMIND_INGEST_URL
        self._trace_id: str = ""
        self._span_buffer: list[dict[str, Any]] = []
        self._step_counter: int = 0

    def new_trace(self) -> str:
        """Start a new trace (call before each graph invocation)."""
        self._trace_id = uuid.uuid4().hex
        self._span_buffer.clear()
        self._step_counter = 0
        return self._trace_id

    def instrument_node(self, node_name: str, fn: Callable) -> Callable:
        """Wrap a node function to capture span data."""
        telemetry = self

        @functools.wraps(fn)
        def wrapper(state: Any) -> Any:
            span_id = uuid.uuid4().hex
            step = telemetry._step_counter
            telemetry._step_counter += 1

            start = time.monotonic()
            start_dt = datetime.now(UTC)

            # Capture input preview
            input_preview = _safe_serialize(state, 500)

            try:
                result = fn(state)

                end = time.monotonic()
                end_dt = datetime.now(UTC)
                duration_ms = round((end - start) * 1000, 2)

                output_preview = _safe_serialize(result, 500)

                # Extract token info from result
                attrs: dict[str, Any] = {
                    "otelmind.node_name": node_name,
                    "otelmind.step_index": step,
                    "otelmind.service_name": telemetry.service_name,
                }

                prompt_tokens = 0
                completion_tokens = 0
                if isinstance(result, dict):
                    prompt_tokens = result.get("total_prompt_tokens", 0)
                    completion_tokens = result.get("total_completion_tokens", 0)
                    model = result.get("model_name", "gpt-4o")
                    if prompt_tokens or completion_tokens:
                        attrs["llm.token.prompt_tokens"] = prompt_tokens
                        attrs["llm.token.completion_tokens"] = completion_tokens
                        attrs["llm.token.total_tokens"] = prompt_tokens + completion_tokens
                        attrs["llm.model"] = model

                span = {
                    "span_id": span_id,
                    "trace_id": telemetry._trace_id,
                    "name": f"node.{node_name}",
                    "start_time": start_dt.isoformat(),
                    "end_time": end_dt.isoformat(),
                    "duration_ms": duration_ms,
                    "kind": "INTERNAL",
                    "status_code": "OK",
                    "attributes": attrs,
                    "inputs": input_preview,
                    "outputs": output_preview,
                    "error_message": None,
                }
                telemetry._span_buffer.append(span)
                return result

            except Exception as exc:
                end = time.monotonic()
                end_dt = datetime.now(UTC)
                duration_ms = round((end - start) * 1000, 2)

                span = {
                    "span_id": span_id,
                    "trace_id": telemetry._trace_id,
                    "name": f"node.{node_name}",
                    "start_time": start_dt.isoformat(),
                    "end_time": end_dt.isoformat(),
                    "duration_ms": duration_ms,
                    "kind": "INTERNAL",
                    "status_code": "ERROR",
                    "attributes": {
                        "otelmind.node_name": node_name,
                        "otelmind.step_index": step,
                        "otelmind.error_type": type(exc).__name__,
                    },
                    "inputs": input_preview,
                    "outputs": None,
                    "error_message": str(exc)[:1000],
                }
                telemetry._span_buffer.append(span)
                raise

        return wrapper

    def instrument_graph(self, graph_builder: Any) -> None:
        """Instrument all nodes on a LangGraph StateGraph builder.

        Modern LangGraph stores nodes as StateNodeSpec objects with a .runnable
        attribute. We wrap the underlying runnable's .func, preserving the spec.
        """
        if not hasattr(graph_builder, "nodes"):
            return

        for node_name in list(graph_builder.nodes.keys()):
            if node_name.startswith("__"):
                continue

            node_spec = graph_builder.nodes[node_name]

            # Modern LangGraph: StateNodeSpec with .runnable (RunnableCallable)
            if hasattr(node_spec, "runnable") and hasattr(node_spec.runnable, "func"):
                original_func = node_spec.runnable.func
                node_spec.runnable.func = self.instrument_node(node_name, original_func)
                print(f"  Instrumented node: {node_name}")
            elif callable(node_spec):
                # Fallback for older LangGraph versions
                graph_builder.nodes[node_name] = self.instrument_node(node_name, node_spec)
                print(f"  Instrumented node: {node_name}")

    def flush(self) -> int:
        """Send all buffered spans to the OtelMind API. Returns count sent."""
        if not self._span_buffer:
            return 0

        spans = list(self._span_buffer)
        self._span_buffer.clear()

        try:
            resp = httpx.post(
                self.ingest_url,
                json=spans,
                timeout=15.0,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
            count = data.get("ingested", len(spans))
            print(f"  → Flushed {count} spans to OtelMind (trace={self._trace_id[:12]}...)")
            return count
        except Exception as exc:
            print(f"  ✗ Failed to flush spans: {exc}")
            # Put them back
            self._span_buffer.extend(spans)
            return 0


def _safe_serialize(obj: Any, max_len: int = 500) -> str:
    """JSON-serialize, truncating to max_len."""
    try:
        text = json.dumps(obj, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(obj)
    if len(text) > max_len:
        text = text[:max_len] + "...[truncated]"
    return text
