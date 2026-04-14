"""Tests for otelmind.eval.regression — baseline vs candidate comparisons."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from otelmind.eval.judge import DimensionScore, JudgeResult
from otelmind.eval.regression import EvalCase, run_regression


def _result(dim_score: float) -> JudgeResult:
    raw = max(1, min(5, round(dim_score * 4) + 1))
    scores = {
        "faithfulness": DimensionScore("faithfulness", dim_score, raw, "", "heuristic"),
        "relevance": DimensionScore("relevance", dim_score, raw, "", "heuristic"),
        "coherence": DimensionScore("coherence", dim_score, raw, "", "heuristic"),
    }
    return JudgeResult("q", "a", "", scores, dim_score)


@pytest.mark.asyncio
async def test_passing_regression_no_regressions():
    baseline = [EvalCase(id="c1", question="q", expected="e", actual="a1")]
    candidate = [EvalCase(id="c1", question="q", expected="e", actual="a1-v2")]

    async def fake_score(self, question, answer, context, dimensions=None):
        return _result(0.8 if answer == "a1" else 0.82)

    with patch("otelmind.eval.regression.LLMJudge.score", fake_score):
        report = await run_regression(baseline, candidate)

    assert report.passed is True
    assert report.regressions == []
    assert report.summary["total_cases"] == 1


@pytest.mark.asyncio
async def test_regression_flagged_when_candidate_drops():
    baseline = [EvalCase(id="c1", question="q", expected="e", actual="good")]
    candidate = [EvalCase(id="c1", question="q", expected="e", actual="bad")]

    async def fake_score(self, question, answer, context, dimensions=None):
        return _result(0.9 if answer == "good" else 0.3)

    with patch("otelmind.eval.regression.LLMJudge.score", fake_score):
        report = await run_regression(baseline, candidate, regression_threshold=0.05)

    assert report.passed is False
    assert len(report.regressions) == 1
    assert report.regressions[0]["id"] == "c1"


@pytest.mark.asyncio
async def test_improvement_flagged():
    baseline = [EvalCase(id="c1", question="q", expected="e", actual="bad")]
    candidate = [EvalCase(id="c1", question="q", expected="e", actual="great")]

    async def fake_score(self, question, answer, context, dimensions=None):
        return _result(0.3 if answer == "bad" else 0.9)

    with patch("otelmind.eval.regression.LLMJudge.score", fake_score):
        report = await run_regression(baseline, candidate, regression_threshold=0.05)

    assert len(report.improvements) == 1
    assert report.improvements[0]["id"] == "c1"


@pytest.mark.asyncio
async def test_mismatched_ids_only_common_compared():
    baseline = [
        EvalCase(id="a", question="q", expected="e", actual="x"),
        EvalCase(id="b", question="q", expected="e", actual="x"),
    ]
    candidate = [
        EvalCase(id="b", question="q", expected="e", actual="x"),
        EvalCase(id="c", question="q", expected="e", actual="x"),
    ]

    async def fake_score(self, question, answer, context, dimensions=None):
        return _result(0.5)

    with patch("otelmind.eval.regression.LLMJudge.score", fake_score):
        report = await run_regression(baseline, candidate)

    assert report.summary["total_cases"] == 1


@pytest.mark.asyncio
async def test_per_case_dimension_deltas():
    baseline = [EvalCase(id="c1", question="q", expected="e", actual="base")]
    candidate = [EvalCase(id="c1", question="q", expected="e", actual="cand")]

    async def fake_score(self, question, answer, context, dimensions=None):
        return _result(0.7 if answer == "base" else 0.5)

    with patch("otelmind.eval.regression.LLMJudge.score", fake_score):
        report = await run_regression(baseline, candidate, regression_threshold=0.1)

    assert report.per_case
    case = report.per_case[0]
    assert case["delta_overall"] == pytest.approx(-0.2, abs=1e-3)
    for dim in ("faithfulness", "relevance", "coherence"):
        assert case["dimensions"][dim]["delta"] < 0


@pytest.mark.asyncio
async def test_empty_common_ids_returns_passing():
    baseline = [EvalCase(id="a", question="q", expected="", actual="x")]
    candidate = [EvalCase(id="z", question="q", expected="", actual="y")]

    async def fake_score(self, question, answer, context, dimensions=None):
        return _result(0.5)

    with patch("otelmind.eval.regression.LLMJudge.score", fake_score):
        report = await run_regression(baseline, candidate)

    assert report.summary["total_cases"] == 0
    assert report.passed is True
