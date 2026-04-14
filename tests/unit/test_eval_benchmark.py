"""Tests for otelmind.eval.benchmark — scenario-based benchmarks."""

from __future__ import annotations

import pytest

from otelmind.eval.benchmark import Benchmark, BenchmarkResults, TraceScenario


def test_add_known_good_and_bad():
    bench = Benchmark()
    bench.add_known_good("happy", {"span_name": "ok"})
    bench.add_known_bad(
        "timeout",
        {"span_name": "slow", "duration_ms": 60_000},
        root_cause="timeout",
        remediation="retry",
    )
    assert len(bench._scenarios) == 2
    assert bench._scenarios[0].expected_failure is False
    assert bench._scenarios[1].expected_failure is True
    assert bench._scenarios[1].expected_remediation == "retry"


def test_add_custom_scenario():
    bench = Benchmark()
    scenario = TraceScenario(name="x", trace={}, expected_failure=True)
    bench.add_scenario(scenario)
    assert bench._scenarios == [scenario]


def test_run_counts_true_positives_and_negatives():
    def analyzer(trace):
        return {"failure_detected": trace.get("is_bad", False)}

    bench = Benchmark(analyzer=analyzer)
    bench.add_known_good("g1", {"is_bad": False})
    bench.add_known_good("g2", {"is_bad": False})
    bench.add_known_bad("b1", {"is_bad": True})
    bench.add_known_bad("b2", {"is_bad": True})

    results = bench.run()
    assert results.total == 4
    assert results.correct == 4
    assert results.false_positives == 0
    assert results.false_negatives == 0
    assert results.accuracy == 1.0


def test_run_counts_false_positive_and_negative():
    def analyzer(trace):
        return {"failure_detected": trace.get("flag", False)}

    bench = Benchmark(analyzer=analyzer)
    bench.add_known_good("fp", {"flag": True})  # should not flag but does
    bench.add_known_bad("fn", {"flag": False})  # should flag but doesn't

    results = bench.run()
    assert results.false_positives == 1
    assert results.false_negatives == 1
    assert results.correct == 0


def test_remediation_counts():
    def analyzer(trace):
        return {"failure_detected": True}

    def remediator(trace, analysis):
        return {"success": trace.get("fixable", False)}

    bench = Benchmark(analyzer=analyzer, remediator=remediator)
    bench.add_known_bad("fixable", {"fixable": True})
    bench.add_known_bad("broken", {"fixable": False})

    results = bench.run()
    assert results.remediation_attempted == 2
    assert results.remediation_succeeded == 1
    assert results.remediation_success_rate == 0.5


def test_empty_benchmark_has_zero_accuracy():
    bench = Benchmark()
    results = bench.run()
    assert results.total == 0
    assert results.accuracy == 0.0
    assert results.failure_rate == 1.0 - 0.0


def test_analyzer_exception_captured():
    def broken(trace):
        raise RuntimeError("analyzer crashed")

    bench = Benchmark(analyzer=broken)
    bench.add_known_good("g", {})
    results = bench.run()
    assert results.per_scenario[0]["error"] == "analyzer crashed"


def test_benchmark_results_failure_rate():
    r = BenchmarkResults(total=10, correct=7)
    assert r.accuracy == 0.7
    assert r.failure_rate == pytest.approx(0.3)
