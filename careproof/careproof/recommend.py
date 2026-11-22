"""Rank Stage 09 Gold rows without I/O. Run this module for a JSON CLI."""

import argparse
from collections import Counter, defaultdict
import json
from math import asin, cos, isfinite, radians, sin, sqrt
import re
import sys

from careproof.attribution import claim_site_conflict, other_named_facility
from careproof.location import AUDITED_CITIES, address, district_key, matches, place_choices, postal_index, state_key
from careproof.location import normalize as _place
from careproof.review_holds import load_coordinate_holds, load_review_holds, review_hold


EARTH_RADIUS_KM = 6371.0088
DEFAULT_RADIUS_KM = 50.0
EVIDENCE_POINTS = {"capability": 60, "procedure": 58, "description": 56,
                   "specialties": 54, "equipment": 52}
# Exact search aliases from Stage 06. Broad related terms deliberately do not match.
SERVICE_ALIASES = {
    "dialysis": ("hemodialysis", "haemodialysis", "peritoneal dialysis", "renal dialysis"),
    "mri": ("magnetic resonance imaging",),
    "nicu": ("neonatal icu", "neonatal intensive care", "newborn intensive care"),
    "emergency_c_section": (
        "emergency cesarean", "emergency caesarean", "emergency cesarean delivery",
        "emergency caesarean section",
    ),
    "cardiac_surgery": ("heart surgery", "open heart surgery", "coronary artery bypass grafting"),
    "trauma_emergency_care": (
        "trauma and emergency care", "trauma care", "trauma center", "trauma centre",
        "emergency care", "emergency department", "accident and emergency",
    ),
}


def _service_key(value):
    name = _place(value)
    for key, aliases in SERVICE_ALIASES.items():
        if name == _place(key) or name in aliases:
            return key
    return None


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if isfinite(number) else None


def _coordinates(lat, lon):
    lat, lon = _number(lat), _number(lon)
    if lat is not None and lon is not None and abs(lat) <= 90 and abs(lon) <= 180:
        return lat, lon
    return None


def haversine_km(origin, destination):
    """Great-circle distance between two validated (latitude, longitude) pairs."""
    lat1, lon1 = map(radians, origin)
    lat2, lon2 = map(radians, destination)
    a = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * asin(sqrt(min(1, max(0, a))))


def _parse_location(location):
    if isinstance(location, str):
        parts = [part.strip() for part in location.split(",")]
        if len(parts) == 2 and all(re.fullmatch(r"[+\-\d.eE]+", part) for part in parts):
            location = dict(zip(("latitude", "longitude"), parts))
        elif len(parts) == 2:
            known_city = (_place(parts[0]), state_key(parts[1])) in AUDITED_CITIES
            location = {"city" if known_city else "place": parts[0], "state": parts[1]}
        elif re.fullmatch(r"[0-9]+", location.strip()):
            location = {"pin": location.strip()}
        else:
            location = {"place": location}
    if isinstance(location, (list, tuple)) and len(location) == 2:
        location = dict(zip(("latitude", "longitude"), location))
    if not isinstance(location, dict):
        raise ValueError("Provide a city, district, PIN or latitude and longitude.")
    modes = ({"latitude", "longitude"}, {"pin"}, {"city", "district", "place"})
    mode_count = sum(bool(mode & location.keys()) for mode in modes)
    if "pin" in location and modes[2] & location.keys() == {"district"}:
        mode_count -= 1
    if mode_count != 1:
        raise ValueError("Provide one location type per search.")
    if modes[0] & location.keys():
        origin = _coordinates(location.get("latitude"), location.get("longitude"))
        if origin is None:
            raise ValueError("Provide valid latitude and longitude.")
        return ("coordinates", origin, "", "")
    state = state_key(location.get("state"))
    if "state" in location and not state:
        raise ValueError("Enter an Indian state or union territory in the State field. Use District for a district name.")
    district = district_key(location.get("district"))
    if "district" in location and not district:
        raise ValueError("Enter a district name in the District field.")
    if "pin" in location:
        pin = str(location["pin"]).strip()
        if not re.fullmatch(r"[1-9][0-9]{5}", pin):
            raise ValueError("Provide a valid six-digit Indian PIN.")
        return ("pin", pin, state, district)
    fields = modes[2] & location.keys()
    if fields == {"city", "district"}:
        fields = {"city"}
    if len(fields) != 1:
        raise ValueError("Provide one place name.")
    field = next(iter(fields))
    place = district if field == "district" else _place(location[field])
    district = district if field == "city" else ""
    if not place:
        raise ValueError("Provide a place name and an optional state.")
    return (field, place, state, district)


def _evidence_issues(row):
    issues = []
    if row.get("evidence_status") != "supported_claim":
        issues.append("evidence_" + str(row.get("evidence_status") or "unknown"))
    if row.get("claim_type") != "candidate_claim" or row.get("has_service_negation") is not False:
        issues.append("offering_not_supported")
    if (row.get("identity_review_count") != 1 or row.get("clear_identity_review_count") != 1
            or row.get("identity_reason_codes") != []):
        issues.append("facility_attribution_uncertain")
    if row.get("verification_reason") != "attributable_dataset_offering_text":
        issues.append("verification_reason_not_supportive")
    return issues


def _citation(row):
    claim, source = row.get("claim_text"), row.get("source_text")
    if (row.get("source_field") not in EVIDENCE_POINTS or not isinstance(claim, str)
            or not claim.strip() or not isinstance(source, str) or claim not in source):
        return None
    return {
        "record_id": row["facility_id"],
        "source_field": row["source_field"],
        "claim_text": claim,
        "source_content_id": row.get("source_content_id"),
        "matched_aliases": row.get("matched_aliases"),
        "source_urls_raw": row.get("source_urls_raw"),
        "source_attribution_status": row.get("source_attribution_status"),
    }


def _location_assessment(row, kind, origin, radius):
    if (row.get("admin_status") == "possible_admin_conflict"
            or row.get("pin_state_disagreement") or row.get("country_disagreement")):
        return None, "possible_admin_conflict"
    if not row.get("geo_facility_id") or row.get("admin_status") not in {
        "dataset_address_unverified", "incomplete_address",
    }:
        return None, "unresolved_geography"
    if kind != "coordinates":
        if (row.get("geo_status") not in {"bbox_pass", "missing_coordinates"}
                or row.get("geo_status") == "bbox_pass"
                and _coordinates(row.get("candidate_latitude"), row.get("candidate_longitude")) is None):
            return None, "coordinate_quality_requires_review"
        return None, None
    hold = load_coordinate_holds().get(row.get("facility_id"))
    if hold:
        return None, hold["reason"]
    if row.get("geo_status") != "bbox_pass" or row.get("coordinate_provenance") != "dataset_coordinates_bbox_only":
        return None, "coordinate_quality_requires_review"
    point = _coordinates(row.get("candidate_latitude"), row.get("candidate_longitude"))
    if point is None or not (6 <= point[0] <= 38 and 68 <= point[1] <= 98):
        return None, "missing_or_invalid_coordinates"
    distance = haversine_km(origin, point)
    return distance, "outside_radius" if distance > radius else None


def recommend(query, rows, *, postal_offices=(), audit=None, review_holds=None, district_regions=None):
    """Return JSON-ready recommendations, review candidates and abstention reasons.

    Supply complete Stage 09 rows as mappings, including other services when
    resolving place-name ambiguity. Location supports text or an explicit
    city/district, pin, or latitude/longitude mapping. Radius is in km and
    applies to coordinate searches. Optional postal office mappings contain
    pin, state and district. Unknown input produces an abstention.
    If provided, audit is populated with measurements from the executed stages.
    Explicit review_holds overrides the packaged registry for comparison replays.
    district_regions may provide an authoritative, complete set of states for
    a particular district name. Unlisted districts remain unvalidated.
    """
    if audit is not None:
        audit.clear()
    service = _service_key(query.get("service"))
    result = {
        "status": "abstain", "reason": None, "service_key": service,
        "recommendations": [], "verification_required": [],
        "distance_assumptions": None,
        "evidence_scope": "Dataset reports only. Confirm service and location directly with the facility.",
    }
    if service is None:
        result["reason"] = "unsupported_service"
        return result
    try:
        kind, value, state, district = _parse_location(query.get("location"))
        radius = _number(query.get("radius_km", query.get("radius", DEFAULT_RADIUS_KM)))
        if kind == "coordinates" and (radius is None or radius <= 0):
            raise ValueError("Radius must be a positive finite number in km.")
        if kind != "coordinates" and ("radius_km" in query or "radius" in query):
            raise ValueError("A radius search requires latitude and longitude.")
    except ValueError as error:
        result.update(reason="invalid_location_or_radius", detail=str(error))
        return result

    result["distance_assumptions"] = (
        {"method": "haversine_straight_line", "earth_radius_km": EARTH_RADIUS_KM,
         "origin": list(value), "radius_km": radius,
         "note": "Facility coordinates pass a bounding-box check only. Confirm the location before travel."}
        if kind == "coordinates" else
        {"method": "dataset_address_exact_match", "coordinate_distance_computed": False,
         "note": "The dataset address is unverified. No location is inferred from a place name or PIN."}
    )
    if audit is not None:
        audit["parsed_location"] = {"mode": kind, "value": value, "state": state, "district": district}
    rows = list(rows)
    post = postal_index(postal_offices)
    addresses = {id(row): address(row, post) for row in rows}
    requested_district = value if kind == "district" else district
    validated_states = set()
    if requested_district:
        validated_states = {state_key(region) for label, regions in (district_regions or {}).items()
                            if district_key(label) == requested_district for region in regions} - {""}
        if kind == "pin" and len(post.get(value, ())) == 1:
            postal_state, postal_district = next(iter(post[value]))
            if postal_district == requested_district:
                validated_states = {postal_state}
        if state and validated_states and state not in validated_states:
            result.update(reason="invalid_state_district_combination",
                          detail="The validated district reference disagrees with the entered state or union territory.",
                          district_validation={"district": requested_district, "status": "invalid_combination"})
            return result
    if kind in {"place", "city", "district"}:
        scope_addresses = [admin for admin in addresses.values()
                           if not validated_states or admin["state"] in validated_states]
        choices = place_choices(scope_addresses, kind, value, state, district)
        if not choices:
            # A unique valid recorded region can still scope verification-only results.
            flagged = place_choices(scope_addresses, kind, value, state, district, allow_flagged=True)
            if len(flagged) == 1:
                choices = flagged
        if len(choices) > 1:
            regions = sorted({choice["state"] for choice in choices})
            result.update(reason="ambiguous_place", matching_states=regions, location_choices=choices)
            return result
        if choices:
            choice = choices[0]
            state = choice["state"]
            if kind == "place":
                kind = "city" if "city" in choice else "district"
            district = choice.get("district", "") if kind == "city" else ""
        elif not state:
            if requested_district:
                # A unique recorded city/state may scope review leads, never prove district membership.
                regions = {choice["state"] for choice in place_choices(scope_addresses, kind, value, "", "")}
                if kind == "city" and len(regions) == 1:
                    state = regions.pop()
            else:
                if audit is not None:
                    audit["stop_reason"] = "no_unambiguous_indexed_place"
                result["reason"] = "no_reliable_match"
                return result
        if kind == "place":
            kind = "city"
    elif kind == "pin":
        regions = {(admin["state"], admin["district"]) for row in rows
                   if (admin := addresses[id(row)])["resolution_allowed"]
                   and matches(row, admin, kind, value, state, district)}
        if post.get(value):
            regions = {(region, area) for region, area in post[value]
                       if (not state or region == state) and (not district or area == district)}
        specific = {region for region, area in regions if area}
        regions = {(region, area) for region, area in regions if area or region not in specific}
        if len(regions) > 1:
            result.update(reason="ambiguous_place", location_choices=[
                {"pin": value, "state": region, **({"district": area} if area else {})}
                for region, area in sorted(regions)
            ])
            return result

    if audit is not None:
        audit.update(location={"mode": kind, "value": value, "state": state, "district": district},
                     service_rows=0, invalid_id_rows=0, duplicate_rows=0,
                     unique_service_facilities=0, geographic_rows=0, geographic_facilities=0,
                     assessed_facilities=0, duplicates_held_before_range=0,
                     unmatched_address_facilities=0, screenings={})
    by_facility = defaultdict(list)
    for row in rows:
        facility_id = row.get("facility_id")
        if row.get("service_key") == service:
            if audit is not None:
                audit["service_rows"] += 1
            if isinstance(facility_id, str) and facility_id.strip():
                if row not in by_facility[facility_id]:
                    by_facility[facility_id].append(row)
                elif audit is not None:
                    audit["duplicate_rows"] += 1
            elif audit is not None:
                audit["invalid_id_rows"] += 1

    if audit is not None:
        audit["unique_service_facilities"] = len(by_facility)
    holds = load_review_holds() if review_holds is None else review_holds
    excluded = Counter()
    known_cities = sorted({admin["city"] for admin in addresses.values() if admin["city"]},
                          key=lambda city: (-len(city), city))
    district_matches = district_unresolved = 0
    for facility_id, records in sorted(by_facility.items()):
        matching = [row for row in records if kind == "coordinates"
                    or matches(row, addresses[id(row)], kind, value, state, district)]
        uncertain_district = False
        if requested_district:
            supported = [row for row in matching if addresses[id(row)]["resolution_allowed"]
                         and (not validated_states or addresses[id(row)]["state"] in validated_states)]
            if supported:
                matching = supported
                district_matches += 1
            else:
                if kind in {"city", "pin"} and (state or kind == "pin"):
                    matching = matching or [
                        row for row in records if not addresses[id(row)]["district"]
                        and matches(row, addresses[id(row)], kind, value, state)
                    ]
                if matching:
                    uncertain_district = True
                    district_unresolved += 1
        if not matching:
            if audit is not None:
                audit["unmatched_address_facilities"] += 1
            continue
        if len(records) > 1:
            if audit is not None and kind == "coordinates":
                audit["duplicates_held_before_range"] += 1
            if audit is not None and kind != "coordinates" and not uncertain_district:
                audit["geographic_rows"] += len(matching)
                audit["geographic_facilities"] += 1
            result["verification_required"].append({
                "facility_id": facility_id, "reason_codes": ["inconsistent_gold_rows"]
                + (["district_membership_unresolved"] if uncertain_district else []),
            })
            continue
        row = matching[0]
        distance, location_issue = _location_assessment(row, kind, value, radius)
        # Unlocatable rows from a nationwide coordinate search are not local leads.
        if kind == "coordinates" and location_issue:
            excluded[location_issue] += 1
            continue
        if audit is not None:
            if not uncertain_district:
                audit["geographic_rows"] += 1
                audit["geographic_facilities"] += 1
            audit["assessed_facilities"] += 1
        citation = _citation(row)
        issues = _evidence_issues(row)
        if citation is None:
            issues.append("missing_grounded_citation")
        admin = addresses[id(row)]
        if location_issue:
            issues.append(location_issue)
        if uncertain_district:
            issues.append("district_membership_unresolved")
        if admin["issue"]:
            issues.append(admin["issue"])
            distance = None
        branch_city = None
        other_facility_text = None
        alternate_site_text = None
        if citation and not issues:
            branch_city = claim_site_conflict(citation["claim_text"], admin["city"], known_cities)
            other_facility_text = other_named_facility(row.get("source_text"), row.get("name"))
            if branch_city:
                issues.append("claim_branch_city_conflict")
                distance = None
            if other_facility_text:
                issues.append("claim_field_other_facility_reference")
            if not branch_city:
                for text in row.get("sibling_site_claims") or ():
                    if text != citation["claim_text"] and claim_site_conflict(text, admin["city"], known_cities):
                        alternate_site_text = text
                        issues.append("alternate_claim_branch_scope_unresolved")
                        distance = None
                        break
        hold = review_hold(row, holds)
        if hold:
            issues.append(hold["reason_code"] if hold["status"] == "matched_evidence"
                          else "review_hold_source_revision")
            distance = None
        if audit is not None:
            audit["screenings"][facility_id] = {
                "location_issue": location_issue or admin["issue"] or
                ("district_membership_unresolved" if uncertain_district else None) or
                ("claim_branch_city_conflict" if branch_city else
                 "alternate_claim_branch_scope_unresolved" if alternate_site_text else None),
                "branch_city": branch_city,
                "other_facility_text": other_facility_text,
                "alternate_site_text": alternate_site_text,
                "claim_type": row.get("claim_type"),
                "claim_reason": row.get("claim_reason"),
                "has_service_negation": row.get("has_service_negation"),
                "identity_review_count": row.get("identity_review_count"),
                "clear_identity_review_count": row.get("clear_identity_review_count"),
                "identity_reason_codes": row.get("identity_reason_codes"),
                "verification_reason": row.get("verification_reason"),
                "source_text": row.get("source_text"),
            }
        candidate = {
            "facility_id": facility_id, "name": row.get("name"),
            "evidence_status": row.get("evidence_status"),
            "verification_reason": row.get("verification_reason"),
            "reason_codes": issues, "citation": citation, "distance_km": distance,
            "city": row.get("city_clean"), "district": row.get("district_clean"),
            "state": row.get("state_clean"), "pin": row.get("pin_clean"),
            "geo_status": row.get("geo_status"), "admin_status": row.get("admin_status"),
            "resolved_address": {key: value for key, value in admin.items() if key != "resolution_allowed"},
        }
        if hold:
            candidate["reviewed_hold"] = hold
        if issues:
            result["verification_required"].append(candidate)
            continue
        components = {
            "evidence": EVIDENCE_POINTS[row["source_field"]],
            "proximity": round(30 * max(0, 1 - distance / radius), 6) if distance is not None else 0,
            "address_consistency": 10 if row["admin_status"] == "dataset_address_unverified" else 0,
        }
        candidate.update(score=round(sum(components.values()), 6), score_components=components)
        result["recommendations"].append(candidate)

    result["excluded_location_counts"] = dict(sorted(excluded.items()))
    result["recommendations"].sort(key=lambda row: (-row["score"], row["facility_id"]))
    result["scoring"] = {
        "evidence_points_by_source_field": dict(EVIDENCE_POINTS),
        "proximity": "30 points at the origin, decreasing linearly to zero at the radius.",
        "address_consistency": "10 points for a recorded city and state with no flagged administrative conflict.",
        "tie_breaker": "Facility ID ascending.",
        "interpretation": "Heuristic ranking points for dataset evidence and location.",
    }
    if requested_district:
        result["district_validation"] = {"district": requested_district,
                                         "status": "matched" if district_matches else "unresolved",
                                         "matched_facilities": district_matches,
                                         "unresolved_facilities": district_unresolved}
    if result["recommendations"]:
        result["status"] = "ranked"
    elif requested_district and not district_matches:
        result.update(reason="district_membership_unresolved",
                      detail="Available address evidence could not establish a match to the requested district. "
                             "Facility coverage within the district remains unknown.")
    else:
        result["reason"] = "no_reliable_match"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", help="JSON file with query and rows keys. Defaults to stdin.")
    args = parser.parse_args()
    if args.input:
        with open(args.input, encoding="utf-8") as source:
            payload = json.load(source)
    else:
        payload = json.load(sys.stdin)
    json.dump(recommend(payload["query"], payload["rows"], postal_offices=payload.get("postal_offices", ()),
                        district_regions=payload.get("district_regions")), sys.stdout, indent=2, allow_nan=False)
    print()


if __name__ == "__main__":
    main()
