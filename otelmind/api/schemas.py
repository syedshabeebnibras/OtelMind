"""Pydantic response schemas for the OtelMind API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

# ── Traces ──────────────────────────────────────────────────────────────


class TraceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    trace_id: str
    service_name: str
    status: str
    start_time: datetime
    end_time: datetime | None = None
    duration_ms: float | None = None
    metadata_: dict[str, Any] | None = None
    created_at: datetime


class TraceDetailResponse(TraceResponse):
    spans: list[SpanResponse] = []


# ── Spans ───────────────────────────────────────────────────────────────


class SpanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    span_id: str
    trace_id: str
    parent_span_id: str | None = None
    name: str
    kind: str
    status_code: str
    start_time: datetime
    end_time: datetime | None = None
    duration_ms: float | None = None
    attributes: dict[str, Any] | None = None
    inputs: dict[str, Any] | None = None
    outputs: dict[str, Any] | None = None
    error_message: str | None = None
    created_at: datetime


# ── Failures ────────────────────────────────────────────────────────────


class FailureResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    trace_id: str
    failure_type: str
    confidence: float
    evidence: dict[str, Any] | None = None
    detection_method: str
    created_at: datetime


# ── Metrics ─────────────────────────────────────────────────────────────


class MetricsResponse(BaseModel):
    total_traces: int
    total_spans: int
    total_failures: int
    total_tool_errors: int
    avg_trace_duration_ms: float
    total_tokens_consumed: int


# ── Dashboard ───────────────────────────────────────────────────────────


class FailureBreakdown(BaseModel):
    failure_type: str
    count: int


class RemediationBreakdown(BaseModel):
    action_type: str
    total: int
    successful: int
    success_rate: float


class DashboardStatsResponse(BaseModel):
    total_traces: int
    total_spans: int
    total_failures: int
    failure_rate: float
    avg_trace_duration_ms: float
    total_tokens_consumed: int
    failures_by_type: list[FailureBreakdown]
    remediation_stats: list[RemediationBreakdown]


# ── Span Ingestion ──────────────────────────────────────────────────────


class SpanIngestRequest(BaseModel):
    span_id: str
    trace_id: str
    name: str
    start_time: str
    end_time: str | None = None
    parent_span_id: str | None = None
    kind: str = "INTERNAL"
    status_code: str = "OK"
    duration_ms: float | None = None
    attributes: dict[str, Any] | None = None
    inputs: Any | None = None
    outputs: Any | None = None
    error_message: str | None = None


class IngestResponse(BaseModel):
    ingested: int
    status: str = "ok"


# ── Health ──────────────────────────────────────────────────────────────


class HealthResponse(BaseModel):
    status: str = "healthy"
    service: str = "otelmind-api"
    database: str = "connected"
    version: str
