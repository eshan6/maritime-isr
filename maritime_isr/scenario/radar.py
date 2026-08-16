"""Simulate the coastal radar picture — from the same vessel truth as the AIS.

**One truth, two sensors.** This module never invents a vessel, a position or a
voyage. It walks `world.tracks` — the integrated motion the scenarios already
authored and the AIS emitter already decimated into position reports — and asks,
every five minutes, which coastal station could see that hull and what it would
report. That is the whole design, and it is the reason the corpus can contain a
vessel that is on radar and not on AIS without either picture being fabricated
to suit: she is one ship, and the two sensors disagree because one of them was
switched off.

Three populations fall out of that, with no extra machinery:

  * **both** — ordinary traffic inside coastal coverage. The correlation stage
    has to explain every one of these, and each one it fails to explain is a
    false dark contact.
  * **AIS only** — anything outside radar range, in a shadow sector, below the
    horizon, or too small to hold. Most of the corpus, because most of the
    corpus is offshore and the network is coastal.
  * **radar only** — the finding. A hull whose transponder is off, or was never
    fitted, inside a station's cover.

**Nothing about *why* enters a plot.** The same contract `primitives/ais.py`
states for suppressions holds here: an intentionally dark vessel, a
never-fitted skiff and a naval unit produce identical rows. The cause lives in
`radar_dark_truth` and nothing that decides anything may read it.

**The dark episodes are derived, not declared.** A scenario says "her
transponder is off from Tuesday"; whether that becomes a *findable* dark episode
depends on whether a station could actually see her, which depends on where she
sailed. So the ledger is computed here, after the picture exists, from the plots
and the emitted AIS — which is also how it can record, honestly, that a real
dark period produced nothing because nothing was watching.
"""
from __future__ import annotations

import bisect
import hashlib
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .geography import (destination, in_aoi,
                        initial_bearing_deg, receiver_coverage)
from .radar_network import (CLUTTER_MAX_REPORTS, CLUTTER_MIN_REPORTS,
                            CLUTTER_RATE_PER_STATION_REPORT, CLUTTER_SPEED_KN,
                            FIXED_TARGETS, RCS_FLUCTUATION_DB,
                            REPORT_INTERVAL_S, STATIONS, TRACK_DROP_S,
                            best_station, measured_position,
                            station_view)
from .radar_truth import (CAUSE_NEVER_TRANSMITS, CAUSE_OUT_OF_RECEPTION,
                          CAUSE_SILENT_THROUGHOUT, CAUSE_TRANSPONDER_OFF,
                          RadarDarkEpisode, RadarTruthLedger)

__all__ = ["RadarPlot", "generate_radar_picture", "RadarGenerationReport"]

#: Fixed installations report less often than vessels. They do not move, so a
#: five-minute cadence would multiply the corpus by a third to say the same
#: thing four thousand more times. Fifteen minutes still gives the static-object
#: layer thousands of looks per object over eight weeks, which is far more than
#: the three it needs.
FIXED_TARGET_INTERVAL_S = 900.0

#: An AIS report within this of a plot counts as explaining it, for TRUTH
#: purposes only. Deliberately generous — a vessel reporting every twenty
#: minutes is not dark, and calling her dark in the answer key would make the
#: measurement reward a detector that over-fires.
AIS_EXPLAINS_WITHIN_S = 20 * 60.0

#: The shortest run of unexplained plots the truth ledger will call an episode,
#: and the fewest plots it may rest on. Below this there is not enough evidence
#: for anyone — human or machine — to conclude anything, so scoring a detector
#: for missing it would deflate recall with cases nobody could win.
DARK_MIN_MINUTES = 40.0
DARK_MIN_PLOTS = 4

#: Below this modelled terrestrial reception, a silence is not evidence of
#: anything — nobody was listening. `emit_ais` uses the same figure to decide
#: that nothing lands at all, so a position under it genuinely produces no AIS
#: row however healthy the transponder is.
#:
#: The number matters more than it looks: the scenario AIS receiver network has
#: five sites at 300 km, which leaves the whole Konkan coast between Mumbai and
#: Mangalore effectively unheard — while the *radar* network covers it densely.
#: So there is a stretch of this corpus where radar sees ordinary traffic that
#: AIS cannot, and every hull in it looks dark. Getting that population out of
#: the answer key is what this constant is for.
AIS_RECEPTION_FLOOR = 0.02


@dataclass
class RadarPlot:
    """One report, in the shape `ingest.radar.conform_plot` consumes.

    `truth_entity_id` is the one field that never lands. It is how the truth
    ledger — and only the truth ledger — knows which hull a plot came from.
    """
    station_id: str
    radar_track_id: str
    ts: datetime
    lat: float
    lon: float
    sog_kn: float
    cog_deg: float
    range_km: float
    bearing_deg: float
    position_sigma_m: float
    rcs_dbsm: float
    snr_db: float
    track_quality: int
    truth_entity_id: str | None = None
    truth_kind: str = "vessel"          # vessel | fixed | clutter

    def as_feed_record(self) -> dict:
        return dict(
            station_id=self.station_id, radar_track_id=self.radar_track_id,
            ts=self.ts, lat=self.lat, lon=self.lon,
            sog_kn=self.sog_kn, cog_deg=self.cog_deg,
            range_km=self.range_km, bearing_deg=self.bearing_deg,
            position_sigma_m=self.position_sigma_m,
            rcs_dbsm=self.rcs_dbsm, snr_db=self.snr_db,
            track_quality=self.track_quality)


@dataclass
class RadarGenerationReport:
    """What the picture contains, split by where it came from."""
    plots: list[RadarPlot] = field(default_factory=list)
    truth: RadarTruthLedger = field(default_factory=RadarTruthLedger)
    n_vessel_plots: int = 0
    n_fixed_plots: int = 0
    n_clutter_plots: int = 0
    n_clutter_tracks: int = 0
    #: entity_id -> plots, for the coverage accounting.
    seen_vessels: set[str] = field(default_factory=set)
    #: Vessels that moved and no station ever saw — the honest denominator.
    unseen_vessels: set[str] = field(default_factory=set)
    n_track_numbers: int = 0

    def counts(self) -> dict:
        return dict(
            radar_plots=len(self.plots),
            radar_plots_vessel=self.n_vessel_plots,
            radar_plots_fixed=self.n_fixed_plots,
            radar_plots_clutter=self.n_clutter_plots,
            radar_tracks=self.n_track_numbers,
            radar_clutter_tracks=self.n_clutter_tracks,
            radar_vessels_seen=len(self.seen_vessels),
            radar_vessels_unseen=len(self.unseen_vessels),
            radar_dark_episodes=len(self.truth),
        )


def _tid(station_id: str, n: int) -> str:
    """A station track number, namespaced. Four digits, wrapping at 10,000 —
    which is what a real tracker does with a fixed-size track table and is the
    reason nothing downstream may treat a repeat as an identity claim."""
    return f"{station_id}:{n % 10_000:04d}"


class _TrackNumberPool:
    """Hands out station track numbers, and reuses them the way a tracker does.

    A number belongs to a target while the station holds it. Lose contact for
    longer than `TRACK_DROP_S` and the number goes back in the pool; the next
    acquisition — of that target or any other — may get it again. That is what
    produces the two behaviours the correlation stage has to survive: one ship
    appearing as several tracks, and one track number covering more than one
    ship over eight weeks.
    """

    def __init__(self):
        self._next: dict[str, int] = {}
        self._live: dict[tuple[str, str], tuple[str, datetime]] = {}
        self.issued = 0

    def number_for(self, station_id: str, target_key: str,
                   t: datetime) -> str:
        cur = self._live.get((station_id, target_key))
        if cur is not None and (t - cur[1]).total_seconds() <= TRACK_DROP_S:
            self._live[(station_id, target_key)] = (cur[0], t)
            return cur[0]
        n = self._next.get(station_id, 1)
        self._next[station_id] = n + 1
        self.issued += 1
        tid = _tid(station_id, n)
        self._live[(station_id, target_key)] = (tid, t)
        return tid


def _decimate(points: list, interval_s: float) -> list:
    """One point per interval, keeping the first. The truth is integrated at
    60 s; a radar report every 60 s for eight weeks is a corpus nobody can hold
    on a laptop, and `radar_network.REPORT_INTERVAL_S` explains the choice."""
    if not points:
        return []
    out = [points[0]]
    for p in points[1:]:
        if (p.t - out[-1].t).total_seconds() >= interval_s - 1e-6:
            out.append(p)
    return out


def _ais_times(world, entity_id: str) -> list[float]:
    return sorted(r.t.timestamp() for r in world.ais_of(entity_id))


def _explained_by_ais(times: list[float], t: float) -> bool:
    """Was this hull on AIS around now? Truth-side only."""
    if not times:
        return False
    i = bisect.bisect_left(times, t)
    for j in (i - 1, i):
        if 0 <= j < len(times) and abs(times[j] - t) <= AIS_EXPLAINS_WITHIN_S:
            return True
    return False


def _aspect_deg(cog_deg: float, bearing_from_station_deg: float) -> float:
    """Angle between the hull's heading and the station's line of sight."""
    return (cog_deg - bearing_from_station_deg + 180.0) % 360.0 - 180.0


def generate_radar_picture(world) -> RadarGenerationReport:
    """Walk the world's integrated truth and produce the radar picture.

    Deterministic for a given seed: every draw comes from `world.rng`, in a
    fixed order over vessels sorted by entity id.
    """
    rep = RadarGenerationReport()
    rng = world.rng
    pool = _TrackNumberPool()

    # ---- vessels -----------------------------------------------------
    for entity_id in sorted(world.tracks):
        v = world.vessels.get(entity_id)
        if v is None:
            continue
        points = _decimate(world.track_of(entity_id), REPORT_INTERVAL_S)
        if not points:
            continue
        ais_t = _ais_times(world, entity_id)
        vessel_plots: list[RadarPlot] = []
        for p in points:
            if not in_aoi(p.lat, p.lon):
                continue
            fluct = rng.gauss(0.0, RCS_FLUCTUATION_DB)
            view = best_station(
                p.lat, p.lon, v.length_m, p.t,
                aspect_of=lambda st, _p=p: _aspect_deg(
                    _p.cog_deg,
                    initial_bearing_deg(st.lat, st.lon, _p.lat, _p.lon)),
                fluctuation_db=fluct)
            if view is None or rng.random() > view.p:
                continue
            tid = pool.number_for(view.station.station_id, entity_id, p.t)
            la, lo, sig = measured_position(
                view.station, p.lat, p.lon, rng,
                range_km=view.range_km, bearing_deg=view.bearing_deg)
            vessel_plots.append(RadarPlot(
                station_id=view.station.station_id, radar_track_id=tid,
                ts=p.t, lat=la, lon=lo,
                # A tracker's speed and course come from differencing its own
                # noisy positions, so they are worse than the ship's own AIS
                # figures. Half a knot and five degrees is the right order.
                sog_kn=max(0.0, round(p.sog_kn + rng.gauss(0.0, 0.5), 2)),
                cog_deg=round((p.cog_deg + rng.gauss(0.0, 5.0)) % 360.0, 1),
                range_km=round(view.range_km, 3),
                bearing_deg=round(view.bearing_deg, 2),
                position_sigma_m=round(sig, 1),
                rcs_dbsm=round(view.rcs_dbsm, 2),
                snr_db=round(view.snr, 2),
                track_quality=int(max(1, min(100, round(
                    100.0 * view.p * (0.6 + 0.4 * min(view.snr / 20.0, 1.0)))))),
                truth_entity_id=entity_id, truth_kind="vessel"))

        if vessel_plots:
            rep.seen_vessels.add(entity_id)
            rep.plots.extend(vessel_plots)
            rep.n_vessel_plots += len(vessel_plots)
            _record_dark_episodes(rep, world, v, entity_id, vessel_plots, ais_t)
        else:
            rep.unseen_vessels.add(entity_id)

    # ---- fixed installations -----------------------------------------
    _generate_fixed(rep, world, pool, rng)

    # ---- sea clutter --------------------------------------------------
    _generate_clutter(rep, world, pool, rng)

    rep.plots.sort(key=lambda p: (p.ts, p.station_id, p.radar_track_id))
    rep.n_track_numbers = len({p.radar_track_id for p in rep.plots})
    return rep


# --------------------------------------------------------------------------
# ground truth
# --------------------------------------------------------------------------

def _record_dark_episodes(rep: RadarGenerationReport, world, v,
                          entity_id: str, plots: list[RadarPlot],
                          ais_t: list[float]) -> None:
    """Find the runs of plots that AIS does not explain, and record them.

    A run ends when an AIS report turns up, or when the radar itself loses
    contact for longer than a tracker would coast. The second condition matters:
    without it, a vessel seen off Porbandar on Monday and off Kochi on Friday
    would be one twelve-hundred-minute "episode" with a midpoint in open water
    that no produced contact could ever be matched against.
    """
    runs: list[list[RadarPlot]] = []
    cur: list[RadarPlot] = []
    for p in plots:
        unexplained = not _explained_by_ais(ais_t, p.ts.timestamp())
        broken = bool(cur) and (p.ts - cur[-1].ts).total_seconds() > TRACK_DROP_S
        if unexplained and not broken:
            cur.append(p)
            continue
        if cur:
            runs.append(cur)
        cur = [p] if unexplained else []
    if cur:
        runs.append(cur)

    for run in runs:
        dur_min = (run[-1].ts - run[0].ts).total_seconds() / 60.0
        if len(run) < DARK_MIN_PLOTS or dur_min < DARK_MIN_MINUTES:
            continue
        mid = run[len(run) // 2]
        # Why was she not on AIS?
        #
        # **Reception is checked before transmission history, and the ordering
        # is the whole correctness of this block.** The first version asked "did
        # this hull land any AIS at all?" before asking "could anyone have heard
        # her here?", and it mislabelled the Konkan coast wholesale: the
        # scenario AIS receiver network has five sites and none of them reaches
        # Vengurla or Mormugao, so an ordinary vessel transmitting perfectly
        # normally off Ratnagiri lands zero AIS rows for her entire voyage. The
        # old ordering called that `never_transmits` and marked it findable —
        # putting four blameless merchants into the answer key as dark vessels
        # a correct system was required to flag. That is the
        # out-of-coverage-is-not-dark anti-pattern (CLAUDE.md §6) written
        # directly into ground truth, which is the worst place for it: the
        # measurement would have rewarded a detector for making the error.
        cov = receiver_coverage(mid.lat, mid.lon)
        if not v.ais_expected:
            # No transponder fitted. Radar sees her; nothing else ever would.
            cause, explainable = CAUSE_NEVER_TRANSMITS, False
        elif cov <= AIS_RECEPTION_FLOOR:
            cause, explainable = CAUSE_OUT_OF_RECEPTION, True
        elif ais_t:
            cause, explainable = CAUSE_TRANSPONDER_OFF, False
        else:
            # Fitted, inside reception here, and never heard once anywhere in
            # eight weeks. Consistent with a transponder that stayed off; also
            # consistent with a voyage that spent every other hour outside
            # reception. We record which of the two we can see, and not a motive.
            cause, explainable = CAUSE_SILENT_THROUGHOUT, False

        # A hull that is *entitled* to be dark. See the field's docstring: the
        # product policy (DX7) is that a naval unit must never be flagged, so
        # the radar answer key inherits that policy rather than contradicting
        # it. `role` is a property of the cast, not of the answer key, and
        # nothing that decides anything can see it.
        unavoidable = (getattr(v, "role", "") == "decoy"
                       and not v.ais_expected)

        rep.truth.add(RadarDarkEpisode(
            episode_id="rdk_" + hashlib.sha1(
                f"{entity_id}|{run[0].ts.isoformat()}".encode()).hexdigest()[:12],
            entity_id=entity_id,
            t_start=run[0].ts, t_end=run[-1].ts,
            lat=round(mid.lat, 5), lon=round(mid.lon, 5),
            lat_min=round(min(p.lat for p in run), 5),
            lat_max=round(max(p.lat for p in run), 5),
            lon_min=round(min(p.lon for p in run), 5),
            lon_max=round(max(p.lon for p in run), 5),
            length_m=round(v.length_m, 1),
            cause=cause,
            n_plots=len(run), duration_min=round(dur_min, 1),
            station_ids=",".join(sorted({p.station_id for p in run})),
            explainable_by_coverage=explainable,
            unavoidable_false_positive=unavoidable,
            expected_detection=not (explainable or unavoidable),
            notes=("transmitting, but demonstrably outside terrestrial "
                   "reception — flagging this would be the "
                   "out-of-coverage-is-not-dark error" if explainable else
                   "legitimately operating without AIS; radar cannot tell, so "
                   "a contact here is a false positive no sensor-level rule "
                   "can prevent" if unavoidable else "")))


# --------------------------------------------------------------------------
# non-vessel returns
# --------------------------------------------------------------------------

def _generate_fixed(rep: RadarGenerationReport, world, pool, rng) -> None:
    """Single point moorings and light floats, reported forever.

    These exist to give the self-building static-object layer something to build
    from. It has never had anything: the SAR corpus holds six contacts in total,
    so the layer has been shipped and never exercised. Radar sees a mooring buoy
    every quarter of an hour for eight weeks, which is precisely the input it
    was designed for.
    """
    t = world.t0
    step = timedelta(seconds=FIXED_TARGET_INTERVAL_S)
    while t <= world.t1:
        for ft in FIXED_TARGETS:
            fluct = rng.gauss(0.0, RCS_FLUCTUATION_DB)
            view = best_station(ft.lat, ft.lon, ft.length_m, t,
                                fluctuation_db=fluct)
            if view is None or rng.random() > view.p:
                continue
            tid = pool.number_for(view.station.station_id, ft.target_id, t)
            la, lo, sig = measured_position(
                view.station, ft.lat, ft.lon, rng,
                range_km=view.range_km, bearing_deg=view.bearing_deg)
            rep.plots.append(RadarPlot(
                station_id=view.station.station_id, radar_track_id=tid,
                ts=t, lat=la, lon=lo,
                sog_kn=round(abs(rng.gauss(0.0, 0.15)), 2),
                cog_deg=round(rng.uniform(0.0, 360.0), 1),
                range_km=round(view.range_km, 3),
                bearing_deg=round(view.bearing_deg, 2),
                position_sigma_m=round(sig, 1),
                rcs_dbsm=round(view.rcs_dbsm, 2), snr_db=round(view.snr, 2),
                track_quality=int(max(1, min(100, round(90 * view.p)))),
                truth_entity_id=ft.target_id, truth_kind="fixed"))
            rep.n_fixed_plots += 1
        t += step


def _generate_clutter(rep: RadarGenerationReport, world, pool, rng) -> None:
    """Sea clutter and other false tracks.

    **The most important non-vessel thing in this file.** Every unexplained
    radar track is a candidate dark vessel, so clutter is the dominant
    false-positive source for this entire build. A simulator that left it out
    would measure a precision the sensor cannot deliver, and the number would go
    into a document.

    Clutter here is short-lived, weak and erratic: a return that persists for
    two to five reports, with a radar cross-section drawn low enough that most
    of them fail the size floor and some do not. Whether the filter cascade
    catches them is exactly what the measurement is for; nothing here is tuned
    so that it does.
    """
    total_s = (world.t1 - world.t0).total_seconds()
    n_reports = int(total_s / REPORT_INTERVAL_S)
    for st in STATIONS:
        for k in range(n_reports):
            if rng.random() > CLUTTER_RATE_PER_STATION_REPORT:
                continue
            t0 = world.t0 + timedelta(seconds=k * REPORT_INTERVAL_S)
            if not st.is_up(t0):
                continue
            # Somewhere inside the station's cover, weighted outward — clutter
            # is worst at long range where the resolution cell is largest.
            rng_km = st.max_range_km * math.sqrt(rng.uniform(0.05, 1.0))
            brg = rng.uniform(0.0, 360.0)
            if st.shadowed(brg):
                continue
            lat, lon = destination(st.lat, st.lon, brg, rng_km * 1000.0)
            if not in_aoi(lat, lon):
                continue
            # Equivalent length: lognormal-ish around 11 m with a tail. Most
            # clutter is below the 20 m dark floor; a minority is not, and that
            # minority is what an operator actually complains about.
            eq_len = max(4.0, min(60.0, math.exp(rng.gauss(math.log(11.0), 0.45))))
            n = rng.randint(CLUTTER_MIN_REPORTS, CLUTTER_MAX_REPORTS)
            tid = pool.number_for(st.station_id, f"clutter:{st.station_id}:{k}", t0)
            cog = rng.uniform(0.0, 360.0)
            sog = rng.uniform(*CLUTTER_SPEED_KN)
            cur_lat, cur_lon = lat, lon
            made = 0
            for i in range(n):
                t = t0 + timedelta(seconds=i * REPORT_INTERVAL_S)
                if t > world.t1:
                    break
                view = station_view(st, cur_lat, cur_lon, eq_len, t,
                                    fluctuation_db=rng.gauss(0.0, 3.0))
                if not view.visible:
                    break
                la, lo, sig = measured_position(
                    st, cur_lat, cur_lon, rng, range_km=view.range_km,
                    bearing_deg=view.bearing_deg)
                rep.plots.append(RadarPlot(
                    station_id=st.station_id, radar_track_id=tid,
                    ts=t, lat=la, lon=lo,
                    sog_kn=round(sog, 2), cog_deg=round(cog, 1),
                    range_km=round(view.range_km, 3),
                    bearing_deg=round(view.bearing_deg, 2),
                    position_sigma_m=round(sig, 1),
                    rcs_dbsm=round(view.rcs_dbsm, 2), snr_db=round(view.snr, 2),
                    # Clutter looks poor to the tracker, and honestly reporting
                    # that is not cheating: a real station does grade it low.
                    # The point is that low quality is not by itself enough to
                    # discard it, which is why the persistence gate exists.
                    track_quality=int(max(1, min(60, round(60 * view.p)))),
                    truth_entity_id=None, truth_kind="clutter"))
                made += 1
                # Erratic: clutter wanders, it does not steer.
                cog = (cog + rng.gauss(0.0, 40.0)) % 360.0
                cur_lat, cur_lon = destination(
                    cur_lat, cur_lon, cog, sog * 1852.0 * REPORT_INTERVAL_S / 3600.0)
            if made:
                rep.n_clutter_plots += made
                rep.n_clutter_tracks += 1


def format_report(rep: RadarGenerationReport) -> str:
    lines = ["radar picture (simulated coastal network — SYNTHETIC)"]
    c = rep.counts()
    for k in ("radar_plots", "radar_plots_vessel", "radar_plots_fixed",
              "radar_plots_clutter", "radar_tracks", "radar_clutter_tracks",
              "radar_vessels_seen", "radar_vessels_unseen",
              "radar_dark_episodes"):
        lines.append(f"  {k:<26}{c[k]:>10,}")
    s = rep.truth.summary()
    lines.append(f"  dark episodes expected to fire : {s['expected_to_fire']}")
    lines.append(f"  ... explainable by AIS coverage: "
                 f"{s['explainable_by_coverage']} (must NOT fire)")
    lines.append(f"  ... legitimately dark (naval)  : "
                 f"{s['unavoidable_false_positives']} (will fire anyway)")
    lines.append(f"  total dark hours on radar      : {s['total_dark_hours']}")
    lines.append(f"  by cause: {s['by_cause']}")
    lines.append("  SYNTHETIC. Every number above is a property of a simulated "
                 "radar network over simulated traffic.")
    return "\n".join(lines)
