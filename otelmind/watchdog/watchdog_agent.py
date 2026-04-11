"""Watchdog meta-agent — periodically scans traces for failures and triggers remediation."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy import select

from otelmind.alerting.alert_router import AlertRouter
from otelmind.config import settings
from otelmind.db import get_session
from otelmind.remediation.remediation_engine import RemediationEngine
from otelmind.storage.models import FailureClassification, Span, Trace
from otelmind.storage.telemetry_service import TelemetryService
from otelmind.watchdog.failure_detection import FailureDetector


class WatchdogAgent:
    """Background agent that monitors traces and detects operational failures."""

    def __init__(self) -> None:
        self._detector = FailureDetector()
        self._running = False
        self._last_check: datetime = datetime.now(UTC) - timedelta(hours=1)

    async def start(self) -> None:
        """Start the watchdog loop."""
        self._running = True
        logger.info(
            "Watchdog agent started — checking every {}s",
            settings.watchdog_interval_seconds,
        )
        while self._running:
            try:
                await self._scan_traces()
            except Exception:
                logger.exception("Watchdog scan failed")
            await asyncio.sleep(settings.watchdog_interval_seconds)

    def stop(self) -> None:
        """Signal the watchdog to stop."""
        self._running = False
        logger.info("Watchdog agent stopped")

    async def _scan_traces(self) -> None:
        """Scan recent traces for failures."""
        async with get_session() as session:
            svc = TelemetryService(session)

            # Get traces created since last check
            stmt = (
                select(Trace)
                .where(Trace.created_at >= self._last_check)
                .order_by(Trace.created_at.asc())
            )
            result = await session.execute(stmt)
            traces = list(result.scalars().all())

            if not traces:
                return

            logger.info("Watchdog scanning {} recent traces", len(traces))

            for trace_obj in traces:
                # Check if already classified
                existing = await session.execute(
                    select(FailureClassification).where(
                        FailureClassification.tenant_id == trace_obj.tenant_id,
                        FailureClassification.trace_id == trace_obj.trace_id,
                    )
                )
                if existing.scalars().first():
                    continue

                # Load spans for this trace
                span_stmt = select(Span).where(
                    Span.tenant_id == trace_obj.tenant_id,
                    Span.trace_id == trace_obj.trace_id,
                )
                span_result = await session.execute(span_stmt)
                spans = list(span_result.scalars().all())

                if not spans:
                    continue

                # Run detection
                failures = self._detector.analyze(trace_obj.trace_id, spans)

                for failure in failures:
                    fc = await svc.record_failure(
                        trace_obj.tenant_id,
                        trace_id=failure.trace_id,
                        failure_type=failure.failure_type,
                        confidence=failure.confidence,
                        evidence=failure.evidence,
                        detection_method=failure.detection_method,
                    )

                    try:
                        router_ar = AlertRouter(session)
                        reasoning = str((failure.evidence or {}).get("reasoning") or "")[:900]
                        if not reasoning:
                            reasoning = f"{failure.failure_type} (confidence {failure.confidence:.0%})"
                        await router_ar.dispatch(fc, trace_obj.service_name, reasoning)
                        fc.alerted = True
                    except Exception:
                        logger.exception("Alert dispatch failed for trace {}", failure.trace_id)

                    engine = RemediationEngine(session)
                    await engine.remediate(fc)

            self._last_check = datetime.now(UTC)


async def run_watchdog() -> None:
    """Entry point for running the watchdog as a standalone process."""
    agent = WatchdogAgent()
    try:
        await agent.start()
    except asyncio.CancelledError:
        agent.stop()
