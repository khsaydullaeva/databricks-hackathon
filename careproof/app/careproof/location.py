"""Administrative address matching with optional India Post office evidence."""

import re
import unicodedata
from collections import defaultdict


def normalize(value):
    if not isinstance(value, str):
        return ""
    text = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[\W_]+", " ", text).strip()


# Government of India state and UT names. See reports/post_deployment_regression.md.
INDIAN_STATES_AND_UTS = frozenset({
    "andhra pradesh", "arunachal pradesh", "assam", "bihar", "chhattisgarh",
    "goa", "gujarat", "haryana", "himachal pradesh", "jharkhand", "karnataka",
    "kerala", "madhya pradesh", "maharashtra", "manipur", "meghalaya", "mizoram",
    "nagaland", "odisha", "punjab", "rajasthan", "sikkim", "tamil nadu",
    "telangana", "tripura", "uttar pradesh", "uttarakhand", "west bengal",
    "andaman and nicobar islands", "chandigarh", "dadra and nagar haveli and daman and diu",
    "delhi", "jammu and kashmir", "ladakh", "lakshadweep", "puducherry",
})

STATE_ALIASES = {
    "tamilnadu": "tamil nadu",
    "telengana": "telangana",
    "chattisgarh": "chhattisgarh",
    "uttar prades h": "uttar pradesh",
    "uttaranchal": "uttarakhand",
    "orissa": "odisha",
    "pondicherry": "puducherry",
    "jammu kashmir": "jammu and kashmir",
    "m p": "madhya pradesh",
    "mp": "madhya pradesh",
    "nct of delhi": "delhi",
    "n c t of delhi": "delhi",
    "n c t delhi": "delhi",
    "nct delhi": "delhi",
    "delhi nct": "delhi",
    "national capital territory of delhi": "delhi",
    "u p": "uttar pradesh",
    "up": "uttar pradesh",
    "br": "bihar",
    "mh": "maharashtra",
    "rj": "rajasthan",
}

# The keys pair the reported city with an administrative name found in the
# state slot. See reports/location_root_cause.md for the administrative sources.
LOCALITY_IN_STATE = {
    ("delhi", "west delhi"): ("delhi", "west delhi"),
    ("new delhi", "west delhi"): ("delhi", "west delhi"),
    ("lucknow", "gomti nagar"): ("uttar pradesh", ""),
    ("mumbai", "mumbai"): ("maharashtra", ""),
    ("mumbai", "palghar"): ("maharashtra", "palghar"),
}

CITY_DISTRICTS = {
    ("lucknow", "uttar pradesh"): {"lucknow"},
    ("patna", "bihar"): {"patna"},
    ("mumbai", "maharashtra"): {"mumbai", "mumbai city", "mumbai suburban"},
}

AUDITED_CITIES = {
    ("jaipur", "rajasthan"), ("delhi", "delhi"), ("new delhi", "delhi"),
    ("lucknow", "uttar pradesh"), ("patna", "bihar"), ("mumbai", "maharashtra"),
}


def state_key(value):
    name = normalize(value.replace("&", " and ") if isinstance(value, str) else value)
    state = STATE_ALIASES.get(name, name)
    return state if state in INDIAN_STATES_AND_UTS else ""


def district_key(value):
    """Normalize administrative labels without guessing district aliases."""
    name = normalize(value).removeprefix("district of ")
    return re.sub(r"(?:^| )(?:district|dist)$", "", name).strip()


def postal_index(offices):
    """Group distinct administrative pairs by PIN, retaining disagreements."""
    index = defaultdict(set)
    for office in offices:
        pin = str(office.get("pin", "")).strip()
        state = state_key(office.get("state"))
        district = district_key(office.get("district"))
        if re.fullmatch(r"[1-9][0-9]{5}", pin) and state:
            index[pin].add((state, district))
    return index


def address(row, post):
    city = normalize(row.get("city_clean"))
    raw_state = normalize(row.get("state_clean"))
    state, district_hint = LOCALITY_IN_STATE.get(
        (city, raw_state), (state_key(raw_state), "")
    )
    district = district_key(row.get("district_clean")) or district_hint
    pin = row.get("pin_clean")
    postal = post.get(pin) if row.get("pin_status") == "format_valid_unverified" else None
    issue = "invalid_recorded_state_or_ut" if raw_state and not state else None
    provenance = "dataset_address"
    if postal and state:
        if len(postal) == 1:
            postal_state, postal_district = next(iter(postal))
            if postal_state != state or (district and postal_district and district != postal_district):
                issue = "postal_admin_conflict"
            elif postal_district and not district:
                district = postal_district
                provenance = "postal_pin_district"
        else:
            issue = "postal_admin_ambiguous"

    expected_districts = CITY_DISTRICTS.get((city, state))
    if ((district_hint or provenance == "postal_pin_district")
            and expected_districts and district not in expected_districts):
        issue = "city_district_conflict"

    # Audited contradictions in the source. Preserve both the address and the
    # reason for review, including when a fresh postal directory is unavailable.
    if city == "lucknow" and pin == "275101":
        issue = "city_pin_conflict"
    if (city, raw_state, pin) == ("lucknow", "gomti nagar", "226001"):
        issue = "locality_pin_conflict"
    if (city, state, pin) == ("delhi", "haryana", "110007"):
        issue = "postal_admin_conflict"
    return {
        "city": city, "state": state, "district": district,
        "district_provenance": provenance if district else None,
        "issue": issue,
        "resolution_allowed": bool(state and not issue
                                   and row.get("admin_status") != "possible_admin_conflict"
                                   and not row.get("pin_state_disagreement")
                                   and not row.get("country_disagreement")),
    }


def matches(row, admin, kind, value, state, district=""):
    if state and admin["state"] != state:
        return False
    if district and admin["district"] != district:
        return False
    if kind == "pin":
        return row.get("pin_clean") == value and row.get("pin_status") == "format_valid_unverified"
    if kind == "city":
        return admin["city"] == value or (
            value == "delhi" and admin["city"] == "new delhi" and admin["state"] == "delhi"
        )
    return admin[kind] == value


def place_choices(addresses, kind, value, state, district, *, allow_flagged=False):
    """Use distinct administrative places across services, independent of row count."""
    names = set()
    for admin in addresses:
        if not admin["state"] or admin["issue"] or (not allow_flagged and not admin["resolution_allowed"]):
            continue
        for mode in (("city", "district") if kind == "place" else (kind,)):
            if matches({}, admin, mode, value, state, district):
                locality = admin["district"] if mode == "city" else ""
                if (value, admin["state"]) in AUDITED_CITIES and not district:
                    locality = ""
                names.add((mode, value, admin["state"], locality))
    # An unknown district cannot itself establish a separate same-state city.
    specific = {item[:3] for item in names if item[3]}
    return [
        {mode: place, "state": region, **({"district": locality} if locality else {})}
        for mode, place, region, locality in sorted(names)
        if locality or (mode, place, region, locality)[:3] not in specific
    ]
