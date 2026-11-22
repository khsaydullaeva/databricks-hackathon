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


@pytest.mark.parametrize("location", [
    {"city": "Nagercoil", "state": "Kanyakumari District"},
    {"pin": "629001", "state": "Kanyakumari District"},
    "Nagercoil, Kanyakumari District",
    {"city": "Pune", "state": "Pune District"},
])
def test_district_or_city_cannot_be_entered_as_state(location):
    result = recommend({"service": "dialysis", "location": location}, [gold()])
    assert result["reason"] == "invalid_location_or_radius"
    assert "state or union territory" in result["detail"]


def test_recorded_district_cannot_poison_cross_service_region_choices():
    rows = [gold(city_clean="Nagercoil", state_clean="Tamil Nadu", district_clean=None),
            {"city_clean": "Nagercoil", "state_clean": "Kanyakumari District"},
            gold("other", service_key="mri", city_clean="Nagercoil", state_clean="Kanyakumari District")]
    query = {"service": "dialysis", "location": {"city": "Nagercoil"}}
    audit = {}
    result = recommend(query, rows, audit=audit)
    assert audit["parsed_location"]["state"] == ""
    assert audit["location"]["state"] == "tamil nadu"
    assert [row["facility_id"] for row in result["recommendations"]] == [rows[0]["facility_id"]]


def test_admin_conflict_cannot_introduce_another_state_option():
    rows = [gold(city_clean="Rampur", state_clean="Uttar Pradesh"),
            {"city_clean": "Rampur", "state_clean": "Bihar", "admin_status": "possible_admin_conflict"}]
    result = recommend({"service": "dialysis", "location": {"city": "Rampur"}}, rows)
    assert result["status"] == "ranked"
    assert result["recommendations"][0]["resolved_address"]["state"] == "uttar pradesh"


@pytest.mark.parametrize("location", [
    {"city": "Rampur"}, {"city": "Rampur", "state": "Bihar"},
    {"city": "Rampur", "district": "East District"},
    {"city": "Rampur", "state": "Bihar", "district": "District of East"},
    {"city": "Rampur", "district": "East Dist."},
    {"district": "District of East", "state": "Bihar"},
])
def test_recorded_district_and_suffix_variants_match(location):
    row = gold(city_clean="Rampur", state_clean="Bihar", district_clean="East District")
    result = recommend({"service": "dialysis", "location": location}, [row])
    assert result["status"] == "ranked"
    if "district" in location:
        assert result["district_validation"] == {
            "district": "east", "status": "matched", "matched_facilities": 1, "unresolved_facilities": 0}


def test_missing_district_stays_unresolved_without_dropping_city_or_state_constraint():
    matched = gold("city-match", city_clean="Rampur", state_clean="Bihar", district_clean=None)
    elsewhere = gold("elsewhere", city_clean="Other City", state_clean="Bihar", district_clean=None)
    audit = {}
    for state in ({}, {"state": "Bihar"}):
        result = recommend({"service": "dialysis", "location": {
            "city": "Rampur", "district": "Not Indexed District", **state}},
            [matched, elsewhere], audit=audit)
        assert result["reason"] == "district_membership_unresolved"
        assert result["recommendations"] == []
        assert audit["geographic_facilities"] == 0
        lead, = result["verification_required"]
        assert lead["facility_id"] == "city-match"
        assert lead["reason_codes"] == ["district_membership_unresolved"]


def test_district_reference_rejects_contradictory_state_without_inferring_facility_membership():
    row = gold(city_clean="Rampur", state_clean="Bihar", district_clean=None)
    reference = {"East District": ["Bihar"]}
    query = {"service": "dialysis", "location": {"city": "Rampur", "district": "East", "state": "Haryana"}}
    invalid = recommend(query, [row], district_regions=reference)
    assert invalid["reason"] == "invalid_state_district_combination"
    assert invalid["district_validation"]["status"] == "invalid_combination"
    query["location"]["state"] = "Bihar"
    unresolved = recommend(query, [row], district_regions=reference)
    assert unresolved["reason"] == "district_membership_unresolved"


def test_exact_postal_district_can_match_but_recorded_conflict_remains_for_review():
    row = gold(city_clean="Rampur", state_clean="Bihar", district_clean=None, pin_clean="800020")
    post = [{"pin": "800020", "state": "Bihar", "district": "East District"}]
    query = {"service": "dialysis", "location": {"city": "Rampur", "state": "Bihar", "district": "East"}}
    assert recommend(query, [row], postal_offices=post)["district_validation"]["status"] == "matched"
    conflicted = recommend(query, [row | {"admin_status": "possible_admin_conflict"}], postal_offices=post)
    assert not conflicted["recommendations"]
    lead, = conflicted["verification_required"]
    assert {"district_membership_unresolved", "possible_admin_conflict"} <= set(lead["reason_codes"])


def test_unknown_district_does_not_broaden_to_a_different_recorded_district():
    row = gold(city_clean="Rampur", state_clean="Bihar", district_clean="East")
    result = recommend({"service": "dialysis", "location": {"city": "Rampur", "district": "West"}}, [row])
    assert result["reason"] == "district_membership_unresolved"
    assert not result["recommendations"] and not result["verification_required"]


def test_authoritative_reference_blocks_conflicting_recorded_state_without_user_state():
    row = gold(city_clean="Rampur", state_clean="Haryana", district_clean="East")
    result = recommend({"service": "dialysis", "location": {"city": "Rampur", "district": "East"}},
                       [row], district_regions={"East": ["Bihar"]})
    assert not result["recommendations"]
    assert result["district_validation"]["status"] == "unresolved"
    assert result["verification_required"][0]["reason_codes"] == ["district_membership_unresolved"]
