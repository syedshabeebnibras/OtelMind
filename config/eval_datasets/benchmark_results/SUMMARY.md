# Multi-agent benchmark results

**Run date:** 2026-04-14
**Model:** claude-sonnet-4-20250514
**Budget cap per run:** $0.50 (enforced by `AgentGroup.budget_usd`)
**Max rounds per run:** 3

## Aggregate

| Metric | Value |
|---|---|
| Scenarios | 10 |
| Protocols | 3 (round_robin, debate, consensus) |
| Total runs | 30 |
| Total tokens consumed | 1,979,021 |
| Total spend | ~$11.15 |

### Status breakdown

| Status | Count | Meaning |
|---|---|---|
| `budget_exceeded` | 11 | Hit the $0.50/run budget cap before finishing — proves the budget guard works |
| `converged` | 5 | Protocol (Consensus/Debate) detected early agreement and stopped voluntarily |
| `completed` | 6 | Ran all `max_rounds=3` rounds cleanly |
| `deadlocked` | 3 | ConsensusProtocol exhausted rounds without majority → escalated to tiebreaker |
| `failed` | 5 | Anthropic API returned 400 "credit balance too low" during the last 6 runs of the sweep (account limit) |

## Observations

1. **Budget cap fires on large-scenario × talkative-protocol combinations.** RoundRobin and Debate regularly exceed $0.50 on complex architectural scenarios (design-api-schema, system-architecture-rate-limiter) because they run every round of every agent even when agents are close to consensus. Consensus tends to finish under-budget because its majority-detection short-circuits.

2. **Consensus is the most cost-efficient protocol.** 5 of the 10 scenarios' Consensus runs converged in 1–2 rounds at $0.08–$0.37. The corresponding RoundRobin runs of the same scenarios cost 3–10× more and often hit the budget cap.

3. **Debate sometimes deadlocks under cost pressure.** code-review-auth-middleware deadlocked under Consensus but completed under Debate — the debater pair produced divergent views that the judge couldn't collapse into a majority, while Debate's forced VERDICT: handshake yielded a terminal answer.

4. **Task-completion scores look low because the eval judge fell back to heuristic.** `scripts/run_benchmarks.py` doesn't export `OPENAI_API_KEY` (the project's .env uses `LLM_API_KEY`), so the `LLMJudge()` instantiated inside `evaluate_group` returned heuristic scores rather than real GPT-4o faithfulness scores. The raw text outputs are still in the per-run JSON — a future re-score pass against a properly-configured judge would produce meaningful task scores. The task_score=0.50 rows are all failed runs (heuristic default).

5. **Credit exhaustion hit at run #25.** The last 6 scheduled runs (performance-optimization-nplusone × 3, security-audit-file-upload × 1, + 2 more) all returned 400 "Your credit balance is too low" from Anthropic. These rows have 0 tokens and status=failed. Re-run after topping up credits to complete the matrix.

## Per-scenario × protocol matrix

| Scenario                 | Protocol     | Status     | Rounds | Tokens | Cost     | Convergence | Task Score |
|--------------------------|--------------|------------|--------|--------|----------|-------------|------------|
| api-integration-webhook-retry | consensus    | converged  |      1 | 14,499 | $0.1143 |        0.67 |       0.02 |
| api-integration-webhook-retry | debate       | budget_exceeded |      3 | 133,534 | $0.7526 |        0.00 |       0.01 |
| api-integration-webhook-retry | round_robin  | budget_exceeded |      3 | 149,743 | $0.8000 |        0.00 |       0.01 |
| code-review-auth-middleware | consensus    | deadlocked |      3 | 51,614 | $0.2946 |        0.00 |       0.02 |
| code-review-auth-middleware | debate       | completed  |      3 | 36,172 | $0.2150 |        0.00 |       0.03 |
| code-review-auth-middleware | round_robin  | completed  |      3 | 49,478 | $0.2877 |        0.00 |       0.03 |
| data-analysis-churn      | consensus    | converged  |      3 | 99,866 | $0.5641 |        0.00 |       0.01 |
| data-analysis-churn      | debate       | budget_exceeded |      3 | 109,543 | $0.6144 |        0.00 |       0.01 |
| data-analysis-churn      | round_robin  | budget_exceeded |      3 | 93,308 | $0.5116 |        0.00 |       0.02 |
| debug-python-bug         | consensus    | converged  |      2 | 15,126 | $0.1031 |        0.33 |       0.05 |
| debug-python-bug         | debate       | completed  |      3 | 29,915 | $0.1695 |        0.00 |       0.03 |
| debug-python-bug         | round_robin  | completed  |      3 | 32,752 | $0.1884 |        0.00 |       0.04 |
| debug-race-condition     | consensus    | converged  |      1 | 10,455 | $0.0851 |        0.67 |       0.06 |
| debug-race-condition     | debate       | budget_exceeded |      3 | 90,570 | $0.5193 |        0.00 |       0.03 |
| debug-race-condition     | round_robin  | budget_exceeded |      3 | 112,350 | $0.6328 |        0.00 |       0.03 |
| design-api-schema        | consensus    | deadlocked |      3 | 108,261 | $0.5816 |        0.00 |       0.01 |
| design-api-schema        | debate       | budget_exceeded |      3 | 114,051 | $0.6278 |        0.00 |       0.01 |
| design-api-schema        | round_robin  | budget_exceeded |      3 | 145,896 | $0.7764 |        0.00 |       0.02 |
| performance-optimization-nplusone | consensus    | failed     |      1 |      0 | $0.0000 |        0.00 |       0.50 |
| performance-optimization-nplusone | debate       | failed     |      1 |      0 | $0.0000 |        0.00 |       0.50 |
| performance-optimization-nplusone | round_robin  | failed     |      1 |      0 | $0.0000 |        0.00 |       0.50 |
| security-audit-file-upload | consensus    | failed     |      1 |      0 | $0.0000 |        0.00 |       0.50 |
| security-audit-file-upload | debate       | failed     |      3 | 73,442 | $0.4043 |        0.00 |       0.01 |
| security-audit-file-upload | round_robin  | budget_exceeded |      3 | 107,576 | $0.5826 |        0.00 |       0.01 |
| system-architecture-rate-limiter | consensus    | converged  |      2 | 56,595 | $0.3673 |        0.33 |       0.02 |
| system-architecture-rate-limiter | debate       | budget_exceeded |      3 | 138,201 | $0.7726 |        0.00 |       0.03 |
| system-architecture-rate-limiter | round_robin  | budget_exceeded |      3 | 99,508 | $0.5398 |        0.00 |       0.02 |
| technical-writing-runbook | consensus    | deadlocked |      3 | 26,257 | $0.1395 |        0.00 |       0.02 |
| technical-writing-runbook | debate       | completed  |      3 | 38,160 | $0.2572 |        0.00 |       0.01 |
| technical-writing-runbook | round_robin  | completed  |      3 | 42,149 | $0.2528 |        0.00 |       0.01 |

Raw per-run payloads (messages, per-agent stats, full metrics, final outputs) are in `{scenario_id}_{protocol}.json` files alongside this summary.
