"""Statistical utilities for rigorous eval comparisons.

Bootstrap confidence intervals, Cohen's d effect size, significance tests,
and inter-rater agreement — all in pure Python so the dependency footprint
stays small. NumPy can be swapped in for better performance at scale.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from typing import Any


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return 0.5 * (ordered[mid - 1] + ordered[mid])


def _variance(values: list[float], *, sample: bool = True) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean = _mean(values)
    sq = sum((v - mean) ** 2 for v in values)
    return sq / (n - 1) if sample else sq / n


def _std(values: list[float], *, sample: bool = True) -> float:
    return math.sqrt(_variance(values, sample=sample))


def bootstrap_confidence_interval(
    scores: list[float],
    statistic: Callable[[list[float]], float] = _mean,
    n_bootstrap: int = 1000,
    confidence_level: float = 0.95,
    seed: int = 42,
) -> tuple[float, float, float]:
    """Return (point_estimate, lower_bound, upper_bound) for the given statistic.

    Uses percentile bootstrap: resamples with replacement n_bootstrap times,
    recomputes the statistic on each resample, then reports the requested
    central interval.
    """
    if not scores:
        return 0.0, 0.0, 0.0

    point = statistic(list(scores))
    if len(scores) == 1:
        return point, point, point

    rng = random.Random(seed)
    resamples: list[float] = []
    n = len(scores)
    for _ in range(n_bootstrap):
        sample = [scores[rng.randrange(n)] for _ in range(n)]
        resamples.append(statistic(sample))

    resamples.sort()
    alpha = (1.0 - confidence_level) / 2.0
    lo_idx = max(0, int(alpha * n_bootstrap))
    hi_idx = min(n_bootstrap - 1, int((1.0 - alpha) * n_bootstrap))
    return point, resamples[lo_idx], resamples[hi_idx]


def cohens_d(group_a: list[float], group_b: list[float]) -> float:
    """Standardized mean difference between two groups.

    |d| < 0.2 negligible, < 0.5 small, < 0.8 medium, >= 0.8 large.
    Sign matches mean(a) - mean(b): positive = a larger than b.
    """
    if not group_a or not group_b:
        return 0.0
    n1, n2 = len(group_a), len(group_b)
    if n1 < 2 and n2 < 2:
        return 0.0
    v1 = _variance(group_a) if n1 >= 2 else 0.0
    v2 = _variance(group_b) if n2 >= 2 else 0.0
    pooled = ((n1 - 1) * v1 + (n2 - 1) * v2) / max(n1 + n2 - 2, 1)
    if pooled <= 0:
        return 0.0
    return (_mean(group_a) - _mean(group_b)) / math.sqrt(pooled)


def is_regression_significant(
    baseline_scores: list[float],
    candidate_scores: list[float],
    threshold: float = 0.05,
    min_effect_size: float = 0.2,
    n_bootstrap: int = 1000,
    seed: int = 42,
) -> tuple[bool, dict[str, Any]]:
    """Decide if candidate regresses meaningfully vs baseline.

    A regression is significant when ALL of:
      1. mean(baseline) - mean(candidate) > threshold
      2. The 95% bootstrap CI on the (candidate - baseline) differences excludes 0
      3. |Cohen's d| >= min_effect_size and sign indicates candidate is worse
    """
    if not baseline_scores or not candidate_scores:
        return False, {
            "reason": "insufficient_data",
            "baseline_n": len(baseline_scores),
            "candidate_n": len(candidate_scores),
        }

    mean_b = _mean(baseline_scores)
    mean_c = _mean(candidate_scores)
    mean_delta = mean_c - mean_b

    n = min(len(baseline_scores), len(candidate_scores))
    paired_deltas = [candidate_scores[i] - baseline_scores[i] for i in range(n)]
    _, ci_lo, ci_hi = bootstrap_confidence_interval(
        paired_deltas, n_bootstrap=n_bootstrap, seed=seed
    )

    d = cohens_d(candidate_scores, baseline_scores)

    threshold_hit = mean_delta < -threshold
    ci_excludes_zero = ci_hi < 0
    effect_size_hit = d <= -min_effect_size

    significant = threshold_hit and ci_excludes_zero and effect_size_hit

    return significant, {
        "mean_baseline": round(mean_b, 6),
        "mean_candidate": round(mean_c, 6),
        "mean_delta": round(mean_delta, 6),
        "threshold": threshold,
        "threshold_hit": threshold_hit,
        "ci_lower": round(ci_lo, 6),
        "ci_upper": round(ci_hi, 6),
        "ci_excludes_zero": ci_excludes_zero,
        "cohens_d": round(d, 6),
        "min_effect_size": min_effect_size,
        "effect_size_hit": effect_size_hit,
        "n": n,
    }


def _bin_score(score: float, n_bins: int, lo: float, hi: float) -> int:
    if hi <= lo:
        return 0
    ratio = (score - lo) / (hi - lo)
    b = int(ratio * n_bins)
    return max(0, min(n_bins - 1, b))


def cohens_kappa(
    rater_a: list[int],
    rater_b: list[int],
) -> float:
    """Cohen's kappa for two categorical raters with the same integer labels.

    Returns 1.0 for perfect agreement, ~0 for chance, negative for worse than chance.
    """
    if not rater_a or len(rater_a) != len(rater_b):
        return 0.0

    categories = sorted(set(rater_a) | set(rater_b))
    n = len(rater_a)
    if not categories:
        return 0.0

    total_agree = sum(1 for a, b in zip(rater_a, rater_b, strict=True) if a == b)
    p_observed = total_agree / n

    count_a = {c: 0 for c in categories}
    count_b = {c: 0 for c in categories}
    for a, b in zip(rater_a, rater_b, strict=True):
        count_a[a] += 1
        count_b[b] += 1

    p_expected = sum((count_a[c] / n) * (count_b[c] / n) for c in categories)
    if p_expected >= 1.0:
        return 1.0 if p_observed >= 1.0 else 0.0
    return (p_observed - p_expected) / (1.0 - p_expected)


def inter_rater_agreement(
    rater_a: list[float],
    rater_b: list[float],
    n_bins: int = 5,
) -> dict[str, float]:
    """Return kappa, Pearson r, and simple agreement percentage for two raters."""
    if not rater_a or len(rater_a) != len(rater_b):
        return {"cohens_kappa": 0.0, "pearson_r": 0.0, "agreement_pct": 0.0}

    lo = min(min(rater_a), min(rater_b))
    hi = max(max(rater_a), max(rater_b))
    binned_a = [_bin_score(v, n_bins, lo, hi) for v in rater_a]
    binned_b = [_bin_score(v, n_bins, lo, hi) for v in rater_b]

    kappa = cohens_kappa(binned_a, binned_b)

    agreement = sum(1 for a, b in zip(binned_a, binned_b, strict=True) if a == b) / len(binned_a)

    mean_a = _mean(rater_a)
    mean_b = _mean(rater_b)
    num = sum((a - mean_a) * (b - mean_b) for a, b in zip(rater_a, rater_b, strict=True))
    den_a = math.sqrt(sum((a - mean_a) ** 2 for a in rater_a))
    den_b = math.sqrt(sum((b - mean_b) ** 2 for b in rater_b))
    pearson = num / (den_a * den_b) if den_a > 0 and den_b > 0 else 0.0

    return {
        "cohens_kappa": kappa,
        "pearson_r": pearson,
        "agreement_pct": agreement,
    }


def percentile(values: list[float], p: float) -> float:
    """Linear-interpolation percentile (p in [0, 1])."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    k = p * (len(ordered) - 1)
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return ordered[int(k)]
    return ordered[lo] + (k - lo) * (ordered[hi] - ordered[lo])
