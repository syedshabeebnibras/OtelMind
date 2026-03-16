"""Unit tests for Pydantic schemas."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from otelmind.api.schemas import (
    HealthResponse,
    IngestResponse,
    MetricsResponse,
    SpanIngestRequest,
    TraceResponse,
)


class TestSchemas:
    def test_health_response(self) -> None:
        h = HealthResponse(status="healthy", version="0.1.0")
        assert h.status == "healthy"
        assert h.service == "otelmind-api"
        assert h.database == "connected"

    def test_metrics_response(self) -> None:
        m = MetricsResponse(
            total_traces=10,
            total_spans=50,
            total_failures=2,
            total_tool_errors=1,
            avg_trace_duration_ms=123.45,
            total_tokens_consumed=5000,
        )
        assert m.total_traces == 10

    def test_span_ingest_request(self) -> None:
        s = SpanIngestRequest(
            span_id="s1",
            trace_id="t1",
            name="test_span",
            start_time="2025-01-01T00:00:00Z",
        )
        assert s.kind == "INTERNAL"
        assert s.status_code == "OK"

    def test_ingest_response(self) -> None:
        r = IngestResponse(ingested=5)
        assert r.status == "ok"

    def test_trace_response(self) -> None:
        t = TraceResponse(
            id=uuid.uuid4(),
            trace_id="t1",
            service_name="test",
            status="ok",
            start_time=datetime.now(UTC),
            created_at=datetime.now(UTC),
        )
        assert t.trace_id == "t1"
