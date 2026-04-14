"""Judge calibration against human-labeled gold sets.

Runs the LLM judge on cases for which humans have already scored each
dimension. Reports Cohen's kappa, simple agreement, bias, a confusion
matrix, and a calibration curve (predicted-vs-actual buckets).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from otelmind.eval.judge import LLMJudge
from otelmind.eval.regression import EvalCase
from otelmind.eval.statistics import cohens_kappa


@dataclass
class HumanLabel:
    case_id: str
    dimension: str
    score: float
    annotator_id: str | None = None


@dataclass
class DimensionCalibration:
    cohens_kappa: float
    agreement_rate: float
    mean_absolute_error: float
    bias: float
    n: int


@dataclass
class CalibrationResult:
    cohens_kappa: float
    agreement_rate: float
    confusion_matrix: dict[tuple[int, int], int]
    per_dimension: dict[str, DimensionCalibration]
    bias: float
    calibration_curve: list[dict[str, Any]]
    case_count: int
    judge_model: str
    raw_pairs: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cohens_kappa": round(self.cohens_kappa, 4),
            "agreement_rate": round(self.agreement_rate, 4),
            "bias": round(self.bias, 4),
            "case_count": self.case_count,
            "judge_model": self.judge_model,
            "confusion_matrix": {f"{k[0]}-{k[1]}": v for k, v in self.confusion_matrix.items()},
            "per_dimension": {
                dim: {
                    "cohens_kappa": round(cal.cohens_kappa, 4),
                    "agreement_rate": round(cal.agreement_rate, 4),
                    "mean_absolute_error": round(cal.mean_absolute_error, 4),
                    "bias": round(cal.bias, 4),
                    "n": cal.n,
                }
                for dim, cal in self.per_dimension.items()
            },
            "calibration_curve": self.calibration_curve,
        }


def _bin_1_to_5(score: float) -> int:
    """Bin a 0-1 score into the underlying 1-5 bucket."""
    return max(1, min(5, round(score * 4) + 1))


async def calibrate_judge(
    judge: LLMJudge,
    cases: list[EvalCase],
    human_labels: list[HumanLabel],
    dimensions: list[str] | None = None,
) -> CalibrationResult:
    """Score cases with the judge, compare to human labels, report agreement."""
    from otelmind.internal_tracing import trace_calibration

    with trace_calibration(case_count=len(cases)):
        return await _calibrate_judge_inner(judge, cases, human_labels, dimensions)


async def _calibrate_judge_inner(
    judge: LLMJudge,
    cases: list[EvalCase],
    human_labels: list[HumanLabel],
    dimensions: list[str] | None,
) -> CalibrationResult:
    cases_by_id = {c.id: c for c in cases}

    labels_by_case: dict[str, dict[str, float]] = {}
    for label in human_labels:
        labels_by_case.setdefault(label.case_id, {})[label.dimension] = label.score

    dim_filter = set(dimensions) if dimensions else None

    raw_pairs: list[dict[str, Any]] = []
    all_judge_bins: list[int] = []
    all_human_bins: list[int] = []
    confusion: dict[tuple[int, int], int] = {}
    per_dim_raw: dict[str, list[tuple[float, float]]] = {}

    for case_id, human_scores in labels_by_case.items():
        case = cases_by_id.get(case_id)
        if case is None:
            logger.warning("calibrate_judge: no case for id {}", case_id)
            continue

        try:
            result = await judge.score(
                case.question,
                case.actual,
                case.context,
                list(human_scores.keys()) if not dim_filter else list(dim_filter),
            )
        except Exception as exc:
            logger.warning("calibrate_judge: scoring failed for {}: {}", case_id, exc)
            continue

        for dim, human_score in human_scores.items():
            if dim_filter and dim not in dim_filter:
                continue
            judge_dim = result.scores.get(dim)
            if judge_dim is None:
                continue
            per_dim_raw.setdefault(dim, []).append((judge_dim.score, human_score))
            j_bin = _bin_1_to_5(judge_dim.score)
            h_bin = _bin_1_to_5(human_score)
            all_judge_bins.append(j_bin)
            all_human_bins.append(h_bin)
            key = (j_bin, h_bin)
            confusion[key] = confusion.get(key, 0) + 1
            raw_pairs.append(
                {
                    "case_id": case_id,
                    "dimension": dim,
                    "judge_score": judge_dim.score,
                    "human_score": human_score,
                    "judge_bin": j_bin,
                    "human_bin": h_bin,
                }
            )

    total = len(all_judge_bins)
    if total == 0:
        return CalibrationResult(
            cohens_kappa=0.0,
            agreement_rate=0.0,
            confusion_matrix={},
            per_dimension={},
            bias=0.0,
            calibration_curve=[],
            case_count=0,
            judge_model=getattr(judge, "_model", "unknown"),
            raw_pairs=[],
        )

    kappa = cohens_kappa(all_judge_bins, all_human_bins)
    agreement_rate = (
        sum(1 for a, b in zip(all_judge_bins, all_human_bins, strict=True) if a == b) / total
    )

    judge_mean = sum(p["judge_score"] for p in raw_pairs) / total
    human_mean = sum(p["human_score"] for p in raw_pairs) / total
    bias = judge_mean - human_mean

    per_dimension: dict[str, DimensionCalibration] = {}
    for dim, pairs in per_dim_raw.items():
        j_scores = [p[0] for p in pairs]
        h_scores = [p[1] for p in pairs]
        j_bins = [_bin_1_to_5(s) for s in j_scores]
        h_bins = [_bin_1_to_5(s) for s in h_scores]
        dim_kappa = cohens_kappa(j_bins, h_bins)
        dim_agree = sum(1 for a, b in zip(j_bins, h_bins, strict=True) if a == b) / len(pairs)
        mae = sum(abs(j - h) for j, h in pairs) / len(pairs)
        dim_bias = (sum(j_scores) - sum(h_scores)) / len(pairs)
        per_dimension[dim] = DimensionCalibration(
            cohens_kappa=dim_kappa,
            agreement_rate=dim_agree,
            mean_absolute_error=mae,
            bias=dim_bias,
            n=len(pairs),
        )

    calibration_curve: list[dict[str, Any]] = []
    for bin_id in range(1, 6):
        bucket = [p for p in raw_pairs if p["judge_bin"] == bin_id]
        if not bucket:
            continue
        predicted = sum(p["judge_score"] for p in bucket) / len(bucket)
        actual = sum(p["human_score"] for p in bucket) / len(bucket)
        calibration_curve.append(
            {
                "bin": bin_id,
                "predicted": round(predicted, 4),
                "actual": round(actual, 4),
                "n": len(bucket),
            }
        )

    return CalibrationResult(
        cohens_kappa=kappa,
        agreement_rate=agreement_rate,
        confusion_matrix=confusion,
        per_dimension=per_dimension,
        bias=bias,
        calibration_curve=calibration_curve,
        case_count=len({p["case_id"] for p in raw_pairs}),
        judge_model=getattr(judge, "_model", "unknown"),
        raw_pairs=raw_pairs,
    )
