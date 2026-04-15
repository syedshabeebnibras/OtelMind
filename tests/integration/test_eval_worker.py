"""Tests for otelmind.eval.worker — private helpers and end-to-end eval runs."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from otelmind.eval import worker as worker_module
from otelmind.eval.judge import DimensionScore, JudgeResult
from otelmind.eval.regression import RegressionReport
from otelmind.eval.worker import (
    _claim_pending_eval_run,
    _execute_eval_run,
    _extract_cases_from_details,
    _run_daily_golden_for_tenant,
)

# ─── _extract_cases_from_details ────────────────────────────────────────────


def test_extract_cases_empty_details():
    baseline, candidate = _extract_cases_from_details(None)
    assert baseline == []
    assert candidate == []


def test_extract_cases_missing_keys():
    baseline, candidate = _extract_cases_from_details({"unrelated": True})
    assert baseline == []
    assert candidate == []


def test_extract_cases_parses_complete_shape():
    details = {
        "baseline_cases": [
            {"id": "c1", "question": "q1", "expected": "e1", "actual": "a1", "context": "ctx"},
        ],
        "candidate_cases": [
            {"id": "c1", "question": "q1", "expected": "e1", "actual": "a2"},
        ],
    }
    baseline, candidate = _extract_cases_from_details(details)
    assert len(baseline) == 1
    assert len(candidate) == 1
    assert baseline[0].id == "c1"
    assert baseline[0].context == "ctx"
    assert candidate[0].actual == "a2"


def test_extract_cases_coerces_types():
    details = {
        "baseline_cases": [{"id": 42, "question": None, "tags": ("t1", "t2")}],
        "candidate_cases": [],
    }
    baseline, _ = _extract_cases_from_details(details)
    assert baseline[0].id == "42"
    assert baseline[0].question == "None"
    assert baseline[0].tags == ["t1", "t2"]


def test_extract_cases_handles_partial_fields():
    details = {"baseline_cases": [{"id": "c1"}], "candidate_cases": [{"id": "c1"}]}
    baseline, candidate = _extract_cases_from_details(details)
    assert baseline[0].question == ""
    assert candidate[0].actual == ""


# ─── helpers for the DB-mocked tests below ────────────────────────────────


def _mock_session(*, scalar_return=None, execute_return=None):
    """Build an async-context-manager session suitable for `get_session`."""
    session = MagicMock()
    session.scalar = AsyncMock(return_value=scalar_return)
    session.execute = AsyncMock(
        return_value=execute_return or MagicMock(scalar_one_or_none=MagicMock(return_value=None))
    )
    session.flush = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()

    @asynccontextmanager
    async def factory():
        yield session

    return session, factory


# ─── _claim_pending_eval_run ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_claim_pending_returns_none_when_queue_empty(monkeypatch):
    exec_result = MagicMock(scalar_one_or_none=MagicMock(return_value=None))
    session, factory = _mock_session(execute_return=exec_result)
    monkeypatch.setattr(worker_module, "get_session", factory)

    result = await _claim_pending_eval_run()
    assert result is None


@pytest.mark.asyncio
async def test_claim_pending_flips_status_to_running(monkeypatch):
    row = MagicMock(id=uuid.uuid4(), status="pending")
    exec_result = MagicMock(scalar_one_or_none=MagicMock(return_value=row))
    session, factory = _mock_session(execute_return=exec_result)
    monkeypatch.setattr(worker_module, "get_session", factory)

    claimed = await _claim_pending_eval_run()
    assert claimed is row
    assert row.status == "running"


# ─── _execute_eval_run ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_eval_run_missing_row_is_noop(monkeypatch):
    session, factory = _mock_session(scalar_return=None)
    monkeypatch.setattr(worker_module, "get_session", factory)

    # Shouldn't raise — just exits early
    await _execute_eval_run(uuid.uuid4())
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_execute_eval_run_empty_cases_marks_complete(monkeypatch):
    row = MagicMock(
        id=uuid.uuid4(),
        status="running",
        details={},
        baseline="b",
        candidate="c",
    )
    session, factory = _mock_session(scalar_return=row)
    monkeypatch.setattr(worker_module, "get_session", factory)

    await _execute_eval_run(row.id)

    assert row.status == "completed"
    assert row.passed is True
    assert row.case_count == 0


@pytest.mark.asyncio
async def test_execute_eval_run_end_to_end_with_mock_judge(monkeypatch):
    run_id = uuid.uuid4()
    row = MagicMock(
        id=run_id,
        status="running",
        baseline="b",
        candidate="c",
        details={
            "baseline_cases": [
                {"id": "c1", "question": "q", "expected": "e", "actual": "base", "context": ""}
            ],
            "candidate_cases": [
                {"id": "c1", "question": "q", "expected": "e", "actual": "cand", "context": ""}
            ],
        },
    )
    session, factory = _mock_session(scalar_return=row)
    monkeypatch.setattr(worker_module, "get_session", factory)

    fake_report = RegressionReport(
        baseline_name="b",
        candidate_name="c",
        passed=True,
        summary={"total_cases": 1, "regressions": 0, "improvements": 0, "dimensions": {}},
        regressions=[],
        improvements=[],
        per_case=[
            {
                "id": "c1",
                "baseline_overall": 0.8,
                "candidate_overall": 0.78,
                "delta_overall": -0.02,
                "dimensions": {
                    "faithfulness": {"baseline": 0.8, "candidate": 0.78, "delta": -0.02},
                    "relevance": {"baseline": 0.85, "candidate": 0.83, "delta": -0.02},
                    "coherence": {"baseline": 0.9, "candidate": 0.9, "delta": 0.0},
                },
            }
        ],
    )
    with patch.object(worker_module, "run_regression", AsyncMock(return_value=fake_report)):
        await _execute_eval_run(run_id)

    assert row.status == "completed"
    assert row.case_count == 1
    # dim_summary gets populated from per_case candidate values
    assert "faithfulness" in row.scores


@pytest.mark.asyncio
async def test_execute_eval_run_marks_failed_on_exception(monkeypatch):
    run_id = uuid.uuid4()
    row = MagicMock(
        id=run_id,
        status="running",
        baseline="b",
        candidate="c",
        details={
            "baseline_cases": [
                {"id": "c1", "question": "q", "expected": "e", "actual": "base", "context": ""}
            ],
            "candidate_cases": [
                {"id": "c1", "question": "q", "expected": "e", "actual": "cand", "context": ""}
            ],
        },
    )
    session, factory = _mock_session(scalar_return=row)
    monkeypatch.setattr(worker_module, "get_session", factory)

    with patch.object(
        worker_module, "run_regression", AsyncMock(side_effect=RuntimeError("judge exploded"))
    ):
        await _execute_eval_run(run_id)

    assert row.status == "failed"
    assert "error" in row.details


# ─── daily golden idempotency ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_daily_golden_skips_when_today_run_exists(monkeypatch, tmp_path):
    golden = tmp_path / "golden.yaml"
    golden.write_text("- id: g1\n  question: q\n  expected: e\n  context: ''\n")
    monkeypatch.setattr(worker_module.settings, "eval_golden_dataset_path", str(golden))

    # A stub "already exists" scalar return for the dedup SELECT
    existing_run = MagicMock(id=uuid.uuid4())
    session, factory = _mock_session(scalar_return=existing_run)
    monkeypatch.setattr(worker_module, "get_session", factory)

    # _execute_eval_run should NEVER be reached when the daily run exists
    with patch.object(worker_module, "_execute_eval_run", AsyncMock()) as exec_mock:
        tenant = MagicMock(id=uuid.uuid4())
        await _run_daily_golden_for_tenant(tenant)

    exec_mock.assert_not_awaited()
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_daily_golden_skips_empty_dataset(monkeypatch, tmp_path):
    empty = tmp_path / "empty.yaml"
    empty.write_text("# no cases\n")
    monkeypatch.setattr(worker_module.settings, "eval_golden_dataset_path", str(empty))

    with patch.object(worker_module, "_execute_eval_run", AsyncMock()) as exec_mock:
        tenant = MagicMock(id=uuid.uuid4())
        await _run_daily_golden_for_tenant(tenant)
    exec_mock.assert_not_awaited()


def _make_judge_result(score: float) -> JudgeResult:
    dim = DimensionScore("faithfulness", score, round(score * 4) + 1, "", "heuristic")
    return JudgeResult("q", "a", "", {"faithfulness": dim}, score)


# ─── _snapshot_benchmark_health ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_snapshot_benchmark_health_detects_regression(monkeypatch):
    """Prior snapshot scored round_robin=0.9; today's runs score 0.5 → regression."""
    from datetime import UTC, datetime, timedelta

    from otelmind.eval.worker import _snapshot_benchmark_health

    tenant = MagicMock(id=uuid.uuid4(), slug="t")
    prev = MagicMock(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        name="benchmark-snapshot-2026-04-13",
        scores={"round_robin": 0.9},
    )
    current_runs = [
        MagicMock(
            tenant_id=tenant.id,
            protocol="round_robin",
            status="completed",
            metrics={"task_completion_score": 0.5},
            created_at=datetime.now(UTC) - timedelta(hours=1),
        )
        for _ in range(3)
    ]

    added: list = []

    # The function opens get_session() 3 times in sequence:
    #   1. dedup check + read recent group_runs (same session)
    #   2. read prior snapshot
    #   3. write the new row
    sessions = [
        MagicMock(
            scalar=AsyncMock(return_value=None),  # dedup → missing
            execute=AsyncMock(
                return_value=MagicMock(
                    scalars=MagicMock(
                        return_value=MagicMock(all=MagicMock(return_value=current_runs))
                    )
                )
            ),
        ),
        MagicMock(scalar=AsyncMock(return_value=prev), execute=AsyncMock()),
        MagicMock(
            scalar=AsyncMock(),
            execute=AsyncMock(),
            add=MagicMock(side_effect=added.append),
        ),
    ]
    it = iter(sessions)

    @asynccontextmanager
    async def factory():
        yield next(it)

    monkeypatch.setattr(worker_module, "get_session", factory)

    await _snapshot_benchmark_health(tenant)

    assert added, "new EvalRun row should be written"
    row = added[0]
    assert row.name.startswith("benchmark-snapshot-")
    assert row.passed is False
    assert "round_robin" in row.details["regressed_protocols"]


@pytest.mark.asyncio
async def test_snapshot_benchmark_health_skip_when_no_recent_runs(monkeypatch):
    from otelmind.eval.worker import _snapshot_benchmark_health

    tenant = MagicMock(id=uuid.uuid4(), slug="t")

    # Only one session is needed — the function returns after the empty
    # group_runs read and never opens the prev-snapshot / write sessions.
    session = MagicMock(
        scalar=AsyncMock(return_value=None),  # dedup → no existing snapshot
        execute=AsyncMock(
            return_value=MagicMock(
                scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
            )
        ),
    )

    @asynccontextmanager
    async def factory():
        yield session

    monkeypatch.setattr(worker_module, "get_session", factory)

    # Should exit cleanly without writing a row
    await _snapshot_benchmark_health(tenant)
