"""Collector service — ingests span records and persists them to PostgreSQL."""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from otelmind.collector.span_processor import SpanProcessor
from otelmind.db import get_session


class Collector:
    """Collects span records from the instrumentor and flushes them to the DB."""

    def __init__(self) -> None:
        self._buffer: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()

    async def ingest(self, span_records: list[dict[str, Any]]) -> int:
        """Add span records to the buffer and flush to the database.

        Returns the number of spans persisted.
        """
        async with self._lock:
            self._buffer.extend(span_records)
            return await self._flush()

    async def _flush(self) -> int:
        """Flush buffered spans to PostgreSQL."""
        if not self._buffer:
            return 0

        batch = list(self._buffer)
        self._buffer.clear()

        try:
            async with get_session() as session:
                processor = SpanProcessor(session)
                count = await processor.process_spans(batch)
                logger.info("Flushed {} spans to database", count)
                return count
        except Exception:
            # Put records back on failure so they can be retried
            self._buffer.extend(batch)
            logger.exception("Failed to flush spans — {} records re-buffered", len(batch))
            raise

    @property
    def buffer_size(self) -> int:
        return len(self._buffer)


# Module-level singleton
collector = Collector()
