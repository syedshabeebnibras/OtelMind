"""Create the default tenant and a bootstrap API key (prints raw key once)."""

from __future__ import annotations

import asyncio
import sys
import uuid

from sqlalchemy import select

from otelmind.config import settings
from otelmind.db import get_session
from otelmind.storage.models import ApiKey, Tenant


async def _run() -> int:
    async with get_session() as session:
        res = await session.execute(select(Tenant).where(Tenant.slug == "default"))
        tenant = res.scalar_one_or_none()
        if tenant is None:
            tenant = Tenant(
                id=uuid.uuid4(),
                name="Default",
                slug="default",
                plan="free",
                retention_days=30,
            )
            session.add(tenant)
            await session.flush()

        res = await session.execute(
            select(ApiKey).where(ApiKey.tenant_id == tenant.id, ApiKey.name == "bootstrap")
        )
        if res.scalar_one_or_none():
            print("Bootstrap API key already exists for tenant 'default'.", file=sys.stderr)
            return 1

        raw, key_hash = ApiKey.generate_key(settings.api_key_prefix)
        session.add(
            ApiKey(
                id=uuid.uuid4(),
                tenant_id=tenant.id,
                name="bootstrap",
                key_hash=key_hash,
                key_prefix=raw[:12],
                scopes=["ingest", "read", "admin"],
            )
        )

    print(raw)
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
