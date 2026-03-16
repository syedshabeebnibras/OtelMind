"""Rule-based heuristic failure detection — fast, free, reliable.

These heuristics run first before the LLM judge. They catch obvious
patterns that don't need AI to detect:
- tool_timeout: any span > 30 seconds
- infinite_loop: same node executed > 5 times in one trace
- context_overflow: token count exceeds model limit
- tool_misuse: multiple error spans in one trace
"""

from __future__ import annotations

from typing import Any


def check_tool_timeout(
    spans: list[dict[str, Any]], threshold_ms: float = 30_000
) -> dict[str, Any] | None:
    """Detect spans that took too long to execute."""
    for span in spans:
        duration = span.get("duration_ms") or 0
        if duration > threshold_ms:
            return {
                "failure_type": "tool_timeout",
                "confidence": min(duration / (threshold_ms * 2), 1.0),
                "judge_model": "heuristic",
                "reasoning": (
                    f"Span '{span.get('span_name', 'unknown')}' took "
                    f"{duration:.0f}ms (threshold: {threshold_ms:.0f}ms)"
                ),
                "span_id": span.get("span_id"),
            }
    return None


def check_infinite_loop(spans: list[dict[str, Any]], threshold: int = 5) -> dict[str, Any] | None:
    """Detect nodes that executed too many times (likely stuck in a loop)."""
    node_counts: dict[str, int] = {}
    for span in spans:
        name = span.get("span_name", "unknown")
        node_counts[name] = node_counts.get(name, 0) + 1

    for name, count in node_counts.items():
        if count >= threshold:
            return {
                "failure_type": "infinite_loop",
                "confidence": min(count / (threshold * 2), 1.0),
                "judge_model": "heuristic",
                "reasoning": (f"Node '{name}' executed {count} times " f"(threshold: {threshold})"),
            }
    return None


def check_context_overflow(
    spans: list[dict[str, Any]], token_threshold: int = 120_000
) -> dict[str, Any] | None:
    """Detect traces where total token usage exceeds the model context limit."""
    total_tokens = 0
    for span in spans:
        total_tokens += span.get("prompt_tokens", 0) + span.get("completion_tokens", 0)

    if total_tokens > token_threshold:
        return {
            "failure_type": "context_overflow",
            "confidence": min(total_tokens / (token_threshold * 1.5), 1.0),
            "judge_model": "heuristic",
            "reasoning": (f"Total tokens ({total_tokens}) exceed threshold ({token_threshold})"),
        }
    return None


def check_tool_misuse(spans: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Detect traces with multiple tool errors."""
    error_spans = [s for s in spans if s.get("status_code") == "ERROR"]
    if len(error_spans) >= 2:
        return {
            "failure_type": "tool_misuse",
            "confidence": min(len(error_spans) / 5.0, 1.0),
            "judge_model": "heuristic",
            "reasoning": (
                f"{len(error_spans)} spans failed with errors: "
                + ", ".join(s.get("span_name", "?") for s in error_spans[:5])
            ),
        }
    return None


def run_all_heuristics(spans: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Run all heuristic checks and return the first match (if any)."""
    checks = [
        check_tool_timeout,
        check_infinite_loop,
        check_context_overflow,
        check_tool_misuse,
    ]
    for check in checks:
        result = check(spans)
        if result is not None:
            return result
    return None
