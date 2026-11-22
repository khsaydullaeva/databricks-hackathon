"""Turn (service, location) into ranked, explained recommendations.

    final = w_evidence x (evidence x plausibility)
          + w_proximity x 0.5 ** (distance_km / half_life[urgency])
          + w_trust x trust
          + w_scale x scale                       (+0.03 if 24x7 signal for emergency services)

Only facilities whose (plausibility-adjusted) evidence reaches `min_evidence` are recommended.
Everything else nearby with *some* mention is returned as "unverified leads", clearly separated.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .evidence import FIELD_LABEL, facility_evidence, level_for, plausibility
from .geo import Location, haversine_km
from .services import HALF_LIFE_KM, Service

DEFAULT_WEIGHTS = {"evidence": 0.45, "proximity": 0.30, "trust": 0.15, "scale": 0.10}
RADIUS_STEPS = (1, 2, 4)
MAX_RADIUS_KM = 600


@dataclass
class Result:
    service: Service
    location: Location
    radius_km: float
    searched_radius_km: float
    weights: dict
    recommendations: list[dict] = field(default_factory=list)
    held: list[dict] = field(default_factory=list)   # passed the evidence floor but failed a safety check
    leads: list[dict] = field(default_factory=list)  # indirect / weak mentions only
    coverage: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    guarded: bool = True
    abstained: bool = False
    abstain_reason: str | None = None
    funnel: list[tuple[str, int, str]] = field(default_factory=list)
    sensitivity: dict = field(default_factory=dict)


# Safety mode (from CareProof): rank only records that pass every check; otherwise abstain and explain.
GUARD_LABEL = {
    "evidence_below_moderate": "the service is only mentioned once, in a weak part of the record (below Moderate)",
    "claim_describes_other_facility": "the quote naming the service seems to describe another facility or city",
    "analyst_hold": "an analyst reviewed this record and put it on hold",
    "record_contradictions": "the record contradicts itself (location, state, or mixed sites)",
}
SAFE_MIN_EVIDENCE = 0.55


def guard_issues(s: dict, e) -> list[str]:
    issues = []
    if s["evidence_adj"] < SAFE_MIN_EVIDENCE:
        issues.append("evidence_below_moderate")
    if _get(e, "attribution_conflict"):
        issues.append("claim_describes_other_facility")
    if isinstance(_get(e, "analyst_hold"), dict):
        issues.append("analyst_hold")
    if s.get("needs_review"):
        issues.append("record_contradictions")
    return issues


def _get(e, k):
    try:
        v = e.get(k) if hasattr(e, "get") else None
    except Exception:
        return None
    return None if (isinstance(v, float) and np.isnan(v)) else v


def scale_score(capacity, doctors) -> tuple[float, str]:
    parts = []
    vals = []
    if capacity is not None and not pd.isna(capacity):
        vals.append(min(1.0, np.log10(1 + capacity) / np.log10(501)))
        parts.append(f"{int(capacity)} beds")
    if doctors is not None and not pd.isna(doctors):
        vals.append(min(1.0, np.log10(1 + doctors) / np.log10(101)))
        parts.append(f"{int(doctors)} doctors")
    if not vals:
        return 0.3, "size unknown (neutral 0.30 used)"
    return float(np.mean(vals)), ", ".join(parts)


def recommend(rt, service: Service, loc: Location, radius_km: float = 50, top_k: int = 5,
              weights: dict | None = None, min_evidence: float = 0.31, evidence_cache: dict | None = None,
              only_confirmed: bool = False, hide_review: bool = False, guarded: bool = True) -> Result:
    if not loc.ok or loc.lat is None or loc.lon is None:
        raise ValueError(f"Location not resolved: {loc.error}")
    w = dict(DEFAULT_WEIGHTS)
    if weights:
        w.update(weights)
    tot = sum(w.values()) or 1.0
    w = {k: v / tot for k, v in w.items()}
    res = Result(service, loc, radius_km, radius_km, w, guarded=guarded)
    fac = rt.facilities
    ok = fac["eff_lat"].notna()
    dist = np.full(len(fac), np.nan)
    dist[ok.to_numpy()] = haversine_km(loc.lat, loc.lon, fac.loc[ok, "eff_lat"].to_numpy(), fac.loc[ok, "eff_lon"].to_numpy())

    ev = _evidence_table(rt, service, evidence_cache)
    half_life = HALF_LIFE_KM[service.urgency]

    keep = ev["evidence_score"].reindex(fac["facility_id"]).fillna(0).to_numpy() > 0
    if only_confirmed and "web_status" in ev:
        keep &= ev["web_status"].reindex(fac["facility_id"]).isin(["confirmed_own_site"]).to_numpy()
    if hide_review and "needs_review" in fac:
        keep &= ~fac["needs_review"].fillna(False).astype(bool).to_numpy()

    def candidates(r):
        m = (dist <= r) & keep
        return fac[m].assign(distance_km=dist[m])

    radius = radius_km
    for mult in RADIUS_STEPS:
        radius = min(radius_km * mult, MAX_RADIUS_KM)
        cand = candidates(radius)
        e_in = ev.loc[ev.index.intersection(cand["facility_id"])]
        floor = max(min_evidence, SAFE_MIN_EVIDENCE) if guarded else min_evidence
        good = e_in["evidence_score"] >= floor
        if guarded:
            if "attribution_conflict" in e_in:
                good &= ~e_in["attribution_conflict"].fillna(False).astype(bool)
            if "needs_review" in cand:
                nr = cand.set_index("facility_id")["needs_review"].reindex(e_in.index).fillna(False).astype(bool)
                good &= ~nr
        n_ok = int(good.sum())
        if n_ok >= min(3, top_k) or radius >= MAX_RADIUS_KM:
            break
    res.searched_radius_km = radius
    if radius > radius_km:
        res.notes.append(f"Fewer than {min(3, top_k)} well-evidenced options within {radius_km:.0f} km, so the search was widened to {radius:.0f} km.")

    scored = []
    for r in cand.to_dict("records"):
        e = ev.loc[r["facility_id"]]
        pf, pnotes = plausibility(r, service)
        adj = e["evidence_score"] * pf
        prox = 0.5 ** (r["distance_km"] / half_life)
        sc, sc_text = scale_score(r.get("capacity"), r.get("num_doctors"))
        bonus = 0.03 if (service.urgency == "emergency" and r.get("has_24x7")) else 0.0
        comps = {
            "evidence": w["evidence"] * adj,
            "proximity": w["proximity"] * prox,
            "trust": w["trust"] * r["trust_score"],
            "scale": w["scale"] * sc,
        }
        total = sum(comps.values()) + bonus
        scored.append({**r, "ev": e, "evidence_adj": adj, "plaus_factor": pf, "plaus_notes": pnotes,
                       "proximity": prox, "scale": sc, "scale_text": sc_text, "bonus_24x7": bonus,
                       "components": comps, "score": total})

    scored.sort(key=lambda x: -x["score"])
    scored = _collapse_duplicates(scored)
    for x in scored:
        x["guard_issues"] = guard_issues(x, x["ev"])
    above = [x for x in scored if x["evidence_adj"] >= min_evidence]
    if guarded:
        recs = [x for x in above if not x["guard_issues"]]
        held = [x for x in above if x["guard_issues"]]
    else:
        recs, held = above, []
    leads = [x for x in scored if x["evidence_adj"] < min_evidence]
    leads.sort(key=lambda x: x["distance_km"])
    res.recommendations = [explain(x, i + 1, service, loc, half_life) for i, x in enumerate(recs[:top_k])]
    for i in range(len(res.recommendations) - 1):
        res.recommendations[i]["vs_next"] = _compare(recs[i], recs[i + 1])
    res.held = [explain(x, None, service, loc, half_life) for x in held[:8]]
    res.leads = [explain(x, None, service, loc, half_life) for x in leads[:5]]
    _attach_ai_reader(rt, service, res.recommendations + res.held + res.leads)

    # decision funnel (CareProof): how many facilities survive each step, and why the rest dropped
    in_r = int(np.nansum(dist <= radius))
    att_ok = [x for x in scored if "claim_describes_other_facility" not in x["guard_issues"]]
    ev_ok = [x for x in att_ok if x["evidence_adj"] >= (SAFE_MIN_EVIDENCE if guarded else min_evidence)]
    cons_ok = [x for x in ev_ok if "record_contradictions" not in x["guard_issues"]]
    hold_ok = [x for x in cons_ok if "analyst_hold" not in x["guard_issues"]]
    res.funnel = [
        ("Facilities with a usable location in range", in_r, f"within {radius:.0f} km"),
        (f"…whose record (or own website) mentions {service.label.lower()}", len(scored), "keyword + negation rules"),
        ("…where the quote describes this facility", len(att_ok), "attribution guard"),
        ("…with at least Moderate evidence" if guarded else "…above the evidence floor", len(ev_ok), "evidence level after plausibility"),
        ("…whose record is internally consistent", len(cons_ok), "location / state / mixed-site checks"),
        ("…not on analyst hold", len(hold_ok), "CareProof review holds"),
        ("Shown as recommendations", len(res.recommendations), f"top {top_k} by score"),
    ]
    # equal-weight sensitivity (CareProof): would the order change if every factor counted the same?
    if recs:
        eq = sorted(recs, key=lambda x: -(0.25 * (x["evidence_adj"] + x["proximity"] + x["trust_score"] + x["scale"]) + x["bonus_24x7"]))
        top_ids = [x["facility_id"] for x in recs[:top_k]]
        eq_ids = [x["facility_id"] for x in eq[:top_k]]
        res.sensitivity = {"top1_same": eq_ids[0] == top_ids[0], "alt_top1": eq[0]["name"],
                           "same_set": set(top_ids) == set(eq_ids), "n_compared": len(top_ids)}
    levels = pd.Series([level_for(s["evidence_adj"])[0] for s in scored])
    res.coverage = {
        "radius_km": radius,
        "n_with_any_mention": len(scored),
        "n_strong": int((levels == "Strong").sum()),
        "n_moderate": int((levels == "Moderate").sum()),
        "n_limited": int((levels == "Limited").sum()),
        "n_indirect": int((levels == "Indirect").sum()),
        "n_facilities_in_radius": int(np.nansum(dist <= radius)),
    }
    if not res.recommendations:
        res.abstained = True
        if held:
            res.abstain_reason = (f"We won't rank anything here: {len(held)} facilit{'y' if len(held) == 1 else 'ies'} within {radius:.0f} km "
                                  f"mention {service.label.lower()}, but each failed a safety check (see 'Held for review'). "
                                  "Call to confirm, or switch off safety mode to see them ranked.")
        else:
            res.abstain_reason = (f"No facility within {radius:.0f} km has more than indirect evidence of {service.label.lower()}. "
                                  "The leads mention related terms only – call before travelling, or ask the district hospital for a referral.")
        res.notes.append(res.abstain_reason)
    if loc.precision_km and loc.precision_km > 15:
        res.notes.append(f"Your location is approximate (±{loc.precision_km:.0f} km), so distances are approximate too.")
    res.notes.append("Distances are straight-line; road distance is typically 1.2–1.5× longer.")
    return res


def _evidence_table(rt, service: Service, cache: dict | None) -> pd.DataFrame:
    if cache is not None and service.id in cache:
        return cache[service.id]
    if getattr(rt, "evidence", None) is not None:
        e = rt.evidence.get(service.id)
        if e is None:  # no facility mentions this service at all
            e = pd.DataFrame(columns=["evidence_score", "level", "has_strong", "fields", "items", "negated"])
        return e
    from .evidence import compute_evidence

    e = compute_evidence(rt.facilities, service).set_index("facility_id")
    if cache is not None:
        cache[service.id] = e
    return e


def _collapse_duplicates(scored: list[dict]) -> list[dict]:
    seen, out = {}, []
    for s in scored:
        g = s.get("dup_group") or s["facility_id"]
        if g in seen:
            seen[g].setdefault("duplicates", []).append(s["facility_id"])
            continue
        seen[g] = s
        out.append(s)
    return out


def _compare(a: dict, b: dict) -> str:
    diffs = {k: a["components"][k] - b["components"][k] for k in a["components"]}
    k = max(diffs, key=diffs.get)
    reason = {
        "evidence": "stronger evidence that it offers this service",
        "proximity": f"it is closer ({a['distance_km']:.0f} km vs {b['distance_km']:.0f} km)",
        "trust": "its record is more consistent and better sourced",
        "scale": "it is larger (more beds / doctors recorded)",
    }[k]
    tradeoff = ""
    losers = [x for x, v in diffs.items() if v < -0.01]
    if losers:
        lname = {"evidence": "evidence", "proximity": "distance", "trust": "record quality", "scale": "size"}
        tradeoff = f", even though {b['name']} does better on {' and '.join(lname[x] for x in losers)}"
    return f"Ranked above {b['name']} mainly because {reason}{tradeoff}."


def explain(s: dict, rank: int | None, service: Service, loc: Location, half_life: float) -> dict:
    e = s["ev"]
    level, meaning = level_for(s["evidence_adj"])
    citations = []
    for it in e["items"][:6]:
        citations.append({
            "field": FIELD_LABEL[it["field"]],
            "kind": "names the service" if it["kind"] == "strong" else "related term",
            "term": it["term"],
            "text": it["text"],
            "url": it.get("url"),
        })
    ignored = [{"field": FIELD_LABEL[it["field"]], "text": it["text"]} for it in e["negated"][:3]]
    cautions = [fl["message"] for fl in s["trust_flags"]] + s["plaus_notes"]
    if not e["has_strong"]:
        cautions.append(f"The record never names {service.label.lower()} directly – only related terms.")
    if s.get("guard_issues"):
        cautions = [f"Held back because {GUARD_LABEL[g]}." for g in s["guard_issues"]] + cautions
    if _get(e, "attribution_note"):
        cautions.append(_get(e, "attribution_note"))
    hold = _get(e, "analyst_hold")
    if isinstance(hold, dict):
        cautions.append(f"Analyst review ({str(hold.get('review_status', '')).replace('_', ' ')}): {hold.get('reason')}")
    web_status = e.get("web_status") if hasattr(e, "get") else None
    if web_status == "not_found_on_site":
        cautions.append("Its own website was checked and does not mention this service (websites are often incomplete – call to confirm).")
    if s.get("duplicates"):
        cautions.append(f"{len(s['duplicates'])} near-identical record(s) for this facility were merged into this result.")
    where = s["location_basis"]
    dist_txt = f"{s['distance_km']:.1f} km" + (" (approx.)" if where != "facility coordinates" else "")
    fields_txt = ", ".join(FIELD_LABEL[f].lower() for f in e["fields"][:3])
    best_quote = citations[0]["text"] if citations else ""
    if rank:
        why = (f"{s['name']} is {dist_txt} away. {level} evidence: {service.label.lower()} is {meaning} "
               f"({fields_txt}). Key quote: “{best_quote}”")
    else:
        why = f"{s['name']} ({dist_txt}) – {level.lower()} evidence only: {meaning}."
    c = s["components"]
    return {
        "rank": rank,
        "facility_id": s["facility_id"],
        "name": s["name"],
        "address": ", ".join(str(x) for x in (s.get("address_text"), s.get("city"), s.get("state"), s.get("postcode")) if x and str(x) != "nan"),
        "phone": s.get("phone"),
        "website": s.get("website"),
        "facility_type": s.get("facility_type"),
        "operator_type": s.get("operator_type"),
        "lat": s["eff_lat"], "lon": s["eff_lon"],
        "distance_km": round(float(s["distance_km"]), 1),
        "location_basis": where,
        "evidence_level": level,
        "evidence_meaning": meaning,
        "evidence_score": round(float(e["evidence_score"]), 3),
        "evidence_adj": round(float(s["evidence_adj"]), 3),
        "plausibility_factor": s["plaus_factor"],
        "trust_score": round(float(s["trust_score"]), 3),
        "proximity": round(float(s["proximity"]), 3),
        "scale": round(float(s["scale"]), 3),
        "scale_text": s["scale_text"],
        "has_24x7": bool(s.get("has_24x7")),
        "score": round(float(s["score"]), 3),
        "components": {k: round(float(v), 3) for k, v in c.items()},
        "bonus_24x7": s["bonus_24x7"],
        "why": why,
        "citations": citations,
        "ignored_mentions": ignored,
        "cautions": cautions,
        "sources": list(s.get("source_urls_list") or [])[:5],
        "half_life_km": half_life,
        "vs_next": None,
        "web_status": web_status if isinstance(web_status, str) else "not_checked",
        "web_url": e.get("web_url") if hasattr(e, "get") and isinstance(e.get("web_url"), str) else None,
        "web_checked_at": s.get("web_checked_at") if isinstance(s.get("web_checked_at"), str) else None,
        "needs_review": bool(s.get("needs_review")),
        "guard_issues": list(s.get("guard_issues") or []),
        "analyst_hold": hold if isinstance(hold, dict) else None,
        "review_reasons": list(s.get("review_reasons") or []),
    }


def evidence_for_facility(rt, facility_id: str, service: Service) -> dict:
    """Convenience for notebooks: full evidence dict for one facility."""
    row = rt.facilities[rt.facilities["facility_id"] == facility_id].iloc[0].to_dict()
    return facility_evidence(row, service)


AI_DOUBT = {"other_facility": "seems to describe a different facility or branch", "not_offered": "says it is not offered",
            "planned": "describes it as planned / not yet available"}


def _attach_ai_reader(rt, service: Service, cards: list[dict]) -> None:
    """Add the LLM second-reader verdict (notebook 03b). Shown as a badge and, when it doubts the claim, a caution.
    Not a hard filter: on real data its verdict adds only a modest signal over the rules (see METHODOLOGY)."""
    checks = getattr(rt, "ai_checks", None) or {}
    for c in cards:
        a = checks.get((c["facility_id"], service.id))
        c["ai_reader"] = a
        if a and a.get("verdict") in AI_DOUBT:
            c["cautions"].append(f"AI reader: the quote {AI_DOUBT[a['verdict']]}" + (f" – {a['reason']}" if a.get("reason") else "") + ".")
