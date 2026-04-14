"""Tests for otelmind.eval.statistics — pure-Python bootstrap, Cohen's d, etc."""

from __future__ import annotations

import random

import pytest

from otelmind.eval.statistics import (
    bootstrap_confidence_interval,
    cohens_d,
    cohens_kappa,
    inter_rater_agreement,
    is_regression_significant,
    percentile,
)


def test_bootstrap_ci_contains_point_estimate():
    values = [0.5, 0.6, 0.55, 0.48, 0.62, 0.51, 0.58, 0.53, 0.57, 0.54]
    point, lo, hi = bootstrap_confidence_interval(values, n_bootstrap=500, seed=1)
    assert lo <= point <= hi
    assert 0.4 < point < 0.7


def test_bootstrap_ci_empty_returns_zeros():
    assert bootstrap_confidence_interval([]) == (0.0, 0.0, 0.0)


def test_bootstrap_ci_single_value_is_degenerate():
    assert bootstrap_confidence_interval([0.42]) == (0.42, 0.42, 0.42)


def test_bootstrap_ci_contains_true_mean_for_normal_distribution():
    rng = random.Random(7)
    sample = [rng.gauss(0.75, 0.05) for _ in range(120)]
    _, lo, hi = bootstrap_confidence_interval(sample, n_bootstrap=600, seed=2)
    assert lo <= 0.75 <= hi


def test_cohens_d_known_values():
    big = cohens_d([1.0, 1.1, 0.9, 1.05, 0.95], [0.0, 0.1, -0.1, 0.05, -0.05])
    assert big > 5.0
    identical = cohens_d([0.5] * 10, [0.5] * 10)
    assert identical == 0.0
    small = cohens_d([0.5, 0.51, 0.49, 0.50, 0.52], [0.48, 0.50, 0.49, 0.51, 0.47])
    assert abs(small) < 1.5


def test_cohens_d_empty_groups():
    assert cohens_d([], [1.0, 2.0]) == 0.0
    assert cohens_d([1.0], []) == 0.0


def test_is_regression_significant_clear_regression():
    baseline = [0.90, 0.92, 0.88, 0.91, 0.89, 0.93, 0.87, 0.90, 0.92, 0.88]
    candidate = [0.70, 0.72, 0.68, 0.71, 0.69, 0.73, 0.67, 0.70, 0.72, 0.68]
    sig, details = is_regression_significant(baseline, candidate, n_bootstrap=500)
    assert sig is True
    assert details["threshold_hit"]
    assert details["effect_size_hit"]
    assert details["ci_excludes_zero"]
    assert details["mean_delta"] < -0.1


def test_is_regression_significant_no_regression():
    baseline = [0.8, 0.82, 0.81, 0.79, 0.80]
    candidate = [0.81, 0.80, 0.82, 0.79, 0.81]
    sig, details = is_regression_significant(baseline, candidate, n_bootstrap=300)
    assert sig is False
    assert not details["threshold_hit"]


def test_is_regression_significant_improvement_is_not_regression():
    baseline = [0.5, 0.52, 0.48, 0.51, 0.49]
    candidate = [0.80, 0.82, 0.78, 0.81, 0.79]
    sig, details = is_regression_significant(baseline, candidate, n_bootstrap=300)
    assert sig is False
    assert details["mean_delta"] > 0


def test_is_regression_significant_insufficient_data():
    sig, details = is_regression_significant([], [])
    assert sig is False
    assert details["reason"] == "insufficient_data"


def test_cohens_kappa_perfect_agreement():
    assert cohens_kappa([1, 2, 3, 4, 5], [1, 2, 3, 4, 5]) == 1.0


def test_cohens_kappa_random_near_zero():
    rng = random.Random(13)
    n = 200
    a = [rng.randint(1, 5) for _ in range(n)]
    b = [rng.randint(1, 5) for _ in range(n)]
    k = cohens_kappa(a, b)
    assert abs(k) < 0.2


def test_cohens_kappa_mismatched_length_returns_zero():
    assert cohens_kappa([1, 2], [1, 2, 3]) == 0.0
    assert cohens_kappa([], []) == 0.0


def test_inter_rater_agreement_basic():
    out = inter_rater_agreement([0.1, 0.5, 0.9, 0.3, 0.7], [0.2, 0.45, 0.85, 0.35, 0.65])
    assert 0.0 <= out["agreement_pct"] <= 1.0
    assert -1.0 <= out["cohens_kappa"] <= 1.0
    assert -1.0 <= out["pearson_r"] <= 1.0


def test_percentile_basic():
    vals = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert percentile(vals, 0.0) == 1
    assert percentile(vals, 1.0) == 10
    assert abs(percentile(vals, 0.5) - 5.5) < 1e-9


def test_percentile_empty():
    assert percentile([], 0.5) == 0.0


@pytest.mark.parametrize(
    "values,p,expected",
    [
        ([1.0], 0.5, 1.0),
        ([1.0, 2.0], 0.0, 1.0),
        ([1.0, 2.0], 1.0, 2.0),
    ],
)
def test_percentile_edge_cases(values, p, expected):
    assert percentile(values, p) == expected
