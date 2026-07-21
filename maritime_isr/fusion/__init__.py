"""Phase 3 — Entity Resolution: the fusion core.

"This is the product. Everything before feeds it; everything after
consumes it."

    run_fusion()  scenes + tracks + coverage model + registry →
                  associations, static-object layer, dark-vessel verdicts,
                  published to the conformed layer + catalog.

The two-pass shape (associate all scenes, build statics from the
accumulated unmatched, then run the cascade) is the honest batch/nightly
form; a streaming deployment applies the static layer as-of.
"""
from __future__ import annotations

import pandas as pd
import pyarrow as pa

from ..config import PIPELINE_VERSION
from ..provenance import now_iso
from ..storage import conformed
from .. import schemas
from ..tracks.coverage import CoverageModel
from .associate import associate_scene
from .dark import build_static_layer, dark_cascade, hearable

__all__ = ["associate_scene", "build_static_layer", "dark_cascade",
           "hearable", "run_fusion"]

_SOURCE = "fusion_core"


def _prov(source_ref: str, acquired_at) -> dict:
    return dict(source=_SOURCE, source_ref=source_ref,
                acquired_at=acquired_at, ingested_at=pd.Timestamp(now_iso()),
                pipeline_version=PIPELINE_VERSION)


def _table(rows: list[dict], schema: pa.Schema) -> pa.Table:
    if not rows:
        return schema.empty_table()
    df = pd.DataFrame(rows)
    for f in schema.names:
        if f not in df.columns:
            df[f] = None
    return pa.Table.from_pandas(df[schema.names], schema=schema,
                                preserve_index=False)


def run_fusion(scenes: list[dict], tracks: list, model: CoverageModel,
               registry: dict[int, float], *, source_ref: str,
               partition_day: str, aoi: str, gaps: list[dict] | None = None,
               spoof_events: list[dict] | None = None,
               write_outputs: bool = True):
    """The nightly run: scene → contacts → association → dark candidates,
    fully automatic (Phase 3 exit criterion #1). `gaps` are Phase 2's
    classified gap rows — they turn matches into SAR-confirmed dark
    periods when scene time falls inside a gap."""
    gaps_by_track: dict[str, list[dict]] = {}
    for g in (gaps or []):
        gaps_by_track.setdefault(g["track_id"], []).append(g)
    associations: list[dict] = []
    det_by_id: dict[str, dict] = {}
    for scene in scenes:
        for d in scene["detections"]:
            det_by_id[d["detection_id"]] = dict(
                d, scene_id=scene["scene_id"], ts=scene["ts"])
        associations.extend(
            associate_scene(scene, tracks, registry, gaps_by_track))

    unmatched = [det_by_id[a["detection_id"]] for a in associations
                 if a["status"] == "unmatched"]
    statics = build_static_layer(unmatched)

    # Static objects claim their contacts BEFORE vessels do: a contact on
    # an accumulated static position is the rig, not the fishing boat that
    # happened to gate it (registry-blind tracks can't be length-cut).
    # Guard: a cell only qualifies if its detections were predominantly
    # unmatched in pass 1 — a berthed transmitting ship matches most of
    # its detections and must never staticize.
    from .dark import _hav_m as _hv
    from ..config import STATIC_RADIUS_M
    def _near_static(lat, lon):
        return any(_hv(lat, lon, so["lat"], so["lon"]) <= STATIC_RADIUS_M
                   for so in statics)
    kept_statics = []
    for so in statics:
        near = [a for a in associations
                if _hv(det_by_id[a["detection_id"]]["lat"],
                       det_by_id[a["detection_id"]]["lon"],
                       so["lat"], so["lon"]) <= STATIC_RADIUS_M]
        matched_frac = sum(a["status"] != "unmatched" for a in near) / len(near) \
            if near else 0.0
        if matched_frac < 0.5:
            kept_statics.append(so)
    statics = kept_statics
    n_reclaimed = 0
    for a in associations:
        d = det_by_id[a["detection_id"]]
        if a["status"] != "unmatched" and _near_static(d["lat"], d["lon"]):
            a.update(status="unmatched", track_id=None, mmsi=None,
                     confidence=0.0, in_ais_gap=False, gap_type=None)
            n_reclaimed += 1
    if n_reclaimed:
        unmatched = [det_by_id[a["detection_id"]] for a in associations
                     if a["status"] == "unmatched"]
    spoof_windows: dict[int, list[tuple[float, float]]] = {}
    for ev in (spoof_events or []):
        if ev.get("event_type") == "DUPLICATE_MMSI":
            spoof_windows.setdefault(ev["mmsi"], []).append(
                (ev["t_start"].timestamp(), ev["t_end"].timestamp()))
    verdicts = dark_cascade(unmatched, model, statics, tracks, spoof_windows)

    for a in associations:
        a.update(_prov(source_ref, a["ts"]))
    for s in statics:
        s.update(_prov(source_ref, s["last_seen"]))
    for v in verdicts:
        v.update(_prov(source_ref, v["ts"]))

    if write_outputs:
        for name, rows, schema in (
                ("association", associations, schemas.ASSOCIATION),
                ("static_object", statics, schemas.STATIC_OBJECT),
                ("dark_candidate", verdicts, schemas.DARK_CANDIDATE)):
            conformed.write(_table(rows, schema), name, source=_SOURCE,
                            aoi=aoi, partition_day=partition_day)

    return dict(associations=associations, statics=statics, verdicts=verdicts)
