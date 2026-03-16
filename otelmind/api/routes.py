"""FastAPI route definitions for the OtelMind REST API."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from otelmind.api.schemas import (
    DashboardStatsResponse,
    FailureBreakdown,
    FailureResponse,
    HealthResponse,
    IngestResponse,
    MetricsResponse,
    RemediationBreakdown,
    SpanIngestRequest,
    SpanResponse,
    TraceDetailResponse,
    TraceResponse,
)
from sqlalchemy import func

from otelmind.collector.collector import collector
from otelmind.db import get_session
from otelmind.storage.models import FailureClassification, RemediationAction, Span, Trace
from otelmind.storage.telemetry_service import TelemetryService

router = APIRouter()


# ── Health ──────────────────────────────────────────────────────────────

@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    from otelmind import __version__

    db_status = "connected"
    try:
        async with get_session() as session:
            await session.execute(select(Trace).limit(0))
    except Exception:
        db_status = "disconnected"

    return HealthResponse(
        status="healthy",
        service="otelmind-api",
        database=db_status,
        version=__version__,
    )


# ── Traces ──────────────────────────────────────────────────────────────

@router.get("/traces", response_model=list[TraceResponse])
async def list_traces(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[TraceResponse]:
    async with get_session() as session:
        svc = TelemetryService(session)
        traces = await svc.list_traces(limit=limit, offset=offset)
        return [TraceResponse.model_validate(t) for t in traces]


@router.get("/traces/{trace_id}", response_model=TraceDetailResponse)
async def get_trace(trace_id: str) -> TraceDetailResponse:
    async with get_session() as session:
        # Load trace with spans eagerly
        stmt = (
            select(Trace)
            .where(Trace.trace_id == trace_id)
            .options(selectinload(Trace.spans))
        )
        result = await session.execute(stmt)
        trace = result.scalar_one_or_none()

        if trace is None:
            raise HTTPException(status_code=404, detail=f"Trace {trace_id} not found")

        return TraceDetailResponse(
            id=trace.id,
            trace_id=trace.trace_id,
            service_name=trace.service_name,
            status=trace.status,
            start_time=trace.start_time,
            end_time=trace.end_time,
            duration_ms=trace.duration_ms,
            metadata_=trace.metadata_,
            created_at=trace.created_at,
            spans=[SpanResponse.model_validate(s) for s in trace.spans],
        )


# ── Spans ───────────────────────────────────────────────────────────────

@router.get("/spans", response_model=list[SpanResponse])
async def list_spans(
    trace_id: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> list[SpanResponse]:
    async with get_session() as session:
        svc = TelemetryService(session)
        spans = await svc.list_spans(trace_id=trace_id, limit=limit, offset=offset)
        return [SpanResponse.model_validate(s) for s in spans]


# ── Failures ────────────────────────────────────────────────────────────

@router.get("/failures", response_model=list[FailureResponse])
async def list_failures(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[FailureResponse]:
    async with get_session() as session:
        svc = TelemetryService(session)
        failures = await svc.list_failures(limit=limit, offset=offset)
        return [FailureResponse.model_validate(f) for f in failures]


# ── Metrics ─────────────────────────────────────────────────────────────

@router.get("/metrics", response_model=MetricsResponse)
async def get_metrics() -> MetricsResponse:
    async with get_session() as session:
        svc = TelemetryService(session)
        data = await svc.get_metrics()
        return MetricsResponse(**data)


# ── Ingestion ───────────────────────────────────────────────────────────

@router.post("/ingest", response_model=IngestResponse)
async def ingest_spans(spans: list[SpanIngestRequest]) -> IngestResponse:
    """Ingest span records from instrumented LangGraph agents."""
    records = [s.model_dump() for s in spans]
    try:
        count = await collector.ingest(records)
        return IngestResponse(ingested=count, status="ok")
    except Exception as exc:
        logger.exception("Ingestion failed")
        raise HTTPException(status_code=500, detail=str(exc))


# ── Dashboard ───────────────────────────────────────────────────────────

@router.get("/dashboard/stats", response_model=DashboardStatsResponse)
async def dashboard_stats() -> DashboardStatsResponse:
    """Aggregated dashboard statistics for the OtelMind platform."""
    async with get_session() as session:
        svc = TelemetryService(session)
        metrics = await svc.get_metrics()

        # Failure rate
        total_traces = metrics["total_traces"]
        total_failures = metrics["total_failures"]
        failure_rate = (
            round(total_failures / total_traces * 100, 2) if total_traces > 0 else 0.0
        )

        # Failures by type
        fc_stmt = (
            select(
                FailureClassification.failure_type,
                func.count(FailureClassification.id).label("count"),
            )
            .group_by(FailureClassification.failure_type)
        )
        fc_result = await session.execute(fc_stmt)
        failures_by_type = [
            FailureBreakdown(failure_type=row.failure_type, count=row.count)
            for row in fc_result.all()
        ]

        # Remediation stats
        ra_stmt = (
            select(
                RemediationAction.action_type,
                func.count(RemediationAction.id).label("total"),
                func.count(
                    func.nullif(RemediationAction.status != "success", True)
                ).label("successful"),
            )
            .group_by(RemediationAction.action_type)
        )
        ra_result = await session.execute(ra_stmt)
        remediation_stats = []
        for row in ra_result.all():
            total = row.total
            successful = row.successful
            rate = round(successful / total * 100, 2) if total > 0 else 0.0
            remediation_stats.append(
                RemediationBreakdown(
                    action_type=row.action_type,
                    total=total,
                    successful=successful,
                    success_rate=rate,
                )
            )

        return DashboardStatsResponse(
            total_traces=total_traces,
            total_spans=metrics["total_spans"],
            total_failures=total_failures,
            failure_rate=failure_rate,
            avg_trace_duration_ms=metrics["avg_trace_duration_ms"],
            total_tokens_consumed=metrics["total_tokens_consumed"],
            failures_by_type=failures_by_type,
            remediation_stats=remediation_stats,
        )
