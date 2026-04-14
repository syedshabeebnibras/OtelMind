"""Scoring metrics for multi-agent group collaboration."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from otelmind.eval.judge import LLMJudge
from otelmind.multiagent.group import GroupResult

# Claude 3.5/4 Sonnet: $3/$15 per 1M tokens (approx).
_DEFAULT_PROMPT_COST_PER_MTOK = 3.0
_DEFAULT_COMPLETION_COST_PER_MTOK = 15.0


@dataclass
class AgentStats:
    messages_sent: int
    tokens_used: int
    corrections_made: int
    corrections_received: int
    contribution_ratio: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "messages_sent": self.messages_sent,
            "tokens_used": self.tokens_used,
            "corrections_made": self.corrections_made,
            "corrections_received": self.corrections_received,
            "contribution_ratio": round(self.contribution_ratio, 4),
        }


@dataclass
class GroupEvalResult:
    task_completion_score: float
    convergence_rate: float
    communication_efficiency: float
    error_correction_count: int
    dominance_score: float
    deadlock_occurred: bool
    rounds_to_completion: int
    total_tokens: int
    total_cost_usd: float
    per_agent_stats: dict[str, AgentStats] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_completion_score": round(self.task_completion_score, 4),
            "convergence_rate": round(self.convergence_rate, 4),
            "communication_efficiency": round(self.communication_efficiency, 4),
            "error_correction_count": self.error_correction_count,
            "dominance_score": round(self.dominance_score, 4),
            "deadlock_occurred": self.deadlock_occurred,
            "rounds_to_completion": self.rounds_to_completion,
            "total_tokens": self.total_tokens,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "per_agent_stats": {k: v.to_dict() for k, v in self.per_agent_stats.items()},
        }


_CORRECTION_PATTERNS = [
    re.compile(r"\bactually\b", re.I),
    re.compile(r"\bcorrection[:\s]", re.I),
    re.compile(r"\bthat'?s (?:not|wrong|incorrect)", re.I),
    re.compile(r"\byou (?:got|have|are) (?:that )?wrong", re.I),
    re.compile(r"\bthis is (?:wrong|incorrect)", re.I),
    re.compile(r"\bi disagree\b", re.I),
    re.compile(r"\bthe (?:correct|right) (?:answer|version|approach)\b", re.I),
]


def _count_corrections(text: str) -> int:
    return sum(1 for p in _CORRECTION_PATTERNS if p.search(text))


def _dominance_score(per_agent_tokens: dict[str, int]) -> float:
    """1 - CV(tokens). 1.0 = perfectly balanced, 0.0 = one agent dominates.

    Clamped to [0, 1]. Returns 1.0 for a single-agent group (trivially balanced).
    """
    values = list(per_agent_tokens.values())
    if not values:
        return 0.0
    if len(values) == 1:
        return 1.0
    mean = sum(values) / len(values)
    if mean == 0:
        return 1.0
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    std = math.sqrt(variance)
    cv = std / mean
    return max(0.0, min(1.0, 1.0 - cv))


def _estimate_cost(messages: list[Any]) -> float:
    total = 0.0
    for m in messages:
        usage = m.token_usage or {}
        p = usage.get("prompt_tokens", 0) or 0
        c = usage.get("completion_tokens", 0) or 0
        total += (p * _DEFAULT_PROMPT_COST_PER_MTOK + c * _DEFAULT_COMPLETION_COST_PER_MTOK) / 1e6
    return total


_CORRECTION_DETECT_PROMPT = """You are auditing inter-agent collaboration messages.

Earlier message from {sender_a} ({role_a}):
"{content_a}"

Later message from {sender_b} ({role_b}):
"{content_b}"

Did the later message correct an error, factual mistake, or misconception in
the earlier message? Reply with ONLY a JSON object:
{{"correction": true|false, "reason": "one short sentence"}}

Be conservative — minor rephrasing or polite addition is NOT a correction.
"""


async def _detect_corrections_with_llm(
    messages: list[Any],
    judge: LLMJudge,
    max_concurrent: int = 5,
) -> dict[str, dict[str, int]]:
    """Use an LLM judge to count corrections between consecutive cross-agent messages.

    Cross-agent message pairs are scored in parallel, capped at `max_concurrent`
    in-flight OpenAI calls. For a 50-message group this turns a ~minute-long
    sequential walk into a few seconds. Returns
    {agent_id: {"corrections_made": int, "corrections_received": int}}.
    """
    import asyncio
    import json

    if len(messages) < 2:
        return {}

    pairs: list[tuple[Any, Any]] = []
    for i in range(1, len(messages)):
        if messages[i - 1].sender_id != messages[i].sender_id:
            pairs.append((messages[i - 1], messages[i]))
    if not pairs:
        return {}

    sem = asyncio.Semaphore(max(1, max_concurrent))
    counts: dict[str, dict[str, int]] = {}
    counts_lock = asyncio.Lock()

    try:
        import openai
    except ImportError:
        logger.warning("evaluate_group: openai not installed, skipping LLM corrections")
        return {}

    client = openai.AsyncOpenAI(api_key=judge._api_key, timeout=30.0)

    async def _check_pair(prev: Any, curr: Any) -> None:
        prompt = _CORRECTION_DETECT_PROMPT.format(
            sender_a=prev.sender_id,
            role_a=prev.sender_role,
            content_a=(prev.content or "")[:1500],
            sender_b=curr.sender_id,
            role_b=curr.sender_role,
            content_b=(curr.content or "")[:1500],
        )
        async with sem:
            try:
                resp = await client.chat.completions.create(
                    model=judge._model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,
                    max_tokens=80,
                    response_format={"type": "json_object"},
                )
                data = json.loads(resp.choices[0].message.content or "{}")
            except Exception as exc:
                logger.warning("evaluate_group: LLM correction check failed: {}", exc)
                return
        if not data.get("correction"):
            return
        async with counts_lock:
            counts.setdefault(curr.sender_id, {"corrections_made": 0, "corrections_received": 0})
            counts.setdefault(prev.sender_id, {"corrections_made": 0, "corrections_received": 0})
            counts[curr.sender_id]["corrections_made"] += 1
            counts[prev.sender_id]["corrections_received"] += 1

    await asyncio.gather(*[_check_pair(p, c) for p, c in pairs])
    return counts


async def evaluate_group(
    group_result: GroupResult,
    expected_output: str | None = None,
    judge: LLMJudge | None = None,
    max_rounds: int | None = None,
) -> GroupEvalResult:
    """Score a completed multi-agent group run."""
    messages = group_result.messages
    total_rounds = group_result.rounds_completed
    max_rounds_val = max_rounds if max_rounds is not None else max(total_rounds, 1)

    deadlock = group_result.status in {"deadlocked", "failed"}

    if max_rounds_val <= 0:
        convergence_rate = 0.0
    elif group_result.status == "converged":
        convergence_rate = max(0.0, 1.0 - (total_rounds / max_rounds_val))
    elif group_result.status == "completed":
        convergence_rate = max(0.0, 1.0 - (total_rounds / max_rounds_val)) * 0.8
    else:
        convergence_rate = 0.0

    # Per-agent stats — token + message accounting (cheap)
    per_agent_messages: dict[str, int] = {}
    per_agent_tokens: dict[str, int] = {}
    for m in messages:
        per_agent_messages[m.sender_id] = per_agent_messages.get(m.sender_id, 0) + 1
        usage = m.token_usage or {}
        per_agent_tokens[m.sender_id] = per_agent_tokens.get(m.sender_id, 0) + int(
            usage.get("total_tokens", 0)
        )

    # Correction detection — prefer the LLM auditor when a real judge is available
    # (model-grounded, fewer false positives on rhetorical phrases). Fall back to
    # the regex pattern matcher otherwise.
    per_agent_corrections_made: dict[str, int] = {}
    per_agent_corrections_received: dict[str, int] = {}
    error_corrections = 0
    correction_method = "regex"

    judge_for_corrections = judge
    if judge_for_corrections is not None and bool(getattr(judge_for_corrections, "_api_key", None)):
        try:
            llm_counts = await _detect_corrections_with_llm(messages, judge_for_corrections)
            if llm_counts:
                correction_method = "llm"
                for agent_id, payload in llm_counts.items():
                    per_agent_corrections_made[agent_id] = payload.get("corrections_made", 0)
                    per_agent_corrections_received[agent_id] = payload.get(
                        "corrections_received", 0
                    )
                error_corrections = sum(p.get("corrections_made", 0) for p in llm_counts.values())
        except Exception as exc:
            logger.warning("evaluate_group: LLM correction detector failed, falling back: {}", exc)

    if correction_method == "regex":
        previous_by_agent: dict[str, str] = {}
        for m in messages:
            hits = _count_corrections(m.content)
            if hits > 0:
                per_agent_corrections_made[m.sender_id] = (
                    per_agent_corrections_made.get(m.sender_id, 0) + hits
                )
                for prior_agent in previous_by_agent:
                    if prior_agent == m.sender_id:
                        continue
                    per_agent_corrections_received[prior_agent] = (
                        per_agent_corrections_received.get(prior_agent, 0) + hits
                    )
                error_corrections += hits
            previous_by_agent[m.sender_id] = m.content
    logger.debug("evaluate_group: correction detection method = {}", correction_method)

    total_tokens_reported = sum(per_agent_tokens.values()) or group_result.total_tokens
    per_agent_stats: dict[str, AgentStats] = {}
    for agent_id in per_agent_messages:
        tokens = per_agent_tokens.get(agent_id, 0)
        per_agent_stats[agent_id] = AgentStats(
            messages_sent=per_agent_messages[agent_id],
            tokens_used=tokens,
            corrections_made=per_agent_corrections_made.get(agent_id, 0),
            corrections_received=per_agent_corrections_received.get(agent_id, 0),
            contribution_ratio=(tokens / total_tokens_reported) if total_tokens_reported else 0.0,
        )

    dominance = _dominance_score(per_agent_tokens)

    # Communication efficiency: fraction of messages containing either a
    # correction (disagreement/refinement) OR a blackboard update OR a
    # non-trivial contribution (> 50 chars). Pure "ok"/"agreed" messages
    # are considered redundant.
    if not messages:
        comm_efficiency = 0.0
    else:
        productive = 0
        for m in messages:
            content = m.content.strip()
            if (
                len(content) >= 50
                or _count_corrections(content) > 0
                or m.message_type in {"blackboard_write", "delegation_report"}
            ):
                productive += 1
        comm_efficiency = productive / len(messages)

    # Task completion
    task_score = 0.5
    if expected_output is not None and group_result.final_output:
        judge_obj = judge or LLMJudge()
        try:
            j_result = await judge_obj.score(
                question=group_result.problem,
                answer=group_result.final_output,
                context=expected_output,
                dimensions=["faithfulness"],
            )
            faith = j_result.scores.get("faithfulness")
            if faith is not None:
                task_score = faith.score
        except Exception as exc:
            logger.warning("evaluate_group: task completion scoring failed: {}", exc)
    elif group_result.final_output:
        task_score = 0.7 if group_result.status == "converged" else 0.5

    cost = _estimate_cost(messages)

    return GroupEvalResult(
        task_completion_score=task_score,
        convergence_rate=convergence_rate,
        communication_efficiency=comm_efficiency,
        error_correction_count=error_corrections,
        dominance_score=dominance,
        deadlock_occurred=deadlock,
        rounds_to_completion=total_rounds,
        total_tokens=total_tokens_reported,
        total_cost_usd=cost,
        per_agent_stats=per_agent_stats,
    )
