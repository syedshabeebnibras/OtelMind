"""Collector service — ingests span records and persists them to PostgreSQL."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from loguru import logger

from otelmind.collector.span_processor import SpanProcessor
from otelmind.db import get_session


class Collector:
    """Collects span records from the instrumentor and flushes them to the DB."""

    def __init__(self) -> None:
        self._buffer: list[tuple[uuid.UUID, dict[str, Any]]] = []
        self._lock = asyncio.Lock()

    async def ingest(self, tenant_id: uuid.UUID, span_records: list[dict[str, Any]]) -> int:
        """Buffer span records for a tenant and flush to PostgreSQL.

        Returns the number of spans persisted.
        """
        async with self._lock:
            for rec in span_records:
                self._buffer.append((tenant_id, rec))
            return await self._flush()

    async def _flush(self) -> int:
        """Flush buffered spans to PostgreSQL (grouped by tenant per batch)."""
        if not self._buffer:
            return 0

        batch = list(self._buffer)
        self._buffer.clear()

        by_tenant: dict[uuid.UUID, list[dict[str, Any]]] = {}
        for tid, rec in batch:
            by_tenant.setdefault(tid, []).append(rec)

        total = 0
        try:
            async with get_session() as session:
                processor = SpanProcessor(session)
                for tid, records in by_tenant.items():
                    total += await processor.process_spans(tid, records)
            logger.info("Flushed {} spans to database", total)
            return total
        except Exception:
            self._buffer.extend(batch)
            logger.exception("Failed to flush spans — {} records re-buffered", len(batch))
            raise

    @property
    def buffer_size(self) -> int:
        return len(self._buffer)


# Module-level singleton
collector = Collector()
