"""Data access layer for telemetry storage operations."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from loguru import logger
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from otelmind.cost.pricing import calculate_cost, detect_provider
from otelmind.storage.models import (
    FailureClassification,
    RemediationAction,
    Span,
    TokenCount,
    ToolError,
    Trace,
)


class TelemetryService:
    """Async service for persisting and querying telemetry data (tenant-isolated)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Traces ──────────────────────────────────────────────────────────

    async def create_trace(
        self,
        tenant_id: uuid.UUID,
        trace_id: str,
        service_name: str,
        start_time: datetime,
        *,
        end_time: datetime | None = None,
        duration_ms: float | None = None,
        status: str = "ok",
        metadata: dict[str, Any] | None = None,
    ) -> Trace:
        trace = Trace(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            trace_id=trace_id,
            service_name=service_name,
            status=status,
            start_time=start_time,
            end_time=end_time,
            duration_ms=duration_ms,
            metadata_=metadata,
        )
        self._session.add(trace)
        await self._session.flush()
        logger.debug("Created trace {} tenant={}", trace_id, tenant_id)
        return trace

    async def get_trace(self, tenant_id: uuid.UUID, trace_id: str) -> Trace | None:
        stmt = select(Trace).where(Trace.tenant_id == tenant_id, Trace.trace_id == trace_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    def _trace_filter_clause(
        self,
        tenant_id: uuid.UUID,
        *,
        service_name: str | None = None,
        ui_status: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ):
        parts: list[Any] = [Trace.tenant_id == tenant_id]
        if service_name:
            parts.append(Trace.service_name.ilike(f"%{service_name}%"))
        if ui_status == "success":
            parts.append(Trace.status == "ok")
        elif ui_status == "error":
            parts.append(Trace.status == "error")
        elif ui_status == "running":
            parts.append(Trace.end_time.is_(None))
        elif ui_status == "warning":
            parts.append(Trace.status == "warning")
        if start_time:
            parts.append(Trace.start_time >= start_time)
        if end_time:
            parts.append(Trace.start_time <= end_time)
        return and_(*parts)

    async def count_traces(
        self,
        tenant_id: uuid.UUID,
        *,
        service_name: str | None = None,
        ui_status: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> int:
        flt = self._trace_filter_clause(
            tenant_id,
            service_name=service_name,
            ui_status=ui_status,
            start_time=start_time,
            end_time=end_time,
        )
        stmt = select(func.count(Trace.id)).where(flt)
        result = await self._session.scalar(stmt)
        return int(result or 0)

    async def list_traces(
        self,
        tenant_id: uuid.UUID,
        *,
        limit: int = 50,
        offset: int = 0,
        service_name: str | None = None,
        ui_status: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Trace]:
        flt = self._trace_filter_clause(
            tenant_id,
            service_name=service_name,
            ui_status=ui_status,
            start_time=start_time,
            end_time=end_time,
        )
        stmt = (
            select(Trace)
            .where(flt)
            .order_by(Trace.start_time.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # ── Spans ───────────────────────────────────────────────────────────

    async def create_span(
        self,
        tenant_id: uuid.UUID,
        span_id: str,
        trace_id: str,
        name: str,
        start_time: datetime,
        *,
        parent_span_id: str | None = None,
        kind: str = "INTERNAL",
        status_code: str = "OK",
        end_time: datetime | None = None,
        duration_ms: float | None = None,
        attributes: dict[str, Any] | None = None,
        events: list[dict[str, Any]] | None = None,
        inputs: dict[str, Any] | None = None,
        outputs: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> Span:
        span = Span(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            span_id=span_id,
            trace_id=trace_id,
            parent_span_id=parent_span_id,
            name=name,
            kind=kind,
            status_code=status_code,
            start_time=start_time,
            end_time=end_time,
            duration_ms=duration_ms,
            attributes=attributes,
            events=events,
            inputs=inputs,
            outputs=outputs,
            error_message=error_message,
        )
        self._session.add(span)
        await self._session.flush()
        logger.debug("Created span {} trace {} tenant={}", span_id, trace_id, tenant_id)
        return span

    async def list_spans(
        self,
        tenant_id: uuid.UUID,
        *,
        trace_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Span]:
        stmt = (
            select(Span)
            .where(Span.tenant_id == tenant_id)
            .order_by(Span.start_time.desc())
            .limit(limit)
            .offset(offset)
        )
        if trace_id:
            stmt = stmt.where(Span.trace_id == trace_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # ── Token Counts ────────────────────────────────────────────────────

    async def record_token_usage(
        self,
        tenant_id: uuid.UUID,
        trace_id: str,
        model_name: str,
        prompt_tokens: int,
        completion_tokens: int,
        *,
        span_id: str | None = None,
    ) -> TokenCount:
        provider = detect_provider(model_name)
        cost = calculate_cost(model_name, prompt_tokens, completion_tokens)
        tc = TokenCount(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            trace_id=trace_id,
            span_id=span_id,
            model_name=model_name,
            model_provider=provider,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            cost_usd=cost,
        )
        self._session.add(tc)
        await self._session.flush()
        return tc

    # ── Tool Errors ─────────────────────────────────────────────────────

    async def record_tool_error(
        self,
        span_id: str,
        tool_name: str,
        error_type: str,
        error_message: str,
        *,
        stack_trace: str | None = None,
    ) -> ToolError:
        te = ToolError(
            id=uuid.uuid4(),
            span_id=span_id,
            tool_name=tool_name,
            error_type=error_type,
            error_message=error_message,
            stack_trace=stack_trace,
        )
        self._session.add(te)
        await self._session.flush()
        return te

    # ── Failures ────────────────────────────────────────────────────────

    async def record_failure(
        self,
        tenant_id: uuid.UUID,
        trace_id: str,
        failure_type: str,
        confidence: float,
        *,
        evidence: dict[str, Any] | None = None,
        detection_method: str = "heuristic",
    ) -> FailureClassification:
        fc = FailureClassification(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            trace_id=trace_id,
            failure_type=failure_type,
            confidence=confidence,
            evidence=evidence,
            detection_method=detection_method,
        )
        self._session.add(fc)
        await self._session.flush()
        logger.info(
            "Recorded failure {} trace {} tenant={} (conf={:.2f})",
            failure_type,
            trace_id,
            tenant_id,
            confidence,
        )
        return fc

    async def list_failures(
        self, tenant_id: uuid.UUID, *, limit: int = 50, offset: int = 0
    ) -> list[FailureClassification]:
        stmt = (
            select(FailureClassification)
            .where(FailureClassification.tenant_id == tenant_id)
            .order_by(FailureClassification.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # ── Remediation ─────────────────────────────────────────────────────

    async def record_remediation(
        self,
        tenant_id: uuid.UUID,
        failure_id: uuid.UUID,
        trace_id: str,
        action_type: str,
        *,
        parameters: dict[str, Any] | None = None,
    ) -> RemediationAction:
        ra = RemediationAction(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            failure_id=failure_id,
            trace_id=trace_id,
            action_type=action_type,
            parameters=parameters,
        )
        self._session.add(ra)
        await self._session.flush()
        logger.info("Recorded remediation {} failure {} tenant={}", action_type, failure_id, tenant_id)
        return ra

    async def update_remediation_status(
        self,
        remediation_id: uuid.UUID,
        status: str,
        *,
        result: dict[str, Any] | None = None,
        executed_at: datetime | None = None,
    ) -> None:
        stmt = select(RemediationAction).where(RemediationAction.id == remediation_id)
        res = await self._session.execute(stmt)
        action = res.scalar_one_or_none()
        if action:
            action.status = status
            action.result = result
            action.executed_at = executed_at
            await self._session.flush()

    # ── Metrics ─────────────────────────────────────────────────────────

    async def get_metrics(self, tenant_id: uuid.UUID) -> dict[str, Any]:
        """Aggregate metrics for one tenant."""
        trace_count = await self._session.scalar(
            select(func.count(Trace.id)).where(Trace.tenant_id == tenant_id)
        )
        span_count = await self._session.scalar(
            select(func.count(Span.id)).where(Span.tenant_id == tenant_id)
        )
        failure_count = await self._session.scalar(
            select(func.count(FailureClassification.id)).where(
                FailureClassification.tenant_id == tenant_id
            )
        )
        error_count = await self._session.scalar(
            select(func.count(ToolError.id))
            .select_from(ToolError)
            .join(Span, ToolError.span_id == Span.span_id)
            .where(Span.tenant_id == tenant_id)
        )

        avg_duration = await self._session.scalar(
            select(func.avg(Trace.duration_ms)).where(Trace.tenant_id == tenant_id)
        )
        total_tokens = await self._session.scalar(
            select(func.sum(TokenCount.total_tokens)).where(TokenCount.tenant_id == tenant_id)
        )

        return {
            "total_traces": trace_count or 0,
            "total_spans": span_count or 0,
            "total_failures": failure_count or 0,
            "total_tool_errors": error_count or 0,
            "avg_trace_duration_ms": round(avg_duration, 2) if avg_duration else 0.0,
            "total_tokens_consumed": total_tokens or 0,
        }
