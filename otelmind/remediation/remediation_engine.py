"""Automated remediation engine — responds to detected failures."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from otelmind.config import settings
from otelmind.storage.models import FailureClassification
from otelmind.storage.telemetry_service import TelemetryService

# Map failure types to default remediation strategies
FAILURE_STRATEGY_MAP: dict[str, str] = {
    "tool_timeout": "retry_node",
    "infinite_loop": "reduce_context",
    "tool_misuse": "switch_tool",
    "context_overflow": "reduce_context",
    "hallucination": "retry_node",
}


class RemediationEngine:
    """Selects and executes remediation strategies for detected failures."""

    def __init__(self, session: AsyncSession) -> None:
        self._svc = TelemetryService(session)

    async def remediate(self, failure: FailureClassification) -> None:
        """Determine and execute the appropriate remediation for a failure."""
        action_type = FAILURE_STRATEGY_MAP.get(failure.failure_type, "notify_webhook")
        parameters = self._build_parameters(failure, action_type)

        action = await self._svc.record_remediation(
            failure_id=failure.id,
            trace_id=failure.trace_id,
            action_type=action_type,
            parameters=parameters,
        )

        try:
            result = await self._execute(action_type, failure, parameters)
            await self._svc.update_remediation_status(
                remediation_id=action.id,
                status="success",
                result=result,
                executed_at=datetime.now(UTC),
            )
            logger.info(
                "Remediation {} executed successfully for trace {}",
                action_type,
                failure.trace_id,
            )
        except Exception as exc:
            await self._svc.update_remediation_status(
                remediation_id=action.id,
                status="failed",
                result={"error": str(exc)},
                executed_at=datetime.now(UTC),
            )
            logger.error(
                "Remediation {} failed for trace {}: {}",
                action_type,
                failure.trace_id,
                exc,
            )

    def _build_parameters(self, failure: FailureClassification, action_type: str) -> dict[str, Any]:
        """Build remediation parameters based on failure evidence."""
        params: dict[str, Any] = {
            "failure_type": failure.failure_type,
            "confidence": failure.confidence,
        }
        evidence = failure.evidence or {}

        if action_type == "retry_node":
            params["max_retries"] = settings.remediation_max_retries
            params["target_span"] = evidence.get("span_id")

        elif action_type == "reduce_context":
            params["reduction_strategy"] = "truncate_oldest"
            params["target_token_count"] = 100_000

        elif action_type == "switch_tool":
            params["failed_tool"] = evidence.get("error_spans", [{}])[0].get("name", "unknown")

        elif action_type == "notify_webhook":
            params["webhook_url"] = settings.remediation_webhook_url

        return params

    async def _execute(
        self,
        action_type: str,
        failure: FailureClassification,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute the remediation action."""
        handler = {
            "retry_node": self._execute_retry,
            "switch_tool": self._execute_switch_tool,
            "reduce_context": self._execute_reduce_context,
            "notify_webhook": self._execute_notify_webhook,
        }.get(action_type)

        if handler is None:
            return {"status": "no_handler", "action_type": action_type}

        return await handler(failure, parameters)

    async def _execute_retry(
        self, failure: FailureClassification, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Retry remediation — records intent (actual re-execution is external)."""
        return {
            "action": "retry_node",
            "trace_id": failure.trace_id,
            "max_retries": params.get("max_retries", 3),
            "target_span": params.get("target_span"),
            "status": "retry_scheduled",
        }

    async def _execute_switch_tool(
        self, failure: FailureClassification, params: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "action": "switch_tool",
            "trace_id": failure.trace_id,
            "failed_tool": params.get("failed_tool"),
            "status": "tool_switch_recommended",
        }

    async def _execute_reduce_context(
        self, failure: FailureClassification, params: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "action": "reduce_context",
            "trace_id": failure.trace_id,
            "strategy": params.get("reduction_strategy", "truncate_oldest"),
            "target_tokens": params.get("target_token_count", 100_000),
            "status": "context_reduction_recommended",
        }

    async def _execute_notify_webhook(
        self, failure: FailureClassification, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Send failure notification to configured webhook."""
        webhook_url = params.get("webhook_url") or settings.remediation_webhook_url
        if not webhook_url:
            return {"status": "skipped", "reason": "no_webhook_url_configured"}

        payload = {
            "event": "failure_detected",
            "trace_id": failure.trace_id,
            "failure_type": failure.failure_type,
            "confidence": failure.confidence,
            "evidence": failure.evidence,
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(webhook_url, json=payload)
                return {
                    "status": "notified",
                    "webhook_status_code": resp.status_code,
                }
        except httpx.HTTPError as exc:
            return {"status": "webhook_failed", "error": str(exc)}
