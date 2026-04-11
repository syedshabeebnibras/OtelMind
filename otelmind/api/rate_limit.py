"""Per-tenant sliding-window rate limits (Redis). Used as an explicit route dependency."""

from __future__ import annotations

import time
from typing import Literal

from fastapi import HTTPException, Request
from loguru import logger

from otelmind.config import settings
from otelmind.storage.models import Tenant

Bucket = Literal["ingest", "read"]


async def enforce_tenant_rate_limit(request: Request, tenant: Tenant, bucket: Bucket) -> None:
    """Raise 429 if the tenant exceeded their plan limit for this bucket."""
    try:
        import redis.asyncio as redis
    except Exception:
        return

    r = redis.from_url(settings.redis_url, decode_responses=True)
    try:
        limit = settings.rate_limit_ingest if bucket == "ingest" else settings.rate_limit_read
        window = 60
        key = f"ratelimit:{tenant.id}:{bucket}"
        pipe = r.pipeline()
        now = int(time.time())
        window_start = now - window
        pipe.zremrangebyscore(key, 0, window_start)
        pipe.zadd(key, {str(now): now})
        pipe.zcard(key)
        pipe.expire(key, window + 5)
        results = await pipe.execute()
        count = results[2]
        if count > limit:
            logger.warning("Rate limit exceeded tenant={} bucket={} count={}", tenant.slug, bucket, count)
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded. Slow down.",
                headers={"Retry-After": "60", "X-RateLimit-Limit": str(limit)},
            )
    except HTTPException:
        raise
    except Exception as exc:
        logger.debug("Rate limit check skipped: {}", exc)
    finally:
        await r.aclose()
