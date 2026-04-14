"""Tests for otelmind.multiagent.group — AgentGroup orchestration."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from otelmind.multiagent.group import AgentGroup
from otelmind.multiagent.protocols import RoundRobinProtocol
from otelmind.multiagent.roles import AgentRole


def _roles(n: int = 2) -> list[AgentRole]:
    return [AgentRole(name=f"r{i}", system_prompt="p", model="claude-test") for i in range(n)]


@pytest.mark.asyncio
async def test_group_empty_roles_raises():
    with pytest.raises(ValueError):
        AgentGroup(roles=[], protocol=RoundRobinProtocol(), api_key="k", max_rounds=1)


@pytest.mark.asyncio
async def test_group_solve_end_to_end_with_mock():
    group = AgentGroup(
        roles=_roles(2),
        protocol=RoundRobinProtocol(max_rounds=2),
        api_key="fake",
        max_rounds=2,
    )

    async def mock_call(self, agent, messages, round_number):
        return f"r{round_number}:{agent.role.name}", {
            "prompt_tokens": 5,
            "completion_tokens": 5,
            "total_tokens": 10,
        }

    with patch.object(AgentGroup, "_call_agent", mock_call):
        result = await group.solve("do the thing")

    assert result.rounds_completed == 2
    # total_tokens comes from agent.tokens_used, which is incremented inside the
    # real _call_agent — with that patched out, we verify token accounting via
    # the per-message usage instead.
    total_msg_tokens = sum(m.token_usage["total_tokens"] for m in result.messages)
    assert total_msg_tokens == 40
    assert len(result.messages) == 4
    assert result.final_output is not None
    assert result.status in {"in_progress", "completed", "deadlocked"}


@pytest.mark.asyncio
async def test_group_to_dict_serializable():
    group = AgentGroup(
        roles=_roles(1),
        protocol=RoundRobinProtocol(max_rounds=1),
        api_key="fake",
        max_rounds=1,
    )

    async def mock_call(self, agent, messages, round_number):
        return "answer", {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}

    with patch.object(AgentGroup, "_call_agent", mock_call):
        result = await group.solve("x")

    d = result.to_dict()
    assert d["protocol"] == "RoundRobinProtocol"
    assert d["rounds_completed"] == 1
    assert d["messages"][0]["content"] == "answer"
    assert d["messages"][0]["token_usage"]["total_tokens"] == 2


@pytest.mark.asyncio
async def test_group_protocol_failure_marks_state_failed():
    group = AgentGroup(
        roles=_roles(2),
        protocol=RoundRobinProtocol(max_rounds=3),
        api_key="fake",
        max_rounds=3,
    )

    async def always_fail(self, agent, messages, round_number):
        raise RuntimeError("API exploded")

    with patch.object(AgentGroup, "_call_agent", always_fail):
        result = await group.solve("x")

    assert result.status == "failed"
    assert "error" in result.shared_context


def test_group_ensure_client_requires_api_key(monkeypatch):
    from otelmind.multiagent import group as group_module

    monkeypatch.setattr(group_module.settings, "anthropic_api_key", "")
    group = AgentGroup(roles=_roles(1), protocol=RoundRobinProtocol(), api_key="")
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        group._ensure_client()


@pytest.mark.asyncio
async def test_group_stops_at_max_rounds():
    group = AgentGroup(
        roles=_roles(1),
        protocol=RoundRobinProtocol(max_rounds=5),
        api_key="fake",
        max_rounds=4,
    )

    async def mock_call(self, agent, messages, round_number):
        return "x", {"total_tokens": 1}

    with patch.object(AgentGroup, "_call_agent", mock_call):
        result = await group.solve("problem")

    assert result.rounds_completed == 4
