"""Per-facility evidence that a facility offers a service, with citable text.

Scoring (deliberately simple so it can be explained to a non-technical user):

  contribution(field) = 0.75 x FIELD_WEIGHT[field] x KIND_WEIGHT[strong|support]
                        (best match within that field)
  evidence            = 1 - PRODUCT(1 - contribution(field))      ("noisy-OR")

* A single field can reach at most 0.75, so "Strong" (>= 0.80) needs the service to be
  named in at least two different parts of the record (e.g. procedures AND equipment).
* If only related terms (support) match, evidence is capped at 0.30 ("Indirect").
* Mentions preceded/followed by negation or "planned / referred to / coming soon" are
  excluded but reported, so a reviewer can see what was ignored and why.
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

from .services import NEGATION_AFTER_RE, NEGATION_BEFORE_RE, Service
from .text import norm

FIELD_WEIGHT = {
    "procedure": 1.00,
    "equipment": 0.95,
    "capability": 0.90,
    "name": 0.80,
    "description": 0.60,
    "specialties": 0.55,
}
FIELD_LABEL = {
    "procedure": "Procedures list",
    "equipment": "Equipment list",
    "capability": "Capabilities list",
    "name": "Facility name",
    "description": "Description text",
    "specialties": "Specialties list",
}
KIND_WEIGHT = {"strong": 1.0, "support": 0.35}
PER_FIELD_CAP = 0.75
SUPPORT_ONLY_CAP = 0.30

LEVELS = [  # (min score, label, plain-language meaning)
    (0.80, "Strong", "named in two or more separate parts of the record"),
    (0.55, "Moderate", "clearly named, but in only one part of the record"),
    (0.31, "Limited", "mentioned once, in a less reliable part of the record"),
    (0.0001, "Indirect", "only related terms (e.g. a department) – the service itself is not named"),
]

LIST_FIELDS = ("procedure", "equipment", "capability", "specialties")
_SENT_SPLIT = re.compile(r"(?<=[.!?;])\s+|\n+")


def level_for(score: float) -> tuple[str, str]:
    for threshold, label, meaning in LEVELS:
        if score >= threshold:
            return label, meaning
    return "None", "no mention of this service"


def _is_negated(n: str, m: re.Match) -> bool:
    before = n[max(0, m.start() - 45):m.start()]
    after = n[m.end():m.end() + 40]
    return bool(NEGATION_BEFORE_RE.search(before) or NEGATION_AFTER_RE.search(after))


def _scan(text_items: list[str], field: str, service: Service) -> tuple[list[dict], list[dict]]:
    """Return (hits, negated) for one field."""
    hits, negated = [], []
    s_re, p_re = service.strong_re(), service.support_re()
    for raw in text_items:
        n = norm(raw)
        if not n:
            continue
        for kind, rx in (("strong", s_re), ("support", p_re)):
            if rx is None:
                continue
            m = rx.search(n)
            if not m:
                continue
            item = {"field": field, "kind": kind, "term": m.group(0).strip(), "text": _clip(raw)}
            if _is_negated(n, m):
                negated.append(item)
            else:
                hits.append(item)
            break  # strongest kind only per item
    return hits, negated


def _clip(s: str, width: int = 240) -> str:
    s = re.sub(r"\s+", " ", str(s)).strip()
    return s if len(s) <= width else s[: width - 1].rsplit(" ", 1)[0] + "…"


def facility_evidence(fac: dict[str, Any], service: Service) -> dict:
    """Evidence for one facility (dict with name, description and *_list fields)."""
    all_hits: list[dict] = []
    all_neg: list[dict] = []
    for f in LIST_FIELDS:
        h, ng = _scan(fac.get(f"{f}_list") or [], f, service)
        all_hits += h
        all_neg += ng
    h, ng = _scan([fac.get("name") or ""], "name", service)
    all_hits += h
    all_neg += ng
    desc = fac.get("description") or ""
    if desc:
        sentences = [s for s in _SENT_SPLIT.split(str(desc)) if s.strip()]
        h, ng = _scan(sentences, "description", service)
        all_hits += h
        all_neg += ng
    return score_hits(all_hits, all_neg)


def score_hits(hits: list[dict], negated: list[dict]) -> dict:
    best: dict[str, float] = {}
    for h in hits:
        c = PER_FIELD_CAP * FIELD_WEIGHT[h["field"]] * KIND_WEIGHT[h["kind"]]
        h["weight"] = round(c, 3)
        best[h["field"]] = max(best.get(h["field"], 0.0), c)
    prod = 1.0
    for c in best.values():
        prod *= 1.0 - c
    score = 1.0 - prod if best else 0.0
    has_strong = any(h["kind"] == "strong" for h in hits)
    if hits and not has_strong:
        score = min(score, SUPPORT_ONLY_CAP)
    level, meaning = level_for(score)
    strong_fields = sorted({h["field"] for h in hits if h["kind"] == "strong"}, key=lambda f: -FIELD_WEIGHT[f])
    # order citations: strong first, then by field weight
    hits_sorted = sorted(hits, key=lambda h: (h["kind"] != "strong", -FIELD_WEIGHT[h["field"]]))
    return {
        "evidence_score": round(score, 4),
        "level": level,
        "level_meaning": meaning,
        "has_strong": has_strong,
        "n_fields": len(best),
        "strong_fields": strong_fields,
        "fields": sorted(best, key=lambda f: -best[f]),
        "items": hits_sorted[:12],
        "negated": negated[:6],
    }


def compute_evidence(facilities: pd.DataFrame, service: Service) -> pd.DataFrame:
    """Evidence for every facility; returns rows only where something matched (or was negated)."""
    cols = ["facility_id", "name", "description"] + [f"{f}_list" for f in LIST_FIELDS]
    rows = []
    for rec in facilities[cols].to_dict("records"):
        ev = facility_evidence(rec, service)
        if ev["items"] or ev["negated"]:
            ev["facility_id"] = rec["facility_id"]
            ev["service_id"] = service.id
            rows.append(ev)
    return pd.DataFrame(rows, columns=["facility_id", "service_id", "evidence_score", "level", "level_meaning",
                                       "has_strong", "n_fields", "strong_fields", "fields", "items", "negated"])


SMALL_TYPES_RE = re.compile(r"\b(?:clinic|dentist|dental|pharmacy|farmacy|chemist|doctor|diagnostic|lab|laboratory|physiotherapy)\b")


def plausibility(fac: dict[str, Any], service: Service) -> tuple[float, list[str]]:
    """Down-weight claims that are implausible for the facility's size/type.

    Returns (multiplier applied to evidence, human-readable notes).
    """
    notes, factor = [], 1.0
    if service.tier != "tertiary":
        return factor, notes
    ftype = norm(fac.get("facility_type") or "")
    cap = fac.get("capacity")
    if ftype and SMALL_TYPES_RE.search(ftype) and "hospital" not in ftype:
        # Diagnostic centres legitimately offer MRI/CT
        if not (service.id in {"mri", "ct_scan"} and "diagnostic" in ftype):
            factor *= 0.75
            notes.append(f"Listed as a '{fac.get('facility_type')}', which rarely provides {service.label.lower()} itself – it may refer patients elsewhere.")
    if cap is not None and not pd.isna(cap) and cap < 20:
        factor *= 0.85
        notes.append(f"Only {int(cap)} beds recorded – small for {service.label.lower()}.")
    return factor, notes
