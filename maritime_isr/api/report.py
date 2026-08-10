"""The one-click incident report — the last named piece of the M6 demo.

`CLAUDE.md` §0 defines done as: a non-engineer opens a map, finds last week's
dark vessels, clicks one, reads the plain-English reason, **and exports a
one-click incident report** — in under five minutes. Everything but the export
existed. This is the export.

**Why a self-contained HTML file rather than a PDF.** It opens in any browser,
prints to PDF from there in one keystroke, survives being emailed, and needs no
renderer on the server or the operator's laptop. Every style is inline and no
asset is fetched, so the file says the same thing on a machine with no network
as it does here — which for a document that exists to be forwarded is the whole
point.

**What this file is really for is being read by someone who was not here.** So
three things are structural rather than a matter of care at writing time:

  * **A scenario vessel is unmistakable.** A synthetic report carries a banner
    at the top and a repeat of it at the bottom. A generated dossier that gets
    forwarded and mistaken for a real one is the exact failure CLAUDE.md §4.6
    exists to prevent, and by the time it happens the label is out of our hands.
  * **Every determination names who made it.** GFW assessed the gaps; OFAC, the
    UN and the EU decided the designations; ours is the identity match between
    them. A report that says "dark vessel detected" where we detected nothing is
    an overclaim that travels.
  * **What we did NOT establish gets its own section**, because a reader
    reasonably assumes an omission is an absence of concern rather than an
    absence of capability.
"""
from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Optional

#: Fields of the provenance envelope worth showing a human. `source_ref` is
#: omitted from the summary table — it is per-row and unbounded — but it is in
#: the JSON form for anyone tracing a specific assertion back.
_PROV_NOTE = ("Every row behind this report carries its source, the time it was "
              "acquired and ingested, and the git revision of the code that "
              "processed it. Nothing here is asserted without that chain.")


def _esc(v) -> str:
    return html.escape("" if v is None else str(v))


def _na(v, suffix: str = "") -> str:
    if v is None or v == "":
        return '<span class="na">not available</span>'
    return _esc(v) + _esc(suffix)


def _fmt_ts(iso: Optional[str]) -> str:
    if not iso:
        return '<span class="na">not available</span>'
    return _esc(str(iso)[:19].replace("T", " ") + "Z")


def _num(v, digits: int = 1, suffix: str = "") -> str:
    if v is None:
        return '<span class="na">—</span>'
    try:
        f = float(v)
    except (TypeError, ValueError):
        return _esc(v)
    s = str(int(f)) if f == int(f) else f"{f:.{digits}f}"
    return _esc(s + suffix)


# --------------------------------------------------------------------------
# assembly
# --------------------------------------------------------------------------

def build_report(*, vessel: dict, finding: Optional[dict],
                 alerts: list[dict], stats: dict) -> dict:
    """Assemble the report payload. Pure — takes what the service already has.

    Kept separate from rendering so the JSON form and the HTML form cannot
    drift: both are this one dict, and a caveat added here appears in both.
    """
    current = vessel.get("current") or {}
    sanctions = vessel.get("sanctions") or []
    findings_only = [s for s in sanctions if s.get("is_finding")]
    gaps = vessel.get("gaps") or []
    flagged_gaps = [g for g in gaps
                    if (g.get("classification") or "").startswith("intentional")]

    sources = sorted({p.get("source_id") for p in
                      [vessel.get("prov") or {}]
                      + [s.get("prov") or {} for s in sanctions]
                      + [e.get("prov") or {} for e in
                         (vessel.get("port_calls") or []) + gaps
                         + (vessel.get("encounters") or [])]
                      if p.get("source_id")})
    versions = sorted({(vessel.get("prov") or {}).get("pipeline_version")}
                      - {None})

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "vessel": vessel,
        "finding": finding,
        "alerts": alerts,
        "is_synthetic": bool(vessel.get("is_synthetic")),
        "corpus_window": stats.get("corpus_window") or {},
        "provenance": {
            "sources": sources,
            "pipeline_versions": versions,
            "note": _PROV_NOTE,
        },
        "summary": {
            "sanctions_findings": len(findings_only),
            "sanctions_candidates": len(sanctions) - len(findings_only),
            "gfw_flagged_gaps": len(flagged_gaps),
            "port_calls": len(vessel.get("port_calls") or []),
            "encounters": len(vessel.get("encounters") or []),
            "gaps": len(gaps),
            "alerts": len(alerts),
        },
        "not_established": _not_established(vessel, finding, flagged_gaps),
    }


def _not_established(vessel: dict, finding: Optional[dict],
                     flagged_gaps: list) -> list[str]:
    """The section a reader will not think to ask for.

    An omission reads as "no concern here" unless it is named as "we cannot see
    this". Every line below is a capability boundary this system currently has,
    stated so the report cannot be read as a clean bill of health.
    """
    out = [
        "We did not detect a dark vessel. Our own dark-vessel detection "
        "requires satellite radar contacts matched against AIS tracks, and no "
        "such detection has been produced on real data.",
        "No claim is made about this vessel's cargo, its destination beyond "
        "the port calls listed, or the intent behind any behaviour shown.",
    ]
    if flagged_gaps:
        out.append(
            "The AIS gaps below are Global Fishing Watch's assessment that the "
            "transponder was switched off deliberately. We did not compute "
            "that and hold no receiver-coverage model at those positions, so "
            "we can neither confirm nor contradict it.")
    else:
        out.append(
            "No AIS gap on this vessel is assessed as intentional disabling. "
            "That is not evidence the vessel never went dark — outside "
            "demonstrated receiver coverage a silence cannot be attributed at "
            "all, and most of this area has no coverage model.")
    if finding and not (finding.get("sanctions_is_finding")):
        out.append("No sanctions designation was matched to this hull.")
    if not (vessel.get("port_calls") or vessel.get("encounters")):
        out.append(
            "No port calls or encounters are recorded for this vessel in the "
            "corpus window, which may mean it was not observed rather than "
            "that it did nothing.")
    return out


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

_CSS = """
:root{--ink:#16202a;--ink2:#4a5763;--line:#d7dde3;--bg:#fff;--accent:#1a5fb4;
--risk:#b0221b;--warn:#8a5a00;--warnbg:#fff6e0}
*{box-sizing:border-box}
body{margin:0;padding:32px 36px;background:var(--bg);color:var(--ink);
font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
max-width:900px}
h1{font-size:22px;margin:0 0 2px}
h2{font-size:14px;text-transform:uppercase;letter-spacing:.06em;color:var(--ink2);
margin:26px 0 8px;padding-bottom:5px;border-bottom:1px solid var(--line)}
p{margin:0 0 10px}
.sub{color:var(--ink2);font-size:12.5px;margin:0 0 18px}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12.5px}
.na{color:#94a0ab;font-style:italic}
.muted{color:var(--ink2)}
.synth{background:var(--warnbg);border:2px solid var(--warn);color:var(--warn);
padding:12px 14px;border-radius:5px;font-weight:600;margin:0 0 20px}
.headline{background:#f4f7fa;border-left:3px solid var(--accent);padding:12px 14px;
margin:0 0 16px;font-size:15px;line-height:1.5}
table{border-collapse:collapse;width:100%;margin:0 0 12px;font-size:13px}
th,td{text-align:left;padding:6px 9px;border-bottom:1px solid var(--line);
vertical-align:top}
th{font-weight:600;color:var(--ink2);font-size:11.5px;text-transform:uppercase;
letter-spacing:.04em}
td.num,th.num{text-align:right}
ul{margin:0 0 10px;padding-left:20px}
li{margin-bottom:5px}
.kv{display:grid;grid-template-columns:150px 1fr;gap:3px 14px;font-size:13px;
margin:0 0 12px}
.kv .k{color:var(--ink2)}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;
font-weight:600;letter-spacing:.03em;border:1px solid}
.b-risk{background:#fdf0ef;color:var(--risk);border-color:#f0c8c5}
.b-neutral{background:#f1f4f7;color:var(--ink2);border-color:var(--line)}
footer{margin-top:32px;padding-top:14px;border-top:1px solid var(--line);
color:var(--ink2);font-size:11.5px}
@media print{body{padding:0;max-width:none}h2{break-after:avoid}
table,ul{break-inside:avoid}}
"""


def render_html(rep: dict) -> str:
    v = rep["vessel"]
    cur = v.get("current") or {}
    f = rep.get("finding")
    name = cur.get("name") or v.get("id")
    syn = rep["is_synthetic"]

    banner = ""
    if syn:
        banner = ('<div class="synth">SCENARIO DATA — THIS IS NOT A REAL '
                  'VESSEL. Every figure in this report is generated by the '
                  'Maritime ISR scenario suite to exercise the pipeline. It is '
                  'not evidence about any ship, and no number here may be '
                  'quoted as a real one.</div>')

    parts = [
        banner,
        f"<h1>Incident report — {_esc(name)}</h1>",
        f'<p class="sub mono">{_esc(v.get("id"))} · generated '
        f'{_fmt_ts(rep["generated_at"])}</p>',
    ]

    if f:
        parts.append(f'<div class="headline">{_esc(f.get("headline"))}</div>')
    else:
        parts.append('<div class="headline">This vessel carries no finding. '
                     'The report below is its observed record in the corpus '
                     'window.</div>')

    # ---- identity ----
    parts.append("<h2>Vessel identity</h2>")
    parts.append('<div class="kv">'
                 f'<span class="k">Name</span><span>{_na(cur.get("name"))}</span>'
                 f'<span class="k">MMSI</span><span class="mono">{_na(cur.get("mmsi"))}</span>'
                 f'<span class="k">IMO</span><span class="mono">{_na(cur.get("imo"))}</span>'
                 f'<span class="k">Call sign</span><span class="mono">{_na(cur.get("call_sign"))}</span>'
                 f'<span class="k">Flag</span><span>{_na(cur.get("flag"))}</span>'
                 f'<span class="k">Type</span><span>{_na(cur.get("vessel_class"))}</span>'
                 f'<span class="k">Length</span><span>{_na(cur.get("length_m"), " m")}</span>'
                 '</div>')

    hist = v.get("identity_history") or []
    if len(hist) > 1:
        parts.append("<h2>Identity history</h2>")
        parts.append("<p class=\"muted\">Each row is a time-scoped assertion. A "
                     "closed interval means we stopped hearing that identity "
                     "inside the query window — not necessarily that it "
                     "changed.</p>")
        parts.append("<table><thead><tr><th>From</th><th>To</th><th>Name</th>"
                     "<th>MMSI</th><th>Flag</th><th>Superseded</th></tr></thead><tbody>")
        for iv in hist:
            parts.append(
                f"<tr><td class='mono'>{_fmt_ts(iv.get('valid_from'))}</td>"
                f"<td class='mono'>{_fmt_ts(iv.get('valid_to'))}</td>"
                f"<td>{_na(iv.get('name'))}</td>"
                f"<td class='mono'>{_na(iv.get('mmsi'))}</td>"
                f"<td>{_na(iv.get('flag'))}</td>"
                f"<td>{'yes' if iv.get('superseded') else 'no'}</td></tr>")
        parts.append("</tbody></table>")

    # ---- why it was flagged ----
    if f and f.get("basis"):
        parts.append("<h2>Why this vessel was flagged</h2>")
        parts.append("<ul>")
        for b in f["basis"]:
            parts.append(f"<li>{_esc(b.get('explanation'))} "
                         f"<span class='mono muted'>(+{_esc(b.get('weight'))})</span></li>")
        parts.append("</ul>")
        parts.append(f'<p class="muted">Priority {_esc(f.get("priority"))} is the '
                     f'sum of the signals above. It is an ordering, not a '
                     f'probability, and it is never quoted without them.</p>')

    # ---- GFW gap assessments ----
    gaps = (f or {}).get("dark_gaps") or []
    if gaps:
        parts.append("<h2>AIS gaps assessed by Global Fishing Watch</h2>")
        parts.append("<table><thead><tr><th>From</th><th>To</th>"
                     "<th class='num'>Hours</th><th class='num'>Distance</th>"
                     "<th class='num'>From shore</th></tr></thead><tbody>")
        for g in gaps:
            parts.append(
                f"<tr><td class='mono'>{_fmt_ts(g.get('start_time'))}</td>"
                f"<td class='mono'>{_fmt_ts(g.get('end_time'))}</td>"
                f"<td class='num mono'>{_num(g.get('duration_hours'))}</td>"
                f"<td class='num mono'>{_num(g.get('distance_km'), 1, ' km')}</td>"
                f"<td class='num mono'>{_num(g.get('distance_from_shore_km'), 0, ' km')}</td>"
                "</tr>")
        parts.append("</tbody></table>")
        parts.append('<p class="muted"><b>Attribution:</b> Global Fishing Watch '
                     'assessed these gaps as intentional AIS disabling. This is '
                     'their determination, reproduced here. We did not compute '
                     'it and hold no receiver-coverage model at these '
                     'positions.</p>')

    # ---- sanctions ----
    sanctions = v.get("sanctions") or []
    if sanctions:
        parts.append("<h2>Sanctions</h2>")
        parts.append("<table><thead><tr><th>Registry</th><th>Listed as</th>"
                     "<th>Programme</th><th>Designated</th><th>Matched on</th>"
                     "<th class='num'>Confidence</th><th>Status</th>"
                     "</tr></thead><tbody>")
        for s in sanctions:
            badge = ('<span class="badge b-risk">finding</span>'
                     if s.get("is_finding")
                     else '<span class="badge b-neutral">candidate</span>')
            listed = _na(s.get("ofac_name"))
            if s.get("listed_entity_type") == "organisation":
                listed += ' <span class="muted">(an organisation, not this hull)</span>'
            parts.append(
                f"<tr><td>{_na(s.get('registry') or 'OFAC')}</td>"
                f"<td>{listed}</td>"
                f"<td>{_na(s.get('ofac_program'))}</td>"
                f"<td class='mono'>{_fmt_ts(s.get('sanctions_as_of'))}</td>"
                f"<td>{_na(s.get('match_tier'))}</td>"
                f"<td class='num mono'>{_num(s.get('confidence'), 2)}</td>"
                f"<td>{badge}</td></tr>")
        parts.append("</tbody></table>")
        parts.append('<p class="muted"><b>Attribution:</b> the designating '
                     'authority decided who is sanctioned and Global Fishing '
                     'Watch observed the vessel. Our contribution is the '
                     'identity match between the two. A <b>candidate</b> is a '
                     'lead to verify, not an assertion: names change and '
                     'collide, and call signs are reassigned.</p>')

    # ---- observed behaviour ----
    parts.append("<h2>Observed behaviour in the corpus window</h2>")
    w = rep.get("corpus_window") or {}
    parts.append(f'<p class="muted">Window: {_fmt_ts(w.get("start"))} to '
                 f'{_fmt_ts(w.get("end"))}.</p>')
    s = rep["summary"]
    parts.append('<div class="kv">'
                 f'<span class="k">Port calls</span><span>{s["port_calls"]}</span>'
                 f'<span class="k">Encounters</span><span>{s["encounters"]}</span>'
                 f'<span class="k">AIS gaps</span><span>{s["gaps"]}</span>'
                 f'<span class="k">Alerts raised</span><span>{s["alerts"]}</span>'
                 '</div>')

    calls = v.get("port_calls") or []
    if calls:
        parts.append("<table><thead><tr><th>Port call</th><th>Arrived</th>"
                     "<th>Departed</th><th class='num'>Hours</th>"
                     "</tr></thead><tbody>")
        for c in calls[:40]:
            parts.append(
                f"<tr><td>{_na(c.get('place'))}</td>"
                f"<td class='mono'>{_fmt_ts(c.get('start_time'))}</td>"
                f"<td class='mono'>{_fmt_ts(c.get('end_time'))}</td>"
                f"<td class='num mono'>{_num(c.get('duration_hours'))}</td></tr>")
        parts.append("</tbody></table>")
        if len(calls) > 40:
            parts.append(f'<p class="muted">Showing the first 40 of '
                         f'{len(calls)} port calls.</p>')

    # ---- alerts ----
    if rep["alerts"]:
        parts.append("<h2>Alerts raised by this system</h2>")
        for a in rep["alerts"]:
            parts.append(f"<p><b>{_esc(a.get('anomaly_type'))}</b> — "
                         f"{_fmt_ts(a.get('ts'))}, confidence "
                         f"{_num(a.get('confidence'), 2)}, disposition "
                         f"{_esc(a.get('disposition'))}</p>")
            ev = a.get("evidence") or []
            if ev:
                parts.append("<ul>")
                for h in ev:
                    detail = h.get("detail") or ""
                    parts.append(
                        f"<li class='mono'>{_esc(h.get('edge'))}: "
                        f"{_esc(h.get('src'))} → {_esc(h.get('dst'))}"
                        + (f" — {_esc(detail)}" if detail else "") + "</li>")
                parts.append("</ul>")

    # ---- what this does not say ----
    parts.append("<h2>What this report does not establish</h2>")
    parts.append("<ul>")
    for line in rep["not_established"]:
        parts.append(f"<li>{_esc(line)}</li>")
    parts.append("</ul>")

    # ---- provenance ----
    parts.append("<h2>Provenance</h2>")
    p = rep["provenance"]
    parts.append(f'<p class="muted">{_esc(p["note"])}</p>')
    parts.append('<div class="kv">'
                 f'<span class="k">Sources</span><span class="mono">'
                 f'{_esc(", ".join(p["sources"]) or "—")}</span>'
                 f'<span class="k">Pipeline version</span><span class="mono">'
                 f'{_esc(", ".join(p["pipeline_versions"]) or "—")}</span>'
                 '</div>')

    parts.append("<footer>Maritime ISR — prototype. "
                 + ("Scenario data: nothing in this report describes a real "
                    "vessel. " if syn else "")
                 + "Determinations are attributed to their source above; where "
                   "no source is named, the assertion is ours and is scoped by "
                   "the section it appears in.</footer>")
    if syn:
        parts.append('<div class="synth" style="margin-top:18px">SCENARIO DATA '
                     '— NOT A REAL VESSEL.</div>')

    return (f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>Incident report — {_esc(name)}</title>"
            f"<style>{_CSS}</style></head><body>"
            + "".join(parts) + "</body></html>")


def filename_for(rep: dict) -> str:
    """A filename an operator can find again in a downloads folder."""
    cur = (rep["vessel"].get("current") or {})
    raw = (cur.get("name") or rep["vessel"].get("id") or "vessel")
    slug = "".join(ch if ch.isalnum() else "-" for ch in str(raw).lower())
    slug = "-".join(x for x in slug.split("-") if x)[:48] or "vessel"
    day = rep["generated_at"][:10]
    prefix = "SCENARIO-" if rep["is_synthetic"] else ""
    return f"{prefix}incident-report-{slug}-{day}.html"
