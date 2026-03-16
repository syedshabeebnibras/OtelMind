#!/usr/bin/env python3
"""Runner script — executes the research agent with OtelMind instrumentation.

Sends live telemetry data to the deployed Koyeb OtelMind instance.
"""

from __future__ import annotations

import os
import sys
import time

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

# Verify OpenAI key is set
if not os.getenv("OPENAI_API_KEY") and not os.getenv("LLM_API_KEY"):
    # Try LLM_API_KEY as fallback
    llm_key = os.getenv("LLM_API_KEY", "")
    if llm_key:
        os.environ["OPENAI_API_KEY"] = llm_key
    else:
        print("ERROR: Set OPENAI_API_KEY or LLM_API_KEY in .env")
        sys.exit(1)

from agent.graph import build_graph
from agent.telemetry import OtelMindTelemetry

# ── Sample queries ──────────────────────────────────────────────────────

QUERIES = [
    # Normal queries
    "What are the latest trends in AI agents and autonomous systems in 2024-2025?",
    "Explain the differences between RAG, fine-tuning, and prompt engineering for LLMs",
    "What is OpenTelemetry and how does it work for distributed tracing?",
    # Harder query — likely to trigger longer processing / revision loops
    "Compare every major cloud provider's serverless GPU offering, include exact pricing "
    "per hour, supported GPU models, cold start times, and regional availability as of 2025. "
    "Be extremely specific with numbers.",
    # Intentionally vague — may produce lower quality that triggers review loop
    "Tell me about the thing with the stuff",
]


def run_agent(query: str, telemetry: OtelMindTelemetry, run_number: int) -> None:
    """Run the research agent on a single query with full instrumentation."""
    print(f"\n{'='*70}")
    print(f"RUN {run_number}: {query[:80]}{'...' if len(query) > 80 else ''}")
    print(f"{'='*70}")

    # Build a fresh graph and instrument it
    graph_builder = build_graph()

    print("\nInstrumenting graph nodes:")
    telemetry.instrument_graph(graph_builder)

    # Start a new trace
    trace_id = telemetry.new_trace()
    print(f"\nTrace ID: {trace_id}")

    # Compile and run
    app = graph_builder.compile()
    start = time.monotonic()

    try:
        result = app.invoke({
            "query": query,
            "research_output": "",
            "draft_output": "",
            "review_feedback": "",
            "review_passed": False,
            "revision_count": 0,
            "final_output": "",
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "model_name": "gpt-4o",
        })

        elapsed = time.monotonic() - start

        print(f"\n--- Result ({elapsed:.1f}s) ---")
        final = result.get("final_output", "")
        print(final[:500] + ("..." if len(final) > 500 else ""))

        print(f"\n--- Token Usage ---")
        print(f"  Prompt tokens:     {result.get('total_prompt_tokens', 0)}")
        print(f"  Completion tokens: {result.get('total_completion_tokens', 0)}")
        print(f"  Revisions:         {result.get('revision_count', 0)}")
        print(f"  Review passed:     {result.get('review_passed', False)}")

    except Exception as exc:
        elapsed = time.monotonic() - start
        print(f"\n✗ Agent failed after {elapsed:.1f}s: {exc}")

    # Flush spans to OtelMind
    print(f"\nSending telemetry to OtelMind...")
    telemetry.flush()


def main() -> None:
    print("=" * 70)
    print("OtelMind Research Agent — Live Telemetry Demo")
    print("=" * 70)
    print(f"Target: https://lively-yolane-shabeebselfprojects-4bc070a2.koyeb.app")
    print(f"Queries: {len(QUERIES)}")

    telemetry = OtelMindTelemetry(service_name="research-agent")

    for i, query in enumerate(QUERIES, 1):
        run_agent(query, telemetry, i)

    # Final check — hit the dashboard
    print(f"\n{'='*70}")
    print("All runs complete! Checking dashboard...")
    print(f"{'='*70}")

    import httpx

    try:
        resp = httpx.get(
            "https://lively-yolane-shabeebselfprojects-4bc070a2.koyeb.app/api/v1/dashboard/stats",
            timeout=10.0,
        )
        import json
        stats = resp.json()
        print(f"\nDashboard Stats:")
        print(json.dumps(stats, indent=2))
    except Exception as exc:
        print(f"Could not fetch dashboard: {exc}")

    print(f"\nView full dashboard at:")
    print(f"  https://lively-yolane-shabeebselfprojects-4bc070a2.koyeb.app/api/v1/dashboard/stats")
    print(f"  https://lively-yolane-shabeebselfprojects-4bc070a2.koyeb.app/api/v1/traces")
    print(f"  https://lively-yolane-shabeebselfprojects-4bc070a2.koyeb.app/docs")


if __name__ == "__main__":
    main()
