"""Live smoke test for otelmind.multiagent against a real Claude API.

Runs a small 2-agent RoundRobin group so you can confirm the Anthropic
wiring, retry logic, token accounting, and evaluation metrics all work
end-to-end. Reads the API key from the ANTHROPIC_API_KEY environment
variable — never hard-code a key here.

Usage:
    ANTHROPIC_API_KEY=sk-ant-... python scripts/smoke_multiagent.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

# Make `otelmind` importable when run as a plain script
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from otelmind.eval.group_metrics import evaluate_group
from otelmind.multiagent.group import AgentGroup
from otelmind.multiagent.protocols import RoundRobinProtocol
from otelmind.multiagent.roles import coder_role, reviewer_role


async def main() -> int:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY is not set", file=sys.stderr)
        return 2

    roles = [coder_role("python"), reviewer_role()]
    protocol = RoundRobinProtocol(max_rounds=1)
    group = AgentGroup(roles=roles, protocol=protocol, api_key=api_key, max_rounds=1)

    problem = (
        "Write a one-line Python function that returns the nth Fibonacci number using "
        "memoization. Keep it under 80 characters."
    )

    print("→ Sending problem to group:", problem)
    result = await group.solve(problem)
    metrics = await evaluate_group(result, max_rounds=1)

    print("\n=== RESULT ===")
    print("status         :", result.status)
    print("rounds         :", result.rounds_completed)
    print("total tokens   :", result.total_tokens)
    print("final output   :", (result.final_output or "")[:400])

    print("\n=== MESSAGES ===")
    for m in result.messages:
        usage = m.token_usage or {}
        print(
            f"  [r{m.round_number} {m.sender_role:<9}] "
            f"tokens={usage.get('total_tokens', 0):>5}  {m.content[:120]}..."
        )

    print("\n=== METRICS ===")
    print(json.dumps(metrics.to_dict(), indent=2))

    ok = (
        result.status in {"completed", "converged"}
        and result.rounds_completed >= 1
        and result.total_tokens > 0
        and result.final_output
    )
    print("\n" + ("PASS — live multi-agent pipeline works" if ok else "FAIL — see above"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
