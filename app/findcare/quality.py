"""Record-level trust: is this record internally consistent and backed by sources?

Every check produces a flag {code, message, penalty}. The trust score is

    trust = clamp(1 - sum(penalties), 0.1, 1) x (0.85 + 0.15 x completeness)

where completeness = share of {doctors, beds, year established, equipment} present.
Flags are shown to the user verbatim as "Cautions".
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from .geo import haversine_km
from .text import find_pincodes

PENALTY = {
    "no_location": 0.35,
    "approx_location": 0.10,
    "coords_vs_pin": 0.20,
    "state_mismatch": 0.15,
    "shared_coords": 0.10,
    "multi_site_text": 0.15,
    "no_sources": 0.10,
    "breadth_outlier": 0.10,
    "duplicate_record": 0.0,  # informational; handled by de-duplication in ranking
}
COORD_PIN_MAX_KM = 40.0


def _flag(code: str, message: str) -> dict:
    return {"code": code, "message": message, "penalty": PENALTY.get(code, 0.0)}


def assess(fac: pd.DataFrame, pin_lookup: pd.DataFrame, district_lookup: pd.DataFrame,
           places: pd.DataFrame | None = None) -> pd.DataFrame:
    """Add effective location, district assignment and trust flags to silver facilities.

    `places` (district + post-office centres, see geo.build_place_index) lets coordinate/postcode
    conflicts be settled by the record's own city name."""
    f = fac.copy()
    pl = pin_lookup.set_index("pincode")
    f = f.join(pl[["lat", "lon", "district", "state", "district_norm", "state_norm", "is_ambiguous", "spread_km"]]
               .add_prefix("pin_"), on="postcode")

    # --- coordinates vs postcode -------------------------------------------------
    has_both = f["coord_valid"] & f["pin_lat"].notna()
    f["coord_pin_km"] = np.where(has_both, haversine_km(f["latitude"].fillna(0), f["longitude"].fillna(0),
                                                         f["pin_lat"].fillna(0), f["pin_lon"].fillna(0)), np.nan)

    # --- conflict resolution: coordinates vs postcode -----------------------------
    # 1) If the record's city can be located (district or post-office name in its state), use whichever
    #    of {coordinates, postcode centre} is closer to that city.
    # 2) Otherwise let the city/state text vote; ties keep the coordinates.
    conflict = (f["coord_pin_km"] > COORD_PIN_MAX_KM) & (f["pin_spread_km"].fillna(0) < COORD_PIN_MAX_KM)
    f["use_pin_over_coords"] = False
    f["conflict_basis"] = None
    if conflict.any() and places is not None and len(places):
        p = places[places["kind"].isin(["district", "office_major", "office"])].dropna(subset=["lat", "lon"])
        p = p.assign(_p=p["kind"].map({"district": 0, "office_major": 1, "office": 2})).sort_values(["_p", "support"], ascending=[True, False])
        centres = p.drop_duplicates(["name_norm", "state_norm"]).set_index(["name_norm", "state_norm"])[["lat", "lon"]]
        for i in f.index[conflict]:
            r = f.loc[i]
            key = next((k for k in ((r["city_norm"], r["state_norm"]), (r["city_norm"], r["pin_state_norm"])) if k in centres.index), None)
            if not r["city_norm"] or key is None:
                continue
            clat, clon = centres.loc[key, ["lat", "lon"]]
            d_coords = float(haversine_km(r["latitude"], r["longitude"], clat, clon))
            d_pin = float(haversine_km(r["pin_lat"], r["pin_lon"], clat, clon))
            f.at[i, "use_pin_over_coords"] = d_pin < d_coords
            f.at[i, "conflict_basis"] = (f"the postcode is {d_pin:.0f} km and the coordinates {d_coords:.0f} km from "
                                         f"{str(r['city']).title()}, the city named in the record")
    rest = conflict & f["conflict_basis"].isna()
    dl0 = district_lookup.dropna(subset=["lat", "lon"]).reset_index(drop=True)
    if rest.any() and len(dl0):
        idx = _nearest_idx(f.loc[rest, "latitude"].to_numpy(), f.loc[rest, "longitude"].to_numpy(),
                           dl0["lat"].to_numpy(), dl0["lon"].to_numpy())
        c = f.loc[rest]
        c_state, c_dist = dl0.loc[idx, "state_norm"].to_numpy(), dl0.loc[idx, "district_norm"].to_numpy()
        vote_pin = (c["state_norm"] == c["pin_state_norm"]).astype(int) + (c["city_norm"] == c["pin_district_norm"]).astype(int)
        vote_coords = (c["state_norm"].to_numpy() == c_state).astype(int) + (c["city_norm"].to_numpy() == c_dist).astype(int)
        f.loc[rest, "use_pin_over_coords"] = (vote_pin.to_numpy() > vote_coords)
        f.loc[rest, "conflict_basis"] = "the record's city/state text agrees better with " + np.where(
            vote_pin.to_numpy() > vote_coords, "the postcode", "the coordinates (or neither)")

    # --- effective location used for distance -----------------------------------
    use_coords = f["coord_valid"] & ~f["use_pin_over_coords"]
    f["eff_lat"] = np.where(use_coords, f["latitude"], f["pin_lat"])
    f["eff_lon"] = np.where(use_coords, f["longitude"], f["pin_lon"])
    f["location_basis"] = np.select(
        [use_coords, f["use_pin_over_coords"], f["pin_lat"].notna()],
        ["facility coordinates", "postcode centre (coordinates contradicted address)", "postcode centre (approximate)"],
        default="unknown")

    # --- district assignment (for NFHS context) ---------------------------------
    f["district_norm"] = f["pin_district_norm"]
    f["district_state_norm"] = f["pin_state_norm"]
    f["district_method"] = np.where(f["pin_district_norm"].notna(), "postcode", None)
    need = f["district_norm"].isna() & f["coord_valid"]
    dl = district_lookup.dropna(subset=["lat", "lon"]).reset_index(drop=True)
    if need.any() and len(dl):
        idx = _nearest_idx(f.loc[need, "latitude"].to_numpy(), f.loc[need, "longitude"].to_numpy(),
                           dl["lat"].to_numpy(), dl["lon"].to_numpy())
        f.loc[need, "district_norm"] = dl.loc[idx, "district_norm"].to_numpy()
        f.loc[need, "district_state_norm"] = dl.loc[idx, "state_norm"].to_numpy()
        f.loc[need, "district_method"] = "nearest district centre (approximate)"

    # --- shared coordinates (city-level geocodes) ---------------------------------
    key = f["latitude"].round(4).astype(str) + "," + f["longitude"].round(4).astype(str)
    names_per_point = f[f["coord_valid"]].groupby(key[f["coord_valid"]])["name_norm"].nunique()
    f["n_shared_coords"] = key.map(names_per_point).fillna(0).astype(int)

    # --- duplicate records (same name, within 2 km) --------------------------------
    f["dup_group"] = _duplicate_groups(f)

    known_states = set(pin_lookup["state_norm"].dropna()) - {""}  # record 'state' fields sometimes hold a locality
    flags_col, trust_col, completeness_col = [], [], []
    for r in f.itertuples(index=False):
        flags = []
        if r.location_basis == "unknown":
            flags.append(_flag("no_location", "No usable coordinates or postcode – distance cannot be computed."))
        elif r.location_basis == "postcode centre (approximate)":
            flags.append(_flag("approx_location", f"No valid coordinates; location approximated from postcode {r.postcode}."))
        if pd.notna(r.coord_pin_km) and r.coord_pin_km > COORD_PIN_MAX_KM and (pd.isna(r.pin_spread_km) or r.pin_spread_km < COORD_PIN_MAX_KM):
            which = (f"Using the postcode location: {r.conflict_basis}." if r.use_pin_over_coords
                     else f"Using the coordinates ({r.conflict_basis}), but the true location is uncertain." if r.conflict_basis
                     else "Using the coordinates, but the true location is uncertain.")
            flags.append(_flag("coords_vs_pin", f"Coordinates are {r.coord_pin_km:.0f} km from the centre of its postcode {r.postcode} – one of them is wrong. {which}"))
        if r.state_norm in known_states and isinstance(r.pin_state_norm, str) and r.pin_state_norm and r.state_norm != r.pin_state_norm:
            flags.append(_flag("state_mismatch", f"Record says state '{r.state}', but postcode {r.postcode} belongs to {str(r.pin_state).title()}."))
        if r.n_shared_coords >= 4:
            flags.append(_flag("shared_coords", f"{r.n_shared_coords - 1} other facilities share the exact same coordinates – likely a city-level geocode, not the building."))
        pins_in_text = [p for p in find_pincodes(" ".join(str(x) for x in (r.address_text, r.description) if isinstance(x, str))) if p != r.postcode]
        other_districts = {pl.loc[p, "district_norm"] for p in pins_in_text if p in pl.index} - {r.district_norm}
        if other_districts:
            flags.append(_flag("multi_site_text", f"Record text mentions postcodes in other districts ({', '.join(sorted(d.title() for d in other_districts))}) – it may merge more than one site."))
        if not r.source_urls_list:
            flags.append(_flag("no_sources", "No source URL recorded – claims cannot be traced to a web page."))
        present = [pd.notna(r.num_doctors), pd.notna(r.capacity), pd.notna(r.year_established), bool(r.equipment_list)]
        completeness = sum(present) / len(present)
        base = max(0.1, min(1.0, 1.0 - sum(fl["penalty"] for fl in flags)))
        trust_col.append(round(base * (0.85 + 0.15 * completeness), 4))
        completeness_col.append(completeness)
        flags_col.append(flags)
    f["trust_flags"] = flags_col
    f["trust_score"] = trust_col
    f["completeness"] = completeness_col
    return f


def apply_breadth_flag(f: pd.DataFrame, n_strong_services: pd.Series, n_services: int) -> pd.DataFrame:
    """Flag records that claim (with strong terms) an unusually large share of all services
    while giving no evidence of the scale that would require."""
    f = f.copy()
    counts = f["facility_id"].map(n_strong_services).fillna(0).astype(int)
    f["n_services_claimed"] = counts
    threshold = max(10, int(np.ceil(0.65 * n_services)))
    small_or_unknown = f["capacity"].isna() | (f["capacity"] < 50)
    # teaching hospitals legitimately offer most services
    teaching = f["name"].fillna("").str.contains(r"medical college|aiims|institute of medical sciences|pgimer|medical sciences", case=False, regex=True)
    hit = (counts >= threshold) & small_or_unknown & ~teaching
    for i in f.index[hit]:
        fl = _flag("breadth_outlier", f"Claims {counts[i]} of {n_services} service types with no recorded bed count of 50+ – may be a generic or merged listing.")
        f.at[i, "trust_flags"] = list(f.at[i, "trust_flags"]) + [fl]
        f.at[i, "trust_score"] = round(max(0.1, f.at[i, "trust_score"] - fl["penalty"]), 4)
    return f


def _nearest_idx(lat, lon, clat, clon, chunk: int = 2000) -> np.ndarray:
    out = np.empty(len(lat), dtype=int)
    for s in range(0, len(lat), chunk):
        d = haversine_km(lat[s:s + chunk, None], lon[s:s + chunk, None], clat[None, :], clon[None, :])
        out[s:s + chunk] = d.argmin(axis=1)
    return out


def _duplicate_groups(f: pd.DataFrame) -> pd.Series:
    """Records with the same normalised name within 2 km get the same group id."""
    groups = pd.Series(f["facility_id"].to_numpy(), index=f.index, dtype=object)
    for _, g in f[f["eff_lat"].notna() & (f["name_norm"] != "")].groupby("name_norm"):
        if len(g) < 2:
            continue
        lat, lon = g["eff_lat"].to_numpy(float), g["eff_lon"].to_numpy(float)
        ids = g["facility_id"].to_numpy()
        assigned = {}
        for i in range(len(g)):
            if i in assigned:
                continue
            assigned[i] = ids[i]
            d = haversine_km(lat[i], lon[i], lat, lon)
            for j in np.where(d <= 2.0)[0]:
                assigned.setdefault(int(j), ids[i])
        for i, gid in assigned.items():
            groups.loc[g.index[i]] = gid
    return groups


def flags_to_json(flags) -> str:
    return json.dumps(list(flags), ensure_ascii=False)
