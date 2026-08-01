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

from ..config import ANOMALY_THRESHOLDS, GEOFENCE_LOITER_MIN_HOURS
from ..graph.identity import resolve_mmsi
from ..ports import PORTS as _PORTS

# --- sensitive geometry (geofence layer, roadmap 5.2 #4) ------------------
# Cables, pipelines, exercise areas, port approaches. Minimal AOI seed; the
# deploy host folds in real charted infrastructure.
SENSITIVE_ZONES = [
    dict(name="Mumbai High oil field", lat=19.30, lon=71.30, radius_km=40),
    dict(name="SW approaches cable", lat=15.50, lon=68.00, radius_km=35),
    dict(name="Naval exercise area W", lat=17.00, lon=69.50, radius_km=50),
    dict(name="Kandla pipeline corridor", lat=22.90, lon=69.90, radius_km=25),
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
        did = f"detection:{v['detection_id']}"
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

def detect_dark_rendezvous(store, encounters: list[dict],
                           associations: list[dict], *, source_ref: str) -> list[str]:
    """An encounter where at least one party is AIS-silent at scene-adjacent
    time — the ship-to-ship transfer signature. We approximate 'silent' via
    the fusion layer: a party with an in_ais_gap match, OR an unmatched dark
    detection within the encounter footprint."""
    out = []
    # index dark detections by rough location/time for footprint lookup
    darks = [a for a in associations if a["status"] == "unmatched"]
    for e in encounters:
        t = e["t_start"].timestamp()
        # nearest dark detection to the encounter centroid within ~3 km / 12 h
        near = [a for a in darks
                if abs(a["ts"].timestamp() - t) < 12 * 3600]
        footprint = None
        for a in near:
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
            if _hav_km(e["lat"], e["lon"], la, lo) < 3.0:
                footprint = a
                break
        gap_party = any(a.get("in_ais_gap") for a in near)
        if not (footprint or gap_party):
            continue
        va = resolve_mmsi(store, e["mmsi_a"], at=t)
        vb = resolve_mmsi(store, e["mmsi_b"], at=t)
        score = min(1.0, 0.5 + e["confidence"] * 0.4 + (0.15 if footprint else 0))
        ev = [dict(edge="met-with", src=va, dst=vb,
                   confidence=round(e["confidence"], 3), source="track_engine",
                   source_ref=source_ref,
                   props=dict(encounter_id=e["encounter_id"],
                              silent_party=bool(footprint or gap_party)))]
        _emit(out, store, "dark_rendezvous", va, t, score, ev,
              props=dict(counterpart=vb, encounter_id=e["encounter_id"],
                         lat=e["lat"], lon=e["lon"]))
    return out


# ---------------- 4. loitering near sensitive geometry ----------------

def detect_sensitive_loitering(store, tracks: list, *, source_ref: str) -> list[str]:
    from ..tracks.features import extract_features
    out = []
    for tr in tracks:
        f = extract_features(tr)
        for ep in f.get("loiter_episodes", []):
            dur_h = (ep["t_end"] - ep["t_start"]) / 3600.0
            if dur_h < GEOFENCE_LOITER_MIN_HOURS:
                continue
            for z in SENSITIVE_ZONES:
                d = _hav_km(ep["lat"], ep["lon"], z["lat"], z["lon"])
                if d > z["radius_km"]:
                    continue
                vid = resolve_mmsi(store, tr.mmsi, at=ep["t_start"])
                # score: deeper inside the zone + longer loiter = higher
                depth = 1.0 - d / z["radius_km"]
                score = min(1.0, 0.5 + 0.3 * depth + 0.1 * min(dur_h / 6, 1))
                ev = [dict(edge="loiter-in-zone", src=vid, dst=f"zone:{z['name']}",
                           confidence=round(score, 3), source="track_engine",
                           source_ref=source_ref,
                           props=dict(hours=round(dur_h, 1),
                                      dist_km=round(d, 1), zone=z["name"]))]
                _emit(out, store, "loitering_sensitive", vid, ep["t_start"],
                      score, ev, props=dict(zone=z["name"], hours=round(dur_h, 1),
                                            lat=ep["lat"], lon=ep["lon"]))
    return out


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
        vid = resolve_mmsi(store, tr.mmsi, at=t)
        # score is the max single-port risk, lightly boosted by repeat calls
        top = max(r for _, r in risky)
        score = min(1.0, top + 0.05 * (len(risky) - 1))
        ev = [dict(edge="docked-at", src=vid, dst=f"port:{p}",
                   confidence=round(r, 3), source="track_engine",
                   source_ref=source_ref, props=dict(port_risk=r))
              for p, r in risky]
        _emit(out, store, "port_risk_propagation", vid, t, score, ev,
              props=dict(ports=[p for p, _ in risky]))
    return out


def run_anomaly_library(store, *, tracks, encounters, spoof_events,
                        associations, verdicts, source_ref: str) -> dict:
    """Run all six detectors. Order matters only for #5, which correlates
    with the alerts the earlier detectors produced."""
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
    return fired
