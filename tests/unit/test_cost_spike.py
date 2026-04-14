"""Tests for otelmind.watchdog.detectors.cost_spike."""

from __future__ import annotations

from otelmind.watchdog.detectors.cost_spike import (
    RUNAWAY_TOKEN_THRESHOLD,
    SINGLE_SPAN_TOKEN_RATIO_THRESHOLD,
    detect_cost_spike,
)


def test_runaway_tokens_flagged():
    spans = [
        {"span_name": "llm.generate", "prompt_tokens": 40_000, "completion_tokens": 50_000},
        {"span_name": "llm.followup", "prompt_tokens": 10_000, "completion_tokens": 5_000},
    ]
    result = detect_cost_spike(spans)
    assert result is not None
    assert result["failure_type"] == "cost_spike"
    assert result["evidence"]["total_tokens"] >= RUNAWAY_TOKEN_THRESHOLD


def test_no_spike_when_tokens_balanced():
    # Total below the runaway threshold AND no span > 70% of total
    spans = [
        {"span_name": "a", "prompt_tokens": 200, "completion_tokens": 100},
        {"span_name": "b", "prompt_tokens": 200, "completion_tokens": 100},
        {"span_name": "c", "prompt_tokens": 200, "completion_tokens": 100},
    ]
    assert detect_cost_spike(spans) is None


def test_dominant_span_flagged():
    spans = [
        {"span_name": "huge", "prompt_tokens": 10_000, "completion_tokens": 10_000},
        {"span_name": "small1", "prompt_tokens": 50, "completion_tokens": 50},
        {"span_name": "small2", "prompt_tokens": 50, "completion_tokens": 50},
    ]
    result = detect_cost_spike(spans)
    assert result is not None
    assert result["evidence"]["ratio"] >= SINGLE_SPAN_TOKEN_RATIO_THRESHOLD
    assert result["evidence"]["span_name"] == "huge"


def test_zero_tokens_returns_none():
    spans = [{"span_name": "a", "prompt_tokens": 0, "completion_tokens": 0}]
    assert detect_cost_spike(spans) is None


def test_name_field_fallback():
    spans = [
        {"name": "without-span-name-prefix", "prompt_tokens": 9_000, "completion_tokens": 9_000},
        {"name": "tiny", "prompt_tokens": 100, "completion_tokens": 100},
    ]
    result = detect_cost_spike(spans)
    assert result is not None
    assert result["evidence"]["span_name"] == "without-span-name-prefix"


def test_empty_spans_handled():
    assert detect_cost_spike([]) is None
