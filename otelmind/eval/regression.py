"""Regression pipeline — compare two eval runs and flag degradations.

Use case: before deploying a new prompt or model, run against a baseline
eval dataset. Block deployment if any metric drops > threshold.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from otelmind.eval.judge import LLMJudge


@dataclass
class EvalCase:
    id: str
    question: str
    expected: str
    actual: str
    context: str = ""
    tags: list[str] = field(default_factory=list)


@dataclass
class RegressionReport:
    baseline_name: str
    candidate_name: str
    passed: bool
    summary: dict[str, Any]
    regressions: list[dict[str, Any]]
    improvements: list[dict[str, Any]]
    per_case: list[dict[str, Any]]


async def run_regression(
    baseline_cases: list[EvalCase],
    candidate_cases: list[EvalCase],
    *,
    baseline_name: str = "baseline",
    candidate_name: str = "candidate",
    dimensions: list[str] | None = None,
    regression_threshold: float = 0.05,  # 5% drop = fail
    api_key: str | None = None,
) -> RegressionReport:
    """Compare two sets of eval cases and report regressions.

    Both lists must have matching IDs. baseline_cases represent the known-good
    run; candidate_cases represent the new version under test.
    """
    judge = LLMJudge(api_key=api_key)
    dims = dimensions or ["faithfulness", "relevance", "coherence"]

    # Index by case ID
    baseline_idx = {c.id: c for c in baseline_cases}
    candidate_idx = {c.id: c for c in candidate_cases}
    common_ids = set(baseline_idx) & set(candidate_idx)

    async def score_pair(case_id: str):
        b = baseline_idx[case_id]
        c = candidate_idx[case_id]
        b_result = await judge.score(b.question, b.actual, b.context, dims)
        c_result = await judge.score(c.question, c.actual, c.context, dims)
        return case_id, b_result, c_result

    results = await asyncio.gather(*[score_pair(cid) for cid in common_ids])

    per_case = []
    dim_deltas: dict[str, list[float]] = {d: [] for d in dims}
    regressions = []
    improvements = []

    for case_id, b_result, c_result in results:
        case_data: dict[str, Any] = {
            "id": case_id,
            "baseline_overall": round(b_result.overall, 4),
            "candidate_overall": round(c_result.overall, 4),
            "delta_overall": round(c_result.overall - b_result.overall, 4),
            "dimensions": {},
        }
        for dim in dims:
            if dim not in b_result.scores or dim not in c_result.scores:
                continue
            b_score = b_result.scores[dim].score
            c_score = c_result.scores[dim].score
            delta = c_score - b_score
            dim_deltas[dim].append(delta)
            case_data["dimensions"][dim] = {
                "baseline": round(b_score, 4),
                "candidate": round(c_score, 4),
                "delta": round(delta, 4),
            }
        per_case.append(case_data)

        overall_delta = c_result.overall - b_result.overall
        if overall_delta < -regression_threshold:
            regressions.append({"id": case_id, "delta": round(overall_delta, 4)})
        elif overall_delta > regression_threshold:
            improvements.append({"id": case_id, "delta": round(overall_delta, 4)})

    # Aggregate per-dimension
    dim_summary = {}
    for dim in dims:
        deltas = dim_deltas[dim]
        if deltas:
            avg_delta = sum(deltas) / len(deltas)
            dim_summary[dim] = {
                "avg_delta": round(avg_delta, 4),
                "regression": avg_delta < -regression_threshold,
            }

    passed = len(regressions) == 0 and all(not v["regression"] for v in dim_summary.values())

    summary = {
        "total_cases": len(common_ids),
        "regressions": len(regressions),
        "improvements": len(improvements),
        "unchanged": len(common_ids) - len(regressions) - len(improvements),
        "dimensions": dim_summary,
        "passed": passed,
    }

    return RegressionReport(
        baseline_name=baseline_name,
        candidate_name=candidate_name,
        passed=passed,
        summary=summary,
        regressions=regressions,
        improvements=improvements,
        per_case=per_case,
    )
