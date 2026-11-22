"""Compare captured city-only searches with city/state searches on real data."""

import argparse
from collections import Counter
import json
from pathlib import Path

from careproof.audit import collect
from careproof.location import AUDITED_CITIES, address, postal_index, state_key
from careproof.recommend import recommend
from careproof_agent.db import DatabricksSQL


STATES = {
    "Jaipur": "Rajasthan",
    "Delhi": "NCT of Delhi",
    "New Delhi": "Delhi",
    "Lucknow": "Uttar Pradesh",
    "Patna": "Bihar",
    "Mumbai": "Maharashtra",
}


def summary(results):
    reasons = Counter(result["reason"] for result in results)
    ranked = sum(result["status"] == "ranked" for result in results)
    return {
        "executions": len(results), "ranked": ranked, "abstained": len(results) - ranked,
        "ambiguous_place": reasons["ambiguous_place"],
        "no_reliable_match": reasons["no_reliable_match"],
        "recommendation_placements": sum(len(result["recommendations"]) for result in results),
        "verification_required_placements": sum(len(result["verification_required"]) for result in results),
    }


def citation_matches(candidate, row):
    citation = candidate["citation"]
    return (
        citation["record_id"] == row["facility_id"]
        and row["bronze_id_count"] == 1
        and sum(record["source_content_id"] == citation["source_content_id"]
                and citation["claim_text"] in (record[citation["source_field"]] or "")
                for record in row["bronze_records"] or []) == 1
    )


def compare(baseline, current, offices=()):
    rows = current["rows"]
    by_key = {(row["facility_id"], row["service_key"]): row for row in rows}
    if len(by_key) != len(rows):
        raise ValueError("Gold facility/service keys are duplicated.")
    post = postal_index(offices)
    searches = []
    before, city_only, after = [], [], []
    failures = []
    for search in baseline["searches"]:
        old_query = search["query"]
        city = old_query["location"]["city"]
        state = STATES[city]
        service = old_query["service"]
        query = old_query | {"location": {"city": city, "state": state}}
        result = recommend(query, rows, postal_offices=offices)
        normalized = recommend(old_query, rows, postal_offices=offices)
        checks = []
        original = search["result"]
        for candidate in result["recommendations"]:
            row = by_key[candidate["facility_id"], service]
            admin = address(row, post)
            expected_cities = {"delhi", "new delhi"} if city == "Delhi" else {city.lower()}
            geography = (
                admin["city"] in expected_cities and admin["state"] == state_key(state)
                and (admin["city"], admin["state"]) in AUDITED_CITIES and not admin["issue"]
                and candidate["distance_km"] is None
            )
            citation = citation_matches(candidate, row)
            checks.append({"facility_id": row["facility_id"], "address_consistent": geography,
                           "exact_own_record_citation": citation})
            if not geography or not citation:
                failures.append({"query": query, **checks[-1]})
        if original["reason"] == "no_reliable_match" and result["status"] == "ranked":
            # Delhi's broader scope is intentional. The original substantive
            # Jaipur and New Delhi searches retain their city scope.
            failures.append({"query": query, "reason": "previous_no_reliable_match_now_ranked"})
        searches.append({"original_query": old_query, "query": query, "before": summary([original]),
                         "normalized_city_only": summary([normalized]), "after": summary([result]),
                         "ranked_checks": checks,
                         "verification_required": [
                             {"facility_id": row["facility_id"], "reason_codes": row["reason_codes"]}
                             for row in result["verification_required"]
                         ]})
        before.append(original)
        city_only.append(normalized)
        after.append(result)
    return {"before": summary(before), "normalized_city_only": summary(city_only), "after": summary(after),
            "before_collected_at_utc": baseline["collected_at_utc"],
            "after_source_collected_at_utc": current["collected_at_utc"],
            "input_row_count": len(rows), "postal_pins_loaded": len(post),
            "checks_failed": failures, "searches": searches}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, default=Path("reports/real_data_capture.json"))
    parser.add_argument("--live", action="store_true", help="Refresh Gold and Bronze from Databricks.")
    parser.add_argument("--postal-offices", type=Path, help="India Post JSON array with pin, state and district.")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    baseline = json.loads(args.capture.read_text(encoding="utf-8"))
    current = collect(DatabricksSQL()) if args.live else baseline
    offices = json.loads(args.postal_offices.read_text(encoding="utf-8")) if args.postal_offices else ()
    report = compare(baseline, current, offices)
    report["after_execution_mode"] = "fresh_databricks_export" if args.live else "recorded_databricks_export"
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "searches"}, indent=2))
    return int(bool(report["checks_failed"]))


if __name__ == "__main__":
    raise SystemExit(main())
