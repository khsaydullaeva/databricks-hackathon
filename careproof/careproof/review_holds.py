"""Versioned, evidence-bound review holds for recommendation eligibility."""
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path


@lru_cache(maxsize=1)
def load_review_holds():
    path = Path(__file__).with_name("review_holds.json")
    holds = {}
    for entry in json.loads(path.read_text(encoding="utf-8")):
        key = entry["facility_id"], entry["service_key"]
        if key in holds or entry["review_status"] not in {
            "analyst_reviewed_pending_human_confirmation", "human_reviewed",
        } or not all(entry.get(field) for field in (
            "reason", "reason_code", "evidence", "provenance", "source_content_id",
            "source_field", "selected_excerpt", "original_field_sha256",
        )):
            raise ValueError("Invalid review hold registry entry.")
        holds[key] = entry
    return holds


def review_hold(row, holds):
    entry = holds.get((row.get("facility_id"), row.get("service_key")))
    if entry is None:
        return None
    original = row.get("source_text")
    matches = (
        row.get("source_content_id") == entry["source_content_id"]
        and row.get("source_field") == entry["source_field"]
        and row.get("claim_text") == entry["selected_excerpt"]
        and isinstance(original, str)
        and sha256(original.encode()).hexdigest() == entry["original_field_sha256"]
    )
    return {
        "status": "matched_evidence" if matches else "source_revision_requires_rereview",
        "reason_code": entry["reason_code"], "reason": entry["reason"],
        "review_status": entry["review_status"], "provenance": entry["provenance"],
        "evidence": entry["evidence"] if matches else [],
    }


@lru_cache(maxsize=1)
def load_coordinate_holds():
    path = Path(__file__).with_name("coordinate_holds.json")
    return json.loads(path.read_text(encoding="utf-8"))
