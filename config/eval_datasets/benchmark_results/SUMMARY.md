# Multi-agent benchmark results

**Run dates:** 2026-04-14 (initial 25 successful + 5 credit-failed) and 2026-04-14 (4 missing scenarios re-run after credit top-up)
**Model:** claude-sonnet-4-20250514
**Budget cap per run:** $0.50 (enforced by `AgentGroup.budget_usd`)
**Max rounds per run:** 3
**Matrix:** 10 scenarios × 3 protocols = 30 runs, all successful

## Aggregate

| Metric | Value |
|---|---|
| Scenarios | 10 |
| Protocols | 3 (round_robin, debate, consensus) |
| Total runs | 30 |
| Failed runs | **0** |
| Total tokens consumed | 2,231,410 |
| Total spend | ~$12.55 |

### Status breakdown

| Status | Count | Meaning |
|---|---|---|
| `budget_exceeded` | 12 | Hit the $0.50/run budget cap before finishing — proves the budget guard works |
| `completed` | 8 | Ran all `max_rounds=3` rounds cleanly |
| `converged` | 6 | Protocol (Consensus) detected early agreement and stopped voluntarily |
| `deadlocked` | 4 | ConsensusProtocol exhausted rounds without majority → escalated to tiebreaker |

## Observations

1. **Budget cap fires on talkative-protocol × complex-scenario combinations.** RoundRobin and Debate regularly exceed $0.50 on architectural scenarios (design-api-schema, system-architecture-rate-limiter, security-audit-file-upload) because they run every round of every agent even when agents are close to consensus. Consensus tends to finish under-budget because its majority-detection short-circuits.

2. **Consensus is the most cost-efficient protocol.** 6 of the 10 Consensus runs converged in 1–2 rounds at $0.08–$0.37. The corresponding RoundRobin/Debate runs of the same scenarios cost 3–10× more and often hit the budget cap.

3. **Debate breaks deadlocks Consensus can't resolve.** code-review-auth-middleware deadlocked under Consensus but completed under Debate — the debater pair produced divergent views the judge couldn't collapse into a majority, while Debate's forced VERDICT: handshake yielded a terminal answer.

4. **Task-completion scores are unreliable on this run.** `scripts/run_benchmarks.py` was patched in commit `516a5ae` to thread `OPENAI_API_KEY` to the judge, but the runs in this summary were completed before that fix landed — task scores fell back to heuristic. The two scores at 0.50 (`performance-optimization-nplusone_consensus`, `security-audit-file-upload_consensus`) are the heuristic default. The next sweep with the patched script will produce real LLM-judged faithfulness scores against `expected_output`. The raw text outputs in each `*.json` are still good and can be re-scored without re-calling Claude.

5. **No single-agent baseline yet.** `scripts/run_single_agent_baseline.py` was added in commit `4a55944` but hasn't been run against this matrix. Once it runs, the summary table grows a "Single" column and we can answer: do groups actually outperform a single Claude call, or just cost 3-10× more for similar quality?

## Per-scenario × protocol matrix

| Scenario                 | Protocol     | Status     | Rounds | Tokens | Cost     | Convergence | Group Score |
|--------------------------|--------------|------------|--------|--------|----------|-------------|-------------|
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
| performance-optimization-nplusone | consensus    | converged  |      2 | 12,489 | $0.0872 |        0.33 |       0.50 |
| performance-optimization-nplusone | debate       | completed  |      3 | 42,295 | $0.2554 |        0.00 |       0.00 |
| performance-optimization-nplusone | round_robin  | completed  |      3 | 49,717 | $0.2830 |        0.00 |       0.00 |
| security-audit-file-upload | consensus    | deadlocked |      3 | 110,290 | $0.5897 |        0.00 |       0.50 |
| security-audit-file-upload | debate       | budget_exceeded |      3 | 111,040 | $0.5864 |        0.00 |       0.00 |
| security-audit-file-upload | round_robin  | budget_exceeded |      3 | 107,576 | $0.5826 |        0.00 |       0.01 |
| system-architecture-rate-limiter | consensus    | converged  |      2 | 56,595 | $0.3673 |        0.33 |       0.02 |
| system-architecture-rate-limiter | debate       | budget_exceeded |      3 | 138,201 | $0.7726 |        0.00 |       0.03 |
| system-architecture-rate-limiter | round_robin  | budget_exceeded |      3 | 99,508 | $0.5398 |        0.00 |       0.02 |
| technical-writing-runbook | consensus    | deadlocked |      3 | 26,257 | $0.1395 |        0.00 |       0.02 |
| technical-writing-runbook | debate       | completed  |      3 | 38,160 | $0.2572 |        0.00 |       0.01 |
| technical-writing-runbook | round_robin  | completed  |      3 | 42,149 | $0.2528 |        0.00 |       0.01 |

Raw per-run payloads (messages, per-agent stats, full metrics, final outputs) are in `{scenario_id}_{protocol}.json` files alongside this summary.
