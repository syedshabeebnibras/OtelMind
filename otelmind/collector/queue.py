"""Redis Streams span queue — replaces the in-memory buffer.

Spans are published to a Redis Stream and consumed by worker tasks.
This ensures spans survive process crashes before flush.

Stream layout:
  otelmind:spans:{tenant_id}  →  {"data": "<json>"}

Consumer group: "writers"
"""

from __future__ import annotations

import contextlib
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_STREAM_PREFIX = "otelmind:spans"
_GROUP = "writers"
_DEAD_LETTER = "otelmind:spans:dlq"
_MAX_STREAM_LEN = 500_000
_MAX_RETRIES = 3


class SpanQueue:
    """Publish/consume span batches via Redis Streams."""

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._redis: Any = None

    async def _get_redis(self) -> Any:
        if self._redis is None:
            import redis.asyncio as redis

            self._redis = redis.from_url(self._redis_url)
        return self._redis

    def _stream_key(self, tenant_id: str) -> str:
        return f"{_STREAM_PREFIX}:{tenant_id}"

    async def publish(self, tenant_id: str, spans: list[dict[str, Any]]) -> int:
        """Publish spans to the tenant's stream. Returns count published."""
        r = await self._get_redis()
        pipe = r.pipeline()
        for span in spans:
            pipe.xadd(
                self._stream_key(tenant_id),
                {"data": json.dumps(span)},
                maxlen=_MAX_STREAM_LEN,
                approximate=True,
            )
        await pipe.execute()
        return len(spans)

    async def ensure_group(self, tenant_id: str) -> None:
        """Create the consumer group if it doesn't exist."""
        r = await self._get_redis()
        with contextlib.suppress(Exception):
            await r.xgroup_create(self._stream_key(tenant_id), _GROUP, id="0", mkstream=True)

    async def consume(self, tenant_id: str, batch_size: int = 100) -> list[tuple[str, dict]]:
        """Read up to batch_size undelivered messages. Returns [(msg_id, span_dict)]."""
        r = await self._get_redis()
        await self.ensure_group(tenant_id)
        messages = await r.xreadgroup(
            _GROUP,
            f"worker-{tenant_id[:8]}",
            {self._stream_key(tenant_id): ">"},
            count=batch_size,
            block=2000,
        )
        if not messages:
            return []
        results = []
        for _stream, entries in messages:
            for msg_id, fields in entries:
                try:
                    span_data = json.loads(
                        fields[b"data"]
                        if isinstance(fields.get(b"data"), bytes)
                        else fields["data"]
                    )
                    results.append((msg_id, span_data))
                except (json.JSONDecodeError, KeyError) as exc:
                    logger.warning("Malformed span message %s: %s", msg_id, exc)
        return results

    async def ack(self, tenant_id: str, message_ids: list[str]) -> None:
        """Acknowledge processed messages."""
        if not message_ids:
            return
        r = await self._get_redis()
        await r.xack(self._stream_key(tenant_id), _GROUP, *message_ids)

    async def move_to_dlq(self, tenant_id: str, message_id: str, span: dict) -> None:
        """Move a repeatedly failing message to the dead-letter queue."""
        r = await self._get_redis()
        await r.xadd(_DEAD_LETTER, {"tenant_id": tenant_id, "data": json.dumps(span)})
        await self.ack(tenant_id, [message_id])
        logger.error("Span moved to DLQ — tenant=%s msg_id=%s", tenant_id, message_id)

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()


class InMemorySpanQueue:
    """Fallback queue when Redis is unavailable — same interface as SpanQueue."""

    def __init__(self) -> None:
        self._buffers: dict[str, list[tuple[str, dict]]] = {}
        self._counter = 0

    async def publish(self, tenant_id: str, spans: list[dict]) -> int:
        if tenant_id not in self._buffers:
            self._buffers[tenant_id] = []
        for span in spans:
            self._counter += 1
            self._buffers[tenant_id].append((str(self._counter), span))
        return len(spans)

    async def ensure_group(self, tenant_id: str) -> None:
        pass

    async def consume(self, tenant_id: str, batch_size: int = 100) -> list[tuple[str, dict]]:
        buf = self._buffers.get(tenant_id, [])
        batch, self._buffers[tenant_id] = buf[:batch_size], buf[batch_size:]
        return batch

    async def ack(self, tenant_id: str, message_ids: list[str]) -> None:
        pass

    async def move_to_dlq(self, tenant_id: str, message_id: str, span: dict) -> None:
        logger.error("DLQ (in-memory): tenant=%s span=%s", tenant_id, span)

    async def close(self) -> None:
        pass


async def build_queue(redis_url: str) -> SpanQueue | InMemorySpanQueue:
    """Return a Redis-backed queue, falling back to in-memory if Redis is unavailable."""
    queue: SpanQueue | InMemorySpanQueue
    try:
        import redis.asyncio as redis

        r: Any = redis.from_url(redis_url)
        await r.ping()
        await r.aclose()
        queue = SpanQueue(redis_url)
        logger.info("SpanQueue: using Redis Streams (%s)", redis_url)
    except Exception as exc:
        logger.warning("Redis unavailable (%s) — falling back to in-memory queue", exc)
        queue = InMemorySpanQueue()
    return queue
