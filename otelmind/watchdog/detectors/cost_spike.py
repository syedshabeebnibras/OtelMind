"""Cost spike detector — catches unexpected token explosions mid-trace."""

from __future__ import annotations

from typing import Any

# If a single span consumes more than this fraction of the total trace tokens, flag it
SINGLE_SPAN_TOKEN_RATIO_THRESHOLD = 0.70
# If total tokens in a trace exceed this, flag as potentially runaway
RUNAWAY_TOKEN_THRESHOLD = 80_000


def detect_cost_spike(spans: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Detect abnormal token consumption in a trace."""
    total_prompt = sum(s.get("prompt_tokens") or 0 for s in spans)
    total_completion = sum(s.get("completion_tokens") or 0 for s in spans)
    total_tokens = total_prompt + total_completion

    if total_tokens == 0:
        return None

    # Check for runaway token usage
    if total_tokens > RUNAWAY_TOKEN_THRESHOLD:
        return {
            "failure_type": "cost_spike",
            "confidence": min(total_tokens / (RUNAWAY_TOKEN_THRESHOLD * 2), 1.0),
            "judge_model": "heuristic",
            "reasoning": (
                f"Trace consumed {total_tokens:,} tokens — "
                f"exceeds runaway threshold ({RUNAWAY_TOKEN_THRESHOLD:,})"
            ),
            "evidence": {
                "total_tokens": total_tokens,
                "total_prompt_tokens": total_prompt,
                "total_completion_tokens": total_completion,
                "threshold": RUNAWAY_TOKEN_THRESHOLD,
            },
        }

    # Check for single span dominating token usage
    for span in spans:
        span_tokens = (span.get("prompt_tokens") or 0) + (span.get("completion_tokens") or 0)
        if span_tokens == 0:
            continue
        ratio = span_tokens / total_tokens
        if ratio >= SINGLE_SPAN_TOKEN_RATIO_THRESHOLD:
            name = span.get("span_name") or span.get("name") or "unknown"
            return {
                "failure_type": "cost_spike",
                "confidence": min(ratio, 1.0),
                "judge_model": "heuristic",
                "reasoning": (
                    f"Span '{name}' consumed {span_tokens:,} tokens "
                    f"({ratio:.0%} of total {total_tokens:,})"
                ),
                "evidence": {
                    "span_name": name,
                    "span_tokens": span_tokens,
                    "total_tokens": total_tokens,
                    "ratio": round(ratio, 4),
                },
            }

    return None
