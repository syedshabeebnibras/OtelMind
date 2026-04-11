"""Alert router — fires the right channels for each failure based on tenant rules.

Flow:
  1. FailureClassification is persisted by the watchdog.
  2. AlertRouter.dispatch() is called with the failure + tenant context.
  3. It queries active AlertRules for the tenant.
  4. For each matching rule, it checks the dedup window in Redis.
  5. If not suppressed, it fires the configured AlertChannel.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from otelmind.alerting.channels.email import send_email_alert
from otelmind.alerting.channels.pagerduty import send_pagerduty_alert
from otelmind.alerting.channels.slack import send_slack_alert
from otelmind.config import settings
from otelmind.storage.models import AlertChannel, AlertRule, FailureClassification

logger = logging.getLogger(__name__)


class AlertRouter:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._redis: Any = None

    async def _get_redis(self) -> Any:
        if self._redis is None:
            try:
                import redis.asyncio as redis

                self._redis = redis.from_url(settings.redis_url)
            except Exception:
                pass
        return self._redis

    async def dispatch(
        self,
        failure: FailureClassification,
        service_name: str,
        reasoning: str,
    ) -> list[str]:
        """Fire all matching alert channels. Returns list of channel names notified."""
        # Load applicable rules
        stmt = (
            select(AlertRule)
            .where(
                AlertRule.tenant_id == failure.tenant_id,
                AlertRule.is_active.is_(True),
            )
            .options(selectinload(AlertRule.channel))
        )
        result = await self._session.execute(stmt)
        rules = list(result.scalars().all())

        notified: list[str] = []
        for rule in rules:
            if not self._matches(rule, failure):
                continue
            if await self._is_deduped(rule, failure):
                logger.debug("Alert suppressed by dedup — rule=%s", rule.id)
                continue

            fired = await self._fire_channel(rule.channel, failure, service_name, reasoning)
            if fired:
                notified.append(rule.channel.name)
                await self._record_dedup(rule, failure)

        return notified

    def _matches(self, rule: AlertRule, failure: FailureClassification) -> bool:
        type_match = rule.failure_type == "*" or rule.failure_type == failure.failure_type
        conf_match = failure.confidence >= rule.min_confidence
        return type_match and conf_match

    async def _is_deduped(self, rule: AlertRule, failure: FailureClassification) -> bool:
        r = await self._get_redis()
        if r is None:
            return False
        key = self._dedup_key(rule, failure.failure_type)
        exists = await r.exists(key)
        return bool(exists)

    async def _record_dedup(self, rule: AlertRule, failure: FailureClassification) -> None:
        r = await self._get_redis()
        if r is None:
            return
        key = self._dedup_key(rule, failure.failure_type)
        await r.setex(key, rule.dedup_window_seconds, "1")

    def _dedup_key(self, rule: AlertRule, failure_type: str) -> str:
        return f"alert:dedup:{rule.tenant_id}:{rule.channel_id}:{failure_type}"

    async def _fire_channel(
        self,
        channel: AlertChannel,
        failure: FailureClassification,
        service_name: str,
        reasoning: str,
    ) -> bool:
        cfg = channel.config
        trace_id = failure.trace_id
        ftype = failure.failure_type
        conf = failure.confidence

        if channel.channel_type == "slack":
            webhook = cfg.get("webhook_url", "")
            if not webhook:
                return False
            return await send_slack_alert(webhook, ftype, conf, trace_id, reasoning, service_name)

        if channel.channel_type == "pagerduty":
            key = cfg.get("routing_key", "")
            if not key:
                return False
            return await send_pagerduty_alert(key, ftype, conf, trace_id, reasoning, service_name)

        if channel.channel_type == "email":
            to = cfg.get("to", [])
            if not to:
                return False
            return await send_email_alert(
                settings.smtp_host,
                settings.smtp_port,
                settings.smtp_user,
                settings.smtp_password,
                settings.alert_email_from,
                to,
                ftype,
                conf,
                trace_id,
                reasoning,
                service_name,
            )

        logger.warning("Unknown channel type: %s", channel.channel_type)
        return False
