"""Regression cases for location conflicts without changing source records."""
import json
from pathlib import Path

import pytest

from careproof.attribution import claim_site_conflict, other_named_facility
from careproof.location import address, postal_index
from careproof.recommend import recommend


@pytest.mark.parametrize(("text", "expected"), [
    ("Offers MRI imaging services at Regional Jodhpur", "jodhpur"),
    ("We provide MRI in Jodhpur", "jodhpur"),
    ("Offers MRI in Jaipur", None),
    ("Offers MRI at a conference in Jodhpur", None),
    ("Offers MRI in partnership with Jodhpur", None),
    ("Referred for MRI at Regional Jodhpur", None),
    ("Offers MRI at Regional Unknown Locality", None),
])
def test_only_service_bound_known_location_is_a_conflict(text, expected):
    assert claim_site_conflict(text, "jaipur", ["jodhpur", "jaipur"]) == expected


def test_independent_facility_declaration_and_alias_control():
    claim = "Performs MRI imaging"
    statement = "V K Diagnostic Centre is listed as a diagnostic centre in Jaipur, Rajasthan."
    assert other_named_facility(json.dumps([claim, statement]), "A.K. Diagnostic") == statement
    assert other_named_facility(json.dumps([claim, statement]), "V.K. Diagnostic Center") is None
    same_brand = "Wellsun Medicity Super Speciality Hospital is listed as a Top Hospital in Lucknow."
    assert other_named_facility(json.dumps([claim, same_brand]), "Wellsun Medicity Hospital") is None
    assert other_named_facility(json.dumps([claim, "Participates in a study at V K Diagnostic Centre.",
                                            "Our diagnostic centre is a diagnostic centre in Jaipur."]),
                                "A.K. Diagnostic") is None


def test_postal_evidence_can_compare_known_districts_without_geocoding():
    row = {"city_clean": "Jaipur", "state_clean": "Rajasthan", "pin_clean": "342011",
           "pin_status": "format_valid_unverified"}
    assert address(row, {})["issue"] is None
    mapping = postal_index([{"pin": "342011", "state": "Rajasthan", "district": "Jodhpur"}] * 2)
    assert address(row | {"district_clean": "Jaipur"}, mapping)["issue"] == "postal_admin_conflict"
    assert address(row, mapping)["issue"] is None  # City text alone is not a district mapping.
    mapping["342011"].add(("rajasthan", "jaipur"))
    assert address(row, mapping)["issue"] == "postal_admin_ambiguous"


def test_captured_jaipur_mri_unsafe_candidates_are_withheld():
    root = Path(__file__).resolve().parents[1]
    rows = json.loads((root / "reports/real_data_capture.json").read_text())["rows"]
    result = recommend({"service": "mri", "location": {"city": "Jaipur"}}, rows)
    assert [row["name"] for row in result["recommendations"]] == [
        "Kamal Diagnostic Center", "DKM Diagnostic Centre"]
    withheld = {row["name"]: row for row in result["verification_required"]}
    for name, reason, exact in (
        ("Okay Diagnostic Center", "claim_branch_city_conflict", "Offers MRI imaging services at Okay Jodhpur"),
        ("A.K. Diagnostic", "claim_field_other_facility_reference", "Performs MRI imaging of the ankle joint (bilateral)"),
    ):
        row = withheld[name]
        assert reason in row["reason_codes"] and row["citation"]["claim_text"] == exact
        assert row["distance_km"] is None and "score" not in row
    assert (root / "app/careproof/attribution.py").read_bytes() == (root / "careproof/attribution.py").read_bytes()



def test_captured_guard_replay_changes_only_two_searches(monkeypatch):
    import careproof.recommend as engine

    root = Path(__file__).resolve().parents[1] / "reports"
    rows = json.loads((root / "real_data_capture.json").read_text())["rows"]
    queries = [entry["query"] for entry in
               json.loads((root / "location_resolution_live_before_after.json").read_text())["searches"]]
    with monkeypatch.context() as original:
        original.setattr(engine, "claim_site_conflict", lambda *_: None)
        original.setattr(engine, "other_named_facility", lambda *_: None)
        before = [engine.recommend(query, rows) for query in queries]
    after = [engine.recommend(query, rows) for query in queries]
    changed = [(query["service"], query["location"]["city"]) for query, old, new in
               zip(queries, before, after, strict=True) if old != new]
    assert changed == [("mri", "Jaipur"), ("dialysis", "Patna")]
    assert sum(len(result["recommendations"]) for result in before) == 57
    assert sum(len(result["recommendations"]) for result in after) == 54
    patna = next(
        result for query, result in zip(queries, after, strict=True)
        if query["service"] == "dialysis" and query["location"]["city"] == "Patna")
    lead = next(row for row in patna["verification_required"] if row["name"] == "Patna Medical College & Hospital")
    assert lead["reason_codes"] == ["claim_field_other_facility_reference"]


def test_supported_service_claim_cannot_override_administrative_conflict():
    root = Path(__file__).resolve().parents[1]
    rows = json.loads((root / "reports/real_data_capture.json").read_text())["rows"]
    indus = next(row for row in rows if row["name"] == "Indus Jaipur Hospital"
                 and row["service_key"] == "cardiac_surgery")
    # Simulate a successful extraction rebuild while leaving a separate geo conflict in place.
    revised = indus | {"claim_type": "candidate_claim", "evidence_status": "supported_claim",
                       "verification_reason": "attributable_dataset_offering_text",
                       "admin_status": "possible_admin_conflict"}
    outcome = recommend({"service": "cardiac_surgery",
                         "location": {"city": "Jaipur", "state": "Rajasthan"}}, [revised])
    assert outcome["recommendations"] == []
    assert outcome["verification_required"][0]["reason_codes"] == ["possible_admin_conflict"]
    assert outcome["verification_required"][0]["citation"]["claim_text"] == indus["claim_text"]
