"""Partition maintenance — ensures monthly child tables exist and drops
expired ones per tenant retention policy.

The 003 migration created two PL/pgSQL helpers:
    otelmind_ensure_month_partition(parent, month_start date)
    otelmind_drop_expired_partitions(parent, cutoff date)

This module is the Python-side driver that calls them. It is invoked:

1. From the API `lifespan` on startup — guarantees the current and next
   month have a partition before any INSERT lands.
2. From a scheduled task (cron / Celery beat / APScheduler) once a day
   to roll the window forward and reclaim storage from old partitions.

Retention is resolved per tenant: the oldest kept partition is the month
that contains `now() - tenant.retention_days`. Because partitioning is
keyed on created_at and rows for different tenants share the same monthly
child, we drop by the *longest* retention across all tenants — a tenant
with a shorter plan can still have its rows scrubbed by a lightweight
tombstone job (future work). For the common "all tenants on 30d/365d"
deployment this is effectively free.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from loguru import logger
from sqlalchemy import select, text

from otelmind.db import get_session
from otelmind.storage.models import Tenant

PARTITIONED_TABLES: tuple[str, ...] = (
    "traces",
    "spans",
    "token_counts",
    "failure_classifications",
    "audit_logs",
)


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _add_months(d: date, n: int) -> date:
    y, m = d.year, d.month + n
    while m > 12:
        y += 1
        m -= 12
    while m < 1:
        y -= 1
        m += 12
    return date(y, m, 1)


async def ensure_partitions(months_ahead: int = 2, months_behind: int = 1) -> int:
    """Create partitions covering [now - months_behind, now + months_ahead].

    Idempotent — the underlying SQL function uses IF NOT EXISTS. Returns
    the total number of (table, month) ensure calls issued, for logging.
    """
    today = datetime.now(UTC).date()
    anchor = _month_start(today)
    months = [_add_months(anchor, delta) for delta in range(-months_behind, months_ahead + 1)]

    count = 0
    async with get_session() as session:
        for table in PARTITIONED_TABLES:
            for month_start in months:
                await session.execute(
                    text("SELECT otelmind_ensure_month_partition(:t, :m)"),
                    {"t": table, "m": month_start},
                )
                count += 1
    logger.info(
        "partitioning: ensured {} child partitions across {} tables",
        count,
        len(PARTITIONED_TABLES),
    )
    return count


async def drop_expired_partitions(min_retention_days: int | None = None) -> dict[str, int]:
    """Drop monthly partitions whose upper bound is <= the cutoff date.

    The cutoff is derived from the *maximum* retention configured across
    all tenants (so no tenant's data is dropped early). If no tenants
    exist, or `min_retention_days` is passed, that value wins.

    Returns {table_name: partitions_dropped}.
    """
    async with get_session() as session:
        if min_retention_days is None:
            max_retention = await session.scalar(
                select(Tenant.retention_days)
                .order_by(Tenant.retention_days.desc())
                .limit(1)
            )
            retention = int(max_retention or 30)
        else:
            retention = int(min_retention_days)

        cutoff = (datetime.now(UTC).date() - timedelta(days=retention)).replace(day=1)

        results: dict[str, int] = {}
        for table in PARTITIONED_TABLES:
            dropped = await session.scalar(
                text("SELECT otelmind_drop_expired_partitions(:t, :c)"),
                {"t": table, "c": cutoff},
            )
            results[table] = int(dropped or 0)

    logger.info(
        "partitioning: dropped expired partitions cutoff={} results={}",
        cutoff.isoformat(),
        results,
    )
    return results
