"""Tests for otelmind.eval.protocol_selector."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from otelmind.eval import protocol_selector as ps
from otelmind.eval.protocol_selector import (
    _aggregate_per_protocol,
    _fetch_neighbours,
    recommend_protocol,
)


def _row(
    problem: str,
    protocol: str,
    status: str = "completed",
    task_score: float | None = 0.8,
    cost: float = 0.1,
) -> MagicMock:
    metrics = {"task_completion_score": task_score} if task_score is not None else {}
    return MagicMock(
        id=uuid.uuid4(),
        problem=problem,
        protocol=protocol,
        status=status,
        metrics=metrics,
        total_cost_usd=cost,
    )


def _stub_session_returning(rows: list[MagicMock], monkeypatch) -> None:
    """Patch get_session to yield a fake session whose SELECT returns rows."""
    exec_result = MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=rows)))
    )
    session = MagicMock()
    session.execute = AsyncMock(return_value=exec_result)

    @asynccontextmanager
    async def factory():
        yield session

    monkeypatch.setattr(ps, "get_session", factory)


# ─── _fetch_neighbours (pure function) ─────────────────────────────────────


def test_fetch_neighbours_ranks_by_similarity():
    target = "debug a python memory leak in a web server"
    rows = [
        _row("completely unrelated legal question about tort law", "round_robin"),
        _row("python memory leak debugging in a flask web server", "debate"),
        _row("how to cook pasta carbonara with pancetta", "consensus"),
    ]
    ranked = _fetch_neighbours(target, rows, top_k=3, min_similarity=0.0)
    assert ranked[0][0].problem.startswith("python memory leak")
    # Irrelevant rows rank below the matching one
    assert ranked[-1][1] <= ranked[0][1]


def test_fetch_neighbours_filters_by_min_similarity():
    target = "very specific cloud networking problem"
    rows = [_row("chocolate cake recipe ingredients", "round_robin")]
    assert _fetch_neighbours(target, rows, top_k=5, min_similarity=0.5) == []


def test_fetch_neighbours_empty_candidates():
    assert _fetch_neighbours("anything", [], top_k=5, min_similarity=0.0) == []


# ─── _aggregate_per_protocol (pure function) ───────────────────────────────


def test_aggregate_favours_higher_task_score():
    neighbours = [
        (_row("p1", "round_robin", task_score=0.2, cost=0.1), 0.9),
        (_row("p2", "round_robin", task_score=0.3, cost=0.1), 0.9),
        (_row("p3", "debate", task_score=0.9, cost=0.1), 0.9),
        (_row("p4", "debate", task_score=0.85, cost=0.1), 0.9),
    ]
    scored = _aggregate_per_protocol(
        neighbours, cost_weight=0.1, task_weight=0.8, success_weight=0.1
    )
    assert scored[0].protocol == "debate"


def test_aggregate_favours_lower_cost_when_scores_tied():
    neighbours = [
        (_row("p1", "round_robin", task_score=0.8, cost=0.50), 1.0),
        (_row("p2", "consensus", task_score=0.8, cost=0.05), 1.0),
    ]
    scored = _aggregate_per_protocol(
        neighbours, cost_weight=0.5, task_weight=0.4, success_weight=0.1
    )
    # consensus costs less → higher cost_fit → wins
    assert scored[0].protocol == "consensus"


def test_aggregate_counts_failed_runs_against_success_rate():
    neighbours = [
        (_row("p1", "round_robin", status="failed", task_score=0.9, cost=0.0), 1.0),
        (_row("p2", "debate", status="converged", task_score=0.9, cost=0.0), 1.0),
    ]
    scored = _aggregate_per_protocol(
        neighbours, cost_weight=0.0, task_weight=0.0, success_weight=1.0
    )
    top = scored[0]
    assert top.protocol == "debate"
    assert top.success_rate == 1.0


# ─── recommend_protocol (end-to-end) ───────────────────────────────────────


@pytest.mark.asyncio
async def test_recommend_returns_round_robin_when_no_history(monkeypatch):
    _stub_session_returning([], monkeypatch)
    rec = await recommend_protocol("new problem")
    assert rec.recommended == "round_robin"
    assert "no historical" in rec.reason


@pytest.mark.asyncio
async def test_recommend_returns_round_robin_on_empty_problem(monkeypatch):
    _stub_session_returning([_row("x", "debate")], monkeypatch)
    rec = await recommend_protocol("   ")
    assert rec.recommended == "round_robin"
    assert "empty" in rec.reason


@pytest.mark.asyncio
async def test_recommend_picks_protocol_matching_history(monkeypatch):
    rows = [
        _row("debug python memory leak flask", "consensus", task_score=0.9, cost=0.05),
        _row("debug python memory leak flask app", "consensus", task_score=0.85, cost=0.06),
        _row("legal question about tort law negligence", "debate", task_score=0.3, cost=0.5),
    ]
    _stub_session_returning(rows, monkeypatch)
    rec = await recommend_protocol(
        "debug a python memory leak in a flask server", min_similarity=0.05
    )
    assert rec.recommended == "consensus"
    assert rec.per_protocol
    assert rec.neighbours  # expose the neighbours used for transparency


@pytest.mark.asyncio
async def test_recommend_to_dict_shape(monkeypatch):
    rows = [_row("some debug problem", "debate", task_score=0.7, cost=0.1)]
    _stub_session_returning(rows, monkeypatch)
    rec = await recommend_protocol("another debug problem", min_similarity=0.05)
    d = rec.to_dict()
    assert set(d.keys()) == {"recommended", "reason", "per_protocol", "neighbours"}
    assert d["recommended"] == "debate"
