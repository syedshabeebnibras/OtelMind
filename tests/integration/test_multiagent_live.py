"""Live integration tests for the multi-agent pipeline.

These tests actually hit the Anthropic API. They are gated on
ANTHROPIC_API_KEY being set, so default CI runs skip cleanly. A nightly
job with the key configured, or a developer running locally with a key
in `.env`, will execute them automatically.

What we verify end-to-end:
  - The anthropic SDK is reachable and auth works.
  - AgentGroup spins up role-specialized agents, runs a protocol round,
    aggregates token usage, and produces a real `final_output`.
  - evaluate_group can score the collaboration without blowing up on
    real-shaped data.
"""

from __future__ import annotations

import os

import pytest

from otelmind.eval.group_metrics import evaluate_group
from otelmind.multiagent.group import AgentGroup
from otelmind.multiagent.protocols import RoundRobinProtocol
from otelmind.multiagent.roles import coder_role, reviewer_role

_SKIP_REASON = (
    "ANTHROPIC_API_KEY not set — live multi-agent integration test skipped. "
    "Set the env var to run it."
)

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"),
        reason=_SKIP_REASON,
    ),
    pytest.mark.slow,
]


@pytest.mark.asyncio
async def test_round_robin_live_two_agents_one_round():
    """Two agents (coder + reviewer), one round, real Claude call.

    This is deliberately minimal — one round keeps it fast (<15 s) and
    cheap (<$0.02). We don't assert on output content because that varies
    by model version; we only assert the structural invariants that would
    break if the SDK wiring regressed.
    """
    roles = [coder_role("python"), reviewer_role()]
    group = AgentGroup(
        roles=roles,
        protocol=RoundRobinProtocol(max_rounds=1),
        max_rounds=1,
    )

    result = await group.solve(
        "In one line of Python, return the sum of two integers."
    )

    # Structural invariants — break only on real integration regression
    assert result.status == "completed", f"expected completed, got {result.status!r}"
    assert result.rounds_completed == 1
    assert result.total_tokens > 0, "no tokens reported — SDK wiring broke"
    assert len(result.messages) == 2, "expected one message per agent"
    assert result.final_output and result.final_output.strip(), "empty final output"

    # Token usage populated on each message (real API returns usage metadata)
    for msg in result.messages:
        assert msg.token_usage is not None, "token_usage missing on message"
        assert msg.token_usage["total_tokens"] > 0
        assert msg.sender_role in {"coder", "reviewer"}

    # evaluate_group runs on a real result without exploding
    metrics = await evaluate_group(result, max_rounds=1)
    assert metrics.total_tokens == result.total_tokens
    assert 0.0 <= metrics.dominance_score <= 1.0
    assert 0.0 <= metrics.communication_efficiency <= 1.0
    assert metrics.rounds_to_completion == 1


@pytest.mark.asyncio
async def test_live_respects_max_rounds_cap():
    """max_rounds is honoured end-to-end against the real API."""
    group = AgentGroup(
        roles=[coder_role("python")],
        protocol=RoundRobinProtocol(max_rounds=2),
        max_rounds=2,
    )
    result = await group.solve("Say 'ok' in one word.")
    assert result.rounds_completed <= 2
    assert result.total_tokens > 0
