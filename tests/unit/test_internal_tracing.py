"""Tests for otelmind.internal_tracing — eat-our-own-dog-food OTel spans."""

from __future__ import annotations

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from otelmind.internal_tracing import (
    get_internal_tracer,
    trace_batch_scoring,
    trace_calibration,
    trace_judge_call,
)


@pytest.fixture
def memory_exporter(monkeypatch):
    """Install an in-memory span exporter on a fresh TracerProvider.

    OTel's `set_tracer_provider` refuses to override once a provider is set
    (warns and no-ops). We bypass that by writing the proxy's underlying
    `_TRACER_PROVIDER` slot directly via monkeypatch so each test gets an
    isolated provider, and the original is restored on teardown.
    """
    from opentelemetry import trace

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(trace, "_TRACER_PROVIDER", provider, raising=False)

    import otelmind.internal_tracing as it

    monkeypatch.setattr(it, "_tracer", None)
    yield exporter
    exporter.clear()


def test_get_internal_tracer_returns_singleton(memory_exporter):
    a = get_internal_tracer()
    b = get_internal_tracer()
    assert a is b


def test_trace_judge_call_emits_span_with_attributes(memory_exporter):
    with trace_judge_call(dimension="faithfulness", model="gpt-4o"):
        pass
    spans = memory_exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "otelmind.judge.score_dimension"
    attrs = dict(span.attributes)
    assert attrs["otelmind.judge.dimension"] == "faithfulness"
    assert attrs["otelmind.judge.model"] == "gpt-4o"
    assert attrs["otelmind.internal"] is True


def test_trace_batch_scoring_records_case_count_and_concurrency(memory_exporter):
    with trace_batch_scoring(total_cases=42, concurrency=8):
        pass
    spans = memory_exporter.get_finished_spans()
    assert len(spans) == 1
    attrs = dict(spans[0].attributes)
    assert attrs["otelmind.eval.total_cases"] == 42
    assert attrs["otelmind.eval.concurrency"] == 8
    assert spans[0].name == "otelmind.eval.batch_score"


def test_trace_calibration_records_case_count(memory_exporter):
    with trace_calibration(case_count=25):
        pass
    spans = memory_exporter.get_finished_spans()
    assert len(spans) == 1
    attrs = dict(spans[0].attributes)
    assert attrs["otelmind.eval.calibration.case_count"] == 25


def test_nested_spans_attach_to_parent(memory_exporter):
    with trace_batch_scoring(total_cases=2, concurrency=1):
        with trace_judge_call(dimension="relevance", model="gpt-4o-mini"):
            pass
    spans = memory_exporter.get_finished_spans()
    # Spans flush in completion order — child finishes first
    by_name = {s.name: s for s in spans}
    parent = by_name["otelmind.eval.batch_score"]
    child = by_name["otelmind.judge.score_dimension"]
    assert child.parent is not None
    assert child.parent.span_id == parent.context.span_id


def test_spans_are_no_op_when_tracer_unavailable(monkeypatch):
    """If trace can't be imported, the helpers must still be safe to call."""
    import otelmind.internal_tracing as it

    monkeypatch.setattr(it, "trace", None)
    monkeypatch.setattr(it, "_tracer", None)
    # No raise, no span — just yields None
    with trace_judge_call("x", "y") as span:
        assert span is None
    with trace_batch_scoring(1, 1) as span:
        assert span is None
    with trace_calibration(1) as span:
        assert span is None
