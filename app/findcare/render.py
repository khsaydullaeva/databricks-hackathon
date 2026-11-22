"""HTML rendering of results (used by the notebook front-end via displayHTML)."""

from __future__ import annotations

from html import escape

LEVEL_COLOR = {"Strong": "#1a7f37", "Moderate": "#9a6700", "Limited": "#bc4c00", "Indirect": "#6e7781", "None": "#6e7781"}


def _badge(level: str) -> str:
    c = LEVEL_COLOR.get(level, "#6e7781")
    return (f'<span style="background:{c};color:#fff;border-radius:10px;padding:2px 9px;font-size:12px;'
            f'font-weight:600">{escape(level)} evidence</span>')


WEB_TEXT = {
    "confirmed_own_site": ("#1a7f37", "✔ Confirmed on the hospital's own website"),
    "mentioned_group_site": ("#0969da", "Mentioned on the hospital group's website (may be another branch)"),
    "not_found_on_site": ("#6e7781", "✖ Not found on the hospital's website (sites are often incomplete)"),
    "site_unreachable": ("#6e7781", "Hospital website could not be reached"),
}


def _web(r: dict) -> str:
    out = ""
    if r.get("web_status") in WEB_TEXT:
        c, t = WEB_TEXT[r["web_status"]]
        link = f" · <a href='{escape(r['web_url'])}' target='_blank'>open page</a>" if r.get("web_url") else ""
        out += f"<div style='color:{c};font-weight:600;margin-bottom:4px'>{escape(t)}{link}</div>"
    if r.get("needs_review"):
        out += "<div style='color:#9a6700;font-weight:600;margin-bottom:4px'>⚑ Record needs review</div>"
    return out


def card(r: dict) -> str:
    rank = f"{r['rank']}. " if r["rank"] else ""
    cites = "".join(
        f"<li><b>{escape(c['field'])}</b> <span style='color:#666'>({escape(c['kind'])})</span>: “{escape(c['text'])}”"
        + (f" <a href='{escape(c['url'])}' target='_blank'>source</a>" if c.get("url") else "") + "</li>"
        for c in r["citations"])
    cautions = "".join(f"<li>{escape(c)}</li>" for c in r["cautions"])
    ignored = "".join(f"<li><b>{escape(i['field'])}</b>: “{escape(i['text'])}”</li>" for i in r["ignored_mentions"])
    comps = r["components"]
    breakdown = (f"evidence {comps['evidence']:.2f} + proximity {comps['proximity']:.2f} + record quality {comps['trust']:.2f} "
                 f"+ size {comps['scale']:.2f}" + (f" + 24x7 {r['bonus_24x7']:.2f}" if r["bonus_24x7"] else "") +
                 f" = <b>{r['score']:.2f}</b>")
    raw = (f"evidence {r['evidence_score']:.2f}" + (f" × plausibility {r['plausibility_factor']:.2f}" if r["plausibility_factor"] < 1 else "") +
           f"; proximity 0.5^({r['distance_km']:.0f}/{r['half_life_km']:.0f} km) = {r['proximity']:.2f}; trust {r['trust_score']:.2f}; size {r['scale']:.2f} ({escape(r['scale_text'])})")
    sources = " · ".join(f"<a href='{escape(u if u.startswith('http') else 'https://' + u)}' target='_blank'>{escape(u[:60])}</a>" for u in r["sources"])
    return f"""
<div style="border:1px solid #d0d7de;border-radius:10px;padding:14px 16px;margin:10px 0;font-family:system-ui,sans-serif">
  <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap">
    <div style="font-size:18px;font-weight:700">{escape(rank + r['name'])}</div>{_badge(r['evidence_level'])}
  </div>
  <div style="color:#444;margin:4px 0 8px">{r['distance_km']:.1f} km ({escape(r['location_basis'])}) · {escape(r['address'] or '')}
    {(' · 📞 ' + escape(r['phone'])) if r.get('phone') else ''}</div>
  {_web(r)}
  <div><b>Why:</b> {escape(r['why'])}</div>
  {'<div style="margin-top:6px"><b>Evidence from the record</b><ul style="margin:4px 0">' + cites + '</ul></div>' if cites else ''}
  {'<div style="background:#fff8c5;border-radius:6px;padding:6px 10px;margin-top:6px"><b>Cautions</b><ul style="margin:4px 0">' + cautions + '</ul></div>' if cautions else ''}
  {'<details style="margin-top:6px"><summary>Mentions we ignored</summary><ul>' + ignored + '</ul></details>' if ignored else ''}
  <details style="margin-top:6px"><summary>How this score was computed</summary>
    <div style="font-size:13px">{breakdown}<br>{raw}</div></details>
  {('<div style="font-size:13px;color:#555;margin-top:6px"><i>' + escape(r['vs_next']) + '</i></div>') if r.get('vs_next') else ''}
  {('<div style="font-size:12px;margin-top:6px">Sources: ' + sources + '</div>') if sources else '<div style="font-size:12px;margin-top:6px;color:#a40e26">No source URL recorded.</div>'}
</div>"""


def result_html(res, ctx: dict | None = None) -> str:
    loc = res.location
    parts = [f"<div style='font-family:system-ui,sans-serif'><h2 style='margin-bottom:4px'>{escape(res.service.label)} near {escape(loc.label)}</h2>"]
    for w in loc.warnings + res.notes:
        parts.append(f"<div style='color:#9a6700'>⚠ {escape(w)}</div>")
    c = res.coverage
    parts.append(f"<p>Within {c['radius_km']:.0f} km: <b>{c['n_strong']}</b> strong, <b>{c['n_moderate']}</b> moderate, "
                 f"{c['n_limited']} limited, {c['n_indirect']} indirect-only mentions (of {c['n_facilities_in_radius']} facilities in range).</p>")
    if res.service.advice:
        parts.append(f"<p>💡 {escape(res.service.advice)} <b>Always call ahead before travelling.</b></p>")
    parts += [card(r) for r in res.recommendations]
    if res.leads:
        parts.append("<h3>Unverified leads (indirect or weak evidence – call to confirm)</h3>")
        parts += [card(r) for r in res.leads]
    if ctx:
        if ctx.get("ok"):
            rows = "".join(f"<tr><td>{escape(r['indicator'])}</td><td>{'' if r['value'] is None else r['value']}</td>"
                           f"<td>{'' if r['state_avg'] is None else r['state_avg']}</td><td>{escape(r['caution'])}</td></tr>" for r in ctx["rows"])
            parts.append(f"<h3>District health context</h3><p>{escape(ctx['note'])}</p><table border=1 cellpadding=4 style='border-collapse:collapse;font-size:13px'>"
                         f"<tr><th>Indicator</th><th>District</th><th>State avg of districts</th><th>Caution</th></tr>{rows}</table>")
        else:
            parts.append(f"<p style='color:#666'>District health context: {escape(ctx.get('note', ''))}</p>")
    parts.append("</div>")
    return "\n".join(parts)
