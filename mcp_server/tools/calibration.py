"""MCP tool: calibrate the LLM judge against human-labeled cases."""

from __future__ import annotations

from typing import Any

from otelmind.eval.calibration import HumanLabel, calibrate_judge
from otelmind.eval.judge import LLMJudge
from otelmind.eval.regression import EvalCase


async def calibrate_judge_tool(
    test_cases: list[dict[str, Any]],
    human_labels: list[dict[str, Any]],
    dimensions: list[str] | None = None,
) -> dict[str, Any]:
    """Score `test_cases` with the judge, compare to `human_labels`, report agreement.

    test_cases  — list of dicts with id, question, actual, context (optional),
                  expected (optional)
    human_labels — list of dicts with case_id, dimension, score (0-1), annotator_id (optional)
    dimensions   — which judge dimensions to evaluate (default: all dimensions found in labels)
    """
    cases = [
        EvalCase(
            id=str(c["id"]),
            question=str(c.get("question", "")),
            expected=str(c.get("expected", "")),
            actual=str(c.get("actual", "")),
            context=str(c.get("context", "")),
            tags=list(c.get("tags", [])),
        )
        for c in test_cases
    ]

    labels = [
        HumanLabel(
            case_id=str(label["case_id"]),
            dimension=str(label["dimension"]),
            score=float(label["score"]),
            annotator_id=label.get("annotator_id"),
        )
        for label in human_labels
    ]

    judge = LLMJudge()
    result = await calibrate_judge(judge, cases, labels, dimensions=dimensions)
    return result.to_dict()
