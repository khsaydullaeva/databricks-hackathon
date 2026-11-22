"""Bronze -> Silver -> Gold transformations, in pandas (10k facilities fits comfortably).

Notebooks call these functions and persist the results as Delta tables; the app loads the
gold tables back and rebuilds the in-memory `Runtime` with `Runtime.from_tables`.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import columns as colres
from .evidence import compute_evidence
from .geo import (build_district_lookup, build_pin_lookup, build_place_index, haversine_km, in_india, norm_place,
                  norm_state)
from .nfhs import match_districts
from .quality import apply_breadth_flag, assess
from .services import EMERGENCY_24X7_RE, SERVICES
from .text import clean_pincode, from_json_list, is_missing, norm, parse_list, to_float

LIST_FIELDS = ("specialties", "procedure", "equipment", "capability")

# Leftovers from the upstream AI extraction (questions, "not specified", prompt echoes). They are
# not claims about the facility, so they are dropped from evidence (and counted for transparency).
ARTIFACT_RE = re.compile(r"explicitly stated|in the (?:provided |given )?(?:text|content|page)\b|not (?:explicitly )?(?:specified|mentioned)|"
                         r"no information|unclear whether|\?\s*$", re.I)
JSON_COLS = [f"{f}_list" for f in LIST_FIELDS] + ["source_urls_list", "trust_flags"]


# --------------------------------------------------------------------------- silver
def clean_facilities(raw: pd.DataFrame, overrides: dict | None = None) -> tuple[pd.DataFrame, dict]:
    m = colres.resolve(list(raw.columns), overrides)
    if not m["name"]:
        raise ValueError("Could not find a facility name column. Columns: " + ", ".join(raw.columns[:60]))

    def first(canon):
        return raw[m[canon][0]] if m[canon] else pd.Series([None] * len(raw), index=raw.index)

    out = pd.DataFrame(index=raw.index)
    out["name"] = first("name").astype(str).str.strip()
    out["name_norm"] = out["name"].map(norm)

    # description: combine all description-like columns, de-duplicated
    def combine_desc(row):
        parts, seen = [], set()
        for c in m["description"]:
            v = row[c]
            if not is_missing(v) and str(v).strip().lower() not in seen:
                seen.add(str(v).strip().lower())
                parts.append(str(v).strip())
        return "\n\n".join(parts)

    out["description"] = raw.apply(combine_desc, axis=1) if m["description"] else ""
    for f in LIST_FIELDS:
        parsed = first(f).map(parse_list)
        out[f"{f}_list"] = parsed.map(lambda xs: [x for x in xs if not ARTIFACT_RE.search(x)])
        out[f"n_artifacts_{f}"] = parsed.map(lambda xs: sum(bool(ARTIFACT_RE.search(x)) for x in xs))
    for f in ("facility_type", "operator_type", "organization_type", "city", "state"):
        out[f] = first(f).map(lambda v: None if is_missing(v) else str(v).strip())
    addr_cols = [c for k in ("address_line1", "address_line2", "address_line3") for c in m[k]]
    out["address_text"] = raw[addr_cols].apply(
        lambda r: ", ".join(str(v).strip() for v in r if not is_missing(v)), axis=1) if addr_cols else ""
    pc = first("postcode").map(clean_pincode)
    pc_from_addr = out["address_text"].map(clean_pincode)
    out["postcode"] = pc.where(pc.notna(), pc_from_addr)
    out["postcode_source"] = np.where(pc.notna(), "postcode field", np.where(pc_from_addr.notna(), "address text", None))
    out["latitude"] = pd.to_numeric(first("latitude").map(to_float), errors="coerce")
    out["longitude"] = pd.to_numeric(first("longitude").map(to_float), errors="coerce")
    zero = (out["latitude"].fillna(0).abs() < 1e-6) & (out["longitude"].fillna(0).abs() < 1e-6)
    out["coord_valid"] = in_india(out["latitude"], out["longitude"]) & ~zero
    out["num_doctors"] = _bounded(first("num_doctors"), 1, 5000)
    out["capacity"] = _bounded(first("capacity"), 1, 10000)
    out["year_established"] = _bounded(first("year_established"), 1800, 2026)

    def urls(row):
        items = []
        for c in m["source_urls"] + m["website"]:
            items += [u for u in parse_list(row[c]) if "." in u]
        return list(dict.fromkeys(items))

    url_cols = m["source_urls"] + m["website"]
    out["source_urls_list"] = raw[url_cols].apply(urls, axis=1) if url_cols else [[] for _ in range(len(raw))]
    out["website"] = first("website").map(lambda v: (parse_list(v) or [None])[0])
    out["phone"] = raw[m["phone"]].apply(
        lambda r: ", ".join(list(dict.fromkeys(p for v in r for p in parse_list(v)))[:3]) or None, axis=1) if m["phone"] else None
    out["email"] = first("email").map(lambda v: (parse_list(v) or [None])[0])
    out["city_norm"] = out["city"].map(norm_place)
    out["state_norm"] = out["state"].map(norm_state)

    # stable id
    if m["facility_id"]:
        ids = first("facility_id").astype(str)
    else:
        ids = (out["name"] + "|" + out["latitude"].astype(str) + "|" + out["longitude"].astype(str)).map(
            lambda s: hashlib.md5(s.encode()).hexdigest()[:12])
    dup = ids.duplicated(keep=False)
    if dup.any():
        ids = ids.where(~dup, ids + "_" + ids.groupby(ids).cumcount().astype(str))
    out.insert(0, "facility_id", ids.to_numpy())
    out = out.reset_index(drop=True)
    return out, m


def _bounded(s: pd.Series, lo, hi) -> pd.Series:
    v = pd.to_numeric(s.map(to_float), errors="coerce")
    return v.where((v >= lo) & (v <= hi))


def clean_pincodes(raw: pd.DataFrame) -> pd.DataFrame:
    cols = {c.lower().strip(): c for c in raw.columns}
    need = ["officename", "pincode", "officetype", "district", "statename", "latitude", "longitude"]
    missing = [n for n in need if n not in cols]
    if missing:
        raise ValueError(f"PIN directory missing columns {missing}; has {list(raw.columns)}")
    df = pd.DataFrame({n: raw[cols[n]] for n in need + [c for c in ("circlename", "regionname", "divisionname", "delivery") if c in cols]})
    df["pincode"] = df["pincode"].map(clean_pincode)
    df = df[df["pincode"].notna()].copy()
    df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
    df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")
    ok = in_india(df["latitude"], df["longitude"])
    swapped = ~ok & in_india(df["longitude"], df["latitude"])
    df.loc[swapped, ["latitude", "longitude"]] = df.loc[swapped, ["longitude", "latitude"]].to_numpy()
    df["geo_fix"] = np.where(swapped, "lat/lon swapped in source", None)
    df["geo_valid"] = ok | swapped  # placeholders like (1,1) or (100,100) fall outside India and stay invalid
    # Same-name geocoding errors: e.g. 229 offices of Aurangabad (Bihar) carry the exact coordinate of
    # Aurangabad (Maharashtra). Build a centre for each 3-digit PIN area from offices that are NOT part of
    # an exact-coordinate cluster (>=5 offices across >=3 PINs), then reject points far from that centre.
    v = df[df["geo_valid"]]
    key = v["latitude"].round(4).astype(str) + "," + v["longitude"].round(4).astype(str)
    kstats = v.groupby(key)["pincode"].agg(["size", "nunique"])
    cluster = key.map((kstats["size"] >= 5) & (kstats["nunique"] >= 3))
    pfx = v["pincode"].str[:3]
    centre = v[~cluster].groupby(pfx[~cluster])[["latitude", "longitude"]].median()
    clat, clon = pfx.map(centre["latitude"]), pfx.map(centre["longitude"])
    d = pd.Series(haversine_km(v["latitude"], v["longitude"], clat.fillna(v["latitude"]), clon.fillna(v["longitude"])), index=v.index)
    q90 = d[~cluster].groupby(pfx[~cluster]).quantile(0.9)
    far = d > pfx.map(np.maximum(q90 * 2.5, 150.0)).fillna(np.inf)
    far_idx = far[far].index
    df.loc[far_idx, "geo_valid"] = False
    df.loc[far_idx, "geo_fix"] = "rejected: far from other offices in its PIN area (likely geocoded to a same-name place)"
    for c in ("officename", "district", "statename", "officetype"):
        df[c] = df[c].astype(str).str.strip()
    # 'NA' text means missing: fill state/district from other offices with the same PIN, else state from the circle
    for c in ("district", "statename"):
        df[c] = df[c].where(~df[c].str.upper().isin({"NA", "", "NAN", "NONE", "NULL"}), None)
    df["admin_fix"] = None
    for c in ("district", "statename"):
        mode = df.dropna(subset=[c]).groupby("pincode")[c].agg(lambda s: s.value_counts().index[0])
        fill = df[c].isna() & df["pincode"].isin(mode.index)
        df.loc[fill, c] = df.loc[fill, "pincode"].map(mode)
        df.loc[fill, "admin_fix"] = "district/state filled from other offices with the same PIN"
    if "circlename" in df:
        circle_state = df["circlename"].astype(str).str.replace(r"\s*Circle$", "", regex=True).map(norm_state).map(
            lambda s: CIRCLE_STATE.get(s, s))
        fill = df["statename"].isna() & circle_state.isin(set(df["statename"].dropna().map(norm_state)))
        df.loc[fill, "statename"] = circle_state[fill].str.upper()
        df.loc[fill, "admin_fix"] = "state inferred from postal circle; district unknown"
    df["district"] = df["district"].fillna("")
    df["statename"] = df["statename"].fillna("")
    df["district_norm"] = df["district"].map(norm_place)
    df["state_norm"] = df["statename"].map(norm_state)
    # Second pass, state level: whole districts can be corrupted (most Vadodara offices have latitude 15.6
    # instead of 22.3; most Pratapgarh-UP offices sit near Raipur), which fools the PIN-area centre. Use a
    # per-axis robust z-score (median / MAD per state) and require > 150 km from the state centre.
    # Fragmented territories (Puducherry's enclaves etc.) are exempt.
    v = df[df["geo_valid"] & ~df["state_norm"].isin(FRAGMENTED_STATES)]
    z = np.zeros(len(v))
    for ax in ("latitude", "longitude"):
        grp = v.groupby("state_norm")[ax]
        med = grp.transform("median")
        mad = grp.transform(lambda s: (s - s.median()).abs().median()) * 1.4826
        z = np.maximum(z, ((v[ax] - med).abs() / mad.clip(lower=0.05)).to_numpy())
    smed = v.groupby("state_norm")[["latitude", "longitude"]].transform("median")
    dstate = haversine_km(v["latitude"], v["longitude"], smed["latitude"], smed["longitude"])
    out_idx = v.index[(z > 4) & (dstate > 150)]
    df.loc[out_idx, "geo_valid"] = False
    df.loc[out_idx, "geo_fix"] = "rejected: far outside the usual extent of its state"
    return df.reset_index(drop=True)


# Territories made of distant enclaves: a state-extent outlier test would wrongly reject them
FRAGMENTED_STATES = {"puducherry", "dadra and nagar haveli and daman and diu", "andaman and nicobar islands", "lakshadweep", ""}

# Postal circles whose name differs from the state they serve
CIRCLE_STATE = {"tamilnadu": "tamil nadu", "chattisgarh": "chhattisgarh"}


_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def quarantine_malformed(raw: pd.DataFrame, overrides: dict | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split off rows that are fragments of a broken multi-line CSV field rather than facilities.

    In the Virtue Foundation table ~88 rows have description text in the id column and values shifted
    across columns. Signal: ids are UUIDs for >=90% of rows, so a non-UUID id marks a malformed row; a
    row whose organization_type is not the dominant value ('facility') is also malformed.
    """
    m = colres.resolve(list(raw.columns), overrides)
    bad = pd.Series(False, index=raw.index)
    if m["facility_id"]:
        ids = raw[m["facility_id"][0]].astype(str).str.strip()
        is_uuid = ids.str.match(_UUID_RE)
        if is_uuid.mean() >= 0.9:
            bad |= ~is_uuid
    if m["organization_type"]:
        ot = raw[m["organization_type"][0]].astype(str).str.strip().str.lower()
        top = ot.value_counts()
        if len(top) and top.iloc[0] / len(ot) >= 0.9:
            bad |= ot != top.index[0]
    return raw[~bad].copy(), raw[bad].copy()


def restore_silver_facilities(df: pd.DataFrame) -> pd.DataFrame:
    """Undo `to_storable` for silver facilities read back from Delta."""
    f = df.copy()
    for c in [f"{x}_list" for x in LIST_FIELDS] + ["source_urls_list"]:
        f[c] = f[c].map(from_json_list)
    for c in ("latitude", "longitude", "num_doctors", "capacity", "year_established"):
        f[c] = pd.to_numeric(f[c], errors="coerce")
    f["coord_valid"] = f["coord_valid"].map(lambda v: bool(v) if isinstance(v, (bool, np.bool_)) else str(v).lower() == "true")
    for c in ("description", "name_norm", "city_norm", "state_norm", "address_text"):
        f[c] = f[c].fillna("")
    f["postcode"] = f["postcode"].where(f["postcode"].notna(), None)
    return f


def restore_silver_pins(df: pd.DataFrame) -> pd.DataFrame:
    p = df.copy()
    p["pincode"] = p["pincode"].astype(str)
    p["latitude"] = pd.to_numeric(p["latitude"], errors="coerce")
    p["longitude"] = pd.to_numeric(p["longitude"], errors="coerce")
    p["geo_valid"] = p["geo_valid"].map(lambda v: bool(v) if isinstance(v, (bool, np.bool_)) else str(v).lower() == "true")
    return p


# --------------------------------------------------------------------------- gold
def build_gold(silver_fac: pd.DataFrame, silver_pins: pd.DataFrame, nfhs_wide: pd.DataFrame | None = None,
               log=print, web_status: pd.DataFrame | None = None, web_hits: pd.DataFrame | None = None) -> dict[str, pd.DataFrame]:
    log("building PIN and district lookups…")
    pin_lookup = build_pin_lookup(silver_pins)
    district_lookup = build_district_lookup(silver_pins)
    log("assessing record quality…")
    fac = assess(silver_fac, pin_lookup, district_lookup, build_place_index(silver_pins, district_lookup, None))
    fac["has_24x7"] = (fac["name"].fillna("") + " " + fac["description"].fillna("") + " " +
                       fac["capability_list"].map(" ".join)).map(lambda s: bool(EMERGENCY_24X7_RE.search(norm(s))))
    log("extracting service evidence…")
    ev_parts = []
    for s in SERVICES:
        e = compute_evidence(fac, s)
        ev_parts.append(e)
        log(f"  {s.id:20s} {int((e['evidence_score'] > 0).sum()):6d} facilities with any evidence")
    evidence = pd.concat(ev_parts, ignore_index=True)
    strong_counts = evidence[evidence["has_strong"]].groupby("facility_id").size()
    fac = apply_breadth_flag(fac, strong_counts, len(SERVICES))  # record-only claims, before web merge
    log("checking attribution (does the text describe THIS facility?)…")
    known_places = set(district_lookup["district_norm"]) | set(fac.loc[fac["city_norm"].map(fac["city_norm"].value_counts()) >= 2, "city_norm"])
    known_places.discard("")
    fac = flag_other_facility_text(fac)
    evidence = apply_attribution(evidence, fac, known_places)
    log("merging website fact-checks…")
    evidence, fac = merge_web_evidence(evidence, fac, web_status, web_hits)
    evidence = finalize_attribution(evidence)
    fac, queue = review_queue(fac, evidence)
    calibration = evidence_calibration(evidence)
    log(f"  {int(fac['needs_review'].sum())} facilities need review; {int((evidence['web_status'] == 'confirmed_own_site').sum())} claims confirmed on own website")
    place_index = build_place_index(silver_pins, district_lookup, fac)
    out = {
        "gold_facilities": fac,
        "gold_facility_service_evidence": evidence,
        "gold_review_queue": queue,
        "gold_evidence_calibration": calibration,
        "gold_pincode_lookup": pin_lookup,
        "gold_district_lookup": district_lookup,
        "gold_place_index": place_index,
    }
    if nfhs_wide is not None and len(nfhs_wide):
        out["gold_district_nfhs_map"] = match_districts(nfhs_wide, district_lookup)
    out["gold_district_service_coverage"] = district_coverage(fac, evidence, district_lookup)
    return out


WEB_CLAIM_STATUS_LABEL = {
    "confirmed_own_site": "Confirmed on the hospital's own website",
    "mentioned_group_site": "Mentioned on the hospital group's website (may be another branch)",
    "not_found_on_site": "Not found on the hospital's website (websites are often incomplete)",
    "not_found_on_group_site": "Not found on the hospital group's website",
    "site_unreachable": "Hospital website could not be reached",
    "no_own_website": "Only listed on directory sites",
    "no_website": "No website recorded",
    "not_checked": "Website not checked",
}


def merge_web_evidence(evidence: pd.DataFrame, fac: pd.DataFrame, web_status: pd.DataFrame | None,
                       web_hits: pd.DataFrame | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Add the hospital-website fact-check to the evidence (an independent source).

    A confirmation is an extra evidence field ('website' weight 1.0, or 'website_chain' 0.4 for group
    sites), so record + own-website agreement reaches Strong. Facilities whose site names a service the
    record didn't claim get a new evidence row. Every (facility, service) row gets a web_status."""
    from .evidence import score_hits

    fac = fac.copy()
    if web_status is not None and len(web_status):
        st = web_status.drop_duplicates("facility_id").set_index("facility_id")
        fac["web_domain"] = fac["facility_id"].map(st["domain"])
        fac["web_site_kind"] = fac["facility_id"].map(st["site_kind"])
        fac["web_site_status"] = fac["facility_id"].map(st["web_status"]).fillna("not_checked")
        fac["web_checked_at"] = fac["facility_id"].map(st.get("fetched_at", pd.Series(dtype=object)))
    else:
        fac["web_domain"], fac["web_site_kind"], fac["web_site_status"], fac["web_checked_at"] = None, None, "not_checked", None
    site = fac.set_index("facility_id")[["web_site_status", "web_site_kind"]].to_dict("index")
    hits = {}
    if web_hits is not None and len(web_hits):
        for h in web_hits.to_dict("records"):
            hits.setdefault((h["facility_id"], h["service_id"]), h)

    def web_item(h):
        own = h.get("site_kind") != "chain"
        return {"field": "website" if own else "website_chain", "kind": "strong", "term": h["term"],
                "text": h["quote"], "url": h["page_url"], "checked_at": h.get("fetched_at")}

    def status(fid, h):
        if h is not None:
            return "confirmed_own_site" if h.get("site_kind") != "chain" else "mentioned_group_site"
        s_ = site.get(fid, {})
        ws, kind = s_.get("web_site_status"), s_.get("web_site_kind")
        if ws == "ok":
            return "not_found_on_site" if kind != "chain" else "not_found_on_group_site"
        if ws in ("unreachable", "blocked_by_robots"):
            return "site_unreachable"
        return {"directory_skipped": "no_own_website", "no_website": "no_website"}.get(ws, "not_checked")

    recs, seen = evidence.to_dict("records"), set()
    for r in recs:
        k = (r["facility_id"], r["service_id"])
        seen.add(k)
        h = hits.get(k)
        if h is not None:
            r.update(score_hits(list(r["items"]) + [web_item(h)], list(r["negated"])))
        r["web_status"] = status(r["facility_id"], h)
        r["web_url"] = h["page_url"] if h is not None else None
    for k, h in hits.items():
        if k not in seen and k[0] in site:
            r = {"facility_id": k[0], "service_id": k[1], **score_hits([web_item(h)], [])}
            r["web_status"], r["web_url"] = status(k[0], h), h["page_url"]
            recs.append(r)
    return pd.DataFrame(recs), fac


def evidence_calibration(evidence: pd.DataFrame) -> pd.DataFrame:
    """How often claims at each RECORD-ONLY evidence level were confirmed by the hospital's own website
    (among claims whose own site was reachable). A lower bound on precision: sites are often incomplete."""
    from .evidence import score_hits

    cols = ["level", "pct_confirmed_on_own_website", "claims_checked"]
    if "web_status" not in evidence:
        return pd.DataFrame(columns=cols)
    c = evidence[evidence["web_status"].isin(["confirmed_own_site", "not_found_on_site"])]
    if c.empty:
        return pd.DataFrame(columns=cols)
    rec_level = [score_hits([i for i in it if not i["field"].startswith("website")], list(n))["level"]
                 for it, n in zip(c["items"], c["negated"])]
    t = (c.assign(level=rec_level, ok=c["web_status"].eq("confirmed_own_site"))
         .query("level != 'None'").groupby("level")["ok"].agg(["mean", "size"]).reset_index())
    t.columns = cols
    t[cols[1]] = (t[cols[1]] * 100).round(1)
    order = {"Strong": 0, "Moderate": 1, "Limited": 2, "Indirect": 3}
    return t.sort_values("level", key=lambda x: x.map(order)).reset_index(drop=True)


def flag_other_facility_text(fac: pd.DataFrame) -> pd.DataFrame:
    """Trust flags from CareProof's guards: text that declares another facility (mixed/merged
    record), and analyst coordinate holds."""
    from .attribution import coordinate_holds, other_facility
    from .quality import _flag

    fac = fac.copy()
    ch = coordinate_holds()
    flags_out, trust_out = [], []
    for r in fac.itertuples(index=False):
        flags = list(r.trust_flags)
        texts = (list(r.capability_list) + list(r.procedure_list) + list(r.equipment_list)
                 + [x for x in re.split(r"(?<=[.!?])\s+", r.description or "") if x])
        others = [(t, n) for t in texts if (n := other_facility(t, r.name))]
        if others:
            t, n = others[0]
            flags.append(_flag("other_facility_text", f"Record text describes another facility ({n}): “{t[:140]}” – it may mix several sites."))
        if r.facility_id in ch:
            flags.append(_flag("coordinate_hold", "Analyst review: its coordinates disagree with its site (coordinate hold from the CareProof audit)."))
        added = sum(f["penalty"] for f in flags[len(r.trust_flags):])
        flags_out.append(flags)
        trust_out.append(round(max(0.1, r.trust_score - added), 4))
    fac["trust_flags"], fac["trust_score"] = flags_out, trust_out
    return fac


def apply_attribution(evidence: pd.DataFrame, fac: pd.DataFrame, known_places: set[str]) -> pd.DataFrame:
    """Mark record quotes that describe another facility or another city (CareProof attribution guard)."""
    from .attribution import other_facility, other_site

    info = fac.set_index("facility_id")[["name", "city_norm"]].to_dict("index")
    recs = evidence.to_dict("records")
    for r in recs:
        f = info.get(r["facility_id"], {})
        for it in r["items"]:
            n = other_facility(it["text"], f.get("name", ""))
            c = None if n else other_site(it["text"], f.get("city_norm", ""), known_places)
            if n or c:
                it["attribution_issue"] = f"describes another facility ({n})" if n else f"places the service in another city ({c})"
    return pd.DataFrame(recs)


def finalize_attribution(evidence: pd.DataFrame) -> pd.DataFrame:
    """Row-level guard result + CareProof analyst holds."""
    from .attribution import review_holds

    holds = review_holds()
    conflict, note, hold = [], [], []
    for r in evidence.itertuples(index=False):
        strong_rec = [i for i in r.items if i["kind"] == "strong" and not i["field"].startswith("website")]
        bad = [i for i in strong_rec if i.get("attribution_issue")]
        is_conf = bool(strong_rec) and len(bad) == len(strong_rec) and r.web_status != "confirmed_own_site"
        conflict.append(is_conf)
        note.append(f"The quote that names the service {bad[0]['attribution_issue']}: “{bad[0]['text'][:140]}”" if is_conf else None)
        hold.append(holds.get((r.facility_id, r.service_id)))
    ev = evidence.copy()
    ev["attribution_conflict"], ev["attribution_note"], ev["analyst_hold"] = conflict, note, hold
    return ev


MAJOR_FLAGS = {"coords_vs_pin", "state_mismatch", "multi_site_text", "no_location", "breadth_outlier", "other_facility_text", "coordinate_hold"}


def review_queue(fac: pd.DataFrame, evidence: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Separate records that are incomplete or untrustworthy, for later human labelling.

    priority: +2 per major contradiction flag, +1 if nothing about size is recorded, +1 if the own
    website is reachable but doesn't mention any of the services the record claims (Strong/Moderate).
    needs_review = priority >= 2. The app still shows these (they may be the only option nearby) but
    marks them, and users can hide them."""
    from .services import SERVICES_BY_ID

    fac = fac.copy()
    claimed = evidence[evidence["level"].isin(["Strong", "Moderate"])]
    unconf = (claimed[claimed["web_status"] == "not_found_on_site"].groupby("facility_id")["service_id"]
              .agg(lambda s: sorted(set(s))))
    conf = claimed[claimed["web_status"] == "confirmed_own_site"].groupby("facility_id").size()
    reasons_col, prio_col = [], []
    for r in fac.itertuples(index=False):
        reasons, prio = [], 0
        for fl in r.trust_flags:
            if fl["code"] in MAJOR_FLAGS:
                reasons.append(fl["message"])
                prio += 2
        if r.completeness == 0:
            reasons.append("No beds, doctors, year established or equipment recorded.")
            prio += 1
        svc = unconf.get(r.facility_id)
        if svc and not conf.get(r.facility_id):
            labels = ", ".join(SERVICES_BY_ID[x].label.split(" (")[0].lower() for x in svc[:4])
            reasons.append(f"Its own website was reachable but mentions none of the claimed services ({labels}{'…' if len(svc) > 4 else ''}).")
            prio += 1
        reasons_col.append(reasons)
        prio_col.append(prio)
    fac["review_reasons"], fac["review_priority"] = reasons_col, prio_col
    fac["needs_review"] = fac["review_priority"] >= 2
    q = fac[fac["review_priority"] > 0][["facility_id", "name", "city", "state", "postcode", "review_priority", "needs_review",
                                         "review_reasons", "trust_score", "web_domain", "web_site_status"]]
    return fac, q.sort_values("review_priority", ascending=False).reset_index(drop=True)


def district_coverage(fac: pd.DataFrame, evidence: pd.DataFrame, district_lookup: pd.DataFrame) -> pd.DataFrame:
    """Per district x service: how many facilities have Strong/Moderate evidence, and how far
    the district centre is from the nearest such facility (straight line)."""
    from .geo import haversine_km

    dl = district_lookup.dropna(subset=["lat", "lon"]).reset_index(drop=True)
    loc = fac.set_index("facility_id")[["eff_lat", "eff_lon", "district_norm", "district_state_norm", "trust_score"]]
    rows = []
    for s in SERVICES:
        e = evidence[(evidence["service_id"] == s.id) & (evidence["level"].isin(["Strong", "Moderate"]))]
        e = e.join(loc, on="facility_id")
        cnt = e.groupby(["district_state_norm", "district_norm"]).size()
        cnt_strong = e[e["level"] == "Strong"].groupby(["district_state_norm", "district_norm"]).size()
        pts = e.dropna(subset=["eff_lat"])
        if len(pts):
            d = haversine_km(dl["lat"].to_numpy()[:, None], dl["lon"].to_numpy()[:, None],
                             pts["eff_lat"].to_numpy()[None, :], pts["eff_lon"].to_numpy()[None, :])
            nearest = d.min(axis=1)
        else:
            nearest = np.full(len(dl), np.nan)
        for i, r in dl.iterrows():
            k = (r["state_norm"], r["district_norm"])
            rows.append({
                "state": r["state"], "district": r["district"], "state_norm": k[0], "district_norm": k[1],
                "service_id": s.id, "service": s.label,
                "n_facilities_strong_or_moderate": int(cnt.get(k, 0)),
                "n_facilities_strong": int(cnt_strong.get(k, 0)),
                "nearest_km_from_district_centre": None if np.isnan(nearest[i]) else round(float(nearest[i]), 1),
            })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- persistence
def to_storable(df: pd.DataFrame) -> pd.DataFrame:
    """Lists/dicts -> JSON strings; object columns -> str (Arrow/Delta friendly)."""
    out = df.copy()
    for c in out.columns:
        if out[c].dtype == object:
            sample = out[c].dropna().head(50)
            if any(isinstance(v, (list, dict, tuple, np.ndarray)) for v in sample):
                out[c] = out[c].map(lambda v: json.dumps(list(v) if isinstance(v, (tuple, np.ndarray)) else v, ensure_ascii=False, default=str)
                                    if v is not None and not (isinstance(v, float) and np.isnan(v)) else None)
            else:
                out[c] = out[c].map(lambda v: None if v is None or (isinstance(v, float) and np.isnan(v)) else str(v))
    return out


def _json_obj(v):
    if isinstance(v, (list, dict)):
        return v
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return []
    try:
        return json.loads(v)
    except Exception:
        return []


@dataclass
class Runtime:
    facilities: pd.DataFrame
    pin_lookup: pd.DataFrame
    district_lookup: pd.DataFrame
    place_index: pd.DataFrame
    nfhs_long: pd.DataFrame | None = None
    district_nfhs_map: pd.DataFrame | None = None
    evidence: dict | None = None  # service_id -> evidence frame indexed by facility_id
    calibration: dict | None = None  # level -> (% confirmed on own website, n checked)
    ai_checks: dict | None = None  # (facility_id, service_id) -> AI reader verdict (ai_query, notebook 03b)

    @classmethod
    def from_tables(cls, t: dict[str, pd.DataFrame]) -> "Runtime":
        fac = t["gold_facilities"].copy()
        for c in [f"{f}_list" for f in LIST_FIELDS] + ["source_urls_list"]:
            fac[c] = fac[c].map(from_json_list)
        fac["trust_flags"] = fac["trust_flags"].map(_json_obj)
        for c in ("latitude", "longitude", "eff_lat", "eff_lon", "capacity", "num_doctors", "year_established",
                  "trust_score", "completeness", "coord_pin_km"):
            if c in fac:
                fac[c] = pd.to_numeric(fac[c], errors="coerce")
        if "review_reasons" in fac:
            fac["review_reasons"] = fac["review_reasons"].map(_json_obj)
        for c in ("coord_valid", "has_24x7", "needs_review"):
            if c in fac:
                fac[c] = fac[c].map(lambda v: str(v).lower() in {"true", "1"} if not isinstance(v, (bool, np.bool_)) else bool(v))
        pin = t["gold_pincode_lookup"].copy()
        pin["pincode"] = pin["pincode"].astype(str)
        for c in ("lat", "lon", "spread_km"):
            pin[c] = pd.to_numeric(pin[c], errors="coerce")
        pin["is_ambiguous"] = pin["is_ambiguous"].map(lambda v: str(v).lower() in {"true", "1"})
        pin["other_districts"] = pin["other_districts"].fillna("")
        dl = t["gold_district_lookup"].copy()
        for c in ("lat", "lon"):
            dl[c] = pd.to_numeric(dl[c], errors="coerce")
        pi = t["gold_place_index"].copy()
        for c in ("lat", "lon", "support"):
            pi[c] = pd.to_numeric(pi[c], errors="coerce")
        nl = t.get("silver_nfhs_long")
        if nl is not None and len(nl):
            nl = nl.copy()
            nl["value"] = pd.to_numeric(nl["value"], errors="coerce")
            for c in ("is_suppressed", "is_low_n"):
                nl[c] = nl[c].map(lambda v: str(v).lower() in {"true", "1"})
        evidence = None
        ev = t.get("gold_facility_service_evidence")
        if ev is not None and len(ev):
            ev = ev.copy()
            ev["evidence_score"] = pd.to_numeric(ev["evidence_score"], errors="coerce")
            ev["n_fields"] = pd.to_numeric(ev["n_fields"], errors="coerce")
            ev["has_strong"] = ev["has_strong"].map(lambda v: v if isinstance(v, (bool, np.bool_)) else str(v).lower() in {"true", "1"})
            for c in ("strong_fields", "fields", "items", "negated"):
                ev[c] = ev[c].map(_json_obj)
            if "attribution_conflict" in ev:
                ev["attribution_conflict"] = ev["attribution_conflict"].map(lambda v: v if isinstance(v, (bool, np.bool_)) else str(v).lower() == "true")
            if "analyst_hold" in ev:
                ev["analyst_hold"] = ev["analyst_hold"].map(lambda v: v if isinstance(v, dict) else (json.loads(v) if isinstance(v, str) and v.startswith("{") else None))
            evidence = {sid: g.drop(columns=["service_id"]).set_index("facility_id") for sid, g in ev.groupby("service_id")}
        rt = cls(fac, pin, dl, pi, nl, t.get("gold_district_nfhs_map"), evidence)
        ai = t.get("gold_ai_claim_checks")
        rt.ai_checks = {}
        if ai is not None and len(ai):
            for r in ai.to_dict("records"):
                ver = r.get("quote_verified")
                ver = ver if isinstance(ver, (bool, np.bool_)) else str(ver).lower() == "true"
                rt.ai_checks[(r["facility_id"], r["service_id"])] = {
                    "verdict": r.get("ai_verdict") if ver else "unverified", "quote": r.get("ai_quote") if ver else None,
                    "reason": r.get("ai_reason"), "model": r.get("model")}
        cal = t.get("gold_evidence_calibration")
        rt.calibration = ({r["level"]: (float(r["pct_confirmed_on_own_website"]), int(r["claims_checked"]))
                           for r in cal.to_dict("records")} if cal is not None and len(cal) else {})
        return rt
