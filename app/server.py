"""Find the Right Care – HTML/3D front-end server (FastAPI).

Serves static/index.html and a small JSON API over the same tested findcare package used by the
pipeline, the notebooks and the agent. The assistant's live steps are streamed with Server-Sent Events.

Run locally:  FINDCARE_LOCAL_DIR=../local_data uvicorn server:app --port 8000
On Databricks Apps: see app.yaml (resources: sql-warehouse, llm, experiment).
"""

from __future__ import annotations

import json
import math
import os
import queue
import threading
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from data import load_tables
from findcare.geo import (city_options, location_from_city, location_from_district, location_from_point,
                          resolve_location)
from findcare.nfhs import district_context
from findcare.pipeline import Runtime
from findcare.ranking import DEFAULT_WEIGHTS, recommend
from findcare.services import HALF_LIFE_KM, SERVICES, SERVICES_BY_ID

HERE = Path(__file__).parent
app = FastAPI(title="Find the Right Care")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

_RT: Runtime | None = None
_PICKERS: dict | None = None
_LOCK = threading.Lock()


def rt() -> Runtime:
    global _RT, _PICKERS
    with _LOCK:
        if _RT is None:
            _RT = Runtime.from_tables(load_tables())
            dl = _RT.district_lookup.dropna(subset=["lat", "lon"]).sort_values(["state_norm", "district_norm"])
            states = {}
            for r in dl.itertuples():
                st = states.setdefault(r.state_norm, {"title": str(r.state).title(), "districts": []})
                st["districts"].append({"id": r.district_norm, "title": str(r.district).title()})
            _PICKERS = {"cities": city_options(_RT.place_index), "states": states}
            try:
                from findcare.agent import setup_tracing

                setup_tracing()
            except Exception:
                pass
        return _RT


def _clean(o):
    """numpy / NaN -> JSON-safe."""
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.ndarray):
        return _clean(o.tolist())
    return o


def J(o) -> JSONResponse:
    return JSONResponse(_clean(o))


# ----------------------------------------------------------------------------- pages
@app.get("/")
def index():
    return FileResponse(HERE / "static" / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True, "loaded": _RT is not None}


@app.get("/api/meta")
def meta():
    r = rt()
    cities = _PICKERS["cities"]
    return J({
        "services": [{"id": s.id, "label": s.label, "urgency": s.urgency, "half_life_km": HALF_LIFE_KM[s.urgency], "advice": s.advice}
                     for s in SERVICES],
        "calibration": {k: {"pct": v[0], "n": v[1]} for k, v in (r.calibration or {}).items()},
        "cities": [{"i": i, "label": c.label, "lat": c.lat, "lon": c.lon} for i, c in enumerate(cities.itertuples())],
        "states": _PICKERS["states"],
        "weights": DEFAULT_WEIGHTS,
        "stats": {
            "facilities": int(len(r.facilities)),
            "website_confirmed": int(sum((e["web_status"] == "confirmed_own_site").sum() for e in (r.evidence or {}).values() if "web_status" in e)),
            "needs_review": int(r.facilities["needs_review"].sum()) if "needs_review" in r.facilities else 0,
            "ai_checked": len(r.ai_checks or {}),
        },
    })


@app.get("/api/coverage/{service_id}")
def coverage(service_id: str):
    """All facilities with Strong/Moderate evidence for a service (for the 3D hexagon layer)."""
    r = rt()
    svc = SERVICES_BY_ID.get(service_id)
    if svc is None:
        raise HTTPException(404, "unknown service")
    ev = (r.evidence or {}).get(service_id)
    if ev is None:
        return J({"points": []})
    good = ev[ev["level"].isin(["Strong", "Moderate"])].index
    f = r.facilities.set_index("facility_id").loc[lambda d: d.index.isin(good)]
    f = f.dropna(subset=["eff_lat", "eff_lon"])
    lvl = ev.loc[f.index, "level"]
    pts = [[round(lo, 4), round(la, 4), 2 if lv == "Strong" else 1] for lo, la, lv in zip(f["eff_lon"], f["eff_lat"], lvl)]
    return J({"service": svc.label, "points": pts})


# ----------------------------------------------------------------------------- search
class LocIn(BaseModel):
    mode: str  # city | district | pin | point | text
    city: int | None = None
    state: str | None = None
    district: str | None = None
    pin: str | None = None
    lat: float | None = None
    lon: float | None = None
    text: str | None = None
    choice: int | None = None


class SearchIn(BaseModel):
    service: str
    loc: LocIn
    radius: float = 50
    top_k: int = 5
    safety: bool = True
    only_confirmed: bool = False
    hide_review: bool = False
    min_evidence: float = 0.31
    weights: dict | None = None


def _resolve(l: LocIn):
    r = rt()
    if l.mode == "city" and l.city is not None:
        return location_from_city(_PICKERS["cities"].iloc[int(l.city)], r.district_lookup)
    if l.mode == "district" and l.state and l.district:
        return location_from_district(l.state, l.district, r.district_lookup)
    if l.mode == "pin" and l.pin:
        return resolve_location(l.pin.strip(), r.pin_lookup, r.district_lookup, r.place_index)
    if l.mode == "point" and l.lat is not None and l.lon is not None:
        return location_from_point(l.lat, l.lon, r.district_lookup)
    if l.text:
        return resolve_location(l.text, r.pin_lookup, r.district_lookup, r.place_index, choice=l.choice)
    return None


@app.post("/api/search")
def search(q: SearchIn):
    r = rt()
    svc = SERVICES_BY_ID.get(q.service)
    if svc is None:
        raise HTTPException(400, "unknown service")
    loc = _resolve(q.loc)
    if loc is None or not loc.ok:
        return J({"ok": False, "error": (loc.error if loc else "Choose a location.")})
    res = recommend(r, svc, loc, radius_km=q.radius, top_k=q.top_k, weights=q.weights, min_evidence=q.min_evidence,
                    only_confirmed=q.only_confirmed, hide_review=q.hide_review, guarded=q.safety)
    ctx = district_context(r.nfhs_long, r.district_nfhs_map, loc.state_norm, loc.district_norm, svc.nfhs, service_id=svc.id)
    cal = r.calibration or {}
    return J({
        "ok": True,
        "service": {"id": svc.id, "label": svc.label, "urgency": svc.urgency, "advice": svc.advice, "half_life_km": HALF_LIFE_KM[svc.urgency]},
        "location": {"label": loc.label, "lat": loc.lat, "lon": loc.lon, "district": loc.district, "state": loc.state,
                     "method": loc.method, "precision_km": loc.precision_km, "warnings": loc.warnings,
                     "alternatives": [a["display"] for a in loc.alternatives] if len(loc.alternatives) > 1 else []},
        "recommendations": res.recommendations, "held": res.held, "leads": res.leads,
        "coverage": res.coverage, "notes": res.notes, "abstained": res.abstained, "abstain_reason": res.abstain_reason,
        "funnel": [{"step": a, "n": b, "check": c} for a, b, c in res.funnel], "sensitivity": res.sensitivity,
        "weights": res.weights, "guarded": res.guarded, "searched_radius_km": res.searched_radius_km,
        "calibration": {k: {"pct": v[0], "n": v[1]} for k, v in cal.items()},
        "nfhs": ctx,
    })


# ----------------------------------------------------------------------------- assistant (SSE)
class ChatIn(BaseModel):
    messages: list[dict]
    prior: list[dict] | None = None
    safety: bool = True


@app.post("/api/agent")
def agent(q: ChatIn):
    from findcare.agent import run_agent

    r = rt()
    events: queue.Queue = queue.Queue()

    def work():
        try:
            res = run_agent(r, q.messages, guarded=q.safety, on_event=events.put, prior_outputs=q.prior or [])
            last = next((s["output"] for s in reversed(res.steps) if s["type"] == "tool" and s["tool"] == "search_facilities"
                         and isinstance(s["output"], dict) and s["output"].get("recommendations") is not None), None)
            events.put({"type": "final", "answer": res.answer, "grounding": res.grounding, "fallback": res.used_fallback,
                        "seconds": res.seconds, "trace_id": res.trace_id, "map": last,
                        "tool_outputs": [s["output"] for s in res.steps if s["type"] == "tool"]})
        except Exception as e:  # endpoint unavailable etc.
            events.put({"type": "error", "error": f"{type(e).__name__}: {str(e)[:300]}"})
        events.put(None)

    threading.Thread(target=work, daemon=True).start()

    def stream():
        while True:
            e = events.get()
            if e is None:
                break
            yield "data: " + json.dumps(_clean(e), ensure_ascii=False, default=str) + "\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
