"""Tests for otelmind.watchdog.llm_judge — LLM-based failure classifier."""

from __future__ import annotations

import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from otelmind.watchdog.llm_judge import (
    VALID_FAILURE_TYPES,
    _build_trace_summary,
    _validate_response,
    classify_with_llm,
)


def _mock_openai_response(content: str, usage_tokens: tuple[int, int, int] = (100, 50, 150)):
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = content
    response.usage = MagicMock(
        prompt_tokens=usage_tokens[0],
        completion_tokens=usage_tokens[1],
        total_tokens=usage_tokens[2],
    )
    return response


def _patched_client(response):
    """Swap sys.modules['openai'] so the in-function `import openai` resolves to our mock."""
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=response)
    fake_openai = MagicMock(
        AsyncOpenAI=MagicMock(return_value=client),
        RateLimitError=type("RateLimitError", (Exception,), {}),
        APIConnectionError=type("APIConnectionError", (Exception,), {}),
        APITimeoutError=type("APITimeoutError", (Exception,), {}),
    )
    return patch.dict(sys.modules, {"openai": fake_openai})


def test_validate_response_rejects_unknown_failure_type():
    assert (
        _validate_response({"failure_type": "bogus", "confidence": 0.9, "reasoning": "x"}) is None
    )


def test_validate_response_rejects_out_of_range_confidence():
    assert (
        _validate_response({"failure_type": "hallucination", "confidence": 1.5, "reasoning": "x"})
        is None
    )
    assert (
        _validate_response({"failure_type": "hallucination", "confidence": -0.1, "reasoning": "x"})
        is None
    )


def test_validate_response_rejects_empty_reasoning():
    assert (
        _validate_response({"failure_type": "hallucination", "confidence": 0.9, "reasoning": ""})
        is None
    )
    assert (
        _validate_response({"failure_type": "hallucination", "confidence": 0.9, "reasoning": "   "})
        is None
    )


def test_validate_response_rejects_non_dict():
    assert _validate_response("not a dict") is None
    assert _validate_response(None) is None


def test_validate_response_accepts_valid_payload():
    out = _validate_response(
        {"failure_type": "hallucination", "confidence": 0.9, "reasoning": "clear"}
    )
    assert out == {"failure_type": "hallucination", "confidence": 0.9, "reasoning": "clear"}


def test_valid_failure_types_matches_expected_set():
    assert "hallucination" in VALID_FAILURE_TYPES
    assert "no_failure" in VALID_FAILURE_TYPES
    assert len(VALID_FAILURE_TYPES) == 6


def test_build_trace_summary_truncates_to_20_spans():
    spans = [{"span_name": f"s{i}", "status_code": "OK", "duration_ms": 100} for i in range(30)]
    summary = _build_trace_summary("trace-abc", spans)
    assert "Total spans: 30" in summary
    assert "Span 19:" in summary
    assert "Span 20:" not in summary


def test_build_trace_summary_truncates_previews():
    long_text = "x" * 500
    spans = [{"span_name": "s0", "input_preview": long_text, "output_preview": long_text}]
    summary = _build_trace_summary("t", spans)
    # The preview is truncated to 200 chars each in the summary
    assert summary.count("x" * 200) >= 2
    assert "x" * 201 not in summary.split("\n")[2]


@pytest.mark.asyncio
async def test_classify_returns_none_when_disabled(monkeypatch):
    monkeypatch.setattr("otelmind.watchdog.llm_judge.settings.watchdog_llm_judge_enabled", False)
    result = await classify_with_llm("t", [])
    assert result is None


@pytest.mark.asyncio
async def test_classify_returns_none_without_api_key(monkeypatch):
    monkeypatch.setattr("otelmind.watchdog.llm_judge.settings.watchdog_llm_judge_enabled", True)
    monkeypatch.setattr("otelmind.watchdog.llm_judge.settings.llm.api_key", "")
    monkeypatch.setattr(
        "otelmind.watchdog.llm_judge.settings",
        MagicMock(
            watchdog_llm_judge_enabled=True,
            llm=MagicMock(api_key="", model="gpt-4o"),
        ),
    )
    result = await classify_with_llm("t", [])
    assert result is None


@pytest.mark.asyncio
async def test_classify_filters_low_confidence(monkeypatch):
    monkeypatch.setattr(
        "otelmind.watchdog.llm_judge.settings",
        MagicMock(
            watchdog_llm_judge_enabled=True,
            llm=MagicMock(api_key="key", model="gpt-4o"),
            llm_api_key="key",
            llm_model="gpt-4o",
        ),
    )
    response = _mock_openai_response(
        json.dumps({"failure_type": "hallucination", "confidence": 0.5, "reasoning": "unsure"})
    )
    with _patched_client(response):
        result = await classify_with_llm("t", [])
    assert result is None


@pytest.mark.asyncio
async def test_classify_filters_no_failure(monkeypatch):
    monkeypatch.setattr(
        "otelmind.watchdog.llm_judge.settings",
        MagicMock(
            watchdog_llm_judge_enabled=True,
            llm=MagicMock(api_key="key", model="gpt-4o"),
            llm_api_key="key",
            llm_model="gpt-4o",
        ),
    )
    response = _mock_openai_response(
        json.dumps({"failure_type": "no_failure", "confidence": 0.95, "reasoning": "all good"})
    )
    with _patched_client(response):
        result = await classify_with_llm("t", [])
    assert result is None


@pytest.mark.asyncio
async def test_classify_returns_valid_classification(monkeypatch):
    monkeypatch.setattr(
        "otelmind.watchdog.llm_judge.settings",
        MagicMock(
            watchdog_llm_judge_enabled=True,
            llm=MagicMock(api_key="key", model="gpt-4o"),
            llm_api_key="key",
            llm_model="gpt-4o",
        ),
    )
    response = _mock_openai_response(
        json.dumps(
            {"failure_type": "hallucination", "confidence": 0.85, "reasoning": "fabricated quote"}
        ),
        usage_tokens=(120, 60, 180),
    )
    with _patched_client(response):
        result = await classify_with_llm("t", [])
    assert result is not None
    assert result["failure_type"] == "hallucination"
    assert result["confidence"] == 0.85
    assert result["judge_model"] == "gpt-4o"
    assert result["token_usage"]["total_tokens"] == 180


@pytest.mark.asyncio
async def test_classify_handles_malformed_json(monkeypatch):
    monkeypatch.setattr(
        "otelmind.watchdog.llm_judge.settings",
        MagicMock(
            watchdog_llm_judge_enabled=True,
            llm=MagicMock(api_key="key", model="gpt-4o"),
            llm_api_key="key",
            llm_model="gpt-4o",
        ),
    )
    response = _mock_openai_response("not json at all {{{")
    with _patched_client(response):
        result = await classify_with_llm("t", [])
    assert result is None
