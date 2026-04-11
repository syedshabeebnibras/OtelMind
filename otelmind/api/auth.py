"""API key authentication and tenant resolution.

Every protected endpoint calls `require_api_key` as a FastAPI dependency.
The resolved Tenant object is injected into request.state and available
to all route handlers and middleware downstream.

Two access-control primitives live here:

* `require_scope(*scopes)` — legacy coarse scope check on the API key.
  Preserved so existing routes keep working unchanged.
* `require_permission(perm)` — RBAC check that accepts either an API key
  whose scopes satisfy the permission *or* a user-session principal
  whose role carries the permission. New routes should use this.

Permissions use `<resource>:<action>` notation, e.g. `traces:read`,
`alerts:write`. A principal holding `*` or `admin` is granted everything.
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
from otelmind.storage.models import ApiKey, AuditLog, Role, Tenant, User, UserTenantRole

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

    async def _check(
        request: Request,
        _tenant: Annotated[Tenant, Depends(require_api_key)],
    ) -> None:
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


# ---------------------------------------------------------------------------
# RBAC — fine-grained permission checks
# ---------------------------------------------------------------------------


# Minimal mapping from coarse API-key scopes to the RBAC permission grid.
# An API key with scope "read" implicitly grants every `*:read` permission,
# so existing integrations keep working after the RBAC rollout.
_SCOPE_TO_PERMISSIONS: dict[str, frozenset[str]] = {
    "read": frozenset(
        {"traces:read", "failures:read", "cost:read", "evals:read", "alerts:read"}
    ),
    "ingest": frozenset({"traces:write", "spans:write"}),
    "write": frozenset({"traces:write", "alerts:write", "evals:write"}),
    "admin": frozenset({"*"}),
    "*": frozenset({"*"}),
}


def _permissions_from_api_key(api_key: ApiKey) -> set[str]:
    perms: set[str] = set()
    for scope in api_key.scopes or []:
        mapped = _SCOPE_TO_PERMISSIONS.get(scope)
        if mapped:
            perms.update(mapped)
        else:
            # Unknown scopes pass through verbatim — lets operators grant
            # brand-new permission strings on an API key without a code
            # change here.
            perms.add(scope)
    return perms


def _permission_satisfied(held: set[str], required: str) -> bool:
    if "*" in held or "admin" in held:
        return True
    if required in held:
        return True
    # `traces:*` grants every action on `traces`.
    resource = required.split(":", 1)[0]
    return f"{resource}:*" in held


async def _load_user_permissions(user_id: str, tenant_id: str) -> set[str]:
    """Resolve a user's effective permissions inside a tenant."""
    async with get_session() as session:
        stmt = (
            select(Role.permissions)
            .join(UserTenantRole, UserTenantRole.role_id == Role.id)
            .where(
                UserTenantRole.user_id == user_id,
                UserTenantRole.tenant_id == tenant_id,
            )
        )
        result = await session.execute(stmt)
        perms: set[str] = set()
        for row in result.all():
            for p in row[0] or []:
                perms.add(p)
        return perms


def require_permission(*required: str):
    """RBAC-aware check — accepts API key OR user session.

    The principal must satisfy **all** of the given permission strings
    (AND semantics). Use multiple calls or custom logic for OR semantics.
    """

    async def _check(
        request: Request,
        _tenant: Annotated[Tenant, Depends(require_api_key)],
    ) -> None:
        # API key path — translate scopes into permission tokens and
        # check containment. This is the hot path for the collector SDK.
        api_key: ApiKey | None = getattr(request.state, "api_key", None)
        if api_key is not None:
            held = _permissions_from_api_key(api_key)
            missing = [p for p in required if not _permission_satisfied(held, p)]
            if missing:
                raise HTTPException(
                    status_code=403,
                    detail=f"Missing permission(s): {', '.join(missing)}",
                )
            return

        # User session path — reserved for the dashboard when a cookie
        # session replaces the x-api-key header. require_api_key will
        # have raised already if neither principal is present, so this
        # branch is a safety net.
        user: User | None = getattr(request.state, "user", None)
        tenant: Tenant | None = getattr(request.state, "tenant", None)
        if user is None or tenant is None:
            raise HTTPException(status_code=401, detail="Not authenticated")

        held = await _load_user_permissions(str(user.id), str(tenant.id))
        missing = [p for p in required if not _permission_satisfied(held, p)]
        if missing:
            raise HTTPException(
                status_code=403,
                detail=f"Missing permission(s): {', '.join(missing)}",
            )

    return _check


async def attach_user_to_tenant(
    user_id: str,
    tenant_id: str,
    role_name: str,
) -> UserTenantRole:
    """Assign a user to a tenant with the named role. Upserts on conflict."""
    import uuid as _uuid

    async with get_session() as session:
        role = await session.scalar(select(Role).where(Role.name == role_name))
        if role is None:
            raise ValueError(f"Unknown role: {role_name}")

        existing = await session.scalar(
            select(UserTenantRole).where(
                UserTenantRole.user_id == _uuid.UUID(user_id),
                UserTenantRole.tenant_id == _uuid.UUID(tenant_id),
            )
        )
        if existing is not None:
            existing.role_id = role.id
            return existing

        assignment = UserTenantRole(
            id=_uuid.uuid4(),
            user_id=_uuid.UUID(user_id),
            tenant_id=_uuid.UUID(tenant_id),
            role_id=role.id,
        )
        session.add(assignment)
        return assignment


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
