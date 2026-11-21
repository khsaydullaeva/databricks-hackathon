"""CareProof facility search for Streamlit."""
from html import escape
import sys
from pathlib import Path

import streamlit as st

APP = Path(__file__).resolve().parent
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))
from data import DataUnavailable, EVIDENCE_PAGE, connect, search, source_links
from decision_trace import build_trace

SERVICES = {
    "Dialysis": "dialysis", "MRI": "mri", "NICU": "nicu",
    "Emergency C-section": "emergency_c_section", "Cardiac surgery": "cardiac_surgery",
    "Trauma and emergency care": "trauma_emergency_care",
}
PAGE = 5
REASONS = {
    "evidence_conflicting": "The record contains conflicting service or facility information.",
    "evidence_rejected": "The evidence includes an explicit denial of the service.",
    "evidence_uncertain": "The recorded text leaves the offering uncertain.",
    "offering_not_supported": "The text does not establish a service offering.",
    "facility_attribution_uncertain": "The record may refer to another facility or branch.",
    "verification_reason_not_supportive": "The claim review did not establish an attributable offering.",
    "coordinate_quality_requires_review": "Coordinates require review.",
    "possible_admin_conflict": "The recorded address contains administrative discrepancies.",
    "city_pin_conflict": "The recorded city and PIN disagree.",
    "locality_pin_conflict": "The recorded locality and PIN disagree.",
    "city_district_conflict": "The recorded city and district disagree.",
    "postal_admin_conflict": "The recorded postal and administrative details disagree.",
    "missing_grounded_citation": "No exact supporting excerpt could be established.",
}
ATTRIBUTION = {
    "unverified_row_leads": "Unverified record-level source leads",
    "no_source_urls": "No source URLs recorded",
}


@st.cache_resource
def warehouse():
    return connect()


@st.cache_data(ttl=900, max_entries=1)
def addresses():
    return warehouse().addresses()


@st.cache_data(ttl=300, max_entries=6)
def service_rows(service):
    return warehouse().service(service)


@st.cache_data(ttl=300, max_entries=128)
def evidence(service, facility, offset):
    return warehouse().evidence(service, facility, offset)[0]


def heading(candidate):
    return candidate.get("name") or f"Facility {candidate['facility_id']}"


def choice_label(choice):
    return " · ".join(f"{key.title()} {value.title()}" for key, value in choice.items())


def citation(item):
    record = item.get("record_id") or item.get("facility_id")
    provenance = f"Field {item['source_field']} · Record {record}"
    if item.get("source_content_id"):
        provenance += f" · Source content ID {item['source_content_id']}"
    status = ATTRIBUTION.get(item.get("source_attribution_status"), "Source attribution unavailable")
    urls = source_links(item.get("source_urls_raw"))
    leads = ""
    if urls:
        links = "".join(
            f'<a href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">Lead {i} ↗</a>'
            for i, url in enumerate(urls, 1)
        )
        leads = (f'<details class="cp-leads"><summary>Unverified source leads ({len(urls)})</summary>'
                 f'<div class="cp-links">{links}</div></details>')
    availability = status if urls or item.get("source_attribution_status") != "unverified_row_leads" else (
        f"{status} · No usable web links"
    )
    st.html(f'<div class="cp-citation"><blockquote>{escape(item["claim_text"])}</blockquote>'
            f'<p>{escape(provenance)}<br>{escape(availability)}</p>{leads}</div>')


def show_evidence(rows, service, scope):
    with st.expander("Source evidence and conflicting claims"):
        facilities = {f"{heading(row)} · {row['facility_id']}": row["facility_id"] for row in rows}
        choice = st.selectbox("Facility evidence", list(facilities), index=None,
                              placeholder="Choose a facility", key=f"{scope}_facility")
        if choice is None:
            return
        selected = facilities[choice]
        page = st.number_input("Evidence page", min_value=1, step=1, key=f"{scope}_{selected}_evidence")
        try:
            with st.spinner("Loading exact claim records…"):
                claims = evidence(service, selected, (page - 1) * EVIDENCE_PAGE)
        except DataUnavailable as error:
            st.warning(str(error))
            return
        if not claims:
            st.info("No evidence entries on this page.")
        for claim in claims[:EVIDENCE_PAGE]:
            with st.container(border=True):
                st.caption(f"{claim['evidence_status'].replace('_', ' ').capitalize()} · "
                           f"{claim['verification_reason'].replace('_', ' ')}")
                citation(claim)
                st.caption("Full recorded field")
                st.text(claim["source_text"])
        if len(claims) > EVIDENCE_PAGE:
            st.caption(f"More claims are available on page {page + 1}.")


def recorded_location(candidate):
    parts = [candidate.get(key) for key in ("city", "district", "state", "pin")]
    return " · ".join(dict.fromkeys(part for part in parts if part)) or "Incomplete"


def geography_detail(candidate, assumptions):
    distance = candidate.get("distance_km")
    if distance is not None:
        return f"{distance:.1f} km straight-line within the {assumptions['radius_km']:g} km search radius."
    if assumptions.get("method") == "haversine_straight_line":
        return "No usable distance for this lead."
    if candidate.get("geo_status") == "missing_coordinates":
        return "Matched by recorded address. Facility coordinates are missing."
    return "Matched by recorded address. No coordinate distance was computed."


def show_reasons(candidate):
    for reason in candidate["reason_codes"]:
        st.write(REASONS.get(reason, reason.replace("_", " ").capitalize() + "."))


def card(candidate, assumptions, position=None):
    with st.container(border=True):
        rank = f'<span class="cp-rank">Rank {position} · Dataset-supported claim</span>' if position else ""
        distance = candidate.get("distance_km")
        location = f"Recorded location · {recorded_location(candidate)}"
        if distance is not None:
            location += f" · {distance:.1f} km straight-line"
        elif candidate.get("geo_status") == "missing_coordinates":
            location += " · Coordinates missing"
        st.html(f'<div class="cp-heading">{rank}<h3>{escape(heading(candidate))}</h3>'
                f'<p>{escape(location)}</p></div>')
        claim = candidate.get("citation")
        if claim:
            citation(claim)
        if position:
            with st.expander("Why this hospital?", type="compact"):
                points = candidate["score_components"]
                st.write(f'**Evidence · {points["evidence"]:g} points.** The ranker found an attributable service '
                         f'claim in the recorded {claim["source_field"]} field.')
                st.write(f'**Proximity · {points["proximity"]:g} points.** {geography_detail(candidate, assumptions)}')
                address = ("The index records a city and state without a flagged administrative conflict."
                           if points["address_consistency"] else "The index awarded no address consistency points.")
                st.write(f'**Address consistency · {points["address_consistency"]:g} points.** {address}')
                st.caption(f'{candidate["score"]:g} total ranking points. Confirm the offering and branch directly.')
        else:
            show_reasons(candidate)


def show_group(rows, service, scope, assumptions):
    pages = (len(rows) + PAGE - 1) // PAGE
    page = st.number_input(f"{scope.title()} page", min_value=1, max_value=pages, step=1,
                           key=f"{scope}_page") if pages > 1 else 1
    offset = (page - 1) * PAGE
    visible = rows[offset:offset + PAGE]
    for index, candidate in enumerate(visible, offset + 1):
        card(candidate, assumptions, index if scope == "ranked" else None)
    show_evidence(visible, service, scope)


def comparison(ranked, reviews, assumptions):
    if not ranked or len(ranked) + len(reviews) < 2:
        return
    with st.expander("Why not this hospital?"):
        alternatives = {f"{heading(row)} · {row['facility_id']}": row for row in ranked[1:] + reviews}
        selected = st.selectbox("Compare the top result with", list(alternatives), index=None,
                                placeholder="Choose a ranked facility or verification lead")
        if selected is None:
            return
        other, top = alternatives[selected], ranked[0]
        for label, row in (("Top result", top), ("Alternative", other)):
            st.text(f"{label} · {heading(row)} · {recorded_location(row)}")
            claim = row.get("citation")
            if claim:
                status = row.get("evidence_status")
                status = ("dataset-supported claim" if status == "supported_claim"
                          else (status or "unknown").replace("_", " "))
                st.caption(f'Evidence · {status} · recorded {claim["source_field"]} field')
            else:
                st.caption("Evidence · No grounded excerpt available")
            st.caption(geography_detail(row, assumptions))
        if "score_components" in other:
            st.table([{"Component": label.replace("_", " ").title(),
                       "Top result": value, "Alternative": other["score_components"][label]}
                      for label, value in top["score_components"].items()] +
                     [{"Component": "Total", "Top result": top["score"], "Alternative": other["score"]}])
            if top["score"] == other["score"]:
                st.caption("Scores tied. The engine orders equal scores by facility record ID.")
        else:
            st.write("The alternative stayed in the verification list because it did not meet eligibility criteria.")
            show_reasons(other)
        st.caption("The comparison reflects dataset evidence and geography. Confirm current services with the facility.")


def form():
    mode = st.radio("Search location by", ["City", "District", "Recorded PIN", "Coordinates"], horizontal=True)
    with st.form("search"):
        service, place = st.columns([1, 2])
        with service:
            choice = st.selectbox("Medical service", list(SERVICES))
        with place:
            if mode == "Coordinates":
                lat, lon = st.columns(2)
                location = {"latitude": lat.text_input("Latitude"), "longitude": lon.text_input("Longitude")}
                radius = st.number_input("Radius in km", min_value=1.0, value=50.0)
            else:
                kind = "pin" if mode == "Recorded PIN" else mode.lower()
                location = {kind: st.text_input(mode, placeholder={"City": "Jaipur", "District": "District name",
                            "Recorded PIN": "Six-digit Indian PIN"}[mode])}
                state = st.text_input("State or union territory (optional)")
                if state.strip():
                    location["state"] = state
                if mode in {"City", "Recorded PIN"}:
                    district = st.text_input("District (optional)")
                    if district.strip():
                        location["district"] = district
                if mode == "Recorded PIN":
                    st.caption("Matches recorded PIN text. Postal lookup and PIN geocoding are unavailable.")
        submitted = st.form_submit_button("Find facilities", type="primary")
    query = {"service": SERVICES[choice], "location": location}
    if mode == "Coordinates":
        query["radius_km"] = radius
    return submitted, choice, query


def show_trace(trace, label):
    with st.expander("How CareProof reached this result"):
        request, geo = trace["request"], trace["geography"]
        retrieval, assessed, decision = trace["retrieval"], trace["evidence"], trace["decision"]
        st.markdown("**1. Request understanding**")
        st.text(f"Service {label} → {request['service_key'] or 'unknown'}")
        st.text(choice_label(request["location"]))
        st.markdown("**2. Geographic matching**")
        assumptions = geo["assumptions"]
        if assumptions:
            st.write(assumptions["note"])
            if "radius_km" in assumptions:
                st.write(f"Great-circle radius used · {assumptions['radius_km']:g} km.")
        if geo["choices"]:
            st.text("Matching places · " + " / ".join(map(choice_label, geo["choices"])))
        for code, count in geo["coordinate_exclusions"].items():
            st.text(f"{code.replace('_', ' ').capitalize()} · {count}")
        if not assumptions:
            st.caption("Location matching stopped before the ranker could assess candidates.")
        st.markdown("**3. Candidate retrieval**")
        st.write(f"{retrieval['gold_rows']} Gold rows for the service before geographic filtering. "
                 f"The app supplied {retrieval['address_rows']} distinct Gold address records for place resolution.")
        st.markdown("**4. Claim evidence and conflicts**")
        st.caption("Evidence states come from dataset claim assessment. Source URLs are unverified leads.")
        for state, count in sorted(assessed["retrieved_states"].items()):
            st.text(f"Retrieved · {state.replace('_', ' ')} · {count}")
        for reason, count in sorted(assessed["review_reasons"].items()):
            st.text(f"Verification list · {reason.replace('_', ' ')} · {count}")
        if assessed["review_leads"]:
            leads = {f"{heading(row)} · {row['facility_id']}": row for row in assessed["review_leads"]}
            selected = st.selectbox("Inspect verification lead", list(leads), index=None)
            if selected is not None:
                lead = leads[selected]
                show_reasons(lead)
                if lead.get("citation"):
                    citation(lead["citation"])
        st.markdown("**5. Ranking calculations**")
        if trace["ranking"]:
            ranked = {f"{heading(row)} · {row['facility_id']}": row for row in trace["ranking"]}
            row = ranked[st.selectbox("Inspect ranked evidence and score", list(ranked))]
            admin = row["resolved_address"]
            st.text("Resolved address fields · " + " · ".join(
                admin[key] for key in ("city", "district", "state") if admin[key]))
            points = row["score_components"]
            st.write(f'{row["score"]:g} total · evidence {points["evidence"]:g} · '
                     f'proximity {points["proximity"]:g} · address consistency {points["address_consistency"]:g}')
            st.caption(geography_detail(row, assumptions))
            citation(row["citation"])
        else:
            st.caption("The ranker returned no eligible candidates to score.")
        st.markdown("**6. Final decision**")
        st.write(f'{decision["ranked"]} ranked · {decision["review"]} require verification')
        if decision["reason"]:
            st.text(f'Abstained · {decision["reason"].replace("_", " ")}')
        if decision["detail"]:
            st.write(decision["detail"])
        st.caption("Confirm the service and branch directly. Ranking points describe dataset evidence and location.")


def run_search(query):
    with st.spinner("Checking the CareProof index. The warehouse may take a moment to start…"):
        lookup, lookup_seconds = addresses()
        rows, service_seconds = service_rows(query["service"])
        result = search(query, lookup, rows)
        result["decision_trace"] = build_trace(query, lookup, rows, result)
    return result, lookup_seconds + service_seconds


def main():
    st.set_page_config(page_title="CareProof | Facility evidence", page_icon="✚", layout="wide")
    st.html("""<style>
        .block-container {max-width: 1100px; padding-top: 2.4rem;}
        [data-testid="stCaptionContainer"] {line-height: 1.5;}
        [data-testid="stText"] pre {white-space: pre-wrap; overflow-wrap: anywhere;}
        h1 {letter-spacing: -0.05em;}
        .cp-heading h3 {padding: .2rem 0; margin: 0;}
        .cp-heading p, .cp-citation p {margin: 0; overflow-wrap: anywhere; font-size: .875rem;}
        .cp-rank {display: inline-block; padding: .2rem .55rem; border-radius: .35rem;
                  background: #e1f1eb; color: #075a4b; font-size: .875rem; font-weight: 650;}
        .cp-citation blockquote {white-space: pre-wrap; overflow-wrap: anywhere; margin: 0 0 .45rem;
                                border-left: .2rem solid #79a69d; padding: .15rem .7rem;}
        .cp-leads {margin-top: .4rem; font-size: .875rem;}
        .cp-leads summary {cursor: pointer;}
        .cp-links {display: flex; flex-wrap: wrap; gap: .15rem .8rem; padding: .4rem 0;}
        </style>""")
    st.title("CareProof")
    st.markdown("### Find facilities by service and location")
    st.write("Review what the Virtue Foundation dataset reports and what you’ll need to confirm with the facility.")
    submitted, label, query = form()
    if submitted:
        st.session_state.pop("active_search", None)
    if submitted or "active_search" in st.session_state:
        if submitted:
            try:
                result, seconds = run_search(query)
            except DataUnavailable as error:
                st.error(str(error))
                return
            st.session_state.active_search = (label, query, result, seconds)
        label, query, result, seconds = st.session_state.active_search
    else:
        st.info("Choose a service and enter a location. For a first search, try Dialysis in Jaipur, Rajasthan.")
        return

    if result["reason"] == "ambiguous_place":
        st.warning("More than one recorded location matches. Choose the intended location.")
        show_trace(result["decision_trace"], label)
        options = {choice_label(choice): choice for choice in result["location_choices"]}
        choice = options[st.selectbox("Matching locations", list(options), key=f"ambiguity_{query}")]
        if st.button("Use this location"):
            new_query = query | {"location": choice}
            try:
                new_result, seconds = run_search(new_query)
            except DataUnavailable as error:
                st.error(str(error))
                return
            st.session_state.active_search = (label, new_query, new_result, seconds)
            st.rerun()
        return
    if result["reason"] == "invalid_location_or_radius":
        st.warning(result["detail"])
        show_trace(result["decision_trace"], label)
        return
    ranked, reviews = result["recommendations"], result["verification_required"]
    st.divider()
    st.header(f"{label} results")
    st.text(choice_label(query["location"]) if "latitude" not in query["location"]
            else f'Coordinate search · {query["location"]["latitude"]}, {query["location"]["longitude"]} · {query["radius_km"]:g} km radius')
    st.caption(f"Warehouse read time at cache fill {seconds:.2f} s. Location and service reads can be cached.")
    st.info("Confirm the service, the branch address, and intake requirements directly with the facility before arranging care.")
    assumptions = result["distance_assumptions"] or {}
    if assumptions:
        st.caption(assumptions["note"])
    if ranked:
        st.subheader(f"Dataset-supported facilities ({len(ranked)})")
        st.caption("Ranking points reflect evidence field, address consistency, and proximity when available. "
                   "They do not measure clinical quality, safety, or current availability.")
        show_group(ranked, result["service_key"], "ranked", assumptions)
    else:
        st.warning("No reliable match. The available records did not meet the ranking criteria.")
    if reviews:
        with st.expander(f"Requires verification ({len(reviews)})", expanded=False):
            st.caption("Review these leads for conflicting claims, weak attribution, or incomplete location information.")
            show_group(reviews, result["service_key"], "verification", assumptions)
    comparison(ranked, reviews, assumptions)
    show_trace(result["decision_trace"], label)


if __name__ == "__main__":
    main()
