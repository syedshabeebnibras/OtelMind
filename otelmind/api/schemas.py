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


# ── Dashboard / list envelopes (Next.js UI) ─────────────────────────────


class TraceListItem(BaseModel):
    trace_id: str
    service_name: str
    status: str
    duration_ms: float
    created_at: datetime
    span_count: int | None = None
    model: str | None = None


class TracesListResponse(BaseModel):
    items: list[TraceListItem]
    total: int
    next_cursor: str | None = None
    prev_cursor: str | None = None


class FailureListItem(BaseModel):
    id: str
    trace_id: str
    failure_type: str
    confidence: float
    detection_method: str
    timestamp: datetime
    service_name: str | None = None
    error_message: str | None = None


class FailuresListResponse(BaseModel):
    items: list[FailureListItem]
    total: int
    next_cursor: str | None = None
    prev_cursor: str | None = None


class CostBreakdownItemPublic(BaseModel):
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost: float
    trace_count: int = 0


class CostBreakdownResponsePublic(BaseModel):
    items: list[CostBreakdownItemPublic]
    total_cost: float
    period_start: str
    period_end: str
    daily_spend: list[dict[str, float | str]]


class DashboardStatsPublic(BaseModel):
    total_traces: int
    total_failures: int
    failure_rate: float
    avg_duration_ms: float
    total_cost_usd: float
    active_services: int
    failures_by_type: dict[str, int]
    traces_by_status: dict[str, int]


class AlertRulePublic(BaseModel):
    id: str
    failure_type: str
    threshold: float
    channels: list[str]
    enabled: bool
    created_at: datetime


class AlertRulesResponsePublic(BaseModel):
    items: list[AlertRulePublic]
