"""Anomaly library v1 (roadmap 5.2) — six detectors, each a rule/model over
the object graph, each shipped behind its OWN precision gate.

Design contract shared by all six:
  - a detector consumes graph state + Phase 2/3 outputs, never raw pixels;
  - it produces candidate anomalies with a score in [0,1] and an evidence
    chain (the same readable-chain discipline as Phase 4 alerts);
  - only candidates scoring above that anomaly's threshold
    (config.ANOMALY_THRESHOLDS) become alerts — precision-gated launch,
    per detector, recall grows only as measured precision holds;
  - every alert is disposable, feeding the Phase 5.1 loop.

The six (roadmap 5.2 list):
  1 dark_vessel            unmatched SAR contact survived the Phase 3 cascade
  2 ais_spoofing           duplicate-MMSI / impossible kinematics / AIS-says-
                           here-but-SAR-says-nothing contradiction
  3 dark_rendezvous        SAR-detected encounter with an AIS-silent party
  4 loitering_sensitive    sustained low-speed inside a sensitive geofence
  5 identity_then_anomaly  rename/reflag/MMSI-swap followed within N days by
                           dark behavior — the laundering sequence
  6 port_risk_propagation  calls at high-risk ports raise vessel score via
                           graph edges
"""
from __future__ import annotations

import hashlib
import math

import pandas as pd

from ..config import (ANCHORAGE_RADIUS_KM, ANOMALY_THRESHOLDS,
                      GEOFENCE_LOITER_MIN_HOURS, PORT_RADIUS_KM,
                      RENDEZVOUS_NEAR_KM, RENDEZVOUS_PARTY_SLACK_S,
                      RENDEZVOUS_WINDOW_S)
from ..graph.identity import (ensure_contact_node, ensure_detection_node,
                              resolve_mmsi, track_subject_id)
from ..ports import PORTS as _PORTS
from ..ports import at_waiting_area
from ..schemas.keys import vessel_node_id
from ..zones.derive import SENSITIVE_AREAS as _SENSITIVE_AREAS

# --- sensitive geometry (geofence layer, roadmap 5.2 #4) ------------------
# Cables, pipelines, exercise areas, port approaches.
#: The four sensitive areas, as (name, lat, lon, radius_km) → the dict shape
#: this rule has always used.
#:
#: **They are no longer defined here.** ADR-030 moved the geometry into the
#: zone layer, where it is a landed row with provenance, is renderable, is
#: toggleable, and sits beside the areas an operator draws. This view exists so
#: the rule behaves identically when no zone index is supplied — a migration
#: that changed detection behaviour at the same time as it moved the data would
#: make any regression impossible to attribute.
SENSITIVE_ZONES = [
    dict(name=name, lat=lat, lon=lon, radius_km=r)
    for name, (lat, lon, r) in sorted(_SENSITIVE_AREAS.items())
]

#: Risk weight per port. **A judgement about a place, not a fact about it**, so
#: it stays here rather than in the shared gazetteer (ADR-023) — `ports.PORTS`
#: says where a port is, this says what we think of it, and the two have
#: different owners and different review standards.
#:
#: Every key must name a port the gazetteer knows, or the rule silently never
#: fires for it. That is checked at import rather than trusted: the previous
#: arrangement had three port lists and a name in one that was absent from
#: another produced no error, only silence.
HIGH_RISK_PORTS = {"Karachi": 0.7, "Kandla": 0.4}   # seed risk weights

_unknown_ports = set(HIGH_RISK_PORTS) - set(_PORTS)
if _unknown_ports:
    raise ValueError(
        f"HIGH_RISK_PORTS names {sorted(_unknown_ports)}, which the shared "
        f"gazetteer does not contain. A track can never be reported as calling "
        f"there, so the risk weight would be dead configuration. Add the port "
        f"to maritime_isr/ports.py or correct the spelling.")


def _hav_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(a))


def _aid(kind: str, *parts) -> str:
    return "anm_" + hashlib.sha1(
        (kind + "|" + "|".join(str(p) for p in parts)).encode()).hexdigest()[:12]


def _emit(out, store, atype, subject, ts, score, evidence, props=None):
    """Gate on the per-anomaly threshold, then record as a disposable alert."""
    if score < ANOMALY_THRESHOLDS[atype]:
        return
    aid = _aid(atype, subject, round(ts))
    store.add_alert(aid, rule=atype, subject=subject, ts=ts,
                    confidence=score, evidence=evidence,
                    anomaly_type=atype, score=score, props=props or {})
    out.append(aid)


# ---------------- 1. dark vessel ----------------

def detect_dark_vessels(store, verdicts: list[dict], *, source_ref: str) -> list[str]:
    out = []
    for v in verdicts:
        if v["status"] != "dark_candidate":
            continue
        # The subject is published as a node before the alert points at it.
        # It used to be a bare f-string, so `add_alert` looked it up, found
        # nothing, and defaulted `is_synthetic` to False — every dark-vessel
        # alert in the scenario corpus was filed as real data. See
        # `ensure_detection_node`.
        did = ensure_detection_node(store, v["detection_id"],
                                    scene_id=v.get("scene_id"),
                                    props=dict(length_m=v.get("length_m")))
        ev = [dict(edge="dark_candidate", src=did, dst="sensor:sentinel1-syn",
                   confidence=round(v["dark_score"], 3), source="fusion_core",
                   source_ref=source_ref,
                   props=dict(length_m=v["length_m"],
                              hearable=v.get("hearable_conf")))]
        _emit(out, store, "dark_vessel", did, pd.Timestamp(v["ts"]).timestamp(),
              v["dark_score"], ev,
              props=dict(lat=v["lat"], lon=v["lon"], length_m=v["length_m"]))
    return out


# ---------------- 2. AIS spoofing ----------------

def detect_spoofing(store, spoof_events: list[dict], verdicts: list[dict],
                    *, source_ref: str) -> list[str]:
    out = []
    # 2a. duplicate-MMSI / impossible kinematics from the Phase 2 engine
    for s in spoof_events:
        t = s["t_start"].timestamp()
        vid = resolve_mmsi(store, s["mmsi"], at=t)
        if s["event_type"] == "DUPLICATE_MMSI":
            # score rises with separation: two hulls 1000 km apart under one
            # MMSI is unambiguous; 30 km could be receiver error
            score = min(1.0, 0.55 + s["max_separation_km"] / 2000.0)
        else:                              # IMPOSSIBLE_KINEMATICS
            score = 0.55
        ev = [dict(edge=s["event_type"].lower(), src=vid,
                   dst=f"identity:mmsi:{s['mmsi']}",
                   confidence=round(score, 3), source="track_engine",
                   source_ref=source_ref,
                   props=dict(max_separation_km=s.get("max_separation_km"),
                              detail=s.get("detail")))]
        _emit(out, store, "ais_spoofing", vid, t, score, ev,
              props=dict(event_type=s["event_type"], mmsi=s["mmsi"]))
    return out


# ---------------- 3. dark rendezvous ----------------

def _encounter_subject(store, e: dict, side: str, t: float) -> str:
    """The graph node for one party to an encounter, whatever saw it.

    An encounter derived from AIS names two MMSIs and resolves to two hulls. One
    derived from **radar** names two station track numbers and no hull at all —
    which is the interesting case, because a meeting between two contacts nobody
    can name is exactly the ship-to-ship signature this rule exists to find.

    `resolve_mmsi(store, None)` would mint `vessel:mmsi:None` for both parties,
    so the rendezvous would be recorded as a vessel meeting itself. Same defect
    as ADR-028's second finding, one module along.
    """
    mmsi = e.get(f"mmsi_{side}")
    if mmsi is not None:
        return resolve_mmsi(store, mmsi, at=t)
    key = e.get(f"track_key_{side}") or e.get(f"track_id_{side}")
    return ensure_contact_node(store, str(key),
                               source=e.get("track_source") or "radar")


def detect_dark_rendezvous(store, encounters: list[dict],
                           associations: list[dict], *, source_ref: str) -> list[str]:
    """An encounter where at least one party is AIS-silent at the time — the
    ship-to-ship transfer signature.

    **"A party to this meeting was silent" is what the rule means, and until
    coastal radar arrived it was not what the rule asked.** Two things were
    wrong, and both stayed invisible because the SAR corpus holds six unmatched
    contacts in total, so a loose test was almost always false by accident:

      * `gap_party` was computed over every unmatched association *anywhere in
        the AOI* within twelve hours. No distance test at all. It asked "was
        anything, anywhere in the Arabian Sea, dark today?"
      * `footprint` did narrow to 3 km of the encounter — but 3 km of open
        water off a working coast contains other traffic, and an unexplained
        blip near two ships is not evidence about either of them.

    Coastal radar supplies four orders of magnitude more unmatched contacts and
    the consequence was immediate: **667 dark_rendezvous alerts on seed 7, 76 of
    them on background traffic with no truth row behind them.** A rule that
    fires on every meeting in the picture is worse than one that never fires,
    because it also buries the ones that matter (ADR-004).

    So the question is asked of the parties themselves. A sensor that tracks its
    own targets stamps the sensing track into the detection id, so an unmatched
    association can be attributed to the exact track it came from — and an
    encounter knows its two tracks by id. "This meeting had a silent party" then
    becomes a lookup rather than a proximity guess.

    The positional test survives for detections that have **no track of their
    own** — a SAR scene detection is a single look at an object nobody is
    following, and position is genuinely all there is. That path is unchanged,
    which is why the SAR behaviour this rule was written for still holds.
    """
    out = []
    darks = [a for a in associations if a["status"] == "unmatched"]

    # Unexplained observations that belong to a track we are following, indexed
    # by that track. `<track_id>@<epoch>` is the radar path's detection id.
    silent_at: dict[str, list[float]] = {}
    untracked: list[dict] = []
    for a in darks:
        did = str(a.get("detection_id") or "")
        if "@" in did:
            silent_at.setdefault(did.rsplit("@", 1)[0], []).append(
                a["ts"].timestamp())
        else:
            untracked.append(a)

    for e in encounters:
        # **A meeting at a berth is a berth, not a transfer.** Two hulls lying
        # within 500 m of each other at under 2 knots is the encounter
        # primitive's definition, and inside a port or its designated waiting
        # area that describes every ship alongside and every ship in the queue.
        #
        # Measured on seed 7 before this test existed: **42 of 43
        # dark_rendezvous alerts fired inside a berth or an anchorage** — 32 of
        # them 470 m from the Mangalore port coordinate, 8 at Mundra, 2 at
        # Kochi. One alert in forty-three was in open water. A queue that is
        # 98% berth traffic is the alert-fatigue failure ADR-004 exists to
        # prevent, and it stayed invisible while the corpus was SAR-only,
        # because six unmatched contacts in total can only meet each other by
        # accident. Coastal radar (ADR-028) put thousands of contacts into the
        # picture and the anchorages lit up.
        #
        # `at_waiting_area` is the shared helper `extract_features` already
        # uses to suppress loiter episodes for exactly this reason — a vessel
        # stopped off Kandla is waiting for a berth, not loitering. The
        # rendezvous rule asks the same question of a place and had never been
        # taught to. Reusing the one helper is what keeps the two rules from
        # drifting apart again; both layers are needed because a berth radius
        # does not reach the anchorage that serves it.
        #
        # What this costs: a ship-to-ship transfer that genuinely happens at
        # anchor is now invisible to this rule. That is a real loss and it is
        # the precision-first trade stated in ADR-004 — bunkering at a
        # designated anchorage is lawful and routine, so an alert there carries
        # almost no information, while the same meeting 40 nm offshore carries
        # most of it. Separating the two needs activity classification (is this
        # hull anchored, or holding station in open water?), which is Area 2 of
        # the Section-3 brief and does not exist yet.
        if at_waiting_area(e["lat"], e["lon"], port_radius_km=PORT_RADIUS_KM,
                           anchorage_radius_km=ANCHORAGE_RADIUS_KM):
            continue
        t = e["t_start"].timestamp()
        t_end = pd.Timestamp(e["t_end"]).timestamp()
        # Direct evidence: one of THESE two tracks was unexplained while THIS
        # meeting was happening. One epoch of slack either side, no more —
        # the claim is about the meeting, not about the day.
        party_silent = any(
            t - RENDEZVOUS_PARTY_SLACK_S <= s <= t_end + RENDEZVOUS_PARTY_SLACK_S
            for k in (e.get("track_id_a"), e.get("track_id_b")) if k
            for s in silent_at.get(k, ()))

        near = []
        for a in untracked:
            if abs(a["ts"].timestamp() - t) >= RENDEZVOUS_WINDOW_S:
                continue
            # Associations carry the contact's position directly now. They did
            # not, and this loop read `a["props"]["lat"]` — a key that was never
            # written — so `footprint` was always None and the rule could only
            # fire through `gap_party`. `props` is still accepted so an older
            # association row does not break the rule.
            la, lo = a.get("lat"), a.get("lon")
            if la is None and isinstance(a.get("props"), dict):
                la, lo = a["props"].get("lat"), a["props"].get("lon")
            if la is None or lo is None:
                continue
            if _hav_km(e["lat"], e["lon"], la, lo) < RENDEZVOUS_NEAR_KM:
                near.append(a)
        footprint = next(iter(near), None)
        gap_party = any(a.get("in_ais_gap") for a in near)
        if not (party_silent or footprint or gap_party):
            continue
        va = _encounter_subject(store, e, "a", t)
        vb = _encounter_subject(store, e, "b", t)
        # **The score has to be able to fall below the threshold, and it could
        # not.** It read `0.5 + confidence * 0.4 + …` against a threshold of
        # 0.50, so its floor WAS the threshold: every encounter that reached
        # this line was emitted, and `ANOMALY_THRESHOLDS["dark_rendezvous"]`
        # had no effect on anything. Starting from the evidence instead means a
        # marginal encounter with only circumstantial silence nearby now scores
        # under the bar and stays out of the queue, while a confident meeting
        # with a demonstrably silent party clears it comfortably.
        score = min(1.0, 0.35 + 0.35 * e["confidence"]
                    + (0.25 if party_silent else 0.0)
                    + (0.10 if footprint else 0.0))
        ev = [dict(edge="met-with", src=va, dst=vb,
                   confidence=round(e["confidence"], 3), source="track_engine",
                   source_ref=source_ref,
                   props=dict(encounter_id=e["encounter_id"],
                              silent_party=bool(party_silent or footprint
                                                or gap_party),
                              # Which kind of evidence — an analyst opening this
                              # needs to know whether a party was demonstrably
                              # unexplained or something merely was, nearby.
                              silence_evidence=("party" if party_silent
                                                else "footprint" if footprint
                                                else "nearby_gap")))]
        _emit(out, store, "dark_rendezvous", va, t, score, ev,
              props=dict(counterpart=vb, encounter_id=e["encounter_id"],
                         lat=e["lat"], lon=e["lon"]))
    return out


# ---------------- 4. loitering near sensitive geometry ----------------

def detect_sensitive_loitering(store, tracks: list, *, source_ref: str,
                               index=None) -> list[str]:
    """Sustained low speed inside sensitive geometry, from any positional sensor.

    **The subject comes from `track_subject_id`, not from `resolve_mmsi`
    (ADR-028).** This rule reasons about *behaviour in a place* and needs no
    identity to do it: a contact holding station over a cable approach for six
    hours is the finding whether or not anything is broadcasting there. It used
    to call `resolve_mmsi(store, tr.mmsi)`, which on an identity-less track
    resolves `None` into a fresh provisional hull node per track — the alert
    would have landed on a stub that says nothing.

    **`index` is what turns geofencing from a stub into a feature (ADR-030).**
    Without it the rule tests the four circles it has always tested. With a
    `zones.ZoneIndex` it tests every `sensitive_area` AND every `geofence` in
    the layer — so an area the operator drew ten minutes ago is watched by the
    same rule, with the same threshold, as the four that were compiled in. That
    is the requirement's "a drawn area and a statutory boundary should be the
    same kind of object", made true at the one place it is easiest to fake.

    The alert lands on the zone's real node id either way, so the evidence chain
    points at something the graph holds rather than at a string built from a
    name (`zone:Mumbai High oil field`, which was never a node).
    """
    from ..tracks.features import extract_features
    from ..zones.geometry import contains as _in_zone
    out = []
    if index is not None:
        watched = [(z.zone_id, z.name, index.geometry(z.zone_id), z.kind)
                   for k in ("sensitive_area", "geofence")
                   for z in index.of_kind(k)]
    else:
        watched = []
    for tr in tracks:
        f = extract_features(tr)
        for ep in f.get("loiter_episodes", []):
            dur_h = (ep["t_end"] - ep["t_start"]) / 3600.0
            if dur_h < GEOFENCE_LOITER_MIN_HOURS:
                continue
            hits: list[tuple[str, str, float, str]] = []
            if index is not None:
                for zid, zname, geom, kind in watched:
                    if _in_zone(geom, ep["lat"], ep["lon"]):
                        # Depth from the centroid, as before — the score has to
                        # keep meaning the same thing across the migration.
                        c = geom.centroid
                        d = _hav_km(ep["lat"], ep["lon"], c.y, c.x)
                        hits.append((zid, zname, d, kind))
            else:
                for z in SENSITIVE_ZONES:
                    d = _hav_km(ep["lat"], ep["lon"], z["lat"], z["lon"])
                    if d <= z["radius_km"]:
                        hits.append((f"zone:{z['name']}", z["name"], d,
                                     "sensitive_area"))
            for zid, zname, d, kind in hits:
                vid = track_subject_id(store, tr, at=ep["t_start"])
                radius = _radius_of(zname)
                depth = max(0.0, 1.0 - d / radius) if radius else 0.5
                score = min(1.0, 0.5 + 0.3 * depth + 0.1 * min(dur_h / 6, 1))
                ev = [dict(edge="loiter-in-zone", src=vid, dst=zid,
                           confidence=round(score, 3), source="track_engine",
                           source_ref=source_ref,
                           props=dict(hours=round(dur_h, 1),
                                      dist_km=round(d, 1), zone=zname,
                                      zone_kind=kind,
                                      sensor=tr.source.name))]
                _emit(out, store, "loitering_sensitive", vid, ep["t_start"],
                      score, ev, props=dict(zone=zname, zone_id=zid,
                                            zone_kind=kind,
                                            hours=round(dur_h, 1),
                                            lat=ep["lat"], lon=ep["lon"],
                                            sensor=tr.source.name,
                                            track_id=tr.track_id))
    return out


def _radius_of(zone_name: str) -> float:
    """The scoring radius for a named sensitive area, or 0 for anything else.

    The depth term needs a scale. For the four migrated circles that scale is
    their own radius and is known; for an operator-drawn polygon there is no
    such thing, so `depth` falls back to a flat 0.5 rather than inventing one.
    A drawn area therefore scores on duration alone, which is the honest
    reading — we know she loitered inside the box the operator cares about, and
    nothing about how central that was.
    """
    hit = _SENSITIVE_AREAS.get(zone_name)
    return float(hit[2]) if hit else 0.0


# ---------------- 5. identity change then anomaly ----------------

def detect_identity_then_anomaly(store, *, window_days: float = 14.0) -> list[str]:
    """A rename/reflag/MMSI-swap followed within window_days by dark
    behavior on the same hull — the laundering pattern. Reads identity_
    changed events from the graph and correlates with each hull's own
    dark_vessel / dark_rendezvous / ais_spoofing alerts."""
    out = []
    id_changes: dict[str, list[dict]] = {}
    # pending_events is drained by Phase 4; re-read identity changes from the
    # processed log via the events table directly
    rows = store._con.execute(
        "SELECT subject, ts, payload FROM events "
        "WHERE event_type='identity_changed'").fetchall()
    for subject, ts, payload in rows:
        id_changes.setdefault(subject, []).append(dict(ts=ts, payload=payload))
    anomaly_alerts = [a for a in store.alerts()
                      if a["anomaly_type"] in ("dark_vessel", "dark_rendezvous",
                                               "ais_spoofing")]
    for vid, changes in id_changes.items():
        for ch in changes:
            for a in anomaly_alerts:
                # subject of dark_vessel is a detection; map back via graph
                subj_vessel = a["subject"]
                if a["anomaly_type"] == "dark_vessel":
                    resolved = [e.src for e in store.edges(
                        a["subject"], "resolved-from", direction="in")]
                    subj_vessel = resolved[0] if resolved else None
                if subj_vessel != vid:
                    continue
                dt_days = (a["ts"] - ch["ts"]) / 86400.0
                if 0 <= dt_days <= window_days:
                    score = min(1.0, 0.6 + 0.4 * (1 - dt_days / window_days))
                    ev = [dict(edge="identity_changed", src=vid,
                               dst="(identity)", confidence=0.9,
                               source="registry", source_ref="graph",
                               props=dict(days_before_anomaly=round(dt_days, 1))),
                          dict(edge=a["anomaly_type"], src=vid,
                               dst=a["subject"], confidence=a["score"],
                               source="anomaly_library", source_ref="graph",
                               props={})]
                    _emit(out, store, "identity_then_anomaly", vid, a["ts"],
                          score, ev, props=dict(gap_days=round(dt_days, 1),
                                                followed_by=a["anomaly_type"]))
    return out


# ---------------- 6. port-call risk propagation ----------------

def detect_port_risk(store, tracks: list, *, source_ref: str) -> list[str]:
    from ..tracks.features import extract_features
    out = []
    for tr in tracks:
        f = extract_features(tr)
        risky = [(p, HIGH_RISK_PORTS[p]) for p in f["port_calls"]
                 if p in HIGH_RISK_PORTS]
        if not risky:
            continue
        t = tr.points["ts"].max().timestamp()
        # Source-agnostic subject — see `detect_sensitive_loitering`. A radar
        # track calling at Karachi is the same observation as an AIS one; what
        # differs is that we cannot say which hull made the call, and the
        # contact node says exactly that rather than inventing a hull.
        vid = track_subject_id(store, tr, at=t)

        # Score is the max single-port risk, lightly boosted by **breadth** —
        # how many *different* high-risk ports the hull touched.
        #
        # It used to boost on `len(risky)`, which is the call *sequence* and so
        # counts a repeat visit as a fresh risk event. That is not what a repeat
        # visit is: a liner working a Kandla rotation calls there every circuit
        # because that is its trade. Measured, once the gazetteer got good enough
        # for Kandla calls to register at all: Kandla's weight is 0.4, three
        # calls added 0.10, and 8 ordinary merchants landed on exactly the 0.50
        # gate — 8 alerts, every one background traffic, none on the cast. The
        # rule was firing on "is in the Kandla trade".
        #
        # Breadth is the thing that is actually unusual. One hull touching both
        # Karachi and Kandla is a different pattern from one hull calling at
        # Kandla three times, and only the first should escalate.
        #
        # Visit counts stay in the evidence, because "called here four times" is
        # something an analyst wants to see even when it is not itself a reason
        # to alert. Whether repeat *intensity* deserves its own signal is open:
        # it needs a per-port baseline of normal call frequency, which we do not
        # have and cannot get from this corpus.
        counts: dict[str, int] = {}
        for p, _ in risky:
            counts[p] = counts.get(p, 0) + 1
        top = max(r for _, r in risky)
        score = min(1.0, top + 0.05 * (len(counts) - 1))
        ev = [dict(edge="docked-at", src=vid, dst=f"port:{p}",
                   confidence=round(HIGH_RISK_PORTS[p], 3),
                   source="track_engine", source_ref=source_ref,
                   props=dict(port_risk=HIGH_RISK_PORTS[p], calls=n,
                              sensor=tr.source.name))
              for p, n in sorted(counts.items())]
        _emit(out, store, "port_risk_propagation", vid, t, score, ev,
              props=dict(ports=sorted(counts), calls=counts,
                         sensor=tr.source.name, track_id=tr.track_id))
    return out


def run_anomaly_library(store, *, tracks, encounters, spoof_events,
                        associations, verdicts, source_ref: str,
                        identities: list[dict] | None = None,
                        baselines=None) -> dict:
    """Run every detector. Order matters only for #5, which correlates with the
    alerts the earlier detectors produced.

    `identities` and `baselines` are the Area 2 inputs and both default to
    absent, so a caller written against the original six keeps working — the
    new detectors stay quiet rather than raising.
    """
    fired = {}
    fired["dark_vessel"] = detect_dark_vessels(store, verdicts, source_ref=source_ref)
    fired["ais_spoofing"] = detect_spoofing(store, spoof_events, verdicts,
                                            source_ref=source_ref)
    fired["dark_rendezvous"] = detect_dark_rendezvous(
        store, encounters, associations, source_ref=source_ref)
    fired["loitering_sensitive"] = detect_sensitive_loitering(
        store, tracks, source_ref=source_ref)
    fired["port_risk_propagation"] = detect_port_risk(store, tracks,
                                                      source_ref=source_ref)
    fired["identity_then_anomaly"] = detect_identity_then_anomaly(store)
    # Area 2 detectors. Both are optional inputs: a run with no identity rows
    # and no baseline index still produces the six original detectors, so an
    # older caller keeps working and the new ones simply stay quiet.
    fired["identity_contradiction"] = detect_identity_contradiction(
        store, identities or [], source_ref=source_ref)
    fired["notable_activity"] = detect_notable_activity(
        store, tracks, source_ref=source_ref, baselines=baselines)
    return fired


# ---------------- 7. declared identity does not hold together (Area 2) ------

def detect_identity_contradiction(store, identities: list[dict], *,
                                  source_ref: str) -> list[str]:
    """A hull whose declared identity contradicts itself or the registry.

    Area 2 of the IDEX Challenge 82 brief: *"the software is not able to
    classify authenticity of static information transmitted on AIS. Hence,
    AI/ML software is required for anomaly detection in static information."*

    `identities` is a list of current-identity rows — the same shape
    `gfw_vessel_identity` lands, one per hull. The checks themselves live in
    `anomaly.identity` and are pure functions over the declared values; this is
    the part that turns a contradiction into a scored, evidenced alert.

    **A hull can fail more than one check and that is one alert, not three.**
    The checks are not independent evidence about different things — they are
    several readings of the same question, "is this identity real" — so they
    combine as a maximum with a small boost for breadth rather than as a
    noisy-OR. Two contradictions on one hull is worse than one; it is not
    nearly-certain.

    **Where this can fire.** Scenario identifiers are minted inside a reserved
    MMSI block and with valid IMO check digits by construction (ADR-019), so
    the arithmetic checks cannot fire on synthetic rows — they are built for the
    landed real GFW corpus and their precision must be measured there. The
    registry-consistency check fires on either.
    """
    from .identity import check_identity

    out: list[str] = []
    for row in identities:
        vid = row.get("vessel_id")
        if not vid:
            continue
        findings = check_identity(
            mmsi=row.get("mmsi"), imo=row.get("imo"), flag=row.get("flag"),
            name=row.get("ship_name"), call_sign=row.get("call_sign"),
            vessel_class=row.get("vessel_class"),
            registry=row.get("registry"))
        bad = [f for f in findings if f.is_contradiction]
        if not bad:
            continue

        node = vessel_node_id(vid)
        if store.node(node) is None:
            # The identity table can name a hull the graph has never been told
            # about. Skipping is right rather than minting a stub: an alert on
            # a node with no edges is the shadow-stub failure ADR-022 exists to
            # prevent, and the graph populator is what should create it.
            continue

        best = max(f.confidence for f in bad)
        score = min(0.97, best + 0.05 * (len(bad) - 1))
        ts = pd.Timestamp(row.get("valid_from") or row.get("acquired_at")
                          or pd.Timestamp.utcnow())
        ev = [dict(edge="identity-contradiction", src=node,
                   dst=f"identity:{f.check}", confidence=round(f.confidence, 3),
                   source="identity_rules", source_ref=source_ref,
                   props=dict(check=f.check, statement=f.statement, **f.detail))
              for f in bad]
        _emit(out, store, "identity_contradiction", node, ts.timestamp(),
              score, ev,
              props=dict(checks=[f.check for f in bad],
                         n_contradictions=len(bad),
                         statements=[f.statement for f in bad]))
    return out


# ---------------- 8. what the vessel is doing (Area 2) ---------------------

#: Activities that are worth an operator's attention on their own.
#:
#: **Most activities are not findings and must not become alerts.** Transiting
#: is what shipping does; anchored is what shipping does at the end of it.
#: Emitting an alert per classified activity would put the whole fleet in the
#: queue — the failure ADR-004 names — so only the behaviours that are unusual
#: *as behaviours* are raised, and even those are scored low: a survey pattern
#: is interesting, it is not evidence of wrongdoing.
#: **Drifting was here and was measured out.** A vessel barely moving off a
#: working coast is waiting for a berth, has her gear out, or has stopped her
#: engine — it is one of the most common things in the picture. Measured on the
#: corpus: 125 drifting windows across the AIS fleet, every one scoring an
#: identical 0.273, which is a detector firing on a background condition rather
#: than on an event. What remains are two behaviours that are unusual *as
#: behaviours*: covering an area in a lawnmower pattern, and changing course far
#: more than passage requires without the regularity of a working pattern.
#: Each entry is `(notability, the metric whose local baseline is relevant)`.
#:
#: **The second half exists because getting it wrong silenced the detector.**
#: The first version compared every notable activity's median speed against the
#: local speed distribution and halved the score where the speed was ordinary.
#: For a survey pattern that is a category error: the finding is the *pattern* —
#: six legs and five reciprocal turns over a day — and it is no less a pattern
#: for being run at a speed other vessels in that cell also use. Measured: three
#: genuine survey patterns in the AIS fleet, every one halved from ~0.37 to
#: ~0.19 and dropped below a 0.30 gate. The detector reported a clean picture
#: because it had been told to ignore the thing it found.
#:
#: So a baseline only scales a score when the activity's *signature* is that
#: metric. Neither behaviour here is speed-defined, so both carry `None` and
#: the local distribution travels as context on the evidence instead — which is
#: what a watchkeeper wants anyway ("4 knots, where the local 95th percentile
#: is 12"). A future speed-defined activity names `sog_kn` and gets the scaling.
NOTABLE_ACTIVITIES: dict[str, tuple[float, str | None]] = {
    "survey_pattern": (0.62, None),
    "manoeuvring_erratically": (0.58, None),
}


def detect_notable_activity(store, tracks: list, *, source_ref: str,
                            baselines=None) -> list[str]:
    """Classify what each track is doing and raise only the notable ones.

    Source-agnostic by construction: it reads `tracks.activity`, which takes a
    built track and never asks which sensor produced it. That is Area 3's
    requirement — *"the same behaviours should be recognisable whether the track
    came from radar or AIS. If they are not, that is a defect in the fusion
    core"* — satisfied by placement rather than by a compatibility shim.

    `baselines` is a `BaselineIndex`. When supplied, an activity in a cell where
    that activity's kinematics are locally ordinary is scored down: drifting in
    an approach channel where the local median speed is already near zero is a
    ship waiting, not a ship adrift. When it is absent, or the cell has too few
    observations, the global judgement stands and the evidence says so — a rule
    that could not tell "normal here" from "we have not watched here" would
    report every unwatched patch of ocean as clean.
    """
    from ..tracks.activity import (classify_activity,
                                   classify_activity_segments)

    out: list[str] = []
    for tr in tracks:
        # **Two scales, because these behaviours live at two scales.**
        # Erratic manoeuvring is visible inside a few hours. A survey pattern is
        # *defined* by structure across many of them — six legs over a day — and
        # a six-hour window contains one leg and cannot see the pattern it is
        # part of. Classifying only in windows silently loses the survey rule
        # entirely, which is what it did: the detector fired zero alerts while
        # the whole-track verdict on the same data was `survey_pattern`.
        #
        # `_emit` keys an alert on (type, subject, timestamp), so a behaviour
        # that both scales agree on collapses to one alert rather than two.
        episodes = list(classify_activity_segments(tr))
        episodes.append(classify_activity(tr))
        for act in episodes:
            spec_ = NOTABLE_ACTIVITIES.get(act.activity)
            if spec_ is None or act.confidence <= 0:
                continue
            base, signature_metric = spec_
            score = base * act.confidence

            # The local distribution is always attached; it only *scales* the
            # score when the activity's signature is the metric it describes.
            # See `NOTABLE_ACTIVITIES`.
            local = None
            if baselines is not None:
                from ..baselines import is_unusual
                local = is_unusual(
                    baselines, lat=act.lat, lon=act.lon, metric="sog_kn",
                    value=act.features.get("sog_median", 0.0))
                if (signature_metric == "sog_kn" and local is not None
                        and not local["unusual"]):
                    # Ordinary for this place. Not silenced — an operator may
                    # still want it — but it should not compete with a finding.
                    score *= 0.5

            vid = track_subject_id(store, tr, at=act.t_start)
            ev = [dict(edge="activity", src=vid,
                       dst=f"activity:{act.activity}",
                       confidence=round(act.confidence, 3),
                       source="activity_classifier", source_ref=source_ref,
                       props=dict(activity=act.activity, reason=act.reason,
                                  hours=round(act.duration_hours, 2),
                                  sensor=tr.source.name,
                                  local_baseline=local,
                                  **{k: v for k, v in act.features.items()
                                     if k in ("sog_median", "turn_rate_deg_min",
                                              "straightness", "spread_m")}))]
            _emit(out, store, "notable_activity", vid, act.t_start, score, ev,
                  props=dict(activity=act.activity,
                             hours=round(act.duration_hours, 2),
                             lat=act.lat, lon=act.lon,
                             reason=act.reason,
                             sensor=tr.source.name,
                             track_id=tr.track_id,
                             local_baseline=local))
    return out
