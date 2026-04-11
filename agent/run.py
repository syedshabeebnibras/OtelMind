#!/usr/bin/env python3
"""Runner script — executes the research agent with OtelMind instrumentation.

Sends live telemetry data to the deployed OtelMind backend. Defaults to
the Railway production URL; override with `OTELMIND_BASE_URL` to point at
a staging instance or your local dev server.
"""

from __future__ import annotations

import os
import sys
import time

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

OTELMIND_BASE_URL = os.environ.get(
    "OTELMIND_BASE_URL",
    "https://otelmind-api-production.up.railway.app",
)

# Verify OpenAI key is set — use LLM_API_KEY as fallback
openai_key = (os.getenv("OPENAI_API_KEY") or "").strip()
llm_key = (os.getenv("LLM_API_KEY") or "").strip()

if openai_key:
    os.environ["OPENAI_API_KEY"] = openai_key
elif llm_key:
    os.environ["OPENAI_API_KEY"] = llm_key
else:
    print("ERROR: Set OPENAI_API_KEY or LLM_API_KEY in .env")
    sys.exit(1)

from agent.graph import build_graph  # noqa: E402  (must follow sys.path setup)
from agent.telemetry import OtelMindTelemetry  # noqa: E402

# ── Sample queries ──────────────────────────────────────────────────────

QUERIES = [
    "Explain how photosynthesis works",
    "Summarize the history of the internet",
    "What is machine learning and how does it differ from traditional programming?",
    "How do vaccines work to protect against diseases?",
    "What causes earthquakes and how are they measured?",
    "Explain the basics of supply and demand in economics",
    "How does encryption keep data secure on the internet?",
    "What is climate change and what are its main causes?",
    "Describe how a CPU processes instructions",
    "What are the key differences between SQL and NoSQL databases?",
    "How does the human immune system fight infections?",
    "Explain the theory of relativity in simple terms",
    "What is blockchain technology and how does it work?",
    "How do electric vehicles differ from traditional combustion engine cars?",
    "What is CRISPR and how is it used in gene editing?",
    "Explain how containerization works in software development",
    "What are black holes and how do they form?",
    "How does natural language processing work in AI?",
    "What is the water cycle and why is it important?",
    "Explain the basics of how the stock market works",
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
        result = app.invoke(
            {
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
            }
        )

        elapsed = time.monotonic() - start

        print(f"\n--- Result ({elapsed:.1f}s) ---")
        final = result.get("final_output", "")
        print(final[:500] + ("..." if len(final) > 500 else ""))

        print("\n--- Token Usage ---")
        print(f"  Prompt tokens:     {result.get('total_prompt_tokens', 0)}")
        print(f"  Completion tokens: {result.get('total_completion_tokens', 0)}")
        print(f"  Revisions:         {result.get('revision_count', 0)}")
        print(f"  Review passed:     {result.get('review_passed', False)}")

    except Exception as exc:
        elapsed = time.monotonic() - start
        print(f"\n✗ Agent failed after {elapsed:.1f}s: {exc}")

    # Flush spans to OtelMind
    print("\nSending telemetry to OtelMind...")
    telemetry.flush()


def main() -> None:
    print("=" * 70)
    print("OtelMind Research Agent — Live Telemetry Demo")
    print("=" * 70)
    print(f"Target: {OTELMIND_BASE_URL}")
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
            f"{OTELMIND_BASE_URL}/api/v1/dashboard/stats",
            timeout=10.0,
        )
        import json

        stats = resp.json()
        print("\nDashboard Stats:")
        print(json.dumps(stats, indent=2))
    except Exception as exc:
        print(f"Could not fetch dashboard: {exc}")

    print("\nView full dashboard at:")
    print("  https://otelmind-dashboard.vercel.app/traces")
    print("  https://otelmind-dashboard.vercel.app/evals")
    print(f"  {OTELMIND_BASE_URL}/docs")


if __name__ == "__main__":
    main()
