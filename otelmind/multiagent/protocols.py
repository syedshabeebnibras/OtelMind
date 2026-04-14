"""Communication protocols for multi-agent groups.

Each protocol is a strategy class that executes one round of communication
given the agent list, shared state, and a callable that invokes the LLM for
a single agent turn. Protocols terminate early on convergence or deadlock.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from otelmind.multiagent.group import AgentInstance, GroupState
    from otelmind.multiagent.tracer import GroupTracer

CallAgentFn = Callable[
    ["AgentInstance", list[dict[str, Any]], int],
    Awaitable[tuple[str, dict[str, int]]],
]


def _broadcast_history(state: GroupState) -> list[dict[str, Any]]:
    """Format the group message history as an Anthropic-style message list.

    We alternate user/assistant by treating the current agent's own prior
    messages as "assistant" and everyone else's as "user" when building the
    context per agent. For broadcast transcripts we flatten to a single user
    message containing a chronological transcript.
    """
    if not state.messages:
        return [
            {
                "role": "user",
                "content": state.shared_context.get("problem", ""),
            }
        ]
    transcript_lines = [f"Problem: {state.shared_context.get('problem', '')}"]
    if state.shared_context.get("context"):
        transcript_lines.append(f"Context: {state.shared_context['context']}")
    transcript_lines.append("")
    transcript_lines.append("Conversation so far:")
    for m in state.messages:
        transcript_lines.append(f"[{m.sender_role}] {m.content}")
    return [
        {
            "role": "user",
            "content": "\n".join(transcript_lines) + "\n\nProvide your contribution now.",
        }
    ]


class CommunicationProtocol(ABC):
    """Strategy for a single communication round."""

    def __init__(self, *, max_rounds: int = 10) -> None:
        self.max_rounds = max_rounds

    @abstractmethod
    async def execute_round(
        self,
        agents: list[AgentInstance],
        shared_state: GroupState,
        round_number: int,
        *,
        call_agent: CallAgentFn,
        tracer: GroupTracer,
    ) -> GroupState:
        """Run one round. Must return the (possibly updated) state."""

    @staticmethod
    def _is_converged(state: GroupState, window: int = 2) -> bool:
        """Best-effort convergence check: all agents in the last window agreed."""
        recent = state.messages[-window * max(1, len(state.shared_context.get("roles", [])) or 1) :]
        if len(recent) < window:
            return False
        texts = {m.content.strip().lower() for m in recent[-window:]}
        return len(texts) == 1


class RoundRobinProtocol(CommunicationProtocol):
    """Every agent speaks in turn. Each agent sees all prior messages."""

    async def execute_round(
        self,
        agents: list[AgentInstance],
        shared_state: GroupState,
        round_number: int,
        *,
        call_agent: CallAgentFn,
        tracer: GroupTracer,
    ) -> GroupState:
        from otelmind.multiagent.group import GroupMessage

        for agent in agents:
            messages = _broadcast_history(shared_state)
            with tracer.trace_agent_call(
                agent_id=agent.agent_id,
                role=agent.role.name,
                round_number=round_number,
            ):
                text, usage = await call_agent(agent, messages, round_number)
            shared_state.append(
                GroupMessage(
                    sender_id=agent.agent_id,
                    sender_role=agent.role.name,
                    content=text,
                    round_number=round_number,
                    timestamp=datetime.now(UTC),
                    token_usage=usage,
                )
            )
        shared_state.final_output = (
            shared_state.messages[-1].content if shared_state.messages else None
        )
        return shared_state


class DebateProtocol(CommunicationProtocol):
    """Two agents argue opposing positions; a third (judge) decides.

    Requires exactly three roles. The first two alternate; the third speaks
    last in each round and may mark the debate converged by emitting text
    starting with `VERDICT:`.
    """

    async def execute_round(
        self,
        agents: list[AgentInstance],
        shared_state: GroupState,
        round_number: int,
        *,
        call_agent: CallAgentFn,
        tracer: GroupTracer,
    ) -> GroupState:
        from otelmind.multiagent.group import GroupMessage

        if len(agents) != 3:
            raise ValueError("DebateProtocol requires exactly three agents (two debaters + judge)")

        debater_a, debater_b, judge = agents
        order = [debater_a, debater_b, judge]
        for agent in order:
            messages = _broadcast_history(shared_state)
            with tracer.trace_agent_call(
                agent_id=agent.agent_id,
                role=agent.role.name,
                round_number=round_number,
                message_type="debate",
            ):
                text, usage = await call_agent(agent, messages, round_number)
            shared_state.append(
                GroupMessage(
                    sender_id=agent.agent_id,
                    sender_role=agent.role.name,
                    content=text,
                    round_number=round_number,
                    timestamp=datetime.now(UTC),
                    token_usage=usage,
                    message_type="debate",
                )
            )
            if agent is judge and text.strip().upper().startswith("VERDICT:"):
                shared_state.status = "converged"
                shared_state.final_output = text.split(":", 1)[-1].strip()
                break
        else:
            shared_state.final_output = shared_state.messages[-1].content
        return shared_state


class BlackboardProtocol(CommunicationProtocol):
    """Shared-state collaboration. Agents read/write a `blackboard` dict.

    Each agent is prompted to emit a JSON object of updates; non-JSON output
    is recorded as a plain contribution. Convergence is declared when every
    agent emits an empty update twice in a row.
    """

    async def execute_round(
        self,
        agents: list[AgentInstance],
        shared_state: GroupState,
        round_number: int,
        *,
        call_agent: CallAgentFn,
        tracer: GroupTracer,
    ) -> GroupState:
        from otelmind.multiagent.group import GroupMessage

        blackboard: dict[str, Any] = shared_state.shared_context.setdefault("blackboard", {})
        empty_streak: dict[str, int] = shared_state.shared_context.setdefault(
            "blackboard_empty_streak", {}
        )

        for agent in agents:
            board_preview = json.dumps(blackboard, default=str)[:2000]
            messages = [
                {
                    "role": "user",
                    "content": (
                        f"Problem: {shared_state.shared_context.get('problem', '')}\n"
                        f"Blackboard (current state): {board_preview}\n\n"
                        "Respond with a JSON object of updates to merge into the blackboard. "
                        "If you have nothing to add, respond with {}."
                    ),
                }
            ]
            with tracer.trace_agent_call(
                agent_id=agent.agent_id,
                role=agent.role.name,
                round_number=round_number,
                message_type="blackboard_write",
            ):
                text, usage = await call_agent(agent, messages, round_number)

            updates: dict[str, Any]
            parsed_ok = False
            try:
                start = text.find("{")
                end = text.rfind("}")
                if start >= 0 and end > start:
                    updates = json.loads(text[start : end + 1])
                    parsed_ok = isinstance(updates, dict)
            except json.JSONDecodeError:
                parsed_ok = False

            if parsed_ok and updates:
                blackboard.update(updates)
                empty_streak[agent.agent_id] = 0
            else:
                empty_streak[agent.agent_id] = empty_streak.get(agent.agent_id, 0) + 1

            shared_state.append(
                GroupMessage(
                    sender_id=agent.agent_id,
                    sender_role=agent.role.name,
                    content=text,
                    round_number=round_number,
                    timestamp=datetime.now(UTC),
                    token_usage=usage,
                    message_type="blackboard_write",
                )
            )

        if all(streak >= 2 for streak in empty_streak.values()) and empty_streak:
            shared_state.status = "converged"

        shared_state.final_output = json.dumps(blackboard, default=str)
        return shared_state


class ConsensusProtocol(CommunicationProtocol):
    """Agents propose independently, then vote. Majority wins; ties escalate.

    Each agent returns a proposal. If a majority matches (case-insensitive,
    normalized), that's the final output. Otherwise continue to the next
    round. After `max_rounds`, escalate to the first agent (tiebreaker).
    """

    async def execute_round(
        self,
        agents: list[AgentInstance],
        shared_state: GroupState,
        round_number: int,
        *,
        call_agent: CallAgentFn,
        tracer: GroupTracer,
    ) -> GroupState:
        from otelmind.multiagent.group import GroupMessage

        proposals: dict[str, str] = {}
        for agent in agents:
            messages = _broadcast_history(shared_state)
            with tracer.trace_agent_call(
                agent_id=agent.agent_id,
                role=agent.role.name,
                round_number=round_number,
                message_type="consensus_vote",
            ):
                text, usage = await call_agent(agent, messages, round_number)
            shared_state.append(
                GroupMessage(
                    sender_id=agent.agent_id,
                    sender_role=agent.role.name,
                    content=text,
                    round_number=round_number,
                    timestamp=datetime.now(UTC),
                    token_usage=usage,
                    message_type="consensus_vote",
                )
            )
            proposals[agent.agent_id] = text.strip()

        # Tally normalized proposals
        tally: dict[str, int] = {}
        for proposal in proposals.values():
            key = proposal.lower()[:200]
            tally[key] = tally.get(key, 0) + 1

        top_key, top_count = max(tally.items(), key=lambda kv: kv[1])
        majority = top_count > len(agents) // 2

        if majority:
            shared_state.status = "converged"
            shared_state.final_output = next(
                p for p in proposals.values() if p.lower()[:200] == top_key
            )
        elif round_number >= self.max_rounds:
            shared_state.status = "deadlocked"
            shared_state.final_output = proposals[agents[0].agent_id]
            logger.info(
                "ConsensusProtocol: deadlock after {} rounds, escalated to tiebreaker", round_number
            )
        return shared_state


class DelegationProtocol(CommunicationProtocol):
    """Lead agent (first) assigns subtasks; specialists report back.

    Round sequence:
      1. The lead emits a JSON list of {"agent": name, "task": description}.
      2. Each named specialist runs their task once.
      3. The lead emits a summary. If it starts with `DONE:`, we converge.
    """

    async def execute_round(
        self,
        agents: list[AgentInstance],
        shared_state: GroupState,
        round_number: int,
        *,
        call_agent: CallAgentFn,
        tracer: GroupTracer,
    ) -> GroupState:
        from otelmind.multiagent.group import GroupMessage

        if not agents:
            return shared_state

        lead, *specialists = agents
        specialists_by_role = {a.role.name: a for a in specialists}

        # Step 1: lead plans
        plan_prompt = [
            {
                "role": "user",
                "content": (
                    f"Problem: {shared_state.shared_context.get('problem', '')}\n"
                    f"Available specialists: {', '.join(specialists_by_role)}\n"
                    "Assign subtasks. Respond ONLY with JSON: "
                    '[{"agent": "role_name", "task": "what to do"}, ...]'
                ),
            }
        ]
        with tracer.trace_agent_call(
            agent_id=lead.agent_id,
            role=lead.role.name,
            round_number=round_number,
            message_type="delegation_plan",
        ):
            plan_text, plan_usage = await call_agent(lead, plan_prompt, round_number)
        shared_state.append(
            GroupMessage(
                sender_id=lead.agent_id,
                sender_role=lead.role.name,
                content=plan_text,
                round_number=round_number,
                timestamp=datetime.now(UTC),
                token_usage=plan_usage,
                message_type="delegation_plan",
            )
        )

        tasks: list[dict[str, str]]
        start = plan_text.find("[")
        end = plan_text.rfind("]")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(plan_text[start : end + 1])
                tasks = [t for t in parsed if isinstance(t, dict) and "agent" in t and "task" in t]
            except json.JSONDecodeError:
                tasks = []
        else:
            tasks = []

        # Step 2: specialists execute
        for task in tasks:
            specialist = specialists_by_role.get(str(task["agent"]))
            if specialist is None:
                continue
            sub_messages = [
                {
                    "role": "user",
                    "content": (
                        f"Problem: {shared_state.shared_context.get('problem', '')}\n"
                        f"Your assigned task from the lead: {task['task']}"
                    ),
                }
            ]
            with tracer.trace_agent_call(
                agent_id=specialist.agent_id,
                role=specialist.role.name,
                round_number=round_number,
                message_type="delegation_report",
            ):
                text, usage = await call_agent(specialist, sub_messages, round_number)
            shared_state.append(
                GroupMessage(
                    sender_id=specialist.agent_id,
                    sender_role=specialist.role.name,
                    content=text,
                    round_number=round_number,
                    timestamp=datetime.now(UTC),
                    token_usage=usage,
                    recipient_id=lead.agent_id,
                    message_type="delegation_report",
                )
            )

        # Step 3: lead summarizes
        summary_prompt = _broadcast_history(shared_state)
        summary_prompt[0]["content"] += (
            "\n\nAs lead, either produce the final consolidated answer "
            "(prefixed with 'DONE:') or dispatch another round of tasks."
        )
        with tracer.trace_agent_call(
            agent_id=lead.agent_id,
            role=lead.role.name,
            round_number=round_number,
            message_type="delegation_summary",
        ):
            summary_text, summary_usage = await call_agent(lead, summary_prompt, round_number)
        shared_state.append(
            GroupMessage(
                sender_id=lead.agent_id,
                sender_role=lead.role.name,
                content=summary_text,
                round_number=round_number,
                timestamp=datetime.now(UTC),
                token_usage=summary_usage,
                message_type="delegation_summary",
            )
        )

        if summary_text.strip().upper().startswith("DONE:"):
            shared_state.status = "converged"
            shared_state.final_output = summary_text.split(":", 1)[-1].strip()
        else:
            shared_state.final_output = summary_text
        return shared_state
