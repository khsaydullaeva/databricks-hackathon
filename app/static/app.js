/* Find the Right Care – HTML/3D front-end.
 * Open-source stack: MapLibre GL JS (3D map + terrain), deck.gl (3D columns, arcs, hexagons),
 * OpenFreeMap tiles, AWS Terrain Tiles (open data), marked + DOMPurify (assistant markdown).
 * All facility facts come from the FastAPI backend, which runs the tested findcare pipeline.
 */
"use strict";

// ----------------------------------------------------------------------------- helpers
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const md = (s) => DOMPurify.sanitize(marked.parse(String(s ?? ""), { breaks: true }));
const fmtKm = (k) => (k == null ? "–" : k < 10 ? `${(+k).toFixed(1)} km` : `${Math.round(k)} km`);
const COLORS = { Strong: [63, 208, 122], Moderate: [242, 193, 78], Limited: [240, 136, 62], Indirect: [139, 149, 168], held: [245, 158, 11], patient: [96, 165, 250] };
const css = (rgb, a = 1) => `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${a})`;
const ICONS = { dialysis: "🩸", emergency_csection: "🤰", nicu: "👶", cardiac_surgery: "🫀", cath_lab: "💓", mri: "🧲", ct_scan: "🩻",
  burn_care: "🔥", trauma: "🚑", emergency: "🚨", icu: "🫁", oncology: "🎗️", radiotherapy: "☢️", blood_bank: "🅱️", neurosurgery: "🧠",
  maternity: "🍼", transplant: "🔁", cataract: "👁️", joint_replacement: "🦴", psychiatry: "💬" };
const FEATURED = ["dialysis", "emergency_csection", "nicu", "trauma", "cardiac_surgery", "mri", "burn_care", "icu"];
const TOOL_ICON = { resolve_location: "📍", search_facilities: "🔎", get_facility_evidence: "📄", district_health_context: "📊", explain_ranking: "⚖️" };

function toast(msg, ms = 2600) {
  const t = document.createElement("div");
  t.className = "toast"; t.textContent = msg; document.body.appendChild(t);
  setTimeout(() => t.remove(), ms);
}
async function api(path, body) {
  const r = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

// ----------------------------------------------------------------------------- state
const S = {
  meta: null, service: "dialysis", mode: "city", city: null, state: null, district: null, pin: "", point: null,
  result: null, hex: [], show: { hex: true, arcs: true, terrain: false, three: true }, spin: false, tab: "rec",
  activeId: null, chat: [], prior: [], busy: false,
};

// ----------------------------------------------------------------------------- map
// Basemap: OpenFreeMap "positron" (open data, no key), recoloured in code into a dark navy theme.
async function darkStyle() {
  const st = await (await fetch("https://tiles.openfreemap.org/styles/positron")).json();
  for (const l of st.layers) {
    const id = l.id.toLowerCase(), p = (l.paint = l.paint || {});
    if (l.type === "background") p["background-color"] = "#0b1528";
    else if (l.type === "fill") {
      if (id.includes("water")) { p["fill-color"] = "#06182f"; p["fill-opacity"] = 1; }
      else if (id.includes("park") || id.includes("wood") || id.includes("forest") || id.includes("grass")) { p["fill-color"] = "#10223a"; p["fill-opacity"] = 0.35; }
      else if (id.includes("building")) { p["fill-color"] = "#1b2c4a"; p["fill-opacity"] = 0.85; }
      else { p["fill-color"] = "#112038"; p["fill-opacity"] = 0.3; }
      delete p["fill-outline-color"];
    } else if (l.type === "line") {
      if (id.includes("water") || id.includes("river")) p["line-color"] = "#123a6b";
      else if (id.includes("boundary") || id.includes("admin")) { p["line-color"] = id.includes("country") || id.includes("2") ? "rgba(94,234,212,0.7)" : "rgba(140,170,220,0.3)"; }
      else p["line-color"] = id.includes("motorway") || id.includes("trunk") ? "#34507f" : "#203253";
    } else if (l.type === "symbol") {
      p["text-color"] = id.includes("country") ? "#c9d6f0" : id.includes("water") ? "#4f78b0" : "#8fa2c6";
      p["text-halo-color"] = "#05080f"; p["text-halo-width"] = 1.4;
      if (l.layout && l.layout["text-field"]) l.layout["text-field"] = ["coalesce", ["get", "name:en"], ["get", "name_en"], ["get", "name"]];
      if (p["icon-opacity"] !== undefined || l.layout?.["icon-image"]) p["icon-opacity"] = 0.35;
    }
  }
  return st;
}
const STYLE_PARAM = new URLSearchParams(location.search).get("style");
const map = new maplibregl.Map({
  container: "map",
  style: STYLE_PARAM ? "https://tiles.openfreemap.org/styles/" + STYLE_PARAM : { version: 8, sources: {}, layers: [{ id: "bg", type: "background", paint: { "background-color": "#070d1a" } }] },
  center: [80.2, 22.6], zoom: 4.15, pitch: 50, bearing: -14, maxPitch: 80, attributionControl: { compact: true },
});
const overlay = new deck.MapboxOverlay({ interleaved: true, layers: [], getTooltip: ({ object }) => object && object.__tip ? { html: object.__tip, className: "deck-tooltip" } : null });
map.addControl(overlay);
map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "bottom-right");

// Terrain shading + sky are (re-)added every time a style finishes loading.
map.on("style.load", () => {
  const tiles = ["https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"];
  if (!map.getSource("dem-hill")) map.addSource("dem-hill", { type: "raster-dem", tiles, encoding: "terrarium", tileSize: 256, maxzoom: 13, attribution: "Terrain: AWS Terrain Tiles / Mapzen" });
  if (!map.getSource("dem-terrain")) map.addSource("dem-terrain", { type: "raster-dem", tiles, encoding: "terrarium", tileSize: 256, maxzoom: 13 });
  // shading goes just above the background, BELOW water/landcover/borders so it never hides the basemap
  const firstData = (map.getStyle().layers || []).find((l) => l.type !== "background");
  if (!map.getLayer("hillshade")) map.addLayer({ id: "hillshade", type: "hillshade", source: "dem-hill",
    paint: { "hillshade-exaggeration": 0.55, "hillshade-shadow-color": "#02060f", "hillshade-highlight-color": "#2b4f7a", "hillshade-accent-color": "#0e1d33" } },
    firstData && firstData.id);
  try { map.setSky({ "sky-color": "#0a1222", "horizon-color": "#18304f", "fog-color": "#0a1222", "sky-horizon-blend": 0.6, "horizon-fog-blend": 0.7, "fog-ground-blend": 0.35 }); } catch (e) { /* optional */ }
  if (S.show.terrain) map.setTerrain({ source: "dem-terrain", exaggeration: 1.6 });
  render();
});
map.on("click", onMapClick);
// Apply the recoloured basemap as soon as it is fetched (don't wait for "load", which waits for all terrain tiles).
if (!STYLE_PARAM) {
  darkStyle().then((st) => map.setStyle(st)).catch(() => map.setStyle("https://tiles.openfreemap.org/styles/positron"));
}

let pulseT = 0;
function pulse() {
  pulseT = (pulseT + 0.012) % 1;
  if (patientPos()) render();
  if (S.spin) map.setBearing(map.getBearing() + 0.08);
  requestAnimationFrame(pulse);
}
requestAnimationFrame(pulse);

function patientPos() {
  if (S.result && S.result.location) return [S.result.location.lon, S.result.location.lat];
  if (S.mode === "point" && S.point) return [S.point.lon, S.point.lat];
  return null;
}

function tipFor(r, kind) {
  const lvl = r.evidence_level || "";
  return `<b>${esc(r.name)}</b><br>${esc(fmtKm(r.distance_km))} · ${esc(lvl)} evidence${r.web_status === "confirmed_own_site" ? " · ✔ website" : ""}` +
    (kind === "held" ? "<br><span style='color:#f59e0b'>Held for review</span>" : "") +
    (r.citations && r.citations[0] ? `<br><i>“${esc(r.citations[0].text).slice(0, 120)}”</i>` : "");
}

function render() {
  const layers = [];
  const p = patientPos();
  if (S.show.hex && S.hex.length) {
    layers.push(new deck.HexagonLayer({
      id: "coverage", data: S.hex, getPosition: (d) => [d[0], d[1]], getElevationWeight: (d) => d[2], getColorWeight: (d) => d[2],
      elevationAggregation: "SUM", colorAggregation: "SUM", radius: 38000, coverage: 0.88, extruded: true, elevationScale: 380,
      upperPercentile: 99, opacity: 0.78, pickable: true, material: { ambient: 0.55, diffuse: 0.7, shininess: 40 },
      colorRange: [[16, 70, 90], [20, 120, 130], [32, 170, 160], [94, 234, 212], [140, 180, 250], [167, 139, 250]],
      onHover: ({ object }) => { if (object) object.__tip = `<b>${object.count ?? object.points?.length ?? ""}</b> facilities with reliable evidence here`; },
    }));
  }
  const R = S.result;
  if (R && R.ok) {
    const recs = R.recommendations || [], held = R.held || [], leads = R.leads || [];
    const all = [...recs.map((r) => ({ ...r, __k: "rec" })), ...held.map((r) => ({ ...r, __k: "held" })), ...leads.map((r) => ({ ...r, __k: "lead" }))]
      .filter((r) => r.lat != null && r.lon != null);
    all.forEach((r) => { r.__tip = tipFor(r, r.__k); });
    const color = (r) => (r.__k === "held" ? COLORS.held : r.__k === "lead" ? COLORS.Indirect : COLORS[r.evidence_level] || COLORS.Indirect);
    layers.push(new deck.ScatterplotLayer({
      id: "ring", data: [{ p }], getPosition: (d) => d.p, getRadius: (R.searched_radius_km || 50) * 1000, filled: false, stroked: true,
      getLineColor: [94, 234, 212, 70], lineWidthMinPixels: 1.5,
    }));
    layers.push(new deck.ColumnLayer({
      id: "facilities", data: all, getPosition: (r) => [r.lon, r.lat], diskResolution: 36, radius: 520, extruded: true, pickable: true,
      getElevation: (r) => (r.__k === "lead" ? 350 : 500 + (r.score || 0.3) * 3600), elevationScale: 1,
      getFillColor: (r) => [...color(r), r.facility_id === S.activeId ? 255 : 225], getLineColor: [255, 255, 255, 60],
      material: { ambient: 0.5, diffuse: 0.75, shininess: 60, specularColor: [80, 100, 140] },
      onClick: ({ object }) => object && openDetail(object, object.__k), updateTriggers: { getFillColor: [S.activeId] },
    }));
    layers.push(new deck.TextLayer({
      id: "ranks", data: all.filter((r) => r.__k === "rec"), getPosition: (r) => [r.lon, r.lat, 800 + (r.score || 0.3) * 3600],
      getText: (r) => String(r.rank), getSize: 15, getColor: [255, 255, 255, 240], fontWeight: 800, billboard: true,
      background: true, getBackgroundColor: (r) => [...color(r), 230], backgroundPadding: [6, 3], fontFamily: "Inter, system-ui",
    }));
    if (S.show.arcs && p) {
      layers.push(new deck.ArcLayer({
        id: "arcs", data: recs.filter((r) => r.lat != null), getSourcePosition: () => p, getTargetPosition: (r) => [r.lon, r.lat],
        getSourceColor: [96, 165, 250, 230], getTargetColor: (r) => [...(COLORS[r.evidence_level] || COLORS.Indirect), 230],
        getWidth: (r) => (r.rank === 1 ? 4.5 : 2.5), getHeight: 0.6, greatCircle: false,
      }));
    }
  }
  if (p) {
    layers.push(new deck.ScatterplotLayer({
      id: "pulse", data: [{ p }], getPosition: (d) => d.p, getRadius: 400 + pulseT * 4200, radiusUnits: "meters",
      getFillColor: [96, 165, 250, Math.round(120 * (1 - pulseT))], stroked: false, updateTriggers: { getRadius: pulseT, getFillColor: pulseT },
    }));
    layers.push(new deck.ScatterplotLayer({
      id: "patient", data: [{ p, __tip: "<b>Patient location</b>" }], getPosition: (d) => d.p, getRadius: 9, radiusUnits: "pixels",
      getFillColor: [96, 165, 250, 255], getLineColor: [255, 255, 255, 255], lineWidthMinPixels: 2.5, stroked: true, pickable: true,
    }));
  }
  overlay.setProps({ layers });
}

const NOFLY = new URLSearchParams(location.search).has("nofly");  // for screenshots / low-power devices
function flyTo(lon, lat, zoom = 10.3) {
  const cam = { center: [lon, lat], zoom, pitch: S.show.three ? 62 : 0, bearing: S.show.three ? -22 : 0 };
  if (NOFLY) map.jumpTo(cam); else map.flyTo({ ...cam, duration: 2600, essential: true });
}

function onMapClick(e) {
  if (S.mode !== "point") return;
  S.point = { lat: +e.lngLat.lat.toFixed(5), lon: +e.lngLat.lng.toFixed(5) };
  renderLoc(); validate(); render();
}

// ----------------------------------------------------------------------------- map controls
$("#btn3d").onclick = (e) => {
  S.show.three = !S.show.three; e.currentTarget.classList.toggle("on", S.show.three);
  map.easeTo({ pitch: S.show.three ? 60 : 0, bearing: S.show.three ? -20 : 0, duration: 1200 });
};
$("#btnHex").onclick = (e) => { S.show.hex = !S.show.hex; e.currentTarget.classList.toggle("on", S.show.hex); render(); };
$("#btnArcs").onclick = (e) => { S.show.arcs = !S.show.arcs; e.currentTarget.classList.toggle("on", S.show.arcs); render(); };
$("#btnTerrain").onclick = (e) => {
  S.show.terrain = !S.show.terrain; e.currentTarget.classList.toggle("on", S.show.terrain);
  map.setTerrain(S.show.terrain ? { source: "dem-terrain", exaggeration: 1.6 } : null);
  if (S.show.terrain && map.getPitch() < 50) map.easeTo({ pitch: 65, duration: 1000 });
};
$("#btnSpin").onclick = (e) => { S.spin = !S.spin; e.currentTarget.classList.toggle("on", S.spin); };

// ----------------------------------------------------------------------------- search form
async function init() {
  try {
    S.meta = await api("/api/meta");
  } catch (e) {
    toast("Couldn't load data: " + e.message, 8000);
    return;
  }
  const st = S.meta.stats;
  $("#stats").innerHTML =
    `<span class="chip"><b>${st.facilities.toLocaleString()}</b> facilities</span>` +
    `<span class="chip ok"><b>${st.website_confirmed.toLocaleString()}</b> claims confirmed on hospital websites</span>` +
    `<span class="chip"><b>${st.ai_checked.toLocaleString()}</b> claims AI-checked</span>` +
    `<span class="chip">Safety mode <b style="color:var(--strong)">ON</b></span>`;
  renderServices(); renderLoc(); renderWeights(); validate(); loadHex();
  applyLink();
}

// Shareable links: /?service=nicu&pin=846004 · &city=Patna, Bihar · &lat=25.61&lon=85.14 · &state=bihar&district=gaya · &assistant=1 · &ask=...
function applyLink() {
  const q = new URLSearchParams(location.search);
  if (q.get("service") && S.meta.services.some((s) => s.id === q.get("service"))) { S.service = q.get("service"); renderServices(); loadHex(); }
  const setMode = (m) => { S.mode = m; $$("#locTabs .tab").forEach((x) => x.classList.toggle("sel", x.dataset.mode === m)); };
  if (q.get("pin")) { setMode("pin"); S.pin = q.get("pin"); }
  else if (q.get("lat") && q.get("lon")) { setMode("point"); S.point = { lat: +q.get("lat"), lon: +q.get("lon") }; }
  else if (q.get("state") && q.get("district")) { setMode("district"); S.state = q.get("state"); S.district = q.get("district"); }
  else if (q.get("city")) { const c = S.meta.cities.find((x) => x.label.toLowerCase() === q.get("city").toLowerCase()); if (c) { setMode("city"); S.city = c.i; } }
  if (q.get("radius")) { $("#radius").value = q.get("radius"); $("#radiusVal").textContent = q.get("radius") + " km"; }
  renderLoc(); validate();
  if (q.get("assistant")) setAssist(true);
  if (q.get("ask")) { setAssist(true); sendChat(q.get("ask")); }
  if (locPayload() && (q.get("pin") || q.get("lat") || q.get("district") || q.get("city"))) runSearch();
}

function renderServices() {
  const svcs = S.meta.services;
  const chips = FEATURED.map((id) => svcs.find((s) => s.id === id)).filter(Boolean).map((s) =>
    `<button class="svc ${s.id === S.service ? "sel" : ""}" data-id="${s.id}"><span class="ic">${ICONS[s.id] || "🏥"}</span>${esc(s.label.split(" (")[0].split(" / ")[0])}</button>`).join("");
  const rest = svcs.filter((s) => !FEATURED.includes(s.id));
  const sel = `<div class="svc-more"><select class="svc-select" id="moreSvc"><option value="">More services…</option>${rest.map((s) =>
    `<option value="${s.id}" ${s.id === S.service ? "selected" : ""}>${ICONS[s.id] || ""} ${esc(s.label)}</option>`).join("")}</select></div>`;
  $("#services").innerHTML = chips + sel;
  $$("#services .svc").forEach((b) => (b.onclick = () => pickService(b.dataset.id)));
  $("#moreSvc").onchange = (e) => e.target.value && pickService(e.target.value);
}
function pickService(id) { S.service = id; renderServices(); loadHex(); }

async function loadHex() {
  try { S.hex = (await api(`/api/coverage/${S.service}`)).points; } catch (e) { S.hex = []; }
  render();
}

$$("#locTabs .tab").forEach((t) => (t.onclick = () => {
  S.mode = t.dataset.mode; $$("#locTabs .tab").forEach((x) => x.classList.toggle("sel", x === t));
  document.body.classList.toggle("pin-cursor", S.mode === "point");
  renderLoc(); validate();
  if (S.mode === "point") toast("Click on the map where the patient is");
}));

function renderLoc() {
  const b = $("#locBody");
  if (S.mode === "city") {
    b.innerHTML = `<input class="field" id="cityIn" list="cityList" placeholder="Start typing a city… e.g. Patna, Bihar" autocomplete="off" value="${S.city != null ? esc(S.meta.cities[S.city].label) : ""}">
      <datalist id="cityList">${S.meta.cities.map((c) => `<option value="${esc(c.label)}">`).join("")}</datalist>
      <div class="hint" id="locHint">${S.meta.cities.length} cities with facilities in the dataset.</div>`;
    $("#cityIn").oninput = (e) => {
      const c = S.meta.cities.find((x) => x.label.toLowerCase() === e.target.value.trim().toLowerCase());
      S.city = c ? c.i : null;
      $("#locHint").className = "hint" + (c ? " ok" : "");
      $("#locHint").textContent = c ? `✓ ${c.label}` : "Pick a city from the list (or use District, PIN or Map).";
      validate();
    };
  } else if (S.mode === "district") {
    const states = Object.entries(S.meta.states).sort((a, b) => a[1].title.localeCompare(b[1].title));
    b.innerHTML = `<select class="field" id="stateIn"><option value="">State / UT…</option>${states.map(([k, v]) => `<option value="${esc(k)}" ${k === S.state ? "selected" : ""}>${esc(v.title)}</option>`).join("")}</select>
      <select class="field" id="distIn" ${S.state ? "" : "disabled"}><option value="">District…</option>${S.state ? S.meta.states[S.state].districts.map((d) =>
        `<option value="${esc(d.id)}" ${d.id === S.district ? "selected" : ""}>${esc(d.title)}</option>`).join("") : ""}</select>
      <div class="hint">Searched from the district centre – less precise than a PIN or map pin.</div>`;
    $("#stateIn").onchange = (e) => { S.state = e.target.value || null; S.district = null; renderLoc(); validate(); };
    $("#distIn").onchange = (e) => { S.district = e.target.value || null; validate(); };
  } else if (S.mode === "pin") {
    b.innerHTML = `<input class="field" id="pinIn" inputmode="numeric" maxlength="6" placeholder="6-digit PIN code, e.g. 800001" value="${esc(S.pin)}">
      <div class="hint" id="locHint">India Post PIN directory · 19,586 PIN codes.</div>`;
    $("#pinIn").oninput = (e) => {
      S.pin = e.target.value.replace(/\D/g, "").slice(0, 6); e.target.value = S.pin;
      const ok = /^[1-9]\d{5}$/.test(S.pin);
      $("#locHint").className = "hint" + (S.pin.length === 6 ? (ok ? " ok" : " err") : "");
      $("#locHint").textContent = S.pin.length < 6 ? "India Post PIN directory · 19,586 PIN codes." : ok ? `✓ PIN ${S.pin}` : "A PIN has 6 digits and doesn't start with 0.";
      validate();
    };
  } else {
    b.innerHTML = `<div class="row"><input class="field" id="latIn" type="number" step="0.0001" placeholder="Latitude" value="${S.point ? S.point.lat : ""}">
      <input class="field" id="lonIn" type="number" step="0.0001" placeholder="Longitude" value="${S.point ? S.point.lon : ""}"></div>
      <div class="hint ${S.point ? "ok" : ""}">${S.point ? `✓ Pin at ${S.point.lat}, ${S.point.lon} – click the map to move it` : "Click the map to drop a pin, or type coordinates."}</div>`;
    const upd = () => {
      const la = parseFloat($("#latIn").value), lo = parseFloat($("#lonIn").value);
      S.point = isFinite(la) && isFinite(lo) ? { lat: la, lon: lo } : null; validate(); render();
    };
    $("#latIn").onchange = upd; $("#lonIn").onchange = upd;
  }
}

function locPayload() {
  if (S.mode === "city" && S.city != null) return { mode: "city", city: S.city };
  if (S.mode === "district" && S.state && S.district) return { mode: "district", state: S.state, district: S.district };
  if (S.mode === "pin" && /^[1-9]\d{5}$/.test(S.pin)) return { mode: "pin", pin: S.pin };
  if (S.mode === "point" && S.point) return { mode: "point", lat: S.point.lat, lon: S.point.lon };
  return null;
}
function validate() { $("#go").disabled = !locPayload(); }

$("#radius").oninput = (e) => ($("#radiusVal").textContent = `${e.target.value} km`);

function renderWeights() {
  const w = S.meta.weights, names = { evidence: "Evidence", proximity: "Closeness", trust: "Record quality", scale: "Facility size" };
  $("#weights").innerHTML = Object.entries(w).map(([k, v]) =>
    `<div class="range-row" style="margin-top:6px">${names[k]} <input type="range" min="0" max="1" step="0.05" value="${v}" data-w="${k}"> <b>${(+v).toFixed(2)}</b></div>`).join("");
  $$("#weights input").forEach((i) => (i.oninput = () => { i.nextElementSibling.textContent = (+i.value).toFixed(2); customNote(); }));
  ["safety", "onlyConfirmed", "hideReview"].forEach((id) => ($("#" + id).onchange = customNote));
}
function weightsNow() { const o = {}; $$("#weights input").forEach((i) => (o[i.dataset.w] = +i.value)); return o; }
function customNote() {
  const w = weightsNow(), d = S.meta.weights, diff = [];
  for (const k in w) if (Math.abs(w[k] - d[k]) > 1e-9) diff.push(`${k} ${w[k].toFixed(2)}`);
  if (!$("#safety").checked) diff.push("safety mode off");
  if ($("#onlyConfirmed").checked) diff.push("website-confirmed only");
  if ($("#hideReview").checked) diff.push("hiding records that need review");
  $("#customNote").hidden = !diff.length;
  $("#customNote").textContent = "⚙ Custom ranking: " + diff.join(", ");
}

$("#go").onclick = runSearch;
async function runSearch() {
  const loc = locPayload(); if (!loc) return;
  $("#go").disabled = true; $("#go").textContent = "Searching…";
  try {
    const body = { service: S.service, loc, radius: +$("#radius").value, top_k: 5, safety: $("#safety").checked,
      only_confirmed: $("#onlyConfirmed").checked, hide_review: $("#hideReview").checked, weights: weightsNow() };
    const R = await api("/api/search", body);
    if (!R.ok) { toast(R.error || "Couldn't resolve that location", 4000); return; }
    S.result = R; S.tab = "rec"; S.activeId = null;
    $("#hero").classList.add("gone");
    if (S.show.hex) { S.show.hex = false; $("#btnHex").classList.remove("on"); }
    renderResults(); render(); flyTo(R.location.lon, R.location.lat, R.searched_radius_km > 60 ? 8.6 : 10.2);
    if (new URLSearchParams(location.search).get("open") && R.recommendations[0]) setTimeout(() => openDetail(R.recommendations[0], "rec"), 300);
  } catch (e) {
    toast("Search failed: " + e.message, 5000);
  } finally {
    $("#go").textContent = "Find care"; validate();
  }
}

// ----------------------------------------------------------------------------- results
function scoreBar(c) {
  const parts = [["evidence", "#3fd07a"], ["proximity", "#60a5fa"], ["trust", "#a78bfa"], ["scale", "#f2c14e"]];
  return `<div class="scorebar" title="evidence · closeness · record quality · size">${parts.map(([k, col]) => `<i style="width:${Math.max(0, (c[k] || 0) * 100)}%;background:${col}"></i>`).join("")}</div>`;
}
function cardHTML(r, kind) {
  const col = kind === "held" ? COLORS.held : kind === "lead" ? COLORS.Indirect : COLORS[r.evidence_level] || COLORS.Indirect;
  const cite = r.citations && r.citations[0];
  const ai = r.ai_reader && r.ai_reader.verdict && r.ai_reader.verdict !== "unverified" ? r.ai_reader.verdict : null;
  return `<div class="card ${r.facility_id === S.activeId ? "active" : ""}" data-id="${esc(r.facility_id)}" data-kind="${kind}">
    <div class="top"><div class="rank" style="background:${css(col)}">${kind === "rec" ? r.rank : kind === "held" ? "!" : "?"}</div>
      <div style="min-width:0"><h3>${esc(r.name)}</h3><div class="sub">${esc(fmtKm(r.distance_km))}${r.location_basis !== "facility coordinates" ? " (approx.)" : ""} · ${esc((r.facility_type || "facility"))}${r.operator_type ? " · " + esc(r.operator_type) : ""}</div></div></div>
    <div class="pills"><span class="pill lvl-${esc(r.evidence_level)}">${esc(r.evidence_level)} evidence</span>
      ${r.web_status === "confirmed_own_site" ? '<span class="pill web">✔ own website</span>' : ""}
      ${ai ? `<span class="pill ai">🤖 AI: ${esc(ai.replace("_", " "))}</span>` : ""}
      ${r.needs_review ? '<span class="pill warn">⚑ needs review</span>' : ""}</div>
    ${cite ? `<div class="quote"><small>${esc(cite.field)}</small>“${esc(cite.text)}”</div>` : ""}
    ${kind === "held" && r.guard_issues ? `<div class="reason">Held back: ${esc((r.cautions[0] || "").replace("Held back because ", ""))}</div>` : ""}
    <div class="foot"><span class="phone">${r.phone ? "📞 " + esc(String(r.phone).split(",")[0]) : "No phone listed"}</span><span>score ${(+r.score).toFixed(2)}</span></div>
    ${kind === "rec" ? scoreBar(r.components || {}) : ""}
  </div>`;
}

function renderResults() {
  const R = S.result, el = $("#results");
  if (!R) { el.innerHTML = ""; return; }
  const recs = R.recommendations, held = R.held, leads = R.leads, cv = R.coverage, top = recs[0];
  const warns = [...(R.location.warnings || []), ...R.notes.filter((n) => n !== R.abstain_reason && !n.startsWith("Distances are straight"))];
  let h = `<div class="step-label">${esc(R.service.label)} near ${esc(R.location.label)}</div>`;
  if (top) {
    h += `<div class="banner best"><span class="t">Best option: ${esc(top.name)}</span>${esc(fmtKm(top.distance_km))} · ${esc(top.evidence_level)} evidence${top.web_status === "confirmed_own_site" ? " · ✔ confirmed on its own website" : ""}${top.phone ? " · 📞 " + esc(String(top.phone).split(",")[0]) : ""}<br><span style="color:var(--muted)">${esc(R.service.advice || "")} Always call ahead.</span></div>`;
  } else if (R.abstained) {
    h += `<div class="banner abstain"><span class="t">No recommendation</span>${esc(R.abstain_reason)}</div>`;
  }
  warns.slice(0, 3).forEach((w) => (h += `<div class="banner warn">⚠ ${esc(w)}</div>`));
  h += `<div class="metrics"><div class="metric"><b style="color:var(--strong)">${cv.n_strong}</b><span>Strong</span></div><div class="metric"><b style="color:var(--moderate)">${cv.n_moderate}</b><span>Moderate</span></div>
    <div class="metric"><b style="color:var(--held)">${held.length}</b><span>Held</span></div><div class="metric"><b>${Math.round(cv.radius_km)}</b><span>km radius</span></div></div>`;
  const tabs = [["rec", `Recommended ${recs.length}`], ["held", `Held ${held.length}`], ["lead", `Leads ${leads.length}`], ["ctx", "District"], ["how", "How we decided"]];
  h += `<div class="subtabs">${tabs.map(([k, l]) => `<button class="subtab ${S.tab === k ? "sel" : ""}" data-t="${k}">${l}</button>`).join("")}</div><div id="tabBody"></div>`;
  el.innerHTML = h;
  $$(".subtab", el).forEach((b) => (b.onclick = () => { S.tab = b.dataset.t; renderResults(); }));
  const tb = $("#tabBody");
  if (S.tab === "rec") tb.innerHTML = recs.length ? recs.map((r) => cardHTML(r, "rec")).join("") : `<div class="empty">Nothing passed every check here – see Held / Leads.</div>`;
  if (S.tab === "held") tb.innerHTML = `<div class="hint" style="margin-bottom:8px">Mention the service clearly but failed a safety check. Not recommended – but worth a call if nothing else is reachable.</div>` + (held.map((r) => cardHTML(r, "held")).join("") || `<div class="empty">Nothing held back.</div>`);
  if (S.tab === "lead") tb.innerHTML = `<div class="hint" style="margin-bottom:8px">Only related terms (e.g. a department). The service itself is not named.</div>` + (leads.map((r) => cardHTML(r, "lead")).join("") || `<div class="empty">No leads.</div>`);
  if (S.tab === "ctx") tb.innerHTML = nfhsHTML(R.nfhs);
  if (S.tab === "how") tb.innerHTML = howHTML(R);
  $$(".card", tb).forEach((c) => (c.onclick = () => {
    const list = c.dataset.kind === "rec" ? recs : c.dataset.kind === "held" ? held : leads;
    const r = list.find((x) => x.facility_id === c.dataset.id); if (r) openDetail(r, c.dataset.kind);
  }));
}

function nfhsHTML(c) {
  if (!c || !c.ok) return `<div class="empty">${esc((c && c.note) || "No district data.")}</div>`;
  return `<div class="hint" style="margin-bottom:10px">${esc(c.note)} These describe the district population, not any facility.</div><div class="bars">` +
    c.rows.map((r) => {
      const v = r.value ?? 0, s = r.state_avg ?? 0, mx = Math.max(100, v, s);
      return `<div style="margin:10px 0"><div style="font-size:12.3px;margin-bottom:4px">${esc(r.indicator)}</div>
        <div class="bar" style="grid-template-columns:70px 1fr 48px;margin:3px 0"><span class="mono">District</span><div class="track"><div class="fill" style="width:${(v / mx) * 100}%;background:var(--accent)"></div></div><b>${r.value ?? "–"}</b></div>
        <div class="bar" style="grid-template-columns:70px 1fr 48px;margin:3px 0"><span class="mono">State avg</span><div class="track"><div class="fill" style="width:${(s / mx) * 100}%;background:var(--accent-2)"></div></div><b>${r.state_avg ?? "–"}</b></div>
        ${r.caution ? `<div class="hint" style="color:var(--moderate)">${esc(r.caution)}</div>` : ""}</div>`;
    }).join("") + "</div>";
}

function howHTML(R) {
  const maxN = Math.max(1, ...R.funnel.map((f) => f.n));
  const cal = R.calibration || {};
  const sv = R.sensitivity || {};
  const w = R.weights;
  return `<div class="sec"><h4>Decision funnel</h4><div class="funnel">${R.funnel.map((f) =>
      `<div class="f"><div class="fb" style="width:${Math.max(8, (f.n / maxN) * 100)}%">${esc(f.step)}</div><b>${f.n}</b></div>`).join("")}</div></div>
    <div class="sec" style="margin-top:14px"><h4>Score</h4><div class="hint">${(w.evidence).toFixed(2)}×evidence + ${(w.proximity).toFixed(2)}×closeness (halves every ${R.service.half_life_km} km for ${esc(R.service.urgency)} care) + ${(w.trust).toFixed(2)}×record quality + ${(w.scale).toFixed(2)}×size. Safety mode ${R.guarded ? "on" : "off"}.</div></div>
    ${sv.n_compared ? `<div class="sec" style="margin-top:14px"><h4>Equal-weight check</h4><div class="hint">${sv.top1_same ? "✔ With all factors weighted equally, the #1 option stays the same." : `With equal weights, #1 would become <b>${esc(sv.alt_top1)}</b>.`}</div></div>` : ""}
    <div class="sec" style="margin-top:14px"><h4>Does the evidence level mean anything?</h4><div class="bars">${Object.entries(cal).map(([k, v]) =>
      `<div class="bar"><span>${esc(k)}</span><div class="track"><div class="fill" style="width:${v.pct}%;background:${css(COLORS[k] || COLORS.Indirect)}"></div></div><b>${Math.round(v.pct)}%</b></div>`).join("")}</div>
      <div class="hint">Share of claims confirmed on the hospital's own website (${Object.values(cal).reduce((a, v) => a + v.n, 0).toLocaleString()} checked). A lower bound – websites are often incomplete.</div></div>`;
}

// ----------------------------------------------------------------------------- detail drawer
function openDetail(r, kind) {
  S.activeId = r.facility_id; renderResults(); render();
  if (r.lat != null) map.easeTo({ center: [r.lon, r.lat], zoom: Math.max(map.getZoom(), 12), duration: 1200 });
  const c = r.components || {};
  const bars = [["Evidence", c.evidence, "#3fd07a", `${(+r.evidence_adj).toFixed(2)} raw`], ["Closeness", c.proximity, "#60a5fa", `${(+r.proximity).toFixed(2)} · ${fmtKm(r.distance_km)}`],
    ["Record quality", c.trust, "#a78bfa", `${(+r.trust_score).toFixed(2)} raw`], ["Size", c.scale, "#f2c14e", esc(r.scale_text || "")]];
  const ai = r.ai_reader && r.ai_reader.verdict && r.ai_reader.verdict !== "unverified" ? r.ai_reader : null;
  $("#drawerBody").innerHTML = `
    <h2>${kind === "rec" ? `#${r.rank} · ` : ""}${esc(r.name)}</h2>
    <div class="pills"><span class="pill lvl-${esc(r.evidence_level)}">${esc(r.evidence_level)} evidence – ${esc(r.evidence_meaning)}</span>
      ${r.web_status === "confirmed_own_site" ? `<span class="pill web">✔ Confirmed on its own website</span>` : ""}
      ${ai ? `<span class="pill ai">🤖 AI reader: ${esc(ai.verdict.replace("_", " "))}</span>` : ""}
      ${kind === "held" ? '<span class="pill warn">Held for review</span>' : ""}</div>
    <div class="hint">📍 ${esc(r.address || "")} · ${esc(fmtKm(r.distance_km))} (${esc(r.location_basis)}) ${r.phone ? " · 📞 " + esc(r.phone) : ""}</div>
    <div class="grid2">
      <div>
        <div class="sec"><h4>Why this facility – quotes from the record</h4>
          ${(r.citations || []).map((q) => `<div class="quote"><small>${esc(q.field)} · ${esc(q.kind)}</small>“${esc(q.text)}”${q.url ? ` <a href="${esc(q.url)}" target="_blank" rel="noopener">source ↗</a>` : ""}</div>`).join("") || '<div class="hint">No direct quotes.</div>'}
          ${ai && ai.quote ? `<div class="quote" style="border-color:var(--accent-2)"><small>AI reader (Llama 3.3, verbatim-checked)</small>“${esc(ai.quote.replace(/^-?\s*\[[a-z_]+\]\s*/i, ""))}” ${ai.reason ? "– " + esc(ai.reason) : ""}</div>` : ""}
        </div>
        ${(r.cautions || []).length ? `<div class="sec" style="margin-top:12px"><h4>Be careful</h4>${r.cautions.map((x) => `<div class="caution">${esc(x)}</div>`).join("")}</div>` : ""}
        ${(r.ignored_mentions || []).length ? `<div class="sec" style="margin-top:12px"><h4>Mentions we ignored (negated / planned / referral)</h4>${r.ignored_mentions.map((m) => `<div class="quote" style="border-color:var(--danger)"><small>${esc(m.field)}</small>“${esc(m.text)}”</div>`).join("")}</div>` : ""}
      </div>
      <div>
        ${r.evidence_adj == null ? `<div class="sec"><h4>Score</h4><div class="hint">Found by the assistant – run the same search in the panel for the full score breakdown.</div></div>` : `<div class="sec"><h4>How the score was computed</h4><div class="bars">${bars.map(([n, v, col, raw]) =>
          `<div class="bar"><span>${n}</span><div class="track"><div class="fill" style="width:${Math.min(100, (v || 0) * 220)}%;background:${col}"></div></div><b>${(+(v || 0)).toFixed(2)}</b></div><div class="mono" style="margin:-4px 0 4px 130px">${raw}</div>`).join("")}</div>
          <div class="hint">Total <b style="color:var(--text)">${(+r.score).toFixed(2)}</b>${r.bonus_24x7 ? ` (incl. +${r.bonus_24x7} for 24x7 emergency)` : ""}${r.vs_next ? "<br>↕ " + esc(r.vs_next) : ""}</div></div>`}
        ${r.web_url ? `<div class="sec" style="margin-top:12px"><h4>Website check</h4><a href="${esc(r.web_url)}" target="_blank" rel="noopener">${esc(r.web_url)}</a></div>` : ""}
        ${(r.sources || []).length ? `<div class="sec" style="margin-top:12px"><h4>Sources</h4>${r.sources.map((u) => `<div><a href="${esc(/^https?:/.test(u) ? u : "https://" + u)}" target="_blank" rel="noopener">${esc(u.slice(0, 70))}</a></div>`).join("")}</div>` : ""}
        <div class="sec" style="margin-top:12px"><h4>Audit</h4><div class="mono">Record ID ${esc(r.facility_id)}</div></div>
      </div>
    </div>`;
  $("#drawer").classList.add("open");
}
$("#drawerClose").onclick = () => { $("#drawer").classList.remove("open"); S.activeId = null; renderResults(); render(); };

// ----------------------------------------------------------------------------- assistant
const EXAMPLES = ["My patient's kidneys have failed. She lives near Gaya in Bihar and can't travel far.",
  "Pregnant woman in obstructed labour at PIN 226001 – who can do an emergency C-section?",
  "Newborn born at 30 weeks, struggling to breathe, we are in Darbhanga.",
  "dialysis kahan milega Varanasi mein?"];
function setAssist(open) {
  $("#right").classList.toggle("closed", !open); document.body.classList.toggle("no-right", !open);
  $("#toggleAssist").classList.toggle("active", open); $("#toggleAssist").textContent = open ? "✕ Close assistant" : "✨ Ask the assistant";
  if (open) $("#hero").classList.add("gone"); else if (!S.result) $("#hero").classList.remove("gone");
  if (open && !S.chat.length) renderExamples();
}
$("#toggleAssist").onclick = () => setAssist($("#right").classList.contains("closed"));
document.body.classList.add("no-right");

function renderExamples() {
  $("#chatLog").innerHTML = `<div class="step-label">Try</div><div class="examples">${EXAMPLES.map((e) => `<button class="example">${esc(e)}</button>`).join("")}</div>`;
  $$(".example").forEach((b) => (b.onclick = () => sendChat(b.textContent)));
}
function lineHTML(ev) {
  if (ev.type === "tool") {
    const args = Object.entries(ev.args || {}).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(", ");
    return (ev.reason ? `<div class="ln"><span class="plan">🧠 ${esc(ev.reason)}</span></div>` : "") +
      `<div class="ln"><span class="call">${TOOL_ICON[ev.tool] || "🔧"} ${esc(ev.tool)}(${esc(args)})</span></div>` +
      `<div class="ln res">↳ ${esc(resultLine(ev.output || {}))} · model ${ev.llm_seconds ?? "–"}s · tool ${ev.seconds ?? "–"}s</div>`;
  }
  if (ev.type === "answer") {
    const g = ev.grounding;
    return g.ok ? `<div class="ln ok">✅ Grounding check passed – ${g.facilities_checked} facilities, ${g.quotes_checked} quotes matched tool results</div>`
      : `<div class="ln bad">❌ Grounding check failed – ${esc((g.issues || []).slice(0, 2).join("; "))}</div>`;
  }
  if (ev.type === "retry") return `<div class="ln">🔁 Asking the model to rewrite using only tool results</div>`;
  if (ev.type === "fallback") return `<div class="ln">🛟 Showing the checked summary of tool results instead</div>`;
  return "";
}
function resultLine(o) {
  if (o.recommendations) {
    const names = o.recommendations.slice(0, 3).map((r) => r.name).join(", ");
    return `${o.recommendations.length} ranked${o.held_for_review && o.held_for_review.length ? `, ${o.held_for_review.length} held` : ""}${names ? " – " + names : ""}${o.status === "abstained" ? " · abstained" : ""}`;
  }
  if (o.options) return "ambiguous place – " + o.options.slice(0, 4).join(", ");
  if (o.status === "missing_location") return "no location given yet";
  if (o.quotes) return `${o.evidence_level} evidence · ${o.quotes.length} quotes · website: ${o.website_check}`;
  if (o.decision_funnel) return "funnel " + o.decision_funnel.map((f) => f.facilities).join(" → ");
  if (o.rows) return `${o.rows.length} district indicators`;
  return o.label || o.error || o.status || "";
}

async function sendChat(text) {
  text = (text || "").trim(); if (!text || S.busy) return;
  if (!S.chat.length) $("#chatLog").innerHTML = "";
  S.busy = true; $("#send").disabled = true;
  S.chat.push({ role: "user", content: text });
  const log = $("#chatLog");
  log.insertAdjacentHTML("beforeend", `<div class="msg user">${esc(text)}</div>`);
  const bot = document.createElement("div"); bot.className = "msg bot";
  bot.innerHTML = `<details class="work" open><summary><span class="spin"></span><span class="lbl">🧠 Thinking…</span></summary><div class="lines"></div></details><div class="bubble" hidden></div>`;
  log.appendChild(bot);
  const lbl = $(".lbl", bot), lines = $(".lines", bot), work = $(".work", bot), bubble = $(".bubble", bot);
  const scroll = () => (log.parentElement.scrollTop = log.parentElement.scrollHeight);
  scroll();
  let nTools = 0;
  try {
    const resp = await fetch("/api/agent", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: S.chat, prior: S.prior, safety: true }) });
    const reader = resp.body.getReader(), dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
        if (!chunk.startsWith("data: ")) continue;
        const ev = JSON.parse(chunk.slice(6));
        if (ev.type === "thinking") lbl.textContent = "🧠 Thinking…";
        else if (ev.type === "tool_start") lbl.textContent = `${TOOL_ICON[ev.tool] || "🔧"} Running ${ev.tool}…`;
        else if (ev.type === "checking") lbl.textContent = "🔍 Checking the answer against tool results…";
        else if (ev.type === "final") {
          work.classList.add("done"); work.open = false;
          lbl.textContent = `Worked for ${ev.seconds}s · ${nTools} tool call${nTools === 1 ? "" : "s"} · ${ev.fallback ? "🛟 checked summary" : "✔ verified"}`;
          bubble.hidden = false; bubble.innerHTML = md(ev.answer) +
            `<div class="verified">${ev.fallback ? "🛟 Model answer failed the check – showing the verified summary" : `✔ Verified against tool results · ${ev.grounding.facilities_checked} facilities · ${ev.grounding.quotes_checked} quotes`}</div>`;
          S.chat.push({ role: "assistant", content: ev.answer });
          S.prior = [...S.prior, ...(ev.tool_outputs || [])].slice(-6);
          if (ev.map) showAgentMap(ev.map);
        } else if (ev.type === "error") {
          work.classList.add("done"); lbl.textContent = "⚠ Assistant unavailable";
          bubble.hidden = false; bubble.innerHTML = `<p>The assistant is unavailable right now – please use the search panel.</p><p class="mono">${esc(ev.error)}</p>`;
        } else {
          if (ev.type === "tool") nTools++;
          lines.insertAdjacentHTML("beforeend", lineHTML(ev));
        }
        scroll();
      }
    }
  } catch (e) {
    bubble.hidden = false; bubble.textContent = "Connection problem: " + e.message;
  } finally {
    S.busy = false; $("#send").disabled = false; scroll();
  }
}
function showAgentMap(o) {
  if (!o || o.location_lat == null) return;
  const recs = (o.recommendations || []).map((r) => ({ ...r, citations: r.key_quote ? [{ field: r.quote_field, text: r.key_quote }] : [],
    web_status: r.website_check, components: {}, cautions: r.cautions || [] }));
  S.result = { ok: true, location: { lat: o.location_lat, lon: o.location_lon, label: o.location }, recommendations: recs, held: [], leads: [],
    searched_radius_km: o.searched_radius_km || 50, __fromAgent: true };
  S.show.hex = false; $("#btnHex").classList.remove("on"); $("#hero").classList.add("gone");
  render(); flyTo(o.location_lon, o.location_lat, 10);
}
$("#send").onclick = () => { const t = $("#chatInput").value; $("#chatInput").value = ""; sendChat(t); };
$("#chatInput").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#send").click(); } });

init();
