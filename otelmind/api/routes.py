"""FastAPI route definitions for the OtelMind REST API."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from loguru import logger
from sqlalchemy import case, func, select
from sqlalchemy.orm import selectinload

from otelmind.api.auth import CurrentTenant, require_scope
from otelmind.api.rate_limit import enforce_tenant_rate_limit
from otelmind.api.schemas import (
    AlertRulesResponsePublic,
    AlertRulePublic,
    CostBreakdownResponsePublic,
    CostBreakdownItemPublic,
    DashboardStatsPublic,
    DashboardStatsResponse,
    FailureBreakdown,
    FailureListItem,
    FailuresListResponse,
    HealthResponse,
    IngestResponse,
    MetricsResponse,
    RemediationBreakdown,
    SpanIngestRequest,
    SpanResponse,
    TraceListItem,
    TraceResponse,
    TracesListResponse,
)
from otelmind.collector.collector import collector
from otelmind.cost.service import CostService
from otelmind.db import get_session
from otelmind.storage.models import (
    AlertRule,
    FailureClassification,
    RemediationAction,
    TokenCount,
    Trace,
)
from otelmind.storage.telemetry_service import TelemetryService

router = APIRouter()


def _trace_status_ui(t: Trace) -> str:
    if t.end_time is None and (t.status or "ok") == "ok":
        return "running"
    if t.status == "ok":
        return "success"
    if t.status == "error":
        return "error"
    if t.status == "warning":
        return "warning"
    return t.status or "success"


def _span_status_ui(code: str) -> str:
    c = (code or "OK").upper()
    if c == "OK":
        return "success"
    if c == "ERROR":
        return "error"
    return "warning"


def _trace_to_list_item(t: Trace) -> TraceListItem:
    return TraceListItem(
        trace_id=t.trace_id,
        service_name=t.service_name,
        status=_trace_status_ui(t),
        duration_ms=float(t.duration_ms or 0.0),
        created_at=t.created_at or t.start_time,
    )


def _span_to_ui_dict(s: Any) -> dict[str, Any]:
    attrs = s.attributes or {}
    svc = attrs.get("service.name") or attrs.get("service_name") or "internal"
    model = attrs.get("llm.model")
    pt = int(attrs.get("llm.token.prompt_tokens") or 0)
    ct = int(attrs.get("llm.token.completion_tokens") or 0)
    return {
        "span_id": s.span_id,
        "trace_id": s.trace_id,
        "parent_span_id": s.parent_span_id,
        "name": s.name,
        "service_name": str(svc),
        "status": _span_status_ui(s.status_code),
        "start_time": s.start_time.isoformat() if s.start_time else "",
        "end_time": s.end_time.isoformat() if s.end_time else "",
        "duration_ms": float(s.duration_ms or 0.0),
        "attributes": attrs,
        "model": model,
        "prompt_tokens": pt,
        "completion_tokens": ct,
        "error_message": s.error_message,
    }


def _parse_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        return max(0, int(cursor))
    except ValueError:
        return 0


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


@router.get("/traces", response_model=TracesListResponse)
async def list_traces(
    request: Request,
    tenant: CurrentTenant,
    _: Annotated[None, Depends(require_scope("read", "admin"))],
    limit: int = Query(50, ge=1, le=500),
    cursor: str | None = Query(None),
    service_name: str | None = Query(None),
    status: str | None = Query(None),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
) -> TracesListResponse:
    await enforce_tenant_rate_limit(request, tenant, "read")
    offset = _parse_cursor(cursor)
    start_dt = None
    end_dt = None
    if start_date:
        d = date.fromisoformat(start_date)
        start_dt = datetime(d.year, d.month, d.day, tzinfo=UTC)
    if end_date:
        d = date.fromisoformat(end_date)
        end_dt = datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=UTC)

    async with get_session() as session:
        svc = TelemetryService(session)
        total = await svc.count_traces(
            tenant.id,
            service_name=service_name,
            ui_status=status,
            start_time=start_dt,
            end_time=end_dt,
        )
        traces = await svc.list_traces(
            tenant.id,
            limit=limit,
            offset=offset,
            service_name=service_name,
            ui_status=status,
            start_time=start_dt,
            end_time=end_dt,
        )
    items = [_trace_to_list_item(t) for t in traces]
    next_cursor = str(offset + limit) if offset + limit < total else None
    prev_cursor = str(max(0, offset - limit)) if offset > 0 else None
    return TracesListResponse(items=items, total=total, next_cursor=next_cursor, prev_cursor=prev_cursor)


@router.get("/traces/{trace_id}")
async def get_trace(trace_id: str, tenant: CurrentTenant, _: Annotated[None, Depends(require_scope("read", "admin"))]):
    async with get_session() as session:
        stmt = (
            select(Trace)
            .where(Trace.tenant_id == tenant.id, Trace.trace_id == trace_id)
            .options(selectinload(Trace.spans))
        )
        result = await session.execute(stmt)
        trace = result.scalar_one_or_none()
        if trace is None:
            raise HTTPException(status_code=404, detail=f"Trace {trace_id} not found")

        tok = await session.execute(
            select(
                func.coalesce(func.sum(TokenCount.prompt_tokens), 0),
                func.coalesce(func.sum(TokenCount.completion_tokens), 0),
                func.coalesce(func.sum(TokenCount.cost_usd), 0.0),
            ).where(TokenCount.tenant_id == tenant.id, TokenCount.trace_id == trace_id)
        )
        pt, ct, cost = tok.one()

    spans_ui = [
        _span_to_ui_dict(s)
        for s in sorted(
            trace.spans,
            key=lambda x: x.start_time or datetime.min.replace(tzinfo=UTC),
        )
    ]
    return {
        "trace_id": trace.trace_id,
        "service_name": trace.service_name,
        "status": _trace_status_ui(trace),
        "duration_ms": float(trace.duration_ms or 0.0),
        "created_at": (trace.created_at or trace.start_time).isoformat(),
        "spans": spans_ui,
        "total_tokens": int(pt + ct),
        "prompt_tokens": int(pt),
        "completion_tokens": int(ct),
        "estimated_cost": float(cost or 0.0),
    }


# ── Spans ───────────────────────────────────────────────────────────────


@router.get("/spans", response_model=list[SpanResponse])
async def list_spans(
    request: Request,
    tenant: CurrentTenant,
    _: Annotated[None, Depends(require_scope("read", "admin"))],
    trace_id: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> list[SpanResponse]:
    await enforce_tenant_rate_limit(request, tenant, "read")
    async with get_session() as session:
        svc = TelemetryService(session)
        spans = await svc.list_spans(tenant.id, trace_id=trace_id, limit=limit, offset=offset)
        return [SpanResponse.model_validate(s) for s in spans]


# ── Failures ────────────────────────────────────────────────────────────


@router.get("/failures", response_model=FailuresListResponse)
async def list_failures(
    request: Request,
    tenant: CurrentTenant,
    _: Annotated[None, Depends(require_scope("read", "admin"))],
    limit: int = Query(50, ge=1, le=500),
    cursor: str | None = Query(None),
    failure_type: str | None = Query(None),
) -> FailuresListResponse:
    await enforce_tenant_rate_limit(request, tenant, "read")
    offset = _parse_cursor(cursor)
    async with get_session() as session:
        stmt = select(FailureClassification).where(FailureClassification.tenant_id == tenant.id)
        if failure_type:
            stmt = stmt.where(FailureClassification.failure_type == failure_type)
        count_stmt = select(func.count(FailureClassification.id)).where(FailureClassification.tenant_id == tenant.id)
        if failure_type:
            count_stmt = count_stmt.where(FailureClassification.failure_type == failure_type)
        total = int(await session.scalar(count_stmt) or 0)
        stmt = stmt.order_by(FailureClassification.created_at.desc()).limit(limit).offset(offset)
        result = await session.execute(stmt)
        rows = list(result.scalars().all())

    items = [
        FailureListItem(
            id=str(f.id),
            trace_id=f.trace_id,
            failure_type=f.failure_type,
            confidence=f.confidence,
            detection_method=f.detection_method,
            timestamp=f.created_at,
        )
        for f in rows
    ]
    next_cursor = str(offset + limit) if offset + limit < total else None
    return FailuresListResponse(items=items, total=total, next_cursor=next_cursor, prev_cursor=None)


@router.get("/stream/failures")
async def stream_failures(
    tenant: CurrentTenant,
    _: Annotated[None, Depends(require_scope("read", "admin"))],
):
    try:
        from sse_starlette.sse import EventSourceResponse
    except ImportError as exc:  # pragma: no cover
        raise HTTPException(status_code=501, detail="sse-starlette not installed") from exc

    async def gen():
        last_ts: datetime | None = None
        while True:
            async with get_session() as session:
                q = select(FailureClassification).where(FailureClassification.tenant_id == tenant.id)
                if last_ts is not None:
                    q = q.where(FailureClassification.created_at > last_ts)
                q = q.order_by(FailureClassification.created_at.asc()).limit(20)
                res = await session.execute(q)
                batch = list(res.scalars().all())
            for f in batch:
                last_ts = f.created_at
                payload = {
                    "id": str(f.id),
                    "trace_id": f.trace_id,
                    "failure_type": f.failure_type,
                    "confidence": f.confidence,
                    "created_at": f.created_at.isoformat(),
                }
                yield {"event": "failure", "data": json.dumps(payload)}
            await asyncio.sleep(2)

    return EventSourceResponse(gen())


# ── Metrics ─────────────────────────────────────────────────────────────


@router.get("/metrics", response_model=MetricsResponse)
async def get_metrics(
    request: Request,
    tenant: CurrentTenant,
    _: Annotated[None, Depends(require_scope("read", "admin"))],
) -> MetricsResponse:
    await enforce_tenant_rate_limit(request, tenant, "read")
    async with get_session() as session:
        svc = TelemetryService(session)
        data = await svc.get_metrics(tenant.id)
        return MetricsResponse(**data)


# ── Cost ────────────────────────────────────────────────────────────────


@router.get("/cost/breakdown", response_model=CostBreakdownResponsePublic)
async def cost_breakdown(
    request: Request,
    tenant: CurrentTenant,
    _: Annotated[None, Depends(require_scope("read", "admin"))],
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    group_by: Literal["model", "provider", "day"] = Query("model"),
) -> CostBreakdownResponsePublic:
    await enforce_tenant_rate_limit(request, tenant, "read")
    start = datetime.now(UTC) - timedelta(days=30)
    end = datetime.now(UTC)
    if start_date:
        d = date.fromisoformat(start_date)
        start = datetime(d.year, d.month, d.day, tzinfo=UTC)
    if end_date:
        d = date.fromisoformat(end_date)
        end = datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=UTC)

    async with get_session() as session:
        csvc = CostService(session)
        raw = await csvc.get_breakdown(tenant.id, start=start, end=end, group_by=group_by)
        day_raw = await csvc.get_breakdown(tenant.id, start=start, end=end, group_by="day")

    items: list[CostBreakdownItemPublic] = []
    if group_by != "day":
        for it in raw.get("items", []):
            items.append(
                CostBreakdownItemPublic(
                    model=it.get("model", it.get("provider", "unknown")),
                    prompt_tokens=int(it.get("prompt_tokens", 0)),
                    completion_tokens=int(it.get("completion_tokens", 0)),
                    total_tokens=int(it.get("prompt_tokens", 0) + it.get("completion_tokens", 0)),
                    estimated_cost=float(it.get("cost_usd", 0)),
                    trace_count=0,
                )
            )
    else:
        for it in raw.get("items", []):
            items.append(
                CostBreakdownItemPublic(
                    model=str(it.get("date", "day")),
                    prompt_tokens=int(it.get("prompt_tokens", 0)),
                    completion_tokens=int(it.get("completion_tokens", 0)),
                    total_tokens=int(it.get("prompt_tokens", 0) + it.get("completion_tokens", 0)),
                    estimated_cost=float(it.get("cost_usd", 0)),
                    trace_count=0,
                )
            )

    daily = [
        {"date": str(it["date"]), "cost": float(it.get("cost_usd", 0))}
        for it in day_raw.get("items", [])
    ]

    return CostBreakdownResponsePublic(
        items=items,
        total_cost=float(raw.get("total_cost_usd", 0)),
        period_start=start.isoformat(),
        period_end=end.isoformat(),
        daily_spend=daily,
    )


# ── Ingestion ───────────────────────────────────────────────────────────


@router.post("/ingest", response_model=IngestResponse)
async def ingest_spans(
    request: Request,
    tenant: CurrentTenant,
    _: Annotated[None, Depends(require_scope("ingest", "admin"))],
    spans: list[SpanIngestRequest],
) -> IngestResponse:
    await enforce_tenant_rate_limit(request, tenant, "ingest")
    records = [s.model_dump() for s in spans]
    try:
        count = await collector.ingest(tenant.id, records)
        return IngestResponse(ingested=count, status="ok")
    except Exception as exc:
        logger.exception("Ingestion failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ── Dashboard ───────────────────────────────────────────────────────────


@router.get("/dashboard/stats", response_model=DashboardStatsPublic)
async def dashboard_stats(
    request: Request,
    tenant: CurrentTenant,
    _: Annotated[None, Depends(require_scope("read", "admin"))],
) -> DashboardStatsPublic:
    await enforce_tenant_rate_limit(request, tenant, "read")
    async with get_session() as session:
        svc = TelemetryService(session)
        metrics = await svc.get_metrics(tenant.id)

        total_traces = metrics["total_traces"]
        total_failures = metrics["total_failures"]
        failure_rate = round(total_failures / total_traces * 100, 2) if total_traces > 0 else 0.0

        fc_stmt = (
            select(FailureClassification.failure_type, func.count(FailureClassification.id))
            .where(FailureClassification.tenant_id == tenant.id)
            .group_by(FailureClassification.failure_type)
        )
        fc_result = await session.execute(fc_stmt)
        failures_by_type: dict[str, int] = {row[0]: row[1] for row in fc_result.all()}

        st_stmt = (
            select(Trace.status, func.count(Trace.id))
            .where(Trace.tenant_id == tenant.id)
            .group_by(Trace.status)
        )
        st_result = await session.execute(st_stmt)
        traces_by_status: dict[str, int] = {}
        for row in st_result.all():
            key = "success" if row[0] == "ok" else (row[0] or "unknown")
            traces_by_status[key] = row[1]

        cost_sum = await session.scalar(
            select(func.coalesce(func.sum(TokenCount.cost_usd), 0.0)).where(
                TokenCount.tenant_id == tenant.id
            )
        )

        svc_count = await session.scalar(
            select(func.count(func.distinct(Trace.service_name))).where(Trace.tenant_id == tenant.id)
        )

        return DashboardStatsPublic(
            total_traces=total_traces,
            total_failures=total_failures,
            failure_rate=failure_rate,
            avg_duration_ms=float(metrics["avg_trace_duration_ms"]),
            total_cost_usd=round(float(cost_sum), 4),
            active_services=int(svc_count or 0),
            failures_by_type=failures_by_type,
            traces_by_status=traces_by_status,
        )


@router.get("/dashboard/stats/legacy", response_model=DashboardStatsResponse)
async def dashboard_stats_legacy(
    request: Request,
    tenant: CurrentTenant,
    _: Annotated[None, Depends(require_scope("read", "admin"))],
) -> DashboardStatsResponse:
    """Original aggregate shape (includes remediation breakdown)."""
    await enforce_tenant_rate_limit(request, tenant, "read")
    async with get_session() as session:
        svc = TelemetryService(session)
        metrics = await svc.get_metrics(tenant.id)

        total_traces = metrics["total_traces"]
        total_failures = metrics["total_failures"]
        failure_rate = round(total_failures / total_traces * 100, 2) if total_traces > 0 else 0.0

        fc_stmt = (
            select(FailureClassification.failure_type, func.count(FailureClassification.id))
            .where(FailureClassification.tenant_id == tenant.id)
            .group_by(FailureClassification.failure_type)
        )
        fc_result = await session.execute(fc_stmt)
        failures_by_type = [FailureBreakdown(failure_type=row[0], count=row[1]) for row in fc_result.all()]

        ra_stmt = (
            select(
                RemediationAction.action_type,
                func.count(RemediationAction.id).label("total"),
                func.sum(case((RemediationAction.status == "success", 1), else_=0)).label("successful"),
            )
            .where(RemediationAction.tenant_id == tenant.id)
            .group_by(RemediationAction.action_type)
        )
        ra_result = await session.execute(ra_stmt)
        remediation_stats = []
        for row in ra_result.all():
            total = row.total
            successful = int(row.successful or 0)
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


# ── Alerts (read rules for dashboard) ───────────────────────────────────


@router.get("/alerts", response_model=AlertRulesResponsePublic)
async def list_alert_rules(
    request: Request,
    tenant: CurrentTenant,
    _: Annotated[None, Depends(require_scope("read", "admin"))],
) -> AlertRulesResponsePublic:
    await enforce_tenant_rate_limit(request, tenant, "read")
    async with get_session() as session:
        stmt = select(AlertRule).where(AlertRule.tenant_id == tenant.id)
        res = await session.execute(stmt)
        rules = list(res.scalars().all())
    items = [
        AlertRulePublic(
            id=str(r.id),
            failure_type=r.failure_type,
            threshold=float(r.min_confidence),
            channels=[str(r.channel_id)],
            enabled=r.is_active,
            created_at=r.created_at,
        )
        for r in rules
    ]
    return AlertRulesResponsePublic(items=items)
