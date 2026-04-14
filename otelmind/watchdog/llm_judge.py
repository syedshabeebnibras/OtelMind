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

VALID_FAILURE_TYPES = {
    "hallucination",
    "tool_timeout",
    "infinite_loop",
    "tool_misuse",
    "context_overflow",
    "no_failure",
}

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


def _validate_response(data: Any) -> dict[str, Any] | None:
    """Return the dict if it matches the expected schema, else None."""
    if not isinstance(data, dict):
        return None

    failure_type = data.get("failure_type")
    if failure_type not in VALID_FAILURE_TYPES:
        logger.warning("LLM judge returned unknown failure_type: %r", failure_type)
        return None

    confidence = data.get("confidence")
    if not isinstance(confidence, (int, float)):
        logger.warning("LLM judge returned non-numeric confidence: %r", confidence)
        return None
    confidence = float(confidence)
    if not 0.0 <= confidence <= 1.0:
        logger.warning("LLM judge confidence out of range: %r", confidence)
        return None

    reasoning = data.get("reasoning")
    if not isinstance(reasoning, str) or not reasoning.strip():
        logger.warning("LLM judge returned empty reasoning")
        return None

    return {
        "failure_type": failure_type,
        "confidence": confidence,
        "reasoning": reasoning,
    }


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

    try:
        from tenacity import (
            AsyncRetrying,
            retry_if_exception_type,
            stop_after_attempt,
            wait_exponential,
        )
    except ImportError:
        logger.warning("tenacity not installed — LLM judge disabled")
        return None

    api_key = getattr(settings, "llm_api_key", "") or getattr(settings.llm, "api_key", "") or ""
    if not api_key:
        logger.warning("LLM_API_KEY not set — LLM judge disabled")
        return None

    trace_summary = _build_trace_summary(trace_id, spans)
    model = getattr(settings, "llm_model", "") or getattr(settings.llm, "model", "") or "gpt-4o"

    client = openai.AsyncOpenAI(api_key=api_key, timeout=30.0)

    retryable = (
        openai.RateLimitError,
        openai.APIConnectionError,
        openai.APITimeoutError,
    )

    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            retry=retry_if_exception_type(retryable),
            reraise=True,
        ):
            with attempt:
                response = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": trace_summary},
                    ],
                    temperature=0.1,
                    max_tokens=500,
                    response_format={"type": "json_object"},
                )
    except retryable as exc:
        logger.error("LLM judge call failed after retries: %s", exc)
        return None
    except Exception as exc:
        logger.error("LLM judge call failed: %s", exc)
        return None

    usage = getattr(response, "usage", None)
    if usage is not None:
        logger.info(
            "LLM judge tokens — prompt=%s completion=%s total=%s",
            getattr(usage, "prompt_tokens", None),
            getattr(usage, "completion_tokens", None),
            getattr(usage, "total_tokens", None),
        )

    content = response.choices[0].message.content or ""
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        logger.warning("Failed to parse LLM judge response: %s", exc)
        return None

    validated = _validate_response(parsed)
    if validated is None:
        return None

    if validated["confidence"] < 0.7:
        return None

    if validated["failure_type"] == "no_failure":
        return None

    validated["judge_model"] = model
    if usage is not None:
        validated["token_usage"] = {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0),
            "completion_tokens": getattr(usage, "completion_tokens", 0),
            "total_tokens": getattr(usage, "total_tokens", 0),
        }
    return validated


def _build_trace_summary(trace_id: str, spans: list[dict[str, Any]]) -> str:
    """Build a concise trace summary for the LLM to analyze."""
    lines = [f"Trace ID: {trace_id}", f"Total spans: {len(spans)}", ""]

    for i, span in enumerate(spans[:20]):
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
