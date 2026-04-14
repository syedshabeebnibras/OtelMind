"""Tests for otelmind.watchdog.detectors.semantic_drift."""

from __future__ import annotations

from otelmind.watchdog.detectors.semantic_drift import detect_semantic_drift


def _span(output: str) -> dict:
    return {"span_name": "llm.call", "output_preview": output}


def test_drift_detected_on_divergent_outputs():
    spans = [
        _span("The quick brown fox jumps over the lazy dog"),
        _span("Stock market closed higher on tariff news today"),
        _span("Chicken soup is the best remedy for a cold"),
    ]
    result = detect_semantic_drift(spans, drift_threshold=0.3, min_outputs=3)
    assert result is not None
    assert result["failure_type"] == "semantic_drift"
    assert result["confidence"] > 0.3
    assert result["evidence"]["output_count"] == 3


def test_no_drift_on_similar_outputs():
    spans = [
        _span("Chicken soup helps with a cold"),
        _span("Chicken soup is good for a cold"),
        _span("A cold is helped by chicken soup"),
    ]
    result = detect_semantic_drift(spans, drift_threshold=0.5, min_outputs=3)
    assert result is None


def test_min_outputs_threshold_honored():
    spans = [_span("hello world very different")]
    assert detect_semantic_drift(spans, min_outputs=3) is None

    two_spans = [_span("aaa " * 10), _span("bbb " * 10)]
    assert detect_semantic_drift(two_spans, min_outputs=3) is None


def test_empty_and_short_outputs_filtered():
    # All outputs below 20 chars are dropped before min_outputs check
    spans = [_span("hi"), _span(""), _span("short")]
    assert detect_semantic_drift(spans, min_outputs=3) is None


def test_identical_outputs_yield_no_drift():
    out = "The agent produces a consistent answer every single time."
    spans = [_span(out), _span(out), _span(out)]
    result = detect_semantic_drift(spans, drift_threshold=0.1, min_outputs=3)
    assert result is None


def test_outputs_dict_nested_supported():
    spans = [
        {"outputs": {"content": "alpha beta gamma long enough text here"}},
        {"outputs": {"content": "delta epsilon zeta completely different text"}},
        {"outputs": {"content": "lorem ipsum dolor sit amet consectetur"}},
    ]
    result = detect_semantic_drift(spans, drift_threshold=0.2, min_outputs=3)
    assert result is not None


def test_threshold_controls_triggering_low_threshold():
    # Moderately overlapping outputs — drift is non-zero but modest
    spans = [
        _span("the quick brown fox jumps over the lazy dog today"),
        _span("the quick brown cat jumps over the lazy dog today"),
        _span("the quick brown bird jumps over the lazy dog today"),
    ]
    assert detect_semantic_drift(spans, drift_threshold=0.05, min_outputs=3) is not None


def test_threshold_controls_triggering_high_threshold():
    # Same inputs — should not trip a very strict threshold
    spans = [
        _span("the quick brown fox jumps over the lazy dog today"),
        _span("the quick brown cat jumps over the lazy dog today"),
        _span("the quick brown bird jumps over the lazy dog today"),
    ]
    assert detect_semantic_drift(spans, drift_threshold=0.99, min_outputs=3) is None
