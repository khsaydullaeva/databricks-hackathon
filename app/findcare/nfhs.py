"""NFHS-5 district indicators: cleaning and matching to PIN-directory districts.

* Column names -> snake_case (original names are kept in a mapping table).
* '*'  -> NULL (suppressed), never zero.
* '(29.5)' -> 29.5 with is_low_n = True (based on 25-49 unweighted cases; use with caution).
* District names are matched to the PIN directory within the same state: exact first,
  then fuzzy (difflib ratio >= 0.80). The match method and score are stored so the UI can
  say "matched by approximate name".
"""

from __future__ import annotations

import difflib
import re

import numpy as np
import pandas as pd

from .geo import norm_place, norm_state


def snake(name: str, maxlen: int = 120) -> str:
    s = re.sub(r"[^0-9a-zA-Z]+", "_", str(name).strip().lower()).strip("_")
    s = re.sub(r"_+", "_", s)
    if s and s[0].isdigit():
        s = "c_" + s
    return s[:maxlen] or "col"


def snake_columns(cols: list[str]) -> list[str]:
    out, seen = [], {}
    for c in cols:
        s = snake(c)
        if s in seen:
            seen[s] += 1
            s = f"{s}_{seen[s]}"
        else:
            seen[s] = 0
        out.append(s)
    return out


def parse_value(v) -> tuple[float | None, bool, bool]:
    """-> (value, is_suppressed, is_low_n)"""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None, False, False
    s = str(v).strip()
    if s in {"", "NA", "na", "-", "nan"}:
        return None, False, False
    if "*" in s:
        return None, True, False
    low_n = s.startswith("(") and s.endswith(")")
    s2 = s.strip("()").replace(",", "").replace("%", "").strip()
    try:
        return float(s2), False, low_n
    except ValueError:
        return None, False, False


def clean_nfhs(raw: pd.DataFrame, original_names: list[str] | None = None):
    """Return (wide, long, column_map). `raw` may already have snake_case column names;
    pass `original_names` (same order) to keep human-readable indicator names."""
    original_names = original_names or list(raw.columns)
    snake_names = snake_columns(list(raw.columns))
    df = raw.copy()
    df.columns = snake_names
    colmap = pd.DataFrame({"column": snake_names, "original_name": original_names})
    dcol = next((c for c in snake_names if "district" in c), None)
    scol = next((c for c in snake_names if c.startswith("state") or "state_ut" in c or c == "state"), None)
    if dcol is None or scol is None:
        raise ValueError(f"Could not find district/state columns in NFHS file: {snake_names[:6]}")
    ind_cols = [c for c in snake_names if c not in (dcol, scol)]
    base = pd.DataFrame({
        "state": df[scol].astype(str).str.strip(),
        "district": df[dcol].astype(str).str.strip(),
    })
    base["state_norm"] = base["state"].map(norm_state)
    base["district_norm"] = base["district"].map(norm_place)
    wide = base.copy()
    long_rows = []
    orig = dict(zip(colmap["column"], colmap["original_name"]))
    for c in ind_cols:
        parsed = df[c].map(parse_value)
        vals = parsed.map(lambda t: t[0])
        numeric_share = vals.notna().mean()
        if numeric_share < 0.2 and not parsed.map(lambda t: t[1]).any():
            continue  # non-indicator text column
        wide[c] = vals.astype(float)
        long_rows.append(pd.DataFrame({
            "state": base["state"], "district": base["district"],
            "state_norm": base["state_norm"], "district_norm": base["district_norm"],
            "indicator_code": c, "indicator": orig.get(c, c),
            "value": vals.astype(float),
            "is_suppressed": parsed.map(lambda t: t[1]).astype(bool),
            "is_low_n": parsed.map(lambda t: t[2]).astype(bool),
            "raw_value": df[c].astype(str),
        }))
    long = pd.concat(long_rows, ignore_index=True) if long_rows else pd.DataFrame()
    return wide, long, colmap


def match_districts(nfhs_wide: pd.DataFrame, district_lookup: pd.DataFrame, cutoff: float = 0.80) -> pd.DataFrame:
    """Map each PIN-directory district to an NFHS district (state-constrained)."""
    nf = nfhs_wide[["state_norm", "district_norm", "district", "state"]].drop_duplicates()
    nf_states = set(nf["state_norm"])
    rows = []
    for r in district_lookup[["state_norm", "district_norm"]].drop_duplicates().itertuples(index=False):
        st = r.state_norm if r.state_norm in nf_states else _closest(r.state_norm, nf_states, 0.85)
        if st is None:
            rows.append((r.state_norm, r.district_norm, None, None, "unmatched_state", 0.0))
            continue
        pool = nf[nf["state_norm"] == st]
        names = pool["district_norm"].tolist()
        if r.district_norm in names:
            rows.append((r.state_norm, r.district_norm, st, r.district_norm, "exact", 1.0))
            continue
        best = difflib.get_close_matches(r.district_norm, names, n=1, cutoff=cutoff)
        if best:
            score = difflib.SequenceMatcher(None, r.district_norm, best[0]).ratio()
            rows.append((r.state_norm, r.district_norm, st, best[0], "fuzzy_name", round(score, 3)))
        else:
            rows.append((r.state_norm, r.district_norm, st, None, "unmatched", 0.0))
    return pd.DataFrame(rows, columns=["state_norm", "district_norm", "nfhs_state_norm", "nfhs_district_norm", "match_method", "match_score"])


def _closest(x, pool, cutoff):
    m = difflib.get_close_matches(x or "", list(pool), n=1, cutoff=cutoff)
    return m[0] if m else None


# Always-useful context indicators (matched by keyword in the indicator name)
GENERAL_KEYWORDS = ("health insurance", "financing scheme")


def district_context(nfhs_long: pd.DataFrame, district_map: pd.DataFrame, state_norm: str | None,
                     district_norm: str | None, keywords: tuple[str, ...], max_rows: int = 8) -> dict:
    """Indicators for one district relevant to a service, with the state average for comparison."""
    if nfhs_long is None or nfhs_long.empty or not district_norm:
        return {"ok": False, "note": "No district identified for this location."}
    m = district_map[(district_map["state_norm"] == state_norm) & (district_map["district_norm"] == district_norm)]
    if m.empty or pd.isna(m.iloc[0]["nfhs_district_norm"]):
        return {"ok": False, "note": f"District '{(district_norm or '').title()}' could not be matched to NFHS-5 (names differ between datasets)."}
    mrow = m.iloc[0]
    d = nfhs_long[(nfhs_long["state_norm"] == mrow["nfhs_state_norm"]) & (nfhs_long["district_norm"] == mrow["nfhs_district_norm"])]
    if d.empty:
        return {"ok": False, "note": "No NFHS-5 indicators for this district."}
    kw = tuple(k.lower() for k in keywords) + GENERAL_KEYWORDS
    sel = d[d["indicator"].str.lower().apply(lambda s: any(k in s for k in kw))].head(max_rows)
    state_avg = (nfhs_long[nfhs_long["state_norm"] == mrow["nfhs_state_norm"]]
                 .groupby("indicator_code")["value"].mean())
    rows = []
    for r in sel.itertuples(index=False):
        rows.append({
            "indicator": r.indicator,
            "value": None if pd.isna(r.value) else round(float(r.value), 1),
            "state_avg": None if pd.isna(state_avg.get(r.indicator_code)) else round(float(state_avg.get(r.indicator_code)), 1),
            "caution": "suppressed (*) – not reported" if r.is_suppressed else ("low sample (25-49 cases) – use with caution" if r.is_low_n else ""),
        })
    note = f"NFHS-5 (2019-21) district: {d.iloc[0]['district']}, {d.iloc[0]['state']}."
    if mrow["match_method"] == "fuzzy_name":
        note += f" Matched by approximate district name (similarity {mrow['match_score']:.2f})."
    return {"ok": True, "district": d.iloc[0]["district"], "state": d.iloc[0]["state"], "rows": rows, "note": note}
