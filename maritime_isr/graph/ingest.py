"""Graph ingest (roadmap 4.5 acceptance #1): populate the graph from
everything upstream. After this runs, every AIS-active vessel is an entity
with registry identity, track history, detection history, and fingerprint
attached — and the edge history starts accumulating, which is the
compounding asset that cannot be backfilled later (principle 6).
"""
from __future__ import annotations

import pandas as pd

from ..tracks.features import AOI_PORTS, extract_features
from .identity import resolve_mmsi

SENSOR_SAR = "sensor:sentinel1-syn"
AUTHORITY = {"OFAC": "authority:OFAC", "UN": "authority:UN",
             "EU": "authority:EU"}


def ensure_world(store) -> None:
    store.upsert_node(SENSOR_SAR, "sensor", dict(kind="sar", constellation="synthetic"))
    for name, (lat, lon) in AOI_PORTS.items():
        store.upsert_node(f"port:{name}", "port", dict(name=name, lat=lat, lon=lon))
    for a, nid in AUTHORITY.items():
        store.upsert_node(nid, "sanctions_authority", dict(name=a))


def ingest_ownership(store, ownership: dict, *, source_ref: str,
                     as_of: float) -> None:
    for org in ownership["organizations"]:
        store.upsert_node(f"org:{org['name']}", "organization",
                          dict(name=org["name"],
                               jurisdiction=org.get("jurisdiction")))
    for org in ownership["organizations"]:
        if org.get("parent"):
            store.add_edge("owned-by", f"org:{org['name']}",
                           f"org:{org['parent']}", t_start=as_of, t_end=None,
                           confidence=org.get("confidence", 0.8),
                           observed_at=as_of, source="corporate_registry",
                           source_ref=source_ref)
    for own in ownership["vessel_owners"]:
        vid = resolve_mmsi(store, own["mmsi"], at=as_of)
        store.add_edge("owned-by", vid, f"org:{own['org']}",
                       t_start=as_of, t_end=None,
                       confidence=own.get("confidence", 0.85),
                       observed_at=as_of, source="corporate_registry",
                       source_ref=source_ref)


def ingest_sanctions(store, entries: list[dict], *, source_ref: str) -> None:
    for e in entries:
        subj = f"org:{e['name']}" if e["entry_type"] == "entity" else \
            resolve_mmsi(store, e["mmsi"], at=e["valid_from_epoch"])
        if store.node(subj) is None:
            store.upsert_node(subj, "organization", dict(name=e["name"]))
        store.add_edge("sanctioned-under", subj,
                       AUTHORITY.get(e["registry"], AUTHORITY["OFAC"]),
                       t_start=e["valid_from_epoch"],
                       t_end=e.get("valid_to_epoch"),
                       confidence=0.98, observed_at=e["valid_from_epoch"],
                       source=f"sanctions:{e['registry']}",
                       source_ref=source_ref,
                       props=dict(program=e.get("program"),
                                  entry_id=e.get("entry_id")))


def ingest_tracks(store, tracks: list, *, source_ref: str) -> None:
    """Vessel entities + track nodes + resolved-from + fingerprint +
    docked-at from port-call features."""
    for tr in tracks:
        t0 = tr.points["ts"].min().timestamp()
        t1 = tr.points["ts"].max().timestamp()
        vid = resolve_mmsi(store, tr.mmsi, at=t0)
        tid = f"track:{tr.track_id}"
        store.upsert_node(tid, "track",
                          dict(track_id=tr.track_id, mmsi=tr.mmsi,
                               n_points=len(tr.points)))
        store.add_edge("resolved-from", vid, tid, t_start=t0, t_end=t1,
                       confidence=0.9, observed_at=t1,
                       source="track_engine", source_ref=source_ref)
        f = extract_features(tr)
        store.upsert_node(vid, "vessel", dict(fingerprint=dict(
            sog_mean=f["sog_mean"], sog_p90=f["sog_p90"],
            heading_change_rate=f["heading_change_rate_deg_min"],
            n_loiter_episodes=f["n_loiter_episodes"],
            port_calls=f["port_calls"]),
            fingerprint_updated_at=t1))
        for port in f["port_calls"]:
            store.add_edge("docked-at", vid, f"port:{port}",
                           t_start=t0, t_end=None, confidence=0.7,
                           observed_at=t1, source="track_engine",
                           source_ref=source_ref)


def ingest_encounters(store, encounters: list[dict], *, source_ref: str) -> None:
    for e in encounters:
        t = e["t_start"].timestamp()
        va = resolve_mmsi(store, e["mmsi_a"], at=t)
        vb = resolve_mmsi(store, e["mmsi_b"], at=t)
        eid = f"encounter:{e['encounter_id']}"
        store.upsert_node(eid, "encounter",
                          dict(encounter_id=e["encounter_id"],
                               duration_min=e["duration_min"],
                               min_distance_m=e["min_distance_m"],
                               lat=e["lat"], lon=e["lon"]))
        store.add_edge("met-with", va, vb, t_start=t,
                       t_end=e["t_end"].timestamp(),
                       confidence=e["confidence"], observed_at=t,
                       source="track_engine", source_ref=source_ref,
                       props=dict(encounter_id=e["encounter_id"]))
        # both parties get the event: the rule checks each one's counterpart
        store.emit("met_with", va, t, dict(counterpart=vb,
                                           encounter_id=e["encounter_id"]))
        store.emit("met_with", vb, t, dict(counterpart=va,
                                           encounter_id=e["encounter_id"]))
        # the met-with edge is stored once (va->vb); the vb-subject event
        # must still find it, so mirror it for traversal symmetry
        store.add_edge("met-with", vb, va, t_start=t,
                       t_end=e["t_end"].timestamp(),
                       confidence=e["confidence"], observed_at=t,
                       source="track_engine", source_ref=source_ref,
                       props=dict(encounter_id=e["encounter_id"]))


def ingest_fusion(store, associations: list[dict], verdicts: list[dict],
                  *, source_ref: str) -> None:
    """Detections into the graph: dark candidates as unresolved detection
    nodes; matched contacts as resolved-from evidence on the vessel;
    gap-confirmed INTENTIONAL matches emit the dark-gap rule's event."""
    for v in verdicts:
        if v["status"] != "dark_candidate":
            continue                      # suppressed verdicts stay in parquet
        did = f"detection:{v['detection_id']}"
        store.upsert_node(did, "detection",
                          dict(detection_id=v["detection_id"],
                               scene_id=v["scene_id"], lat=v["lat"],
                               lon=v["lon"], length_m=v["length_m"],
                               dark_score=v["dark_score"]))
        store.add_edge("detected-by", did, SENSOR_SAR,
                       t_start=v["ts"].timestamp(),
                       t_end=v["ts"].timestamp() + 1,
                       confidence=v["dark_score"],
                       observed_at=v["ts"].timestamp(),
                       source="fusion_core", source_ref=source_ref)
    for a in associations:
        if a["status"] not in ("matched", "ambiguous"):
            continue
        t = a["ts"].timestamp()
        vid = resolve_mmsi(store, a["mmsi"], at=t)
        did = f"detection:{a['detection_id']}"
        store.upsert_node(did, "detection",
                          dict(detection_id=a["detection_id"],
                               scene_id=a["scene_id"]))
        store.add_edge("detected-by", did, SENSOR_SAR, t_start=t, t_end=t + 1,
                       confidence=0.9, observed_at=t,
                       source="fusion_core", source_ref=source_ref)
        store.add_edge("resolved-from", vid, did, t_start=t, t_end=t + 1,
                       confidence=a["confidence"], observed_at=t,
                       source="fusion_core", source_ref=source_ref)
        if a.get("in_ais_gap"):
            store.emit("gap_confirmed", vid, t,
                       dict(gap_type=a.get("gap_type"),
                            scene_id=a["scene_id"],
                            detection_id=a["detection_id"],
                            confidence=a["confidence"]))
