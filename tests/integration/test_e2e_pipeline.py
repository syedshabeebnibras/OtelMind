"""End-to-end pipeline tests against a real PostgreSQL database.

These tests exercise the actual SQLAlchemy session, real Alembic-migrated
schemas, and real async DB I/O — no mocked sessions. They are gated on
TEST_DATABASE_URL being set so default `pytest` runs skip them cleanly.

To run locally:

    TEST_DATABASE_URL=postgresql://otelmind:otelmind@localhost:5432/otelmind_test \\
      DATABASE_URL_SYNC=$TEST_DATABASE_URL \\
      .venv/bin/alembic upgrade head
    TEST_DATABASE_URL=postgresql+asyncpg://otelmind:otelmind@localhost:5432/otelmind_test \\
      pytest tests/integration/test_e2e_pipeline.py -v -m e2e

The async URL needs the `+asyncpg` driver suffix for the SQLAlchemy
async engine. The sync URL is used by Alembic and should NOT include
`+asyncpg`.
"""

from __future__ import annotations

import os
import uuid

import pytest
import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from otelmind.eval.calibration import HumanLabel, calibrate_judge
from otelmind.eval.judge import DimensionScore, JudgeResult
from otelmind.eval.regression import EvalCase
from otelmind.storage.models import (
    FailureClassification,
    JudgeCalibration,
    Span,
    Tenant,
    Trace,
)
from otelmind.watchdog.failure_detection import FailureDetector

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("TEST_DATABASE_URL"),
        reason="TEST_DATABASE_URL not set — skipping e2e tests",
    ),
    pytest.mark.slow,
    pytest.mark.e2e,
]


@pytest.fixture(scope="module")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="module")
def engine():
    url = os.environ["TEST_DATABASE_URL"]
    eng = create_async_engine(url, echo=False)
    yield eng
    # Engine cleanup happens at process exit; pool disposal is async-only.


@pytest.fixture
async def session(engine) -> AsyncSession:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        try:
            yield s
            await s.commit()
        except Exception:
            await s.rollback()
            raise


@pytest.fixture
async def tenant(session) -> Tenant:
    """Create (or reuse) a dedicated e2e test tenant."""
    slug = "e2e-test-tenant"
    existing = await session.scalar(select(Tenant).where(Tenant.slug == slug))
    if existing is not None:
        return existing
    t = Tenant(id=uuid.uuid4(), name="E2E Tests", slug=slug, plan="enterprise")
    session.add(t)
    await session.flush()
    return t


# ─── ingest → detect ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ingest_trace_then_detect_failure(session, tenant):
    """Insert a trace whose spans trigger infinite_loop, run analyze(), persist failure."""
    trace_id = f"e2e-{uuid.uuid4().hex[:12]}"
    trace = Trace(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        trace_id=trace_id,
        service_name="e2e-service",
        status="ok",
    )
    session.add(trace)
    spans = [
        Span(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            trace_id=trace_id,
            span_id=f"span-{i}",
            name="loop_node",
            duration_ms=100.0,
            status_code="OK",
        )
        for i in range(6)  # > LOOP_NODE_REPEAT_THRESHOLD (5)
    ]
    for s in spans:
        session.add(s)
    await session.flush()

    detector = FailureDetector()
    detected = detector.analyze(trace_id, spans)
    assert any(f.failure_type == "infinite_loop" for f in detected)

    # Persist the detected failure as the watchdog would
    f = detected[0]
    fc = FailureClassification(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        trace_id=trace_id,
        failure_type=f.failure_type,
        confidence=f.confidence,
        evidence=f.evidence,
        detection_method=f.detection_method,
    )
    session.add(fc)
    await session.flush()

    # Round-trip read
    fetched = await session.scalar(
        select(FailureClassification).where(FailureClassification.id == fc.id)
    )
    assert fetched is not None
    assert fetched.failure_type == "infinite_loop"
    assert fetched.detection_method == "pattern"


# ─── eval scoring + regression detection ───────────────────────────────────


@pytest.mark.asyncio
async def test_eval_run_scores_and_detects_regression(session, tenant):
    """Two EvalCase sets; candidate is degraded; regression should be flagged."""
    from unittest.mock import patch

    from otelmind.eval.regression import run_regression

    baseline = [EvalCase(id="ec-1", question="q", expected="e", actual="good")]
    candidate = [EvalCase(id="ec-1", question="q", expected="e", actual="bad")]

    def _r(score: float) -> JudgeResult:
        raw = max(1, min(5, round(score * 4) + 1))
        return JudgeResult(
            "q",
            "a",
            "",
            {"faithfulness": DimensionScore("faithfulness", score, raw, "", "heuristic")},
            score,
        )

    async def fake_score(self, q, a, c, dims=None):
        return _r(0.9 if a == "good" else 0.3)

    with patch("otelmind.eval.regression.LLMJudge.score", fake_score):
        report = await run_regression(baseline, candidate, regression_threshold=0.05)

    assert report.passed is False
    assert len(report.regressions) == 1


# ─── calibration round-trip through the DB ─────────────────────────────────


@pytest.mark.asyncio
async def test_calibration_against_human_labels_persists_row(session, tenant):
    """Compute calibration for a stub judge, then persist + read back the row."""
    with open("config/eval_datasets/human_labels.yaml") as fh:
        cases_data = yaml.safe_load(fh)["cases"][:5]

    cases = [
        EvalCase(
            id=c["id"],
            question=c["question"],
            expected=c["expected"],
            actual=c["actual"],
            context=c.get("context", ""),
        )
        for c in cases_data
    ]
    labels = [
        HumanLabel(case_id=c["id"], dimension=dim, score=(s - 1) / 4)
        for c in cases_data
        for dim, s in c["human_scores"].items()
    ]

    # Stub judge that mirrors human scores exactly → kappa should be 1.0
    class _StubJudge:
        _model = "stub"
        _api_key = ""

        async def score(self, q, a, ctx, dims=None):
            target = next(c for c in cases_data if c["actual"] == a)
            scores = {}
            for dim in dims or []:
                raw = target["human_scores"][dim]
                scores[dim] = DimensionScore(dim, (raw - 1) / 4, raw, "", "stub")
            return JudgeResult(q, a, ctx, scores, 0.5)

    result = await calibrate_judge(_StubJudge(), cases, labels)
    assert result.cohens_kappa == pytest.approx(1.0)

    # Persist as the API does
    row = JudgeCalibration(
        tenant_id=tenant.id,
        judge_model=result.judge_model,
        cohens_kappa=result.cohens_kappa,
        agreement_rate=result.agreement_rate,
        bias=result.bias,
        case_count=result.case_count,
    )
    session.add(row)
    await session.flush()

    fetched = await session.scalar(select(JudgeCalibration).where(JudgeCalibration.id == row.id))
    assert fetched is not None
    assert fetched.cohens_kappa == pytest.approx(1.0)
    assert fetched.case_count == 5


# ─── tenant scoping ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tenant_scoping_isolates_failures(session, tenant):
    """Failures inserted under one tenant are invisible to another tenant query."""
    other = Tenant(id=uuid.uuid4(), name="Other", slug=f"e2e-other-{uuid.uuid4().hex[:6]}")
    session.add(other)
    await session.flush()

    fc = FailureClassification(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        trace_id="iso-trace",
        failure_type="hallucination",
        confidence=0.9,
        evidence={},
        detection_method="heuristic",
    )
    session.add(fc)
    await session.flush()

    # Other tenant must not see it
    visible = await session.scalar(
        select(FailureClassification).where(
            FailureClassification.tenant_id == other.id,
            FailureClassification.id == fc.id,
        )
    )
    assert visible is None


# ─── schema sanity ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_judge_calibrations_table_exists(engine):
    """Migration 007 ran — the judge_calibrations table is queryable."""
    async with engine.begin() as conn:
        result = await conn.execute(select(JudgeCalibration).limit(0))
    assert result is not None  # no exception = table exists
