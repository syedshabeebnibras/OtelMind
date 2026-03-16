"""Escalation remediation strategy — sends alerts via webhook."""

from __future__ import annotations

from typing import Any

import httpx
from loguru import logger

from otelmind.config import settings
from otelmind.remediation.base import RemediationStrategy


class EscalateStrategy(RemediationStrategy):
    """Send a failure alert to a configured webhook endpoint.

    The webhook URL is read from ``settings.remediation_webhook_url`` and can
    be overridden via ``context["webhook_url"]``.

    The POST payload includes the full failure classification and any
    additional context supplied by the caller.
    """

    async def execute(
        self,
        classification: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        webhook_url: str = context.get("webhook_url") or settings.remediation_webhook_url

        if not webhook_url:
            logger.warning(
                "EscalateStrategy: no webhook URL configured for trace {}",
                classification.get("trace_id", "unknown"),
            )
            return {
                "status": "skipped",
                "reason": "no_webhook_url_configured",
                "trace_id": classification.get("trace_id"),
            }

        payload: dict[str, Any] = {
            "event": "failure_escalation",
            "trace_id": classification.get("trace_id"),
            "failure_type": classification.get("failure_type"),
            "confidence": classification.get("confidence"),
            "evidence": classification.get("evidence"),
            "context": {
                k: v
                for k, v in context.items()
                if k not in ("webhook_url",)
            },
        }

        timeout = context.get("timeout", 15.0)

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(webhook_url, json=payload)
                response.raise_for_status()

            logger.info(
                "EscalateStrategy: alert sent for trace {} — HTTP {}",
                classification.get("trace_id", "unknown"),
                response.status_code,
            )
            return {
                "status": "success",
                "trace_id": classification.get("trace_id"),
                "webhook_status_code": response.status_code,
                "webhook_url": webhook_url,
            }

        except httpx.TimeoutException as exc:
            logger.error(
                "EscalateStrategy: webhook timed out for trace {}: {}",
                classification.get("trace_id", "unknown"),
                exc,
            )
            return {
                "status": "failed",
                "trace_id": classification.get("trace_id"),
                "error": f"Webhook timed out: {exc}",
                "webhook_url": webhook_url,
            }

        except httpx.HTTPStatusError as exc:
            logger.error(
                "EscalateStrategy: webhook returned HTTP {} for trace {}",
                exc.response.status_code,
                classification.get("trace_id", "unknown"),
            )
            return {
                "status": "failed",
                "trace_id": classification.get("trace_id"),
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}",
                "webhook_status_code": exc.response.status_code,
                "webhook_url": webhook_url,
            }

        except httpx.HTTPError as exc:
            logger.error(
                "EscalateStrategy: webhook request failed for trace {}: {}",
                classification.get("trace_id", "unknown"),
                exc,
            )
            return {
                "status": "failed",
                "trace_id": classification.get("trace_id"),
                "error": str(exc),
                "webhook_url": webhook_url,
            }
