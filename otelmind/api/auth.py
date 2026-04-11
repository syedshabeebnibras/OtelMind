"""API key authentication and tenant resolution.

Every protected endpoint calls `require_api_key` as a FastAPI dependency.
The resolved Tenant object is injected into request.state and available
to all route handlers and middleware downstream.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import APIKeyHeader
from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from otelmind.db import get_session
from otelmind.storage.models import ApiKey, AuditLog, Tenant

_api_key_header = APIKeyHeader(name="x-api-key", auto_error=False)


def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def require_api_key(
    request: Request,
    raw_key: str | None = Security(_api_key_header),
) -> Tenant:
    """Resolve an API key to a Tenant. Raises 401 if invalid or revoked."""
    if not raw_key:
        raise HTTPException(status_code=401, detail="Missing x-api-key header")

    key_hash = _hash_key(raw_key)

    async with get_session() as session:
        stmt = (
            select(ApiKey)
            .where(ApiKey.key_hash == key_hash, ApiKey.revoked_at.is_(None))
            .options(selectinload(ApiKey.tenant))
        )
        result = await session.execute(stmt)
        api_key = result.scalar_one_or_none()

        if api_key is None or not api_key.tenant.is_active:
            raise HTTPException(status_code=401, detail="Invalid or revoked API key")

        api_key.last_used_at = datetime.now(UTC)

        if request.method not in ("GET", "HEAD", "OPTIONS"):
            audit = AuditLog(
                tenant_id=api_key.tenant_id,
                api_key_id=api_key.id,
                action=f"{request.method.lower()}.{request.url.path.strip('/').replace('/', '.')}",
                ip_address=_get_client_ip(request),
                user_agent=request.headers.get("user-agent"),
            )
            session.add(audit)

        tenant = api_key.tenant
        logger.debug("Auth OK — tenant={} key_prefix={}", tenant.slug, api_key.key_prefix)

    request.state.tenant = tenant
    request.state.tenant_id = tenant.id
    request.state.api_key = api_key
    return tenant


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


# Type alias for cleaner route signatures
CurrentTenant = Annotated[Tenant, Depends(require_api_key)]


def require_scope(*allowed: str):
    """Require the API key to include at least one of the given scopes (or admin/*)."""

    async def _check(request: Request, _tenant: Tenant = Depends(require_api_key)) -> None:
        ak = getattr(request.state, "api_key", None)
        if ak is None:
            raise HTTPException(status_code=401, detail="Not authenticated")
        scopes = set(ak.scopes or [])
        if not scopes:
            return
        if "admin" in scopes or "*" in scopes:
            return
        if not scopes.intersection(allowed):
            raise HTTPException(
                status_code=403,
                detail=f"Insufficient API key scope (need one of: {', '.join(allowed)})",
            )

    return _check


async def create_api_key(tenant_id: str, name: str, scopes: list[str]) -> tuple[str, ApiKey]:
    """Create a new API key for a tenant. Returns (raw_key, ApiKey model)."""
    import uuid

    from otelmind.config import settings

    raw_key, key_hash = ApiKey.generate_key(settings.api_key_prefix)
    async with get_session() as session:
        api_key = ApiKey(
            id=uuid.uuid4(),
            tenant_id=uuid.UUID(tenant_id),
            name=name,
            key_hash=key_hash,
            key_prefix=raw_key[:12],
            scopes=scopes,
        )
        session.add(api_key)
    return raw_key, api_key
