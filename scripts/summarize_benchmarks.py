"""Read benchmark JSON results and print a markdown table summary.

Run after `scripts/run_benchmarks.py` has populated
`config/eval_datasets/benchmark_results/`. Output is markdown so you can
paste it into a PR description, README, or release notes.

    python scripts/summarize_benchmarks.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = REPO_ROOT / "config" / "eval_datasets" / "benchmark_results"


def _row(payload: dict) -> str:
    if "error" in payload:
        return (
            f"| {payload.get('scenario_id', '?'):<24} "
            f"| {payload.get('protocol', '?'):<12} "
            f"| ERROR | – | – | – | – | – |"
        )
    result = payload.get("result", {})
    metrics = payload.get("metrics", {})
    cost = metrics.get("total_cost_usd", 0.0) or 0.0
    return (
        f"| {payload.get('scenario_id', '?'):<24} "
        f"| {payload.get('protocol', '?'):<12} "
        f"| {result.get('status', '?'):<10} "
        f"| {result.get('rounds_completed', 0):>6} "
        f"| {result.get('total_tokens', 0):>6,} "
        f"| ${cost:0.4f} "
        f"| {metrics.get('convergence_rate', 0.0):>11.2f} "
        f"| {metrics.get('task_completion_score', 0.0):>10.2f} |"
    )


def main() -> int:
    if not RESULTS_DIR.exists():
        print(f"No results directory at {RESULTS_DIR}", file=sys.stderr)
        return 1
    files = sorted(RESULTS_DIR.glob("*.json"))
    if not files:
        print(f"No benchmark JSON files in {RESULTS_DIR}", file=sys.stderr)
        return 1

    print("# Multi-agent benchmark results\n")
    print(
        "| Scenario                 | Protocol     | Status     "
        "| Rounds | Tokens | Cost     | Convergence | Task Score |"
    )
    print(
        "|--------------------------|--------------|------------"
        "|--------|--------|----------|-------------|------------|"
    )
    for path in files:
        try:
            with path.open() as fh:
                payload = json.load(fh)
        except Exception as exc:
            print(f"  (skipped {path.name}: {exc})", file=sys.stderr)
            continue
        print(_row(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
