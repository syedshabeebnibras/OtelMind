"""Tests for otelmind.eval.group_metrics — multi-agent collaboration scoring."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from otelmind.eval.group_metrics import _count_corrections, _dominance_score, evaluate_group
from otelmind.multiagent.group import GroupMessage, GroupResult
from otelmind.multiagent.roles import AgentRole


def _msg(sender_id: str, role: str, content: str, tokens: int, round_n: int = 1) -> GroupMessage:
    return GroupMessage(
        sender_id=sender_id,
        sender_role=role,
        content=content,
        round_number=round_n,
        timestamp=datetime.now(UTC),
        token_usage={
            "prompt_tokens": tokens // 2,
            "completion_tokens": tokens // 2,
            "total_tokens": tokens,
        },
    )


def _group_result(
    messages: list[GroupMessage], status: str = "completed", rounds: int = 1
) -> GroupResult:
    now = datetime.now(UTC)
    return GroupResult(
        problem="p",
        protocol="RoundRobin",
        final_output=messages[-1].content if messages else None,
        status=status,
        rounds_completed=rounds,
        total_tokens=sum(m.token_usage["total_tokens"] for m in messages if m.token_usage),
        messages=messages,
        roles=[AgentRole(name="r", system_prompt="p")],
        shared_context={},
        started_at=now,
        completed_at=now,
    )


def test_count_corrections_detects_disagreement():
    assert _count_corrections("Actually, that's not right") >= 1
    assert _count_corrections("I disagree with the conclusion") >= 1
    assert _count_corrections("The correct answer is different") >= 1
    assert _count_corrections("Looks good to me") == 0


def test_dominance_balanced_distribution_high():
    score = _dominance_score({"a": 100, "b": 100, "c": 100})
    assert score == pytest.approx(1.0)


def test_dominance_skewed_low():
    score = _dominance_score({"a": 1000, "b": 10, "c": 5})
    assert score < 0.5


def test_dominance_single_agent_is_one():
    assert _dominance_score({"solo": 500}) == 1.0


def test_dominance_empty_is_zero():
    assert _dominance_score({}) == 0.0


@pytest.mark.asyncio
async def test_evaluate_group_converged_high_convergence_rate():
    messages = [
        _msg("a-0", "coder", "proposal one — " + "x" * 60, 100),
        _msg("b-0", "reviewer", "looks good to me", 50),
    ]
    result = _group_result(messages, status="converged", rounds=2)
    eval_result = await evaluate_group(result, max_rounds=10)
    assert eval_result.convergence_rate > 0.7
    assert eval_result.rounds_to_completion == 2
    assert eval_result.deadlock_occurred is False


@pytest.mark.asyncio
async def test_evaluate_group_deadlock_sets_flag():
    messages = [_msg("a", "r", "no agreement", 10)]
    result = _group_result(messages, status="deadlocked", rounds=5)
    eval_result = await evaluate_group(result, max_rounds=5)
    assert eval_result.deadlock_occurred is True
    assert eval_result.convergence_rate == 0.0


@pytest.mark.asyncio
async def test_evaluate_group_error_corrections_counted():
    messages = [
        _msg("a-0", "coder", "answer is 42 " + "x" * 60, 100),
        _msg("b-0", "reviewer", "Actually, I disagree — that is wrong", 100),
    ]
    result = _group_result(messages, status="completed", rounds=1)
    eval_result = await evaluate_group(result, max_rounds=5)
    assert eval_result.error_correction_count >= 2
    assert eval_result.per_agent_stats["b-0"].corrections_made >= 2
    assert eval_result.per_agent_stats["a-0"].corrections_received >= 2


@pytest.mark.asyncio
async def test_evaluate_group_per_agent_contribution_ratio_sums_to_one():
    messages = [
        _msg("a", "coder", "x" * 60, 100),
        _msg("b", "reviewer", "y" * 60, 100),
        _msg("c", "critic", "z" * 60, 100),
    ]
    result = _group_result(messages, status="completed", rounds=1)
    eval_result = await evaluate_group(result, max_rounds=3)
    total = sum(s.contribution_ratio for s in eval_result.per_agent_stats.values())
    assert abs(total - 1.0) < 1e-6


@pytest.mark.asyncio
async def test_evaluate_group_empty_messages_handled():
    result = _group_result([], status="completed", rounds=0)
    eval_result = await evaluate_group(result, max_rounds=5)
    assert eval_result.communication_efficiency == 0.0
    assert eval_result.per_agent_stats == {}


@pytest.mark.asyncio
async def test_evaluate_group_uses_llm_correction_detector_when_judge_has_key(monkeypatch):
    """When the judge has an API key, evaluate_group routes correction detection
    through the LLM auditor instead of the regex pattern matcher."""
    from unittest.mock import AsyncMock, MagicMock, patch

    messages = [
        _msg("a-0", "coder", "x" * 80, 100),
        _msg("b-0", "reviewer", "y" * 80, 100),
        _msg("a-0", "coder", "z" * 80, 100),
    ]
    result = _group_result(messages, status="completed", rounds=1)

    judge = MagicMock()
    judge._api_key = "fake-key"
    judge._model = "gpt-4o"

    async def fake_detect(msgs, j):
        return {
            "b-0": {"corrections_made": 1, "corrections_received": 0},
            "a-0": {"corrections_made": 0, "corrections_received": 1},
        }

    with patch(
        "otelmind.eval.group_metrics._detect_corrections_with_llm",
        AsyncMock(side_effect=fake_detect),
    ):
        eval_result = await evaluate_group(result, judge=judge, max_rounds=5)

    assert eval_result.error_correction_count == 1
    assert eval_result.per_agent_stats["b-0"].corrections_made == 1
    assert eval_result.per_agent_stats["a-0"].corrections_received == 1


@pytest.mark.asyncio
async def test_evaluate_group_falls_back_to_regex_when_no_judge_key():
    """No judge key => regex detector path."""
    messages = [
        _msg("a-0", "coder", "answer is 42 " + "x" * 60, 100),
        _msg("b-0", "reviewer", "actually that is wrong, the answer is 43", 100),
    ]
    result = _group_result(messages, status="completed", rounds=1)
    eval_result = await evaluate_group(result, max_rounds=5)
    # Regex sees "actually" + "that is wrong" → at least 1 correction
    assert eval_result.error_correction_count >= 1


@pytest.mark.asyncio
async def test_evaluate_group_short_messages_count_as_redundant():
    messages = [_msg("a", "r", "ok", 10) for _ in range(5)]
    result = _group_result(messages, status="completed", rounds=1)
    eval_result = await evaluate_group(result, max_rounds=5)
    assert eval_result.communication_efficiency == 0.0
