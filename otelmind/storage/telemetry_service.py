"""Data access layer for telemetry storage operations."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from otelmind.storage.models import (
    FailureClassification,
    RemediationAction,
    Span,
    TokenCount,
    ToolError,
    Trace,
)


class TelemetryService:
    """Async service for persisting and querying telemetry data."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Traces ──────────────────────────────────────────────────────────

    async def create_trace(
        self,
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
        logger.debug("Created trace {}", trace_id)
        return trace

    async def get_trace(self, trace_id: str) -> Trace | None:
        stmt = select(Trace).where(Trace.trace_id == trace_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_traces(self, *, limit: int = 50, offset: int = 0) -> list[Trace]:
        stmt = select(Trace).order_by(Trace.start_time.desc()).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # ── Spans ───────────────────────────────────────────────────────────

    async def create_span(
        self,
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
        logger.debug("Created span {} for trace {}", span_id, trace_id)
        return span

    async def list_spans(
        self,
        *,
        trace_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Span]:
        stmt = select(Span).order_by(Span.start_time.desc()).limit(limit).offset(offset)
        if trace_id:
            stmt = stmt.where(Span.trace_id == trace_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # ── Token Counts ────────────────────────────────────────────────────

    async def record_token_usage(
        self,
        trace_id: str,
        model_name: str,
        prompt_tokens: int,
        completion_tokens: int,
        *,
        span_id: str | None = None,
    ) -> TokenCount:
        tc = TokenCount(
            id=uuid.uuid4(),
            trace_id=trace_id,
            span_id=span_id,
            model_name=model_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
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
        trace_id: str,
        failure_type: str,
        confidence: float,
        *,
        evidence: dict[str, Any] | None = None,
        detection_method: str = "heuristic",
    ) -> FailureClassification:
        fc = FailureClassification(
            id=uuid.uuid4(),
            trace_id=trace_id,
            failure_type=failure_type,
            confidence=confidence,
            evidence=evidence,
            detection_method=detection_method,
        )
        self._session.add(fc)
        await self._session.flush()
        logger.info(
            "Recorded failure {} for trace {} (conf={:.2f})", failure_type, trace_id, confidence
        )
        return fc

    async def list_failures(
        self, *, limit: int = 50, offset: int = 0
    ) -> list[FailureClassification]:
        stmt = (
            select(FailureClassification)
            .order_by(FailureClassification.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # ── Remediation ─────────────────────────────────────────────────────

    async def record_remediation(
        self,
        failure_id: uuid.UUID,
        trace_id: str,
        action_type: str,
        *,
        parameters: dict[str, Any] | None = None,
    ) -> RemediationAction:
        ra = RemediationAction(
            id=uuid.uuid4(),
            failure_id=failure_id,
            trace_id=trace_id,
            action_type=action_type,
            parameters=parameters,
        )
        self._session.add(ra)
        await self._session.flush()
        logger.info("Recorded remediation {} for failure {}", action_type, failure_id)
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

    async def get_metrics(self) -> dict[str, Any]:
        """Aggregate metrics across all telemetry data."""
        trace_count = await self._session.scalar(select(func.count(Trace.id)))
        span_count = await self._session.scalar(select(func.count(Span.id)))
        failure_count = await self._session.scalar(select(func.count(FailureClassification.id)))
        error_count = await self._session.scalar(select(func.count(ToolError.id)))

        avg_duration = await self._session.scalar(select(func.avg(Trace.duration_ms)))
        total_tokens = await self._session.scalar(select(func.sum(TokenCount.total_tokens)))

        return {
            "total_traces": trace_count or 0,
            "total_spans": span_count or 0,
            "total_failures": failure_count or 0,
            "total_tool_errors": error_count or 0,
            "avg_trace_duration_ms": round(avg_duration, 2) if avg_duration else 0.0,
            "total_tokens_consumed": total_tokens or 0,
        }
