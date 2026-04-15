"""Run the multi-agent group benchmarks against real Claude.

Loads scenarios from `config/eval_datasets/group_scenarios.yaml` and runs
each one through three protocols (round_robin, debate, consensus). Records
results to `config/eval_datasets/benchmark_results/{scenario}_{protocol}.json`.

Run manually with ANTHROPIC_API_KEY set:

    ANTHROPIC_API_KEY=sk-ant-... python scripts/run_benchmarks.py

The committed JSON files are evidence — they let reviewers see real
collaboration metrics without spending API credits themselves.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from otelmind.config import settings  # noqa: E402
from otelmind.eval.group_metrics import evaluate_group  # noqa: E402
from otelmind.eval.judge import LLMJudge  # noqa: E402
from otelmind.multiagent.group import AgentGroup  # noqa: E402
from otelmind.multiagent.protocols import (  # noqa: E402
    ConsensusProtocol,
    DebateProtocol,
    RoundRobinProtocol,
)
from otelmind.multiagent.roles import (  # noqa: E402
    coder_role,
    critic_role,
    planner_role,
    reviewer_role,
)

SCENARIO_PATH = REPO_ROOT / "config" / "eval_datasets" / "group_scenarios.yaml"
RESULTS_DIR = REPO_ROOT / "config" / "eval_datasets" / "benchmark_results"

PROTOCOLS = {
    "round_robin": RoundRobinProtocol,
    "debate": DebateProtocol,
    "consensus": ConsensusProtocol,
}


# Build a small role pool. Each protocol picks a sensible subset.
def _roles_for(protocol: str) -> list:
    if protocol == "debate":
        # DebateProtocol requires exactly 3 agents (debater A, debater B, judge)
        return [coder_role("python"), critic_role(), reviewer_role()]
    if protocol == "consensus":
        return [coder_role("python"), reviewer_role(), critic_role()]
    return [planner_role(), coder_role("python"), reviewer_role()]


async def _run_one(scenario: dict, protocol_key: str) -> dict:
    proto_cls = PROTOCOLS[protocol_key]
    roles = _roles_for(protocol_key)
    group = AgentGroup(
        roles=roles,
        protocol=proto_cls(max_rounds=3),
        max_rounds=3,
        # Cap at $0.50 per scenario × protocol so a buggy run can't drain credits.
        budget_usd=0.50,
    )
    expected = scenario.get("expected_output")
    result = await group.solve(scenario["problem"], context=scenario.get("context", ""))

    # Pass a real OpenAI-judge through so task_completion_score is a real
    # faithfulness score against `expected_output`, not the heuristic 0.5
    # fallback we got on the first sweep (this project stores the key as
    # LLM_API_KEY, not the default OPENAI_API_KEY the judge looks up).
    openai_key = settings.llm.api_key or os.environ.get("OPENAI_API_KEY") or None
    judge = (
        LLMJudge(api_key=openai_key, model=settings.llm.model or "gpt-4o") if openai_key else None
    )
    metrics = await evaluate_group(result, expected_output=expected, judge=judge, max_rounds=3)
    return {
        "scenario_id": scenario["id"],
        "protocol": protocol_key,
        "result": result.to_dict(),
        "metrics": metrics.to_dict(),
    }


async def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        return 2

    with SCENARIO_PATH.open() as fh:
        scenarios = yaml.safe_load(fh)["scenarios"]

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Running {len(scenarios)} scenarios × {len(PROTOCOLS)} protocols")
    for scenario in scenarios:
        for protocol_key in PROTOCOLS:
            out_path = RESULTS_DIR / f"{scenario['id']}_{protocol_key}.json"
            if out_path.exists():
                print(f"  skip (exists): {out_path.name}")
                continue
            try:
                print(f"  running {scenario['id']} via {protocol_key}...")
                payload = await _run_one(scenario, protocol_key)
            except Exception as exc:
                print(f"    FAILED: {exc}", file=sys.stderr)
                payload = {
                    "scenario_id": scenario["id"],
                    "protocol": protocol_key,
                    "error": str(exc),
                }
            with out_path.open("w") as fh:
                json.dump(payload, fh, indent=2, default=str)
            print(f"    wrote {out_path.name}")

    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
