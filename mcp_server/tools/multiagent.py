"""MCP tool: run a multi-agent group and evaluate their collaboration.

Imports the otelmind package lazily so the published `otelmind-mcp` wheel
loads cleanly without it. Calling this tool without otelmind installed
raises a clear ImportError.
"""

from __future__ import annotations

from typing import Any

# Built lazily on first call so module import doesn't depend on otelmind
_PROTOCOL_MAP: dict[str, type] = {}


def _ensure_protocols() -> dict[str, type]:
    global _PROTOCOL_MAP
    if _PROTOCOL_MAP:
        return _PROTOCOL_MAP
    try:
        from otelmind.multiagent.protocols import (
            BlackboardProtocol,
            ConsensusProtocol,
            DebateProtocol,
            DelegationProtocol,
            RoundRobinProtocol,
        )
    except ImportError as exc:
        raise ImportError(
            "run_multiagent_eval requires the otelmind package. "
            "Install with: pip install otelmind  (or `pip install otelmind-mcp[full]`)"
        ) from exc
    _PROTOCOL_MAP = {
        "round_robin": RoundRobinProtocol,
        "debate": DebateProtocol,
        "blackboard": BlackboardProtocol,
        "consensus": ConsensusProtocol,
        "delegation": DelegationProtocol,
    }
    return _PROTOCOL_MAP


def _role_from_dict(spec: dict[str, Any]) -> Any:
    from otelmind.multiagent.roles import AgentRole

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

    protocol_map = _ensure_protocols()
    protocol_cls = protocol_map.get(protocol.lower())
    if protocol_cls is None:
        raise ValueError(f"unknown protocol {protocol!r}; choose one of {list(protocol_map)}")

    from otelmind.eval.group_metrics import evaluate_group
    from otelmind.multiagent.group import AgentGroup

    role_objs = [_role_from_dict(r) for r in roles]
    protocol_instance = protocol_cls(max_rounds=max_rounds)
    group = AgentGroup(roles=role_objs, protocol=protocol_instance, max_rounds=max_rounds)

    result = await group.solve(problem=problem)
    metrics = await evaluate_group(result, expected_output=expected_output, max_rounds=max_rounds)

    return {
        "result": result.to_dict(),
        "metrics": metrics.to_dict(),
    }
