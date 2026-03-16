"""OtelMind Instrumentor — monkey-patches LangGraph's CompiledGraph.invoke for tracing."""

from __future__ import annotations

import functools
import time
from typing import Any, Callable

from loguru import logger
from opentelemetry import trace
from opentelemetry.trace import StatusCode

from otelmind.instrumentation.tracer import init_tracer, get_tracer

_original_invoke: Callable | None = None


def _extract_token_counts(response: Any, state: dict[str, Any] | None = None) -> dict[str, int]:
    """Extract token usage counts from LLM responses.

    Checks three common patterns used by LangChain / LangGraph models:
    1. response.response_metadata.token_usage  (OpenAI-style)
    2. response.usage_metadata                 (Anthropic / generic)
    3. state["messages"][-1].response_metadata (last message in graph state)

    Returns a dict with prompt_tokens, completion_tokens, total_tokens (all default to 0).
    """
    counts: dict[str, int] = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }

    # Pattern 1: response_metadata.token_usage (OpenAI-style via ChatOpenAI)
    resp_meta = getattr(response, "response_metadata", None)
    if resp_meta and isinstance(resp_meta, dict):
        token_usage = resp_meta.get("token_usage")
        if token_usage and isinstance(token_usage, dict):
            counts["prompt_tokens"] = token_usage.get("prompt_tokens", 0)
            counts["completion_tokens"] = token_usage.get("completion_tokens", 0)
            counts["total_tokens"] = token_usage.get("total_tokens", 0)
            return counts

    # Pattern 2: usage_metadata on the response object (Anthropic / generic)
    usage_meta = getattr(response, "usage_metadata", None)
    if usage_meta:
        if isinstance(usage_meta, dict):
            counts["prompt_tokens"] = usage_meta.get("input_tokens", 0)
            counts["completion_tokens"] = usage_meta.get("output_tokens", 0)
            counts["total_tokens"] = usage_meta.get("total_tokens", 0)
            return counts
        # Some models expose usage_metadata as an object with attributes
        counts["prompt_tokens"] = getattr(usage_meta, "input_tokens", 0)
        counts["completion_tokens"] = getattr(usage_meta, "output_tokens", 0)
        counts["total_tokens"] = getattr(usage_meta, "total_tokens", 0)
        if counts["total_tokens"] or counts["prompt_tokens"]:
            return counts

    # Pattern 3: state["messages"][-1].response_metadata
    if state and isinstance(state, dict):
        messages = state.get("messages")
        if messages and len(messages) > 0:
            last_msg = messages[-1]
            msg_meta = getattr(last_msg, "response_metadata", None)
            if msg_meta and isinstance(msg_meta, dict):
                token_usage = msg_meta.get("token_usage", {})
                if token_usage:
                    counts["prompt_tokens"] = token_usage.get("prompt_tokens", 0)
                    counts["completion_tokens"] = token_usage.get("completion_tokens", 0)
                    counts["total_tokens"] = token_usage.get("total_tokens", 0)
                    return counts
                # Also check for usage_metadata within response_metadata
                usage = msg_meta.get("usage_metadata", {})
                if usage:
                    counts["prompt_tokens"] = usage.get("input_tokens", 0)
                    counts["completion_tokens"] = usage.get("output_tokens", 0)
                    counts["total_tokens"] = usage.get("total_tokens", 0)

    return counts


class OtelMindInstrumentor:
    """Instruments LangGraph's ``CompiledGraph.invoke`` with OpenTelemetry spans.

    Usage::

        instrumentor = OtelMindInstrumentor(service_name="my-agent")
        instrumentor.instrument()
        # ... run your LangGraph graph as usual ...
        instrumentor.uninstrument()
    """

    def __init__(
        self,
        *,
        service_name: str = "otelmind",
        otel_endpoint: str | None = None,
        console_export: bool = False,
    ) -> None:
        self._service_name = service_name
        self._otel_endpoint = otel_endpoint
        self._console_export = console_export
        self._tracer: trace.Tracer | None = None
        self._patched_nodes: dict[str, Callable] = {}

    def instrument(self) -> None:
        """Patch ``CompiledGraph.invoke`` to emit root and child spans."""
        global _original_invoke

        # Set up tracer provider
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter

        exporter = None
        if self._console_export:
            exporter = ConsoleSpanExporter()
        elif self._otel_endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                    OTLPSpanExporter,
                )
                exporter = OTLPSpanExporter(endpoint=self._otel_endpoint)
            except ImportError:
                logger.warning(
                    "OTLP gRPC exporter not available; falling back to console export"
                )
                exporter = ConsoleSpanExporter()

        init_tracer(exporter=exporter, service_name=self._service_name)
        self._tracer = get_tracer("otelmind.instrumentor")

        # Monkey-patch CompiledGraph.invoke
        try:
            from langgraph.pregel import CompiledGraph
        except ImportError:
            logger.error(
                "langgraph is not installed — cannot instrument CompiledGraph.invoke"
            )
            return

        if _original_invoke is not None:
            logger.warning("CompiledGraph.invoke is already patched; skipping")
            return

        _original_invoke = CompiledGraph.invoke
        instrumentor = self

        @functools.wraps(_original_invoke)
        def _traced_invoke(graph_self: Any, input: Any, config: Any = None, **kwargs: Any) -> Any:
            tracer = instrumentor._tracer
            assert tracer is not None

            graph_name = getattr(graph_self, "name", None) or graph_self.__class__.__name__

            with tracer.start_as_current_span(
                f"graph.invoke:{graph_name}",
                attributes={
                    "graph.name": graph_name,
                    "otelmind.service": instrumentor._service_name,
                },
            ) as root_span:
                start = time.perf_counter()
                try:
                    # Patch individual nodes before invocation
                    instrumentor._patch_nodes(graph_self)

                    result = _original_invoke(graph_self, input, config, **kwargs)

                    elapsed_ms = (time.perf_counter() - start) * 1000
                    root_span.set_attribute("graph.duration_ms", round(elapsed_ms, 2))

                    # Extract token counts from final state
                    tokens = _extract_token_counts(result, result if isinstance(result, dict) else None)
                    if tokens["total_tokens"]:
                        root_span.set_attribute("llm.prompt_tokens", tokens["prompt_tokens"])
                        root_span.set_attribute("llm.completion_tokens", tokens["completion_tokens"])
                        root_span.set_attribute("llm.total_tokens", tokens["total_tokens"])

                    root_span.set_status(StatusCode.OK)
                    return result
                except Exception as exc:
                    elapsed_ms = (time.perf_counter() - start) * 1000
                    root_span.set_attribute("graph.duration_ms", round(elapsed_ms, 2))
                    root_span.set_status(StatusCode.ERROR, str(exc))
                    root_span.record_exception(exc)
                    raise
                finally:
                    instrumentor._unpatch_nodes(graph_self)

        CompiledGraph.invoke = _traced_invoke  # type: ignore[assignment]
        logger.info("OtelMind instrumented CompiledGraph.invoke")

    def uninstrument(self) -> None:
        """Restore the original ``CompiledGraph.invoke``."""
        global _original_invoke

        if _original_invoke is None:
            return

        try:
            from langgraph.pregel import CompiledGraph

            CompiledGraph.invoke = _original_invoke  # type: ignore[assignment]
            _original_invoke = None
            logger.info("OtelMind uninstrumented CompiledGraph.invoke")
        except ImportError:
            pass

    # ------------------------------------------------------------------
    # Node-level patching
    # ------------------------------------------------------------------

    def _patch_nodes(self, graph: Any) -> None:
        """Wrap each node function in the compiled graph with a child span."""
        nodes: dict[str, Any] | None = getattr(graph, "nodes", None)
        if nodes is None:
            return

        for idx, (node_name, node_fn) in enumerate(nodes.items()):
            if node_name in self._patched_nodes:
                continue  # already wrapped
            original = node_fn
            self._patched_nodes[node_name] = original
            nodes[node_name] = self._create_node_wrapper(node_name, original, step_index=idx)

    def _unpatch_nodes(self, graph: Any) -> None:
        """Restore original node functions."""
        nodes: dict[str, Any] | None = getattr(graph, "nodes", None)
        if nodes is None:
            self._patched_nodes.clear()
            return

        for node_name, original_fn in self._patched_nodes.items():
            if node_name in nodes:
                nodes[node_name] = original_fn
        self._patched_nodes.clear()

    def _create_node_wrapper(
        self,
        node_name: str,
        original_fn: Callable,
        *,
        step_index: int = 0,
    ) -> Callable:
        """Return a wrapper that executes *original_fn* inside a child span.

        Attributes recorded on each span:
        - node.name, node.step_index
        - node.duration_ms
        - node.input_preview  (first 500 chars of input repr)
        - node.output_preview (first 500 chars of output repr)
        - error details on failure
        """
        tracer = self._tracer
        assert tracer is not None
        max_preview = 500

        @functools.wraps(original_fn)
        def _wrapper(*args: Any, **kwargs: Any) -> Any:
            input_preview = repr(args)[:max_preview] if args else repr(kwargs)[:max_preview]

            with tracer.start_as_current_span(
                f"node:{node_name}",
                attributes={
                    "node.name": node_name,
                    "node.step_index": step_index,
                    "node.input_preview": input_preview,
                },
            ) as span:
                start = time.perf_counter()
                try:
                    result = original_fn(*args, **kwargs)

                    elapsed_ms = (time.perf_counter() - start) * 1000
                    span.set_attribute("node.duration_ms", round(elapsed_ms, 2))

                    output_preview = repr(result)[:max_preview]
                    span.set_attribute("node.output_preview", output_preview)

                    # Try to extract token counts from node output
                    tokens = _extract_token_counts(
                        result, result if isinstance(result, dict) else None
                    )
                    if tokens["total_tokens"]:
                        span.set_attribute("llm.prompt_tokens", tokens["prompt_tokens"])
                        span.set_attribute("llm.completion_tokens", tokens["completion_tokens"])
                        span.set_attribute("llm.total_tokens", tokens["total_tokens"])

                    span.set_status(StatusCode.OK)
                    return result
                except Exception as exc:
                    elapsed_ms = (time.perf_counter() - start) * 1000
                    span.set_attribute("node.duration_ms", round(elapsed_ms, 2))
                    span.set_attribute("error.type", type(exc).__name__)
                    span.set_attribute("error.message", str(exc)[:max_preview])
                    span.set_status(StatusCode.ERROR, str(exc))
                    span.record_exception(exc)
                    raise

        return _wrapper
