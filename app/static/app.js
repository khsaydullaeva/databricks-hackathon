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
const COLORS = { Strong: [52, 193, 123], Moderate: [229, 181, 59], Limited: [232, 130, 58], Indirect: [138, 145, 156], held: [226, 109, 160], patient: [76, 141, 255] };
const css = (rgb, a = 1) => `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${a})`;
const FEATURED = ["dialysis", "emergency_csection", "nicu", "trauma", "cardiac_surgery", "mri", "burn_care", "icu"];
const TOOL_TEXT = { resolve_location: "Finding the place", search_facilities: "Searching facilities", get_facility_evidence: "Reading the record",
  district_health_context: "Reading district data", explain_ranking: "Checking the ranking" };

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
      if (p["icon-opacity"] !== undefined || l.layout?.["icon-image"]) p["icon-opacity"] = 0;
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
  try { map.setSky({ "sky-color": "#0a1222", "horizon-color": "#0a1222", "fog-color": "#0a1222", "sky-horizon-blend": 0.6, "horizon-fog-blend": 0.7, "fog-ground-blend": 0.35 }); } catch (e) { /* optional */ }
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
  return `<b>${esc(r.name)}</b><br>${esc(fmtKm(r.distance_km))} · ${esc(lvl)} evidence${r.web_status === "confirmed_own_site" ? " · Website ✓" : ""}` +
    (kind === "held" ? "<br><span style='color:var(--held)'>Held for review</span>" : "") +
    "";
}

function render() {
  $("#legend").hidden = !S.result;
  const layers = [];
  const p = patientPos();
  if (S.show.hex && S.hex.length) {
    layers.push(new deck.HexagonLayer({
      id: "coverage", data: S.hex, getPosition: (d) => [d[0], d[1]], getElevationWeight: (d) => d[2], getColorWeight: (d) => d[2],
      elevationAggregation: "SUM", colorAggregation: "SUM", radius: 38000, coverage: 0.88, extruded: true, elevationScale: 380,
      upperPercentile: 99, opacity: 0.78, pickable: true, material: { ambient: 0.55, diffuse: 0.7, shininess: 40 },
      colorRange: Array(6).fill(COLORS.patient),
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
      getLineColor: [...COLORS.patient, 90], lineWidthMinPixels: 1.5,
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
        getSourceColor: [...COLORS.patient, 210], getTargetColor: [...COLORS.patient, 210],
        getWidth: (r) => (r.rank === 1 ? 4.5 : 2.5), getHeight: 0.2, greatCircle: false,
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

function serviceTile(s) {
  const i = SERVICE_ICONS[s.id], on = s.id === S.service;
  const icon = i ? `<svg viewBox="${i.box}" aria-hidden="true">${i.body}</svg>` : "";
  return `<button class="svc ${on ? "sel" : ""}" data-id="${esc(s.id)}" aria-pressed="${on}" title="${esc(s.label)}"><span class="ic">${icon}</span><span>${esc(i ? i.label : s.label)}</span></button>`;
}
function renderServices() {
  const svcs = S.meta.services;
  const featured = FEATURED.map((id) => svcs.find((s) => s.id === id)).filter(Boolean);
  const rest = svcs.filter((s) => !FEATURED.includes(s.id));
  // When collapsed, still show the current choice if it is one of the other services.
  const shown = S.allServices ? rest : rest.filter((s) => s.id === S.service);
  $("#services").innerHTML = [...featured, ...shown].map(serviceTile).join("") +
    `<button class="svc-more" id="moreSvc" aria-expanded="${!!S.allServices}">${S.allServices ? "Show fewer" : `All ${svcs.length} services`}</button>`;
  $$("#services .svc").forEach((b) => (b.onclick = () => pickService(b.dataset.id)));
  $("#moreSvc").onclick = () => { S.allServices = !S.allServices; renderServices(); $("#moreSvc").focus({ preventScroll: true }); };
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
    b.innerHTML = `<input class="field" id="cityIn" list="cityList" placeholder="City, e.g. Patna, Bihar" autocomplete="off" value="${S.city != null ? esc(S.meta.cities[S.city].label) : ""}">
      <datalist id="cityList">${S.meta.cities.map((c) => `<option value="${esc(c.label)}">`).join("")}</datalist>
      <div class="hint" id="locHint"></div>`;
    $("#cityIn").oninput = (e) => {
      const c = S.meta.cities.find((x) => x.label.toLowerCase() === e.target.value.trim().toLowerCase());
      S.city = c ? c.i : null;
      $("#locHint").className = "hint" + (c ? " ok" : "");
      $("#locHint").textContent = c || !e.target.value ? "" : "Pick a city from the list.";
      validate();
    };
  } else if (S.mode === "district") {
    const states = Object.entries(S.meta.states).sort((a, b) => a[1].title.localeCompare(b[1].title));
    b.innerHTML = `<select class="field" id="stateIn"><option value="">State / UT…</option>${states.map(([k, v]) => `<option value="${esc(k)}" ${k === S.state ? "selected" : ""}>${esc(v.title)}</option>`).join("")}</select>
      <select class="field" id="distIn" ${S.state ? "" : "disabled"}><option value="">District…</option>${S.state ? S.meta.states[S.state].districts.map((d) =>
        `<option value="${esc(d.id)}" ${d.id === S.district ? "selected" : ""}>${esc(d.title)}</option>`).join("") : ""}</select>
      <div class="hint">Measured from the district centre.</div>`;
    $("#stateIn").onchange = (e) => { S.state = e.target.value || null; S.district = null; renderLoc(); validate(); };
    $("#distIn").onchange = (e) => { S.district = e.target.value || null; validate(); };
  } else if (S.mode === "pin") {
    b.innerHTML = `<input class="field" id="pinIn" inputmode="numeric" maxlength="6" placeholder="6-digit PIN code, e.g. 800001" value="${esc(S.pin)}">
      <div class="hint" id="locHint"></div>`;
    $("#pinIn").oninput = (e) => {
      S.pin = e.target.value.replace(/\D/g, "").slice(0, 6); e.target.value = S.pin;
      const ok = /^[1-9]\d{5}$/.test(S.pin);
      $("#locHint").className = "hint" + (S.pin.length === 6 ? (ok ? " ok" : " err") : "");
      $("#locHint").textContent = S.pin.length < 6 || ok ? "" : "A PIN has 6 digits and doesn't start with 0.";
      validate();
    };
  } else {
    b.innerHTML = `<div class="row"><input class="field" id="latIn" type="number" step="0.0001" placeholder="Latitude" value="${S.point ? S.point.lat : ""}">
      <input class="field" id="lonIn" type="number" step="0.0001" placeholder="Longitude" value="${S.point ? S.point.lon : ""}"></div>
      <div class="hint ${S.point ? "ok" : ""}">${S.point ? "Pin placed." : "Click the map to drop a pin, or type coordinates."}</div>`;
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
  $("#customNote").textContent = "Custom ranking: " + diff.join(", ");
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
    S.result = R; S.tab = "rec"; S.activeId = null; $("#drawer").classList.remove("open");
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
const LEVEL_LABEL = { Strong: "Strong evidence", Moderate: "Moderate evidence", Limited: "Limited evidence", Indirect: "Indirect only" };
const dotColor = (r, kind) => css(kind === "held" ? COLORS.held : kind === "lead" ? COLORS.Indirect : COLORS[r.evidence_level] || COLORS.Indirect);
const firstPhone = (p) => (p ? String(p).split(",")[0].trim() : "");
const bestQuote = (r) => {
  const cs = r.citations || [];
  return cs.find((c) => c.kind === "names the service") || cs[0] || null;
};

// Plain short wording for the first view. The full sentence stays under "All evidence and sources".
const SHORT = [
  [/^Held back because the service is only/, "Service only weakly mentioned"],
  [/^Held back because the quote/, "Quote may describe another facility"],
  [/^Held back because an analyst/, "On hold after analyst review"],
  [/^Held back because the record/, "Record contradicts itself"],
  [/^Coordinates are/, "Location may be wrong"],
  [/^Analyst review: its coordinates/, "Location may be wrong"],
  [/^No (usable|valid) coordinates/, "Exact location unknown"],
  [/share the exact same coordinates/, "Location is approximate"],
  [/^Record says state/, "State and PIN don't match"],
  [/^Record text describes another facility/, "May be mixed up with another facility"],
  [/^Record text mentions postcodes/, "May combine several sites"],
  [/^The quote that names the service/, "Quote may describe another facility"],
  [/^Claims \d+ of/, "Lists unusually many services"],
  [/^No source URL/, "No source link"],
  [/^Its own website was checked/, "Not mentioned on its website"],
  [/near-identical record/, "Duplicate records merged"],
  [/^Listed as a/, "Small facility for this service: may refer on"],
  [/^Only \d+ beds/, "Few beds for this service"],
  [/^The record never names/, "Service not named directly"],
  [/^Analyst review/, "Flagged in analyst review"],
  [/^AI reader:/, "AI check doubts this claim"],
];
const short = (text) => (SHORT.find(([re]) => re.test(text)) || [null, text])[1];
const shortList = (list) => [...new Set((list || []).map(short))];
const shortNote = (t) => {
  let m;
  if ((m = t.match(/widened to (\d+) km/))) return `Search widened to ${m[1]} km.`;
  if ((m = t.match(/approximate \(±(\d+) km\)/))) return `Location is approximate (±${m[1]} km).`;
  if ((m = t.match(/^PIN \d+ spans .*\(also: (.+)\)\. Using the most common: (.+)\.$/))) return `PIN also covers ${m[1].replace(/ \([^)]*\)/g, "")}. Using ${m[2]}.`;
  if (/district centre/.test(t)) return "";
  return t;
};

// 'Record contradicts itself' says little: name the contradiction when the record has one.
function heldReason(r) {
  const first = short((r.cautions || [])[0] || "");
  return first === "Record contradicts itself" && r.cautions[1] ? short(r.cautions[1]) : first;
}

function cardHTML(r, kind) {
  const q = bestQuote(r);
  const nCaution = shortList(r.cautions).length;
  return `<div class="card ${r.facility_id === S.activeId ? "active" : ""}" data-id="${esc(r.facility_id)}" data-kind="${kind}">
    <div class="top"><span class="rank">${kind === "rec" ? r.rank : ""}</span><div style="min-width:0">
      <h3>${esc(r.name)}</h3>
      <div class="meta"><span class="dot" style="background:${dotColor(r, kind)}"></span>${esc(LEVEL_LABEL[r.evidence_level] || r.evidence_level)}
        <span class="sep">·</span>${esc(fmtKm(r.distance_km))}
        ${r.web_status === "confirmed_own_site" ? '<span class="sep">·</span><span class="ok-web">Website ✓</span>' : ""}
        ${kind === "rec" && nCaution ? `<span class="sep">·</span><span class="warn-ic">${nCaution} caution${nCaution > 1 ? "s" : ""}</span>` : ""}</div>
      ${kind === "held" ? `<div class="reason">${esc(heldReason(r))}</div>` : q ? `<div class="q">“${esc(q.text)}”</div>` : ""}
      ${kind === "rec" && r.phone ? `<div class="ph">${esc(firstPhone(r.phone))}</div>` : ""}
    </div></div></div>`;
}

function renderResults() {
  const R = S.result, el = $("#results");
  if (!R || R.__fromAgent) { el.hidden = true; el.innerHTML = ""; return; }
  el.hidden = false;
  const recs = R.recommendations, held = R.held, leads = R.leads;
  const warns = [...new Set([...(R.location.warnings || []), ...R.notes.filter((n) => n !== R.abstain_reason && !n.startsWith("Distances are straight"))].map(shortNote).filter(Boolean))];
  let h = `<div class="res-title"><b>${esc(R.service.label.split(" (")[0])}</b> near ${esc(R.location.label)}</div>`;
  if (R.abstained) h += `<div class="note abstain">${esc(R.abstain_reason)}</div>`;
  if (warns.length) h += `<div class="note">${warns.map(esc).join("<br>")}</div>`;
  const tabs = [["rec", `Options ${recs.length}`], ["held", `Held ${held.length}`]];
  if (leads.length) tabs.push(["lead", `Unverified ${leads.length}`]);
  tabs.push(["ctx", "District"], ["how", "Method"]);
  h += `<div class="subtabs">${tabs.map(([k, l]) => `<button class="subtab ${S.tab === k ? "sel" : ""}" data-t="${k}">${l}</button>`).join("")}</div><div id="tabBody"></div>`;
  el.innerHTML = h;
  $$(".subtab", el).forEach((b) => (b.onclick = () => { S.tab = b.dataset.t; renderResults(); }));
  const tb = $("#tabBody");
  if (S.tab === "rec") tb.innerHTML = recs.length ? recs.map((r) => cardHTML(r, "rec")).join("") : `<div class="empty">No facility passed every check. See Held.</div>`;
  if (S.tab === "held") tb.innerHTML = held.length ? held.map((r) => cardHTML(r, "held")).join("") : `<div class="empty">Nothing held back.</div>`;
  if (S.tab === "lead") tb.innerHTML = `<div class="hint" style="margin-bottom:8px">Mention related terms only. Call to confirm.</div>` + leads.map((r) => cardHTML(r, "lead")).join("");
  if (S.tab === "ctx") tb.innerHTML = nfhsHTML(R.nfhs);
  if (S.tab === "how") tb.innerHTML = howHTML(R);
  $$(".card", tb).forEach((c) => (c.onclick = () => {
    const list = c.dataset.kind === "rec" ? recs : c.dataset.kind === "held" ? held : leads;
    const r = list.find((x) => x.facility_id === c.dataset.id); if (r) openDetail(r, c.dataset.kind);
  }));
}

function nfhsHTML(c) {
  if (!c || !c.ok) return `<div class="empty">${esc((c && c.note) || "No district data.")}</div>`;
  return `<div class="hint" style="margin-bottom:8px">${esc(c.district)}, ${esc(c.state)} · NFHS-5 (2019–21)</div><div class="kv">` +
    c.rows.map((r) => `<span>${esc(r.indicator)}${r.caution ? " *" : ""}</span><b>${r.value ?? "–"}${r.state_avg != null ? ` <span class="hint">(state ${r.state_avg})</span>` : ""}</b>`).join("") +
    `</div>${c.rows.some((r) => r.caution) ? '<div class="hint">* small sample or not reported</div>' : ""}`;
}

function howHTML(R) {
  const w = R.weights, cal = R.calibration || {}, sv = R.sensitivity || {};
  return `<div class="hint" style="margin-bottom:10px">Score = ${w.evidence.toFixed(2)} evidence + ${w.proximity.toFixed(2)} closeness + ${w.trust.toFixed(2)} record quality + ${w.scale.toFixed(2)} size. Closeness halves every ${R.service.half_life_km} km.</div>
    <div class="funnel">${R.funnel.map((f) => `<div class="f"><span>${esc(f.step.replace("…", ""))}</span><b>${f.n}</b></div>`).join("")}</div>
    ${sv.n_compared ? `<div class="hint" style="margin-top:10px">${sv.top1_same ? "Equal weights give the same #1." : `Equal weights would put ${esc(sv.alt_top1)} first.`}</div>` : ""}
    <div class="sheet-sec"><h4>Claims found on hospital websites</h4><div class="hint" style="margin-bottom:8px">Not independent proof: most of these sites were also sources for the records.</div><div class="kv">${Object.entries(cal).map(([k, v]) => `<span>${esc(k)}</span><b>${Math.round(v.pct)}%</b>`).join("")}</div></div>`;
}

// ----------------------------------------------------------------------------- place sheet
function openDetail(r, kind) {
  S.activeId = r.facility_id; renderResults(); render();
  if (r.lat != null) map.easeTo({ center: [r.lon, r.lat], zoom: Math.max(map.getZoom(), 10.8), offset: window.innerWidth > 1200 ? [220, 0] : [0, 0], duration: NOFLY ? 0 : 900 });
  const phone = firstPhone(r.phone);
  const site = r.website || (r.sources || [])[0];
  const siteUrl = site ? (/^https?:/.test(site) ? site : "https://" + site) : null;
  const dir = r.lat != null ? `https://www.google.com/maps/dir/?api=1&destination=${r.lat},${r.lon}` : null;
  const why = bestQuote(r);
  const cautions = shortList(kind === "held" ? (r.cautions || []).slice(1) : r.cautions);
  const ai = r.ai_reader && r.ai_reader.verdict && r.ai_reader.verdict !== "unverified" ? r.ai_reader : null;
  const c = r.components || {};
  $("#drawerBody").innerHTML = `
    <h2>${esc(r.name)}</h2>
    <div class="meta"><span class="dot" style="background:${dotColor(r, kind)}"></span>${esc(LEVEL_LABEL[r.evidence_level] || r.evidence_level)}
      <span class="sep">·</span>${esc(fmtKm(r.distance_km))}${r.location_basis && r.location_basis !== "facility coordinates" ? " (approx.)" : ""}
      ${r.web_status === "confirmed_own_site" ? '<span class="sep">·</span><span class="ok-web">Website ✓</span>' : ""}</div>
    ${kind === "held" ? `<div class="note" style="margin-top:10px;border-left:3px solid var(--held)">Not recommended: ${esc(heldReason(r))}</div>` : ""}
    <div class="actions">
      <a class="act call ${phone ? "" : "disabled"}" href="${phone ? "tel:" + esc(phone.replace(/\s/g, "")) : "#"}"><span class="i">☎</span>Call</a>
      <a class="act dir ${dir ? "" : "disabled"}" href="${esc(dir || "#")}" target="_blank" rel="noopener"><span class="i">➚</span>Directions</a>
      <a class="act web ${siteUrl ? "" : "disabled"}" href="${esc(siteUrl || "#")}" target="_blank" rel="noopener"><span class="i">⌂</span>Website</a>
    </div>
    <p class="call-first">Call before travelling. Distance is straight-line.</p>
    ${why ? `<div class="sheet-sec"><h4>Why it's listed</h4><div class="quote">“${esc(why.text)}”<small>${esc(why.field)}</small></div></div>` : ""}
    ${cautions.length ? `<div class="sheet-sec"><h4>Before you go</h4>${cautions.map((x) => `<div class="caution">${esc(x)}</div>`).join("")}</div>` : ""}
    ${r.evidence_adj != null ? `<details class="more"><summary>Score ${(+r.score).toFixed(2)}</summary>
      <div class="kv"><span>Evidence</span><b>${(+c.evidence).toFixed(2)}</b><span>Closeness (${esc(fmtKm(r.distance_km))})</span><b>${(+c.proximity).toFixed(2)}</b>
        <span>Record quality</span><b>${(+c.trust).toFixed(2)}</b><span>Size (${esc(r.scale_text || "")})</span><b>${(+c.scale).toFixed(2)}</b></div>
      ${r.vs_next ? `<div class="hint" style="padding-bottom:8px">${esc(r.vs_next)}</div>` : ""}</details>` : ""}
    <details class="more"><summary>All evidence and sources</summary>
      ${(r.citations || []).map((q) => `<div class="quote"><small>${esc(q.field)} · ${esc(q.kind)}</small>“${esc(q.text)}”${q.url ? ` <a href="${esc(q.url)}" target="_blank" rel="noopener">source</a>` : ""}</div>`).join("")}
      ${ai ? `<div class="quote"><small>AI reader: ${esc(ai.verdict.replace("_", " "))}</small>${ai.quote ? "“" + esc(ai.quote.replace(/^-?\s*\[[a-z_]+\]\s*/i, "")) + "”" : ""}</div>` : ""}
      ${(r.cautions || []).map((x) => `<div class="caution">${esc(x)}</div>`).join("")}
      ${(r.ignored_mentions || []).map((m) => `<div class="quote"><small>Ignored (${esc(m.field)})</small>“${esc(m.text)}”</div>`).join("")}
      ${(r.sources || []).map((u) => `<div style="font-size:12.5px;padding:2px 0"><a href="${esc(/^https?:/.test(u) ? u : "https://" + u)}" target="_blank" rel="noopener">${esc(u.replace(/^https?:\/\//, "").slice(0, 60))}</a></div>`).join("")}
      ${r.address ? `<div class="hint" style="padding:6px 0">${esc(r.address)}</div>` : ""}
      <div class="mono" style="padding:4px 0 10px">Record ${esc(r.facility_id)}</div>
    </details>`;
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
  $("#toggleAssist").classList.toggle("active", open); $("#toggleAssist").textContent = open ? "Close assistant" : "Ask the assistant";
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
    return (ev.reason ? `<div class="ln"><span class="plan">${esc(ev.reason)}</span></div>` : "") +
      `<div class="ln res">${esc(TOOL_TEXT[ev.tool] || ev.tool)}: ${esc(resultLine(ev.output || {}))}</div>`;
  }
  if (ev.type === "answer") {
    const g = ev.grounding;
    return g.ok ? `<div class="ln">${esc(checkText(g, false))}</div>`
      : `<div class="ln bad">Check failed: ${esc((g.issues || []).slice(0, 2).join("; "))}</div>`;
  }
  if (ev.type === "retry") return `<div class="ln">Rewriting the answer</div>`;
  if (ev.type === "fallback") return `<div class="ln">Showing search results instead</div>`;
  return "";
}
// Say only what the check did: it matches quotes, known facility names and distances. Nothing else is checked.
function checkText(g, fallback) {
  if (fallback) return "Search results shown as returned";
  if (!g || !g.ok) return "Not checked against the data";
  return g.quotes_checked || g.facilities_checked ? "Names and quotes matched to the data" : "No facility claims to check";
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
  bot.innerHTML = `<details class="work" open><summary><span class="spin"></span><span class="lbl">Thinking…</span></summary><div class="lines"></div></details><div class="bubble" hidden></div>`;
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
        if (ev.type === "thinking") lbl.textContent = "Thinking…";
        else if (ev.type === "tool_start") lbl.textContent = `${TOOL_TEXT[ev.tool] || "Working"}…`;
        else if (ev.type === "checking") lbl.textContent = "Checking the answer…";
        else if (ev.type === "final") {
          work.classList.add("done"); work.open = false;
          lbl.textContent = `${nTools} step${nTools === 1 ? "" : "s"} · ${Math.round(ev.seconds)} s`;
          bubble.hidden = false;
          bubble.innerHTML = md(ev.answer) + `<div class="verified">${esc(checkText(ev.grounding, ev.fallback))}</div>`;
          S.chat.push({ role: "assistant", content: ev.answer });
          S.prior = [...S.prior, ...(ev.tool_outputs || [])].slice(-6);
          if (ev.map) showAgentMap(ev.map);
        } else if (ev.type === "error") {
          work.classList.add("done"); lbl.textContent = "Assistant unavailable";
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
  const recs = (o.recommendations || []).map((r) => ({ ...r, citations: r.key_quote ? [{ field: r.quote_field, kind: "quote", text: r.key_quote }] : [],
    web_status: r.website_check, components: {}, cautions: r.cautions || [] }));
  S.result = { ok: true, location: { lat: o.location_lat, lon: o.location_lon, label: o.location }, recommendations: recs, held: [], leads: [],
    searched_radius_km: o.searched_radius_km || 50, __fromAgent: true };
  S.show.hex = false; $("#btnHex").classList.remove("on"); S.activeId = null; $("#drawer").classList.remove("open");
  renderResults(); render(); flyTo(o.location_lon, o.location_lat, 10);
}
$("#send").onclick = () => { const t = $("#chatInput").value; $("#chatInput").value = ""; sendChat(t); };
$("#chatInput").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#send").click(); } });

init();
