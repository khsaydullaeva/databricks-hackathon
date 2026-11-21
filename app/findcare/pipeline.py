"""Bronze -> Silver -> Gold transformations, in pandas (10k facilities fits comfortably).

Notebooks call these functions and persist the results as Delta tables; the app loads the
gold tables back and rebuilds the in-memory `Runtime` with `Runtime.from_tables`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import columns as colres
from .evidence import compute_evidence
from .geo import (build_district_lookup, build_pin_lookup, build_place_index, in_india, norm_place,
                  norm_state)
from .nfhs import match_districts
from .quality import apply_breadth_flag, assess
from .services import EMERGENCY_24X7_RE, SERVICES
from .text import clean_pincode, from_json_list, is_missing, norm, parse_list, to_float

LIST_FIELDS = ("specialties", "procedure", "equipment", "capability")
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
        out[f"{f}_list"] = first(f).map(parse_list)
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
        lambda r: ", ".join(dict.fromkeys(p for v in r for p in parse_list(v)))[:120] or None, axis=1) if m["phone"] else None
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
    df["geo_valid"] = in_india(df["latitude"], df["longitude"])
    for c in ("officename", "district", "statename", "officetype"):
        df[c] = df[c].astype(str).str.strip()
    df["district_norm"] = df["district"].map(norm_place)
    df["state_norm"] = df["statename"].map(norm_state)
    return df.reset_index(drop=True)


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
               log=print) -> dict[str, pd.DataFrame]:
    log("building PIN and district lookups…")
    pin_lookup = build_pin_lookup(silver_pins)
    district_lookup = build_district_lookup(silver_pins)
    log("assessing record quality…")
    fac = assess(silver_fac, pin_lookup, district_lookup)
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
    fac = apply_breadth_flag(fac, strong_counts, len(SERVICES))
    place_index = build_place_index(silver_pins, district_lookup, fac)
    out = {
        "gold_facilities": fac,
        "gold_facility_service_evidence": evidence,
        "gold_pincode_lookup": pin_lookup,
        "gold_district_lookup": district_lookup,
        "gold_place_index": place_index,
    }
    if nfhs_wide is not None and len(nfhs_wide):
        out["gold_district_nfhs_map"] = match_districts(nfhs_wide, district_lookup)
    out["gold_district_service_coverage"] = district_coverage(fac, evidence, district_lookup)
    return out


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
        for c in ("coord_valid", "has_24x7"):
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
            evidence = {sid: g.drop(columns=["service_id"]).set_index("facility_id") for sid, g in ev.groupby("service_id")}
        return cls(fac, pin, dl, pi, nl, t.get("gold_district_nfhs_map"), evidence)
