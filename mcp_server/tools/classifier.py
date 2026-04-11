"""Classify AI agent failures using heuristics first, LLM judge as fallback.

Mirrors the logic in otelmind/watchdog/heuristics.py and llm_judge.py
without importing them directly, to keep this MCP server self-contained.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# --- Heuristic thresholds (mirrors otelmind/watchdog/heuristics.py) ---
TIMEOUT_THRESHOLD_MS: float = 30_000
LOOP_THRESHOLD: int = 5
TOKEN_THRESHOLD: int = 120_000

CLASSIFIER_SYSTEM_PROMPT = """You are an AI agent failure classifier.

Analyze the provided trace data and classify the failure into one of:
- hallucination: output not grounded in inputs or tool results
- tool_timeout: a tool call took too long
- infinite_loop: the agent is repeating the same actions
- tool_misuse: tools called incorrectly or with wrong parameters
- context_overflow: context window exceeded
- no_failure: trace looks normal

Respond with ONLY a JSON object:
{
  "failure_type": "one_of_the_above",
  "confidence": 0.0,
  "reasoning": "brief explanation"
}

Be conservative: if unsure, classify as no_failure with low confidence.
A confidence below 0.7 will be treated as no_failure."""


def _normalize_span(span: dict[str, Any]) -> dict[str, Any]:
    """Normalise span dicts — support span_name, name, and node_name interchangeably."""
    normalized = dict(span)
    # Resolve span_name from any of the common field names
    resolved = (
        normalized.get("span_name")
        or normalized.get("name")
        or normalized.get("node_name")
        or normalized.get("node")
    )
    if resolved:
        normalized["span_name"] = resolved
        normalized["name"] = resolved
    return normalized


def _check_tool_timeout(spans: list[dict[str, Any]]) -> dict[str, Any] | None:
    for span in spans:
        s = _normalize_span(span)
        duration = s.get("duration_ms") or 0
        if duration > TIMEOUT_THRESHOLD_MS:
            return {
                "failure_type": "tool_timeout",
                "confidence": min(duration / (TIMEOUT_THRESHOLD_MS * 2), 1.0),
                "judge_model": "heuristic",
                "reasoning": (
                    f"Span '{s.get('span_name', 'unknown')}' took "
                    f"{duration:.0f}ms (threshold: {TIMEOUT_THRESHOLD_MS:.0f}ms)"
                ),
                "span_id": s.get("span_id"),
            }
    return None


def _check_infinite_loop(spans: list[dict[str, Any]]) -> dict[str, Any] | None:
    node_counts: dict[str, int] = {}
    for span in spans:
        s = _normalize_span(span)
        name = s.get("span_name") or s.get("node") or "unknown"
        node_counts[name] = node_counts.get(name, 0) + 1

    for name, count in node_counts.items():
        if count >= LOOP_THRESHOLD:
            return {
                "failure_type": "infinite_loop",
                "confidence": min(count / (LOOP_THRESHOLD * 2), 1.0),
                "judge_model": "heuristic",
                "reasoning": (
                    f"Node '{name}' executed {count} times (threshold: {LOOP_THRESHOLD})"
                ),
            }
    return None


def _check_context_overflow(spans: list[dict[str, Any]]) -> dict[str, Any] | None:
    total_tokens = 0
    for span in spans:
        total_tokens += (span.get("prompt_tokens") or 0) + (span.get("completion_tokens") or 0)

    if total_tokens > TOKEN_THRESHOLD:
        return {
            "failure_type": "context_overflow",
            "confidence": min(total_tokens / (TOKEN_THRESHOLD * 1.5), 1.0),
            "judge_model": "heuristic",
            "reasoning": (
                f"Total tokens ({total_tokens:,}) exceed threshold ({TOKEN_THRESHOLD:,})"
            ),
        }
    return None


def _check_tool_misuse(spans: list[dict[str, Any]]) -> dict[str, Any] | None:
    error_spans = [s for s in spans if s.get("status_code") == "ERROR"]
    if len(error_spans) >= 2:
        names = ", ".join(
            _normalize_span(s).get("span_name", "?") for s in error_spans[:5]
        )
        return {
            "failure_type": "tool_misuse",
            "confidence": min(len(error_spans) / 5.0, 1.0),
            "judge_model": "heuristic",
            "reasoning": f"{len(error_spans)} spans failed with errors: {names}",
        }
    return None


def _run_heuristics(spans: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Run all heuristic checks; return the first match."""
    for check in (
        _check_tool_timeout,
        _check_infinite_loop,
        _check_context_overflow,
        _check_tool_misuse,
    ):
        result = check(spans)
        if result is not None:
            return result
    return None


def _build_trace_summary_for_llm(trace: list[dict[str, Any]]) -> str:
    lines = [f"Total spans: {len(trace)}", ""]
    for i, span in enumerate(trace[:20]):
        s = _normalize_span(span)
        name = s.get("span_name", "unknown")
        status = s.get("status_code", "?")
        duration = s.get("duration_ms", "?")
        input_preview = str(s.get("input_preview") or s.get("input") or "")[:200]
        output_preview = str(s.get("output_preview") or s.get("output") or "")[:200]
        error = s.get("error_message", "")

        lines.append(f"Span {i}: {name} [{status}] ({duration}ms)")
        if input_preview:
            lines.append(f"  Input: {input_preview}")
        if output_preview:
            lines.append(f"  Output: {output_preview}")
        if error:
            lines.append(f"  Error: {error}")
        lines.append("")
    return "\n".join(lines)


async def _classify_with_llm(
    trace: list[dict[str, Any]], api_key: str
) -> dict[str, Any] | None:
    try:
        import openai
    except ImportError:
        logger.warning("openai package not installed — LLM judge unavailable")
        return None

    summary = _build_trace_summary_for_llm(trace)
    try:
        client = openai.AsyncOpenAI(api_key=api_key)
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": CLASSIFIER_SYSTEM_PROMPT},
                {"role": "user", "content": summary},
            ],
            temperature=0.1,
            max_tokens=500,
        )
        content = response.choices[0].message.content or ""
        result = json.loads(content)

        if result.get("confidence", 0) < 0.7:
            return None
        if result.get("failure_type") == "no_failure":
            return None

        result["judge_model"] = "gpt-4o"
        return result

    except (json.JSONDecodeError, KeyError) as e:
        logger.warning("Failed to parse LLM judge response: %s", e)
        return None
    except Exception as e:
        logger.error("LLM judge call failed: %s", e)
        return None


async def classify_agent_failure(trace: list[dict[str, Any]]) -> dict[str, Any]:
    """Classify failures in an agent trace.

    Pipeline:
      1. Heuristic rules (fast, free — always runs).
      2. GPT-4o judge (requires OPENAI_API_KEY, runs only when heuristics find nothing).
      3. no_failure if neither fires.
    """
    if not trace:
        return {
            "failure_type": "no_failure",
            "confidence": 1.0,
            "judge_model": "heuristic",
            "reasoning": "Empty trace — nothing to analyze.",
        }

    result = _run_heuristics(trace)
    if result is not None:
        return result

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if api_key:
        llm_result = await _classify_with_llm(trace, api_key)
        if llm_result is not None:
            return llm_result

    return {
        "failure_type": "no_failure",
        "confidence": 0.9,
        "judge_model": "heuristic" if not api_key else "gpt-4o",
        "reasoning": "No failure patterns detected by heuristics"
        + (" or LLM judge." if api_key else ". Set OPENAI_API_KEY to enable semantic analysis."),
    }
