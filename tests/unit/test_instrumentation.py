"""Unit tests for the LangGraph instrumentor."""

from __future__ import annotations

from otelmind.instrumentation.langgraph_instrumentor import (
    LangGraphInstrumentor,
    _extract_token_usage,
    _safe_serialize,
)
from otelmind.instrumentation.tracer import init_tracer, shutdown_tracer


class TestSafeSerialize:
    def test_dict(self) -> None:
        result = _safe_serialize({"key": "value"})
        assert '"key"' in result

    def test_truncation(self) -> None:
        result = _safe_serialize("x" * 5000, max_len=100)
        assert len(result) <= 120  # 100 + truncation suffix
        assert "truncated" in result

    def test_non_serializable(self) -> None:
        result = _safe_serialize(object())
        assert isinstance(result, str)


class TestExtractTokenUsage:
    def test_dict_with_usage_metadata(self) -> None:
        data = {"usage_metadata": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}}
        result = _extract_token_usage(data)
        assert result is not None
        assert result["prompt_tokens"] == 10
        assert result["completion_tokens"] == 20

    def test_returns_none_for_missing(self) -> None:
        assert _extract_token_usage({"foo": "bar"}) is None
        assert _extract_token_usage("string") is None


class TestInstrumentor:
    def setup_method(self) -> None:
        init_tracer()

    def teardown_method(self) -> None:
        shutdown_tracer()

    def test_instrument_node_captures_span(self) -> None:
        instrumentor = LangGraphInstrumentor()

        @instrumentor.instrument_node("test_node")
        def my_node(state: dict) -> dict:
            return {"result": "ok"}

        result = my_node({"input": "hello"})
        assert result == {"result": "ok"}

        records = instrumentor.span_records
        assert len(records) == 1
        assert records[0]["name"] == "langgraph.node.test_node"
        assert records[0]["status_code"] == "OK"

    def test_instrument_node_captures_errors(self) -> None:
        instrumentor = LangGraphInstrumentor()

        @instrumentor.instrument_node("failing_node")
        def bad_node(state: dict) -> dict:
            raise ValueError("boom")

        try:
            bad_node({})
        except ValueError:
            pass

        records = instrumentor.span_records
        assert len(records) == 1
        assert records[0]["status_code"] == "ERROR"
        assert "boom" in (records[0]["error_message"] or "")

    def test_drain_clears_records(self) -> None:
        instrumentor = LangGraphInstrumentor()

        @instrumentor.instrument_node("drain_test")
        def node(state: dict) -> dict:
            return state

        node({})
        drained = instrumentor.drain_span_records()
        assert len(drained) == 1
        assert len(instrumentor.span_records) == 0
