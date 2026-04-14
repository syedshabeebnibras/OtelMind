"""Tests for otelmind.eval.judge — multi-dimensional LLM judge."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from otelmind.eval.judge import _DIMENSION_WEIGHTS, DIMENSIONS, LLMJudge


def test_dimension_weights_sum_to_one():
    total = sum(_DIMENSION_WEIGHTS.values())
    assert abs(total - 1.0) < 1e-9


def test_dimensions_list_matches_weights():
    assert set(DIMENSIONS) == set(_DIMENSION_WEIGHTS)


@pytest.mark.asyncio
async def test_heuristic_fallback_without_api_key():
    judge = LLMJudge(api_key=None)
    result = await judge.score("What is 2+2?", "Four", "2+2=4")
    assert 0.0 <= result.overall <= 1.0
    for dim in DIMENSIONS:
        if dim in result.scores:
            score = result.scores[dim]
            assert 0.0 <= score.score <= 1.0
            assert 1 <= score.raw_score <= 5
            assert score.method == "heuristic"


@pytest.mark.asyncio
async def test_heuristic_fallback_short_answer_low_coherence():
    judge = LLMJudge(api_key=None)
    result = await judge.score("q", "hi", "", ["coherence"])
    assert result.scores["coherence"].score < 0.5


@pytest.mark.asyncio
async def test_llm_scoring_maps_raw_to_normalized():
    judge = LLMJudge(api_key="fake", model="gpt-4o")

    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = json.dumps({"score": 5, "reason": "perfect"})

    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=response)

    with patch("openai.AsyncOpenAI", return_value=client):
        result = await judge.score("q", "a", "c", ["faithfulness"])

    assert result.scores["faithfulness"].raw_score == 5
    assert result.scores["faithfulness"].score == 1.0
    assert result.scores["faithfulness"].method == "llm"


@pytest.mark.asyncio
async def test_llm_scoring_malformed_falls_back():
    judge = LLMJudge(api_key="fake")

    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = "this is not JSON"

    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=response)

    with patch("openai.AsyncOpenAI", return_value=client):
        result = await judge.score("q", "a", "c", ["faithfulness"])

    score = result.scores["faithfulness"]
    assert score.method == "heuristic"
    assert score.score == 0.5


@pytest.mark.asyncio
async def test_llm_scoring_clamps_raw_score_to_1_5():
    judge = LLMJudge(api_key="fake")

    responses_sequence = [
        json.dumps({"score": 99, "reason": "out of range high"}),
        json.dumps({"score": -5, "reason": "out of range low"}),
    ]

    def make_resp(content):
        r = MagicMock()
        r.choices = [MagicMock()]
        r.choices[0].message.content = content
        return r

    call_idx = 0

    async def fake_create(*args, **kwargs):
        nonlocal call_idx
        r = make_resp(responses_sequence[call_idx])
        call_idx += 1
        return r

    client = MagicMock()
    client.chat.completions.create = fake_create

    with patch("openai.AsyncOpenAI", return_value=client):
        result = await judge.score("q", "a", "c", ["faithfulness", "relevance"])

    assert result.scores["faithfulness"].raw_score == 5
    assert result.scores["relevance"].raw_score == 1


@pytest.mark.asyncio
async def test_judge_runs_dimensions_concurrently():
    judge = LLMJudge(api_key="fake")

    import asyncio

    concurrent = 0
    max_concurrent = 0

    async def fake_create(*args, **kwargs):
        nonlocal concurrent, max_concurrent
        concurrent += 1
        max_concurrent = max(max_concurrent, concurrent)
        await asyncio.sleep(0.02)
        concurrent -= 1
        r = MagicMock()
        r.choices = [MagicMock()]
        r.choices[0].message.content = json.dumps({"score": 3, "reason": "mid"})
        return r

    client = MagicMock()
    client.chat.completions.create = fake_create

    with patch("openai.AsyncOpenAI", return_value=client):
        await judge.score("q", "a", "c", ["faithfulness", "relevance", "coherence"])
    assert max_concurrent > 1


def test_judge_result_to_dict_shape():
    from otelmind.eval.judge import DimensionScore, JudgeResult

    scores = {
        "faithfulness": DimensionScore("faithfulness", 0.8, 4, "good", "llm"),
    }
    result = JudgeResult("q", "a", "c", scores, 0.8)
    out = result.to_dict()
    assert out["overall"] == 0.8
    assert out["scores"]["faithfulness"]["raw_score"] == 4
    assert out["scores"]["faithfulness"]["method"] == "llm"
