"""Conservative checks on grounded Gold text. Findings trigger review only."""
import json
import re

from careproof.location import normalize


# Bind a terminal site clause to a positive offering, never a free city mention.
SITE_CLAUSE = re.compile(
    r"^(?:offers|provides|performs|we offer|we provide|we perform) "
    r"(?P<offering>.+?) (?:at|in) (?P<site>[\w]+(?: [\w]+){0,6})$"
)
INCIDENTAL_SITE_WORDS = {"at", "in", "near", "from", "with", "for", "through", "partnership",
                         "collaboration", "study", "research", "referral", "referrals", "referred"}
FACILITY_DECLARATION = re.compile(
    r"^(?P<name>[\w]+(?: [\w]+){0,6} (?:diagnostic cent(?:re|er)|medical cent(?:re|er)|hospital|clinic)) "
    r"(?:is listed as|is) (?:a|an) (?:[\w]+ ){0,4}"
    r"(?:diagnostic cent(?:re|er)|medical cent(?:re|er)|hospital|clinic)(?: |$)"
)


def claim_site_conflict(claim, record_city, known_cities):
    match = SITE_CLAUSE.fullmatch(normalize(claim))
    if not match or not record_city:
        return None
    site = match["site"]
    if INCIDENTAL_SITE_WORDS.intersection((site + " " + match["offering"]).split()):
        return None
    # Prefer the longest known suffix, e.g. New Delhi over Delhi.
    city = next((city for city in known_cities if site == city or site.endswith(" " + city)), None)
    if city and city != record_city and {city, record_city} != {"delhi", "new delhi"}:
        return city
    return None


def other_named_facility(source, record_name):
    """Look for an independent facility declaration inside the selected claim's field."""
    own_tokens = normalize(record_name).replace("centre", "center").split()
    own = "".join(own_tokens)
    if not own or not isinstance(source, str) or not source.lstrip().startswith("["):
        return None
    try:
        spans = json.loads(source)
    except ValueError:
        return None
    if not isinstance(spans, list):
        return None
    for span in spans:
        match = FACILITY_DECLARATION.match(normalize(span))
        if not match:
            continue
        name_tokens = match["name"].replace("centre", "center").split()
        if name_tokens[0] in {"a", "an", "our", "the", "this"}:
            continue
        name = "".join(name_tokens)
        # A shared two-word brand prefix may describe a renamed branch.
        if len(own_tokens) >= 2 and own_tokens[:2] == name_tokens[:2]:
            continue
        generic = {"diagnosticcenter", "medicalcenter", "multispecialityhospital", "districthospital"}
        if name not in generic and not (own in name or name in own):
            return span
    return None
