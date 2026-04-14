"""Tests for otelmind.eval.meta_eval — the judge-the-judge auditor."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from otelmind.eval.judge import DimensionScore, JudgeResult
from otelmind.eval.meta_eval import MetaEvaluator
from otelmind.eval.regression import EvalCase


def _result(raw: int, dim: str = "faithfulness") -> JudgeResult:
    score = (raw - 1) / 4
    return JudgeResult(
        question="q",
        answer="a",
        context="",
        scores={
            dim: DimensionScore(
                dimension=dim,
                score=score,
                raw_score=raw,
                reason="judge reasoning",
                method="llm",
            )
        },
        overall=score,
    )


@pytest.mark.asyncio
async def test_meta_eval_empty_inputs():
    evaluator = MetaEvaluator(api_key="x")
    report = await evaluator.audit_scores([], [], sample_rate=0.5)
    assert report.total_audited == 0
    assert report.agreement_rate == 0.0


@pytest.mark.asyncio
async def test_meta_eval_length_mismatch_raises():
    evaluator = MetaEvaluator(api_key="x")
    with pytest.raises(ValueError):
        await evaluator.audit_scores(
            [EvalCase(id="c1", question="q", expected="", actual="a")],
            [_result(4), _result(3)],
        )


@pytest.mark.asyncio
async def test_meta_eval_zero_sample_rate_returns_empty():
    evaluator = MetaEvaluator(api_key="x")
    cases = [EvalCase(id="c1", question="q", expected="", actual="a")]
    report = await evaluator.audit_scores(cases, [_result(4)], sample_rate=0.0)
    assert report.total_audited == 0


@pytest.mark.asyncio
async def test_meta_eval_agreement_within_threshold():
    evaluator = MetaEvaluator(api_key="x", disagreement_threshold=1.0, seed=1)
    cases = [EvalCase(id=f"c{i}", question="q", expected="", actual="a") for i in range(5)]
    results = [_result(4) for _ in range(5)]

    async def _stub_call_auditor(self, case, dimension, judge_raw, judge_reason):
        return 4, "agree", True  # same as judge → agreement

    with patch.object(MetaEvaluator, "_call_auditor", _stub_call_auditor):
        report = await evaluator.audit_scores(cases, results, sample_rate=1.0)

    assert report.total_audited == 5
    assert report.agreements == 5
    assert report.disagreements == 0
    assert report.agreement_rate == pytest.approx(1.0)
    assert report.flagged_cases == []


@pytest.mark.asyncio
async def test_meta_eval_flags_disagreements():
    evaluator = MetaEvaluator(api_key="x", disagreement_threshold=1.0, seed=1)
    cases = [EvalCase(id=f"c{i}", question="q", expected="", actual="a") for i in range(4)]
    results = [_result(5) for _ in range(4)]  # judge says 5

    async def _stub_call_auditor(self, case, dimension, judge_raw, judge_reason):
        return 2, "auditor disagrees", True  # delta 3 > threshold 1

    with patch.object(MetaEvaluator, "_call_auditor", _stub_call_auditor):
        report = await evaluator.audit_scores(cases, results, sample_rate=1.0)

    assert report.total_audited == 4
    assert report.disagreements == 4
    assert report.agreements == 0
    assert len(report.flagged_cases) == 4
    flagged = report.flagged_cases[0]
    assert flagged.judge_score == pytest.approx(1.0)
    assert flagged.auditor_score == pytest.approx(0.25)
    assert flagged.score_delta < 0


@pytest.mark.asyncio
async def test_meta_eval_sample_rate_respected():
    evaluator = MetaEvaluator(api_key="x", disagreement_threshold=1.0, seed=1)
    cases = [EvalCase(id=f"c{i}", question="q", expected="", actual="a") for i in range(10)]
    results = [_result(3) for _ in range(10)]

    call_count = 0

    async def _stub_call_auditor(self, case, dimension, judge_raw, judge_reason):
        nonlocal call_count
        call_count += 1
        return 3, "agree", True

    with patch.object(MetaEvaluator, "_call_auditor", _stub_call_auditor):
        report = await evaluator.audit_scores(cases, results, sample_rate=0.3)

    # 10 * 0.3 = 3 sampled cases, one dimension each → 3 auditor calls
    assert call_count == 3
    assert report.total_audited == 3


@pytest.mark.asyncio
async def test_meta_eval_auditor_skip_when_no_api_key():
    evaluator = MetaEvaluator(api_key="")
    cases = [EvalCase(id="c1", question="q", expected="", actual="a")]
    results = [_result(4)]
    report = await evaluator.audit_scores(cases, results, sample_rate=1.0)
    # auditor returns False ok-flag → nothing audited
    assert report.total_audited == 0


@pytest.mark.asyncio
async def test_meta_eval_to_dict_shape():
    evaluator = MetaEvaluator(api_key="x")
    report = await evaluator.audit_scores([], [], sample_rate=0.0)
    d = report.to_dict()
    for key in (
        "total_audited",
        "agreements",
        "disagreements",
        "agreement_rate",
        "auditor_model",
        "primary_judge_model",
        "flagged_cases",
    ):
        assert key in d
