"""Create the default tenant and a bootstrap API key.

The raw key is written to a 0600-permissioned file (path printed to
stderr) rather than echoed to stdout, so it doesn't end up in shell
history, terminal scrollback buffers, or CI logs by accident.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
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

    # Write the key to a private file in $XDG_RUNTIME_DIR (or temp) with
    # restrictive permissions, then print only the path to stderr.
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    fd, path = tempfile.mkstemp(prefix="otelmind-key-", suffix=".txt", dir=runtime_dir)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(raw + "\n")
    except Exception:
        os.close(fd)
        raise
    print(f"API key written to {path} (chmod 0600).", file=sys.stderr)
    print(f"Read once with: cat {path} && rm {path}", file=sys.stderr)
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
