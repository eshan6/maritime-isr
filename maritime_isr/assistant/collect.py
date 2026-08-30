"""Turning what the system holds into factors on a subject.

One collector per source of suspicion. Each reads a store the system already
maintains — the object graph, the landed conformed tables — and emits
:class:`~.model.Factor` objects keyed to a subject. Nothing here computes a new
detection: this layer is assembly and attribution, and a collector that started
detecting would be a second, uncalibrated copy of a rule that already exists.

Two rules govern every collector:

* **Never read the answer key.** ``scenario_truth`` is ground truth and no
  serving path may touch it (ADR-019 §d). The reader does not even register the
  table; this module does not import it.
* **Carry attribution through.** Several of these facts are other people's
  findings — Global Fishing Watch assessed a gap, OFAC designated a hull. The
  sentence that reaches an operator has to say who asserted what, so
  ``catalog.FactorSpec.attribution`` travels onto the factor's evidence rather
  than being dropped at the boundary.

**Merging is where the workload reduction actually happens.** Raw signals arrive
one per event; a watchkeeper wants one line per subject per kind of thing.
:func:`merge_factors` collapses repeats of a kind into a single factor holding
every occurrence as evidence, and combines their confidences as independent
observations, capped so that a long tail of weak repeats cannot manufacture
certainty.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Optional, Sequence

from ..api.reader import Reader, as_iso, open_reader
from ..config import PIPELINE_VERSION
from ..schemas.keys import vessel_node_id
from .catalog import FACTOR_KINDS, REPEAT_RESTATEMENT, spec
from .model import Evidence, Factor, iso

__all__ = ["collect_all", "merge_factors", "SubjectMeta"]

#: At most this many occurrences of one kind combine into its confidence.
#:
#: Repeats are real evidence — a hull that has gone dark four times is worse
#: than one that has done it once — but noisy-OR over an unbounded list drives
#: any kind to certainty given enough weak members, which is how a detector with
#: a bad day takes over the whole queue. Three is the point past which the
#: fourth repeat tells an operator nothing they will act on differently; the
#: rest stay attached as evidence and are counted in `detail["occurrences"]`.
MERGE_CONFIDENCE_TOP_N = 3


class SubjectMeta:
    """What we can say about a subject beyond its factors.

    Held separately from :class:`~.model.Factor` because it is not evidence of
    anything — a name and a flag do not make a vessel suspicious — but the
    surface needs it to render a row a human can recognise.
    """

    __slots__ = ("subject_id", "kind", "name", "identifiers", "position",
                 "is_synthetic")

    def __init__(self, subject_id: str, kind: str, name: str,
                 identifiers: dict, position: dict, is_synthetic: bool):
        self.subject_id = subject_id
        self.kind = kind
        self.name = name
        self.identifiers = identifiers
        self.position = position
        self.is_synthetic = is_synthetic


# --------------------------------------------------------------------------
# provenance helpers
# --------------------------------------------------------------------------

def _prov_from_row(row: dict) -> dict:
    """The conformed envelope, verbatim. CLAUDE.md §4.1."""
    return {
        "source_id": row.get("source_id"),
        "source_ref": row.get("source_ref"),
        "acquired_at": as_iso(row.get("acquired_at")),
        "ingested_at": as_iso(row.get("ingested_at")),
        "pipeline_version": row.get("pipeline_version"),
        "confidence": row.get("confidence"),
    }


#: What each tracked identity field is called in a sentence. The populator's
#: column names are fine in a database and wrong in an accusation.
_IDENTITY_FIELD_LABEL = {
    "name": "ship name", "flag": "flag", "mmsi": "MMSI",
    "imo": "IMO number", "call_sign": "call sign",
}


def _identity_change_payload(payload) -> dict:
    """The event payload as a dict, whatever the store handed back.

    `emit` writes JSON text, but a caller reading through a different path may
    already hold the dict. Both are accepted rather than assuming one, because
    the failure mode of assuming is a label that reads `{"field": "flag"...`.
    """
    if isinstance(payload, dict):
        return payload
    try:
        import json
        loaded = json.loads(payload or "{}")
        return loaded if isinstance(loaded, dict) else {}
    except (ValueError, TypeError):
        return {}


def _identity_change_label(payload) -> str:
    """"flag changed from PAN to TON" — the fact, not the fact's category."""
    p = _identity_change_payload(payload)
    field = _IDENTITY_FIELD_LABEL.get(str(p.get("field") or ""),
                                      str(p.get("field") or "").replace("_", " "))
    old, new = p.get("old"), p.get("new")
    if field and old and new:
        return f"{field} changed from {old} to {new}"
    if field:
        return f"{field} changed on record"
    # A payload we cannot read is reported as unreadable rather than described
    # as a change we cannot substantiate.
    return "an identity field changed; the record does not say which"


def _identity_change_detail(payload) -> dict:
    p = _identity_change_payload(payload)
    return {"field": p.get("field"), "old": p.get("old"), "new": p.get("new")}


def _prov_from_edge(ev: dict) -> dict:
    """The envelope an alert's evidence chain carries.

    An alert's evidence is a list of graph-edge shaped dicts written by the
    detector that raised it, so `source` and `source_ref` are populated but the
    landed timestamps are not — the edge is a derived assertion, not a landed
    row. `pipeline_version` is filled from the running build rather than left
    blank, because "which code decided this" is the half of provenance an alert
    most needs and the detector does not stamp it.
    """
    return {
        "source_id": ev.get("source"),
        "source_ref": ev.get("source_ref"),
        "acquired_at": None,
        "ingested_at": None,
        "pipeline_version": PIPELINE_VERSION,
        "confidence": ev.get("confidence"),
    }


def _num(v) -> Optional[float]:
    """A float, or None. NaN counts as None.

    Parquet round-trips a missing numeric as NaN rather than null, and NaN
    propagates silently through every arithmetic operation downstream — a score
    that is NaN sorts unpredictably and renders as "NaN" on the surface.
    """
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


# --------------------------------------------------------------------------
# 1. the anomaly library, through the graph's alert table
# --------------------------------------------------------------------------

def collect_alert_factors(store) -> list[Factor]:
    """One factor per alert the anomaly library and zone analyses raised.

    Dismissed alerts are dropped outright, open ones stand at the confidence
    their detector stated, and analyst confirmation raises it — see the comment
    on ``disp`` below for why this deliberately differs from ``anomaly.risk``.
    An analyst who has looked at something and said "no" must not see it again
    on the same list: that is the feedback loop's whole purpose (roadmap 5.1),
    and a queue that ignores dispositions trains people to stop entering them.
    """
    out: list[Factor] = []
    for a in store.alerts():
        kind = a.get("anomaly_type") or a.get("rule")
        if kind not in FACTOR_KINDS:
            # An unregistered detector is a real event, not a silent skip: it
            # means someone added a rule and did not give it a weight, and the
            # subject would sit on the list scoring nothing.
            continue
        if a.get("disposition") == "dismiss":
            continue
        base = _num(a.get("score")) or _num(a.get("confidence")) or 0.0
        # **An open alert is taken at the confidence its detector stated.**
        #
        # `anomaly.risk` discounts unreviewed alerts to 0.6 of their score, and
        # copying that here was wrong in a way that only showed up once the
        # ranked list existed: the discount is applied to alert-derived factors
        # and *not* to registry-derived ones, so a blanket 40% haircut fell on
        # exactly the detections this system exists to make while a sanctions
        # match kept its full value. Measured on seed 7, it pushed all nine
        # dark-contact subjects — the headline capability — to the bottom of
        # their own queue against hulls whose only fact was an ownership chain.
        #
        # In a risk *index*, where confirmed and unreviewed alerts sit side by
        # side over months, that discount is right. In a *queue*, whose entire
        # content is by definition unreviewed, it is a constant that cancels
        # within one source and distorts across sources.
        #
        # So: dismissed alerts are gone (above), open alerts stand as stated,
        # and analyst confirmation moves confidence 30% of the way to certainty
        # — a human having looked and agreed is genuinely new evidence, and it
        # is the only thing here that raises a number rather than lowering it.
        disp = a.get("disposition")
        conf = base + (1.0 - base) * 0.3 if disp == "confirm" else base
        ts = _num(a.get("ts"))
        s = spec(kind)

        evidence = []
        for ev in (a.get("evidence") or []):
            props = ev.get("props") or {}
            evidence.append(Evidence(
                kind="graph_edge",
                label=_edge_label(ev, props),
                ref=a.get("alert_id"),
                occurred_at=iso(ts),
                lat=_num(a.get("props", {}).get("lat")),
                lon=_num(a.get("props", {}).get("lon")),
                confidence=_num(ev.get("confidence")),
                detail={"edge": ev.get("edge"), "src": ev.get("src"),
                        "dst": ev.get("dst"), **props},
                provenance=_prov_from_edge(ev),
                is_synthetic=bool(a.get("is_synthetic"))))
        if not evidence:
            # An alert with an empty chain cannot become a factor — the model
            # refuses it — but the alert itself is still a fact about the
            # system, so it becomes its own evidence rather than vanishing.
            evidence = [Evidence(
                kind="alert", label=f"{s.label} alert {a.get('alert_id')}",
                ref=a.get("alert_id"), occurred_at=iso(ts),
                confidence=base, detail=dict(a.get("props") or {}),
                provenance=_prov_from_edge({"source": "anomaly_library",
                                            "source_ref": a.get("rule")}),
                is_synthetic=bool(a.get("is_synthetic")))]

        out.append(Factor(
            kind=kind, subject_id=a["subject"],
            headline=s.label, confidence=min(1.0, conf),
            evidence=evidence, occurred_at=iso(ts),
            family=s.family, area=s.area, weight=s.weight,
            detail={"alert_id": a.get("alert_id"),
                    "disposition": disp,
                    "stated_confidence": round(base, 4),
                    "analyst_confirmed": disp == "confirm",
                    **{k: v for k, v in (a.get("props") or {}).items()}},
            is_synthetic=bool(a.get("is_synthetic"))))
    return out


def _edge_label(ev: dict, props: dict) -> str:
    edge = str(ev.get("edge") or "relationship").replace("_", " ")
    src, dst = ev.get("src"), ev.get("dst")
    if props.get("zone"):
        return f"{edge}: {props['zone']}"
    if props.get("hours") is not None:
        return f"{edge}: {props['hours']} h"
    if src and dst:
        return f"{edge}: {src} → {dst}"
    return edge


# --------------------------------------------------------------------------
# 2. sanctions designation, from the landed match table
# --------------------------------------------------------------------------

def collect_sanctions_factors(reader: Reader) -> list[Factor]:
    """Designations matched to a hull. ADR-018: theirs to designate, ours to match.

    Only ``is_finding`` rows become factors. A name-only candidate is a lead for
    the vessels table and putting it on a ranked queue is the alert-fatigue
    failure ADR-004 names outright — thousands of hulls share a name fragment
    with something on a list.
    """
    if not reader.has("sanctioned_vessel_matches"):
        return []
    by_vessel: dict[str, list[dict]] = defaultdict(list)
    for r in reader.rows("SELECT * FROM sanctioned_vessel_matches"):
        if r.get("vessel_id") and r.get("is_finding"):
            by_vessel[vessel_node_id(r["vessel_id"])].append(r)

    out: list[Factor] = []
    for vid, rows in by_vessel.items():
        registries = sorted({(r.get("registry") or "OFAC") for r in rows})
        by_imo = any(r.get("match_tier") == "imo" for r in rows)
        # An IMO number survives renaming and reflagging; a call-sign-and-name
        # match does not, and the confidence has to say which one this is.
        conf = 0.95 if by_imo else 0.7
        if len(registries) > 1:
            # Independent lists agreeing is a genuinely stronger claim than one
            # list saying it twice.
            conf = min(0.99, conf + 0.04 * (len(registries) - 1))
        evidence = [Evidence(
            kind="list_entry",
            label=f"{r.get('registry') or 'OFAC'} designation "
                  f"{r.get('ofac_name') or '(unnamed)'}"
                  + (f", programme {r['ofac_program']}"
                     if r.get("ofac_program") else ""),
            ref=str(r.get("ofac_ent_num") or ""),
            occurred_at=as_iso(r.get("sanctions_as_of")),
            confidence=_num(r.get("confidence")),
            detail={"match_tier": r.get("match_tier"),
                    "registry": r.get("registry"),
                    "listed_name": r.get("ofac_name"),
                    "listed_entity_type": r.get("listed_entity_type") or "vessel",
                    "listed_owner": r.get("ofac_owner"),
                    "listed_imo": r.get("ofac_imo"),
                    "our_name": r.get("vessel_name"),
                    "our_flag": r.get("vessel_flag"),
                    "our_imo": r.get("vessel_imo")},
            provenance=_prov_from_row(r),
            is_synthetic=bool(r.get("is_synthetic"))) for r in rows]

        # A designation that names the *owner* is a different claim from one
        # that names the hull, and conflating them would tell an operator a ship
        # is listed when it is not.
        hull_listed = any((r.get("listed_entity_type") or "vessel") == "vessel"
                          for r in rows)
        kind = "sanctions_designation" if hull_listed else "sanctioned_ownership"
        s = spec(kind)
        out.append(Factor(
            kind=kind, subject_id=vid, headline=s.label,
            confidence=conf if hull_listed else min(conf, 0.8),
            evidence=evidence,
            occurred_at=max((as_iso(r.get("sanctions_as_of")) or ""
                             for r in rows), default=None) or None,
            family=s.family, area=s.area, weight=s.weight,
            detail={"registries": registries, "matched_on_imo": by_imo,
                    "n_listings": len(rows), "hull_listed": hull_listed},
            is_synthetic=any(bool(r.get("is_synthetic")) for r in rows)))
    return out


# --------------------------------------------------------------------------
# 3. ownership chain to a designated entity, from the graph
# --------------------------------------------------------------------------

def collect_ownership_factors(store, subjects: Sequence[str],
                              at: float | None = None) -> list[Factor]:
    """Reachability from a hull to a designated organisation through ownership.

    Distance matters and is carried: one hop is control, three hops is a
    corporate structure that may mean nothing. Restricted to subjects that are
    already on the list for some other reason, because walking the ownership
    chain of every hull in the graph on every request is minutes of work for a
    fact that cannot by itself put a vessel on a queue.
    """
    from ..graph.rules import ownership_chains

    out: list[Factor] = []
    for vid in subjects:
        node = store.node(vid)
        if node is None or node.get("node_type") != "vessel":
            continue
        best: tuple[int, str, list] | None = None
        for org, path in ownership_chains(store, vid, at):
            if not store.edges(org, "sanctioned-under", as_of=at):
                continue
            hops = len(path)
            if best is None or hops < best[0]:
                best = (hops, org, path)
        if best is None:
            continue
        hops, org, path = best
        org_node = store.node(org) or {"props": {}}
        org_name = org_node["props"].get("name") or org
        # 1 hop -> 0.85, 2 -> 0.65, 3 -> 0.45. A chain we had to walk three
        # deep is a lead, not a finding, and the number says so.
        conf = max(0.2, 1.05 - 0.2 * hops)
        s = spec("sanctioned_ownership")
        evidence = [Evidence(
            kind="ownership_chain",
            label=f"{hops}-hop ownership chain to {org_name}, "
                  f"designated under sanctions",
            ref=org,
            confidence=round(conf, 3),
            detail={"hops": hops, "organisation": org_name,
                    "organisation_id": org,
                    "path": [str(p) for p in path]},
            provenance={"source_id": "graph", "source_ref": "ownership_chains",
                        "acquired_at": None, "ingested_at": None,
                        "pipeline_version": PIPELINE_VERSION,
                        "confidence": round(conf, 3)},
            is_synthetic=bool(node.get("is_synthetic")))]
        out.append(Factor(
            kind="sanctioned_ownership", subject_id=vid, headline=s.label,
            confidence=conf, evidence=evidence,
            family=s.family, area=s.area, weight=s.weight,
            detail={"hops": hops, "organisation": org_name},
            is_synthetic=bool(node.get("is_synthetic"))))
    return out


# --------------------------------------------------------------------------
# 4. flag opacity and identity churn, from the graph
# --------------------------------------------------------------------------

#: Flags-of-convenience seed list, shared with ``anomaly.risk``. Illustrative
#: rather than exhaustive, and imported rather than re-typed so the two cannot
#: drift into disagreeing about what an opaque flag is.
def _foc_flags() -> set[str]:
    from ..anomaly.risk import FOC_FLAGS
    return set(FOC_FLAGS)


def collect_identity_factors(store, subjects: Sequence[str]) -> list[Factor]:
    """Flag opacity and recorded identity change, for hulls already on the list.

    Neither of these puts a vessel on a queue on its own and neither should:
    tens of thousands of honest ships fly Panama, and a rename is routine on
    sale. They are context that sharpens a subject already under suspicion,
    which is exactly what their weights (0.30 and 0.35) encode.
    """
    foc = _foc_flags()
    out: list[Factor] = []
    for vid in subjects:
        node = store.node(vid)
        if node is None or node.get("node_type") != "vessel":
            continue
        syn = bool(node.get("is_synthetic"))

        flags = store.edges(vid, "flagged-to", history=True)
        current = [e for e in flags if e.t_end is None]
        reflags = [e for e in flags if e.t_end is not None]
        flag_ev: list[Evidence] = []
        for e in current:
            dst = store.node(e.dst) or {"props": {}}
            code = dst["props"].get("code") or str(e.dst).split(":")[-1]
            if code in foc:
                flag_ev.append(Evidence(
                    kind="registry_record",
                    label=f"currently flagged to {code}, a flag of convenience",
                    ref=e.dst, occurred_at=iso(e.t_start),
                    confidence=round(e.base_confidence, 3),
                    detail={"flag": code, "open_interval": True},
                    provenance={"source_id": e.source,
                                "source_ref": e.source_ref,
                                "acquired_at": iso(e.observed_at),
                                "ingested_at": None,
                                "pipeline_version": PIPELINE_VERSION,
                                "confidence": round(e.base_confidence, 3)},
                    is_synthetic=e.is_synthetic))
        for e in reflags:
            dst = store.node(e.dst) or {"props": {}}
            code = dst["props"].get("code") or str(e.dst).split(":")[-1]
            flag_ev.append(Evidence(
                kind="registry_record",
                label=f"previously flagged to {code}, interval closed",
                ref=e.dst, occurred_at=iso(e.t_start),
                confidence=round(e.base_confidence, 3),
                detail={"flag": code, "open_interval": False,
                        "closed_at": iso(e.t_end)},
                provenance={"source_id": e.source, "source_ref": e.source_ref,
                            "acquired_at": iso(e.observed_at),
                            "ingested_at": None,
                            "pipeline_version": PIPELINE_VERSION,
                            "confidence": round(e.base_confidence, 3)},
                is_synthetic=e.is_synthetic))
        if flag_ev:
            conf = min(0.9, 0.5 + 0.15 * len(reflags))
            s = spec("flag_opacity")
            out.append(Factor(
                kind="flag_opacity", subject_id=vid, headline=s.label,
                confidence=conf, evidence=flag_ev,
                family=s.family, area=s.area, weight=s.weight,
                detail={"n_reflags": len(reflags),
                        "flag_of_convenience": any(
                            e.detail.get("open_interval") for e in flag_ev)},
                is_synthetic=syn))

        rows = store._con.execute(
            "SELECT ts, payload FROM events WHERE subject=? AND "
            "event_type='identity_changed' ORDER BY ts", (vid,)).fetchall()
        if rows:
            # **Say which identity changed, and to what.** Every row used to
            # render as the same sentence — "identity changed on record" — so a
            # hull that changed her name and her flag in the same minute
            # produced two lines an operator could not tell apart, and nine
            # changes read as nine copies of one fact. The payload has carried
            # `field`, `old` and `new` since the populator wrote it; only the
            # label was throwing them away. A list of identical strings is not
            # evidence, it is a count wearing evidence's clothes.
            ev = [Evidence(
                kind="identity_change",
                label=_identity_change_label(payload),
                ref=vid, occurred_at=iso(ts), confidence=0.9,
                detail=_identity_change_detail(payload),
                provenance={"source_id": "graph", "source_ref": "events",
                            "acquired_at": iso(ts), "ingested_at": None,
                            "pipeline_version": PIPELINE_VERSION,
                            "confidence": 0.9},
                is_synthetic=syn) for ts, payload in rows]
            s = spec("identity_change")
            out.append(Factor(
                kind="identity_change", subject_id=vid, headline=s.label,
                confidence=min(0.9, 0.4 + 0.2 * len(rows)), evidence=ev,
                occurred_at=iso(rows[-1][0]),
                family=s.family, area=s.area, weight=s.weight,
                detail={"n_changes": len(rows)}, is_synthetic=syn))
    return out


# --------------------------------------------------------------------------
# 5. GFW-assessed AIS disabling — carried, never claimed
# --------------------------------------------------------------------------

def collect_gap_factors(reader: Reader) -> list[Factor]:
    """AIS gaps Global Fishing Watch assessed as intentional disabling.

    **This is GFW's finding, not ours,** and the attribution travels on every
    piece of evidence. We have no receiver-coverage model at those positions,
    and asserting intentional silence outside demonstrated coverage is a false
    positive by construction (CLAUDE.md §6). The honest sentence is "Global
    Fishing Watch assessed this gap as intentional disabling", and the factor
    is worth carrying precisely because someone competent asserted it.
    """
    if not reader.has("gfw_ais_gaps"):
        return []
    if "gfw_intentional_disabling" not in reader.columns("gfw_ais_gaps"):
        return []
    rows = reader.rows(
        "SELECT * FROM gfw_ais_gaps WHERE gfw_intentional_disabling IS NOT NULL "
        "AND CAST(gfw_intentional_disabling AS INTEGER) = 1")
    by_vessel: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("vessel_id"):
            by_vessel[vessel_node_id(r["vessel_id"])].append(r)

    s = spec("assessed_ais_disabling")
    out: list[Factor] = []
    for vid, gs in by_vessel.items():
        evidence = []
        for g in gs:
            hours = _num(g.get("gap_duration_hours") or g.get("duration_hours"))
            evidence.append(Evidence(
                kind="ais_gap",
                label=("Global Fishing Watch assessed this AIS gap as "
                       "intentional disabling"
                       + (f", {hours:.0f} h" if hours else "")),
                ref=str(g.get("event_id") or ""),
                occurred_at=as_iso(g.get("start_time")),
                lat=_num(g.get("gap_off_lat")), lon=_num(g.get("gap_off_lon")),
                confidence=_num(g.get("confidence")) or 0.8,
                detail={"attribution": s.attribution,
                        "duration_hours": hours,
                        "start_time": as_iso(g.get("start_time")),
                        "end_time": as_iso(g.get("end_time")),
                        "off_lat": _num(g.get("gap_off_lat")),
                        "off_lon": _num(g.get("gap_off_lon")),
                        "on_lat": _num(g.get("gap_on_lat")),
                        "on_lon": _num(g.get("gap_on_lon")),
                        "distance_km": _num(g.get("gap_distance_km"))},
                provenance=_prov_from_row(g),
                is_synthetic=bool(g.get("is_synthetic"))))
        out.append(Factor(
            kind="assessed_ais_disabling", subject_id=vid, headline=s.label,
            confidence=0.8, evidence=evidence,
            occurred_at=evidence[-1].occurred_at,
            family=s.family, area=s.area, weight=s.weight,
            detail={"n_gaps": len(gs), "attribution": s.attribution},
            is_synthetic=any(e.is_synthetic for e in evidence)))
    return out


# --------------------------------------------------------------------------
# 6. transponder shutdown, from the landed radar correlation
# --------------------------------------------------------------------------

def collect_shutdown_factors() -> list[Factor]:
    """A contact that was identified, then stopped broadcasting mid-track.

    The strongest thing coastal radar produces (ADR-029). The vessel is not
    merely absent from AIS — she was correlated to a hull for long enough that
    the identification was not luck, and then the transmission stopped while the
    radar kept holding her. That pairs a name with a position and a moment,
    which is the difference between "somebody went dark" and "this ship went
    dark, here, at this time".
    """
    from ..fusion.radar_ais import CONTACT_TABLE
    from ..graph.identity import contact_node_id
    from ..ingest.landing import read_table

    try:
        rows = read_table(CONTACT_TABLE)
    except Exception:                                            # noqa: BLE001
        return []
    s = spec("transponder_shutdown")
    out: list[Factor] = []
    for r in rows:
        if r.get("status") != "dark_candidate" or not r.get("went_dark_at"):
            continue
        track = r.get("radar_track_id")
        if not track:
            continue
        subject = contact_node_id(str(track), source="radar")
        mins = _num(r.get("dark_minutes")) or 0.0
        mmsi = r.get("mmsi")
        ev = [Evidence(
            kind="track_segment",
            label=(f"correlated to MMSI {mmsi}, then silent for "
                   f"{mins / 60:.1f} h while still held on radar"
                   if mmsi else
                   f"held on radar for {mins / 60:.1f} h with nothing "
                   f"broadcasting"),
            ref=str(track),
            occurred_at=as_iso(r.get("went_dark_at")),
            lat=_num(r.get("went_dark_lat")), lon=_num(r.get("went_dark_lon")),
            confidence=_num(r.get("dark_score")),
            detail={"radar_track_id": track,
                    "station_ids": r.get("station_ids"),
                    "mmsi": mmsi,
                    "went_dark_at": as_iso(r.get("went_dark_at")),
                    "dark_minutes": mins,
                    "length_m": _num(r.get("length_m")),
                    "correlation_status": r.get("correlation_status"),
                    "hearable_conf": _num(r.get("hearable_conf"))},
            provenance=_prov_from_row(r),
            is_synthetic=bool(r.get("is_synthetic")))]
        out.append(Factor(
            kind="transponder_shutdown", subject_id=subject,
            headline=s.label,
            confidence=min(0.95, _num(r.get("dark_score")) or 0.5),
            evidence=ev, occurred_at=as_iso(r.get("went_dark_at")),
            family=s.family, area=s.area, weight=s.weight,
            detail={"radar_track_id": track, "mmsi": mmsi,
                    "dark_minutes": mins},
            is_synthetic=bool(r.get("is_synthetic"))))
    return out


# --------------------------------------------------------------------------
# merge + subject metadata
# --------------------------------------------------------------------------

def merge_factors(factors: Sequence[Factor]) -> list[Factor]:
    """Collapse repeats of a kind on a subject into one factor.

    **This is the workload reduction, made arithmetic.** A hull with four
    loitering alerts is one line on a watchkeeper's list, not four, and the four
    stay attached underneath as evidence. Confidence combines the top
    :data:`MERGE_CONFIDENCE_TOP_N` occurrences as independent observations, so
    repeats strengthen the claim without a long tail of weak ones reaching
    certainty.
    """
    grouped: dict[tuple[str, str], list[Factor]] = defaultdict(list)
    order: list[tuple[str, str]] = []
    for f in factors:
        key = (f.subject_id, f.kind)
        if key not in grouped:
            order.append(key)
        grouped[key].append(f)

    out: list[Factor] = []
    for key in order:
        group = grouped[key]
        if len(group) == 1:
            out.append(group[0])
            continue
        group.sort(key=lambda f: float(f.confidence), reverse=True)
        semantics = spec(group[0].kind).repeats
        if semantics == REPEAT_RESTATEMENT:
            # One standing fact, re-derived. Corroboration is worth recording
            # in the evidence but it is not a second observation, so the
            # confidence is the best single derivation and no more. See
            # `catalog.REPEAT_RESTATEMENT` for the measured reason.
            conf = float(group[0].confidence)
        else:
            acc = 1.0
            for f in group[:MERGE_CONFIDENCE_TOP_N]:
                acc *= (1.0 - float(f.confidence))
            conf = min(0.99, 1.0 - acc)

        evidence: list[Evidence] = []
        for f in group:
            evidence.extend(f.evidence)
        occurred = [f.occurred_at for f in group if f.occurred_at]
        merged = Factor(
            kind=group[0].kind, subject_id=group[0].subject_id,
            headline=group[0].headline, confidence=conf, evidence=evidence,
            occurred_at=max(occurred) if occurred else None,
            family=group[0].family, area=group[0].area,
            weight=group[0].weight,
            detail={**group[0].detail,
                    "occurrences": len(group),
                    "first_seen": min(occurred) if occurred else None,
                    "last_seen": max(occurred) if occurred else None,
                    "merged_confidences": [round(float(f.confidence), 3)
                                           for f in group],
                    "repeat_semantics": semantics,
                    "confidence_from_top_n": (
                        MERGE_CONFIDENCE_TOP_N
                        if semantics != REPEAT_RESTATEMENT else 1)},
            is_synthetic=any(f.is_synthetic for f in group),
            # Stable across merges: the id must not depend on which occurrence
            # happened to sort first, or a link into the UI breaks on rebuild.
            factor_id="")
        out.append(merged)
    return out


def _identity_index(reader: Reader) -> dict[str, dict]:
    if not reader.has("gfw_vessel_identity"):
        return {}
    sql = """
    SELECT * FROM (
      SELECT *, row_number() OVER (
        PARTITION BY vessel_id
        ORDER BY (valid_to IS NULL) DESC, valid_from DESC NULLS LAST,
                 ingested_at DESC NULLS LAST) AS _rn
      FROM gfw_vessel_identity) WHERE _rn = 1
    """
    return {vessel_node_id(r["vessel_id"]): r for r in reader.rows(sql)
            if r.get("vessel_id")}


def _last_position(reader: Reader) -> dict[str, dict]:
    if not reader.has("ais_position"):
        return {}
    sql = """
    SELECT vessel_id, lat, lon, ts FROM (
      SELECT vessel_id, lat, lon, ts,
             row_number() OVER (PARTITION BY vessel_id ORDER BY ts DESC) AS _rn
      FROM ais_position WHERE vessel_id IS NOT NULL) WHERE _rn = 1
    """
    return {vessel_node_id(r["vessel_id"]): {
        "lat": _num(r.get("lat")), "lon": _num(r.get("lon")),
        "at": as_iso(r.get("ts")), "basis": "last AIS position"}
        for r in reader.rows(sql)}


def subject_meta(store, reader: Reader, subject_ids: Sequence[str],
                 factors_by_subject: dict[str, list[Factor]]
                 ) -> dict[str, SubjectMeta]:
    """Name, identifiers and last known position for each subject.

    A contact has none of the first two and that is not a gap to be filled with
    a plausible guess — "Unidentified radar contact SYN-MUM:0223" is the true
    label, and the position comes from the factor evidence because a target with
    no identity has no row in any identity table to look it up in.
    """
    idents = _identity_index(reader)
    positions = _last_position(reader)
    out: dict[str, SubjectMeta] = {}
    for sid in subject_ids:
        node = store.node(sid) or {}
        ntype = node.get("node_type") or ("vessel" if sid.startswith("vessel:")
                                          else "contact")
        row = idents.get(sid, {})
        syn = bool(node.get("is_synthetic")) or any(
            f.is_synthetic for f in factors_by_subject.get(sid, []))

        if ntype == "vessel":
            name = (row.get("ship_name") or node.get("props", {}).get("name")
                    or sid.split(":")[-1])
            identifiers = {
                "mmsi": _str(row.get("mmsi")),
                "imo": _str(row.get("imo")),
                "call_sign": _str(row.get("call_sign")),
                "flag": row.get("flag"),
                "vessel_class": row.get("vessel_class"),
                "length_m": _num(row.get("length_m")),
            }
            pos = positions.get(sid, {})
        else:
            props = node.get("props", {})
            key = props.get("track_key") or sid.split(":", 2)[-1]
            if props.get("single_look"):
                # A detection is one look, and the useful half of its label is
                # which sensor took it — the correlation id means nothing to an
                # operator. "radar:SYN-MUN" reduces to the station.
                where = str(props.get("scene_id") or "")
                where = where[len("radar:"):] if where.startswith("radar:") \
                    else where
                name = ("Unidentified contact"
                        + (f", {where}" if where else "")
                        + f" ({key})")
            else:
                name = f"Unidentified radar contact {key}"
            identifiers = {"mmsi": None, "imo": None, "call_sign": None,
                           "flag": None, "vessel_class": None,
                           "length_m": None,
                           "track_key": key,
                           "note": "No broadcast identity. This target has "
                                   "not been named by any sensor"}
            pos = _position_from_factors(factors_by_subject.get(sid, []))
        out[sid] = SubjectMeta(sid, ntype, name, identifiers, pos, syn)
    return out


def _str(v) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s or None


def _position_from_factors(factors: Sequence[Factor]) -> dict:
    best = None
    for f in factors:
        for e in f.evidence:
            if e.lat is None or e.lon is None:
                continue
            if best is None or (e.occurred_at or "") > (best.occurred_at or ""):
                best = e
    if best is None:
        return {}
    return {"lat": best.lat, "lon": best.lon, "at": best.occurred_at,
            "basis": f"position of the {best.kind.replace('_', ' ')} evidence"}


def collect_all(store, reader: Reader | None = None,
                at: float | None = None) -> list[Factor]:
    """Every factor the system can produce today, merged.

    Ownership, flag and identity-churn factors are collected only for subjects
    that some other source has already put on the list — see
    :func:`collect_ownership_factors` for why.
    """
    close = False
    if reader is None:
        ctx = open_reader()
        reader = ctx.__enter__()
        close = True
    try:
        primary: list[Factor] = []
        primary += collect_alert_factors(store)
        primary += collect_sanctions_factors(reader)
        primary += collect_gap_factors(reader)
        primary += collect_shutdown_factors()

        seeds = sorted({f.subject_id for f in primary})
        secondary: list[Factor] = []
        secondary += collect_ownership_factors(store, seeds, at=at)
        secondary += collect_identity_factors(store, seeds)

        # Ownership arrives from two places — the landed match table when the
        # designation names a company, and the graph walk. Merging by kind
        # collapses them into one factor rather than double-counting the same
        # organisation.
        return merge_factors(primary + secondary)
    finally:
        if close:
            ctx.__exit__(None, None, None)
