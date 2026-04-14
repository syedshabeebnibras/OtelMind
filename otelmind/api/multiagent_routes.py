"""REST API for multi-agent group runs.

POST /api/multiagent/runs is fire-and-forget: it stores a `group_runs` row
with status="pending", spawns a background task that drives `AgentGroup.solve`,
and returns the row immediately so the client can poll for completion.

All endpoints are tenant-scoped through the existing `require_api_key`
dependency. Multi-agent runs are billed against the tenant whose API key
was used to create them.
"""

from __future__ import annotations

import asyncio
import uuid as _uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from loguru import logger
from sqlalchemy import desc, func, select

from otelmind.api.auth import CurrentTenant, require_scope
from otelmind.api.rate_limit import enforce_tenant_rate_limit
from otelmind.api.schemas import (
    GroupMessageResponse,
    GroupRunCreate,
    GroupRunDetail,
    GroupRunResponse,
    GroupRunsListResponse,
    GroupRunSummary,
)
from otelmind.db import get_session
from otelmind.eval.group_metrics import evaluate_group
from otelmind.multiagent.group import AgentGroup
from otelmind.multiagent.protocols import (
    BlackboardProtocol,
    ConsensusProtocol,
    DebateProtocol,
    DelegationProtocol,
    RoundRobinProtocol,
)
from otelmind.multiagent.roles import AgentRole
from otelmind.storage.models import GroupMessage, GroupRun

router = APIRouter(prefix="/multiagent", tags=["multiagent"])

_PROTOCOL_MAP: dict[str, type] = {
    "round_robin": RoundRobinProtocol,
    "debate": DebateProtocol,
    "blackboard": BlackboardProtocol,
    "consensus": ConsensusProtocol,
    "delegation": DelegationProtocol,
}


def _role_from_dict(spec: dict[str, Any]) -> AgentRole:
    return AgentRole(
        name=str(spec.get("name") or spec.get("role") or "agent"),
        system_prompt=str(spec.get("system_prompt", "")),
        tools=spec.get("tools"),
        model=str(spec.get("model", "")),
        max_tokens=int(spec.get("max_tokens", 4096)),
        temperature=float(spec.get("temperature", 0.7)),
        metadata=dict(spec.get("metadata", {})),
    )


def _row_to_summary(row: GroupRun) -> GroupRunSummary:
    return GroupRunSummary(
        id=str(row.id),
        problem=row.problem,
        protocol=row.protocol,
        status=row.status,
        rounds_completed=row.rounds_completed,
        total_tokens=row.total_tokens,
        total_cost_usd=row.total_cost_usd,
        created_at=row.created_at,
        completed_at=row.completed_at,
    )


async def _execute_group_run(run_id: _uuid.UUID, body: GroupRunCreate) -> None:
    """Background task: actually run the group, persist the result.

    Errors are caught and recorded on the row as status="failed" so the
    API never leaks an unhandled exception into the worker logs.
    """
    protocol_cls = _PROTOCOL_MAP[body.protocol.lower()]
    role_objs = [_role_from_dict(r) for r in body.roles]
    protocol = protocol_cls(max_rounds=body.max_rounds)

    try:
        kwargs: dict[str, Any] = {"max_rounds": body.max_rounds}
        if body.budget_usd is not None:
            kwargs["budget_usd"] = body.budget_usd
        group = AgentGroup(roles=role_objs, protocol=protocol, **kwargs)
        result = await group.solve(problem=body.problem, context=body.context)
        metrics = await evaluate_group(
            result, expected_output=body.expected_output, max_rounds=body.max_rounds
        )
    except Exception as exc:
        logger.exception("multiagent: run {} failed: {}", run_id, exc)
        async with get_session() as session:
            row = await session.scalar(select(GroupRun).where(GroupRun.id == run_id))
            if row is not None:
                row.status = "failed"
                row.result = {"error": str(exc)}
                row.completed_at = datetime.now(UTC)
        return

    async with get_session() as session:
        row = await session.scalar(select(GroupRun).where(GroupRun.id == run_id))
        if row is None:
            return
        row.status = result.status
        row.rounds_completed = result.rounds_completed
        row.total_tokens = result.total_tokens
        row.total_cost_usd = float(metrics.total_cost_usd)
        row.result = result.to_dict()
        row.metrics = metrics.to_dict()
        row.completed_at = datetime.now(UTC)

        for msg in result.messages:
            session.add(
                GroupMessage(
                    group_run_id=run_id,
                    sender_id=msg.sender_id,
                    sender_role=msg.sender_role,
                    recipient_id=msg.recipient_id,
                    content=msg.content,
                    round_number=msg.round_number,
                    token_usage=msg.token_usage,
                )
            )


@router.post("/runs", response_model=GroupRunResponse, status_code=202)
async def create_group_run(
    body: GroupRunCreate,
    request: Request,
    tenant: CurrentTenant,
    _: Annotated[None, Depends(require_scope("write", "admin"))],
) -> GroupRunResponse:
    """Spawn a multi-agent group asynchronously. Returns the run record immediately."""
    # Bucket = Literal["ingest", "read"] — POST counts against the ingest budget
    await enforce_tenant_rate_limit(request, tenant, "ingest")

    if not body.roles:
        raise HTTPException(status_code=422, detail="at least one role is required")
    if body.protocol.lower() not in _PROTOCOL_MAP:
        raise HTTPException(
            status_code=422,
            detail=f"unknown protocol {body.protocol!r}; choose one of {list(_PROTOCOL_MAP)}",
        )

    run_id = _uuid.uuid4()
    async with get_session() as session:
        row = GroupRun(
            id=run_id,
            tenant_id=tenant.id,
            problem=body.problem,
            protocol=body.protocol.lower(),
            status="pending",
            roles=[
                {k: v for k, v in r.items() if k != "system_prompt"} | {"name": r.get("name", "")}
                for r in body.roles
            ],
        )
        session.add(row)
        await session.flush()

    asyncio.create_task(_execute_group_run(run_id, body))

    return GroupRunResponse(
        id=str(run_id),
        problem=body.problem,
        protocol=body.protocol.lower(),
        status="pending",
        rounds_completed=0,
        total_tokens=0,
        total_cost_usd=0.0,
        created_at=datetime.now(UTC),
        completed_at=None,
    )


@router.get("/runs", response_model=GroupRunsListResponse)
async def list_group_runs(
    request: Request,
    tenant: CurrentTenant,
    _: Annotated[None, Depends(require_scope("read", "admin"))],
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: str | None = Query(None),
) -> GroupRunsListResponse:
    await enforce_tenant_rate_limit(request, tenant, "read")
    async with get_session() as session:
        stmt = select(GroupRun).where(GroupRun.tenant_id == tenant.id)
        count_stmt = select(func.count(GroupRun.id)).where(GroupRun.tenant_id == tenant.id)
        if status:
            stmt = stmt.where(GroupRun.status == status)
            count_stmt = count_stmt.where(GroupRun.status == status)
        stmt = stmt.order_by(desc(GroupRun.created_at)).limit(limit).offset(offset)
        rows = list((await session.execute(stmt)).scalars().all())
        total = int(await session.scalar(count_stmt) or 0)

    return GroupRunsListResponse(items=[_row_to_summary(r) for r in rows], total=total)


@router.get("/runs/{run_id}", response_model=GroupRunDetail)
async def get_group_run(
    run_id: str,
    tenant: CurrentTenant,
    _: Annotated[None, Depends(require_scope("read", "admin"))],
) -> GroupRunDetail:
    try:
        rid = _uuid.UUID(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid run_id") from exc

    async with get_session() as session:
        row = await session.scalar(
            select(GroupRun).where(GroupRun.id == rid, GroupRun.tenant_id == tenant.id)
        )
        if row is None:
            raise HTTPException(status_code=404, detail="group run not found")

        msg_stmt = (
            select(GroupMessage).where(GroupMessage.group_run_id == rid).order_by(GroupMessage.id)
        )
        messages = list((await session.execute(msg_stmt)).scalars().all())

    base = _row_to_summary(row)
    return GroupRunDetail(
        **base.model_dump(),
        roles=row.roles or [],
        result=row.result,
        metrics=row.metrics,
        messages=[
            {
                "sender_id": m.sender_id,
                "sender_role": m.sender_role,
                "recipient_id": m.recipient_id,
                "content": m.content,
                "round_number": m.round_number,
                "token_usage": m.token_usage,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in messages
        ],
    )


@router.get("/runs/{run_id}/messages", response_model=list[GroupMessageResponse])
async def get_group_messages(
    run_id: str,
    tenant: CurrentTenant,
    _: Annotated[None, Depends(require_scope("read", "admin"))],
) -> list[GroupMessageResponse]:
    try:
        rid = _uuid.UUID(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid run_id") from exc

    async with get_session() as session:
        owner = await session.scalar(select(GroupRun.tenant_id).where(GroupRun.id == rid))
        if owner is None:
            raise HTTPException(status_code=404, detail="group run not found")
        if owner != tenant.id:
            raise HTTPException(status_code=404, detail="group run not found")

        msg_stmt = (
            select(GroupMessage).where(GroupMessage.group_run_id == rid).order_by(GroupMessage.id)
        )
        messages = list((await session.execute(msg_stmt)).scalars().all())

    return [
        GroupMessageResponse(
            sender_id=m.sender_id,
            sender_role=m.sender_role,
            recipient_id=m.recipient_id,
            content=m.content,
            round_number=m.round_number,
            token_usage=m.token_usage,
            created_at=m.created_at,
        )
        for m in messages
    ]
