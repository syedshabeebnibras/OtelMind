"""LLM-based failure classification judge.

Invoked only when heuristics don't match but error signals are present.
Uses GPT-4o (or configured LLM) to classify failures like hallucination
that require semantic understanding of inputs/outputs.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from otelmind.config import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an AI agent failure classifier for a LangGraph-based system.

Analyze the provided trace data and classify the failure into one of these categories:
- hallucination: The agent produced output not grounded in its inputs or tool results
- tool_timeout: A tool call took too long (already caught by heuristics, but confirm)
- infinite_loop: The agent is repeating the same actions (already caught by heuristics)
- tool_misuse: The agent is calling tools incorrectly or with wrong parameters
- context_overflow: The context window was exceeded
- no_failure: No failure detected — the trace looks normal

Respond with ONLY a JSON object:
{
  "failure_type": "one_of_the_above",
  "confidence": 0.0 to 1.0,
  "reasoning": "brief explanation"
}

Be conservative: if unsure, classify as no_failure with low confidence.
A confidence below 0.7 will be treated as no_failure."""


async def classify_with_llm(
    trace_id: str,
    spans: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Send trace data to LLM for failure classification.

    Returns classification dict or None if LLM judge is disabled or fails.
    """
    if not settings.watchdog_llm_judge_enabled:
        return None

    try:
        import openai
    except ImportError:
        logger.warning("openai package not installed — LLM judge disabled")
        return None

    api_key = getattr(settings, "llm_api_key", "") or ""
    if not api_key:
        logger.warning("LLM_API_KEY not set — LLM judge disabled")
        return None

    # Build trace summary for the LLM
    trace_summary = _build_trace_summary(trace_id, spans)

    try:
        client = openai.AsyncOpenAI(api_key=api_key)
        model = getattr(settings, "llm_model", "gpt-4o")

        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": trace_summary},
            ],
            temperature=0.1,
            max_tokens=500,
        )

        content = response.choices[0].message.content or ""
        result = json.loads(content)

        # Apply confidence threshold
        if result.get("confidence", 0) < 0.7:
            return None

        if result.get("failure_type") == "no_failure":
            return None

        result["judge_model"] = model
        return result

    except (json.JSONDecodeError, KeyError) as e:
        logger.warning("Failed to parse LLM judge response: %s", e)
        return None
    except Exception as e:
        logger.error("LLM judge call failed: %s", e)
        return None


def _build_trace_summary(trace_id: str, spans: list[dict[str, Any]]) -> str:
    """Build a concise trace summary for the LLM to analyze."""
    lines = [f"Trace ID: {trace_id}", f"Total spans: {len(spans)}", ""]

    for i, span in enumerate(spans[:20]):  # Limit to first 20 spans
        status = span.get("status_code", "?")
        name = span.get("span_name", "unknown")
        duration = span.get("duration_ms", "?")
        input_preview = str(span.get("input_preview", ""))[:200]
        output_preview = str(span.get("output_preview", ""))[:200]
        error = span.get("error_message", "")

        lines.append(f"Span {i}: {name} [{status}] ({duration}ms)")
        if input_preview:
            lines.append(f"  Input: {input_preview}")
        if output_preview:
            lines.append(f"  Output: {output_preview}")
        if error:
            lines.append(f"  Error: {error}")
        lines.append("")

    return "\n".join(lines)
