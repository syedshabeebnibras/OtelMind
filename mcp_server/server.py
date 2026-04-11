"""OtelMind MCP Server.

Exposes OtelMind's AI agent failure detection and evaluation logic as four
MCP tools consumable by Claude Desktop, Claude Code, or any MCP client.

Tools:
  classify_agent_failure  — heuristic + LLM failure classification
  check_hallucination     — grounding check for LLM outputs
  run_eval_benchmark      — accuracy / faithfulness / relevance scoring
  get_trace_summary       — duration, tokens, cost, bottlenecks, timeline

Run directly:
    python server.py

Or via the installed script:
    otelmind-mcp
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Allow running as `python server.py` from any working directory
sys.path.insert(0, str(Path(__file__).parent))

from mcp.server.fastmcp import FastMCP

from tools.classifier import classify_agent_failure as _classify
from tools.eval_runner import run_eval_benchmark as _run_eval
from tools.hallucination import check_hallucination as _check_hallucination
from tools.trace_summary import get_trace_summary as _trace_summary

mcp = FastMCP(
    "OtelMind",
    instructions=(
        "OtelMind detects failures in AI agent traces and evaluates LLM outputs. "
        "All tools work without an API key (heuristic mode). "
        "Set OPENAI_API_KEY to unlock LLM-powered semantic analysis."
    ),
)


@mcp.tool()
async def classify_agent_failure(trace: list[dict[str, Any]]) -> dict[str, Any]:
    """Classify failures in an AI agent trace.

    Runs heuristic rules first (fast, free — no API key needed):
      - tool_timeout    : any span > 30 seconds
      - infinite_loop   : same node executed ≥ 5 times
      - context_overflow: total tokens > 120 000
      - tool_misuse     : ≥ 2 spans with ERROR status

    Falls back to GPT-4o judge when OPENAI_API_KEY is set and heuristics
    find nothing, enabling hallucination detection.

    Each span in `trace` should be a dict with:
      span_name / name  (str)   — node or tool name
      duration_ms       (float) — execution time in ms
      status_code       (str)   — "OK" | "ERROR" | "UNSET"
      prompt_tokens     (int)   — optional
      completion_tokens (int)   — optional
      error_message     (str)   — optional
      input_preview     (str)   — optional
      output_preview    (str)   — optional

    Returns:
      failure_type  — hallucination | tool_timeout | infinite_loop |
                      context_overflow | tool_misuse | no_failure
      confidence    — float 0–1
      judge_model   — "heuristic" | "gpt-4o"
      reasoning     — human-readable explanation
    """
    return await _classify(trace)


@mcp.tool()
async def check_hallucination(
    llm_output: str,
    source_context: str,
) -> dict[str, Any]:
    """Check whether an LLM output is grounded in the provided source context.

    With OPENAI_API_KEY set: uses GPT-4o to semantically verify grounding and
    identify specific unsupported claims.

    Without API key: uses keyword-overlap heuristic (30% overlap threshold).

    Args:
      llm_output     — the text produced by the LLM to verify
      source_context — the reference text the LLM should have used

    Returns:
      is_grounded        — bool
      confidence         — float 0–1
      reasoning          — explanation
      unsupported_claims — list of hallucinated phrases (LLM mode only)
      method             — "llm_judge" | "keyword_overlap"
      overlap_score      — keyword overlap fraction (heuristic mode only)
    """
    return await _check_hallucination(llm_output, source_context)


@mcp.tool()
async def run_eval_benchmark(
    test_cases: list[dict[str, Any]],
    metrics: list[str] | None = None,
) -> dict[str, Any]:
    """Score a set of LLM test cases across evaluation metrics.

    Args:
      test_cases — list of dicts, each with:
                     input    (str) — the question or prompt
                     expected (str) — the reference / ground-truth answer
                     actual   (str) — the LLM's actual output

      metrics    — which metrics to compute. Default: ["accuracy"].
                   Options:
                     "accuracy"     — fuzzy string match (SequenceMatcher),
                                      always available, no API key needed
                     "faithfulness" — LLM-judged faithfulness to expected
                                      answer; requires OPENAI_API_KEY
                     "relevance"    — LLM-judged relevance to input question;
                                      requires OPENAI_API_KEY

    Returns:
      summary     — per-metric aggregates: mean, min, max, scored count
      per_case    — individual case results with all scores and reasoning
      total_cases — int
      llm_scoring — whether LLM scoring was active
    """
    return await _run_eval(test_cases, metrics)


@mcp.tool()
def get_trace_summary(trace: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarise an agent trace: duration, tokens, cost, bottlenecks, errors.

    Works entirely offline — no API key required.

    Each span dict may include:
      span_name / name  (str)   — node or tool name
      duration_ms       (float) — execution time in ms
      status_code       (str)   — "OK" | "ERROR" | "UNSET"
      prompt_tokens     (int)   — optional, for cost estimation
      completion_tokens (int)   — optional, for cost estimation
      model             (str)   — optional LLM model name (gpt-4o, etc.)
      error_message     (str)   — optional
      span_id           (str)   — optional
      start_time        (float) — optional Unix timestamp for timeline ordering

    Returns:
      span_count        — number of spans analysed
      total_duration_ms — sum of all span durations
      token_usage       — {prompt, completion, total}
      cost_estimate_usd — estimated API cost (based on model pricing tables)
      model             — model detected in trace
      bottlenecks       — spans taking >30% of total trace time
      repeated_nodes    — nodes that appear more than once (possible loops)
      error_details     — spans with ERROR status or error_message set
      timeline          — ordered list of spans with key fields
    """
    return _trace_summary(trace)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
