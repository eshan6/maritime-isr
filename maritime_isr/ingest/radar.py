"""Coastal-surveillance-radar connector. ADR-028.

**This is a connector and nothing else.** Its whole job is to turn whatever a
radar feed hands us into `radar_track_report` rows in the canonical shape, stamp
the provenance envelope and the H3 cells, and land them. Every downstream stage
— the track engine, the correlation, the dark cascade, the anomaly library —
reads the canonical table and knows nothing about radar. That is the claim
CLAUDE.md §4.5 makes about this architecture, and radar is the first source
built after it to actually test it.

**We have no radar data.** The Indian Coast Guard's Coastal Surveillance Network
is theirs; nothing comparable is public for these waters. So the rows this
connector lands in the prototype come from `scenario.radar`, which simulates a
station network over the *same vessel truth* the synthetic AIS is emitted from —
and they land through this module, flagged synthetic, into the same table a real
feed would use. A parallel path for simulated radar would prove only that the
parallel path works.

The two public entry points differ only in where the plots come from:

    land_plots(plots, source_id=..., is_synthetic=...)   # in-memory rows
    run(path=...)                                        # a feed file on disk

`run` accepts the two shapes a station feed plausibly arrives in — newline JSON
and CSV — and is deliberately untested against any real system, because there is
no real system to test it against. It is here so that the day a feed exists,
the work is mapping its column names and not building a landing path.
"""
from __future__ import annotations

import csv
import io
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from ..schemas.provenance import Provenance
from ..schemas.records import RadarTrackReport
from .checks import report_landed
from .landing import land_raw, land_table, stamp_envelope, stamp_h3

__all__ = ["TABLE", "conform_plot", "land_plots", "rcs_dbsm_from_length",
           "length_m_from_rcs_dbsm", "position_sigma_m", "run"]

#: The canonical table. Shared with any future real feed, by design.
TABLE = "radar_track_report"

#: Natural key. A station's report about one of its tracks at one instant is
#: unique; re-landing an overlapping window merges rather than duplicates.
KEY_FIELDS = ("station_id", "radar_track_id", "ts")

#: Source id for a real coastal-radar feed. Unused in the prototype — scenario
#: rows carry `synthetic-scenario` and the envelope stamper enforces that they
#: must — but named here so it is not invented twice later.
SOURCE_ID = "coastal_radar"


# --------------------------------------------------------------------------
# sensor physics — shared with the simulator so the two cannot disagree
# --------------------------------------------------------------------------

#: Ship radar cross-section against length. RCS ≈ k · L^n with n ≈ 2.4 is the
#: usual working fit for surface vessels at X-band; k is set so a 100 m ship
#: comes out near 5,000 m² (37 dBsm), which is the right order for a merchant
#: broadside. **This is a coarse engineering fit, not a measurement.** Real RCS
#: depends on aspect, sea state, superstructure and material, and fluctuates by
#: several dB look to look. It is used here for one purpose: to give the size
#: gate something to work with that is honest about being approximate.
_RCS_K = 0.08
_RCS_N = 2.4


def rcs_dbsm_from_length(length_m: float) -> float:
    """Nominal radar cross-section in dB m² for a vessel of this length."""
    return 10.0 * math.log10(max(_RCS_K * max(length_m, 1.0) ** _RCS_N, 1e-3))


def length_m_from_rcs_dbsm(rcs_dbsm: float) -> float:
    """Invert the fit: a length estimate from an echo.

    **Deliberately the exact inverse of the forward model.** In the prototype
    the simulator uses the forward direction and the connector the reverse, so
    a bug in one is a bug in both and cannot flatter the size gate. With a real
    feed the station supplies RCS and only this direction runs.
    """
    rcs = 10.0 ** (rcs_dbsm / 10.0)
    return float((max(rcs, 1e-3) / _RCS_K) ** (1.0 / _RCS_N))


#: Range accuracy of a coastal X-band surveillance radar, metres, 1-σ.
#: Effectively constant: it is set by pulse width and sampling, not by range.
SIGMA_RANGE_M = 25.0

#: Bearing accuracy, degrees, 1-σ. Set by beamwidth and the tracker's
#: centroiding. This is the term that matters, because the cross-range error it
#: produces grows linearly with range.
SIGMA_BEARING_DEG = 0.25


def position_sigma_m(range_km: float) -> float:
    """1-σ position error of a plot at this range from its station.

    The combined range/cross-range error, treated as isotropic for the
    consumer's benefit — the fusion gate is a circle, so handing it the larger
    axis would over-gate and the smaller would under-gate. The geometric mean of
    the two axes is the honest single number.

        10 km →  ~33 m      30 km →  ~57 m      50 km →  ~74 m

    The growth is what makes the radar picture degrade with range on its own,
    without anyone tuning a coverage falloff by hand.
    """
    cross = max(range_km, 0.0) * 1000.0 * math.radians(SIGMA_BEARING_DEG)
    return float(math.sqrt(max(SIGMA_RANGE_M, 1e-6) * max(cross, 1.0)))


# --------------------------------------------------------------------------
# conforming
# --------------------------------------------------------------------------

def conform_plot(plot: dict, *, source_id: str = SOURCE_ID,
                 source_ref: str = "radar-feed") -> dict:
    """One feed record → one canonical row, validated.

    **Validation runs through the pydantic `RadarTrackReport`**, the same way
    the AIS connectors run theirs through `PositionReport`, so the schema is the
    contract rather than a comment beside one. That is what refuses an
    un-namespaced `radar_track_id` — two stations both numbering their tracks
    from 1 is the normal case, and merging them would splice unrelated targets
    into one track — and what catches an out-of-range coordinate at the boundary
    instead of three stages downstream.

    Two fields are *derived* when the feed omits them, because they are
    functions of geometry the feed always does supply:
      * `position_sigma_m` from `range_km` — see `position_sigma_m`;
      * `length_est_m` from `rcs_dbsm` — see `length_m_from_rcs_dbsm`.
    A feed that supplies its own is believed; it knows its own hardware.

    The returned row is a flat dict with the envelope already on it. `land_plots`
    then re-stamps through `stamp_envelope`, which is not redundancy for its own
    sake: that function is the one place the `is_synthetic`/`source_id`
    agreement is enforced, and every row in this project has to pass it.
    """
    ts = plot["ts"]
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    rng = _f(plot.get("range_km"))
    rcs = _f(plot.get("rcs_dbsm"))
    rid = plot.get("report_id") or report_id(
        plot["station_id"], str(plot["radar_track_id"]), ts)

    rec = RadarTrackReport(
        report_id=rid,
        station_id=str(plot["station_id"]),
        radar_track_id=str(plot["radar_track_id"]),
        lat=float(plot["lat"]), lon=float(plot["lon"]),
        sog_kn=_f(plot.get("sog_kn")), cog_deg=_f(plot.get("cog_deg")),
        timestamp=ts,
        range_km=rng, bearing_deg=_f(plot.get("bearing_deg")),
        position_sigma_m=_f(plot.get("position_sigma_m")) or (
            position_sigma_m(rng) if rng is not None else None),
        rcs_dbsm=rcs,
        length_est_m=_f(plot.get("length_est_m")) or (
            length_m_from_rcs_dbsm(rcs) if rcs is not None else None),
        snr_db=_f(plot.get("snr_db")),
        track_quality=(int(plot["track_quality"])
                       if plot.get("track_quality") is not None else None),
        prov=Provenance(source_id=source_id, source_ref=source_ref,
                        acquired_at=ts),
    )

    return dict(
        report_id=rec.report_id,
        station_id=rec.station_id,
        radar_track_id=rec.radar_track_id,
        ts=rec.timestamp,
        lat=rec.lat, lon=rec.lon,
        sog_kn=rec.sog_kn, cog_deg=rec.cog_deg,
        range_km=rec.range_km, bearing_deg=rec.bearing_deg,
        position_sigma_m=rec.position_sigma_m,
        rcs_dbsm=rec.rcs_dbsm, length_est_m=rec.length_est_m,
        snr_db=rec.snr_db, track_quality=rec.track_quality,
    )


def _f(v):
    if v is None or v == "":
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def report_id(station_id: str, radar_track_id: str, ts: datetime) -> str:
    """Deterministic and readable. Two runs of the same feed produce the same
    ids, so re-landing converges instead of duplicating."""
    return f"{station_id}:{radar_track_id.split(':', 1)[-1]}:{ts:%Y%m%dT%H%M%SZ}"


# --------------------------------------------------------------------------
# landing
# --------------------------------------------------------------------------

def land_plots(plots: Iterable[dict], *, source_id: str = SOURCE_ID,
               source_ref: str = "radar-feed",
               is_synthetic: bool = False) -> dict[str, int]:
    """Conform, stamp and land. Returns rows per day partition after merge.

    The envelope's `acquired_at` is the report time, not the ingest time. On a
    radar feed those are seconds apart and it looks pedantic; it is the same
    discipline that makes a re-landed historical file behave, and the stamper
    refuses a row without it.
    """
    rows = []
    for p in plots:
        row = conform_plot(p, source_id=source_id, source_ref=source_ref)
        stamp_envelope(
            row, source_id=source_id,
            source_ref=f"{source_ref}:{row['station_id']}",
            acquired_at=row["ts"],
            # A radar plot's confidence is the station's own track quality,
            # normalised. Where the station says nothing, so do we — a
            # fabricated 1.0 would be worse than a null.
            confidence=(row["track_quality"] / 100.0
                        if row.get("track_quality") is not None else None),
            is_synthetic=is_synthetic)
        stamp_h3(row)
        rows.append(row)
    if not rows:
        return {}
    return land_table(rows, table=TABLE, key_fields=KEY_FIELDS, day_field="ts")


def run(path: str | Path, *, station_id: str | None = None) -> int:
    """Land a radar feed file. Returns rows landed.

    **Untested against any real system, and that is stated rather than hidden.**
    No coastal-radar feed is available to this project; this exists so the day
    one is, the work is a column mapping. Raw bytes are landed immutably first,
    the way every connector here does it, so a mis-mapped column can be fixed
    and re-derived rather than re-fetched.
    """
    path = Path(path)
    payload = path.read_bytes()
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    land_raw("coastal_radar", path.name, payload, day=day)

    text = payload.decode("utf-8", errors="replace")
    plots: list[dict] = []
    if path.suffix.lower() in (".json", ".ndjson", ".jsonl"):
        for line in text.splitlines():
            line = line.strip()
            if line:
                plots.append(json.loads(line))
    else:
        for rec in csv.DictReader(io.StringIO(text)):
            plots.append(dict(rec))
    for p in plots:
        if station_id and not p.get("station_id"):
            p["station_id"] = station_id
        if p.get("radar_track_id") and ":" not in str(p["radar_track_id"]):
            p["radar_track_id"] = f"{p['station_id']}:{p['radar_track_id']}"

    written = land_plots(plots, source_ref=path.name)
    # `report_landed`, not a print of `len(plots)`. The count a reader carries
    # away has to be the one on disk: plots merge on their natural key, so a
    # re-landed overlapping file legitimately writes fewer rows than it read.
    report_landed("radar", TABLE, written, len(plots), noun="plot")
    return 0
