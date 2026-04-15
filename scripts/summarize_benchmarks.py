"""Read benchmark JSON results and print a markdown table summary.

Now includes a "Single-agent" column when `{scenario_id}_single_agent.json`
exists alongside the group JSONs (written by scripts/run_single_agent_baseline.py).
That lets reviewers see at a glance whether multi-agent groups actually
beat a single Claude call — the experimental control.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = REPO_ROOT / "config" / "eval_datasets" / "benchmark_results"


def _load_single_scores() -> dict[str, dict]:
    """Load {scenario_id: payload} for every `*_single_agent.json`."""
    out: dict[str, dict] = {}
    for p in RESULTS_DIR.glob("*_single_agent.json"):
        try:
            with p.open() as fh:
                out[p.stem.replace("_single_agent", "")] = json.load(fh)
        except Exception:
            continue
    return out


def _row(payload: dict, single: dict | None) -> str:
    if "error" in payload:
        return (
            f"| {payload.get('scenario_id', '?'):<24} "
            f"| {payload.get('protocol', '?'):<12} "
            f"| ERROR | – | – | – | – | – | – |"
        )
    result = payload.get("result", {})
    metrics = payload.get("metrics", {})
    cost = metrics.get("total_cost_usd", 0.0) or 0.0
    group_score = metrics.get("task_completion_score", 0.0) or 0.0

    if single and "score" in single:
        s_score = float(single["score"].get("task_completion_score", 0.0) or 0.0)
        single_cell = f"{s_score:.2f}"
    else:
        single_cell = "—"

    return (
        f"| {payload.get('scenario_id', '?'):<24} "
        f"| {payload.get('protocol', '?'):<12} "
        f"| {result.get('status', '?'):<10} "
        f"| {result.get('rounds_completed', 0):>6} "
        f"| {result.get('total_tokens', 0):>6,} "
        f"| ${cost:0.4f} "
        f"| {metrics.get('convergence_rate', 0.0):>11.2f} "
        f"| {group_score:>10.2f} "
        f"| {single_cell:>10} |"
    )


def main() -> int:
    if not RESULTS_DIR.exists():
        print(f"No results directory at {RESULTS_DIR}", file=sys.stderr)
        return 1
    group_files = sorted(p for p in RESULTS_DIR.glob("*.json") if "_single_agent" not in p.stem)
    if not group_files:
        print(f"No benchmark JSON files in {RESULTS_DIR}", file=sys.stderr)
        return 1

    singles = _load_single_scores()

    print("# Multi-agent benchmark results\n")
    if singles:
        print(
            "(Single-agent column shows the baseline task score from "
            "`scripts/run_single_agent_baseline.py` — comparable to the "
            "group `task_completion_score` column.)\n"
        )

    print(
        "| Scenario                 | Protocol     | Status     "
        "| Rounds | Tokens | Cost     | Convergence | Group Score | Single    |"
    )
    print(
        "|--------------------------|--------------|------------"
        "|--------|--------|----------|-------------|-------------|-----------|"
    )
    for path in group_files:
        try:
            with path.open() as fh:
                payload = json.load(fh)
        except Exception as exc:
            print(f"  (skipped {path.name}: {exc})", file=sys.stderr)
            continue
        sid = payload.get("scenario_id", "?")
        print(_row(payload, singles.get(sid)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
