"""Schema resolver.

The Marketplace facility table has ~51 attributes whose exact names may vary between
releases. Rather than hard-coding names, each canonical field lists candidate source
columns; matching is case/punctuation-insensitive. Override with `overrides` if needed.
"""

from __future__ import annotations

import re

# canonical -> candidate source names (first match wins for scalar fields;
# for MULTI fields every matching column is combined)
CANDIDATES: dict[str, list[str]] = {
    "facility_id": ["pk_unique_id", "unique_id", "facility_id", "pk_id", "id", "uuid", "content_table_id"],
    "name": ["name", "facility_name", "organization_name", "org_name"],
    "description": ["description", "organizationDescription", "facility_description", "missionStatement", "about"],
    "specialties": ["specialties", "specialty", "specialities"],
    "procedure": ["procedure", "procedures"],
    "equipment": ["equipment", "equipments"],
    "capability": ["capability", "capabilities"],
    "facility_type": ["facilityTypeId", "facility_type", "facilityType", "facility_type_id", "type"],
    "operator_type": ["operatorTypeId", "operator_type", "operatorType", "operator_type_id"],
    "organization_type": ["organization_type", "organizationType"],
    "address_line1": ["address_line1", "addressLine1", "address"],
    "address_line2": ["address_line2", "addressLine2"],
    "address_line3": ["address_line3", "addressLine3"],
    "city": ["address_city", "city", "addressCity"],
    "state": ["address_stateOrRegion", "address_state", "state", "stateOrRegion", "region"],
    "postcode": ["address_zipOrPostcode", "postcode", "pincode", "pin_code", "zip", "zipcode", "postal_code"],
    "latitude": ["latitude", "lat", "address_latitude"],
    "longitude": ["longitude", "lon", "lng", "long", "address_longitude"],
    "num_doctors": ["numberDoctors", "number_doctors", "num_doctors", "doctors"],
    "capacity": ["capacity", "bed_capacity", "beds", "number_beds", "numberBeds"],
    "year_established": ["yearEstablished", "year_established", "established"],
    "source_urls": ["source_urls", "source_url", "sourceUrls", "sourceUrl", "sources", "source"],
    "website": ["officialWebsite", "websites", "website", "official_website"],
    "phone": ["officialPhone", "phone_numbers", "phoneNumbers", "phone", "phone_number"],
    "email": ["email", "emails"],
}

MULTI = {"description", "source_urls", "website", "phone"}


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def resolve(columns: list[str], overrides: dict[str, str | list[str]] | None = None) -> dict[str, list[str]]:
    """Return canonical -> list of source columns present in `columns`."""
    by_key = {}
    for c in columns:
        by_key.setdefault(_key(c), c)
    out: dict[str, list[str]] = {}
    used: set[str] = set()
    overrides = overrides or {}
    for canon, cands in CANDIDATES.items():
        if canon in overrides:
            ov = overrides[canon]
            out[canon] = [ov] if isinstance(ov, str) else list(ov)
            continue
        found = []
        for cand in cands:
            src = by_key.get(_key(cand))
            if src and src not in found:
                # a column feeds only one scalar field (e.g. 'websites' → website, not source_urls twice)
                if canon not in MULTI and src in used:
                    continue
                found.append(src)
                if canon not in MULTI:
                    break
        out[canon] = found
        used.update(found)
    return out


def describe(mapping: dict[str, list[str]]) -> str:
    lines = []
    for canon, src in mapping.items():
        lines.append(f"{canon:18s} <- {', '.join(src) if src else '(not found)'}")
    return "\n".join(lines)
