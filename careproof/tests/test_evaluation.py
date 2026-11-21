import json
from pathlib import Path

import pytest

from careproof.evaluate import evaluate
from stage11_fixtures import cases


@pytest.mark.parametrize("case", list(cases()), ids=lambda case: case["case_id"])
def test_labeled_red_team_case(case):
    report = evaluate([case], evidence_basis="synthetic")
    assert report["cases"][0]["failures"] == []
    assert report["summary"]["unexpected_asserted_positives"] == 0


def test_report_replays():
    path = Path(__file__).resolve().parents[1] / "reports" / "11_evaluation.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    if report["summary"]["evidence_basis"].startswith("Manually labeled synthetic"):
        assert report == evaluate(list(cases()), evidence_basis=report["summary"]["evidence_basis"])


def test_metrics_count_an_asserted_positive_against_negative_label():
    case = next(cases())
    case["expected"] = {"eligible_ids": [], "review_ids": [], "reason": "no_reliable_match"}
    summary = evaluate([case], evidence_basis="synthetic")["summary"]
    assert summary["unexpected_asserted_positives"] == 1
    assert summary["precision_among_recommended"] == 0
    assert summary["failed_cases"] == 1
