"""Stage 12 acceptance tests using the captured export and explicit test doubles."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from app.data import DataUnavailable, Warehouse, search, source_links
from careproof.location_audit import citation_matches

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"

CASES = [
    ("dialysis", "Jaipur", 2), ("mri", "Jaipur", 4), ("nicu", "Jaipur", 2),
    ("dialysis", "Delhi", 1), ("mri", "New Delhi", 5),
    ("dialysis", "Lucknow", 5), ("dialysis", "Patna", 4), ("dialysis", "Mumbai", 4),
    ("cardiac_surgery", "Jaipur", 0),
]


@pytest.fixture(scope="module")
def capture():
    return json.loads((ROOT / "reports/real_data_capture.json").read_text())["rows"]


def run(capture, service, city):
    rows = [row for row in capture if row["service_key"] == service]
    addresses = [{k: row[k] for k in ("city_clean", "state_clean", "district_clean",
                                      "pin_clean", "pin_status")} for row in capture]
    return search({"service": service, "location": {"city": city}}, addresses, rows)


@pytest.mark.parametrize("service,city,count", CASES)
def test_recorded_search_and_original_claim_lineage(capture, service, city, count):
    result = run(capture, service, city)
    assert len(result["recommendations"]) == count
    assert result["status"] == ("ranked" if count else "abstain")
    by_id = {row["facility_id"]: row for row in capture if row["service_key"] == service}
    for candidate in result["recommendations"]:
        assert citation_matches(candidate, by_id[candidate["facility_id"]])
        assert candidate["distance_km"] is None


def test_ambiguous_location(capture):
    result = run(capture, "dialysis", "Hyderabad")
    assert result["reason"] == "ambiguous_place"
    assert len(result["location_choices"]) > 1
    assert not result["recommendations"]


def test_actual_conflict(capture):
    result = run(capture, "dialysis", "Nagercoil")
    conflicted = [row for row in result["verification_required"] if row["evidence_status"] == "conflicting"]
    assert conflicted
    assert all(row["citation"]["claim_text"] for row in conflicted)
    assert not {r["facility_id"] for r in conflicted} & {r["facility_id"] for r in result["recommendations"]}


def test_missing_coordinates(capture):
    result = run(capture, "dialysis", "Jaipur")
    facility = result["recommendations"][0]["facility_id"]
    rows = deepcopy(capture)
    row = next(r for r in rows if (r["facility_id"], r["service_key"]) == (facility, "dialysis"))
    row.update(geo_status="missing_coordinates", candidate_latitude=None, candidate_longitude=None)
    candidate = next(r for r in run(rows, "dialysis", "Jaipur")["recommendations"]
                     if r["facility_id"] == facility)
    assert candidate["distance_km"] is None
    assert candidate["geo_status"] == "missing_coordinates"


def test_packaged_engine_matches_authoritative_files():
    for name in ("recommend.py", "location.py"):
        assert (APP / "careproof" / name).read_bytes() == (ROOT / "careproof" / name).read_bytes()


def test_source_links():
    assert source_links('["https://example.org/a","javascript:alert(1)","https://example.org/a",'
                        '"https://user:pass@example.org","https://a.example/a b"]') == ["https://example.org/a"]


def test_parameters_chunks_and_truncation():
    requests = []
    manifest = NS(truncated=False, total_row_count=2)
    def execute(**kwargs):
        requests.append(kwargs)
        return NS(statement_id="statement", status=NS(state=NS(value="SUCCEEDED")), manifest=manifest,
                  result=NS(data_array=[['{"claim_text":"a"}']], next_chunk_index=1))
    sql = NS(execute_statement=execute, get_statement_result_chunk_n=lambda *args:
             NS(next_chunk_index=None, data_array=[['{"claim_text":"b"}']]))
    warehouse = Warehouse(NS(statement_execution=sql), "warehouse")
    facility = "facility' 42"
    data, seconds = warehouse.evidence("mri", facility, 6)
    assert data == [{"claim_text": "a"}, {"claim_text": "b"}]
    assert seconds >= 0
    assert facility not in requests[0]["statement"]
    assert {p.name: p.value for p in requests[0]["parameters"]} == {
        "facility": facility, "service": "mri", "offset": "6"}
    manifest.truncated = True
    with pytest.raises(DataUnavailable, match="incomplete"):
        warehouse.evidence("mri", facility, 6)


def test_warehouse_timeout_cancels(monkeypatch):
    from app import data
    clock = iter([0, 40])
    monkeypatch.setattr(data.time, "monotonic", lambda: next(clock))
    canceled = []
    sql = NS(execute_statement=lambda **_: NS(statement_id="job", status=NS(state=NS(value="PENDING"))),
             cancel_execution=canceled.append)
    with pytest.raises(DataUnavailable, match="may be starting"):
        Warehouse(NS(statement_execution=sql), "warehouse").addresses()
    assert canceled == ["job"]


@pytest.fixture
def ui(monkeypatch, capture):
    from streamlit.testing.v1 import AppTest

    # The app entry point imports data from its self-contained source directory.
    monkeypatch.setitem(__import__("sys").modules, "data", __import__("app.data", fromlist=["data"]))
    from app import data
    def service(key):
        return [r for r in capture if r["service_key"] == key], 0.2

    evidence_calls = []
    def evidence(key, facility, offset):
        evidence_calls.append((key, facility, offset))
        row = next(r for r in capture if r["service_key"] == key and r["facility_id"] == facility)
        return [row], 0.1

    from streamlit import cache_data, cache_resource
    cache_data.clear()
    cache_resource.clear()
    lookup = [{key: row[key] for key in data.ADDRESS.split(", ")} for row in capture]
    monkeypatch.setattr(data, "connect", lambda: NS(addresses=lambda: (lookup, 0.1), service=service,
                                                   evidence=evidence))
    return AppTest.from_file(str(APP / "app.py"), default_timeout=15), evidence_calls


def test_ui_search_and_expand_evidence(ui):
    app, calls = ui
    app.run()
    assert not app.exception
    app.text_input[0].input("Jaipur")
    app.button[0].click().run()
    assert not app.exception
    assert any("Dataset-supported facilities (2)" == h.value for h in app.subheader)
    review = next(section for section in app.expander if section.label == "Requires verification (11)")
    assert not review.proto.expanded
    assert any(field.label == "Verification page" for field in app.number_input)
    assert not calls
    evidence_picker = next(item for item in app.selectbox if item.label == "Facility evidence")
    evidence_picker.select_index(0).run()
    assert not app.exception
    assert calls and calls[0][0] == "dialysis"
    assert any("Full recorded field" == item.value for item in app.caption)


def test_ui_ambiguity_selection(ui):
    app, _ = ui
    app.run()
    app.text_input[0].input("Hyderabad")
    app.button[0].click().run()
    assert any("More than one" in item.value for item in app.warning)
    next(button for button in app.button if button.label == "Use this location").click().run()
    assert not app.exception
    assert any("Dialysis results" in item.value for item in app.header)


def test_ui_abstention_and_pin_scope(ui):
    app, _ = ui
    app.run()
    app.selectbox[0].select("Cardiac surgery")
    app.text_input[0].input("Jaipur")
    app.button[0].click().run()
    assert any("No reliable match" in item.value for item in app.warning)
    app.radio[0].set_value("Recorded PIN").run()
    assert any("PIN geocoding are unavailable" in item.value for item in app.caption)


def test_ui_database_error(ui, monkeypatch):
    from app import data
    app, _ = ui
    def unavailable():
        raise DataUnavailable("Databricks access was denied.")
    monkeypatch.setattr(data, "connect", unavailable)
    app.run()
    app.text_input[0].input("Jaipur")
    app.button[0].click().run()
    assert not app.exception
    assert [item.value for item in app.error] == ["Databricks access was denied."]


def test_real_coordinates_generate_straight_line_distance(capture):
    city_results = run(capture, "dialysis", "Jaipur")["recommendations"]
    facility = city_results[0]["facility_id"]
    point = next(row for row in capture if row["facility_id"] == facility
                 and row["service_key"] == "dialysis")
    query = {"service": "dialysis", "location": {"latitude": point["candidate_latitude"],
             "longitude": point["candidate_longitude"]}, "radius_km": 10}
    result = search(query, [], [row for row in capture if row["service_key"] == "dialysis"])
    candidate = next(r for r in result["recommendations"] if r["facility_id"] == facility)
    assert candidate["distance_km"] == 0
    assert candidate["score_components"]["proximity"] == 30
    assert result["distance_assumptions"]["method"] == "haversine_straight_line"


def test_ui_source_disclosures_preserve_links_and_omit_empty_controls(ui, capture, monkeypatch):
    from html import escape

    from app import data

    app, _ = ui
    rows = deepcopy(capture)
    result = run(rows, "dialysis", "Jaipur")
    first, second = result["recommendations"]
    second_row = next(row for row in rows if row["service_key"] == "dialysis"
                      and row["facility_id"] == second["facility_id"])
    second_row["source_urls_raw"] = '["", "javascript:alert(1)"]'
    original = data.connect()
    monkeypatch.setattr(data, "connect", lambda: NS(
        addresses=original.addresses,
        service=lambda service: ([r for r in rows if r["service_key"] == service], 0.2),
    ))
    app.run()
    app.text_input[0].input("Jaipur")
    app.button[0].click().run()

    assert not app.exception
    citations = [element.proto.body for element in app.get("html")
                 if '<div class="cp-citation">' in element.proto.body]
    supported = [next(item for item in citations if f"Record {candidate['facility_id']}" in item)
                 for candidate in (first, second)]
    original_urls = source_links(first["citation"]["source_urls_raw"])
    assert len(original_urls) == 10
    assert all(f'href="{escape(url, quote=True)}"' in supported[0] for url in original_urls)
    assert "Unverified source leads (10)" in supported[0]
    assert "<details" not in supported[1] and 'href=' not in supported[1]
    assert "No usable web links" in supported[1]
    for item, candidate in zip(supported, (first, second)):
        claim = candidate["citation"]
        assert escape(claim["claim_text"]) in item
        assert claim["source_content_id"] in item


def test_ui_comparison_and_review_pagination(ui):
    app, _ = ui
    app.run()
    app.text_input[0].input("Jaipur")
    app.button[0].click().run()
    selection = next(widget for widget in app.selectbox if widget.label == "Compare the top result with")
    selection.select_index(0).run()
    assert not app.exception
    assert list(app.table[0].value["Component"])[-1] == "Total"
    assert any("No coordinate distance was computed" in item.value for item in app.caption)
    selection = next(widget for widget in app.selectbox if widget.label == "Compare the top result with")
    selection.select_index(1).run()
    assert any("did not meet eligibility criteria" in item.value for item in app.markdown)
    page = next(widget for widget in app.number_input if widget.label == "Verification page")
    initial = [element.proto.body for element in app.get("html") if '<div class="cp-heading">' in element.proto.body]
    page.set_value(3).run()
    final = [element.proto.body for element in app.get("html") if '<div class="cp-heading">' in element.proto.body]
    assert not app.exception
    assert len(initial) == 7 and len(final) == 3
    assert initial[:2] == final[:2]


@pytest.mark.parametrize("city,service,expected", [
    ("Jaipur", "dialysis", None), ("Jaipur", "cardiac_surgery", "no_reliable_match"),
    ("Hyderabad", "dialysis", "ambiguous_place"),
])
def test_decision_trace_reconciles_with_ranker_and_bronze(capture, city, service, expected):
    from app.decision_trace import build_trace

    query = {"service": service, "location": {"city": city}}
    lookup = [{key: row[key] for key in ("city_clean", "state_clean", "district_clean",
                                        "pin_clean", "pin_status")} for row in capture]
    rows = [row for row in capture if row["service_key"] == service]
    result = search(query, lookup, rows)
    trace = build_trace(query, lookup, rows, result)
    assert trace["request"]["service_key"] == result["service_key"] == service
    assert trace["geography"]["choices"] == result.get("location_choices", [])
    assert trace["geography"]["assumptions"] == result["distance_assumptions"]
    assert trace["retrieval"] == {"address_rows": len(lookup), "gold_rows": len(rows)}
    assert sum(trace["evidence"]["retrieved_states"].values()) == len(rows)
    assert trace["evidence"]["review_leads"] == result["verification_required"]
    assert trace["decision"]["reason"] == expected
    assert trace["decision"]["review"] == len(result["verification_required"])
    assert trace["decision"]["ranked"] == len(result["recommendations"])
    assert [r["facility_id"] for r in trace["ranking"]] == [r["facility_id"] for r in result["recommendations"]]
    for traced, ranked in zip(trace["ranking"], result["recommendations"], strict=True):
        assert traced["score"] == ranked["score"]
        assert traced["score_components"] == ranked["score_components"]
        assert traced["citation"] == ranked["citation"]
        row = next(row for row in rows if row["facility_id"] == traced["facility_id"])
        assert citation_matches(traced, row)


def test_ui_decision_trace(ui):
    app, _ = ui
    app.run()
    app.text_input[0].input("Jaipur")
    app.button[0].click().run()
    assert not app.exception
    trace = next(item for item in app.expander if item.label == "How CareProof reached this result")
    assert not trace.proto.expanded
    assert any(item.value == "2 ranked · 11 require verification" for item in trace.markdown)
    headings = [item.value for item in trace.markdown if item.value[:3] in {f"**{i}" for i in range(1, 7)}]
    assert len(headings) == 6


def test_jaipur_dialysis_trace_audits_scores_and_record_conflict(capture):
    from app.decision_trace import build_trace
    from careproof.recommend import EVIDENCE_POINTS

    rows = [row for row in capture if row["service_key"] == "dialysis"]
    query = {"service": "dialysis", "location": {"city": "Jaipur"}}
    result = search(query, [], rows)
    trace = build_trace(query, [], rows, result)
    assert trace["scoring"]["evidence_points_by_source_field"] == EVIDENCE_POINTS
    liberty, scr = trace["ranking"]
    for item, field, score in ((liberty, "capability", 70), (scr, "procedure", 68)):
        gold = next(row for row in rows if row["facility_id"] == item["facility_id"])
        assert item["citation"]["source_field"] == field
        assert item["citation"]["claim_text"] == "Provides dialysis services"
        assert citation_matches(item, gold)
        assert item["score_components"] == {"evidence": EVIDENCE_POINTS[field],
                                            "address_consistency": 10, "proximity": 0}
        assert item["score"] == score and item["distance_km"] is None
        assessment = trace["assessment"][item["facility_id"]]
        assert assessment["identity_review_count"] == assessment["clear_identity_review_count"] == 1
        assert assessment["identity_reason_codes"] == []
    cks = next(row for row in trace["evidence"]["review_leads"] if row["name"] == "CKS Hospital")
    gold = next(row for row in rows if row["facility_id"] == cks["facility_id"])
    assert citation_matches(cks, gold)
    assert "RS Hospital" in gold["bronze_records"][0]["capability"]
    assert "evidence_conflicting" in cks["reason_codes"]
    assessment = trace["assessment"][cks["facility_id"]]
    assert assessment["verification_reason"] == "text_mentions_other_facility_or_branch"
    assert assessment["identity_reason_codes"] == [assessment["verification_reason"]]
    assert assessment["claim_reason"] == "no_explicit_offering_cue"
    assert assessment["has_service_negation"] is False
    assert "score" not in cks


@pytest.mark.parametrize("service,city,message", [
    ("Dialysis", "Hyderabad", "ambiguous place"),
    ("Cardiac surgery", "Jaipur", "no reliable match"),
])
def test_ui_trace_on_unranked_paths(ui, service, city, message):
    app, _ = ui
    app.run()
    app.selectbox[0].select(service)
    app.text_input[0].input(city)
    app.button[0].click().run()
    assert not app.exception
    trace = next(item for item in app.expander if item.label == "How CareProof reached this result")
    assert any(item.value == f"Abstained · {message}" for item in trace.text)
    assert sum(item.value.startswith("**") for item in trace.markdown) == 6


def test_ui_trace_shows_policy_and_selected_conflict(ui):
    app, _ = ui
    app.run()
    app.text_input[0].input("Jaipur")
    app.button[0].click().run()
    trace = next(item for item in app.expander if item.label == "How CareProof reached this result")
    assert any("Capability 60 · Procedure 58" in item.value for item in trace.text)
    lead = next(item for item in trace.selectbox if item.label == "Inspect verification lead")
    lead.select(next(option for option in lead.options if option.startswith("CKS Hospital ·"))).run()
    assert not app.exception
    trace = next(item for item in app.expander if item.label == "How CareProof reached this result")
    assert any("Assessment reason · text_mentions_other_facility_or_branch" == item.value for item in trace.text)
    assert any("no explicit denial" in item.value for item in trace.caption)
