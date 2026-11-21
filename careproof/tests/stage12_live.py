"""Run the Stage 12 search path against Databricks and reconcile ranked claims."""
import json
from time import perf_counter

from app.data import DataUnavailable, connect, search

SEARCHES = [
    ("dialysis", "Jaipur", "Rajasthan"), ("mri", "Jaipur", "Rajasthan"),
    ("nicu", "Jaipur", "Rajasthan"), ("dialysis", "Delhi", "Delhi"),
    ("mri", "New Delhi", "Delhi"), ("dialysis", "Lucknow", "Uttar Pradesh"),
    ("dialysis", "Patna", "Bihar"), ("dialysis", "Mumbai", "Maharashtra"),
    ("cardiac_surgery", "Jaipur", "Rajasthan"), ("dialysis", "Hyderabad", None),
    ("dialysis", "Nagercoil", "Tamil Nadu"),
]


def validate():
    warehouse = connect()
    addresses, lookup_seconds = warehouse.addresses()
    print(json.dumps({"address_rows": len(addresses), "warehouse_seconds": lookup_seconds}))
    assert addresses, "Gold address lookup is empty"
    by_service = {}
    for service, city, state in SEARCHES:
        sql_seconds = 0
        if service not in by_service:
            by_service[service], sql_seconds = warehouse.service(service)
        location = {"city": city}
        if state:
            location["state"] = state
        start = perf_counter()
        result = search({"service": service, "location": location}, addresses, by_service[service])
        rank_seconds = perf_counter() - start
        ranked = result["recommendations"]
        verified = 0
        for candidate in ranked:
            citation = candidate["citation"]
            checked, _ = warehouse.read("""
                SELECT to_json(named_struct('records', count(*), 'matches', count_if(
                    source_content_id = :content_id AND instr(
                        CASE :field WHEN 'capability' THEN capability WHEN 'procedure' THEN procedure
                            WHEN 'description' THEN description WHEN 'specialties' THEN specialties
                            WHEN 'equipment' THEN equipment END, :claim) > 0)))
                FROM workspace.careproof.bronze_facilities WHERE unique_id = :facility
            """, content_id=citation["source_content_id"], field=citation["source_field"],
                claim=citation["claim_text"], facility=citation["record_id"])
            assert citation["record_id"] == candidate["facility_id"]
            assert checked == [{"records": 1, "matches": 1}], candidate["facility_id"]
            verified += 1
        if city == "Hyderabad":
            assert result["reason"] == "ambiguous_place", result["reason"]
        elif service == "cardiac_surgery":
            assert result["reason"] == "no_reliable_match", result["reason"]
        elif city == "Nagercoil":
            assert any(row["evidence_status"] == "conflicting" for row in result["verification_required"])
        else:
            assert ranked, (service, city, result["reason"])
        print(json.dumps({"service": service, "location": location, "reason": result["reason"],
                          "ranked": len(ranked), "requires_verification": len(result["verification_required"]),
                          "matched_original_records": verified, "warehouse_seconds": sql_seconds,
                          "ranking_seconds": rank_seconds}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        validate()
    except DataUnavailable as error:
        raise SystemExit(str(error)) from None
