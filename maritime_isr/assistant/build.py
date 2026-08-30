"""Assembling the ranked Vessel of Interest list, and measuring what it saves.

This is the frame the Section-3 brief says to build first. Everything upstream
already existed as detectors, registries and a graph; what did not exist was one
object that gathers them per subject, ranks it, explains it in a sentence a duty
officer can read aloud, proposes what to do, and answers a follow-up question.

**The workload claim has to be measured, not asserted.** The requirement this
build answers is "dramatically reducing operator workload", and the only honest
form of that claim is a ratio with both ends stated: how many things were in the
picture, how many the operator is asked to look at, measured on a named corpus.
:func:`workload` computes it from the same run that produced the list, and every
number it returns is labelled with the corpus it came from.

**Suppression is part of the product.** A subject that carried a signal and was
kept off the list is recorded with its reason (:class:`~.model.Suppression`), so
"why is this NOT on the list" is answerable from the surface. That discipline
came from the radar cascade (ADR-028) and matters more here, because this list
is where an operator forms their model of what the system does.
"""
from __future__ import annotations

import time
from collections import Counter
from typing import Optional, Sequence

from ..api.reader import open_reader
from ..config import PIPELINE_VERSION
from ..schemas.keys import native_vessel_id, vessel_node_id
from . import collect as _collect
from .catalog import FACTOR_KINDS, family_coverage, spec
from .model import Factor, Suppression, VesselOfInterest, iso
from .narrate import narrate_subject
from .qa import GroundedQA, family_gaps
from .recommend import recommend
from .score import explain_arithmetic, score_factors

__all__ = ["build_list", "build_one", "workload", "ask", "MIN_SCORE"]


#: A subject must clear this to reach the list.
#:
#: **Precision before recall is a product policy with a number attached**
#: (ADR-004), and this is where it bites for the assistant. A subject whose only
#: factor is a weak one — a flag of convenience, a call at a port we think
#: poorly of — scores under 0.25 and stays off, because a queue that contains
#: every hull flying Panama is a queue nobody opens twice.
#:
#: Set at 0.25 rather than tuned: it is the score of a single factor at the
#: weakest weight in the catalog (port risk, 0.25) at full confidence. In other
#: words, the rule is "one weak fact is not a reason", stated as arithmetic
#: rather than as a threshold that happened to look right on this corpus.
MIN_SCORE = 0.25


def _subject_kind(store, subject_id: str) -> str:
    node = store.node(subject_id)
    if node is not None:
        return "vessel" if node.get("node_type") == "vessel" else "contact"
    return "vessel" if subject_id.startswith("vessel:") else "contact"


def _assemble(store, factors_by_subject: dict[str, list[Factor]],
              metas: dict, as_of: Optional[float]) -> list[VesselOfInterest]:
    out: list[VesselOfInterest] = []
    for sid, factors in factors_by_subject.items():
        meta = metas[sid]
        score = score_factors(factors)
        recs = recommend(factors, position=meta.position,
                         subject_kind=meta.kind, name=meta.name)
        account, lines = narrate_subject(
            name=meta.name, subject_kind=meta.kind, score=score,
            factors=factors, position=meta.position,
            is_synthetic=meta.is_synthetic)
        out.append(VesselOfInterest(
            subject_id=sid, subject_kind=meta.kind, display_name=meta.name,
            score=score, factors=sorted(factors,
                                        key=lambda f: -(f.points or 0.0)),
            recommendations=recs, identifiers=meta.identifiers,
            position=meta.position, account=account, account_lines=lines,
            as_of=iso(as_of), is_synthetic=meta.is_synthetic))
    return out


def build_list(*, synthetic: Optional[bool] = None, limit: int = 50,
               min_score: float = MIN_SCORE,
               at: Optional[float] = None) -> dict:
    """The ranked list, with its suppressions, its coverage and its arithmetic.

    ``synthetic`` filters the corpus split the way every other endpoint in this
    system does; ``None`` returns both and the counts stay split. Nothing here
    ever blends a real and a scenario count into one number (ADR-019).
    """
    at = time.time() if at is None else at
    from ..api import graph_service as gsvc

    with gsvc.open_graph() as store:
        if store is None:
            return _empty("No object graph has been built. "
                          "run the pipeline first")
        with open_reader() as reader:
            factors = _collect.collect_all(store, reader, at=at)
            by_subject: dict[str, list[Factor]] = {}
            for f in factors:
                by_subject.setdefault(f.subject_id, []).append(f)
            metas = _collect.subject_meta(store, reader,
                                          sorted(by_subject), by_subject)
        items = _assemble(store, by_subject, metas, at)

    # ---- suppression, with a stated reason ------------------------------
    kept: list[VesselOfInterest] = []
    suppressed: list[Suppression] = []
    for v in items:
        if v.score < min_score:
            top = v.factors[0] if v.factors else None
            suppressed.append(Suppression(
                subject_id=v.subject_id,
                reason="below_attention_threshold",
                explanation=(
                    f"Scored {v.score:.2f}, below the {min_score:.2f} bar. "
                    + (f"Its only evidence is {spec(top.kind).label.lower()}, "
                       f"which is not on its own a reason to look at a ship."
                       if top and len(v.factors) == 1 else
                       "None of its factors is strong enough alone and "
                       "together they do not reach the bar.")),
                detail={"score": round(v.score, 4),
                        "factors": [f.kind for f in v.factors]}))
            continue
        kept.append(v)

    kept.sort(key=lambda v: (-v.score, v.subject_id))
    for i, v in enumerate(kept, 1):
        v.rank = i

    shown = kept
    if synthetic is not None:
        shown = [v for v in kept if v.is_synthetic == synthetic]

    return {
        "as_of": iso(at),
        "count": {"real": sum(1 for v in kept if not v.is_synthetic),
                  "synthetic": sum(1 for v in kept if v.is_synthetic)},
        "total_matched": len(shown),
        "min_score": min_score,
        "items": [v.as_dict(with_evidence=False) for v in shown[:limit]],
        "suppressed": [s.as_dict() for s in suppressed],
        "n_suppressed": len(suppressed),
        "coverage": family_coverage([f.kind for v in kept for f in v.factors]),
        "queue_health": _queue_health(kept),
        "notes": _notes(kept, suppressed),
        "pipeline_version": PIPELINE_VERSION,
    }


def build_one(subject_id: str, *, at: Optional[float] = None) -> Optional[dict]:
    """One subject in full: every factor, every evidence item, the arithmetic.

    Accepts either the canonical node id or a bare native vessel id, because a
    link pasted from another screen may carry either.
    """
    at = time.time() if at is None else at
    from ..api import graph_service as gsvc

    with gsvc.open_graph() as store:
        if store is None:
            return None
        candidates = [subject_id]
        if not subject_id.startswith(("vessel:", "contact:", "detection:")):
            candidates.append(vessel_node_id(subject_id))
        else:
            candidates.append(vessel_node_id(native_vessel_id(subject_id)))

        with open_reader() as reader:
            factors = _collect.collect_all(store, reader, at=at)
            by_subject: dict[str, list[Factor]] = {}
            for f in factors:
                by_subject.setdefault(f.subject_id, []).append(f)
            sid = next((c for c in candidates if c in by_subject), None)
            if sid is None:
                return None
            metas = _collect.subject_meta(store, reader, [sid],
                                          {sid: by_subject[sid]})
            extras = _extras(reader, store, sid)
        v = _assemble(store, {sid: by_subject[sid]}, metas, at)[0]

    out = v.as_dict(with_evidence=True)
    out["arithmetic"] = explain_arithmetic(v.factors, v.score)
    out["not_known"] = family_gaps(v)
    out["answerable_questions"] = GroundedQA(extras).answerable()
    out["extras"] = extras
    return out


def ask(subject_id: str, question: str, *,
        at: Optional[float] = None) -> Optional[dict]:
    """Answer one question about one subject, grounded in what is held."""
    at = time.time() if at is None else at
    from ..api import graph_service as gsvc

    with gsvc.open_graph() as store:
        if store is None:
            return None
        with open_reader() as reader:
            factors = _collect.collect_all(store, reader, at=at)
            by_subject: dict[str, list[Factor]] = {}
            for f in factors:
                by_subject.setdefault(f.subject_id, []).append(f)
            sid = subject_id if subject_id in by_subject else None
            if sid is None:
                cand = vessel_node_id(native_vessel_id(subject_id))
                sid = cand if cand in by_subject else None
            if sid is None:
                return None
            metas = _collect.subject_meta(store, reader, [sid],
                                          {sid: by_subject[sid]})
            extras = _extras(reader, store, sid)
        v = _assemble(store, {sid: by_subject[sid]}, metas, at)[0]

    answer = GroundedQA(extras).answer(question, v)
    return {"subject_id": sid, "display_name": v.display_name,
            "is_synthetic": v.is_synthetic, **answer.as_dict()}


# --------------------------------------------------------------------------
# retrievals the VOI object does not itself carry
# --------------------------------------------------------------------------

def _extras(reader, store, sid: str) -> dict:
    """Port calls, zone visits, imaging opportunities and ownership.

    Fetched per subject rather than for the whole list: these are the answers to
    follow-up questions, so paying for them on a detail view is right and paying
    for them on every row of a fifty-row list is not.
    """
    out: dict = {"ports": [], "zones": [], "imaging": [], "ownership": []}
    keys = [native_vessel_id(sid), sid]

    if reader.has("gfw_port_visits"):
        cols = reader.columns("gfw_port_visits")
        names = [c for c in ("port_name", "visit_port_name", "anchorage_name",
                             "anchorage_top_destination") if c in cols]
        if names:
            expr = "coalesce(" + ", ".join(names) + ")"
            marks = ",".join("?" for _ in keys)
            rows = reader.rows(
                f"SELECT {expr} AS place, max(start_time) AS last_at "
                f"FROM gfw_port_visits WHERE vessel_id IN ({marks}) "
                f"AND {expr} IS NOT NULL GROUP BY place "
                "ORDER BY last_at DESC NULLS LAST", keys)
            out["ports"] = [r["place"] for r in rows]

    if reader.has("gfw_ais_gaps") and reader.has("sar_imaging_opportunity"):
        marks = ",".join("?" for _ in keys)
        rows = reader.rows(
            "SELECT o.* FROM sar_imaging_opportunity o "
            "JOIN gfw_ais_gaps g ON CAST(g.event_id AS VARCHAR) "
            "  = CAST(o.gap_event_id AS VARCHAR) "
            f"WHERE g.vessel_id IN ({marks})", keys)
        order = {"confirmed": 0, "partial": 1, "none": 2, "unknown": 3}
        rows.sort(key=lambda r: order.get(r.get("tier"), 9))
        out["imaging"] = [{"tier": r.get("tier"), "scene_id": r.get("scene_id"),
                           "statement": r.get("statement")} for r in rows]

    node = store.node(sid)
    if node is not None and node.get("node_type") == "vessel":
        for e in store.edges(sid, "owned-by") + store.edges(sid, "operated-by"):
            org = store.node(e.dst) or {"props": {}}
            out["ownership"].append({
                "label": f"{e.edge_type.replace('-', ' ')} "
                         f"{org['props'].get('name') or e.dst}",
                "organisation_id": e.dst,
                "confidence": round(e.base_confidence, 3),
                "valid_from": iso(e.t_start), "valid_to": iso(e.t_end)})
    return out


# --------------------------------------------------------------------------
# the workload claim
# --------------------------------------------------------------------------

def workload(*, at: Optional[float] = None) -> dict:
    """Tracks in, subjects out — the reduction ratio, with both ends stated.

    Deliberately reported as several ratios rather than one headline. They
    measure different things and collapsing them would hide which stage did the
    work:

      * **tracks to subjects** — of everything the sensors held, how much does
        the operator open. This is the number the requirement is asking for.
      * **alerts to subjects** — how much of the reduction came from grouping
        raw detector output by subject rather than from ranking. On a corpus
        where one hull fires four rules, this is where the queue shortens.
      * **subjects to list** — how much came from the attention threshold.

    Every figure is labelled with the corpus it was measured on, and any figure
    quoted outside this system has to carry that label with it (CLAUDE.md §4.6).
    """
    at = time.time() if at is None else at
    from ..api import graph_service as gsvc
    from ..ingest.landing import read_table

    n_ais_tracks = n_radar_tracks = 0
    n_alerts = 0
    try:
        rows = read_table("radar_track_report")
        n_radar_tracks = len({r.get("radar_track_id") for r in rows
                              if r.get("radar_track_id")})
    except Exception:                                            # noqa: BLE001
        # No landed radar picture is the normal state on a corpus that has
        # only AIS, not an error. The count stays 0 and the ratio is computed
        # over what does exist — the alternative, refusing to state any
        # workload figure without radar, would hide the measurement on exactly
        # the corpus the operator has.
        n_radar_tracks = 0

    with open_reader() as reader:
        if reader.has("ais_position"):
            n_ais_tracks = int(reader.scalar(
                "SELECT count(DISTINCT vessel_id) FROM ais_position") or 0)

    with gsvc.open_graph() as store:
        if store is None:
            return _empty("no object graph has been built")
        alerts = [a for a in store.alerts() if a.get("disposition") != "dismiss"]
        n_alerts = len(alerts)

    listed = build_list(limit=100_000, at=at)
    n_subjects = listed["count"]["real"] + listed["count"]["synthetic"]
    n_candidates = n_subjects + listed["n_suppressed"]
    n_tracks = n_ais_tracks + n_radar_tracks

    def ratio(a: int, b: int) -> Optional[float]:
        return None if not b else round(a / b, 4)

    return {
        "as_of": iso(at),
        "corpus": "synthetic scenario corpus" if listed["count"]["real"] == 0
                  else "mixed real + synthetic corpus",
        "inputs": {
            "ais_tracked_vessels": n_ais_tracks,
            "radar_tracks": n_radar_tracks,
            "total_tracks": n_tracks,
            "raw_alerts": n_alerts,
        },
        "outputs": {
            "subjects_with_any_factor": n_candidates,
            "subjects_on_the_list": n_subjects,
            "suppressed_below_threshold": listed["n_suppressed"],
        },
        "ratios": {
            "tracks_per_subject_surfaced": ratio(n_tracks, n_subjects),
            "fraction_of_tracks_surfaced": ratio(n_subjects, n_tracks),
            "alerts_per_subject_surfaced": ratio(n_alerts, n_subjects),
        },
        "statement": _workload_sentence(n_tracks, n_alerts, n_subjects,
                                        listed["count"]["real"] == 0),
        "caveat": (
            "Measured on the corpus named above, in this sandbox. It is a "
            "statement about how much this system shortens a queue, not about "
            "whether the things in the queue are the right things, precision "
            "and recall are measured separately, against scenario truth, and "
            "neither has ever been measured on an operational feed."),
        "pipeline_version": PIPELINE_VERSION,
    }


def _workload_sentence(n_tracks: int, n_alerts: int, n_subjects: int,
                       synthetic: bool) -> str:
    if not n_subjects:
        return ("Nothing reached the list, so no reduction can be stated. "
                "An empty queue is a result, not a failure, but check the "
                "detectors fired at all before reading it as one.")
    label = ("on the synthetic scenario corpus" if synthetic
             else "on the landed corpus")
    return (f"{n_tracks:,} tracked targets and {n_alerts:,} raw detector alerts "
            f"{label} resolve to {n_subjects} subject"
            f"{'' if n_subjects == 1 else 's'} an operator is asked to open, "
            f"about 1 in {max(1, round(n_tracks / n_subjects)):,} of the "
            f"targets in the picture.")


# --------------------------------------------------------------------------
# notes and health
# --------------------------------------------------------------------------

def _queue_health(items: Sequence[VesselOfInterest]) -> dict:
    """Signals that the queue itself is unwell, computed from the queue.

    **Geographic concentration is the one that matters most**, and it is here
    because of a measured failure: before this build, 42 of 43 dark-rendezvous
    alerts fired inside a berth or a designated anchorage — 32 of them within
    500 m of one port coordinate. Every one was a ship lying alongside another
    ship, which is what a port is. A ranked list is exactly where that becomes
    intolerable and exactly where it is visible, so the check lives here even
    though the fix belonged in the detector.
    """
    if not items:
        return {"n": 0, "concentrated": False, "notes": []}
    cells = Counter()
    for v in items:
        lat, lon = v.position.get("lat"), v.position.get("lon")
        if lat is None or lon is None:
            continue
        cells[(round(float(lat), 1), round(float(lon), 1))] += 1
    notes: list[str] = []
    concentrated = False
    if cells:
        (cell, n) = cells.most_common(1)[0]
        frac = n / len(items)
        if n >= 5 and frac >= 0.4:
            concentrated = True
            notes.append(
                f"{n} of {len(items)} subjects ({frac:.0%}) sit within about "
                f"10 km of {cell[0]}°N {cell[1]}°E. A queue concentrated in one "
                f"place is usually a detector firing on a normal local "
                f"activity, not a cluster of suspicious ships. Check it before "
                f"working the list.")
    kinds = Counter(f.kind for v in items for f in v.factors)
    if kinds:
        top_kind, top_n = kinds.most_common(1)[0]
        total = sum(kinds.values())
        if top_n / total >= 0.7 and total >= 5:
            notes.append(
                f"{top_n} of {total} factors ({top_n / total:.0%}) are "
                f"{spec(top_kind).label.lower()}. One detector is carrying this "
                f"queue; its precision is the queue's precision.")
    return {"n": len(items), "concentrated": concentrated,
            "by_factor_kind": dict(kinds), "notes": notes}


def _notes(items: Sequence[VesselOfInterest],
           suppressed: Sequence[Suppression]) -> list[str]:
    real = sum(1 for v in items if not v.is_synthetic)
    syn = len(items) - real
    notes = [
        f"{real} subject(s) from real data, {syn} from the scenario corpus. "
        "Scenario rows are generated and prove the machinery runs; they are "
        "not evidence about real vessels (CLAUDE.md §4.6).",
    ]
    if suppressed:
        notes.append(
            f"{len(suppressed)} further subject(s) carried a signal and were "
            f"kept off this list as too weak to be worth opening. Each one "
            f"records its reason, 'why is this NOT flagged' is answerable "
            f"from this response, not only from a terminal.")
    families = {f.family for v in items for f in v.factors}
    missing = [name for name in ("paperwork", "imagery", "radio")
               if name not in families]
    if missing:
        notes.append(
            "No factor on this list comes from " + ", ".join(missing)
            + ". Those are Areas 4, 5 and 6 of the Section-3 build and are not "
              "implemented, the absence is a build state, not a finding that "
              "the paperwork, the imagery and the radio traffic were clean.")
    unregistered = [k for k in FACTOR_KINDS
                    if k not in {f.kind for v in items for f in v.factors}]
    if unregistered:
        notes.append(
            f"{len(unregistered)} registered factor kind(s) produced nothing "
            f"on this corpus: {', '.join(sorted(unregistered))}. A detector "
            f"that never fires is not evidence of a clean picture.")
    return notes


def _empty(note: str) -> dict:
    return {"as_of": None, "count": {"real": 0, "synthetic": 0},
            "total_matched": 0, "min_score": MIN_SCORE, "items": [],
            "suppressed": [], "n_suppressed": 0,
            "coverage": family_coverage([]), "queue_health": {"n": 0,
                                                              "notes": []},
            "notes": [note], "pipeline_version": PIPELINE_VERSION}
