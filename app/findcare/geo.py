"""Geography: distance, PIN/district lookups, and resolving a user's location text.

PIN directory grain is *post office*, not PIN: one PIN can have many rows and even
span districts/states. We aggregate to one row per PIN (median of trimmed coordinates)
and keep the ambiguity visible (n_districts, spread_km) instead of hiding it.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .text import norm

INDIA_BBOX = (6.0, 37.6, 68.0, 97.5)  # lat_min, lat_max, lon_min, lon_max

STATE_ALIASES = {
    "orissa": "odisha",
    "pondicherry": "puducherry",
    "uttaranchal": "uttarakhand",
    "nct of delhi": "delhi",
    "new delhi": "delhi",
    "delhi ncr": "delhi",
    "national capital territory of delhi": "delhi",
    "telengana": "telangana",
    "chhatisgarh": "chhattisgarh",
    "chattisgarh": "chhattisgarh",
    "jammu kashmir": "jammu and kashmir",
    "j and k": "jammu and kashmir",
    "andaman and nicobar": "andaman and nicobar islands",
    "dadra and nagar haveli": "dadra and nagar haveli and daman and diu",
    "daman and diu": "dadra and nagar haveli and daman and diu",
    "the dadra and nagar haveli and daman and diu": "dadra and nagar haveli and daman and diu",
    "tamilnadu": "tamil nadu",
    "maharastra": "maharashtra",
    "west bangal": "west bengal",
    "uttar pradesh up": "uttar pradesh",
    "up": "uttar pradesh",
    "u p": "uttar pradesh",
    "mp": "madhya pradesh",
    "m p": "madhya pradesh",
    "ts": "telangana",
    "tn": "tamil nadu",
    "mh": "maharashtra",
    "wb": "west bengal",
    "hp": "himachal pradesh",
    "jk": "jammu and kashmir",
}

PLACE_ALIASES = {
    "bombay": "mumbai",
    "calcutta": "kolkata",
    "madras": "chennai",
    "bangalore": "bengaluru",
    "bengalooru": "bengaluru",
    "gurgaon": "gurugram",
    "trivandrum": "thiruvananthapuram",
    "allahabad": "prayagraj",
    "poona": "pune",
    "baroda": "vadodara",
    "cochin": "kochi",
    "calicut": "kozhikode",
    "mysore": "mysuru",
    "mangalore": "mangaluru",
    "belgaum": "belagavi",
    "gulbarga": "kalaburagi",
    "benares": "varanasi",
    "banaras": "varanasi",
    "vizag": "visakhapatnam",
    "pondicherry": "puducherry",
    "simla": "shimla",
    "cawnpore": "kanpur",
    "faizabad": "ayodhya",
    "hubli": "hubballi",
    "tuticorin": "thoothukudi",
    "trichy": "tiruchirappalli",
    "tiruchirapalli": "tiruchirappalli",
}

_OFFICE_SUFFIX_RE = re.compile(r"\b(?:b o|s o|h o|g p o|bo|so|ho|gpo|mdg|ndso|east|west)\s*$")


def norm_state(s) -> str:
    n = norm(s)
    n = re.sub(r"^the ", "", n)
    return STATE_ALIASES.get(n, n)


def norm_place(s) -> str:
    n = norm(s)
    n = re.sub(r"\b(?:district|dist|city|town|rural|urban)\b", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    return PLACE_ALIASES.get(n, n)


def norm_office(s) -> str:
    n = norm(s)
    n = _OFFICE_SUFFIX_RE.sub("", n).strip()
    return n


def in_india(lat, lon) -> np.ndarray:
    lat = pd.to_numeric(pd.Series(lat), errors="coerce").to_numpy(dtype=float)
    lon = pd.to_numeric(pd.Series(lon), errors="coerce").to_numpy(dtype=float)
    a, b, c, d = INDIA_BBOX
    return (lat >= a) & (lat <= b) & (lon >= c) & (lon <= d)


def haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = (np.radians(np.asarray(x, dtype=float)) for x in (lat1, lon1, lat2, lon2))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return 6371.0 * 2 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def _trimmed_centers(df: pd.DataFrame, keys: list[str], trim_km: float) -> pd.DataFrame:
    """Per-group median centre after dropping points > trim_km from the first median
    (removes mis-geocoded post offices). Returns keys + lat, lon, spread_km (p90), n_geocoded."""
    v = df.loc[df["geo_valid"], keys + ["latitude", "longitude"]].copy()
    if v.empty:
        return pd.DataFrame(columns=keys + ["lat", "lon", "spread_km", "n_geocoded"])
    m1 = v.groupby(keys)[["latitude", "longitude"]].median().rename(columns={"latitude": "_mlat", "longitude": "_mlon"})
    v = v.join(m1, on=keys)
    v["_d"] = haversine_km(v["latitude"], v["longitude"], v["_mlat"], v["_mlon"])
    kept = v[v["_d"] <= trim_km]
    m2 = kept.groupby(keys)[["latitude", "longitude"]].median().rename(columns={"latitude": "lat", "longitude": "lon"})
    m = m1.join(m2, how="left")
    m["lat"] = m["lat"].fillna(m["_mlat"])
    m["lon"] = m["lon"].fillna(m["_mlon"])
    v = v.drop(columns=["_mlat", "_mlon"]).join(m[["lat", "lon"]], on=keys)
    v["_d2"] = haversine_km(v["latitude"], v["longitude"], v["lat"], v["lon"])
    stats = v.groupby(keys).agg(spread_km=("_d2", lambda s: float(np.percentile(s, 90))), n_geocoded=("_d2", "size"))
    return m[["lat", "lon"]].join(stats).reset_index()


def build_pin_lookup(pins: pd.DataFrame) -> pd.DataFrame:
    """One row per PIN: centre, primary district/state, and ambiguity stats.

    `pins` must be the cleaned directory (see pipeline.clean_pincodes).
    """
    centres = _trimmed_centers(pins, ["pincode"], trim_km=50.0)
    dc = (pins.groupby(["pincode", "state_norm", "district_norm"])
          .agg(n=("officename", "size"), district=("district", "first"), state=("statename", "first"))
          .reset_index().sort_values(["pincode", "n"], ascending=[True, False]))
    dc["_label"] = dc["district"].astype(str).str.title() + " (" + dc["state"].astype(str).str.title() + ")"
    primary = dc.drop_duplicates("pincode")
    others = (dc[dc.duplicated("pincode")].groupby("pincode")["_label"].agg("; ".join).rename("other_districts"))
    n_d = dc.groupby("pincode").size().rename("n_districts")
    offices = pins.groupby("pincode").agg(
        n_offices=("officename", "size"),
        office_names=("officename", lambda s: "; ".join(sorted(s.astype(str).unique())[:12])))
    out = (primary[["pincode", "district", "state", "district_norm", "state_norm"]]
           .merge(centres, on="pincode", how="left")
           .join(n_d, on="pincode").join(others, on="pincode").join(offices, on="pincode"))
    out["other_districts"] = out["other_districts"].fillna("")
    out["n_geocoded"] = out["n_geocoded"].fillna(0).astype(int)
    out["is_ambiguous"] = out["n_districts"] > 1
    return out[["pincode", "lat", "lon", "n_offices", "n_geocoded", "spread_km", "district", "state",
                "district_norm", "state_norm", "n_districts", "other_districts", "office_names", "is_ambiguous"]]


def build_district_lookup(pins: pd.DataFrame) -> pd.DataFrame:
    keys = ["state_norm", "district_norm"]
    pins = pins[(pins["district_norm"] != "") & (pins["state_norm"] != "")]
    centres = _trimmed_centers(pins, keys, trim_km=120.0)
    base = pins.groupby(keys).agg(district=("district", "first"), state=("statename", "first"),
                                  n_pins=("pincode", "nunique")).reset_index()
    out = base.merge(centres.drop(columns=["n_geocoded"]), on=keys, how="left")
    return out[["state_norm", "district_norm", "district", "state", "lat", "lon", "n_pins", "spread_km"]]


def build_place_index(pins: pd.DataFrame, district_lookup: pd.DataFrame, facilities: pd.DataFrame | None = None) -> pd.DataFrame:
    """Searchable names -> coordinates. kinds: city (from facility records), district, office."""
    parts = []
    dl = district_lookup.dropna(subset=["lat", "lon"])
    parts.append(pd.DataFrame({
        "name_norm": dl["district_norm"], "display": dl["district"].str.title() + ", " + dl["state"].str.title(),
        "state_norm": dl["state_norm"], "district_norm": dl["district_norm"],
        "lat": dl["lat"], "lon": dl["lon"], "kind": "district", "support": dl["n_pins"],
    }))
    offices = pins[pins["geo_valid"]].copy()
    offices["name_norm"] = offices["officename"].map(norm_office)
    og = offices.groupby(["name_norm", "state_norm", "district_norm"]).agg(
        lat=("latitude", "median"), lon=("longitude", "median"), support=("pincode", "size"),
        display=("officename", "first"), district=("district", "first"), state=("statename", "first"),
        is_major=("officetype", lambda t: bool(set(map(str, t)) & {"HO", "PO", "H.O", "S.O", "SO"})),
    ).reset_index()
    og["display"] = og["display"].str.replace(r"\s+(B\.?O|S\.?O|H\.?O)\.?$", "", regex=True).str.title() + \
        ", " + og["district"].str.title() + ", " + og["state"].str.title()
    og["kind"] = np.where(og["is_major"], "office_major", "office")
    parts.append(og[["name_norm", "display", "state_norm", "district_norm", "lat", "lon", "kind", "support"]])
    if facilities is not None and len(facilities):
        f = facilities[facilities["coord_valid"] & (facilities["city_norm"] != "")]
        if len(f):
            known_states = set(district_lookup["state_norm"])
            f = f[f["state_norm"].isin(known_states)]  # 'state' fields sometimes hold a locality or abbreviation
            mode = lambda s: s.dropna().value_counts().index[0] if s.notna().any() else None
            fg = f.groupby(["city_norm", "state_norm"]).agg(
                lat=("latitude", "median"), lon=("longitude", "median"), support=("facility_id", "size"),
                display=("city", "first"), state=("state", "first"), district_norm=("district_norm", mode),
            ).reset_index()
            fg = fg[fg["support"] >= 2]
            parts.append(pd.DataFrame({
                "name_norm": fg["city_norm"], "display": fg["display"].astype(str).str.title() + ", " + fg["state"].astype(str).str.title(),
                "state_norm": fg["state_norm"], "district_norm": fg["district_norm"],
                "lat": fg["lat"], "lon": fg["lon"], "kind": "city", "support": fg["support"],
            }))
    idx = pd.concat(parts, ignore_index=True)
    idx = idx[idx["name_norm"].astype(str).str.len() > 1]
    return idx


KIND_PRIORITY = {"city": 0, "district": 1, "office_major": 2, "office": 3}
KIND_PRECISION_KM = {"city": 8.0, "district": 30.0, "office_major": 5.0, "office": 5.0}


@dataclass
class Location:
    ok: bool
    lat: float | None = None
    lon: float | None = None
    label: str = ""
    method: str = ""
    precision_km: float | None = None
    district_norm: str | None = None
    state_norm: str | None = None
    district: str | None = None
    state: str | None = None
    warnings: list[str] = field(default_factory=list)
    alternatives: list[dict] = field(default_factory=list)
    error: str | None = None


def nearest_district(lat: float, lon: float, district_lookup: pd.DataFrame) -> pd.Series | None:
    dl = district_lookup.dropna(subset=["lat", "lon"])
    if dl.empty:
        return None
    d = haversine_km(lat, lon, dl["lat"].to_numpy(), dl["lon"].to_numpy())
    return dl.iloc[int(np.argmin(d))]


_COORD_RE = re.compile(r"^\s*\(?\s*(-?\d{1,3}(?:\.\d+)?)\s*[,;\s]\s*(-?\d{1,3}(?:\.\d+)?)\s*\)?\s*$")
_PIN_RE = re.compile(r"(?<!\d)([1-9]\d{2})\s?(\d{3})(?!\d)")


def resolve_location(query: str, pin_lookup: pd.DataFrame, district_lookup: pd.DataFrame,
                     place_index: pd.DataFrame, choice: int | None = None) -> Location:
    """Turn free text (city / district / PIN / 'lat, lon') into a point, with honesty about precision."""
    q = (query or "").strip()
    if not q:
        return Location(ok=False, error="Please enter a place, PIN code or coordinates.")

    m = _COORD_RE.match(q)
    if m:
        a, b = float(m.group(1)), float(m.group(2))
        warnings = []
        if not in_india([a], [b])[0] and in_india([b], [a])[0]:
            a, b = b, a
            warnings.append("Coordinates looked like (longitude, latitude); swapped them.")
        if not in_india([a], [b])[0]:
            return Location(ok=False, error=f"({a}, {b}) is outside India.")
        nd = nearest_district(a, b, district_lookup)
        loc = Location(ok=True, lat=a, lon=b, label=f"{a:.4f}, {b:.4f}", method="coordinates", precision_km=0.5, warnings=warnings)
        if nd is not None:
            loc.district_norm, loc.state_norm = nd["district_norm"], nd["state_norm"]
            loc.district, loc.state = str(nd["district"]).title(), str(nd["state"]).title()
            loc.warnings.append(f"District context assigned by nearest district centre ({loc.district}); approximate near borders.")
        return loc

    m = _PIN_RE.search(q)
    if m and len(re.sub(r"\D", "", q)) == 6:
        pin = m.group(1) + m.group(2)
        row = pin_lookup[pin_lookup["pincode"] == pin]
        if row.empty:
            prefix = pin_lookup[pin_lookup["pincode"].str[:3] == pin[:3]].dropna(subset=["lat"])
            if prefix.empty:
                return Location(ok=False, error=f"PIN {pin} is not in the India Post directory.")
            r = prefix.iloc[0]
            return Location(ok=True, lat=float(prefix["lat"].median()), lon=float(prefix["lon"].median()),
                            label=f"PIN area {pin[:3]}xxx", method="pin_prefix", precision_km=40.0,
                            district_norm=r["district_norm"], state_norm=r["state_norm"],
                            district=str(r["district"]).title(), state=str(r["state"]).title(),
                            warnings=[f"PIN {pin} not found; using the centre of nearby PINs starting {pin[:3]} (rough)."])
        r = row.iloc[0]
        warnings = []
        lat, lon = r["lat"], r["lon"]
        method, precision = "pincode", max(3.0, float(r["spread_km"]) if pd.notna(r["spread_km"]) else 10.0)
        if pd.isna(lat):
            dl = district_lookup[(district_lookup["state_norm"] == r["state_norm"]) & (district_lookup["district_norm"] == r["district_norm"])]
            if dl.empty or pd.isna(dl.iloc[0]["lat"]):
                return Location(ok=False, error=f"PIN {pin} has no coordinates in the directory.")
            lat, lon, method, precision = dl.iloc[0]["lat"], dl.iloc[0]["lon"], "pincode_district_centre", 30.0
            warnings.append(f"No post office in PIN {pin} is geocoded; using the {str(r['district']).title()} district centre.")
        if r["is_ambiguous"]:
            warnings.append(f"PIN {pin} spans more than one district (also: {r['other_districts']}). Using the most common: {str(r['district']).title()}.")
        if precision > 15:
            warnings.append(f"Post offices in this PIN are spread over ~{precision:.0f} km; distances are approximate.")
        return Location(ok=True, lat=float(lat), lon=float(lon), label=f"PIN {pin} ({str(r['district']).title()}, {str(r['state']).title()})",
                        method=method, precision_km=precision, district_norm=r["district_norm"], state_norm=r["state_norm"],
                        district=str(r["district"]).title(), state=str(r["state"]).title(), warnings=warnings)

    # Text: "place" or "place, state"
    parts = [p.strip() for p in q.split(",") if p.strip()]
    place = norm_place(parts[0])
    state_filter = norm_state(parts[-1]) if len(parts) > 1 else None
    full_alias = PLACE_ALIASES.get(norm(q))
    if full_alias:
        place = full_alias
    cands = place_index[place_index["name_norm"] == place]
    if state_filter:
        known_states = set(place_index["state_norm"])
        if state_filter in known_states:
            c2 = cands[cands["state_norm"] == state_filter]
            if not c2.empty:
                cands = c2
    warnings: list[str] = []
    if cands.empty:
        names = place_index["name_norm"].drop_duplicates().tolist()
        close = difflib.get_close_matches(place, names, n=3, cutoff=0.84)
        if not close:
            return Location(ok=False, error=f"Couldn't find '{parts[0]}'. Try the district name, a 6-digit PIN code, or 'lat, lon'.")
        cands = place_index[place_index["name_norm"] == close[0]]
        if state_filter and state_filter in set(cands["state_norm"]):
            cands = cands[cands["state_norm"] == state_filter]
        warnings.append(f"No exact match for '{parts[0]}'; using closest name '{close[0].title()}'.")

    cands = cands.assign(_p=cands["kind"].map(KIND_PRIORITY)).sort_values(["_p", "support"], ascending=[True, False])
    # Ambiguity only among places of comparable standing: a city/district named X beats a village
    # post office named X elsewhere ("Jaipur" should not offer a branch office in another state).
    best_p = cands["_p"].min()
    cands = cands[cands["_p"] <= max(best_p, 1)]
    # genuinely different places only: drop candidates within 25 km of a better-ranked one
    kept = []
    for r in cands.itertuples():
        if all(haversine_km(r.lat, r.lon, k.lat, k.lon) > 25 for k in kept):
            kept.append(r)
        if len(kept) == 8:
            break
    distinct = cands.loc[[k.Index for k in kept]]
    distinct = distinct.sort_values(["support"], ascending=False) if distinct["state_norm"].nunique() > 1 else distinct
    alternatives = [{"display": r.display, "kind": r.kind, "lat": float(r.lat), "lon": float(r.lon),
                     "state_norm": r.state_norm, "district_norm": r.district_norm} for r in distinct.itertuples()]
    pick = alternatives[choice] if choice is not None and 0 <= choice < len(alternatives) else alternatives[0]
    if len(alternatives) > 1 and choice is None:
        warnings.append(f"'{parts[0]}' matches {len(alternatives)} places; showing {pick['display']}. Add the state (e.g. '{parts[0]}, {alternatives[1]['display'].split(', ')[-1]}') to choose another.")
    dl = district_lookup[(district_lookup["state_norm"] == pick["state_norm"]) & (district_lookup["district_norm"] == pick["district_norm"])]
    district = str(dl.iloc[0]["district"]).title() if not dl.empty else (pick["district_norm"] or "").title()
    state = str(dl.iloc[0]["state"]).title() if not dl.empty else (pick["state_norm"] or "").title()
    return Location(ok=True, lat=pick["lat"], lon=pick["lon"], label=pick["display"], method=f"name:{pick['kind']}",
                    precision_km=KIND_PRECISION_KM.get(pick["kind"], 10.0), district_norm=pick["district_norm"],
                    state_norm=pick["state_norm"], district=district, state=state, warnings=warnings, alternatives=alternatives)


# ----------------------------------------------------------------------------- structured location pickers (UI)
def city_options(place_index: pd.DataFrame) -> pd.DataFrame:
    """Cities known from facility records (>= 2 facilities), one row per city+state, for a dropdown."""
    c = place_index[place_index["kind"] == "city"].dropna(subset=["lat", "lon"]).copy()
    c["label"] = c["name_norm"].str.title() + ", " + c["state_norm"].str.title()
    return c.sort_values(["label"]).drop_duplicates("label").reset_index(drop=True)


def _titles(district_lookup: pd.DataFrame, state_norm, district_norm):
    d = district_lookup[(district_lookup["state_norm"] == state_norm) & (district_lookup["district_norm"] == district_norm)]
    if d.empty:
        return (district_norm or "").title() or None, (state_norm or "").title() or None
    return str(d.iloc[0]["district"]).title(), str(d.iloc[0]["state"]).title()


def location_from_city(row, district_lookup: pd.DataFrame) -> Location:
    district, state = _titles(district_lookup, row["state_norm"], row["district_norm"])
    return Location(ok=True, lat=float(row["lat"]), lon=float(row["lon"]), label=row["label"], method="name:city",
                    precision_km=KIND_PRECISION_KM["city"], district_norm=row["district_norm"], state_norm=row["state_norm"],
                    district=district, state=state)


def location_from_district(state_norm: str, district_norm: str, district_lookup: pd.DataFrame) -> Location:
    d = district_lookup[(district_lookup["state_norm"] == state_norm) & (district_lookup["district_norm"] == district_norm)]
    if d.empty or pd.isna(d.iloc[0]["lat"]):
        return Location(ok=False, error="No coordinates for this district in the PIN directory.")
    r = d.iloc[0]
    district, state = str(r["district"]).title(), str(r["state"]).title()
    loc = Location(ok=True, lat=float(r["lat"]), lon=float(r["lon"]), label=f"{district} district, {state}", method="name:district",
                   precision_km=KIND_PRECISION_KM["district"], district_norm=district_norm, state_norm=state_norm,
                   district=district, state=state)
    loc.warnings.append("Searching from the district centre (median of its post offices); for a precise start point, use a PIN or the map.")
    return loc


def location_from_point(lat: float, lon: float, district_lookup: pd.DataFrame) -> Location:
    loc = resolve_location(f"{lat}, {lon}", pd.DataFrame(columns=["pincode"]), district_lookup, pd.DataFrame(columns=["name_norm"]))
    if loc.ok:
        loc.label = f"the pinned point ({lat:.4f}, {lon:.4f})" + (f" · {loc.district}, {loc.state}" if loc.district else "")
    return loc
