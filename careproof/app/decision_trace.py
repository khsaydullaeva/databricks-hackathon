"""Summarize the executed search without changing eligibility or ordering."""
from collections import Counter


def build_trace(query, addresses, gold_rows, result):
    ranked = result["recommendations"]
    review = result["verification_required"]
    assessment_fields = ("claim_type", "claim_reason", "has_service_negation",
                         "identity_review_count", "clear_identity_review_count",
                         "identity_reason_codes", "verification_reason")
    by_id = {}
    for row in gold_rows:
        facility_id = row["facility_id"]
        by_id[facility_id] = row if facility_id not in by_id else None
    return {
        "request": {"location": query["location"], "service_key": result["service_key"]},
        "geography": {
            "assumptions": result["distance_assumptions"],
            "choices": result.get("location_choices", []),
            "coordinate_exclusions": result.get("excluded_location_counts", {}),
        },
        "retrieval": {"address_rows": len(addresses), "gold_rows": len(gold_rows)},
        "evidence": {
            "retrieved_states": dict(Counter(row.get("evidence_status") or "unknown" for row in gold_rows)),
            "review_reasons": dict(Counter(reason for row in review for reason in row["reason_codes"])),
            "review_leads": review,
        },
        "assessment": {
            candidate["facility_id"]: {key: row.get(key) for key in assessment_fields}
            for candidate in [*ranked, *review]
            if (row := by_id.get(candidate["facility_id"])) is not None
        },
        "scoring": result.get("scoring"),
        "ranking": [
            {key: candidate[key] for key in ("facility_id", "name", "citation", "score",
                                            "score_components", "distance_km", "geo_status", "resolved_address")}
            for candidate in ranked
        ],
        "decision": {"status": result["status"], "reason": result["reason"],
                     "ranked": len(ranked), "review": len(review), "detail": result.get("detail")},
    }
