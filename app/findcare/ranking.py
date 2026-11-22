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
    leads: list[dict] = field(default_factory=list)
    coverage: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


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
              only_confirmed: bool = False, hide_review: bool = False) -> Result:
    if not loc.ok or loc.lat is None or loc.lon is None:
        raise ValueError(f"Location not resolved: {loc.error}")
    w = dict(DEFAULT_WEIGHTS)
    if weights:
        w.update(weights)
    tot = sum(w.values()) or 1.0
    w = {k: v / tot for k, v in w.items()}
    res = Result(service, loc, radius_km, radius_km, w)
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
        n_ok = (ev.loc[ev.index.intersection(cand["facility_id"]), "evidence_score"] >= min_evidence).sum()
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
    recs = [s for s in scored if s["evidence_adj"] >= min_evidence]
    leads = [s for s in scored if s["evidence_adj"] < min_evidence]
    leads.sort(key=lambda x: x["distance_km"])
    res.recommendations = [explain(s, i + 1, service, loc, half_life) for i, s in enumerate(recs[:top_k])]
    for i in range(len(res.recommendations) - 1):
        res.recommendations[i]["vs_next"] = _compare(recs[i], recs[i + 1])
    res.leads = [explain(s, None, service, loc, half_life) for s in leads[:5]]
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
        res.notes.append(f"No facility within {radius:.0f} km has more than indirect evidence of {service.label.lower()}. "
                         "The leads below mention related terms only – call before travelling, or contact the district hospital for referral.")
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
        "review_reasons": list(s.get("review_reasons") or []),
    }


def evidence_for_facility(rt, facility_id: str, service: Service) -> dict:
    """Convenience for notebooks: full evidence dict for one facility."""
    row = rt.facilities[rt.facilities["facility_id"] == facility_id].iloc[0].to_dict()
    return facility_evidence(row, service)
