"""Tests for MCP server tools (invoked as plain functions, not over the MCP wire)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make `tools.*` importable the same way mcp_server/server.py does
_MCP_ROOT = Path(__file__).resolve().parents[2] / "mcp_server"
if str(_MCP_ROOT) not in sys.path:
    sys.path.insert(0, str(_MCP_ROOT))

from tools.classifier import classify_agent_failure  # noqa: E402
from tools.eval_runner import run_eval_benchmark  # noqa: E402
from tools.hallucination import check_hallucination  # noqa: E402
from tools.trace_summary import get_trace_summary  # noqa: E402


@pytest.mark.asyncio
async def test_classify_timeout_trace():
    trace = [
        {"span_name": "slow_tool", "duration_ms": 60_000, "status_code": "OK"},
        {"span_name": "fast_tool", "duration_ms": 100, "status_code": "OK"},
    ]
    result = await classify_agent_failure(trace)
    assert result["failure_type"] == "tool_timeout"
    assert result["judge_model"] == "heuristic"


@pytest.mark.asyncio
async def test_classify_loop_trace():
    trace = [
        {"span_name": "search_node", "duration_ms": 100, "status_code": "OK"} for _ in range(6)
    ]
    result = await classify_agent_failure(trace)
    assert result["failure_type"] == "infinite_loop"


@pytest.mark.asyncio
async def test_classify_empty_trace():
    result = await classify_agent_failure([])
    assert result["failure_type"] == "no_failure"


@pytest.mark.asyncio
async def test_classify_no_failure_without_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    trace = [{"span_name": "ok", "duration_ms": 100, "status_code": "OK"}]
    result = await classify_agent_failure(trace)
    assert result["failure_type"] == "no_failure"


@pytest.mark.asyncio
async def test_check_hallucination_heuristic_overlap(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # Output completely grounded — every keyword appears in source
    result = await check_hallucination(
        llm_output="Paris is the capital of France.",
        source_context="Paris is the capital of France. It is a major city.",
    )
    assert result["method"] == "keyword_overlap"
    assert result["is_grounded"] is True
    assert result["overlap_score"] >= 0.8


@pytest.mark.asyncio
async def test_check_hallucination_low_overlap(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = await check_hallucination(
        llm_output="The moon is made of cheese and unicorns dance there.",
        source_context="Paris is the capital of France.",
    )
    assert result["method"] == "keyword_overlap"
    assert result["is_grounded"] is False


@pytest.mark.asyncio
async def test_run_eval_benchmark_accuracy_only(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cases = [
        {"input": "q1", "expected": "paris", "actual": "paris"},
        {"input": "q2", "expected": "london", "actual": "london"},
        {"input": "q3", "expected": "berlin", "actual": "tokyo"},
    ]
    result = await run_eval_benchmark(cases, metrics=["accuracy"])
    assert result["total_cases"] == 3
    assert "accuracy" in result["summary"]
    assert result["summary"]["accuracy"]["scored"] == 3


def test_trace_summary_cost_estimate():
    trace = [
        {
            "span_name": "llm.generate",
            "duration_ms": 200,
            "status_code": "OK",
            "model": "gpt-4o",
            "prompt_tokens": 1000,
            "completion_tokens": 500,
        }
    ]
    result = get_trace_summary(trace)
    assert result["span_count"] == 1
    assert result["cost_estimate_usd"] > 0
    assert result["token_usage"]["total"] == 1500


def test_trace_summary_bottleneck_detection():
    trace = [
        {"span_name": "big", "duration_ms": 8_000, "status_code": "OK"},
        {"span_name": "little1", "duration_ms": 500, "status_code": "OK"},
        {"span_name": "little2", "duration_ms": 500, "status_code": "OK"},
    ]
    result = get_trace_summary(trace)
    assert result["bottlenecks"]
    assert any(b["span_name"] == "big" for b in result["bottlenecks"])


def test_trace_summary_timeline_ordered_by_start_time():
    trace = [
        {"span_name": "b", "duration_ms": 100, "status_code": "OK", "start_time": 200},
        {"span_name": "a", "duration_ms": 100, "status_code": "OK", "start_time": 100},
        {"span_name": "c", "duration_ms": 100, "status_code": "OK", "start_time": 300},
    ]
    result = get_trace_summary(trace)
    names = [step["span_name"] for step in result["timeline"]]
    assert names == ["a", "b", "c"]


def test_trace_summary_error_details_included():
    trace = [
        {"span_name": "failed", "duration_ms": 100, "status_code": "ERROR", "error_message": "boom"}
    ]
    result = get_trace_summary(trace)
    assert result["error_details"]
    assert result["error_details"][0]["error_message"] == "boom"
