"""Explain recorded ranker decisions without changing them."""
from collections import Counter


def facility_checks(candidate, assessment):
    """Report checks for a surfaced candidate, using its executed screening."""
    if assessment is None:
        return [
            ("Matched service claim", "NOT EVALUATED"),
            ("Offering cue", "NOT EVALUATED"),
            ("No explicit denial", "NOT EVALUATED"),
            ("No other-facility reference flagged", "NOT EVALUATED"),
            ("Dataset identity review", "NOT EVALUATED"),
            ("Geographic quality", "NOT EVALUATED"),
            ("Distance computed", "NOT EVALUATED"),
            ("Eligible for ranking", "FAIL"),
        ]
    codes = assessment["identity_reason_codes"]
    count = assessment["identity_review_count"]
    clear = assessment["clear_identity_review_count"]
    denial = assessment["has_service_negation"]
    claim_type = assessment["claim_type"]
    return [
        ("Matched service claim", "PASS" if candidate.get("citation") else "UNCERTAIN"),
        ("Offering cue", "PASS" if claim_type == "candidate_claim" else
         "FAIL" if claim_type in {"mention_only", "negated"} else "UNCERTAIN"),
        ("No explicit denial", "FAIL" if denial is True else "PASS" if denial is False else "UNCERTAIN"),
        ("No other-facility reference flagged", "UNCERTAIN" if codes is None or not count else
         "FAIL" if "text_mentions_other_facility_or_branch" in codes else "PASS"),
        ("Dataset identity review", "PASS" if count == clear == 1 and codes == [] else
         "UNCERTAIN" if not count or clear is None or codes is None else "FAIL"),
        ("Geographic quality", "FAIL" if assessment["location_issue"] else "PASS"),
        ("Distance computed", "PASS" if candidate.get("distance_km") is not None else "NOT EVALUATED"),
        ("Eligible for ranking", "PASS" if "score" in candidate else "FAIL"),
    ]


def counterfactual(candidate, assumptions):
    reasons = candidate.get("reason_codes", [])
    notes = []
    if "facility_attribution_uncertain" in reasons:
        notes.append("Confirm the named branch and check whether references to other facilities belong to a separate record.")
    if "offering_not_supported" in reasons or "missing_grounded_citation" in reasons:
        notes.append("Find branch-specific evidence that explicitly says the service is offered and resolve any denial.")
    if any(reason in reasons for reason in ("unresolved_geography", "possible_admin_conflict",
                                             "coordinate_quality_requires_review", "city_pin_conflict",
                                             "locality_pin_conflict", "city_district_conflict", "postal_admin_conflict")):
        notes.append("Confirm the recorded branch address and correct any geographic discrepancy.")
    if "inconsistent_gold_rows" in reasons:
        notes.append("Reconcile the competing Gold rows for this facility and service.")
    if "score" in candidate:
        notes.append("Confirm current service and intake directly with the branch. Dataset support cannot establish availability.")
    else:
        notes.append("Any corrected record must still pass every eligibility check before it can be ranked.")
    if assumptions and assumptions["method"] == "dataset_address_exact_match":
        notes.append("This search uses recorded addresses. Comparing nearness requires a search origin and validated facility coordinates.")
    return notes


def equal_weight_sensitivity(ranked, scoring):
    """Rescore only already eligible rows. Neither eligibility nor production order is edited."""
    if not scoring:
        return []
    weights = scoring["evidence_points_by_source_field"]
    equal_weight = (weights["capability"] + weights["procedure"]) / 2
    scenarios = []
    for candidate in ranked:
        points = candidate["score_components"]
        field = candidate["citation"]["source_field"]
        evidence = equal_weight if field in {"capability", "procedure"} else points["evidence"]
        scenarios.append({"facility": candidate["name"], "facility_id": candidate["facility_id"],
                          "production_points": candidate["score"],
                          "equal_weight_points": round(evidence + points["proximity"] + points["address_consistency"], 6)})
    return sorted(scenarios, key=lambda row: (-row["equal_weight_points"], row["facility_id"]))


def build_trace(query, addresses, gold_rows, result, audit=None):
    ranked = result["recommendations"]
    review = result["verification_required"]
    audit = audit or {}
    assessments = audit.get("screenings", {})
    funnel = {key: value for key, value in audit.items()
              if key not in {"screenings", "parsed_location", "location", "stop_reason"}}
    if funnel:
        for check, label in (("identity", "Dataset identity review"), ("offering", "Offering cue")):
            counts = Counter(dict(facility_checks(candidate, assessments[candidate["facility_id"]]))[label]
                             for candidate in ranked + review if candidate["facility_id"] in assessments)
            funnel[check] = dict(counts)
    return {
        "request": {"location": query["location"], "service_key": result["service_key"]},
        "geography": {
            "assumptions": result["distance_assumptions"],
            "choices": result.get("location_choices", []),
            "coordinate_exclusions": result.get("excluded_location_counts", {}),
            "normalized": audit.get("location") or audit.get("parsed_location"),
            "stop_reason": audit.get("stop_reason"),
        },
        "funnel": funnel,
        "retrieval": {"address_rows": len(addresses), "gold_rows": len(gold_rows)},
        "evidence": {
            "retrieved_states": dict(Counter(row.get("evidence_status") or "unknown" for row in gold_rows)),
            "review_reasons": dict(Counter(reason for row in review for reason in row["reason_codes"])),
            "review_leads": review,
        },
        "assessment": assessments,
        "scoring": result.get("scoring"),
        "ranking": ranked,
        "decision": {"status": result["status"], "reason": result["reason"],
                     "ranked": len(ranked), "review": len(review), "detail": result.get("detail")},
    }
