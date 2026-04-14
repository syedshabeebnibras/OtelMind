"""Tests for otelmind.eval.worker — internal helpers only, no DB required."""

from __future__ import annotations

from otelmind.eval.worker import _extract_cases_from_details


def test_extract_cases_empty_details():
    baseline, candidate = _extract_cases_from_details(None)
    assert baseline == []
    assert candidate == []


def test_extract_cases_missing_keys():
    baseline, candidate = _extract_cases_from_details({"unrelated": True})
    assert baseline == []
    assert candidate == []


def test_extract_cases_parses_complete_shape():
    details = {
        "baseline_cases": [
            {"id": "c1", "question": "q1", "expected": "e1", "actual": "a1", "context": "ctx"},
        ],
        "candidate_cases": [
            {"id": "c1", "question": "q1", "expected": "e1", "actual": "a2"},
        ],
    }
    baseline, candidate = _extract_cases_from_details(details)
    assert len(baseline) == 1
    assert len(candidate) == 1
    assert baseline[0].id == "c1"
    assert baseline[0].context == "ctx"
    assert candidate[0].actual == "a2"


def test_extract_cases_coerces_types():
    details = {
        "baseline_cases": [{"id": 42, "question": None, "tags": ("t1", "t2")}],
        "candidate_cases": [],
    }
    baseline, _ = _extract_cases_from_details(details)
    assert baseline[0].id == "42"
    assert baseline[0].question == "None"
    assert baseline[0].tags == ["t1", "t2"]


def test_extract_cases_handles_partial_fields():
    details = {"baseline_cases": [{"id": "c1"}], "candidate_cases": [{"id": "c1"}]}
    baseline, candidate = _extract_cases_from_details(details)
    assert baseline[0].question == ""
    assert candidate[0].actual == ""
