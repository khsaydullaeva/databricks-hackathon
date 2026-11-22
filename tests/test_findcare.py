import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.dirname(__file__))

import synthetic  # noqa: E402
from findcare import pipeline  # noqa: E402
from findcare.evidence import facility_evidence  # noqa: E402
from findcare.geo import resolve_location  # noqa: E402
from findcare.nfhs import clean_nfhs, district_context, parse_value  # noqa: E402
from findcare.ranking import recommend  # noqa: E402
from findcare.services import SERVICES_BY_ID, match_service  # noqa: E402
from findcare.text import clean_pincode, parse_list  # noqa: E402


@pytest.fixture(scope="module")
def gold():
    fac, _ = pipeline.clean_facilities(synthetic.facilities())
    pins = pipeline.clean_pincodes(synthetic.pincodes())
    wide, long, _ = clean_nfhs(synthetic.nfhs())
    g = pipeline.build_gold(fac, pins, wide, log=lambda *_: None)
    g["silver_nfhs_long"] = long
    return g


@pytest.fixture(scope="module")
def rt(gold):
    # round-trip through the storable (Delta-like) representation, as the app does
    stored = {k: pipeline.to_storable(v) for k, v in gold.items()}
    return pipeline.Runtime.from_tables(stored)


def test_parse_list_variants():
    assert parse_list('["a", "b", "a"]') == ["a", "b"]
    assert parse_list("['x', 'y']") == ["x", "y"]
    assert parse_list("cardiology, nephrology") == ["cardiology", "nephrology"]
    assert parse_list("Offers dialysis; Has ICU") == ["Offers dialysis", "Has ICU"]
    assert parse_list(None) == [] and parse_list("[]") == [] and parse_list(float("nan")) == []
    assert parse_list(["a", None, "b"]) == ["a", "b"]


def test_clean_pincode():
    assert clean_pincode("302001") == "302001"
    assert clean_pincode(302001.0) == "302001"
    assert clean_pincode("Jaipur 302 001") == "302001"
    assert clean_pincode("012345") is None


def test_match_service():
    assert match_service("kidney dialysis").id == "dialysis"
    assert match_service("emergency C-section").id == "emergency_csection"
    assert match_service("NICU").id == "nicu"
    assert match_service("MRI").id == "mri"
    assert match_service("burns").id == "burn_care"
    assert match_service("xyzzy") is None


def test_evidence_levels():
    d = SERVICES_BY_ID["dialysis"]
    strong = facility_evidence({"name": "X", "procedure_list": ["Performs hemodialysis"], "equipment_list": ["dialysis machines"]}, d)
    assert strong["level"] == "Strong" and strong["n_fields"] == 2
    single = facility_evidence({"name": "X", "procedure_list": ["hemodialysis"]}, d)
    assert single["level"] == "Moderate"
    indirect = facility_evidence({"name": "X", "specialties_list": ["nephrology"]}, d)
    assert indirect["level"] == "Indirect" and indirect["evidence_score"] <= 0.30
    neg = facility_evidence({"name": "X", "description": "We do not offer dialysis currently."}, d)
    assert neg["evidence_score"] == 0 and len(neg["negated"]) == 1


def test_nfhs_parsing():
    assert parse_value("*") == (None, True, False)
    assert parse_value("(29.5)") == (29.5, False, True)
    assert parse_value("1,234") == (1234.0, False, False)


def test_quality_flags(gold):
    f = gold["gold_facilities"].set_index("facility_id")
    codes = lambda i: {x["code"] for x in f.loc[i, "trust_flags"]}
    assert "coords_vs_pin" in codes("F4") and "no_sources" in codes("F4")
    assert f.loc["F7", "location_basis"].startswith("postcode")
    assert "shared_coords" in codes("F9")
    assert "state_mismatch" in codes("F12")
    assert "breadth_outlier" in codes("F8")
    assert f.loc["F1", "dup_group"] == f.loc["F1b", "dup_group"]
    assert f.loc["F1", "trust_score"] > f.loc["F4", "trust_score"]


def test_pin_lookup(gold):
    pl = gold["gold_pincode_lookup"].set_index("pincode")
    assert bool(pl.loc["824101", "is_ambiguous"])
    assert abs(pl.loc["302001", "lat"] - 26.91) < 0.05  # 0,0 geocode excluded


def test_resolve_location(rt):
    args = (rt.pin_lookup, rt.district_lookup, rt.place_index)
    loc = resolve_location("302001", *args)
    assert loc.ok and loc.method == "pincode" and loc.district == "Jaipur"
    loc = resolve_location("26.9, 75.8", *args)
    assert loc.ok and loc.district == "Jaipur"
    loc = resolve_location("75.8, 26.9", *args)
    assert loc.ok and abs(loc.lat - 26.9) < 1e-9 and any("swapped" in w for w in loc.warnings)
    loc = resolve_location("Aurangabad", *args)
    assert loc.ok and len(loc.alternatives) >= 2 and loc.warnings
    loc = resolve_location("Aurangabad, Maharashtra", *args)
    assert loc.ok and loc.state_norm == "maharashtra"
    loc = resolve_location("Jaipure", *args)
    assert loc.ok and loc.district == "Jaipur"
    assert not resolve_location("Atlantis", *args).ok
    assert resolve_location("824101", *args).warnings  # multi-district PIN


def test_recommend_dialysis_jaipur(rt):
    loc = resolve_location("Jaipur", rt.pin_lookup, rt.district_lookup, rt.place_index)
    res = recommend(rt, SERVICES_BY_ID["dialysis"], loc, radius_km=50)
    top = res.recommendations[0]
    assert top["name"] == "Jaipur Kidney Care Hospital"
    assert top["evidence_level"] == "Strong"
    assert top["citations"] and top["citations"][0]["kind"] == "names the service"
    names = [r["name"] for r in res.recommendations]
    assert names.count("Jaipur Kidney Care Hospital") == 1  # duplicate collapsed
    assert "Pink City Hospital" not in names  # negated mention
    # F4's coordinates contradict its postcode -> safety mode holds it back (with reasons) instead of ranking it
    assert "Sawai Multispeciality" not in names
    sawai = [r for r in res.held if r["name"] == "Sawai Multispeciality"]
    assert sawai, "postcode+city outvote wrong coordinates, so F4 should be found near Jaipur"
    assert "record_contradictions" in sawai[0]["guard_issues"]
    assert any(r["name"] == "Sawai Multispeciality" for r in recommend(rt, SERVICES_BY_ID["dialysis"], loc, guarded=False).recommendations)
    assert sawai[0]["location_basis"].startswith("postcode centre (coordinates contradicted")
    assert any("km from the centre of its postcode" in c for c in sawai[0]["cautions"])
    assert any(l["name"] == "City Nephro Clinic" for l in res.leads)  # indirect only
    assert res.recommendations[0]["vs_next"] is None or "Ranked above" in res.recommendations[0]["vs_next"]


def test_recommend_widens_radius(rt):
    loc = resolve_location("Ajmer", rt.pin_lookup, rt.district_lookup, rt.place_index)
    res = recommend(rt, SERVICES_BY_ID["dialysis"], loc, radius_km=10)
    assert res.searched_radius_km > 10
    ajmer = [r for r in res.recommendations if r["name"] == "Ajmer Dialysis Centre"]
    assert ajmer and ajmer[0]["location_basis"].startswith("postcode")


def test_plausibility_penalty(rt):
    loc = resolve_location("Patna", rt.pin_lookup, rt.district_lookup, rt.place_index)
    res = recommend(rt, SERVICES_BY_ID["nicu"], loc, radius_km=50)
    every = res.recommendations + res.held + res.leads
    tiny = [r for r in every if r["name"] == "Tiny Child Clinic"][0]
    assert tiny["plausibility_factor"] < 1 and any("beds" in c for c in tiny["cautions"])


def test_nfhs_context(rt):
    loc = resolve_location("Patna", rt.pin_lookup, rt.district_lookup, rt.place_index)
    ctx = district_context(rt.nfhs_long, rt.district_nfhs_map, loc.state_norm, loc.district_norm,
                           SERVICES_BY_ID["emergency_csection"].nfhs)
    assert ctx["ok"]
    cs = [r for r in ctx["rows"] if "caesarean" in r["indicator"].lower()][0]
    assert cs["value"] == 29.5 and "low sample" in cs["caution"]


def test_coverage_table(gold):
    cov = gold["gold_district_service_coverage"]
    j = cov[(cov["district_norm"] == "jaipur") & (cov["service_id"] == "dialysis")].iloc[0]
    assert j["n_facilities_strong_or_moderate"] >= 1


def test_silver_roundtrip_matches_direct():
    """Notebook path (silver -> Delta -> restore -> gold) must equal the in-memory path."""
    fac, _ = pipeline.clean_facilities(synthetic.facilities())
    pins = pipeline.clean_pincodes(synthetic.pincodes())
    direct = pipeline.build_gold(fac, pins, None, log=lambda *_: None)
    fac2 = pipeline.restore_silver_facilities(pipeline.to_storable(fac))
    pins2 = pipeline.restore_silver_pins(pipeline.to_storable(pins))
    via = pipeline.build_gold(fac2, pins2, None, log=lambda *_: None)
    a = direct["gold_facility_service_evidence"][["facility_id", "service_id", "evidence_score"]]
    b = via["gold_facility_service_evidence"][["facility_id", "service_id", "evidence_score"]]
    pd.testing.assert_frame_equal(a.reset_index(drop=True), b.reset_index(drop=True))
    assert list(direct["gold_facilities"]["trust_score"]) == list(via["gold_facilities"]["trust_score"])


def test_render_html(rt):
    from findcare.render import result_html

    loc = resolve_location("Jaipur", rt.pin_lookup, rt.district_lookup, rt.place_index)
    res = recommend(rt, SERVICES_BY_ID["dialysis"], loc)
    html = result_html(res, {"ok": False, "note": "n/a"})
    assert "Jaipur Kidney Care Hospital" in html and "Performs hemodialysis" in html


# ---------------------------------------------------------------- real-data quirks (found in the Marketplace tables)
def test_quarantine_malformed_rows():
    import uuid

    good = [{"unique_id": str(uuid.uuid4()), "name": f"H{i}", "organization_type": "facility"} for i in range(30)]
    bad = [{"unique_id": "Mobile: +91-98xxxx", "name": " and accountability", "organization_type": " Doctor couple"}]
    ok, q = pipeline.quarantine_malformed(pd.DataFrame(good + bad))
    assert len(ok) == 30 and len(q) == 1


def test_extraction_artifacts_dropped():
    raw = synthetic.facilities().head(1).copy()
    raw["capability"] = '["Cancer?", "Neonatal care not specified", "Has ICU", "Provides emergency services only if explicitly stated in the text"]'
    fac, _ = pipeline.clean_facilities(raw)
    assert fac.loc[0, "capability_list"] == ["Has ICU"] and fac.loc[0, "n_artifacts_capability"] == 3


def test_future_and_planned_mentions_ignored():
    r = SERVICES_BY_ID["radiotherapy"]
    for text in ("Phase II will include TrueBeam radiation therapy system", "Plans to add a radiation oncology program"):
        e = facility_evidence({"name": "X", "equipment_list": [text]}, r)
        assert e["evidence_score"] == 0 and e["negated"], text


def test_corneal_transplant_is_eye_care_not_organ_transplant():
    e = facility_evidence({"name": "X", "procedure_list": ["Corneal transplant (keratoplasty)"]}, SERVICES_BY_ID["transplant"])
    assert not e["has_strong"]
    e = facility_evidence({"name": "X", "procedure_list": ["Corneal transplant (keratoplasty)"]}, SERVICES_BY_ID["cataract"])
    assert e["has_strong"]
    assert not facility_evidence({"name": "X", "procedure_list": ["Revision Hair Transplant Surgery"]}, SERVICES_BY_ID["transplant"])["has_strong"]


def test_pin_directory_repairs():
    raw = synthetic.pincodes()
    extra = pd.DataFrame([
        # swapped lat/lon
        ("Bihar", "Patna", "Patna", "Swapped S.O", 800001, "PO", "Delivery", "PATNA", "BIHAR", "85.14", "25.61"),
        # 'NA' admin fields -> filled from same PIN
        ("Bihar", "Patna", "Patna", "Unknown B.O", 800001, "BO", "Delivery", "NA", "NA", "25.60", "85.13"),
    ] + [  # same-name geocoding cluster: Bihar offices carrying Aurangabad (Maharashtra) coordinates
        ("Bihar", "Gaya", "Aurangabad", f"Wrong {i} BO", 824101 + (i % 3), "BO", "Delivery", "AURANGABAD", "BIHAR", "19.87617", "75.343313")
        for i in range(6)
    ], columns=raw.columns)
    p = pipeline.clean_pincodes(pd.concat([raw, extra], ignore_index=True))
    sw = p[p["officename"] == "Swapped S.O"].iloc[0]
    assert sw["geo_valid"] and abs(sw["latitude"] - 25.61) < 1e-9
    na = p[p["officename"] == "Unknown B.O"].iloc[0]
    assert na["district"] == "PATNA" and na["statename"] == "BIHAR"
    wrong = p[p["officename"].str.startswith("Wrong")]
    assert not wrong["geo_valid"].any()
    from findcare.geo import build_district_lookup

    a = build_district_lookup(p).set_index(["state_norm", "district_norm"]).loc[("bihar", "aurangabad")]
    assert abs(a["lat"] - 24.75) < 0.1  # not dragged to Maharashtra


def test_district_rename_alias():
    from findcare.nfhs import match_districts

    nf = pd.DataFrame({"state_norm": ["haryana"], "district_norm": ["mewat"], "district": ["Mewat"], "state": ["Haryana"]})
    dl = pd.DataFrame({"state_norm": ["haryana"], "district_norm": ["nuh"]})
    m = match_districts(nf, dl).iloc[0]
    assert m["nfhs_district_norm"] == "mewat" and m["match_method"] == "known_rename"


def test_nfhs_curated_indicators_for_abbreviated_columns():
    raw = pd.DataFrame({
        "district_name": ["Patna "], "state_ut": ["Bihar"],
        "births_delivered_by_csection_5y_pct": [12.0], "institutional_birth_5y_pct": ["(81.5)"],
        "hh_member_covered_health_insurance_pct": ["*"], "hh_electricity_pct": [99.0],
    })
    wide, long, _ = clean_nfhs(raw)
    dmap = pd.DataFrame({"state_norm": ["bihar"], "district_norm": ["patna"], "nfhs_state_norm": ["bihar"],
                         "nfhs_district_norm": ["patna"], "match_method": ["exact"], "match_score": [1.0]})
    ctx = district_context(long, dmap, "bihar", "patna", (), service_id="emergency_csection")
    labels = [r["indicator"] for r in ctx["rows"]]
    assert labels[0] == "Births in a health facility (%)" and "Births delivered by C-section (%)" in labels
    assert "Households with electricity (%)" not in labels  # not relevant to C-section
    ins = [r for r in ctx["rows"] if "insurance" in r["indicator"]][0]
    assert ins["value"] is None and "suppressed" in ins["caution"]


def test_locality_in_state_field_is_not_a_contradiction():
    raw = synthetic.facilities()
    raw.loc[raw["pk_unique_id"] == "F2", "address_stateOrRegion"] = "Sikandra"
    fac, _ = pipeline.clean_facilities(raw)
    g = pipeline.build_gold(fac, pipeline.clean_pincodes(synthetic.pincodes()), None, log=lambda *_: None)
    f = g["gold_facilities"].set_index("facility_id")
    assert "state_mismatch" not in {x["code"] for x in f.loc["F2", "trust_flags"]}
    assert "state_mismatch" in {x["code"] for x in f.loc["F12", "trust_flags"]}  # a real state that disagrees still flags


# ---------------------------------------------------------------- website fact-check
def test_find_services_on_web_pages():
    from findcare.webcheck import find_services, site_of, is_directory

    pages = [{"url": "https://h.example/services", "text": "Home\nMedicine Delivery\n24x7 Dialysis Unit with 10 machines\n"
                                                             "We do not have a NICU at present.\nA Mother's NICU Journey"}]
    got = {h["service_id"]: h for h in find_services(pages)}
    assert "dialysis" in got and got["dialysis"]["page_url"].endswith("/services")
    assert "maternity" not in got  # 'Medicine Delivery' is not childbirth
    assert "nicu" not in got  # negated, and blog-story titles are ignored
    assert site_of('["esic.nic.in"]') == ("http://esic.nic.in", "esic.nic.in")
    assert is_directory("xyz.business.site") and not is_directory(float("nan"))


def _gold_with_web():
    fac, _ = pipeline.clean_facilities(synthetic.facilities())
    pins = pipeline.clean_pincodes(synthetic.pincodes())
    status = pd.DataFrame([
        {"facility_id": "F1", "domain": "jkc.example", "site_kind": "own", "web_status": "ok", "fetched_at": "2026-11-21T10:00:00+00:00"},
        {"facility_id": "F2", "domain": "nephro.example", "site_kind": "own", "web_status": "ok", "fetched_at": "2026-11-21T10:00:00+00:00"},
        {"facility_id": "F4", "domain": "sawai.example", "site_kind": "own", "web_status": "ok", "fetched_at": "2026-11-21T10:00:00+00:00"},
        {"facility_id": "F3", "domain": "pink.example", "site_kind": "own", "web_status": "unreachable"},
    ])
    hits = pd.DataFrame([
        {"facility_id": "F1", "service_id": "dialysis", "site_kind": "own", "term": "dialysis", "quote": "Dialysis unit open 24x7",
         "page_url": "https://jkc.example/services", "fetched_at": "2026-11-21T10:00:00+00:00"},
        {"facility_id": "F2", "service_id": "dialysis", "site_kind": "own", "term": "hemodialysis", "quote": "Hemodialysis available",
         "page_url": "https://nephro.example/", "fetched_at": "2026-11-21T10:00:00+00:00"},
    ])
    return pipeline.build_gold(fac, pins, None, log=lambda *_: None, web_status=status, web_hits=hits)


def test_web_confirmation_raises_evidence_and_is_cited():
    g = _gold_with_web()
    ev = g["gold_facility_service_evidence"]
    d = ev[ev["service_id"] == "dialysis"].set_index("facility_id")
    assert d.loc["F1", "web_status"] == "confirmed_own_site" and d.loc["F1", "level"] == "Strong"
    # record only had 'nephrology' (Indirect); its own website names hemodialysis -> now a real claim
    assert d.loc["F2", "level"] == "Moderate" and any(i["field"] == "website" for i in d.loc["F2", "items"])
    assert d.loc["F4", "web_status"] == "not_found_on_site"
    rt_ = pipeline.Runtime.from_tables({k: pipeline.to_storable(v) for k, v in g.items()})
    loc = resolve_location("Jaipur", rt_.pin_lookup, rt_.district_lookup, rt_.place_index)
    res = recommend(rt_, SERVICES_BY_ID["dialysis"], loc)
    top = res.recommendations[0]
    assert top["web_status"] == "confirmed_own_site" and top["web_url"] == "https://jkc.example/services"
    assert any(c.get("url") == "https://jkc.example/services" for c in top["citations"])
    sawai = [r for r in res.recommendations + res.held if r["name"] == "Sawai Multispeciality"][0]
    assert any("does not mention this service" in c for c in sawai["cautions"])
    only = recommend(rt_, SERVICES_BY_ID["dialysis"], loc, only_confirmed=True)
    assert {r["web_status"] for r in only.recommendations} == {"confirmed_own_site"}


def test_review_queue_separates_untrustworthy_records():
    g = _gold_with_web()
    f = g["gold_facilities"].set_index("facility_id")
    assert f.loc["F4", "needs_review"]  # coordinates contradict postcode (+ site doesn't mention its claim)
    assert f.loc["F12", "needs_review"]  # state contradicts postcode
    assert not f.loc["F1", "needs_review"]
    q = g["gold_review_queue"]
    assert {"F4", "F12"} <= set(q["facility_id"]) and q["review_priority"].is_monotonic_decreasing
    rt_ = pipeline.Runtime.from_tables({k: pipeline.to_storable(v) for k, v in g.items()})
    loc = resolve_location("Jaipur", rt_.pin_lookup, rt_.district_lookup, rt_.place_index)
    names = [r["name"] for r in recommend(rt_, SERVICES_BY_ID["dialysis"], loc, hide_review=True).recommendations]
    assert "Sawai Multispeciality" not in names


def test_patient_education_text_is_not_a_confirmation():
    from findcare.webcheck import find_services

    edu = [{"url": "u", "text": "Some of the common cardiac surgeries include heart transplants, heart valve repairs, angioplasty and bypass grafting for patients."}]
    assert not any(h["service_id"] == "transplant" for h in find_services(edu))
    offer = [{"url": "u", "text": "Our hospital provides kidney transplant surgery with a dedicated transplant team and modern ICU support for recovery."}]
    assert any(h["service_id"] == "transplant" for h in find_services(offer))


# ---------------------------------------------------------------- CareProof guards (safety mode)
def test_attribution_guard_detects_other_facility_and_city():
    from findcare.attribution import other_facility, other_site

    assert other_facility("Janakpuri Super Speciality Hospital is a government hospital in Delhi", "Aravind Eye Hospital") == "Janakpuri Super Speciality Hospital"
    assert other_facility("Aravind Eye Hospital is a private hospital", "Aravind Eye Hospital") is None  # itself
    assert other_facility("Apollo Hospitals Jubilee Hills is a multispecialty hospital", "Apollo Hospitals Chennai") is None  # brand branch
    assert other_facility("SGVP Clinic is a unit of SGVP Holistic Hospital", "SGVP Holistic Hospital") is None
    assert other_site("Provides dialysis at Namakkal", "chennai", {"chennai", "namakkal"}) == "Namakkal"
    assert other_site("Provides dialysis in partnership with X", "chennai", {"chennai"}) is None


def test_safety_mode_holds_conflicted_claims_and_abstains():
    raw = synthetic.facilities()
    # the only Ajmer facility's dialysis claim describes another hospital
    raw.loc[raw["pk_unique_id"] == "F7", "name"] = "Ajmer Care Clinic"
    raw.loc[raw["pk_unique_id"] == "F7", "procedure"] = '["Shanti Dialysis Hospital is a dialysis hospital in Jaipur"]'
    fac, _ = pipeline.clean_facilities(raw)
    g = pipeline.build_gold(fac, pipeline.clean_pincodes(synthetic.pincodes()), None, log=lambda *_: None)
    rt_ = pipeline.Runtime.from_tables({k: pipeline.to_storable(v) for k, v in g.items()})
    f = g["gold_facilities"].set_index("facility_id")
    assert "other_facility_text" in {x["code"] for x in f.loc["F7", "trust_flags"]}
    ev = g["gold_facility_service_evidence"].set_index(["facility_id", "service_id"])
    assert bool(ev.loc[("F7", "dialysis"), "attribution_conflict"])
    loc = resolve_location("Ajmer", rt_.pin_lookup, rt_.district_lookup, rt_.place_index)
    res = recommend(rt_, SERVICES_BY_ID["dialysis"], loc, radius_km=10)
    assert all(r["name"] != "Ajmer Care Clinic" for r in res.recommendations)
    held = [r for r in res.held if r["name"] == "Ajmer Care Clinic"]
    assert held and "claim_describes_other_facility" in held[0]["guard_issues"]


def test_funnel_and_sensitivity(rt):
    loc = resolve_location("Jaipur", rt.pin_lookup, rt.district_lookup, rt.place_index)
    res = recommend(rt, SERVICES_BY_ID["dialysis"], loc)
    counts = [c for _, c, _ in res.funnel]
    assert counts[1:-1] == sorted(counts[1:-1], reverse=True)  # each step can only remove facilities
    assert counts[-1] == len(res.recommendations)
    assert res.sensitivity and "top1_same" in res.sensitivity


def test_abstains_when_nothing_passes(rt):
    loc = resolve_location("Patna", rt.pin_lookup, rt.district_lookup, rt.place_index)
    res = recommend(rt, SERVICES_BY_ID["burn_care"], loc)
    assert res.abstained and not res.recommendations and res.abstain_reason


def test_analyst_holds_load_and_map_services():
    from findcare.attribution import review_holds

    h = review_holds()
    assert h and all(sid in {"dialysis", "mri", "nicu", "emergency_csection", "cardiac_surgery", "trauma", "emergency"} for _, sid in h)
    assert all(v["reason"] for v in h.values())
