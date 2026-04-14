"""Parallel scoring of many eval cases with a semaphore-limited worker pool.

Uses asyncio.Semaphore to cap concurrent LLM calls. Reports progress via an
optional callback and returns per-case scores plus per-dimension aggregates.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from otelmind.eval.judge import LLMJudge
from otelmind.eval.regression import EvalCase
from otelmind.eval.statistics import percentile


@dataclass
class DimensionAggregate:
    mean: float
    std: float
    min: float
    max: float
    p50: float
    p95: float
    n: int


@dataclass
class BatchScoringResult:
    total: int
    scored: int
    failed: int
    duration_seconds: float
    per_case: list[dict[str, Any]] = field(default_factory=list)
    aggregate: dict[str, DimensionAggregate] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "scored": self.scored,
            "failed": self.failed,
            "duration_seconds": round(self.duration_seconds, 4),
            "per_case": self.per_case,
            "aggregate": {
                dim: {
                    "mean": round(agg.mean, 4),
                    "std": round(agg.std, 4),
                    "min": round(agg.min, 4),
                    "max": round(agg.max, 4),
                    "p50": round(agg.p50, 4),
                    "p95": round(agg.p95, 4),
                    "n": agg.n,
                }
                for dim, agg in self.aggregate.items()
            },
        }


def _stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return (sum((v - mean) ** 2 for v in values) / (len(values) - 1)) ** 0.5


def _aggregate(scores: list[float]) -> DimensionAggregate:
    if not scores:
        return DimensionAggregate(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0)
    mean = sum(scores) / len(scores)
    return DimensionAggregate(
        mean=mean,
        std=_stdev(scores),
        min=min(scores),
        max=max(scores),
        p50=percentile(scores, 0.50),
        p95=percentile(scores, 0.95),
        n=len(scores),
    )


class BatchScorer:
    """Score many eval cases in parallel with a concurrency cap."""

    def __init__(
        self,
        judge: LLMJudge,
        concurrency: int = 10,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> None:
        self._judge = judge
        self._concurrency = max(1, concurrency)
        self._progress_callback = progress_callback

    async def score_batch(
        self,
        cases: list[EvalCase],
        dimensions: list[str] | None = None,
    ) -> BatchScoringResult:
        total = len(cases)
        if total == 0:
            return BatchScoringResult(total=0, scored=0, failed=0, duration_seconds=0.0)

        semaphore = asyncio.Semaphore(self._concurrency)
        completed = 0
        start = time.monotonic()

        async def _score_one(case: EvalCase) -> dict[str, Any]:
            nonlocal completed
            async with semaphore:
                try:
                    result = await self._judge.score(
                        case.question, case.actual, case.context, dimensions
                    )
                    payload: dict[str, Any] = {
                        "id": case.id,
                        "overall": result.overall,
                        "dimensions": {
                            dim: {
                                "score": s.score,
                                "raw_score": s.raw_score,
                                "method": s.method,
                                "reason": s.reason,
                            }
                            for dim, s in result.scores.items()
                        },
                        "error": None,
                    }
                except Exception as exc:
                    logger.warning("BatchScorer: case {} failed: {}", case.id, exc)
                    payload = {
                        "id": case.id,
                        "overall": None,
                        "dimensions": {},
                        "error": str(exc),
                    }
                finally:
                    completed += 1
                    if self._progress_callback is not None:
                        try:
                            self._progress_callback(completed, total)
                        except Exception:
                            logger.exception("BatchScorer: progress callback raised")
                return payload

        results = await asyncio.gather(*[_score_one(c) for c in cases])
        duration = time.monotonic() - start

        scored = sum(1 for r in results if r["error"] is None)
        failed = total - scored

        dim_scores: dict[str, list[float]] = {}
        for r in results:
            if r["error"] is not None:
                continue
            for dim, payload in r["dimensions"].items():
                dim_scores.setdefault(dim, []).append(payload["score"])

        aggregate = {dim: _aggregate(scores) for dim, scores in dim_scores.items()}

        return BatchScoringResult(
            total=total,
            scored=scored,
            failed=failed,
            duration_seconds=duration,
            per_case=results,
            aggregate=aggregate,
        )
