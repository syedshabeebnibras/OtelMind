"""MCP tool: run a multi-agent group and evaluate their collaboration."""

from __future__ import annotations

from typing import Any

from otelmind.eval.group_metrics import evaluate_group
from otelmind.multiagent.group import AgentGroup
from otelmind.multiagent.protocols import (
    BlackboardProtocol,
    ConsensusProtocol,
    DebateProtocol,
    DelegationProtocol,
    RoundRobinProtocol,
)
from otelmind.multiagent.roles import AgentRole

_PROTOCOL_MAP = {
    "round_robin": RoundRobinProtocol,
    "debate": DebateProtocol,
    "blackboard": BlackboardProtocol,
    "consensus": ConsensusProtocol,
    "delegation": DelegationProtocol,
}


def _role_from_dict(spec: dict[str, Any]) -> AgentRole:
    name = str(spec.get("name") or spec.get("role") or "agent")
    return AgentRole(
        name=name,
        system_prompt=str(spec.get("system_prompt", "")),
        tools=spec.get("tools"),
        model=str(spec.get("model", "")),
        max_tokens=int(spec.get("max_tokens", 4096)),
        temperature=float(spec.get("temperature", 0.7)),
        metadata=dict(spec.get("metadata", {})),
    )


async def run_multiagent_eval_tool(
    problem: str,
    roles: list[dict[str, Any]],
    protocol: str = "round_robin",
    max_rounds: int = 5,
    expected_output: str | None = None,
) -> dict[str, Any]:
    """Spawn an AgentGroup, run the chosen protocol, return result + metrics."""
    if not roles:
        raise ValueError("at least one role is required")

    protocol_cls = _PROTOCOL_MAP.get(protocol.lower())
    if protocol_cls is None:
        raise ValueError(f"unknown protocol {protocol!r}; choose one of {list(_PROTOCOL_MAP)}")

    role_objs = [_role_from_dict(r) for r in roles]
    protocol_instance = protocol_cls(max_rounds=max_rounds)
    group = AgentGroup(roles=role_objs, protocol=protocol_instance, max_rounds=max_rounds)

    result = await group.solve(problem=problem)
    metrics = await evaluate_group(result, expected_output=expected_output, max_rounds=max_rounds)

    return {
        "result": result.to_dict(),
        "metrics": metrics.to_dict(),
    }
