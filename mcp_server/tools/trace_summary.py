"""Trace analysis and summarization.

Computes duration, token usage, cost estimates, bottlenecks, repeated nodes,
error details, and a step-by-step timeline from a list of spans.

No external API calls — works entirely offline.
"""

from __future__ import annotations

from typing import Any

# Approximate cost per 1 000 tokens (USD) for common LLM models
_MODEL_COSTS_PER_1K: dict[str, dict[str, float]] = {
    "gpt-4o": {"input": 0.005, "output": 0.015},
    "gpt-4o-mini": {"input": 0.000150, "output": 0.000600},
    "gpt-4-turbo": {"input": 0.010, "output": 0.030},
    "gpt-3.5-turbo": {"input": 0.0005, "output": 0.0015},
    "claude-3-5-sonnet": {"input": 0.003, "output": 0.015},
    "claude-3-5-haiku": {"input": 0.0008, "output": 0.004},
    "claude-3-opus": {"input": 0.015, "output": 0.075},
    "gemini-1.5-pro": {"input": 0.00125, "output": 0.005},
    # fallback
    "default": {"input": 0.005, "output": 0.015},
}

# Bottleneck: spans taking more than this fraction of total trace time
BOTTLENECK_THRESHOLD = 0.30


def _get_model_costs(model: str | None) -> dict[str, float]:
    if not model:
        return _MODEL_COSTS_PER_1K["default"]
    model_lower = model.lower()
    for key, costs in _MODEL_COSTS_PER_1K.items():
        if key != "default" and key in model_lower:
            return costs
    return _MODEL_COSTS_PER_1K["default"]


def _normalize_span(span: dict[str, Any]) -> dict[str, Any]:
    """Normalise span dicts — support span_name, name, and node_name interchangeably."""
    normalized = dict(span)
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


def get_trace_summary(trace: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarise an agent trace.

    Each span dict may include any of:
      span_name / name  — str, node or tool name
      duration_ms       — float, execution time in milliseconds
      status_code       — "OK" | "ERROR" | "UNSET"
      prompt_tokens     — int
      completion_tokens — int
      model             — str, LLM model name (used for cost estimation)
      error_message     — str
      span_id           — str
      start_time        — float (Unix timestamp, used for timeline ordering)
      input_preview     — str
      output_preview    — str

    Returns:
      span_count          — total number of spans
      total_duration_ms   — sum of all span durations
      token_usage         — {prompt, completion, total}
      cost_estimate_usd   — estimated API cost in USD
      model               — model detected in trace (if any)
      bottlenecks         — spans taking >30% of total trace time
      repeated_nodes      — nodes that appear more than once
      error_details       — spans with ERROR status or error_message
      timeline            — ordered list of spans with key fields
    """
    if not trace:
        return {
            "span_count": 0,
            "total_duration_ms": 0,
            "token_usage": {"prompt": 0, "completion": 0, "total": 0},
            "cost_estimate_usd": 0.0,
            "model": None,
            "bottlenecks": [],
            "repeated_nodes": [],
            "error_details": [],
            "timeline": [],
        }

    spans = [_normalize_span(s) for s in trace]

    # --- Totals ---
    total_duration = sum(s.get("duration_ms") or 0 for s in spans)
    total_prompt = sum(s.get("prompt_tokens") or 0 for s in spans)
    total_completion = sum(s.get("completion_tokens") or 0 for s in spans)
    total_tokens = total_prompt + total_completion

    # --- Cost estimate ---
    model: str | None = None
    for s in spans:
        candidate = (
            s.get("model")
            or s.get("llm_model")
            or (s.get("attributes") or {}).get("llm.model")
        )
        if candidate:
            model = str(candidate)
            break

    costs = _get_model_costs(model)
    cost_usd = (
        (total_prompt / 1_000) * costs["input"]
        + (total_completion / 1_000) * costs["output"]
    )

    # --- Bottlenecks ---
    bottleneck_threshold_ms = total_duration * BOTTLENECK_THRESHOLD
    bottlenecks: list[dict[str, Any]] = []
    if total_duration > 0:
        for s in spans:
            dur = s.get("duration_ms") or 0
            if dur > bottleneck_threshold_ms:
                bottlenecks.append(
                    {
                        "span_name": s.get("span_name", "unknown"),
                        "duration_ms": dur,
                        "pct_of_total": round(dur / total_duration * 100, 1),
                        "span_id": s.get("span_id"),
                    }
                )
        bottlenecks.sort(key=lambda x: x["duration_ms"], reverse=True)

    # --- Repeated nodes ---
    node_counts: dict[str, int] = {}
    for s in spans:
        name = s.get("span_name") or s.get("node") or "unknown"
        node_counts[name] = node_counts.get(name, 0) + 1

    repeated_nodes = sorted(
        [{"node": name, "count": count} for name, count in node_counts.items() if count > 1],
        key=lambda x: x["count"],
        reverse=True,
    )

    # --- Error details ---
    error_details = [
        {
            "span_name": s.get("span_name", "unknown"),
            "error_message": s.get("error_message") or "",
            "span_id": s.get("span_id"),
            "status_code": s.get("status_code", "UNSET"),
        }
        for s in spans
        if s.get("status_code") == "ERROR" or s.get("error_message")
    ]

    # --- Timeline ---
    # Sort by start_time if available, otherwise preserve input order
    ordered = sorted(spans, key=lambda s: s.get("start_time") or 0)
    timeline = [
        {
            "index": i,
            "span_name": s.get("span_name", "unknown"),
            "duration_ms": s.get("duration_ms"),
            "status": s.get("status_code", "UNSET"),
            "prompt_tokens": s.get("prompt_tokens"),
            "completion_tokens": s.get("completion_tokens"),
            "error": s.get("error_message") or None,
        }
        for i, s in enumerate(ordered)
    ]

    return {
        "span_count": len(spans),
        "total_duration_ms": total_duration,
        "token_usage": {
            "prompt": total_prompt,
            "completion": total_completion,
            "total": total_tokens,
        },
        "cost_estimate_usd": round(cost_usd, 6),
        "model": model,
        "bottlenecks": bottlenecks,
        "repeated_nodes": repeated_nodes,
        "error_details": error_details,
        "timeline": timeline,
    }
