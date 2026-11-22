"""Care-finding agent: an LLM plans and explains; audited tools supply every fact.

Design (the brief requires every recommendation to explain WHY and HOW):
* The LLM (Databricks Foundation Model API, default Llama 3.3 70B) turns a free-text request into tool
  calls, asks a clarifying question when the place is ambiguous, and writes the answer.
* Facts come ONLY from tools that wrap the tested findcare pipeline: location resolution, the ranker
  (with safety mode / abstention), per-facility evidence, NFHS-5 district context.
* A grounding check runs before an answer is shown: every facility named and every quote must appear in
  the tool outputs. On failure the model gets one chance to fix it; otherwise we fall back to a
  deterministic summary of the tool results.
* Every step is recorded (and traced to MLflow when available), so "HOW did you decide?" is the trace.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import time
from dataclasses import dataclass, field

from .geo import resolve_location
from .nfhs import district_context
from .ranking import GUARD_LABEL, recommend
from .services import SERVICES, SERVICES_BY_ID, match_service
from .text import norm

DEFAULT_ENDPOINT = os.getenv("FINDCARE_LLM_ENDPOINT", "databricks-meta-llama-3-3-70b-instruct")
MAX_STEPS = 6

SYSTEM_PROMPT = f"""You are CareFinder, an assistant that helps community health workers, NGO coordinators and district
planners in India find the right healthcare facility for a patient.

How to work:
1. Work out which SERVICE is needed and WHERE the patient is. If the place is missing, ask for it. Map conditions
   to services (e.g. kidney failure -> dialysis, high-risk pregnancy / obstructed labour -> emergency_csection,
   premature or very sick newborn -> nicu, heart attack -> cath_lab, road accident -> trauma).
2. Call search_facilities. If it reports an ambiguous place, ask the user which one (list the options).
3. Use get_facility_evidence when the user asks why/why not about a facility, and district_health_context when
   local health needs are relevant. Use explain_ranking when asked how the ranking was decided.

Rules you must follow:
- State ONLY facts that appear in tool results. Never invent facilities, distances, phone numbers or quotes.
- For each facility you recommend give: name, distance, evidence level, one short exact quote from the tool
  result in double quotes with its field (e.g. Procedures list), and whether its own website confirmed it.
- Say how sure we are using the evidence level. If a calibration figure is provided, quote it exactly as
  "claims rated Strong were confirmed on hospitals' own websites 65% of the time" – never turn it into
  "we are 65% sure" (never write "X% sure/certain/confident"). Mention the main caution for each facility,
  using only cautions listed in the tool result.
- If the tool abstained or held facilities back, say so plainly and give the reason; do not recommend them.
- Always end with: "Please phone the facility to confirm before travelling."
- Do not give medical advice beyond where to seek care. Be concise and use plain language.
Available services: {", ".join(f"{s.id} ({s.label})" for s in SERVICES)}."""

_SERVICE_ENUM = [s.id for s in SERVICES]
REASON_PARAM = {"type": "string", "description": (
    "Your plan, shown live to the user: one sentence (max 20 words) saying what you are checking and why. "
    "Example: 'A 30-week newborn with breathing trouble needs a NICU, so I am searching NICUs near Darbhanga.'")}
TOOLS = [
    {"type": "function", "function": {
        "name": "resolve_location",
        "description": "Turn a place description (city, district, 6-digit PIN code, or 'lat, lon') into a point. Returns options if ambiguous.",
        "parameters": {"type": "object", "properties": {"location": {"type": "string"}}, "required": ["location"]}}},
    {"type": "function", "function": {
        "name": "search_facilities",
        "description": "Rank facilities offering a service near a location, with evidence quotes, website confirmation, cautions, "
                       "facilities held back by safety checks, and whether the system abstained.",
        "parameters": {"type": "object", "properties": {
            "service": {"type": "string", "enum": _SERVICE_ENUM},
            "location": {"type": "string", "description": "city, district, 'place, state', 6-digit PIN, or 'lat, lon'"},
            "radius_km": {"type": "number", "description": "search radius in km (default 50)"},
            "public_only": {"type": "boolean", "description": "only government/public facilities"}},
            "required": ["service", "location"]}}},
    {"type": "function", "function": {
        "name": "get_facility_evidence",
        "description": "All evidence for one facility and service: quotes with fields, website check, ignored mentions, "
                       "record-quality flags, analyst holds. Use to answer 'why' / 'why not' questions.",
        "parameters": {"type": "object", "properties": {
            "facility_id": {"type": "string"}, "service": {"type": "string", "enum": _SERVICE_ENUM}},
            "required": ["facility_id", "service"]}}},
    {"type": "function", "function": {
        "name": "district_health_context",
        "description": "NFHS-5 (2019-21) district health indicators relevant to a service, with the state average.",
        "parameters": {"type": "object", "properties": {
            "service": {"type": "string", "enum": _SERVICE_ENUM}, "location": {"type": "string"}},
            "required": ["service", "location"]}}},
    {"type": "function", "function": {
        "name": "explain_ranking",
        "description": "How the ranking was decided for a search: scoring weights, decision funnel, equal-weight sensitivity, "
                       "and why each facility ranks above the next.",
        "parameters": {"type": "object", "properties": {
            "service": {"type": "string", "enum": _SERVICE_ENUM}, "location": {"type": "string"},
            "radius_km": {"type": "number"}},
            "required": ["service", "location"]}}},
]


# ----------------------------------------------------------------------------- LLM client
class LLM:
    """Minimal client for Databricks Foundation Model API chat endpoints (OpenAI-compatible)."""

    def __init__(self, endpoint: str = DEFAULT_ENDPOINT):
        from databricks.sdk.core import Config

        self.cfg, self.endpoint = Config(), endpoint

    def chat(self, messages: list[dict], tools: list[dict] | None = None, max_tokens: int = 900, temperature: float = 0.1) -> dict:
        import requests

        body = {"messages": messages, "max_tokens": max_tokens, "temperature": temperature}
        if tools:
            body["tools"] = tools
        headers = {**self.cfg.authenticate(), "Content-Type": "application/json"}
        url = f"{self.cfg.host.rstrip('/')}/serving-endpoints/{self.endpoint}/invocations"
        for attempt in range(6):
            r = requests.post(url, headers=headers, json=body, timeout=120)
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 5:
                time.sleep(min(20, 2 ** attempt))  # back off on rate limits / transient errors
                continue
            r.raise_for_status()
            return r.json()["choices"][0]["message"]
        r.raise_for_status()


# ----------------------------------------------------------------------------- tracing (MLflow if available)
def _mlflow():
    if os.getenv("FINDCARE_MLFLOW", "1") == "0":
        return None
    try:
        import mlflow  # noqa: F401  (mlflow or mlflow-tracing)
        return mlflow
    except Exception:
        return None


def setup_tracing(experiment: str | None = None) -> str | None:
    """Send traces to a Databricks MLflow experiment (FINDCARE_MLFLOW_EXPERIMENT or argument). Returns the
    experiment id, or None if MLflow isn't available (the agent still records its own step log)."""
    mf = _mlflow()
    exp = experiment or os.getenv("FINDCARE_MLFLOW_EXPERIMENT")
    if mf is None or not exp:
        return None
    try:
        mf.set_tracking_uri("databricks")
        e = mf.set_experiment(experiment_id=exp) if exp.isdigit() else mf.set_experiment(exp)
        return e.experiment_id
    except Exception:
        return None


@contextlib.contextmanager
def _span(name: str, span_type: str, inputs: dict):
    mf = _mlflow()
    if mf is None:
        yield None
        return
    try:
        cm = mf.start_span(name=name, span_type=span_type)
    except Exception:
        yield None
        return
    with cm as sp:
        try:
            sp.set_inputs(inputs)
        except Exception:
            pass
        yield sp


# ----------------------------------------------------------------------------- tools
class Tools:
    def __init__(self, rt, guarded: bool = True):
        self.rt, self.guarded = rt, guarded

    def _service(self, s):
        return SERVICES_BY_ID.get(s) or match_service(s or "")

    def _loc(self, location):
        return resolve_location(str(location or ""), self.rt.pin_lookup, self.rt.district_lookup, self.rt.place_index)

    def resolve_location(self, location):
        loc = self._loc(location)
        if not loc.ok:
            return {"status": "not_found", "error": loc.error}
        out = {"status": "ok", "label": loc.label, "lat": round(loc.lat, 4), "lon": round(loc.lon, 4), "district": loc.district,
               "state": loc.state, "precision_km": loc.precision_km, "warnings": loc.warnings[:2]}
        if len(loc.alternatives) > 1:
            out.update(status="ambiguous", options=[a["display"] for a in loc.alternatives],
                       hint="Ask the user which one; then search again with 'place, state'.")
        return out

    def _search(self, service, location, radius_km=50, public_only=False):
        svc = self._service(service)
        if svc is None:
            return None, None, {"status": "unknown_service", "error": f"Unknown service '{service}'.", "services": _SERVICE_ENUM}
        if not str(location or "").strip():
            return svc, None, {"status": "missing_location", "hint": "Ask the user where the patient is (city, district, PIN or coordinates)."}
        loc = self._loc(location)
        if not loc.ok:
            return svc, None, {"status": "location_not_found", "error": loc.error}
        if len(loc.alternatives) > 1:
            return svc, loc, {"status": "ambiguous_location", "options": [a["display"] for a in loc.alternatives],
                              "hint": "Ask the user which place they mean, then search with 'place, state'."}
        res = recommend(self.rt, svc, loc, radius_km=float(radius_km or 50), top_k=8 if public_only else 5, guarded=self.guarded)
        if public_only:
            keep = lambda r: str(r.get("operator_type") or "").lower() in {"public", "government"}
            res.recommendations = [r for r in res.recommendations if keep(r)][:5]
            res.held = [r for r in res.held if keep(r)]
        return svc, loc, res

    def search_facilities(self, service, location, radius_km=50, public_only=False):
        svc, loc, res = self._search(service, location, radius_km, public_only)
        if isinstance(res, dict):
            return res
        cal = getattr(self.rt, "calibration", None) or {}
        fmt = lambda r: {
            "facility_id": r["facility_id"], "name": r["name"], "distance_km": r["distance_km"],
            "distance_basis": r["location_basis"], "evidence_level": r["evidence_level"],
            "confirmed_on_own_website": r["web_status"] == "confirmed_own_site", "website_check": r["web_status"],
            "key_quote": (r["citations"][0]["text"] if r["citations"] else None),
            "quote_field": (r["citations"][0]["field"] if r["citations"] else None),
            "phone": r["phone"], "operator": r.get("operator_type"), "cautions": r["cautions"][:2], "score": r["score"],
            "ai_reader_verdict": (r.get("ai_reader") or {}).get("verdict")}
        return {
            "status": "abstained" if res.abstained else "ok",
            "service": svc.label, "location": loc.label, "district": loc.district, "state": loc.state,
            "searched_radius_km": res.searched_radius_km,
            "recommendations": [{"rank": r["rank"], **fmt(r)} for r in res.recommendations],
            "held_for_review": [{"name": r["name"], "facility_id": r["facility_id"], "distance_km": r["distance_km"],
                                 "reasons": [GUARD_LABEL[g] for g in r["guard_issues"]]} for r in res.held[:5]],
            "abstain_reason": res.abstain_reason,
            "notes": [n for n in res.notes if n != res.abstain_reason][:3],
            "calibration": {lvl: f"claims rated {lvl} were confirmed on hospitals' own websites {p:.0f}% of the time"
                            for lvl, (p, _) in cal.items()},
            "advice": svc.advice,
        }

    def get_facility_evidence(self, facility_id, service):
        svc = self._service(service)
        f = self.rt.facilities[self.rt.facilities["facility_id"] == facility_id]
        if svc is None or f.empty:
            return {"status": "not_found", "error": "Unknown facility_id or service."}
        r = f.iloc[0]
        ev = (self.rt.evidence or {}).get(svc.id)
        e = ev.loc[facility_id] if ev is not None and facility_id in ev.index else None
        get = lambda k: (e.get(k) if e is not None and hasattr(e, "get") else None)
        return {
            "status": "ok", "facility_id": facility_id, "name": r["name"], "service": svc.label,
            "evidence_level": get("level") or "None", "evidence_score": float(get("evidence_score") or 0),
            "quotes": [{"field": i["field"], "kind": i["kind"], "text": i["text"], "url": i.get("url"),
                        "attribution_issue": i.get("attribution_issue")} for i in (get("items") or [])[:8]],
            "ignored_mentions": [{"field": i["field"], "text": i["text"]} for i in (get("negated") or [])[:4]],
            "website_check": get("web_status") or "not_checked", "website_page": get("web_url"),
            "attribution_conflict": bool(get("attribution_conflict")), "analyst_hold": get("analyst_hold"),
            "record_flags": [fl["message"] for fl in r["trust_flags"]], "needs_review": bool(r.get("needs_review")),
            "trust_score": float(r["trust_score"]), "phone": r.get("phone"), "address": r.get("address_text"),
            "ai_reader": (getattr(self.rt, "ai_checks", None) or {}).get((facility_id, svc.id)),
            "beds": None if r.get("capacity") != r.get("capacity") else r.get("capacity"),
        }

    def district_health_context(self, service, location):
        svc, loc = self._service(service), self._loc(location)
        if svc is None or not loc.ok:
            return {"status": "not_found", "error": "Unknown service or location."}
        ctx = district_context(self.rt.nfhs_long, self.rt.district_nfhs_map, loc.state_norm, loc.district_norm, svc.nfhs, service_id=svc.id)
        return {"status": "ok" if ctx.get("ok") else "no_data", **{k: v for k, v in ctx.items() if k != "ok"}}

    def explain_ranking(self, service, location, radius_km=50):
        svc, loc, res = self._search(service, location, radius_km)
        if isinstance(res, dict):
            return res
        return {"status": "ok", "location": loc.label,
                "ranked": [{"rank": r["rank"], "name": r["name"], "distance_km": r["distance_km"], "evidence_level": r["evidence_level"],
                            "score": r["score"], "components": r["components"]} for r in res.recommendations],
                "weights": res.weights, "decision_funnel": [{"step": a, "facilities": b} for a, b, _ in res.funnel],
                "equal_weight_check": res.sensitivity,
                "why_each_ranks_above_next": [r["vs_next"] for r in res.recommendations if r.get("vs_next")],
                "safety_checks": list(GUARD_LABEL.values()),
                "method": "score = weighted evidence + closeness (halves every N km by urgency) + record quality + size; "
                          "safety mode ranks only records passing every check, otherwise abstains."}

    def call(self, name: str, args: dict) -> dict:
        fn = getattr(self, name, None)
        if fn is None or name.startswith("_") or name == "call":
            return {"status": "error", "error": f"Unknown tool {name}"}
        args = {k: v for k, v in args.items() if k != "reason"}
        try:
            return fn(**args)
        except TypeError as e:
            return {"status": "error", "error": f"Bad arguments: {e}"}


# ----------------------------------------------------------------------------- grounding check
_QUOTE_RE = re.compile(r"[\"“]([^\"”]{12,300})[\"”]")
_KM_RE = re.compile(r"(\d+(?:\.\d+)?)\s?km\b")
# Calibration is a website-confirmation rate (a lower bound), not a probability that the answer is right.
_OVERCLAIM_RE = re.compile(r"\b(?:we are|we're|i am|i'm)?\s*\d{1,3}\s?%\s*(?:sure|certain|confident|likely|chance|probability)\b"
                           r"|\b(?:sure|certain|confident)\s+(?:at|of)\s+\d{1,3}\s?%", re.I)


def _quoted_spans(text: str) -> list[str]:
    """Quoted spans of >= 12 chars, pairing quote marks in order (so the text BETWEEN two quotes is never
    mistaken for a quote, e.g. '"Strong" evidence ... "1.5T MRI"')."""
    out = [m.group(1) for m in re.finditer(r"“([^”]{12,400})”", text)]
    marks = [m.start() for m in re.finditer(r'"', text)]
    for a, b in zip(marks[0::2], marks[1::2]):
        span = text[a + 1:b].strip()
        if len(span) >= 12:
            out.append(span)
    return out


def check_grounding(answer: str, tool_outputs: list[dict], all_names: set[str]) -> dict:
    """Every quote and facility named in the answer must come from the tool outputs."""
    blob = json.dumps(tool_outputs, ensure_ascii=False)
    blob_n = norm(blob)
    issues = []
    quotes = _quoted_spans(answer or "")
    for q in quotes:
        # a quote abbreviated with "..." is fine if every fragment is genuine
        frags = [norm(f) for f in re.split(r"\.\.\.|…", q) if len(norm(f)) >= 8]
        if frags and not all(f in blob_n for f in frags):
            issues.append(f"quote not found in tool results: “{q[:80]}”")
    ans_n = f" {norm(answer)} "
    tool_names = {norm(m) for m in re.findall(r'"name":\s?"([^"]+)"', blob)}
    mentioned = [n for n in all_names if len(n) >= 10 and len(n.split()) >= 2 and f" {n} " in ans_n]
    for n in mentioned:
        # allowed if it is (part of) a facility name returned by a tool, e.g. "Care Hospital" in "CARE Hospitals Nagpur"
        if not any(n in t or t in n for t in tool_names):
            issues.append(f"facility not in tool results: {n.title()}")
    dists = [float(x) for x in re.findall(r'"distance_km":\s?(\d+(?:\.\d+)?)', blob)] + \
            [float(x) for x in re.findall(r'"searched_radius_km":\s?(\d+(?:\.\d+)?)', blob)] + \
            [float(x) for x in re.findall(r'"radius_km":\s?(\d+(?:\.\d+)?)', blob)]
    for x in _KM_RE.findall(answer or ""):
        v = float(x)
        if dists and not any(abs(v - d) <= max(1.0, 0.1 * d) for d in dists) and v not in (10, 25, 50, 100, 200):
            issues.append(f"distance {v:g} km not in tool results")
    for m in _OVERCLAIM_RE.finditer(answer or ""):
        issues.append(f"overclaims certainty ('{m.group(0).strip()}'): the calibration is a website-confirmation rate, not a probability")
    return {"ok": not issues, "quotes_checked": len(quotes), "facilities_checked": len(mentioned), "issues": issues[:6]}


def fallback_answer(tool_outputs: list[dict]) -> str:
    """Deterministic answer from the last search result (used if the LLM can't produce a grounded one)."""
    s = next((t for t in reversed(tool_outputs) if isinstance(t, dict) and "recommendations" in t), None)
    if not s:
        return "I couldn't complete the search. Please choose a service and a location in the Search tab."
    if s["status"] == "abstained":
        return f"No recommendation for {s['service']} near {s['location']}: {s['abstain_reason']}\n\nPlease phone the facility to confirm before travelling."
    lines = [f"**{s['service']} near {s['location']}** (checked results):"]
    for r in s["recommendations"][:3]:
        web = " · confirmed on its own website" if r["confirmed_on_own_website"] else ""
        lines.append(f"{r['rank']}. **{r['name']}**, {r['distance_km']} km, {r['evidence_level']} evidence{web}. "
                     f"{r['quote_field']}: \"{r['key_quote']}\"" + (f" · Phone: {r['phone']}" if r.get("phone") else ""))
    lines.append("\nPlease phone the facility to confirm before travelling.")
    return "\n".join(lines)


# ----------------------------------------------------------------------------- agent loop
@dataclass
class AgentResult:
    answer: str
    steps: list[dict] = field(default_factory=list)
    tool_outputs: list[dict] = field(default_factory=list)
    grounding: dict = field(default_factory=dict)
    used_fallback: bool = False
    trace_id: str | None = None
    seconds: float = 0.0


def run_agent(rt, history: list[dict], llm=None, guarded: bool = True, max_steps: int = MAX_STEPS, on_event=None,
              prior_outputs: list[dict] | None = None) -> AgentResult:
    """history: [{"role": "user"|"assistant", "content": str}, ...] ending with the user's message.
    on_event(step_dict) is called live for every step (tool start/end, checks, retries) so a UI can stream them.
    prior_outputs: tool results from earlier turns of the same conversation (follow-ups may refer to them)."""
    prior = list(prior_outputs or [])
    emit = on_event or (lambda e: None)
    t0 = time.time()
    llm = llm or LLM()
    tools = Tools(rt, guarded=guarded)
    all_names = set(rt.facilities["name"].dropna().map(norm))
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + [{"role": m["role"], "content": m["content"]} for m in history[-10:]]
    res = AgentResult(answer="")
    with _span("care_finder_agent", "AGENT", {"request": history[-1]["content"] if history else ""}) as root:
        retried = False
        for _ in range(max_steps):
            emit({"type": "thinking"})
            t_llm = time.time()
            with _span("llm", "CHAT_MODEL", {"n_messages": len(messages)}) as sp:
                msg = llm.chat(messages, TOOLS)
                if sp is not None:
                    sp.set_outputs({"tool_calls": [c["function"]["name"] for c in (msg.get("tool_calls") or [])],
                                    "content": str(msg.get("content"))[:500]})
            calls = msg.get("tool_calls") or []
            if calls:
                messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
                for c in calls:
                    name = c["function"]["name"]
                    try:
                        args = json.loads(c["function"].get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    reason = str(args.get("reason") or "").strip()
                    shown = {k: v for k, v in args.items() if k != "reason"}
                    emit({"type": "tool_start", "tool": name, "args": shown, "reason": reason, "llm_seconds": round(time.time() - t_llm, 1)})
                    t_tool = time.time()
                    with _span(f"tool:{name}", "TOOL", args) as sp:
                        out = tools.call(name, args)
                        if sp is not None:
                            sp.set_outputs(out)
                    step = {"type": "tool", "tool": name, "args": shown, "reason": reason, "output": out,
                            "seconds": round(time.time() - t_tool, 2), "llm_seconds": round(t_tool - t_llm, 1)}
                    res.steps.append(step)
                    emit(step)
                    res.tool_outputs.append(out)
                    messages.append({"role": "tool", "tool_call_id": c.get("id", name), "content": json.dumps(out, ensure_ascii=False, default=str)[:12000]})
                continue
            answer = (msg.get("content") or "").strip()
            emit({"type": "checking"})
            g = check_grounding(answer, prior + res.tool_outputs, all_names)
            res.steps.append({"type": "answer", "grounding": g, "attempt": 2 if retried else 1,
                              "llm_seconds": round(time.time() - t_llm, 1)})
            emit(res.steps[-1])
            if g["ok"] or not (prior or res.tool_outputs):
                res.answer, res.grounding = answer, g
                break
            if not retried:
                retried = True
                res.steps.append({"type": "retry", "issues": g["issues"]})
                emit(res.steps[-1])
                messages.append({"role": "assistant", "content": answer})
                messages.append({"role": "user", "content": "SYSTEM CHECK: your answer contains statements not found in the tool results: "
                                 + "; ".join(g["issues"]) + ". Rewrite the answer using only facts and exact quotes from the tool results."})
                continue
            res.answer, res.grounding, res.used_fallback = fallback_answer(res.tool_outputs or prior), g, True
            res.steps.append({"type": "fallback"})
            emit(res.steps[-1])
            break
        else:
            res.answer, res.used_fallback = fallback_answer(res.tool_outputs), True
            res.grounding = check_grounding(res.answer, res.tool_outputs, all_names)
        if root is not None:
            try:
                root.set_outputs({"answer": res.answer, "grounded": res.grounding.get("ok"), "fallback": res.used_fallback})
                res.trace_id = getattr(root, "request_id", None) or getattr(root, "trace_id", None)
            except Exception:
                pass
    res.seconds = round(time.time() - t0, 1)
    return res


for _t in TOOLS:  # every tool call carries the model's stated plan (models don't emit text alongside tool calls)
    _p = _t["function"]["parameters"]
    _p["properties"] = {"reason": REASON_PARAM, **_p["properties"]}
    _p["required"] = ["reason"] + [r for r in _p["required"] if r != "reason"]
