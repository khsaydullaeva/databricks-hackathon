"""Manually labeled synthetic scenarios. All IDs and quotations are fictional."""

from test_recommend import gold


SERVICES = (
    ("dialysis", "hemodialysis", "hemodialysis", "nephrology", "Delhi", "Delhi", "110001"),
    ("mri", "magnetic resonance imaging", "MRI", "imaging", "Mumbai", "Maharashtra", "400001"),
    ("nicu", "neonatal intensive care", "neonatal intensive care", "maternity care",
     "Chennai", "Tamil Nadu", "600001"),
    ("emergency_c_section", "emergency caesarean", "emergency caesarean delivery", "obstetrics",
     "Kolkata", "West Bengal", "700001"),
    ("cardiac_surgery", "open heart surgery", "open heart surgery", "cardiology",
     "Bengaluru", "Karnataka", "560001"),
    ("trauma_emergency_care", "trauma care", "trauma care", "first aid",
     "Hyderabad", "Telangana", "500001"),
)


def cases():
    for key, alias, service, broad, city, state, pin in SERVICES:
        query = {"service": alias, "location": {"city": city, "state": state}}
        facility = f"fixture-{key}"
        claim = f"This facility offers {service}."
        base = gold(facility, service_key=key, claim_text=claim, source_text=claim,
                    city_clean=city, state_clean=state, pin_clean=pin,
                    geo_status="missing_coordinates", candidate_latitude=None,
                    candidate_longitude=None)

        def case(suffix, label, rows, rationale, *, search=None, eligible=(), review=(),
                 reason="no_reliable_match"):
            return {
                "case_id": f"{key}-{suffix}",
                "label": label,
                "query": query if search is None else search,
                "rows": rows,
                "expected": {"eligible_ids": list(eligible), "review_ids": list(review),
                             "reason": reason},
                "label_rationale": rationale,
            }

        yield case("offering", "positive", [base], "Direct offering text and matching address.",
                   eligible=[facility], reason=None)
        negation = f"This facility does not offer {service}."
        yield case("negation", "negative", [
            base | {"claim_text": negation, "source_text": negation, "has_service_negation": True,
                    "claim_type": "negated", "evidence_status": "rejected",
                    "verification_reason": "explicit_service_negation"}
        ], "Explicit service denial blocks ranking.", review=[facility])
        third_party = f"Patients are referred to a different hospital for {service}."
        yield case("branch", "conflict", [
            base | {"claim_text": third_party, "source_text": third_party,
                    "evidence_status": "conflicting",
                    "identity_reason_codes": ["text_mentions_other_facility_or_branch"]}
        ], "The service is attributed to another facility.", review=[facility])
        historical = f"This facility previously offered {service}."
        yield case("historical", "unknown", [
            base | {"claim_text": historical, "source_text": historical,
                    "evidence_status": "uncertain", "verification_reason": "possible_historical_offering"}
        ], "Historical wording and an attached URL do not establish a current offering.",
                   review=[facility])
        yield case("broad-alias", "negative", [base],
                   "The related specialty or service does not identify the requested canonical service.",
                   search=query | {"service": broad}, reason="unsupported_service")
        yield case("no-match", "unknown", [base], "The facility address does not match the search.",
                   search=query | {"location": {"city": "Fixture Unmatched City"}})

        if key == "dialysis":
            yield case("pin-fanout", "positive", [base, base],
                       "Duplicate input rows must produce one candidate for the PIN.",
                       search=query | {"location": pin}, eligible=[facility], reason=None)
            yield case("missing-coordinates", "unknown", [base],
                       "A radius search requires coordinates for each candidate.",
                       search=query | {"location": [28.6, 77.2]})
            yield case("wrong-city", "conflict", [
                base | {"geo_status": "bbox_pass", "candidate_latitude": 19.08,
                        "candidate_longitude": 72.88, "admin_status": "possible_admin_conflict"}
            ], "Flagged administrative conflict blocks ranking despite in-bounds coordinates.",
                       review=[facility])
            same_name = base | {"facility_id": "fixture-other-state", "state_clean": "Uttar Pradesh"}
            yield case("same-name", "conflict", [base, same_name],
                       "An unqualified place matching two states requires disambiguation.",
                       search=query | {"location": city}, reason="ambiguous_place")
