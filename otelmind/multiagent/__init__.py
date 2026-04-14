"""Multi-agent group evaluation — spawn roles, run a protocol, score collaboration."""

from __future__ import annotations

from otelmind.multiagent.group import (
    AgentGroup,
    AgentInstance,
    GroupMessage,
    GroupResult,
    GroupState,
)
from otelmind.multiagent.protocols import (
    BlackboardProtocol,
    CommunicationProtocol,
    ConsensusProtocol,
    DebateProtocol,
    DelegationProtocol,
    RoundRobinProtocol,
)
from otelmind.multiagent.roles import (
    AgentRole,
    coder_role,
    critic_role,
    planner_role,
    researcher_role,
    reviewer_role,
)
from otelmind.multiagent.tracer import GroupTracer

__all__ = [
    "AgentGroup",
    "AgentInstance",
    "AgentRole",
    "BlackboardProtocol",
    "CommunicationProtocol",
    "ConsensusProtocol",
    "DebateProtocol",
    "DelegationProtocol",
    "GroupMessage",
    "GroupResult",
    "GroupState",
    "GroupTracer",
    "RoundRobinProtocol",
    "coder_role",
    "critic_role",
    "planner_role",
    "researcher_role",
    "reviewer_role",
]
