"""Unit tests for the failure detection module."""

from __future__ import annotations

from otelmind.watchdog.failure_detection import FailureDetector
from tests.conftest import make_span


class TestToolTimeoutDetection:
    def test_detects_timeout(self, failure_detector: FailureDetector) -> None:
        spans = [make_span(duration_ms=60_000)]
        failures = failure_detector._detect_tool_timeout("t1", spans)
        assert len(failures) == 1
        assert failures[0].failure_type == "tool_timeout"
        assert failures[0].confidence > 0

    def test_no_timeout_for_fast_spans(self, failure_detector: FailureDetector) -> None:
        spans = [make_span(duration_ms=500)]
        failures = failure_detector._detect_tool_timeout("t1", spans)
        assert len(failures) == 0


class TestInfiniteLoopDetection:
    def test_detects_loop(self, failure_detector: FailureDetector) -> None:
        spans = [make_span(name="langgraph.node.classify") for _ in range(7)]
        failures = failure_detector._detect_infinite_loop("t1", spans)
        assert len(failures) == 1
        assert failures[0].failure_type == "infinite_loop"

    def test_no_loop_for_few_executions(self, failure_detector: FailureDetector) -> None:
        spans = [make_span(name="langgraph.node.classify") for _ in range(2)]
        failures = failure_detector._detect_infinite_loop("t1", spans)
        assert len(failures) == 0


class TestContextOverflowDetection:
    def test_detects_overflow(self, failure_detector: FailureDetector) -> None:
        spans = [make_span(attributes={"llm.token.total_tokens": 200_000})]
        failures = failure_detector._detect_context_overflow("t1", spans)
        assert len(failures) == 1
        assert failures[0].failure_type == "context_overflow"

    def test_no_overflow_for_small_context(self, failure_detector: FailureDetector) -> None:
        spans = [make_span(attributes={"llm.token.total_tokens": 1000})]
        failures = failure_detector._detect_context_overflow("t1", spans)
        assert len(failures) == 0


class TestToolMisuseDetection:
    def test_detects_misuse(self, failure_detector: FailureDetector) -> None:
        spans = [
            make_span(status_code="ERROR", error_message="err1"),
            make_span(status_code="ERROR", error_message="err2"),
        ]
        failures = failure_detector._detect_tool_misuse("t1", spans)
        assert len(failures) == 1
        assert failures[0].failure_type == "tool_misuse"

    def test_no_misuse_for_single_error(self, failure_detector: FailureDetector) -> None:
        spans = [
            make_span(status_code="ERROR", error_message="err1"),
            make_span(status_code="OK"),
        ]
        failures = failure_detector._detect_tool_misuse("t1", spans)
        assert len(failures) == 0


class TestHallucinationDetection:
    def test_detects_hallucination(self, failure_detector: FailureDetector) -> None:
        spans = [
            make_span(name="llm.generate", outputs=None),
            make_span(name="llm.generate", outputs=None),
        ]
        failures = failure_detector._detect_hallucination("t1", spans)
        assert len(failures) == 1
        assert failures[0].failure_type == "hallucination"

    def test_no_hallucination_with_outputs(self, failure_detector: FailureDetector) -> None:
        spans = [
            make_span(name="llm.generate", outputs={"text": "hello"}),
            make_span(name="llm.generate", outputs={"text": "world"}),
        ]
        failures = failure_detector._detect_hallucination("t1", spans)
        assert len(failures) == 0


class TestFullAnalysis:
    def test_analyze_returns_multiple_failures(self, failure_detector: FailureDetector) -> None:
        spans = [
            make_span(duration_ms=60_000),
            make_span(name="langgraph.node.x"),
            *[make_span(name="langgraph.node.x") for _ in range(6)],
        ]
        failures = failure_detector.analyze("t1", spans)
        types = {f.failure_type for f in failures}
        assert "tool_timeout" in types
        assert "infinite_loop" in types

    def test_analyze_empty_spans(self, failure_detector: FailureDetector) -> None:
        failures = failure_detector.analyze("t1", [])
        assert failures == []
