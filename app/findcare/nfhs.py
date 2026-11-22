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
    wide_cols = {}
    long_rows = []
    orig = dict(zip(colmap["column"], colmap["original_name"]))
    for c in ind_cols:
        parsed = df[c].map(parse_value)
        vals = parsed.map(lambda t: t[0])
        numeric_share = vals.notna().mean()
        if numeric_share < 0.2 and not parsed.map(lambda t: t[1]).any():
            continue  # non-indicator text column
        wide_cols[c] = vals.astype(float)
        long_rows.append(pd.DataFrame({
            "state": base["state"], "district": base["district"],
            "state_norm": base["state_norm"], "district_norm": base["district_norm"],
            "indicator_code": c, "indicator": orig.get(c, c),
            "value": vals.astype(float),
            "is_suppressed": parsed.map(lambda t: t[1]).astype(bool),
            "is_low_n": parsed.map(lambda t: t[2]).astype(bool),
            "raw_value": df[c].astype(str),
        }))
    wide = pd.concat([base, pd.DataFrame(wide_cols, index=base.index)], axis=1)
    long = pd.concat(long_rows, ignore_index=True) if long_rows else pd.DataFrame()
    return wide, long, colmap


# Renamed districts: (state, PIN-directory name) -> NFHS-5 name. Districts *created after* NFHS-5
# (e.g. Andhra Pradesh's 2022 districts) are deliberately left unmatched: no data exists for them.
DISTRICT_ALIASES = {
    ("assam", "kamrup metro"): "kamrup metropolitan",
    ("chhattisgarh", "kanker"): "uttar bastar kanker",
    ("gujarat", "dang"): "the dangs",
    ("haryana", "nuh"): "mewat",
    ("jammu and kashmir", "poonch"): "punch",
    ("jharkhand", "east singhbum"): "purbi singhbhum",
    ("jharkhand", "west singhbhum"): "pashchimi singhbhum",
    ("karnataka", "ballari"): "bellary",
    ("karnataka", "vijayapura"): "bijapur",
    ("madhya pradesh", "east nimar"): "khandwa east nimar",
    ("madhya pradesh", "khargone"): "khargone west nimar",
    ("maharashtra", "beed"): "bid",
    ("maharashtra", "raigad"): "raigarh",
    ("odisha", "sonepur"): "subarnapur",
    ("punjab", "s a s nagar"): "sahibzada ajit singh nagar",
    ("punjab", "sri muktsar sahib"): "muktsar",
    ("uttar pradesh", "amroha"): "jyotiba phule nagar",
    ("uttar pradesh", "hathras"): "mahamaya nagar",
    ("uttar pradesh", "kasganj"): "kanshiram nagar",
    ("uttar pradesh", "bhadohi"): "sant ravidas nagar bhadohi",
    ("uttarakhand", "pauri garhwal"): "garhwal",
    ("andhra pradesh", "spsr nellore"): "sri potti sriramulu nello",
    ("west bengal", "24 paraganas north"): "north twenty four pargana",
    ("west bengal", "24 paraganas south"): "south twenty four pargana",
    ("west bengal", "dinajpur dakshin"): "dakshin dinajpur",
    ("west bengal", "dinajpur uttar"): "uttar dinajpur",
    ("west bengal", "hooghly"): "hugli",
    ("west bengal", "howrah"): "haora",
    ("west bengal", "coochbehar"): "koch bihar",
    ("west bengal", "medinipur east"): "purba medinipur",
    ("west bengal", "medinipur west"): "paschim medinipur",
}


def match_districts(nfhs_wide: pd.DataFrame, district_lookup: pd.DataFrame, cutoff: float = 0.80) -> pd.DataFrame:
    """Map each PIN-directory district to an NFHS district (state-constrained):
    exact name -> known rename (DISTRICT_ALIASES) -> fuzzy name (ratio >= cutoff) -> unmatched."""
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
        if not r.district_norm:
            continue
        if r.district_norm in names:
            rows.append((r.state_norm, r.district_norm, st, r.district_norm, "exact", 1.0))
            continue
        alias = DISTRICT_ALIASES.get((st, r.district_norm))
        if alias in names:
            rows.append((r.state_norm, r.district_norm, st, alias, "known_rename", 1.0))
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

# The Marketplace NFHS-5 table ships abbreviated snake_case codes, so the indicators shown for each
# service are curated here: code prefix -> plain-English label.
INDICATOR_LABELS = {
    "hh_member_covered_health_insurance": "Households with a member covered by health insurance (%)",
    "w15_plus_with_high_or_very_high_gt_140_mg_dl_blood_sugar": "Women 15+ with high blood sugar (>140 mg/dl) or on medication (%)",
    "m15_plus_with_high_or_very_high_gt_140_mg_dl_blood_sugar": "Men 15+ with high blood sugar (>140 mg/dl) or on medication (%)",
    "w15_plus_with_high_bp_sys_gte_140": "Women 15+ with high blood pressure or on medication (%)",
    "m15_plus_with_high_bp_sys_gte_140": "Men 15+ with high blood pressure or on medication (%)",
    "births_delivered_by_csection_5y": "Births delivered by C-section (%)",
    "births_in_a_public_fac_that_were_delivered_by_csection": "C-section rate in public facilities (%)",
    "births_in_a_private_fac_that_were_delivered_by_csection": "C-section rate in private facilities (%)",
    "institutional_birth_5y": "Births in a health facility (%)",
    "institutional_birth_in_public_facility_5y": "Births in a public facility (%)",
    "mothers_who_had_at_least_4_anc_visits": "Mothers with 4+ antenatal care visits (%)",
    "mothers_who_had_an_anc_visit_in_the_first_trimester": "Mothers with antenatal visit in first trimester (%)",
    "average_out_of_pocket_expenditure_per_delivery_in_a_public_fac": "Average out-of-pocket cost per delivery in a public facility (Rs.)",
    "pregnant_w15_49_who_are_anaemic": "Pregnant women who are anaemic (%)",
    "all_w15_49_who_are_anaemic": "Women 15-49 who are anaemic (%)",
    "child_6_59m_who_are_anaemic": "Children 6-59 months who are anaemic (%)",
    "home_birth_that_were_conducted_by_skilled_hp": "Home births attended by a skilled health worker (%)",
    "children_who_received_pnc_from_a_doctor": "Newborns who got a postnatal check within 2 days (%)",
    "children_under_age_3_years_breastfed_within_one_hour": "Newborns breastfed within 1 hour of birth (%)",
    "w15_plus_who_use_any_kind_of_tobacco": "Women 15+ who use tobacco (%)",
    "m15_plus_who_use_any_kind_of_tobacco": "Men 15+ who use tobacco (%)",
    "w15_plus_who_consume_alcohol": "Women 15+ who drink alcohol (%)",
    "m15_plus_who_consume_alcohol": "Men 15+ who drink alcohol (%)",
    "women_age_30_49_years_ever_undergone_a_cervical_screen": "Women 30-49 ever screened for cervical cancer (%)",
    "women_age_30_49_years_ever_undergone_a_breast_exam": "Women 30-49 ever had a breast cancer exam (%)",
    "women_age_30_49_years_ever_undergone_an_oral_cancer_exam": "Women 30-49 ever had an oral cancer exam (%)",
    "households_using_clean_fuel_for_cooking": "Households cooking with clean fuel (%)",
    "hh_electricity": "Households with electricity (%)",
}
_BP_SUGAR = ["w15_plus_with_high_or_very_high_gt_140_mg_dl_blood_sugar", "m15_plus_with_high_or_very_high_gt_140_mg_dl_blood_sugar",
             "w15_plus_with_high_bp_sys_gte_140", "m15_plus_with_high_bp_sys_gte_140"]
_BIRTH = ["institutional_birth_5y", "births_delivered_by_csection_5y", "births_in_a_public_fac_that_were_delivered_by_csection",
          "births_in_a_private_fac_that_were_delivered_by_csection", "mothers_who_had_at_least_4_anc_visits", "pregnant_w15_49_who_are_anaemic"]
_CANCER = ["women_age_30_49_years_ever_undergone_a_cervical_screen", "women_age_30_49_years_ever_undergone_a_breast_exam",
           "women_age_30_49_years_ever_undergone_an_oral_cancer_exam", "w15_plus_who_use_any_kind_of_tobacco", "m15_plus_who_use_any_kind_of_tobacco"]
SERVICE_INDICATORS = {
    "dialysis": _BP_SUGAR,
    "emergency_csection": _BIRTH,
    "maternity": ["institutional_birth_5y", "institutional_birth_in_public_facility_5y", "mothers_who_had_an_anc_visit_in_the_first_trimester",
                  "mothers_who_had_at_least_4_anc_visits", "average_out_of_pocket_expenditure_per_delivery_in_a_public_fac", "births_delivered_by_csection_5y"],
    "nicu": ["institutional_birth_5y", "home_birth_that_were_conducted_by_skilled_hp", "children_who_received_pnc_from_a_doctor",
             "children_under_age_3_years_breastfed_within_one_hour"],
    "cardiac_surgery": _BP_SUGAR[2:] + ["w15_plus_who_use_any_kind_of_tobacco", "m15_plus_who_use_any_kind_of_tobacco"],
    "cath_lab": _BP_SUGAR[2:] + ["w15_plus_who_use_any_kind_of_tobacco", "m15_plus_who_use_any_kind_of_tobacco"],
    "oncology": _CANCER, "radiotherapy": _CANCER,
    "burn_care": ["households_using_clean_fuel_for_cooking", "hh_electricity"],
    "blood_bank": ["all_w15_49_who_are_anaemic", "pregnant_w15_49_who_are_anaemic", "child_6_59m_who_are_anaemic"],
    "psychiatry": ["m15_plus_who_consume_alcohol", "w15_plus_who_consume_alcohol", "m15_plus_who_use_any_kind_of_tobacco"],
}


def district_context(nfhs_long: pd.DataFrame, district_map: pd.DataFrame, state_norm: str | None,
                     district_norm: str | None, keywords: tuple[str, ...], max_rows: int = 8,
                     service_id: str | None = None) -> dict:
    """Indicators for one district relevant to a service, with the state average for comparison."""
    if nfhs_long is None or nfhs_long.empty or not district_norm:
        return {"ok": False, "note": "No district identified for this location."}
    m = district_map[(district_map["state_norm"] == state_norm) & (district_map["district_norm"] == district_norm)]
    if m.empty or pd.isna(m.iloc[0]["nfhs_district_norm"]):
        return {"ok": False, "note": f"No NFHS-5 data for district '{(district_norm or '').title()}'. It may have been created after "
                                     "the 2019-21 survey, or its name differs between datasets; we don't guess."}
    mrow = m.iloc[0]
    d = nfhs_long[(nfhs_long["state_norm"] == mrow["nfhs_state_norm"]) & (nfhs_long["district_norm"] == mrow["nfhs_district_norm"])]
    if d.empty:
        return {"ok": False, "note": "No NFHS-5 indicators for this district."}
    codes = SERVICE_INDICATORS.get(service_id, []) + ["hh_member_covered_health_insurance"]
    order = {c: i for i, c in enumerate(codes)}
    pick = d["indicator_code"].map(lambda c: next((order[p] for p in codes if c.startswith(p)), None))
    if pick.notna().any():  # curated codes (Marketplace table)
        sel = d.assign(_o=pick).dropna(subset=["_o"]).sort_values("_o").drop_duplicates("_o").head(max_rows)
    else:  # human-readable column names (original data.gov.in CSV)
        kw = tuple(k.lower() for k in keywords) + GENERAL_KEYWORDS
        sel = d[d["indicator"].str.lower().apply(lambda s: any(k in s for k in kw))].head(max_rows)
    state_avg = (nfhs_long[nfhs_long["state_norm"] == mrow["nfhs_state_norm"]]
                 .groupby("indicator_code")["value"].mean())
    rows = []
    for r in sel.itertuples(index=False):
        label = next((v for k, v in INDICATOR_LABELS.items() if r.indicator_code.startswith(k)), r.indicator)
        rows.append({
            "indicator": label,
            "value": None if pd.isna(r.value) else round(float(r.value), 1),
            "state_avg": None if pd.isna(state_avg.get(r.indicator_code)) else round(float(state_avg.get(r.indicator_code)), 1),
            "caution": "suppressed (*) – not reported" if r.is_suppressed else ("low sample (25-49 cases) – use with caution" if r.is_low_n else ""),
        })
    note = f"NFHS-5 (2019-21) district: {d.iloc[0]['district']}, {d.iloc[0]['state']}."
    if mrow["match_method"] == "fuzzy_name":
        note += f" Matched by approximate district name (similarity {mrow['match_score']:.2f})."
    return {"ok": True, "district": d.iloc[0]["district"], "state": d.iloc[0]["state"], "rows": rows, "note": note}
