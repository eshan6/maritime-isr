"""Phase 2 — AIS Track Engine.

Detections without tracks are photographs; tracks are the memory the graph
is built from. Public surface:

    build_tracks()      raw conformed positions → smoothed BuiltTracks + spoof events
    CoverageModel       the honest map of where silence is meaningful
    classify_gaps()     every gap labeled: COVERAGE_GAP | SAT_PASS_GAP | INTENTIONAL_SILENCE
    detect_encounters() the rendezvous primitive
    extract_features()  behavioral fingerprint seed for Phase 4
    run_track_engine()  the whole thing, conformed-in → conformed-out + catalog
"""
from __future__ import annotations

import pandas as pd
import pyarrow as pa

from ..config import PIPELINE_VERSION
from ..provenance import now_iso
from ..storage import conformed
from .. import schemas, tiling
from .builder import BuiltTrack, build_tracks
from .coverage import CoverageModel, SatPassSchedule, classify_gaps
from .features import detect_encounters, extract_features

__all__ = ["build_tracks", "BuiltTrack", "CoverageModel", "SatPassSchedule",
           "classify_gaps", "detect_encounters", "extract_features",
           "run_track_engine"]

_SOURCE = "track_engine"


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


def run_track_engine(positions: pd.DataFrame, *, source_ref: str,
                     sat_schedule: SatPassSchedule | None = None,
                     partition_day: str, aoi: str,
                     write_outputs: bool = True):
    """positions: conformed ais_position rows. Returns dict of everything;
    optionally publishes TRACK/TRACK_POINT/TRACK_GAP/SPOOF_EVENT/ENCOUNTER
    parquet + catalog registration in the same call — nothing lands
    unconformed or uncataloged (Phase 0 discipline, unchanged)."""
    tracks, spoofs = build_tracks(positions)

    t0 = positions["ts"].min()
    model = CoverageModel(t0.timestamp(), sat_schedule).fit(positions)

    # active DUPLICATE_MMSI windows per MMSI: silence attribution is
    # suppressed inside them (identity compromised)
    spoof_win: dict[int, list[tuple[float, float]]] = {}
    for s_ in spoofs:
        if s_["event_type"] == "DUPLICATE_MMSI":
            spoof_win.setdefault(s_["mmsi"], []).append(
                (s_["t_start"].timestamp(), s_["t_end"].timestamp()))

    gaps: list[dict] = []
    for tr in tracks:
        gaps.extend(classify_gaps(tr, model, spoof_win.get(tr.mmsi)))
    encounters = detect_encounters(tracks)
    feats = [extract_features(tr) for tr in tracks]

    track_rows, point_rows = [], []
    for tr in tracks:
        pv = _prov(source_ref, tr.points["ts"].min())
        track_rows.append(dict(
            track_id=tr.track_id, mmsi=tr.mmsi, hypothesis=tr.hypothesis,
            t_start=tr.points["ts"].min(), t_end=tr.points["ts"].max(),
            n_points=len(tr.points), n_outliers=tr.n_outliers,
            median_report_s=tr.median_report_s,
            fragmented_from=tr.fragmented_from, **pv))
        for r in tr.points.itertuples():
            point_rows.append(dict(
                track_id=tr.track_id, mmsi=tr.mmsi, ts=r.ts,
                lat=r.lat, lon=r.lon, sog_kn=r.sog_kn, cog_deg=r.cog_deg,
                sigma_m=r.sigma_m, quality=r.quality,
                h3_cell=tiling.cell(r.lat, r.lon),
                **_prov(source_ref, r.ts)))
    for g in gaps:
        g.update(_prov(source_ref, g["t_start"]))
    for s in spoofs:
        s["event_id"] = "spf_" + str(abs(hash((s["mmsi"], str(s["t_start"])))))[:12]
        s.update(_prov(source_ref, s["t_start"]))
    for e in encounters:
        e.update(_prov(source_ref, e["t_start"]))

    out = dict(tracks=tracks, spoof_events=spoofs, gaps=gaps,
               encounters=encounters, features=feats, coverage_model=model)

    if write_outputs:
        for name, rows, schema in (
                ("track", track_rows, schemas.TRACK),
                ("track_point", point_rows, schemas.TRACK_POINT),
                ("track_gap", gaps, schemas.TRACK_GAP),
                ("spoof_event", spoofs, schemas.SPOOF_EVENT),
                ("encounter", encounters, schemas.ENCOUNTER)):
            conformed.write(_table(rows, schema), name, source=_SOURCE,
                            aoi=aoi, partition_day=partition_day)
    return out
