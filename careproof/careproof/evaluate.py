"""Run a labeled evaluation against the deterministic CareProof ranker."""

import argparse
from collections import Counter
import json
from pathlib import Path

from careproof.recommend import recommend


def evaluate(cases, *, evidence_basis):
    if not cases or len({case["case_id"] for case in cases}) != len(cases):
        raise ValueError("Provide a nonempty case set with distinct IDs.")
    results = []
    total_ranked = true_positives = unsafe = 0
    failures = Counter()
    for case in cases:
        expected = case["expected"]
        rows = case["rows"]
        result = recommend(case["query"], rows)
        ranked = [row["facility_id"] for row in result["recommendations"]]
        review = [row["facility_id"] for row in result["verification_required"]]
        eligible = set(expected["eligible_ids"])
        unexpected = set(ranked) - eligible
        problems = []
        if unexpected:
            problems.append("unexpected_recommendation")
        if eligible - set(ranked):
            problems.append("missed_eligible_candidate")
        if result["reason"] != expected["reason"]:
            problems.append("unexpected_search_reason")
        if set(review) != set(expected["review_ids"]):
            problems.append("unexpected_review_candidates")
        if len(ranked) != len(set(ranked)):
            problems.append("duplicate_recommendation")
        for candidate in result["recommendations"]:
            citation = candidate["citation"]
            if not citation or not any(
                citation["record_id"] == row["facility_id"] == candidate["facility_id"]
                and citation["source_field"] == row["source_field"]
                and citation["claim_text"] == row["claim_text"]
                and citation["claim_text"] in (row["source_text"] or "")
                for row in rows
            ):
                problems.append("ungrounded_citation")
                break
        if result["evidence_scope"] != (
            "Dataset reports only. Confirm service and location directly with the facility."
        ):
            problems.append("unexpected_evidence_scope")
        failures.update(problems)
        total_ranked += len(ranked)
        true_positives += sum(facility in eligible for facility in ranked)
        unsafe += len(unexpected)
        results.append({
            "case_id": case["case_id"],
            "label": case["label"],
            "query": case["query"],
            "expected": expected,
            "observed": result,
            "evidence": [
                {key: row.get(key) for key in (
                    "facility_id", "service_key", "source_field", "claim_text",
                    "source_text", "source_content_id", "evidence_status",
                    "verification_reason", "identity_reason_codes", "geo_status",
                    "admin_status", "city_clean", "state_clean", "pin_clean",
                )}
                for row in rows
            ],
            "label_rationale": case["label_rationale"],
            "manual_source_review": case.get("manual_source_review", False),
            "failures": problems,
        })
    ranked_cases = sum(case["observed"]["status"] == "ranked" for case in results)
    return {
        "cases": results,
        "summary": {
            "evidence_basis": evidence_basis,
            "case_count": len(results),
            "manual_source_review_count": sum(case["manual_source_review"] for case in results),
            "labels": dict(Counter(case["label"] for case in results)),
            "cases_with_recommendations": ranked_cases,
            "recommended_candidate_count": total_ranked,
            "precision_among_recommended": true_positives / total_ranked if total_ranked else None,
            "coverage": ranked_cases / len(results),
            "abstention_rate": (len(results) - ranked_cases) / len(results),
            "unexpected_asserted_positives": unsafe,
            "failed_cases": sum(bool(case["failures"]) for case in results),
            "failure_categories": dict(sorted(failures.items())),
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="JSON with evidence_basis and labeled cases")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    report = evaluate(payload["cases"], evidence_basis=payload["evidence_basis"])
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                           encoding="utf-8")
    return int(bool(report["summary"]["failed_cases"]))


if __name__ == "__main__":
    raise SystemExit(main())
