"""Does a quote describe THIS facility? (ported from CareProof's attribution guards)

Records were assembled from directory pages, so a capability line can describe another hospital
("Madras Medical Mission, Chennai is listed as a referral hospital…") or another branch city
("Provides dialysis at Namakkal"). Such quotes must not count as evidence for this record.

Also loads CareProof's analyst review holds: facility x service pairs a person reviewed and withheld,
with the reason and quotes (review_holds.json, coordinate_holds.json).
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from .text import norm

# "<Name> Hospital is (listed as) a … hospital"  -> an independent facility declaration
FACILITY_DECLARATION = re.compile(
    r"^(?P<name>[a-z0-9]+(?: [a-z0-9]+){0,6} (?:diagnostic cent(?:re|er)|medical cent(?:re|er)|hospitals?|clinic|nursing home|medical college))"
    r" (?:is listed (?:as|under)|is (?:a|an|the)|operates as)\b"
)
# "Offers/provides <service> at|in <place>" -> a site clause naming where the service is
SITE_CLAUSE = re.compile(r"^(?:offers|provides|performs|we offer|we provide|we perform) (?P<offering>.+?) (?:at|in) (?P<site>[a-z0-9]+(?: [a-z0-9]+){0,6})$")
INCIDENTAL = {"at", "in", "near", "from", "with", "for", "through", "partnership", "collaboration", "study", "research",
              "referral", "referrals", "referred", "association", "tie", "up"}
GENERIC_NAMES = {"diagnosticcenter", "medicalcenter", "multispecialityhospital", "multispecialtyhospital", "districthospital",
                 "hospital", "clinic", "generalhospital", "privatehospital", "governmenthospital"}


def _tokens(name: str) -> list[str]:
    return norm(name).replace("centre", "center").split()


def other_facility(quote: str, record_name: str) -> str | None:
    """Name of another facility the quote declares, or None."""
    m = FACILITY_DECLARATION.match(norm(quote))
    if not m:
        return None
    own_t, name_t = _tokens(record_name), m["name"].replace("centre", "center").split()
    if not own_t or name_t[0] in {"a", "an", "our", "the", "this", "it"}:
        return None
    own, name = "".join(own_t), "".join(name_t)
    if name in GENERIC_NAMES or own in name or name in own:
        return None
    if " ".join(own_t) in norm(quote).replace("centre", "center"):  # "X Clinic is a unit of <own name>"
        return None
    if len(own_t) >= 2 and own_t[:2] == name_t[:2]:  # shared brand prefix: renamed branch
        return None
    return m["name"].title()


def other_site(quote: str, record_city: str, known_places: set[str]) -> str | None:
    """City named as the service's location when it differs from the record's city, or None."""
    m = SITE_CLAUSE.fullmatch(norm(quote))
    if not m or not record_city:
        return None
    site = m["site"]
    if INCIDENTAL & set((site + " " + m["offering"]).split()):
        return None
    place = next((p for p in sorted(known_places, key=len, reverse=True) if site == p or site.endswith(" " + p)), None)
    if place and place != record_city and {place, record_city} != {"delhi", "new delhi"}:
        return place.title()
    return None


# CareProof service keys -> findcare service ids
HOLD_SERVICE_MAP = {"dialysis": ["dialysis"], "mri": ["mri"], "nicu": ["nicu"], "emergency_c_section": ["emergency_csection"],
                    "cardiac_surgery": ["cardiac_surgery"], "trauma_emergency_care": ["trauma", "emergency"]}
_DATA = Path(__file__).with_name("data")


@lru_cache(maxsize=1)
def review_holds() -> dict[tuple[str, str], dict]:
    """(facility_id, service_id) -> analyst hold {reason, reason_code, review_status, evidence[], provenance}."""
    p = _DATA / "review_holds.json"
    if not p.exists():
        return {}
    out = {}
    for e in json.loads(p.read_text(encoding="utf-8")):
        for sid in HOLD_SERVICE_MAP.get(e["service_key"], []):
            out[(e["facility_id"], sid)] = {k: e.get(k) for k in ("reason", "reason_code", "review_status", "evidence", "provenance", "selected_excerpt")}
    return out


@lru_cache(maxsize=1)
def coordinate_holds() -> dict[str, dict]:
    p = _DATA / "coordinate_holds.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
