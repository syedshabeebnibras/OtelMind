"""Tests for AgentGroup budget enforcement."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from otelmind.multiagent.group import AgentGroup
from otelmind.multiagent.protocols import RoundRobinProtocol
from otelmind.multiagent.roles import AgentRole


def _roles(n: int = 2) -> list[AgentRole]:
    return [AgentRole(name=f"r{i}", system_prompt="p", model="claude-test") for i in range(n)]


@pytest.mark.asyncio
async def test_budget_none_runs_to_max_rounds():
    """No budget = no early exit. Runs all max_rounds rounds."""
    group = AgentGroup(
        roles=_roles(2),
        protocol=RoundRobinProtocol(max_rounds=3),
        api_key="fake",
        max_rounds=3,
        budget_usd=None,
    )

    async def mock_call(self, agent, messages, round_number):
        return "ok", {"prompt_tokens": 100, "completion_tokens": 100, "total_tokens": 200}

    with patch.object(AgentGroup, "_call_agent", mock_call):
        result = await group.solve("test")

    assert result.rounds_completed == 3
    assert result.budget_usd is None
    assert result.budget_remaining_usd is None
    assert result.cost_usd > 0
    assert result.status != "budget_exceeded"


@pytest.mark.asyncio
async def test_budget_enforced_stops_after_first_round():
    """Tiny budget — group should stop after round 1 once cost exceeds it."""
    group = AgentGroup(
        roles=_roles(2),
        protocol=RoundRobinProtocol(max_rounds=10),
        api_key="fake",
        max_rounds=10,
        budget_usd=0.001,  # ~$0.001 — easily exceeded by 2 agents @ 1k tokens each
    )

    async def mock_call(self, agent, messages, round_number):
        # 1000 prompt + 1000 completion = pricing of (1000*3 + 1000*15)/1e6 = $0.018
        # Per agent per round, 2 agents → ~$0.036/round, way over $0.001
        return "x" * 50, {
            "prompt_tokens": 1000,
            "completion_tokens": 1000,
            "total_tokens": 2000,
        }

    with patch.object(AgentGroup, "_call_agent", mock_call):
        result = await group.solve("test")

    assert result.status == "budget_exceeded"
    assert result.rounds_completed == 1  # stopped before round 2
    assert result.cost_usd > 0.001


@pytest.mark.asyncio
async def test_budget_remaining_usd_calculated():
    """budget_remaining_usd = budget - cost (clamped to 0)."""
    group = AgentGroup(
        roles=_roles(1),
        protocol=RoundRobinProtocol(max_rounds=1),
        api_key="fake",
        max_rounds=1,
        budget_usd=10.0,
    )

    async def mock_call(self, agent, messages, round_number):
        return "x", {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}

    with patch.object(AgentGroup, "_call_agent", mock_call):
        result = await group.solve("test")

    assert result.budget_usd == 10.0
    assert result.budget_remaining_usd is not None
    assert 0.0 <= result.budget_remaining_usd <= 10.0
    assert abs(result.budget_remaining_usd - (10.0 - result.cost_usd)) < 1e-9


@pytest.mark.asyncio
async def test_budget_remaining_clamped_to_zero():
    """When cost > budget, remaining is 0, not negative."""
    group = AgentGroup(
        roles=_roles(2),
        protocol=RoundRobinProtocol(max_rounds=10),
        api_key="fake",
        max_rounds=10,
        budget_usd=0.0001,
    )

    async def mock_call(self, agent, messages, round_number):
        return "x", {"prompt_tokens": 1000, "completion_tokens": 1000, "total_tokens": 2000}

    with patch.object(AgentGroup, "_call_agent", mock_call):
        result = await group.solve("test")

    assert result.budget_remaining_usd == 0.0


@pytest.mark.asyncio
async def test_budget_serializes_into_to_dict():
    group = AgentGroup(
        roles=_roles(1),
        protocol=RoundRobinProtocol(max_rounds=1),
        api_key="fake",
        max_rounds=1,
        budget_usd=5.0,
    )

    async def mock_call(self, agent, messages, round_number):
        return "x", {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}

    with patch.object(AgentGroup, "_call_agent", mock_call):
        result = await group.solve("test")

    d = result.to_dict()
    assert d["budget_usd"] == 5.0
    assert "budget_remaining_usd" in d
    assert "cost_usd" in d
    assert d["cost_usd"] >= 0
