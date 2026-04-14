"""AgentGroup — orchestrates N role-specialized agents through a protocol."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from loguru import logger

from otelmind.config import settings
from otelmind.multiagent.roles import AgentRole
from otelmind.multiagent.tracer import GroupTracer

if TYPE_CHECKING:
    from otelmind.multiagent.protocols import CommunicationProtocol


@dataclass
class GroupMessage:
    sender_id: str
    sender_role: str
    content: str
    round_number: int
    timestamp: datetime
    recipient_id: str | None = None
    token_usage: dict[str, int] | None = None
    message_type: str = "broadcast"

    def to_dict(self) -> dict[str, Any]:
        return {
            "sender_id": self.sender_id,
            "sender_role": self.sender_role,
            "recipient_id": self.recipient_id,
            "content": self.content,
            "round_number": self.round_number,
            "timestamp": self.timestamp.isoformat(),
            "token_usage": self.token_usage,
            "message_type": self.message_type,
        }


@dataclass
class AgentInstance:
    role: AgentRole
    agent_id: str
    message_history: list[dict[str, Any]] = field(default_factory=list)
    tokens_used: int = 0


@dataclass
class GroupState:
    messages: list[GroupMessage] = field(default_factory=list)
    shared_context: dict[str, Any] = field(default_factory=dict)
    round_number: int = 0
    status: str = "in_progress"
    final_output: str | None = None

    def append(self, message: GroupMessage) -> None:
        self.messages.append(message)


@dataclass
class GroupResult:
    problem: str
    protocol: str
    final_output: str | None
    status: str
    rounds_completed: int
    total_tokens: int
    messages: list[GroupMessage]
    roles: list[AgentRole]
    shared_context: dict[str, Any]
    started_at: datetime
    completed_at: datetime
    budget_usd: float | None = None
    budget_remaining_usd: float | None = None
    cost_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "problem": self.problem,
            "protocol": self.protocol,
            "status": self.status,
            "rounds_completed": self.rounds_completed,
            "total_tokens": self.total_tokens,
            "final_output": self.final_output,
            "messages": [m.to_dict() for m in self.messages],
            "roles": [
                {
                    "name": r.name,
                    "model": r.resolved_model(),
                    "metadata": r.metadata,
                }
                for r in self.roles
            ],
            "shared_context": self.shared_context,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "budget_usd": self.budget_usd,
            "budget_remaining_usd": self.budget_remaining_usd,
            "cost_usd": round(self.cost_usd, 6),
        }


class AgentGroup:
    """A team of agents cooperating via a `CommunicationProtocol`."""

    def __init__(
        self,
        roles: list[AgentRole],
        protocol: CommunicationProtocol,
        api_key: str | None = None,
        max_rounds: int | None = None,
        budget_usd: float | None = None,
    ) -> None:
        if not roles:
            raise ValueError("AgentGroup requires at least one role")
        self._roles = roles
        self._protocol = protocol
        self._api_key = api_key or settings.anthropic_api_key
        self._max_rounds = max_rounds or settings.multiagent_max_rounds
        self._budget_usd = budget_usd
        self._tracer = GroupTracer()
        self._client: Any | None = None

    @property
    def roles(self) -> list[AgentRole]:
        return list(self._roles)

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import anthropic
        except ImportError as exc:
            raise RuntimeError(
                "anthropic package is not installed — install with `pip install anthropic>=0.39.0`"
            ) from exc
        if not self._api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set — multi-agent calls require a Claude API key"
            )
        self._client = anthropic.AsyncAnthropic(api_key=self._api_key, timeout=60.0)
        return self._client

    def _instantiate(self) -> list[AgentInstance]:
        return [
            AgentInstance(role=role, agent_id=f"{role.name}-{i}")
            for i, role in enumerate(self._roles)
        ]

    async def solve(self, problem: str, context: str = "") -> GroupResult:
        """Run the protocol to completion and return the full trace."""
        # Local import to avoid a circular dependency on group_metrics → group
        from otelmind.eval.group_metrics import _estimate_cost

        started = datetime.now(UTC)
        agents = self._instantiate()
        state = GroupState(shared_context={"problem": problem, "context": context})

        with self._tracer.trace_group(
            problem=problem, protocol=type(self._protocol).__name__
        ) as root_span:
            while state.status == "in_progress" and state.round_number < self._max_rounds:
                state.round_number += 1
                with self._tracer.trace_round(state.round_number, root_span=root_span):
                    try:
                        state = await self._protocol.execute_round(
                            agents=agents,
                            shared_state=state,
                            round_number=state.round_number,
                            call_agent=self._call_agent,
                            tracer=self._tracer,
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        logger.exception("multiagent: protocol round failed: {}", exc)
                        state.status = "failed"
                        state.shared_context["error"] = str(exc)
                        break

                # Budget check after each round — break out before the next one.
                if self._budget_usd is not None and state.status == "in_progress":
                    cost_so_far = _estimate_cost(state.messages)
                    if cost_so_far >= self._budget_usd:
                        state.status = "budget_exceeded"
                        logger.info(
                            "multiagent: budget ${:.4f} exceeded after round {} "
                            "(spent ${:.4f}) — stopping",
                            self._budget_usd,
                            state.round_number,
                            cost_so_far,
                        )
                        break

        completed = datetime.now(UTC)
        total_tokens = sum(a.tokens_used for a in agents)
        cost_usd = _estimate_cost(state.messages)
        budget_remaining = (
            max(0.0, self._budget_usd - cost_usd) if self._budget_usd is not None else None
        )

        if state.status == "in_progress":
            # Protocols that detect deadlock/convergence mark the status themselves
            # (ConsensusProtocol on a no-majority round, DebateProtocol on a VERDICT,
            # BlackboardProtocol on repeated empty streaks). Reaching this branch
            # means the protocol ran every round without declaring a terminal state,
            # which is the expected finish for round-robin/delegation loops.
            state.status = "completed"

        return GroupResult(
            problem=problem,
            protocol=type(self._protocol).__name__,
            final_output=state.final_output,
            status=state.status,
            rounds_completed=state.round_number,
            total_tokens=total_tokens,
            messages=list(state.messages),
            roles=list(self._roles),
            shared_context=state.shared_context,
            started_at=started,
            completed_at=completed,
            budget_usd=self._budget_usd,
            budget_remaining_usd=budget_remaining,
            cost_usd=cost_usd,
        )

    async def _call_agent(
        self,
        agent: AgentInstance,
        messages: list[dict[str, Any]],
        round_number: int,
    ) -> tuple[str, dict[str, int]]:
        """Send `messages` to the underlying Claude API. Returns (text, usage)."""
        client = self._ensure_client()

        try:
            import anthropic
            from tenacity import (
                AsyncRetrying,
                retry_if_exception_type,
                stop_after_attempt,
                wait_exponential,
            )

            retryable: tuple[type[BaseException], ...] = (
                anthropic.APIConnectionError,
                anthropic.APITimeoutError,
                anthropic.RateLimitError,
            )
        except Exception:  # pragma: no cover — anthropic ImportError handled in _ensure_client
            retryable = (Exception,)
            AsyncRetrying = None  # type: ignore[assignment,misc]  # noqa: N806

        async def _once() -> Any:
            return await client.messages.create(
                model=agent.role.resolved_model(),
                max_tokens=agent.role.max_tokens,
                temperature=agent.role.temperature,
                system=agent.role.system_prompt,
                messages=messages,
            )

        response: Any
        if AsyncRetrying is None:
            response = await _once()
        else:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(3),
                wait=wait_exponential(multiplier=1, min=1, max=10),
                retry=retry_if_exception_type(retryable),
                reraise=True,
            ):
                with attempt:
                    response = await _once()

        text_parts: list[str] = []
        for block in getattr(response, "content", []) or []:
            t = getattr(block, "text", None)
            if t:
                text_parts.append(t)
        text = "".join(text_parts).strip()

        usage_raw = getattr(response, "usage", None)
        usage = {
            "prompt_tokens": getattr(usage_raw, "input_tokens", 0) if usage_raw else 0,
            "completion_tokens": getattr(usage_raw, "output_tokens", 0) if usage_raw else 0,
        }
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        agent.tokens_used += usage["total_tokens"]

        self._tracer.record_agent_call(
            agent_id=agent.agent_id,
            role=agent.role.name,
            round_number=round_number,
            tokens=usage["total_tokens"],
        )
        return text, usage
