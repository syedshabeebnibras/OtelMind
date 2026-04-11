"""RBAC administration endpoints — users, roles, memberships, audit logs.

Protected by `require_permission("admin:*")` so only tenant owners and
admins can touch membership. The dashboard "Settings → Team" view hits
these routes; they also back the CLI's `otelmind invite` command.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import desc, select
from sqlalchemy.orm import selectinload

from otelmind.api.auth import (
    CurrentTenant,
    attach_user_to_tenant,
    require_permission,
)
from otelmind.db import get_session
from otelmind.storage.models import AuditLog, Role, User, UserTenantRole

rbac_router = APIRouter(prefix="/rbac", tags=["rbac"])


# ── Schemas ─────────────────────────────────────────────────────────────


class RolePublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    permissions: list[str]
    is_system: bool


class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str | None
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime


class MembershipPublic(BaseModel):
    user: UserPublic
    role: RolePublic
    created_at: datetime


class CreateUserRequest(BaseModel):
    email: str
    full_name: str | None = None
    role: str = Field(
        default="viewer",
        description="One of: owner, admin, engineer, viewer, billing",
    )
    # If set, caller pre-generates a temporary password. If None, the
    # server returns a random 32-char token the inviter must hand off
    # out-of-band. Real deployments would send a magic-link email.
    temporary_password: str | None = None

    @field_validator("email")
    @classmethod
    def _validate_email(cls, v: str) -> str:
        # Intentionally minimal — avoid pulling in email-validator.
        if "@" not in v or "." not in v.split("@", 1)[1]:
            raise ValueError("invalid email address")
        return v.lower().strip()


class CreateUserResponse(BaseModel):
    user: UserPublic
    role: str
    temporary_password: str


class UpdateMembershipRequest(BaseModel):
    role: str = Field(description="One of: owner, admin, engineer, viewer, billing")


class AuditLogEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    action: str
    resource_id: str | None
    api_key_id: uuid.UUID | None
    user_id: uuid.UUID | None
    ip_address: str | None
    status_code: int | None
    created_at: datetime


class AuditLogResponse(BaseModel):
    items: list[AuditLogEntry]
    total: int


# ── Helpers ─────────────────────────────────────────────────────────────


def _hash_password(raw: str) -> str:
    salt = secrets.token_hex(8)
    digest = hashlib.sha256((salt + raw).encode()).hexdigest()
    return f"{salt}${digest}"


# ── Roles ───────────────────────────────────────────────────────────────


@rbac_router.get("/roles", response_model=list[RolePublic])
async def list_roles(
    tenant: CurrentTenant,
    _: Annotated[None, Depends(require_permission("admin:read"))],
) -> list[RolePublic]:
    async with get_session() as session:
        result = await session.execute(select(Role).order_by(Role.name))
        return [RolePublic.model_validate(r) for r in result.scalars()]


# ── Users / memberships ─────────────────────────────────────────────────


@rbac_router.get("/members", response_model=list[MembershipPublic])
async def list_members(
    tenant: CurrentTenant,
    _: Annotated[None, Depends(require_permission("admin:read"))],
) -> list[MembershipPublic]:
    async with get_session() as session:
        stmt = (
            select(UserTenantRole)
            .where(UserTenantRole.tenant_id == tenant.id)
            .options(
                selectinload(UserTenantRole.user),
                selectinload(UserTenantRole.role),
            )
        )
        result = await session.execute(stmt)
        memberships = result.scalars().all()

    return [
        MembershipPublic(
            user=UserPublic.model_validate(m.user),
            role=RolePublic.model_validate(m.role),
            created_at=m.created_at,
        )
        for m in memberships
    ]


@rbac_router.post(
    "/members",
    response_model=CreateUserResponse,
    status_code=201,
)
async def invite_member(
    body: CreateUserRequest,
    tenant: CurrentTenant,
    _: Annotated[None, Depends(require_permission("admin:write"))],
) -> CreateUserResponse:
    """Create a user (if needed) and attach them to the current tenant."""
    temp_password = body.temporary_password or secrets.token_urlsafe(24)

    async with get_session() as session:
        existing = await session.scalar(select(User).where(User.email == body.email))
        if existing is None:
            existing = User(
                id=uuid.uuid4(),
                email=body.email,
                full_name=body.full_name,
                password_hash=_hash_password(temp_password),
                is_active=True,
            )
            session.add(existing)
            await session.flush()

    await attach_user_to_tenant(str(existing.id), str(tenant.id), body.role)

    return CreateUserResponse(
        user=UserPublic.model_validate(existing),
        role=body.role,
        temporary_password=temp_password,
    )


@rbac_router.patch("/members/{user_id}", response_model=MembershipPublic)
async def update_member_role(
    user_id: uuid.UUID,
    body: UpdateMembershipRequest,
    tenant: CurrentTenant,
    _: Annotated[None, Depends(require_permission("admin:write"))],
) -> MembershipPublic:
    await attach_user_to_tenant(str(user_id), str(tenant.id), body.role)

    async with get_session() as session:
        stmt = (
            select(UserTenantRole)
            .where(
                UserTenantRole.tenant_id == tenant.id,
                UserTenantRole.user_id == user_id,
            )
            .options(
                selectinload(UserTenantRole.user),
                selectinload(UserTenantRole.role),
            )
        )
        membership = (await session.execute(stmt)).scalar_one_or_none()

    if membership is None:
        raise HTTPException(status_code=404, detail="Membership not found")

    return MembershipPublic(
        user=UserPublic.model_validate(membership.user),
        role=RolePublic.model_validate(membership.role),
        created_at=membership.created_at,
    )


@rbac_router.delete("/members/{user_id}", status_code=204)
async def remove_member(
    user_id: uuid.UUID,
    tenant: CurrentTenant,
    _: Annotated[None, Depends(require_permission("admin:write"))],
) -> None:
    async with get_session() as session:
        stmt = select(UserTenantRole).where(
            UserTenantRole.tenant_id == tenant.id,
            UserTenantRole.user_id == user_id,
        )
        membership = (await session.execute(stmt)).scalar_one_or_none()
        if membership is None:
            raise HTTPException(status_code=404, detail="Membership not found")
        await session.delete(membership)


# ── Audit log ───────────────────────────────────────────────────────────


@rbac_router.get("/audit", response_model=AuditLogResponse)
async def list_audit_log(
    tenant: CurrentTenant,
    _: Annotated[None, Depends(require_permission("audit:read"))],
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> AuditLogResponse:
    async with get_session() as session:
        base = select(AuditLog).where(AuditLog.tenant_id == tenant.id)
        total_stmt = select(AuditLog).where(AuditLog.tenant_id == tenant.id)
        total = len((await session.execute(total_stmt)).scalars().all())

        rows = (
            (
                await session.execute(
                    base.order_by(desc(AuditLog.created_at)).limit(limit).offset(offset)
                )
            )
            .scalars()
            .all()
        )

    return AuditLogResponse(
        items=[AuditLogEntry.model_validate(r) for r in rows],
        total=total,
    )
