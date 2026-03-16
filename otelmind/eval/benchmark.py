"""Evaluation benchmark framework for OtelMind trace analysis."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TraceScenario:
    """A single test scenario with a trace and expected outcome."""

    name: str
    trace: dict[str, Any]
    expected_failure: bool
    expected_root_cause: str | None = None
    expected_remediation: str | None = None


@dataclass
class BenchmarkResults:
    """Aggregated results from a benchmark run."""

    total: int = 0
    correct: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    remediation_attempted: int = 0
    remediation_succeeded: int = 0
    duration_seconds: float = 0.0
    per_scenario: list[dict[str, Any]] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        """Detection accuracy as a fraction (0.0 - 1.0)."""
        return self.correct / self.total if self.total > 0 else 0.0

    @property
    def false_positive_rate(self) -> float:
        """False positive rate as a fraction (0.0 - 1.0)."""
        total_negatives = self.total - (self.correct - self.false_positives + self.false_negatives)
        if total_negatives <= 0:
            return 0.0
        return self.false_positives / (
            self.false_positives + (self.total - self.false_positives - self.false_negatives)
        )

    @property
    def failure_rate(self) -> float:
        """Failure rate (incorrect detections) as a fraction."""
        return 1.0 - self.accuracy

    @property
    def remediation_success_rate(self) -> float:
        """Remediation success rate as a fraction (0.0 - 1.0)."""
        if self.remediation_attempted == 0:
            return 0.0
        return self.remediation_succeeded / self.remediation_attempted


class Benchmark:
    """Runs evaluation benchmarks against traces to measure analysis quality.

    Measures accuracy, false positive rate, and remediation success rate
    by running known-good and known-bad trace scenarios through the
    analysis pipeline.
    """

    def __init__(
        self,
        analyzer: Callable | None = None,
        remediator: Callable | None = None,
    ) -> None:
        self._scenarios: list[TraceScenario] = []
        self._analyzer = analyzer
        self._remediator = remediator

    def add_scenario(self, scenario: TraceScenario) -> None:
        """Register a test scenario."""
        self._scenarios.append(scenario)

    def add_known_good(self, name: str, trace: dict[str, Any]) -> None:
        """Add a known-good trace (should not be flagged as a failure)."""
        self._scenarios.append(TraceScenario(name=name, trace=trace, expected_failure=False))

    def add_known_bad(
        self,
        name: str,
        trace: dict[str, Any],
        root_cause: str | None = None,
        remediation: str | None = None,
    ) -> None:
        """Add a known-bad trace (should be flagged as a failure)."""
        self._scenarios.append(
            TraceScenario(
                name=name,
                trace=trace,
                expected_failure=True,
                expected_root_cause=root_cause,
                expected_remediation=remediation,
            )
        )

    def run(self) -> BenchmarkResults:
        """Execute all registered scenarios and return aggregated results."""
        results = BenchmarkResults(total=len(self._scenarios))
        start = time.monotonic()

        for scenario in self._scenarios:
            outcome = self._evaluate_scenario(scenario)
            results.per_scenario.append(outcome)

            if outcome["correct"]:
                results.correct += 1
            if outcome["false_positive"]:
                results.false_positives += 1
            if outcome["false_negative"]:
                results.false_negatives += 1
            if outcome["remediation_attempted"]:
                results.remediation_attempted += 1
            if outcome["remediation_succeeded"]:
                results.remediation_succeeded += 1

        results.duration_seconds = time.monotonic() - start
        return results

    def _evaluate_scenario(self, scenario: TraceScenario) -> dict[str, Any]:
        """Evaluate a single scenario against the analyzer and remediator."""
        outcome: dict[str, Any] = {
            "name": scenario.name,
            "expected_failure": scenario.expected_failure,
            "detected_failure": False,
            "correct": False,
            "false_positive": False,
            "false_negative": False,
            "remediation_attempted": False,
            "remediation_succeeded": False,
            "error": None,
        }

        try:
            if self._analyzer is not None:
                analysis = self._analyzer(scenario.trace)
                detected = bool(analysis.get("failure_detected", False))
            else:
                detected = False

            outcome["detected_failure"] = detected
            outcome["correct"] = detected == scenario.expected_failure
            outcome["false_positive"] = detected and not scenario.expected_failure
            outcome["false_negative"] = not detected and scenario.expected_failure

            if detected and self._remediator is not None:
                outcome["remediation_attempted"] = True
                rem_result = self._remediator(scenario.trace, analysis)
                outcome["remediation_succeeded"] = bool(rem_result.get("success", False))
        except Exception as exc:
            outcome["error"] = str(exc)

        return outcome
