"""Collect read-only Databricks evidence for an independent CareProof audit."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from careproof.recommend import SERVICE_ALIASES, recommend
from careproof_agent.db import DatabricksSQL, load_config


CITIES = ("Jaipur", "Delhi", "New Delhi", "Lucknow", "Patna", "Mumbai")


def collect(db):
    schema = db.config["target_schema"]
    gold = f"{schema}.gold_service_index"
    bronze = f"{schema}.bronze_facilities"
    cities = ", ".join(f"'{city.lower()}'" for city in CITIES)
    # Include every indexed service in the selected cities for place disambiguation.
    selection = f"""
        SELECT * FROM {gold}
        WHERE lower(city_clean) IN ({cities}) OR lower(district_clean) IN ({cities})
        UNION
        SELECT * FROM {gold}
        QUALIFY row_number() OVER (
            PARTITION BY service_key, evidence_status, claim_type, geo_status
            ORDER BY facility_id
        ) <= 5
    """
    query = f"""
        WITH selected AS ({selection}),
        lineage AS (
            SELECT b.unique_id, count(*) AS bronze_id_count,
                collect_list(named_struct(
                    'source_content_id', b.source_content_id, 'name', b.name,
                    'address_city', b.address_city, 'address_state', b.address_stateOrRegion,
                    'address_pin', b.address_zipOrPostcode,
                    'cluster_id', b.cluster_id, 'latitude', b.latitude, 'longitude', b.longitude,
                    'description', b.description, 'capability', b.capability,
                    'procedure', b.procedure, 'specialties', b.specialties,
                    'equipment', b.equipment, 'source_urls', b.source_urls
                )) AS bronze_records
            FROM {bronze} b
            JOIN (SELECT DISTINCT facility_id FROM selected) s ON s.facility_id = b.unique_id
            GROUP BY b.unique_id
        )
        SELECT to_json(struct(s.*, l.bronze_id_count, l.bronze_records),
                       map('ignoreNullFields', 'false'))
        FROM selected s LEFT JOIN lineage l ON l.unique_id = s.facility_id
        ORDER BY s.facility_id, s.service_key
    """
    expected = db.number(f"SELECT count(*) FROM ({selection}) sampled")
    rows = []
    # The existing SQL helper returns only the first chunk. Keep pages small
    # and fail on a short page rather than treating it as the end of the data.
    for offset in range(0, expected, 20):
        page = db.execute(f"{query} LIMIT 20 OFFSET {offset}")
        if len(page) != min(20, expected - offset):
            raise RuntimeError(f"Incomplete audit export at offset {offset}")
        rows.extend(json.loads(row[0]) for row in page)
    by_key = {(row["facility_id"], row["service_key"]): row for row in rows}
    if len(by_key) != expected:
        raise RuntimeError("Duplicate audit keys. Check Gold cardinality and concurrent rebuilds.")

    searches = []
    for city in CITIES:
        for service in SERVICE_ALIASES:
            request = {"service": service, "location": {"city": city}}
            result = recommend(request, rows)
            citations = []
            for candidate in result["recommendations"]:
                row = by_key[candidate["facility_id"], service]
                citation = candidate["citation"]
                matches = [
                    record for record in row["bronze_records"] or []
                    if record["source_content_id"] == citation["source_content_id"]
                    and citation["claim_text"] in (record[citation["source_field"]] or "")
                ]
                citations.append({
                    "facility_id": candidate["facility_id"],
                    "bronze_id_count": row["bronze_id_count"],
                    "exact_own_record_citation": (
                        citation["record_id"] == candidate["facility_id"]
                        and row["bronze_id_count"] == len(matches) == 1
                    ),
                })
            searches.append({"query": request, "result": result, "citation_checks": citations})
    return {
        "collected_at_utc": datetime.now(timezone.utc).isoformat(),
        "selection_sql": selection.strip(),
        "export_sql": query.strip(),
        "review_status": "unlabeled",
        "external_url_status": "unreviewed",
        "rows": rows,
        "searches": searches,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = collect(DatabricksSQL(load_config()))
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(report, output, indent=2, ensure_ascii=False, allow_nan=False)
        output.write("\n")


if __name__ == "__main__":
    main()
