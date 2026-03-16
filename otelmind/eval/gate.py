"""Quality gate that validates benchmark results against thresholds."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from otelmind.eval.benchmark import BenchmarkResults


@dataclass
class GateThresholds:
    """Configurable pass/fail thresholds for the quality gate."""

    min_accuracy: float = 0.95          # >= 95%
    max_failure_rate: float = 0.05      # <= 5%
    min_remediation_success: float = 0.90  # >= 90%


@dataclass
class GateResult:
    """Detailed result of a quality gate check."""

    passed: bool
    checks: List[Dict[str, object]]

    def summary(self) -> str:
        """Return a human-readable summary of the gate result."""
        status = "PASSED" if self.passed else "FAILED"
        lines = [f"Quality Gate: {status}"]
        for check in self.checks:
            mark = "PASS" if check["passed"] else "FAIL"
            lines.append(
                f"  [{mark}] {check['name']}: "
                f"{check['actual']:.2%} (threshold: {check['threshold']:.2%})"
            )
        return "\n".join(lines)


class QualityGate:
    """Checks benchmark results against quality thresholds.

    Default thresholds:
      - accuracy >= 95%
      - failure rate <= 5%
      - remediation success >= 90%
    """

    def __init__(self, thresholds: GateThresholds | None = None) -> None:
        self.thresholds = thresholds or GateThresholds()

    def check(self, results: BenchmarkResults) -> bool:
        """Return True if all quality thresholds are met, False otherwise."""
        return self.check_detailed(results).passed

    def check_detailed(self, results: BenchmarkResults) -> GateResult:
        """Return a detailed gate result with per-check breakdown."""
        checks: List[Dict[str, object]] = []

        # Accuracy check
        accuracy_ok = results.accuracy >= self.thresholds.min_accuracy
        checks.append({
            "name": "accuracy",
            "passed": accuracy_ok,
            "actual": results.accuracy,
            "threshold": self.thresholds.min_accuracy,
        })

        # Failure rate check
        failure_ok = results.failure_rate <= self.thresholds.max_failure_rate
        checks.append({
            "name": "failure_rate",
            "passed": failure_ok,
            "actual": results.failure_rate,
            "threshold": self.thresholds.max_failure_rate,
        })

        # Remediation success check
        remediation_ok = (
            results.remediation_success_rate
            >= self.thresholds.min_remediation_success
        )
        checks.append({
            "name": "remediation_success",
            "passed": remediation_ok,
            "actual": results.remediation_success_rate,
            "threshold": self.thresholds.min_remediation_success,
        })

        passed = accuracy_ok and failure_ok and remediation_ok
        return GateResult(passed=passed, checks=checks)
