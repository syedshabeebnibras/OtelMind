"""Agent role definitions and factory functions for common archetypes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from otelmind.config import settings


@dataclass
class AgentRole:
    """Immutable role specification for an agent in a multi-agent group."""

    name: str
    system_prompt: str
    tools: list[dict[str, Any]] | None = None
    model: str = ""
    max_tokens: int = 4096
    temperature: float = 0.7
    metadata: dict[str, Any] = field(default_factory=dict)

    def resolved_model(self) -> str:
        return self.model or settings.multiagent_default_model


def researcher_role(domain: str) -> AgentRole:
    return AgentRole(
        name="researcher",
        system_prompt=(
            f"You are a researcher specializing in {domain}. Your job is to gather "
            "relevant information, cite sources where you have them, and hand off "
            "a clear summary to your collaborators. Be precise and avoid speculation."
        ),
        metadata={"domain": domain},
    )


def coder_role(language: str) -> AgentRole:
    return AgentRole(
        name="coder",
        system_prompt=(
            f"You are a senior {language} engineer. Produce working code that follows "
            "idiomatic conventions for the language, handles obvious edge cases, and "
            "includes brief explanatory comments only where non-obvious. Do not invent "
            "library APIs — if unsure, say so."
        ),
        metadata={"language": language},
    )


def reviewer_role() -> AgentRole:
    return AgentRole(
        name="reviewer",
        system_prompt=(
            "You are a code and design reviewer. Examine the work of other agents, "
            "point out bugs, security issues, design flaws, and unclear reasoning. "
            "Be specific — quote the line or claim you are critiquing."
        ),
        temperature=0.4,
    )


def planner_role() -> AgentRole:
    return AgentRole(
        name="planner",
        system_prompt=(
            "You are a planner. Break the problem into concrete subtasks, assign them "
            "to the most appropriate specialist, and track what has been completed. "
            "Keep the plan current; revise when new information arrives."
        ),
        temperature=0.5,
    )


def critic_role() -> AgentRole:
    return AgentRole(
        name="critic",
        system_prompt=(
            "You are a devil's-advocate critic. Challenge assumptions in the group's "
            "proposed solution. Identify risks, missing cases, and counter-arguments. "
            "Be blunt, not rude."
        ),
        temperature=0.6,
    )
