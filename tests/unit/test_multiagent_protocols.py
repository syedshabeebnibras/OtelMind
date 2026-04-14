"""Tests for otelmind.multiagent.protocols — communication strategies."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from otelmind.multiagent.group import AgentInstance, GroupState
from otelmind.multiagent.protocols import (
    BlackboardProtocol,
    ConsensusProtocol,
    DebateProtocol,
    DelegationProtocol,
    RoundRobinProtocol,
)
from otelmind.multiagent.roles import AgentRole
from otelmind.multiagent.tracer import GroupTracer


def _agent(name: str, idx: int = 0) -> AgentInstance:
    return AgentInstance(
        role=AgentRole(name=name, system_prompt="", model="claude-test"),
        agent_id=f"{name}-{idx}",
    )


def _state(problem: str = "solve this") -> GroupState:
    return GroupState(shared_context={"problem": problem})


@pytest.mark.asyncio
async def test_round_robin_calls_every_agent_in_order():
    agents = [_agent("coder", i) for i in range(3)]
    state = _state()
    proto = RoundRobinProtocol(max_rounds=1)
    tracer = GroupTracer()

    call_order: list[str] = []

    async def call_agent(agent, messages, round_number):
        call_order.append(agent.agent_id)
        return f"response from {agent.agent_id}", {"total_tokens": 10}

    result_state = await proto.execute_round(
        agents, state, round_number=1, call_agent=call_agent, tracer=tracer
    )
    assert [a.agent_id for a in agents] == call_order
    assert len(result_state.messages) == 3
    assert result_state.final_output == "response from coder-2"


@pytest.mark.asyncio
async def test_debate_requires_exactly_three_agents():
    proto = DebateProtocol(max_rounds=2)
    tracer = GroupTracer()
    with pytest.raises(ValueError):
        await proto.execute_round(
            [_agent("a"), _agent("b")],
            _state(),
            round_number=1,
            call_agent=AsyncMock(),
            tracer=tracer,
        )


@pytest.mark.asyncio
async def test_debate_converges_on_verdict():
    agents = [_agent("pro"), _agent("con"), _agent("judge")]
    state = _state()
    proto = DebateProtocol(max_rounds=1)
    tracer = GroupTracer()

    responses = {
        "pro-0": "pro argument",
        "con-0": "con argument",
        "judge-0": "VERDICT: pro wins",
    }

    async def call_agent(agent, messages, round_number):
        return responses[agent.agent_id], {"total_tokens": 5}

    result_state = await proto.execute_round(
        agents, state, round_number=1, call_agent=call_agent, tracer=tracer
    )
    assert result_state.status == "converged"
    assert result_state.final_output == "pro wins"


@pytest.mark.asyncio
async def test_blackboard_merges_json_updates():
    agents = [_agent("writer", 0), _agent("writer", 1)]
    state = _state()
    proto = BlackboardProtocol(max_rounds=5)
    tracer = GroupTracer()

    responses = {
        "writer-0": json.dumps({"section_1": "first"}),
        "writer-1": json.dumps({"section_2": "second"}),
    }

    async def call_agent(agent, messages, round_number):
        return responses[agent.agent_id], {"total_tokens": 10}

    result_state = await proto.execute_round(
        agents, state, round_number=1, call_agent=call_agent, tracer=tracer
    )
    board = result_state.shared_context["blackboard"]
    assert board == {"section_1": "first", "section_2": "second"}


@pytest.mark.asyncio
async def test_blackboard_supports_concurrent_reads():
    """Each agent in a round reads the blackboard state independently.

    A concurrent-style reader should never see torn updates: within one round,
    every agent observes the same pre-round blackboard snapshot, not partial
    mid-round writes. We simulate two agents that read-then-write and verify
    that both writes land (no lost updates) and that both reads saw the
    blackboard in a consistent state.
    """
    import asyncio
    import json

    agents = [_agent("writer", 0), _agent("writer", 1)]
    proto = BlackboardProtocol(max_rounds=5)
    tracer = GroupTracer()
    state = _state()
    # Seed the blackboard with baseline state the agents should observe.
    state.shared_context["blackboard"] = {"seed": "initial"}

    reads_seen: list[dict] = []
    call_count = {"n": 0}

    async def call_agent(agent, messages, round_number):
        # Record what this agent saw on the blackboard (passed in the prompt).
        content = messages[0]["content"]
        start = content.find("Blackboard (current state): ") + len("Blackboard (current state): ")
        end = content.find("\n\nRespond with", start)
        board_text = content[start:end]
        reads_seen.append(json.loads(board_text))

        # Introduce a yield so both agents are in flight during the round.
        await asyncio.sleep(0)
        call_count["n"] += 1
        payload = {f"from-{agent.agent_id}": f"write-{call_count['n']}"}
        return json.dumps(payload), {"total_tokens": 4}

    result_state = await proto.execute_round(
        agents, state, round_number=1, call_agent=call_agent, tracer=tracer
    )

    # Both agents saw the initial seed — no torn reads
    assert len(reads_seen) == 2
    for snapshot in reads_seen:
        assert snapshot.get("seed") == "initial"

    # Both writes merged into the blackboard — no lost updates
    board = result_state.shared_context["blackboard"]
    assert board["seed"] == "initial"
    assert board["from-writer-0"] == "write-1"
    assert board["from-writer-1"] == "write-2"


@pytest.mark.asyncio
async def test_blackboard_convergence_on_repeated_empty_updates():
    agents = [_agent("a"), _agent("b")]
    proto = BlackboardProtocol(max_rounds=10)
    tracer = GroupTracer()
    state = _state()

    async def empty_call(agent, messages, round_number):
        return "{}", {"total_tokens": 3}

    for r in range(1, 3):
        state = await proto.execute_round(
            agents, state, round_number=r, call_agent=empty_call, tracer=tracer
        )
    assert state.status == "converged"


@pytest.mark.asyncio
async def test_consensus_detects_majority():
    agents = [_agent("voter", i) for i in range(3)]
    state = _state()
    proto = ConsensusProtocol(max_rounds=3)
    tracer = GroupTracer()

    responses = {
        "voter-0": "answer A",
        "voter-1": "answer A",
        "voter-2": "different",
    }

    async def call_agent(agent, messages, round_number):
        return responses[agent.agent_id], {"total_tokens": 5}

    result_state = await proto.execute_round(
        agents, state, round_number=1, call_agent=call_agent, tracer=tracer
    )
    assert result_state.status == "converged"
    assert result_state.final_output == "answer A"


@pytest.mark.asyncio
async def test_consensus_detects_semantic_agreement():
    """Two agents say the same thing differently — TF-IDF cosine clusters them.

    Literal-match tally would see 3 distinct strings and fail to find a
    majority. The semantic-similarity fallback should cluster the first two
    agents and declare convergence.
    """
    agents = [_agent("voter", i) for i in range(3)]
    state = _state()
    proto = ConsensusProtocol(max_rounds=3)
    tracer = GroupTracer()

    responses = {
        "voter-0": "the answer is forty two and the value is forty two",
        "voter-1": "forty two is the answer the value forty two answer",
        "voter-2": "completely unrelated banana cake recipe today",
    }

    async def call_agent(agent, messages, round_number):
        return responses[agent.agent_id], {"total_tokens": 5}

    result_state = await proto.execute_round(
        agents, state, round_number=1, call_agent=call_agent, tracer=tracer
    )
    assert result_state.status == "converged"
    assert result_state.final_output is not None
    # The winning output should be one of the two semantically-aligned voters
    assert result_state.final_output in {responses["voter-0"], responses["voter-1"]}


@pytest.mark.asyncio
async def test_consensus_deadlocks_after_max_rounds():
    agents = [_agent("voter", i) for i in range(3)]
    state = _state()
    proto = ConsensusProtocol(max_rounds=2)
    tracer = GroupTracer()

    # Three semantically distinct positions — neither literal nor TF-IDF
    # cosine should cluster these into a majority.
    responses = [
        "the recommendation is to migrate to postgres immediately",
        "kafka streaming with snowflake aggregation handles this best",
        "rewrite everything in rust using actix and async tokio runtime",
    ]
    idx = 0

    async def call_agent(agent, messages, round_number):
        nonlocal idx
        resp = responses[idx % 3]
        idx += 1
        return resp, {"total_tokens": 5}

    for r in range(1, 3):
        state = await proto.execute_round(
            agents, state, round_number=r, call_agent=call_agent, tracer=tracer
        )
    assert state.status == "deadlocked"
    assert state.final_output is not None


@pytest.mark.asyncio
async def test_delegation_dispatches_and_summarizes():
    lead = _agent("planner")
    coder = _agent("coder")
    reviewer = _agent("reviewer")
    agents = [lead, coder, reviewer]
    state = _state()
    proto = DelegationProtocol(max_rounds=2)
    tracer = GroupTracer()

    responses = {
        "planner-0-plan": json.dumps(
            [
                {"agent": "coder", "task": "write code"},
                {"agent": "reviewer", "task": "review"},
            ]
        ),
        "coder-0": "here is code",
        "reviewer-0": "LGTM",
        "planner-0-summary": "DONE: final answer",
    }

    call_count = {"planner-0": 0}

    async def call_agent(agent, messages, round_number):
        if agent.agent_id == "planner-0":
            call_count["planner-0"] += 1
            key = "planner-0-plan" if call_count["planner-0"] == 1 else "planner-0-summary"
            return responses[key], {"total_tokens": 10}
        return responses[agent.agent_id], {"total_tokens": 10}

    result_state = await proto.execute_round(
        agents, state, round_number=1, call_agent=call_agent, tracer=tracer
    )
    assert result_state.status == "converged"
    assert result_state.final_output == "final answer"
    # Lead called twice (plan + summary), each specialist once = 4 calls
    assert call_count["planner-0"] == 2
