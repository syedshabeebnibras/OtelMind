"""MCP tool: recommend a multi-agent protocol for a new problem.

Thin wrapper around otelmind.eval.protocol_selector.recommend_protocol.
Exposed via the MCP server so Claude can ask for a protocol suggestion
before spawning a group.
"""

from __future__ import annotations

from typing import Any

from otelmind.eval.protocol_selector import recommend_protocol


async def recommend_protocol_tool(
    problem: str,
    top_k: int = 5,
    min_similarity: float = 0.1,
) -> dict[str, Any]:
    """Recommend a multi-agent protocol based on historical benchmark data.

    problem — the task description the group will be asked to solve.
    top_k — how many nearest historical problems to consider (default 5).
    min_similarity — TF-IDF cosine floor; similarities below this are ignored.

    Returns:
      recommended — protocol name (round_robin | debate | consensus |
                    blackboard | delegation). Falls back to round_robin
                    when there's no history.
      reason — human-readable explanation naming the scores that drove
               the choice.
      per_protocol — [{protocol, neighbour_count, success_rate,
                      avg_task_score, avg_cost_usd, combined}] sorted
                      by combined score descending.
      neighbours — the top_k similar historical group_runs with their
                   ids, problems, protocols, statuses, similarities,
                   task scores, and costs — for transparency.
    """
    rec = await recommend_protocol(problem, top_k=top_k, min_similarity=min_similarity)
    return rec.to_dict()
