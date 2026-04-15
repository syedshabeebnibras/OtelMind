"""Single-agent baseline for multi-agent benchmarks.

For each scenario in `config/eval_datasets/group_scenarios.yaml`, send the
SAME problem to a SINGLE Claude call with the same model and a comparable
token budget. Score the output against `expected_output` with the LLM
judge (faithfulness dimension) and persist per-scenario JSON next to the
group benchmarks.

Once both sweeps exist, `scripts/summarize_benchmarks.py` adds a
"Single-agent task score" column so you can tell at a glance whether
multi-agent coordination actually outperforms a single call, or just
costs 3–10× more for a similar answer.

Usage:
    ANTHROPIC_API_KEY=... OPENAI_API_KEY=... python scripts/run_single_agent_baseline.py
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
from otelmind.eval.judge import LLMJudge  # noqa: E402

SCENARIO_PATH = REPO_ROOT / "config" / "eval_datasets" / "group_scenarios.yaml"
RESULTS_DIR = REPO_ROOT / "config" / "eval_datasets" / "benchmark_results"

# Sized to roughly match the per-scenario token budget used by the group
# runs (max_rounds=3 × 3 agents ≈ 9 calls, so a single call with ~12k
# output tokens has comparable wall-clock spend). Adjust if your model
# caps differ.
SINGLE_MAX_TOKENS = 4096


async def _call_claude_once(
    problem: str, context: str, api_key: str, model: str
) -> tuple[str, dict[str, int]]:
    """One Claude call, return (text, usage)."""
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=api_key, timeout=120.0)
    system = (
        "You are a senior engineer solving a technical problem end-to-end. "
        "Think carefully; be concise; produce the final answer only (no internal monologue)."
    )
    user = f"Problem:\n{problem}"
    if context:
        user += f"\n\nContext:\n{context}"

    resp = await client.messages.create(
        model=model,
        max_tokens=SINGLE_MAX_TOKENS,
        temperature=0.5,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(getattr(b, "text", "") for b in (getattr(resp, "content", []) or [])).strip()
    usage_raw = getattr(resp, "usage", None)
    usage = {
        "prompt_tokens": getattr(usage_raw, "input_tokens", 0) if usage_raw else 0,
        "completion_tokens": getattr(usage_raw, "output_tokens", 0) if usage_raw else 0,
    }
    usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
    return text, usage


def _estimate_cost(usage: dict[str, int]) -> float:
    # Claude Sonnet 4 approximate pricing: $3/M input, $15/M output.
    p, c = usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
    return (p * 3.0 + c * 15.0) / 1e6


async def _score_against_expected(
    judge: LLMJudge | None, problem: str, answer: str, expected: str
) -> dict[str, float | str]:
    if judge is None or not expected:
        return {
            "task_completion_score": 0.5,
            "method": "heuristic (no judge / no expected_output)",
        }
    try:
        result = await judge.score(
            question=problem,
            answer=answer,
            context=expected,
            dimensions=["faithfulness"],
        )
        faith = result.scores.get("faithfulness")
        if faith is None:
            return {"task_completion_score": 0.5, "method": "heuristic (missing dim)"}
        return {
            "task_completion_score": float(faith.score),
            "raw_score": faith.raw_score,
            "method": faith.method,
            "reason": faith.reason,
        }
    except Exception as exc:
        return {"task_completion_score": 0.5, "method": f"error: {exc}"}


async def _run_one(scenario: dict, api_key: str, model: str, judge: LLMJudge | None) -> dict:
    sid = scenario["id"]
    out_path = RESULTS_DIR / f"{sid}_single_agent.json"
    if out_path.exists():
        return {"scenario_id": sid, "status": "skipped (exists)"}

    try:
        text, usage = await _call_claude_once(
            scenario["problem"], scenario.get("context", ""), api_key, model
        )
    except Exception as exc:
        payload = {
            "scenario_id": sid,
            "protocol": "single_agent",
            "error": str(exc)[:500],
        }
        with out_path.open("w") as fh:
            json.dump(payload, fh, indent=2, default=str)
        return {"scenario_id": sid, "status": f"FAILED: {exc!s:.80s}"}

    score = await _score_against_expected(
        judge, scenario["problem"], text, scenario.get("expected_output") or ""
    )
    payload = {
        "scenario_id": sid,
        "protocol": "single_agent",
        "model": model,
        "output": text,
        "usage": usage,
        "cost_usd": round(_estimate_cost(usage), 6),
        "score": score,
        "expected_output": scenario.get("expected_output"),
    }
    with out_path.open("w") as fh:
        json.dump(payload, fh, indent=2, default=str)
    return {
        "scenario_id": sid,
        "status": (
            f"ok ({usage['total_tokens']:,} tok, ${payload['cost_usd']:.4f}, "
            f"score={score['task_completion_score']:.2f})"
        ),
    }


async def main() -> int:
    anthropic_key = settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY") or ""
    if not anthropic_key:
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        return 2

    model = settings.multiagent_default_model or "claude-sonnet-4-20250514"

    openai_key = settings.llm.api_key or os.environ.get("OPENAI_API_KEY") or ""
    judge = (
        LLMJudge(api_key=openai_key, model=settings.llm.model or "gpt-4o") if openai_key else None
    )
    if judge is None:
        print("WARN: no OPENAI_API_KEY / LLM_API_KEY — task scores will be heuristic 0.5")

    with SCENARIO_PATH.open() as fh:
        scenarios = yaml.safe_load(fh)["scenarios"]

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Single-agent baseline across {len(scenarios)} scenarios with model={model}")

    for scenario in scenarios:
        row = await _run_one(scenario, anthropic_key, model, judge)
        print(f"  {row['scenario_id']:<34} {row['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
