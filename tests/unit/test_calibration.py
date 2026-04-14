"""Tests for otelmind.eval.calibration — judge vs human labels."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from otelmind.eval.calibration import HumanLabel, calibrate_judge
from otelmind.eval.judge import DimensionScore, JudgeResult
from otelmind.eval.regression import EvalCase


def _judge_stub(mapping: dict[str, dict[str, float]]):
    """Return a judge whose score() looks up the caller's case by actual text."""
    judge = MagicMock()

    async def score(question: str, answer: str, context: str, dimensions=None):
        per_dim = mapping.get(answer, {})
        scores = {}
        for dim, value in per_dim.items():
            raw = max(1, min(5, round(value * 4) + 1))
            scores[dim] = DimensionScore(
                dimension=dim,
                score=value,
                raw_score=raw,
                reason="",
                method="heuristic",
            )
        return JudgeResult(
            question=question, answer=answer, context=context, scores=scores, overall=0.5
        )

    judge.score = score
    judge._model = "stub-judge"
    return judge


@pytest.mark.asyncio
async def test_calibrate_judge_perfect_agreement():
    judge = _judge_stub(
        {
            "answer-1": {"faithfulness": 0.8},
            "answer-2": {"faithfulness": 0.4},
        }
    )
    cases = [
        EvalCase(id="c1", question="q", expected="", actual="answer-1"),
        EvalCase(id="c2", question="q", expected="", actual="answer-2"),
    ]
    labels = [
        HumanLabel(case_id="c1", dimension="faithfulness", score=0.8),
        HumanLabel(case_id="c2", dimension="faithfulness", score=0.4),
    ]
    result = await calibrate_judge(judge, cases, labels)
    assert result.case_count == 2
    assert result.cohens_kappa == pytest.approx(1.0)
    assert result.agreement_rate == pytest.approx(1.0)
    assert abs(result.bias) < 1e-9


@pytest.mark.asyncio
async def test_calibrate_judge_positive_bias():
    # Judge scores higher than humans on every case
    judge = _judge_stub(
        {
            "a1": {"faithfulness": 0.9},
            "a2": {"faithfulness": 0.8},
            "a3": {"faithfulness": 0.7},
        }
    )
    cases = [
        EvalCase(id="c1", question="q", expected="", actual="a1"),
        EvalCase(id="c2", question="q", expected="", actual="a2"),
        EvalCase(id="c3", question="q", expected="", actual="a3"),
    ]
    labels = [
        HumanLabel(case_id="c1", dimension="faithfulness", score=0.6),
        HumanLabel(case_id="c2", dimension="faithfulness", score=0.5),
        HumanLabel(case_id="c3", dimension="faithfulness", score=0.4),
    ]
    result = await calibrate_judge(judge, cases, labels)
    assert result.bias > 0
    assert "faithfulness" in result.per_dimension
    assert result.per_dimension["faithfulness"].bias > 0


@pytest.mark.asyncio
async def test_calibrate_judge_calibration_curve_bins():
    judge = _judge_stub(
        {
            "a1": {"faithfulness": 0.2},
            "a2": {"faithfulness": 0.4},
            "a3": {"faithfulness": 0.8},
        }
    )
    cases = [
        EvalCase(id="c1", question="q", expected="", actual="a1"),
        EvalCase(id="c2", question="q", expected="", actual="a2"),
        EvalCase(id="c3", question="q", expected="", actual="a3"),
    ]
    labels = [
        HumanLabel(case_id="c1", dimension="faithfulness", score=0.25),
        HumanLabel(case_id="c2", dimension="faithfulness", score=0.45),
        HumanLabel(case_id="c3", dimension="faithfulness", score=0.75),
    ]
    result = await calibrate_judge(judge, cases, labels)
    assert result.calibration_curve
    # Every returned bucket has a predicted + actual
    for row in result.calibration_curve:
        assert "predicted" in row and "actual" in row
        assert row["n"] >= 1


@pytest.mark.asyncio
async def test_calibrate_judge_no_labels_empty_result():
    judge = _judge_stub({})
    result = await calibrate_judge(judge, [], [])
    assert result.case_count == 0
    assert result.cohens_kappa == 0.0
    assert result.agreement_rate == 0.0


@pytest.mark.asyncio
async def test_calibrate_judge_missing_case_skipped():
    judge = _judge_stub({"a1": {"faithfulness": 0.8}})
    cases = [EvalCase(id="c1", question="q", expected="", actual="a1")]
    labels = [
        HumanLabel(case_id="missing", dimension="faithfulness", score=0.5),
        HumanLabel(case_id="c1", dimension="faithfulness", score=0.8),
    ]
    result = await calibrate_judge(judge, cases, labels)
    assert result.case_count == 1


@pytest.mark.asyncio
async def test_calibrate_judge_handles_scoring_failure():
    judge = MagicMock()
    judge.score = AsyncMock(side_effect=RuntimeError("boom"))
    judge._model = "broken"
    cases = [EvalCase(id="c1", question="q", expected="", actual="a1")]
    labels = [HumanLabel(case_id="c1", dimension="faithfulness", score=0.5)]
    result = await calibrate_judge(judge, cases, labels)
    assert result.case_count == 0
