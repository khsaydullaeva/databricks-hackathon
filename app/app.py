"""Find the Right Care — Databricks App (Streamlit).

Audience: community health workers, NGO coordinators and district planners. Plain language,
phone numbers up front, every claim backed by a quote from the facility record, and every
uncertainty stated rather than hidden.
"""

from __future__ import annotations

import re
from dataclasses import asdict

import pandas as pd
import streamlit as st

from data import load_tables
from findcare.geo import Location, city_options, location_from_city, location_from_district, location_from_point, resolve_location
from findcare.nfhs import district_context
from findcare.pipeline import Runtime
from findcare.ranking import DEFAULT_WEIGHTS, recommend
from findcare.services import HALF_LIFE_KM, SERVICES, SERVICES_BY_ID

st.set_page_config(page_title="Find the Right Care · India", page_icon="🏥", layout="wide")

LEVEL_STYLE = {"Strong": ("green", "✅"), "Moderate": ("orange", "🟠"), "Limited": ("red", "⚠️"), "Indirect": ("gray", "❔")}
WEB_BADGE = {
    "confirmed_own_site": ":green-background[**✔ Confirmed on the hospital's own website**]",
    "mentioned_group_site": ":blue-background[Mentioned on the hospital group's website] (may describe another branch)",
    "not_found_on_site": ":gray-background[✖ Not found on the hospital's website] (websites are often incomplete)",
    "site_unreachable": ":gray-background[Hospital website could not be reached]",
}
LEVEL_HEX = {"Strong": "#1a7f37", "Moderate": "#d4a72c", "Limited": "#bc4c00", "Indirect": "#8c959f"}


@st.cache_resource(show_spinner="Loading ~10,000 facility records…")
def get_runtime() -> Runtime:
    return Runtime.from_tables(load_tables())


try:
    rt = get_runtime()
except Exception as e:  # make set-up problems understandable
    st.error(f"Couldn't load the data tables: {e}")
    st.info("Run notebooks 01–03 first, give the app a SQL warehouse resource named `sql-warehouse`, "
            "and grant the app's service principal SELECT on the `findcare` schema (see README).")
    st.stop()

# ----------------------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("How results are ranked")
    st.caption("Change what matters most. Weights are re-normalised to add up to 1.")
    w = {
        "evidence": st.slider("Evidence the service exists", 0.0, 1.0, DEFAULT_WEIGHTS["evidence"], 0.05),
        "proximity": st.slider("Closeness", 0.0, 1.0, DEFAULT_WEIGHTS["proximity"], 0.05),
        "trust": st.slider("Record quality / consistency", 0.0, 1.0, DEFAULT_WEIGHTS["trust"], 0.05),
        "scale": st.slider("Facility size (beds, doctors)", 0.0, 1.0, DEFAULT_WEIGHTS["scale"], 0.05),
    }
    min_ev = st.select_slider("Minimum evidence to recommend", options=[0.31, 0.55, 0.80], value=0.31,
                              format_func=lambda v: {0.31: "Limited or better", 0.55: "Moderate or better", 0.80: "Strong only"}[v])
    safety = st.toggle("Safety mode: only rank records that pass every check", value=True,
                       help="From CareProof: if a record's evidence is weak, its quote seems to describe another facility, "
                            "it contradicts itself, or an analyst put it on hold, we don't rank it – we show it under "
                            "'Held for review' with the reason. If nothing passes, we say so instead of guessing.")
    only_confirmed = st.toggle("Only show services confirmed on the hospital's own website", value=False,
                               help="We checked each hospital's official website for the service. Sites are often incomplete, "
                                    "so this can hide genuine options.")
    hide_review = st.toggle("Hide records that need review", value=False,
                            help="Records with contradictions (e.g. location vs postcode) or possible merged sites.")
    st.divider()
    st.caption(f"Data: Virtue Foundation facility dataset ({len(rt.facilities):,} records), India Post PIN directory, "
               "NFHS-5 (2019–21) district fact sheets. Records were extracted from web pages and may be wrong or out of date.")

# ----------------------------------------------------------------------------- search form
st.title("🏥 Find the right care")
st.markdown("Tell us what the patient needs and where they are. We'll suggest facilities **and show exactly why** — "
            "including how sure we are. *Always phone the facility before travelling.*")

labels = [s.label for s in SERVICES]
service_label = st.selectbox("**1. What care does the patient need?**", labels, index=0)

st.markdown("**2. Where is the patient?**")
MODES = ["🏙️ City", "🗺️ District", "📮 PIN code", "📍 Point on map"]
mode = st.radio("How do you want to give the location?", MODES, horizontal=True, key="loc_mode")


@st.cache_resource
def pickers():
    dl = rt.district_lookup.dropna(subset=["lat", "lon"])
    return city_options(rt.place_index), dl.sort_values(["state_norm", "district_norm"])


cities, districts = pickers()
pending = None  # Location chosen in the picker, used when "Find care" is pressed

if mode == MODES[0]:
    i = st.selectbox("City", range(len(cities)), index=None, format_func=lambda i: cities.at[i, "label"],
                     placeholder="Start typing a city name…")
    st.caption(f"{len(cities)} cities that have facilities in the dataset. Not listed? Use District, PIN code or the map.")
    if i is not None:
        pending = location_from_city(cities.iloc[i], rt.district_lookup)

elif mode == MODES[1]:
    c1, c2 = st.columns(2)
    states = sorted(districts["state_norm"].unique())
    st_sel = c1.selectbox("State / UT", states, index=None, format_func=str.title, placeholder="Choose a state…")
    if st_sel:
        ds = districts[districts["state_norm"] == st_sel]
        d_sel = c2.selectbox("District", ds["district_norm"].tolist(), index=None,
                             format_func=lambda d: str(ds.loc[ds["district_norm"] == d, "district"].iloc[0]).title(),
                             placeholder="Choose a district…")
        if d_sel:
            pending = location_from_district(st_sel, d_sel, rt.district_lookup)

elif mode == MODES[2]:
    pin = st.text_input("6-digit PIN code", max_chars=6, placeholder="e.g. 800001").strip()
    if pin:
        if not re.fullmatch(r"[1-9]\d{5}", pin):
            st.caption(":red[A PIN code has 6 digits and doesn't start with 0.]")
        else:
            cand = resolve_location(pin, rt.pin_lookup, rt.district_lookup, rt.place_index)
            if cand.ok:
                pending = cand
                st.caption(f"✓ {cand.label}")
            else:
                st.caption(f":red[{cand.error}]")

else:
    import folium
    from streamlit_folium import st_folium

    st.caption("Click on the map where the patient is (zoom in for accuracy), or type the coordinates below.")
    lat0, lon0 = st.session_state.get("lat_in"), st.session_state.get("lon_in")
    has_pt = lat0 is not None and lon0 is not None
    fmap = folium.Map(location=[lat0, lon0] if has_pt else [22.6, 79.0], zoom_start=11 if has_pt else 5, tiles="OpenStreetMap")
    if has_pt:
        folium.Marker([lat0, lon0], tooltip="Patient location", icon=folium.Icon(color="blue", icon="user")).add_to(fmap)
    out = st_folium(fmap, height=380, use_container_width=True, returned_objects=["last_clicked"], key="pick_map")
    click = (out or {}).get("last_clicked")
    if click:
        new_pt = (round(click["lat"], 5), round(click["lng"], 5))
        if new_pt != (lat0, lon0):
            st.session_state["lat_in"], st.session_state["lon_in"] = new_pt
            st.rerun()
    c1, c2 = st.columns(2)
    lat = c1.number_input("Latitude", min_value=6.0, max_value=37.6, format="%.5f", key="lat_in", placeholder="e.g. 25.61",
                          **({} if "lat_in" in st.session_state else {"value": None}))
    lon = c2.number_input("Longitude", min_value=68.0, max_value=97.5, format="%.5f", key="lon_in", placeholder="e.g. 85.14",
                          **({} if "lon_in" in st.session_state else {"value": None}))
    if lat is not None and lon is not None:
        cand = location_from_point(lat, lon, rt.district_lookup)
        if cand.ok:
            pending = cand
            st.caption(f"✓ {cand.label}")
        else:
            st.caption(f":red[{cand.error}]")

c3, c4, c5 = st.columns([2, 2, 1])
radius = c3.select_slider("Search within", options=[10, 25, 50, 100, 200], value=50, format_func=lambda x: f"{x} km")
top_k = c4.select_slider("Show up to", options=[3, 5, 10], value=5, format_func=lambda x: f"{x} options")
c5.write("")
if c5.button("Find care", type="primary", width="stretch", disabled=pending is None):
    st.session_state["last_search"] = {"service": service_label, "loc": asdict(pending), "radius": radius, "top_k": top_k}

search = st.session_state.get("last_search")
if not search:
    st.info("Choose the care needed and the patient's location, then press **Find care**.")
    st.stop()

svc = next(s for s in SERVICES if s.label == search["service"])
loc = Location(**search["loc"])

res = recommend(rt, svc, loc, radius_km=search["radius"], top_k=search["top_k"], weights=w, min_evidence=min_ev,
                only_confirmed=only_confirmed, hide_review=hide_review, guarded=safety)

# ----------------------------------------------------------------------------- summary
st.subheader(f"{svc.label} near {loc.label}")
method_txt = {"coordinates": "exact coordinates", "pincode": "centre of the PIN code area",
              "pincode_district_centre": "district centre (PIN not geocoded)", "pin_prefix": "rough PIN area"}
st.caption(f"Location used: {method_txt.get(loc.method, loc.method.replace('name:', 'centre of ').replace('office_major', 'post office').replace('office', 'post office'))}"
           + (f" · District: {loc.district}, {loc.state}" if loc.district else ""))
for msg in loc.warnings + [n for n in res.notes[:-1] if n != res.abstain_reason]:
    st.warning(msg, icon="⚠️")

cov = res.coverage
m1, m2, m3, m4 = st.columns(4)
m1.metric("Strong evidence", cov["n_strong"], help="Service named in 2+ separate parts of the record")
m2.metric("Moderate", cov["n_moderate"], help="Clearly named, in one part of the record")
m3.metric("Limited / indirect", cov["n_limited"] + cov["n_indirect"], help="Weak or related mentions only")
m4.metric("Search radius", f"{cov['radius_km']:.0f} km")

if res.recommendations:
    top = res.recommendations[0]
    color, icon = LEVEL_STYLE[top["evidence_level"]]
    st.success(f"**Best option: {top['name']}** — {top['distance_km']:.0f} km · {icon} {top['evidence_level']} evidence"
               + (" · ✔ confirmed on its own website" if top["web_status"] == "confirmed_own_site" else "")
               + (f" · 📞 {top['phone']}" if top.get("phone") else ""))
if svc.advice:
    st.info(f"💡 {svc.advice}")

# ----------------------------------------------------------------------------- map
pts = [{"lat": loc.lat, "lon": loc.lon, "color": "#0969da", "size": 900, "name": "Patient location"}]
for r in res.recommendations:
    pts.append({"lat": r["lat"], "lon": r["lon"], "color": LEVEL_HEX[r["evidence_level"]], "size": 600, "name": r["name"]})
for r in res.held:
    pts.append({"lat": r["lat"], "lon": r["lon"], "color": "#bf8700", "size": 400, "name": r["name"]})
for r in res.leads:
    pts.append({"lat": r["lat"], "lon": r["lon"], "color": "#d0d7de", "size": 350, "name": r["name"]})
st.map(pd.DataFrame(pts), latitude="lat", longitude="lon", color="color", size="size", height=320)
st.caption("Blue = patient · green/orange/red = recommended (by evidence strength) · dark yellow = held for review · grey = unverified leads. "
           "Distances are straight-line; roads are usually 1.2–1.5× longer.")

# ----------------------------------------------------------------------------- results
tab_rec, tab_held, tab_leads, tab_ctx, tab_how = st.tabs(["Recommended", f"Held for review ({len(res.held)})",
                                                          f"Unverified leads ({len(res.leads)})", "Local health context", "How we decided"])


def render_card(r: dict):
    color, icon = LEVEL_STYLE.get(r["evidence_level"], ("gray", "❔"))
    with st.container(border=True):
        head = f"{r['rank']}. {r['name']}" if r["rank"] else r["name"]
        st.markdown(f"#### {head}")
        cal = (rt.calibration or {}).get(r["evidence_level"])
        cal_txt = f" · *claims at this level were confirmed on hospitals' own websites {cal[0]:.0f}% of the time*" if cal else ""
        st.markdown(f":{color}[**{icon} {r['evidence_level']} evidence**] — {r['evidence_meaning']}{cal_txt}")
        badge = WEB_BADGE.get(r["web_status"])
        if badge:
            st.markdown(badge + (f" · [open page]({r['web_url']})" if r.get("web_url") else ""))
        if r["needs_review"]:
            st.markdown(":orange[**⚑ Record needs review**] — it has contradictions or may merge several sites (details below).")
        a, b, c = st.columns(3)
        a.markdown(f"**Distance**  \n{r['distance_km']:.1f} km" + ("  \n*(approximate location)*" if r["location_basis"] != "facility coordinates" else ""))
        b.markdown(f"**Phone**  \n{r['phone'] or '— not listed'}")
        c.markdown(f"**Type**  \n{(r['facility_type'] or 'unknown').title()}" + (f" · {r['operator_type']}" if r.get("operator_type") else ""))
        if r["address"]:
            st.caption(f"📍 {r['address']}")
        st.caption(f"Record ID: `{r['facility_id']}`")
        st.markdown("**Why this facility?**")
        if r["citations"]:
            for cit in r["citations"]:
                link = f" [source]({cit['url']})" if cit.get("url") else ""
                st.markdown(f"- *{cit['field']}* ({cit['kind']}): “{cit['text']}”{link}")
        else:
            st.markdown("- No direct quotes.")
        if r["cautions"]:
            st.warning("**Be careful:**\n" + "\n".join(f"- {x}" for x in r["cautions"]))
        if r.get("analyst_hold") and r["analyst_hold"].get("evidence"):
            with st.expander("Analyst review evidence"):
                for q in r["analyst_hold"]["evidence"]:
                    st.markdown(f"- *{q.get('field')}*: “{q.get('quote')}”")
                st.caption(f"Provenance: {r['analyst_hold'].get('provenance')}")
        if r.get("vs_next"):
            st.caption(f"↕ {r['vs_next']}")
        with st.expander("Score breakdown"):
            comp = r["components"]
            st.dataframe(pd.DataFrame([
                {"factor": "Evidence the service exists", "raw value": r["evidence_adj"], "weighted": comp["evidence"],
                 "how": f"noisy-OR over fields = {r['evidence_score']:.2f}" + (f" × plausibility {r['plausibility_factor']:.2f}" if r["plausibility_factor"] < 1 else "")},
                {"factor": "Closeness", "raw value": r["proximity"], "weighted": comp["proximity"],
                 "how": f"0.5^({r['distance_km']:.0f} km / {r['half_life_km']:.0f} km half-life)"},
                {"factor": "Record quality", "raw value": r["trust_score"], "weighted": comp["trust"],
                 "how": "1 − penalties for contradictions/missing data"},
                {"factor": "Facility size", "raw value": r["scale"], "weighted": comp["scale"], "how": r["scale_text"]},
            ] + ([{"factor": "24x7 emergency signal", "raw value": 1.0, "weighted": r["bonus_24x7"], "how": "record mentions 24x7 / emergency dept."}] if r["bonus_24x7"] else [])),
                hide_index=True, width="stretch")
            st.markdown(f"**Total score: {r['score']:.2f}**")
            if r["ignored_mentions"]:
                st.markdown("**Mentions we ignored** (negated, planned or referral wording):")
                for i in r["ignored_mentions"]:
                    st.markdown(f"- *{i['field']}*: “{i['text']}”")
        if r["sources"]:
            st.caption("Sources: " + " · ".join(f"[{u[:50]}]({u if u.startswith('http') else 'https://' + u})" for u in r["sources"]))
        else:
            st.caption(":red[No source URL recorded for this facility.]")


with tab_rec:
    if res.abstained:
        st.error(f"**No recommendation.** {res.abstain_reason}")
    for r in res.recommendations:
        render_card(r)
    if res.recommendations:
        export = pd.DataFrame([{k: r[k] for k in ("rank", "name", "distance_km", "evidence_level", "score", "phone", "address", "why")}
                               | {"cautions": " | ".join(r["cautions"]), "sources": " ".join(r["sources"])} for r in res.recommendations])
        st.download_button("Download these results (CSV)", export.to_csv(index=False).encode(), file_name=f"{svc.id}_{loc.label[:30]}.csv")

with tab_held:
    st.caption("These facilities mention the service clearly enough, but failed a safety check (reason shown on each card). "
               "They are not recommended – but in a remote area one of them may still be worth a phone call.")
    if not res.guarded:
        st.info("Safety mode is off, so nothing is held back.")
    for r in res.held:
        render_card(r)

with tab_leads:
    st.caption("These facilities mention related terms (e.g. a department) but the record does not clearly say they offer "
               f"{svc.label.lower()}. Useful only if nothing better is reachable — **call to confirm**.")
    for r in res.leads:
        render_card(r)

with tab_ctx:
    ctx = district_context(rt.nfhs_long, rt.district_nfhs_map, loc.state_norm, loc.district_norm, svc.nfhs, service_id=svc.id)
    if ctx.get("ok"):
        st.caption(ctx["note"] + " Values are % unless stated. These describe the district population, not any facility.")
        df = pd.DataFrame(ctx["rows"]).rename(columns={"indicator": "Indicator", "value": "District", "state_avg": "State (avg of districts)", "caution": "Caution"})
        st.dataframe(df, hide_index=True, width="stretch")
    else:
        st.info(ctx.get("note", "No district context available."))

with tab_how:
    hl = HALF_LIFE_KM[svc.urgency]
    tot = sum(w.values()) or 1
    st.markdown(f"""
**1. Find where the patient is.** Your text was matched to *{loc.label}* ({loc.method}). PIN codes use the median of
geocoded post offices in that PIN (outliers > 50 km removed); place names use city, district or post-office centres.

**2. Find facilities that say they offer {svc.label.lower()}.** We search each record's *procedures, equipment,
capabilities, specialties, name and description* for terms that name the service (e.g. `{svc.strong[0]}`) and related
terms. Mentions with "no / not available / planned / referred to" wording are ignored. Each field contributes up to 0.75;
fields combine as 1 − ∏(1 − c). Strong = named in 2+ separate fields; Moderate = 1 field; Limited = description/specialty
only; Indirect = related terms only (never recommended, shown as leads).

**3. Fact-check against the hospital's own website.** We fetched each facility's official website (homepage + up to 4
service pages) and looked for the service there. A confirmation is an *independent* source, so record + website agreement
counts as Strong. Not finding it doesn't count against the facility much: hospital websites are often incomplete.

**4. Check whether the record can be trusted.** We penalise records whose coordinates contradict their postcode,
whose state contradicts their postcode, that share coordinates with many others (city-level geocode), that mention
postcodes in other districts (possible merged record), that claim an implausibly wide set of services, or that have
no source URL. Tertiary services claimed by small clinics are down-weighted.

**5. Rank.** Score = {w['evidence']/tot:.2f}×evidence + {w['proximity']/tot:.2f}×closeness + {w['trust']/tot:.2f}×record
quality + {w['scale']/tot:.2f}×size. Closeness halves every **{hl:.0f} km** for this service ({svc.urgency} care).
If fewer than 3 options are found we widen the radius and tell you.

**Does the evidence level mean anything?** We checked {sum(n for _, n in (rt.calibration or {}).values()):,} claims against
the hospitals' own websites. Share confirmed, by level: {" · ".join(f"{k} {v[0]:.0f}%" for k, v in (rt.calibration or {}).items()) or "not available"}.
(A lower bound: hospital websites are often incomplete.)

**6. Safety checks (from CareProof).** Before ranking, every candidate must pass: evidence at least Moderate · the quote
describes *this* facility (not another hospital or branch city) · a self-consistent record · no analyst hold. Failing
candidates are *held for review*, with the reason; if none pass we **abstain** rather than guess.

**What we trade off:** a closer facility with weaker evidence can rank below a farther one with strong evidence —
because a wasted trip costs more time than a longer one. Use the sliders on the left to change this.

**What we can't know:** whether the service is available *today*, cost, waiting time, or quality of care.
Records were extracted from websites by AI and may be outdated. **Always call ahead.**
""")
    st.markdown("**Decision funnel for this search** – how many facilities survived each step:")
    st.dataframe(pd.DataFrame(res.funnel, columns=["Step", "Facilities", "Check"]), hide_index=True, width="stretch")
    if res.sensitivity:
        sv = res.sensitivity
        st.markdown("**Would the answer change with equal weights?** "
                    + ("No – the #1 option stays the same" if sv["top1_same"] else f"Yes – #1 would become **{sv['alt_top1']}**")
                    + (" and the same facilities make the list." if sv["same_set"] else "; some lower positions would change."))
