"""Evaluation benchmark runner for LLM test cases.

Supports three metrics:
  accuracy    — fuzzy string match (difflib SequenceMatcher), always available
  faithfulness — LLM-judged: how faithful actual is to expected/context
  relevance   — LLM-judged: how relevant actual is to the input question

faithfulness and relevance require OPENAI_API_KEY. Without it they return null
scores with an explanatory message.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from difflib import SequenceMatcher
from typing import Any

logger = logging.getLogger(__name__)

VALID_METRICS = {"accuracy", "faithfulness", "relevance"}

FAITHFULNESS_PROMPT = """\
You are an evaluation judge. Rate how faithful the ACTUAL answer is to the
EXPECTED answer or reference context.

Score 0.0 = completely unfaithful/contradicts expected.
Score 1.0 = perfectly faithful, all key facts present.

INPUT: {input}
EXPECTED: {expected}
ACTUAL: {actual}

Respond with ONLY:
{{"score": 0.85, "reasoning": "..."}}"""

RELEVANCE_PROMPT = """\
You are an evaluation judge. Rate how relevant and on-topic the ACTUAL answer
is to the INPUT question or prompt.

Score 0.0 = completely off-topic or irrelevant.
Score 1.0 = directly answers the question with appropriate detail.

INPUT: {input}
ACTUAL: {actual}

Respond with ONLY:
{{"score": 0.85, "reasoning": "..."}}"""


def _fuzzy_accuracy(actual: str, expected: str) -> float:
    """Normalised edit-distance similarity (0–1)."""
    return SequenceMatcher(
        None, actual.strip().lower(), expected.strip().lower()
    ).ratio()


async def _llm_score(prompt_template: str, api_key: str, **kwargs: str) -> dict[str, Any]:
    try:
        import openai
    except ImportError:
        return {"score": None, "reasoning": "openai package not installed"}

    prompt = prompt_template.format(**kwargs)
    try:
        client = openai.AsyncOpenAI(api_key=api_key)
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=200,
        )
        content = response.choices[0].message.content or ""
        return json.loads(content)
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning("Failed to parse LLM score response: %s", e)
        return {"score": None, "reasoning": f"Parse error: {e}"}
    except Exception as e:
        logger.error("LLM score call failed: %s", e)
        return {"score": None, "reasoning": str(e)}


async def _score_test_case(
    case: dict[str, Any],
    metrics: list[str],
    api_key: str,
) -> dict[str, Any]:
    input_text = str(case.get("input", ""))
    expected = str(case.get("expected", ""))
    actual = str(case.get("actual", ""))

    scores: dict[str, Any] = {}

    if "accuracy" in metrics:
        scores["accuracy"] = round(_fuzzy_accuracy(actual, expected), 4)

    # LLM-judged metrics
    llm_metrics_needed = [m for m in ("faithfulness", "relevance") if m in metrics]

    if llm_metrics_needed and not api_key:
        for metric in llm_metrics_needed:
            scores[metric] = None
            scores[f"{metric}_reasoning"] = (
                "OPENAI_API_KEY not set — LLM scoring unavailable"
            )
    elif llm_metrics_needed and api_key:
        tasks = []
        task_labels = []

        if "faithfulness" in llm_metrics_needed:
            tasks.append(
                _llm_score(
                    FAITHFULNESS_PROMPT,
                    api_key,
                    input=input_text,
                    expected=expected,
                    actual=actual,
                )
            )
            task_labels.append("faithfulness")

        if "relevance" in llm_metrics_needed:
            tasks.append(
                _llm_score(
                    RELEVANCE_PROMPT,
                    api_key,
                    input=input_text,
                    actual=actual,
                )
            )
            task_labels.append("relevance")

        results = await asyncio.gather(*tasks)
        for label, res in zip(task_labels, results):
            raw_score = res.get("score")
            scores[label] = round(raw_score, 4) if isinstance(raw_score, (int, float)) else None
            scores[f"{label}_reasoning"] = res.get("reasoning", "")

    return {
        "input": input_text,
        "expected": expected,
        "actual": actual,
        "scores": scores,
    }


async def run_eval_benchmark(
    test_cases: list[dict[str, Any]],
    metrics: list[str] | None = None,
) -> dict[str, Any]:
    """Score a set of LLM test cases across evaluation metrics.

    Args:
        test_cases: List of dicts with required keys: input, expected, actual.
        metrics: Which metrics to compute. Defaults to ["accuracy"].
                 Options: accuracy (fuzzy match, always free),
                          faithfulness (LLM-judged, needs OPENAI_API_KEY),
                          relevance (LLM-judged, needs OPENAI_API_KEY).

    Returns:
        summary: per-metric aggregates (mean, min, max, scored count)
        per_case: individual case results with all scores
        total_cases: int
        llm_scoring: whether LLM scoring was used
    """
    if metrics is None:
        metrics = ["accuracy"]

    unknown = set(metrics) - VALID_METRICS
    if unknown:
        return {
            "error": f"Unknown metrics: {sorted(unknown)}. Valid options: {sorted(VALID_METRICS)}"
        }

    if not test_cases:
        return {"error": "No test cases provided."}

    for i, case in enumerate(test_cases):
        missing = [k for k in ("input", "expected", "actual") if k not in case]
        if missing:
            return {
                "error": f"test_cases[{i}] is missing required keys: {missing}. "
                "Each case must have 'input', 'expected', and 'actual'."
            }

    api_key = os.environ.get("OPENAI_API_KEY", "")

    tasks = [_score_test_case(case, metrics, api_key) for case in test_cases]
    per_case = list(await asyncio.gather(*tasks))

    # Aggregate scores per metric
    aggregated: dict[str, list[float]] = {m: [] for m in metrics}
    for case_result in per_case:
        for metric in metrics:
            score = case_result["scores"].get(metric)
            if isinstance(score, (int, float)):
                aggregated[metric].append(score)

    summary: dict[str, Any] = {}
    for metric, values in aggregated.items():
        if values:
            summary[metric] = {
                "mean": round(sum(values) / len(values), 4),
                "min": round(min(values), 4),
                "max": round(max(values), 4),
                "scored": len(values),
                "total": len(test_cases),
            }
        else:
            summary[metric] = {
                "mean": None,
                "scored": 0,
                "total": len(test_cases),
                "note": "No scores available (OPENAI_API_KEY required)",
            }

    return {
        "summary": summary,
        "per_case": per_case,
        "total_cases": len(test_cases),
        "metrics": metrics,
        "llm_scoring": bool(api_key),
    }
