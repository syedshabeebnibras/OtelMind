"""Sanity tests for the shipped human-label calibration gold set."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

DATASET_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "eval_datasets" / "human_labels.yaml"
)


@pytest.fixture(scope="module")
def cases() -> list[dict]:
    with DATASET_PATH.open() as fh:
        data = yaml.safe_load(fh)
    return data["cases"]


def test_dataset_has_25_cases(cases):
    assert len(cases) == 25


def test_every_case_has_required_fields(cases):
    required = {"id", "question", "expected", "actual", "context", "human_scores"}
    for case in cases:
        missing = required - set(case)
        assert not missing, f"case {case.get('id')} missing {missing}"


def test_ids_are_unique(cases):
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids))


def test_human_scores_are_in_1_to_5_range(cases):
    for case in cases:
        for dim, score in case["human_scores"].items():
            assert 1 <= score <= 5, f"{case['id']}.{dim} = {score} out of range"


def test_distribution_covers_strong_failure_borderline(cases):
    """The dataset should include cases where faithfulness spans the full 1-5 range."""
    faithfulness_scores = [c["human_scores"]["faithfulness"] for c in cases]
    assert min(faithfulness_scores) == 1, "no clear-failure faithfulness cases"
    assert max(faithfulness_scores) == 5, "no perfect-faithfulness cases"
    # At least 3 cases in each broad bucket (low/mid/high)
    low = sum(1 for s in faithfulness_scores if s <= 2)
    mid = sum(1 for s in faithfulness_scores if s == 3)
    high = sum(1 for s in faithfulness_scores if s >= 4)
    assert low >= 3, f"too few low-faithfulness cases: {low}"
    assert mid >= 3, f"too few mid-faithfulness cases: {mid}"
    assert high >= 3, f"too few high-faithfulness cases: {high}"
