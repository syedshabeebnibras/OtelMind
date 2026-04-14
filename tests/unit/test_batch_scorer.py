"""Tests for otelmind.eval.batch_scorer — parallel scoring with semaphore."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from otelmind.eval.batch_scorer import BatchScorer
from otelmind.eval.judge import DimensionScore, JudgeResult
from otelmind.eval.regression import EvalCase


def _make_judge_result(overall: float = 0.8) -> JudgeResult:
    scores = {
        "faithfulness": DimensionScore(
            dimension="faithfulness",
            score=overall,
            raw_score=round(overall * 4) + 1,
            reason="",
            method="heuristic",
        ),
        "relevance": DimensionScore(
            dimension="relevance",
            score=overall * 0.9,
            raw_score=round(overall * 3.6) + 1,
            reason="",
            method="heuristic",
        ),
    }
    return JudgeResult(question="q", answer="a", context="", scores=scores, overall=overall)


def _cases(n: int) -> list[EvalCase]:
    return [EvalCase(id=f"c{i}", question="q", expected="e", actual="a") for i in range(n)]


@pytest.mark.asyncio
async def test_batch_scorer_scores_all_cases():
    judge = MagicMock()
    judge.score = AsyncMock(return_value=_make_judge_result(0.8))
    scorer = BatchScorer(judge=judge, concurrency=3)

    result = await scorer.score_batch(_cases(5))

    assert result.total == 5
    assert result.scored == 5
    assert result.failed == 0
    assert len(result.per_case) == 5
    assert "faithfulness" in result.aggregate
    assert abs(result.aggregate["faithfulness"].mean - 0.8) < 1e-6


@pytest.mark.asyncio
async def test_batch_scorer_empty_cases():
    judge = MagicMock()
    judge.score = AsyncMock()
    scorer = BatchScorer(judge=judge, concurrency=2)
    result = await scorer.score_batch([])
    assert result.total == 0
    assert result.scored == 0
    assert result.failed == 0
    judge.score.assert_not_called()


@pytest.mark.asyncio
async def test_batch_scorer_progress_callback_fires():
    judge = MagicMock()
    judge.score = AsyncMock(return_value=_make_judge_result(0.6))
    calls: list[tuple[int, int]] = []

    def cb(done: int, total: int) -> None:
        calls.append((done, total))

    scorer = BatchScorer(judge=judge, concurrency=2, progress_callback=cb)
    await scorer.score_batch(_cases(4))
    assert len(calls) == 4
    assert calls[-1] == (4, 4)
    assert all(total == 4 for _, total in calls)


@pytest.mark.asyncio
async def test_batch_scorer_handles_individual_failures():
    results = [
        _make_judge_result(0.9),
        RuntimeError("API down"),
        _make_judge_result(0.7),
    ]

    async def flaky(*args, **kwargs):
        r = results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    judge = MagicMock()
    judge.score = flaky
    scorer = BatchScorer(judge=judge, concurrency=1)

    result = await scorer.score_batch(_cases(3))
    assert result.total == 3
    assert result.scored == 2
    assert result.failed == 1
    errored = [c for c in result.per_case if c["error"] is not None]
    assert len(errored) == 1
    assert "API down" in errored[0]["error"]


@pytest.mark.asyncio
async def test_batch_scorer_respects_concurrency_limit():
    in_flight = 0
    peak = 0
    lock = asyncio.Lock()

    async def gated(*args, **kwargs):
        nonlocal in_flight, peak
        async with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        await asyncio.sleep(0.02)
        async with lock:
            in_flight -= 1
        return _make_judge_result(0.5)

    judge = MagicMock()
    judge.score = gated
    scorer = BatchScorer(judge=judge, concurrency=3)

    await scorer.score_batch(_cases(10))
    assert peak <= 3


@pytest.mark.asyncio
async def test_batch_scorer_aggregates_statistics():
    judge = MagicMock()
    seq = [0.1, 0.4, 0.5, 0.7, 0.9]
    call_idx = 0

    async def iter_scores(*args, **kwargs):
        nonlocal call_idx
        r = _make_judge_result(seq[call_idx])
        call_idx += 1
        return r

    judge.score = iter_scores
    scorer = BatchScorer(judge=judge, concurrency=1)
    result = await scorer.score_batch(_cases(5))

    agg = result.aggregate["faithfulness"]
    assert agg.n == 5
    assert agg.min == pytest.approx(0.1)
    assert agg.max == pytest.approx(0.9)
    assert abs(agg.mean - sum(seq) / 5) < 1e-6
    assert agg.p50 == pytest.approx(0.5)
