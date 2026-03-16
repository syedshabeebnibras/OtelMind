"""Batch Database Writer — efficiently writes processed spans to PostgreSQL.

Uses batch inserts via asyncpg for 10-50x better performance than individual INSERTs.
Accumulates spans in a buffer and flushes on a timer or when the buffer is full.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)


class BatchWriter:
    """Accumulates span records and flushes them to PostgreSQL periodically.

    Args:
        pool: asyncpg connection pool.
        batch_size: Flush when this many records accumulate. Default: 100.
        flush_interval: Flush every N seconds regardless of batch size. Default: 2.0.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        batch_size: int = 100,
        flush_interval: float = 2.0,
    ) -> None:
        self.pool = pool
        self.batch_size = batch_size
        self.flush_interval = flush_interval

        self._trace_buffer: list[dict[str, Any]] = []
        self._span_buffer: list[dict[str, Any]] = []
        self._token_buffer: list[dict[str, Any]] = []
        self._error_buffer: list[dict[str, Any]] = []

        self._lock = asyncio.Lock()
        self._flush_task: asyncio.Task[None] | None = None
        self._running = False

    async def start(self) -> None:
        """Start the background flush loop."""
        self._running = True
        self._flush_task = asyncio.create_task(self._flush_loop())
        logger.info(
            "BatchWriter started (batch_size=%d, flush_interval=%.1fs)",
            self.batch_size,
            self.flush_interval,
        )

    async def stop(self) -> None:
        """Stop the flush loop and flush any remaining data."""
        self._running = False
        if self._flush_task:
            self._flush_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._flush_task
        await self._flush()
        logger.info("BatchWriter stopped")

    async def write(self, processed: dict[str, Any]) -> None:
        """Add a processed span to the write buffer."""
        async with self._lock:
            self._trace_buffer.append(processed["trace"])
            self._span_buffer.append(processed["span"])
            if processed["tokens"]:
                self._token_buffer.append(processed["tokens"])
            if processed["error"]:
                self._error_buffer.append(processed["error"])

            if len(self._span_buffer) >= self.batch_size:
                await self._flush()

    async def _flush_loop(self) -> None:
        """Background task that flushes on a timer."""
        while self._running:
            await asyncio.sleep(self.flush_interval)
            await self._flush()

    async def _flush(self) -> None:
        """Write all buffered records to PostgreSQL."""
        async with self._lock:
            if not self._span_buffer:
                return

            traces = self._trace_buffer
            spans = self._span_buffer
            tokens = self._token_buffer
            errors = self._error_buffer

            self._trace_buffer = []
            self._span_buffer = []
            self._token_buffer = []
            self._error_buffer = []

        try:
            async with self.pool.acquire() as conn, conn.transaction():
                if traces:
                    await self._insert_traces(conn, traces)
                if spans:
                    await self._insert_spans(conn, spans)
                if tokens:
                    await self._insert_tokens(conn, tokens)
                if errors:
                    await self._insert_errors(conn, errors)

            logger.debug(
                "Flushed %d spans, %d token records, %d errors",
                len(spans),
                len(tokens),
                len(errors),
            )
        except Exception as e:
            logger.error("Failed to flush batch: %s", e)
            # Put records back for retry
            async with self._lock:
                self._trace_buffer = traces + self._trace_buffer
                self._span_buffer = spans + self._span_buffer
                self._token_buffer = tokens + self._token_buffer
                self._error_buffer = errors + self._error_buffer

    async def _insert_traces(self, conn: asyncpg.Connection, traces: list[dict[str, Any]]) -> None:
        await conn.executemany(
            """
            INSERT INTO traces (trace_id, service_name, started_at)
            VALUES ($1, $2, $3)
            ON CONFLICT (trace_id) DO NOTHING
            """,
            [(t["trace_id"], t["service_name"], t["started_at"]) for t in traces],
        )

    async def _insert_spans(self, conn: asyncpg.Connection, spans: list[dict[str, Any]]) -> None:
        await conn.executemany(
            """
            INSERT INTO spans (
                span_id, trace_id, parent_span_id, span_name,
                step_index, duration_ms, status_code,
                input_preview, output_preview, created_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            ON CONFLICT (span_id) DO NOTHING
            """,
            [
                (
                    s["span_id"],
                    s["trace_id"],
                    s["parent_span_id"],
                    s["span_name"],
                    s["step_index"],
                    s["duration_ms"],
                    s["status_code"],
                    s["input_preview"],
                    s["output_preview"],
                    s["created_at"],
                )
                for s in spans
            ],
        )

    async def _insert_tokens(self, conn: asyncpg.Connection, tokens: list[dict[str, Any]]) -> None:
        await conn.executemany(
            """
            INSERT INTO token_counts (
                span_id, trace_id, prompt_tokens,
                completion_tokens, model, created_at
            ) VALUES ($1, $2, $3, $4, $5, $6)
            """,
            [
                (
                    t["span_id"],
                    t["trace_id"],
                    t["prompt_tokens"],
                    t["completion_tokens"],
                    t["model"],
                    t["created_at"],
                )
                for t in tokens
            ],
        )

    async def _insert_errors(self, conn: asyncpg.Connection, errors: list[dict[str, Any]]) -> None:
        await conn.executemany(
            """
            INSERT INTO tool_errors (
                span_id, trace_id, tool_name,
                error_type, error_message, created_at
            ) VALUES ($1, $2, $3, $4, $5, $6)
            """,
            [
                (
                    e["span_id"],
                    e["trace_id"],
                    e["tool_name"],
                    e["error_type"],
                    e["error_message"],
                    e["created_at"],
                )
                for e in errors
            ],
        )
