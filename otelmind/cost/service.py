"""Cost attribution service — queries and aggregates spend data per tenant."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from otelmind.cost.pricing import detect_provider
from otelmind.storage.models import TokenCount


class CostService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_breakdown(
        self,
        tenant_id: uuid.UUID,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        group_by: str = "model",
    ) -> dict[str, Any]:
        """Return cost breakdown grouped by model, provider, or day."""
        if start is None:
            start = datetime.now(UTC) - timedelta(days=30)
        if end is None:
            end = datetime.now(UTC)

        stmt = select(TokenCount).where(
            TokenCount.tenant_id == tenant_id,
            TokenCount.created_at >= start,
            TokenCount.created_at <= end,
        )
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())

        if group_by == "model":
            return self._group_by_model(rows)
        if group_by == "provider":
            return self._group_by_provider(rows)
        return self._group_by_day(rows, start, end)

    def _group_by_model(self, rows: list[TokenCount]) -> dict[str, Any]:
        acc: dict[str, dict] = {}
        for row in rows:
            key = row.model_name
            if key not in acc:
                acc[key] = {
                    "model": key,
                    "provider": detect_provider(key),
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "cost_usd": 0.0,
                }
            acc[key]["prompt_tokens"] += row.prompt_tokens
            acc[key]["completion_tokens"] += row.completion_tokens
            acc[key]["cost_usd"] += row.cost_usd

        items = sorted(acc.values(), key=lambda x: x["cost_usd"], reverse=True)
        total = sum(x["cost_usd"] for x in items)
        for item in items:
            item["cost_usd"] = round(item["cost_usd"], 6)

        return {
            "group_by": "model",
            "total_cost_usd": round(total, 6),
            "items": items,
        }

    def _group_by_provider(self, rows: list[TokenCount]) -> dict[str, Any]:
        acc: dict[str, dict] = {}
        for row in rows:
            key = detect_provider(row.model_name)
            if key not in acc:
                acc[key] = {
                    "provider": key,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "cost_usd": 0.0,
                }
            acc[key]["prompt_tokens"] += row.prompt_tokens
            acc[key]["completion_tokens"] += row.completion_tokens
            acc[key]["cost_usd"] += row.cost_usd

        items = sorted(acc.values(), key=lambda x: x["cost_usd"], reverse=True)
        total = sum(x["cost_usd"] for x in items)
        return {"group_by": "provider", "total_cost_usd": round(total, 6), "items": items}

    def _group_by_day(
        self, rows: list[TokenCount], start: datetime, end: datetime
    ) -> dict[str, Any]:
        acc: dict[str, dict] = {}
        for row in rows:
            day = row.created_at.strftime("%Y-%m-%d")
            if day not in acc:
                acc[day] = {
                    "date": day,
                    "cost_usd": 0.0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                }
            acc[day]["cost_usd"] += row.cost_usd
            acc[day]["prompt_tokens"] += row.prompt_tokens
            acc[day]["completion_tokens"] += row.completion_tokens

        items = sorted(acc.values(), key=lambda x: x["date"])
        total = sum(x["cost_usd"] for x in items)
        return {"group_by": "day", "total_cost_usd": round(total, 6), "items": items}

    async def get_summary(self, tenant_id: uuid.UUID) -> dict[str, Any]:
        """Quick cost summary: this month, last month, projected."""
        now = datetime.now(UTC)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_month_start = (month_start - timedelta(days=1)).replace(day=1)

        current = await self._total_cost(tenant_id, month_start, now)
        previous = await self._total_cost(tenant_id, last_month_start, month_start)

        days_elapsed = (now - month_start).days + 1
        days_in_month = 30
        projected = (current / days_elapsed * days_in_month) if days_elapsed > 0 else 0.0

        return {
            "current_month_usd": round(current, 4),
            "last_month_usd": round(previous, 4),
            "projected_month_usd": round(projected, 4),
            "mom_change_pct": (
                round((current - previous) / previous * 100, 1) if previous > 0 else None
            ),
        }

    async def _total_cost(self, tenant_id: uuid.UUID, start: datetime, end: datetime) -> float:
        stmt = select(func.sum(TokenCount.cost_usd)).where(
            TokenCount.tenant_id == tenant_id,
            TokenCount.created_at >= start,
            TokenCount.created_at <= end,
        )
        result = await self._session.scalar(stmt)
        return float(result or 0.0)
