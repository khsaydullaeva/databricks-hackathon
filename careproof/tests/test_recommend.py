"""Synthetic Gold-shaped fixtures for the Stage 10 ranker."""

import json
import subprocess
import sys

import pytest

from careproof.recommend import haversine_km, recommend


def gold(facility_id="fixture-a", **changes):
    row = {
        "facility_id": facility_id,
        "geo_facility_id": facility_id,
        "name": "Fixture Hospital",
        "service_key": "dialysis",
        "claim_type": "candidate_claim",
        "evidence_status": "supported_claim",
        "verification_reason": "attributable_dataset_offering_text",
        "identity_review_count": 1,
        "clear_identity_review_count": 1,
        "identity_reason_codes": [],
        "has_service_negation": False,
        "source_field": "capability",
        "claim_text": "Offers  dialysis.",
        "source_text": '["Offers  dialysis."]',
        "source_urls_raw": '["https://example.invalid/fixture"]',
        "source_attribution_status": "unverified_row_leads",
        "city_clean": "delhi",
        "state_clean": "delhi",
        "district_clean": "central delhi",
        "pin_clean": "110001",
        "pin_status": "format_valid_unverified",
        "geo_status": "bbox_pass",
        "admin_status": "dataset_address_unverified",
        "coordinate_provenance": "dataset_coordinates_bbox_only",
        "candidate_latitude": 28.6,
        "candidate_longitude": 77.2,
    }
    return row | changes


CITY = {"service": " Hemodialysis ", "location": "Delhi"}
POINT = {"service": "dialysis", "location": [28.6, 77.2], "radius_km": 20}


def test_ranking_deterministic_with_ties_and_duplicate_rows():
    a, b = gold(), gold("fixture-b")
    weaker_field = gold("fixture-c", source_field="description")
    result = recommend(CITY, [b, weaker_field, a, a])
    assert result == recommend(CITY, [a, weaker_field, b])
    assert result["status"] == "ranked"
    ranked = result["recommendations"]
    assert [entry["facility_id"] for entry in ranked] == ["fixture-a", "fixture-b", "fixture-c"]
    assert ranked[0]["score_components"] == {"evidence": 60, "proximity": 0, "address_consistency": 10}
    assert ranked[0]["score"] == sum(ranked[0]["score_components"].values())
    assert ranked[0]["distance_km"] is None
    assert ranked[0]["citation"]["claim_text"] == "Offers  dialysis."
    assert ranked[0]["citation"]["record_id"] == a["facility_id"]
    assert ranked[0]["citation"]["source_field"] == "capability"
    assert ranked[0]["citation"]["source_attribution_status"] == "unverified_row_leads"


@pytest.mark.parametrize("changes,reason", [
    ({"evidence_status": "conflicting"}, "evidence_conflicting"),
    ({"evidence_status": "uncertain", "claim_type": "mention_only"}, "evidence_uncertain"),
    ({"evidence_status": "rejected", "claim_type": "negated"}, "evidence_rejected"),
    ({"has_service_negation": True}, "offering_not_supported"),
    ({"identity_reason_codes": ["text_mentions_other_facility_or_branch"]}, "facility_attribution_uncertain"),
    ({"verification_reason": "possible_historical_offering"}, "verification_reason_not_supportive"),
    ({"source_text": "Different text."}, "missing_grounded_citation"),
    ({"geo_status": "outside_india_bbox"}, "coordinate_quality_requires_review"),
    ({"admin_status": "possible_admin_conflict"}, "possible_admin_conflict"),
])
def test_unsafe_rows_are_visible_without_rank(changes, reason):
    result = recommend(CITY, [gold(**changes)])
    assert result["status"] == "abstain"
    assert result["reason"] == "no_reliable_match"
    assert result["recommendations"] == []
    review, = result["verification_required"]
    assert reason in review["reason_codes"]
    assert "score" not in review
    if reason == "missing_grounded_citation":
        assert review["citation"] is None


def test_duplicate_conflicts_cannot_be_outvoted():
    a = gold()
    b = gold(evidence_status="rejected", claim_type="negated")
    result = recommend(CITY, [a, a, b])
    assert result == recommend(CITY, [b, a])
    assert result["status"] == "abstain"
    assert result["verification_required"][0]["reason_codes"] == ["inconsistent_gold_rows"]


def test_distance_rank_components_and_radius():
    near = gold(source_field="description")
    mid = gold("fixture-mid", candidate_latitude=28.7)
    far = gold("fixture-far", candidate_latitude=30)
    result = recommend(POINT, [far, mid, near])
    assert result["distance_assumptions"]["method"] == "haversine_straight_line"
    assert result["distance_assumptions"]["radius_km"] == 20
    assert result["excluded_location_counts"] == {"outside_radius": 1}
    first, second = result["recommendations"]
    assert first["facility_id"] == "fixture-a"
    assert first["distance_km"] == 0
    assert first["score_components"]["proximity"] == 30
    assert second["distance_km"] == pytest.approx(11.119508)


def test_haversine_antimeridian():
    assert haversine_km((0, 179.5), (0, -179.5)) == pytest.approx(111.19508)


def test_missing_coords_allow_address_match_without_invented_distance():
    row = gold(candidate_latitude=None, candidate_longitude=None, geo_status="missing_coordinates")
    assert recommend(POINT, [row])["status"] == "abstain"
    result = recommend(CITY, [row])
    assert result["status"] == "ranked"
    assert result["recommendations"][0]["distance_km"] is None


def test_place_ambiguity_checks_other_services():
    rows = [gold(), gold("other", state_clean="Haryana", service_key="mri")]
    assert recommend(CITY, rows)["reason"] == "ambiguous_place"
    assert recommend(CITY | {"location": "Delhi, Delhi"}, rows)["status"] == "ranked"


@pytest.mark.parametrize("location", ["110001", {"district": "Central Delhi"}])
def test_address_modes(location):
    assert recommend(CITY | {"location": location}, [gold()])["status"] == "ranked"


@pytest.mark.parametrize("query,reason", [
    (CITY | {"service": "nephrology"}, "unsupported_service"),
    (CITY | {"location": "12345"}, "invalid_location_or_radius"),
    (POINT | {"location": ["NaN", 77]}, "invalid_location_or_radius"),
    (POINT | {"radius_km": -1}, "invalid_location_or_radius"),
    (CITY | {"radius_km": 10}, "invalid_location_or_radius"),
    (CITY | {"location": "Unknown City"}, "no_reliable_match"),
])
def test_invalid_or_unmatched_queries_abstain(query, reason):
    assert recommend(query, [gold()])["reason"] == reason


def test_json_cli():
    rows = [gold()]
    run = subprocess.run(
        [sys.executable, "-m", "careproof.recommend"],
        input=json.dumps({"query": CITY, "rows": rows}),
        check=True, capture_output=True, text=True,
    )
    assert json.loads(run.stdout) == recommend(CITY, rows)
