"""Summarize the executed search without changing eligibility or ordering."""
from collections import Counter


def build_trace(query, addresses, gold_rows, result):
    ranked = result["recommendations"]
    review = result["verification_required"]
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
        "ranking": [
            {key: candidate[key] for key in ("facility_id", "name", "citation", "score",
                                            "score_components", "distance_km", "geo_status", "resolved_address")}
            for candidate in ranked
        ],
        "decision": {"status": result["status"], "reason": result["reason"],
                     "ranked": len(ranked), "review": len(review), "detail": result.get("detail")},
    }
