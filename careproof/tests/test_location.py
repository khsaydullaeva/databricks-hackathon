"""Location regressions at the public recommendation entry point."""

import pytest

from careproof.recommend import recommend
from test_recommend import gold


@pytest.mark.parametrize("city,state,reported_city,reported_state", [
    ("Jaipur", "Rajasthan", "JAIPUR", "rajasthan"),
    ("Delhi", "NCT of Delhi", "New Delhi", "Delhi"),
    ("Delhi", "NCT of Delhi", "Delhi", "West Delhi"),
    ("New Delhi", "Delhi", "New Delhi", "N.C.T. of Delhi"),
    ("Lucknow", "Uttar Pradesh", "Lucknow", "U.p"),
    ("Patna", "Bihar", "Patna", "Br"),
    ("Mumbai", "Maharashtra", "Mumbai", "Mumbai"),
])
def test_city_state_keeps_address_and_citation(city, state, reported_city, reported_state):
    row = gold(city_clean=reported_city, state_clean=reported_state, district_clean=None, pin_clean=None)
    result = recommend({"service": "dialysis", "location": f"{city}, {state}"}, [
        row, gold("another-city", city_clean="Faridabad", state_clean=state),
        gold("another-state", city_clean=city, state_clean="West Bengal"),
    ])
    ranked, = result["recommendations"]
    assert ranked["facility_id"] == ranked["citation"]["record_id"] == row["facility_id"]
    assert ranked["city"] == reported_city
    assert ranked["state"] == reported_state
    assert ranked["distance_km"] is None


def test_new_delhi_city_search_keeps_narrow_scope():
    result = recommend({"service": "dialysis", "location": "New Delhi, Delhi"}, [
        gold(), gold("new-delhi", city_clean="new delhi"),
    ])
    assert [row["facility_id"] for row in result["recommendations"]] == ["new-delhi"]


@pytest.mark.parametrize("location", [{"city": "Rampur"}, {"city": "Rampur", "state": "Bihar"}])
def test_same_name_places_return_reusable_choices(location):
    rows = [
        gold("east", city_clean="Rampur", state_clean="Bihar", district_clean="East"),
        gold("west", city_clean="Rampur", state_clean="Bihar", district_clean="West"),
        gold("other", city_clean="Rampur", state_clean="Uttar Pradesh", district_clean="North"),
    ]
    query = {"service": "dialysis", "location": location}
    result = recommend(query, rows)
    assert result["reason"] == "ambiguous_place"
    assert not result["recommendations"]
    assert len(result["location_choices"]) == (2 if "state" in location else 3)
    selected = recommend(query | {"location": result["location_choices"][0]}, rows)
    assert [row["facility_id"] for row in selected["recommendations"]] == ["east"]


@pytest.mark.parametrize("location", [{"district": "Patna", "state": "Bihar"}, "800020"])
def test_postal_offices_do_not_multiply_facilities(location):
    row = gold(city_clean="Patna", state_clean="Br", district_clean=None, pin_clean="800020")
    office = {"pin": "800020", "state": "Bihar", "district": "Patna"}
    query = {"service": "dialysis", "location": location}
    result = recommend(query, [row, row], postal_offices=[
        office, office | {"office": "Different office"}, office | {"state": "BR"},
    ])
    ranked, = result["recommendations"]
    assert ranked["facility_id"] == row["facility_id"]
    assert ranked["district"] is None
    assert ranked["resolved_address"]["district"] == "patna"
    assert ranked["resolved_address"]["district_provenance"] == "postal_pin_district"
    assert ranked["distance_km"] is None


def test_postal_disagreement_is_retained_and_blocks_ranking():
    row = gold(city_clean="Patna", state_clean="Bihar", pin_clean="800020", district_clean=None)
    query = {"service": "dialysis", "location": "800020"}
    offices = [{"pin": "800020", "state": "Bihar", "district": district}
               for district in ("Patna", "Gaya")]
    result = recommend(query, [row], postal_offices=offices)
    assert result["reason"] == "ambiguous_place"
    assert len(result["location_choices"]) == 2
    result = recommend(query | {"location": "Patna, Bihar"}, [row], postal_offices=offices)
    assert result["reason"] == "no_reliable_match"
    assert "postal_admin_ambiguous" in result["verification_required"][0]["reason_codes"]


@pytest.mark.parametrize("city,state,reported_state,pin,issue", [
    ("Lucknow", "Uttar Pradesh", "U.p", "275101", "city_pin_conflict"),
    ("Lucknow", "Uttar Pradesh", "Gomti Nagar", "226001", "locality_pin_conflict"),
    ("Mumbai", "Maharashtra", "Palghar", "401201", "city_district_conflict"),
    ("Delhi", "Haryana", "Haryana", "110007", "postal_admin_conflict"),
])
def test_audited_geography_conflicts_cannot_rank(city, state, reported_state, pin, issue):
    row = gold(city_clean=city, state_clean=reported_state, district_clean=None, pin_clean=pin)
    result = recommend({"service": "dialysis", "location": {"city": city, "state": state}}, [row])
    assert result["reason"] == "no_reliable_match"
    review, = result["verification_required"]
    assert issue in review["reason_codes"]
    assert review["facility_id"] == row["facility_id"]


@pytest.mark.parametrize("location", ["012345", "1234567", {"pin": "12a456"}, [91, 77], [28, "Inf"]])
def test_invalid_pin_and_explicit_coordinates(location):
    assert recommend({"service": "dialysis", "location": location}, [gold()])["reason"] == "invalid_location_or_radius"


@pytest.mark.parametrize("latitude,longitude,status", [
    (None, None, "missing_coordinates"), (100, 77, "bbox_pass"), (28, 190, "bbox_pass"),
])
def test_missing_and_invalid_facility_coordinates(latitude, longitude, status):
    row = gold(candidate_latitude=latitude, candidate_longitude=longitude, geo_status=status)
    result = recommend({"service": "dialysis", "location": [28.6, 77.2]}, [row])
    assert result["reason"] == "no_reliable_match"
    result = recommend({"service": "dialysis", "location": "Delhi, Delhi"}, [row])
    if status == "missing_coordinates":
        assert result["recommendations"][0]["distance_km"] is None
    else:
        assert result["reason"] == "no_reliable_match"
        assert result["verification_required"][0]["distance_km"] is None


def test_city_only_delhi_can_resolve_from_nct_new_delhi_addresses():
    result = recommend({"service": "dialysis", "location": "Delhi"}, [
        gold("new-delhi", city_clean="New Delhi", state_clean="Delhi"),
    ])
    assert [row["facility_id"] for row in result["recommendations"]] == ["new-delhi"]
