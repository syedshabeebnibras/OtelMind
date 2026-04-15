"""Recommend a multi-agent protocol for a new problem.

Reuses the TF-IDF cosine similarity already in
`otelmind.watchdog.detectors.semantic_drift`, plus the historical
`group_runs` table (populated by real benchmarks + real POSTs) as
training data.

Pipeline:
  1. Find the K most similar historical problems by TF-IDF cosine.
  2. For each candidate protocol, aggregate those neighbours'
     task_completion_score (faithfulness against expected_output
     when available, heuristic 0.5 otherwise), cost, and success rate
     into a score. Scores combine: mean task quality, cost efficiency
     (lower cost is better), and status-success rate.
  3. Return the protocol whose aggregate score wins, plus the
     per-protocol breakdown so callers can surface "why".

Deliberately simple: no embeddings, no learned weights, no ML
dependencies — just the pieces already in the repo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from loguru import logger
from sqlalchemy import select

from otelmind.db import get_session
from otelmind.storage.models import GroupRun
from otelmind.watchdog.detectors.semantic_drift import (
    _cosine_similarity,
    _tfidf_vector,
    _tokenize,
)

# Protocol options we'll consider. Matches the keys in
# otelmind.multiagent.protocols._PROTOCOL_MAP.
_PROTOCOLS = ("round_robin", "debate", "consensus", "blackboard", "delegation")

# Statuses we count as "success" when computing the success-rate component.
# Everything else (failed / deadlocked / budget_exceeded) counts against
# the protocol in the final score.
_SUCCESS_STATUSES = frozenset({"completed", "converged"})


@dataclass
class ProtocolScore:
    protocol: str
    neighbour_count: int
    success_rate: float  # 0..1
    avg_task_score: float  # 0..1
    avg_cost_usd: float
    combined: float  # the number we sort by

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol": self.protocol,
            "neighbour_count": self.neighbour_count,
            "success_rate": round(self.success_rate, 4),
            "avg_task_score": round(self.avg_task_score, 4),
            "avg_cost_usd": round(self.avg_cost_usd, 6),
            "combined": round(self.combined, 4),
        }


@dataclass
class ProtocolRecommendation:
    recommended: str
    reason: str
    per_protocol: list[ProtocolScore] = field(default_factory=list)
    neighbours: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "recommended": self.recommended,
            "reason": self.reason,
            "per_protocol": [s.to_dict() for s in self.per_protocol],
            "neighbours": self.neighbours,
        }


def _fetch_neighbours(
    problem: str, candidates: list[GroupRun], top_k: int, min_similarity: float
) -> list[tuple[GroupRun, float]]:
    """Rank candidate rows by TF-IDF cosine similarity to `problem`, cut to top_k."""
    if not candidates:
        return []
    target_vec = _tfidf_vector(_tokenize(problem))
    scored: list[tuple[GroupRun, float]] = []
    for row in candidates:
        vec = _tfidf_vector(_tokenize(row.problem or ""))
        sim = _cosine_similarity(target_vec, vec)
        if sim >= min_similarity:
            scored.append((row, sim))
    scored.sort(key=lambda kv: kv[1], reverse=True)
    return scored[:top_k]


def _aggregate_per_protocol(
    neighbours: list[tuple[GroupRun, float]],
    *,
    cost_weight: float,
    task_weight: float,
    success_weight: float,
) -> list[ProtocolScore]:
    """Group neighbours by protocol and compute the combined score for each."""
    buckets: dict[str, list[tuple[GroupRun, float]]] = {p: [] for p in _PROTOCOLS}
    for row, sim in neighbours:
        if row.protocol in buckets:
            buckets[row.protocol].append((row, sim))

    scored: list[ProtocolScore] = []
    costs_seen: list[float] = []
    for rows in buckets.values():
        costs_seen.extend(float(r.total_cost_usd or 0.0) for r, _ in rows)
    max_cost = max(costs_seen) if costs_seen else 1.0

    for protocol, rows in buckets.items():
        if not rows:
            continue
        weights = [sim for _, sim in rows]
        total_w = sum(weights) or 1.0

        success_rate = (
            sum(
                (1.0 if r.status in _SUCCESS_STATUSES else 0.0) * w
                for (r, _), w in zip(rows, weights, strict=True)
            )
            / total_w
        )

        task_scores: list[tuple[float, float]] = []
        for row, sim in rows:
            score = ((row.metrics or {}).get("task_completion_score")) if row.metrics else None
            if isinstance(score, (int, float)):
                task_scores.append((float(score), sim))
        if task_scores:
            avg_task = sum(s * w for s, w in task_scores) / sum(w for _, w in task_scores)
        else:
            avg_task = 0.5  # conservative prior when no real judge scores exist

        avg_cost = (
            sum(
                (float(r.total_cost_usd or 0.0)) * w
                for (r, _), w in zip(rows, weights, strict=True)
            )
            / total_w
        )
        # Normalize cost into [0, 1] where 1 is cheapest
        cost_fit = 1.0 - (avg_cost / max_cost) if max_cost > 0 else 0.5

        combined = task_weight * avg_task + success_weight * success_rate + cost_weight * cost_fit

        scored.append(
            ProtocolScore(
                protocol=protocol,
                neighbour_count=len(rows),
                success_rate=success_rate,
                avg_task_score=avg_task,
                avg_cost_usd=avg_cost,
                combined=combined,
            )
        )

    scored.sort(key=lambda s: s.combined, reverse=True)
    return scored


async def recommend_protocol(
    problem: str,
    *,
    tenant_id: Any | None = None,
    top_k: int = 5,
    min_similarity: float = 0.1,
    cost_weight: float = 0.2,
    task_weight: float = 0.5,
    success_weight: float = 0.3,
) -> ProtocolRecommendation:
    """Recommend a protocol for `problem` based on historical group_runs.

    When no similar neighbours are found, falls back to the repo-wide
    default of `round_robin` with an honest reason string.

    The three weights must be set by the caller; they don't need to sum
    to 1.0 — the combined score is just a weighted sum.
    """
    if not problem.strip():
        return ProtocolRecommendation(
            recommended="round_robin",
            reason="empty problem string — defaulting to round_robin",
        )

    async with get_session() as session:
        stmt = select(GroupRun)
        if tenant_id is not None:
            stmt = stmt.where(GroupRun.tenant_id == tenant_id)
        candidates = list((await session.execute(stmt)).scalars().all())

    if not candidates:
        return ProtocolRecommendation(
            recommended="round_robin",
            reason="no historical group_runs — defaulting to round_robin",
        )

    neighbours = _fetch_neighbours(problem, candidates, top_k=top_k, min_similarity=min_similarity)
    if not neighbours:
        return ProtocolRecommendation(
            recommended="round_robin",
            reason=(
                f"no historical problems above cosine {min_similarity} — "
                "falling back to round_robin"
            ),
        )

    per_protocol = _aggregate_per_protocol(
        neighbours,
        cost_weight=cost_weight,
        task_weight=task_weight,
        success_weight=success_weight,
    )
    if not per_protocol:
        return ProtocolRecommendation(
            recommended="round_robin",
            reason="no neighbours matched any protocol bucket — defaulting",
        )

    winner = per_protocol[0]
    neighbour_payload = [
        {
            "group_run_id": str(row.id),
            "problem": (row.problem or "")[:240],
            "protocol": row.protocol,
            "status": row.status,
            "similarity": round(sim, 4),
            "task_completion_score": ((row.metrics or {}) or {}).get("task_completion_score"),
            "cost_usd": float(row.total_cost_usd or 0.0),
        }
        for row, sim in neighbours
    ]
    reason = (
        f"picked '{winner.protocol}' after reviewing {len(neighbours)} similar "
        f"historical problems; combined score {winner.combined:.3f} "
        f"(task={winner.avg_task_score:.2f}, success={winner.success_rate:.2f}, "
        f"avg_cost=${winner.avg_cost_usd:.4f})"
    )
    logger.info("protocol_selector: {}", reason)
    return ProtocolRecommendation(
        recommended=winner.protocol,
        reason=reason,
        per_protocol=per_protocol,
        neighbours=neighbour_payload,
    )
