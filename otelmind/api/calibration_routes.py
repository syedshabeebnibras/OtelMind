"""REST API for judge calibration runs.

Calibration is fast (a few seconds for ~25 cases) so POST is synchronous —
we score, compute Cohen's kappa / bias / curve, persist the row, and return
it in one request. Tenant-scoped through the existing API-key auth.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import desc, func, select

from otelmind.api.auth import CurrentTenant, require_scope
from otelmind.api.rate_limit import enforce_tenant_rate_limit
from otelmind.api.schemas import (
    CalibrationCreate,
    CalibrationDetail,
    CalibrationResponse,
    CalibrationsListResponse,
    CalibrationSummary,
)
from otelmind.config import settings
from otelmind.db import get_session
from otelmind.eval.calibration import HumanLabel, calibrate_judge
from otelmind.eval.judge import LLMJudge
from otelmind.eval.regression import EvalCase
from otelmind.storage.models import JudgeCalibration

router = APIRouter(prefix="/calibrations", tags=["calibrations"])


def _row_to_summary(row: JudgeCalibration) -> CalibrationSummary:
    return CalibrationSummary(
        id=row.id,
        judge_model=row.judge_model,
        cohens_kappa=row.cohens_kappa,
        agreement_rate=row.agreement_rate,
        bias=row.bias,
        case_count=row.case_count,
        created_at=row.created_at,
    )


def _row_to_detail(row: JudgeCalibration) -> CalibrationDetail:
    return CalibrationDetail(
        id=row.id,
        judge_model=row.judge_model,
        cohens_kappa=row.cohens_kappa,
        agreement_rate=row.agreement_rate,
        bias=row.bias,
        case_count=row.case_count,
        created_at=row.created_at,
        per_dimension=row.per_dimension,
        calibration_curve=row.calibration_curve,
    )


@router.post("", response_model=CalibrationResponse, status_code=201)
async def create_calibration(
    body: CalibrationCreate,
    request: Request,
    tenant: CurrentTenant,
    _: Annotated[None, Depends(require_scope("write", "admin"))],
) -> CalibrationResponse:
    """Score the supplied cases with the LLM judge, compare to the human labels."""
    # Bucket = Literal["ingest", "read"] — POST counts against the ingest budget
    await enforce_tenant_rate_limit(request, tenant, "ingest")

    if not body.test_cases:
        raise HTTPException(status_code=422, detail="test_cases must be non-empty")
    if not body.human_labels:
        raise HTTPException(status_code=422, detail="human_labels must be non-empty")

    cases = [
        EvalCase(
            id=c.id,
            question=c.question,
            expected=c.expected,
            actual=c.actual,
            context=c.context,
        )
        for c in body.test_cases
    ]
    labels = [
        HumanLabel(
            case_id=label.case_id,
            dimension=label.dimension,
            score=label.score,
            annotator_id=label.annotator_id,
        )
        for label in body.human_labels
    ]

    judge = LLMJudge(
        api_key=settings.llm.api_key or None,
        model=settings.llm.model or "gpt-4o",
    )
    result = await calibrate_judge(judge, cases, labels, dimensions=body.dimensions)

    serialized: dict[str, Any] = result.to_dict()
    async with get_session() as session:
        row = JudgeCalibration(
            tenant_id=tenant.id,
            judge_model=result.judge_model,
            cohens_kappa=result.cohens_kappa,
            agreement_rate=result.agreement_rate,
            bias=result.bias,
            per_dimension=serialized.get("per_dimension"),
            calibration_curve=serialized.get("calibration_curve"),
            case_count=result.case_count,
        )
        session.add(row)
        await session.flush()
        await session.refresh(row)
        detail = _row_to_detail(row)

    return CalibrationResponse(**detail.model_dump())


@router.get("", response_model=CalibrationsListResponse)
async def list_calibrations(
    request: Request,
    tenant: CurrentTenant,
    _: Annotated[None, Depends(require_scope("read", "admin"))],
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> CalibrationsListResponse:
    await enforce_tenant_rate_limit(request, tenant, "read")
    async with get_session() as session:
        stmt = (
            select(JudgeCalibration)
            .where(JudgeCalibration.tenant_id == tenant.id)
            .order_by(desc(JudgeCalibration.created_at))
            .limit(limit)
            .offset(offset)
        )
        count_stmt = select(func.count(JudgeCalibration.id)).where(
            JudgeCalibration.tenant_id == tenant.id
        )
        rows = list((await session.execute(stmt)).scalars().all())
        total = int(await session.scalar(count_stmt) or 0)

    return CalibrationsListResponse(items=[_row_to_summary(r) for r in rows], total=total)


@router.get("/{calibration_id}", response_model=CalibrationDetail)
async def get_calibration(
    calibration_id: int,
    tenant: CurrentTenant,
    _: Annotated[None, Depends(require_scope("read", "admin"))],
) -> CalibrationDetail:
    async with get_session() as session:
        row = await session.scalar(
            select(JudgeCalibration).where(
                JudgeCalibration.id == calibration_id,
                JudgeCalibration.tenant_id == tenant.id,
            )
        )
        if row is None:
            raise HTTPException(status_code=404, detail="calibration not found")

    return _row_to_detail(row)
