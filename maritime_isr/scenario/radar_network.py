"""The simulated coastal radar network, and the physics it detects with.

**Every station coordinate is a real coastal place.** They are the towns,
headlands and harbour entrances along the Indian west coast where a coastal
surveillance station plausibly sits — the same discipline `geography.py` applies
to ports and the cable approach. They are **not** the positions of any actual
Indian Coast Guard installation: the Coastal Surveillance Network's site list is
not ours to publish and we do not have it. Anyone reading a coverage map drawn
from this file is reading a plausible network over real coastline, and the
docstrings say so at every level rather than once.

**The coverage is deliberately imperfect.** Three ways, and all three are
load-bearing:

  * *Range falls off with physics, not with a tuned curve.* Detection is decided
    by signal-to-noise, which goes as RCS / range⁴, against the radar horizon
    for the antenna and target heights. A small dhow disappears at 12 km and a
    laden VLCC is held to 45; nobody set those numbers, they fall out.
  * *There are holes.* Station spacing along this coast leaves a genuine gap
    between Mumbai and Ratnagiri, and each station carries shadow sectors where
    terrain blocks it. A picture with uniform circular coverage would flatter
    the system: everything unexplained would be dark, and the hardest question
    in the product — is this contact unexplained, or merely unobserved — would
    never be asked.
  * *One station goes down.* A maintenance outage puts a hole in a place that
    had coverage yesterday, which is the case an operator actually meets and the
    case a naive "no AIS here means dark" rule gets wrong.

Sea clutter produces false tracks. They are generated here rather than assumed
away, because an unexplained radar track is by construction a dark-vessel
candidate — so clutter is the dominant false-positive source for this whole
build, and a simulator without it would measure a precision the real sensor
could not deliver.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from ..ingest.radar import (SIGMA_BEARING_DEG, SIGMA_RANGE_M,
                            position_sigma_m,
                            rcs_dbsm_from_length)
from .geography import destination, haversine_m, initial_bearing_deg

__all__ = ["RadarStation", "STATIONS", "FIXED_TARGETS", "FixedTarget",
           "radar_horizon_km", "snr_db", "p_detect", "station_view",
           "best_station", "SYNTHETIC_STATION_PREFIX"]

#: Every station id starts with this. `graph.identity` reads it to decide that a
#: contact node minted from one of these tracks is scenario data — a fact about
#: the identifier space, not a peek at ground truth.
SYNTHETIC_STATION_PREFIX = "SYN-"

#: Report cadence, seconds. **A decimation, and a large one.** A real coastal
#: radar turns every 2-3 s and its tracker updates at that rate; a network of a
#: hundred of them forwarding every update is a firehose no laptop is going to
#: hold. What is modelled here is the *track report* an analytics layer over the
#: network would actually subscribe to. Five minutes is chosen as the coarsest
#: cadence at which the existing behavioural machinery still works — the
#: encounter detector resamples to five-minute grid steps — so the decimation
#: does not quietly disable the thing being tested.
REPORT_INTERVAL_S = 300.0

#: A station drops a track after this long without a detection, and the next
#: detection starts a new track number. Real trackers coast for a few scans and
#: then give up; at a five-minute report cadence this is three missed reports.
TRACK_DROP_S = 16 * 60.0

#: Antenna height above sea level, metres. A coastal radar tower.
ANTENNA_HEIGHT_M = 35.0

#: Detection threshold, dB, and the softness of it. Below the threshold the
#: probability of detection falls off rather than cutting to zero, because a
#: fluctuating target near threshold is detected on some scans and not others —
#: which is exactly what produces the fragmented tracks this build has to cope
#: with.
SNR_THRESHOLD_DB = 8.0
SNR_SOFTNESS_DB = 2.0

#: Radar-equation constant, chosen so a 5,000 m² target (a ~100 m merchant
#: broadside) at 25 km sits at 13 dB — comfortably detected, and gone by 45 km.
_SNR_K = 32.0

#: Per-look RCS fluctuation, dB, 1-σ. Swerling-like. This is what makes a single
#: plot's length estimate worth a size class and a long track's median worth
#: rather more, and the size gate downstream depends on the difference.
RCS_FLUCTUATION_DB = 4.0

#: Sea-clutter false tracks per station per report interval. Low, but not zero:
#: over eight weeks and sixteen stations it is the dominant source of contacts
#: that no AIS track will ever explain.
CLUTTER_RATE_PER_STATION_REPORT = 0.0016

#: How many reports a clutter track survives, and how fast it appears to move.
CLUTTER_MIN_REPORTS = 2
CLUTTER_MAX_REPORTS = 5
CLUTTER_SPEED_KN = (0.5, 26.0)


@dataclass(frozen=True)
class RadarStation:
    """One coastal surveillance station.

    `shadow_sectors` are (from_bearing, to_bearing) arcs in degrees true, swept
    clockwise, in which terrain blocks the beam. Real installations have them —
    a headland, an island, the town behind the tower — and they are the reason a
    coverage map is not a set of circles.
    """
    station_id: str
    name: str
    lat: float
    lon: float
    antenna_height_m: float = ANTENNA_HEIGHT_M
    #: Hard instrumented range: beyond this the set does not display, whatever
    #: the physics says.
    max_range_km: float = 55.0
    shadow_sectors: tuple[tuple[float, float], ...] = ()
    #: Maintenance outage, (start, end). Set on exactly one station, so the
    #: corpus contains a coverage hole that opens and closes in *time* rather
    #: than only in space.
    offline: tuple[datetime, datetime] | None = None

    def is_up(self, t: datetime) -> bool:
        return not (self.offline and self.offline[0] <= t < self.offline[1])

    def shadowed(self, bearing_deg: float) -> bool:
        b = bearing_deg % 360.0
        for lo, hi in self.shadow_sectors:
            lo, hi = lo % 360.0, hi % 360.0
            if lo <= hi:
                if lo <= b <= hi:
                    return True
            elif b >= lo or b <= hi:            # sector wraps through north
                return True
        return False


#: The network. Sixteen stations from Kachchh to Kochi, all inside AOI v1.
#:
#: Spacing is realistic and therefore uneven. Gujarat is dense because the crude
#: terminals are there; the long Konkan run between Mumbai and Ratnagiri is
#: nearly 200 km with one station in it, which leaves a genuine hole roughly 60
#: nm offshore of Dabhol. That hole is used by the deliberate-miss scenario and
#: it is *not* an accident to be tidied up later.
STATIONS: tuple[RadarStation, ...] = (
    RadarStation("SYN-JAK", "Jakhau",      23.220, 68.610),
    RadarStation("SYN-MUN", "Mundra",      22.740, 69.720,
                 # The Gulf of Kachchh closes to the north-east behind the
                 # station; nothing floats there to be seen.
                 shadow_sectors=((20.0, 140.0),)),
    RadarStation("SYN-OKH", "Okha",        22.470, 69.070),
    RadarStation("SYN-DWA", "Dwarka",      22.240, 68.960,
                 shadow_sectors=((30.0, 120.0),)),
    RadarStation("SYN-POR", "Porbandar",   21.630, 69.610,
                 shadow_sectors=((10.0, 100.0),)),
    RadarStation("SYN-VER", "Veraval",     20.900, 70.370,
                 shadow_sectors=((330.0, 80.0),)),
    RadarStation("SYN-DIU", "Diu",         20.710, 70.980,
                 shadow_sectors=((340.0, 70.0),)),
    RadarStation("SYN-DAH", "Dahanu",      19.980, 72.720,
                 shadow_sectors=((300.0, 60.0),)),
    RadarStation("SYN-MUM", "Mumbai",      18.880, 72.790,
                 # Prongs Reef looks west; the city and harbour are behind it.
                 max_range_km=60.0,
                 shadow_sectors=((330.0, 90.0),)),
    RadarStation("SYN-RAT", "Ratnagiri",   16.990, 73.280,
                 shadow_sectors=((320.0, 80.0),)),
    RadarStation("SYN-VEN", "Vengurla",    15.860, 73.620,
                 shadow_sectors=((330.0, 90.0),)),
    RadarStation("SYN-MOR", "Mormugao",    15.400, 73.780,
                 shadow_sectors=((340.0, 100.0),)),
    RadarStation("SYN-KAR", "Karwar",      14.810, 74.120,
                 shadow_sectors=((340.0, 110.0),)),
    RadarStation("SYN-MNG", "Mangalore",   12.870, 74.830,
                 shadow_sectors=((350.0, 110.0),)),
    RadarStation("SYN-KNR", "Kannur",      11.870, 75.360,
                 shadow_sectors=((350.0, 120.0),)),
    RadarStation("SYN-KOC", "Kochi",        9.970, 76.240,
                 shadow_sectors=((350.0, 130.0),)),
)

STATIONS_BY_ID = {s.station_id: s for s in STATIONS}


@dataclass(frozen=True)
class FixedTarget:
    """Something permanently there that is not a ship.

    The static-object layer in `fusion/dark.py` self-builds from unmatched
    contacts recurring in one place, and it has never had anything to build
    from: the SAR corpus holds six contacts. Radar sees a single point mooring
    every five minutes for eight weeks, which is what that layer was designed
    for and the first chance to find out whether it works.

    All four are real installations in these waters, positioned from published
    approximate offsets — a mooring buoy's exact coordinate is a chart datum we
    do not hold, and metre precision here would be false.
    """
    target_id: str
    name: str
    lat: float
    lon: float
    length_m: float
    kind: str


FIXED_TARGETS: tuple[FixedTarget, ...] = (
    FixedTarget("FIX-VAD-SPM1", "Vadinar SPM 1", 22.410, 69.610, 90.0, "spm"),
    FixedTarget("FIX-VAD-SPM2", "Vadinar SPM 2", 22.380, 69.560, 90.0, "spm"),
    FixedTarget("FIX-SIK-SPM",  "Sikka SPM",     22.430, 69.780, 85.0, "spm"),
    FixedTarget("FIX-MUM-LT",   "Prongs light float", 18.830, 72.700, 45.0,
                "light_float"),
)


# --------------------------------------------------------------------------
# physics
# --------------------------------------------------------------------------

def target_height_m(length_m: float) -> float:
    """Height of the reflecting structure above the waterline.

    Roughly a tenth of length for a merchant, floored at 3 m so a small craft
    still has a mast and a wheelhouse. It matters only through the radar
    horizon, which is a square-root term, so the approximation is well within
    what this simulation claims.
    """
    return max(3.0, 0.10 * length_m)


def radar_horizon_km(antenna_h_m: float, target_h_m: float) -> float:
    """Where the target drops below the radar horizon.

    The standard 4/3-earth approximation, `4.12·(√h_ant + √h_tgt)` in km for
    heights in metres. A 35 m tower sees a 30 m superstructure at 47 km and a
    3 m dhow at 31 km — before any question of whether there is enough signal.
    """
    return 4.12 * (math.sqrt(max(antenna_h_m, 0.0))
                   + math.sqrt(max(target_h_m, 0.0)))


def snr_db(rcs_dbsm: float, range_km: float) -> float:
    """Signal-to-noise for this echo at this range. RCS / R⁴, in decibels."""
    return rcs_dbsm - 40.0 * math.log10(max(range_km, 0.05)) + _SNR_K


def p_detect(snr: float) -> float:
    """Probability of detection on one report, from SNR.

    A logistic rather than a step, because a fluctuating target near threshold
    is seen on some scans and missed on others — which is what fragments tracks
    at the edge of coverage, and coping with fragmented tracks is half the work
    this build exists to do.
    """
    return 1.0 / (1.0 + math.exp(-(snr - SNR_THRESHOLD_DB) / SNR_SOFTNESS_DB))


@dataclass
class StationView:
    """What one station can say about one target right now."""
    station: RadarStation
    range_km: float
    bearing_deg: float
    snr: float
    p: float
    rcs_dbsm: float
    visible: bool
    #: Why not, when not — for the coverage accounting the measurement needs.
    reason: str = ""


def station_view(st: RadarStation, lat: float, lon: float, length_m: float,
                 t: datetime, *, aspect_deg: float | None = None,
                 fluctuation_db: float = 0.0) -> StationView:
    """Geometry and signal budget for one target seen from one station.

    `aspect_deg` is the angle between the target's heading and the line of sight.
    A ship seen broadside returns far more energy than one seen bow-on; the
    swing is several dB and it is why a vessel steering directly at a station
    can fade in and out while holding a steady course. Passing None skips the
    aspect term.
    """
    rng_m = haversine_m(st.lat, st.lon, lat, lon)
    rng_km = rng_m / 1000.0
    brg = initial_bearing_deg(st.lat, st.lon, lat, lon)
    rcs = rcs_dbsm_from_length(length_m) + fluctuation_db
    if aspect_deg is not None:
        # |sin(aspect)| peaks broadside. Clamped 6 dB below the peak so a bow-on
        # ship dims rather than vanishing — real hulls are not flat plates.
        rcs += 10.0 * math.log10(
            max(abs(math.sin(math.radians(aspect_deg))), 0.25))
    snr = snr_db(rcs, rng_km)
    p = p_detect(snr)

    if not st.is_up(t):
        return StationView(st, rng_km, brg, snr, 0.0, rcs, False, "station offline")
    if rng_km > st.max_range_km:
        return StationView(st, rng_km, brg, snr, 0.0, rcs, False,
                           "beyond instrumented range")
    if rng_km > radar_horizon_km(st.antenna_height_m, target_height_m(length_m)):
        return StationView(st, rng_km, brg, snr, 0.0, rcs, False,
                           "below radar horizon")
    if st.shadowed(brg):
        return StationView(st, rng_km, brg, snr, 0.0, rcs, False,
                           "terrain shadow")
    return StationView(st, rng_km, brg, snr, p, rcs, True, "")


def best_station(lat: float, lon: float, length_m: float, t: datetime, *,
                 aspect_of=None, fluctuation_db: float = 0.0
                 ) -> StationView | None:
    """The station with the strongest view of this target, or None.

    **One report per target per interval, from the best station.** The network
    feeds a central picture, so a target held by three stations appears once,
    not three times. The consequence is the interesting part: when the best
    station changes — the target passes from Veraval's cover into Diu's — the
    track number changes with it, and the fused picture contains two tracks for
    one ship. That fragmentation is real, it is the main reason radar↔AIS
    correlation is not trivial, and generating it away would have made this
    build measure an easier problem than the one it claims to solve.
    """
    best: StationView | None = None
    for st in STATIONS:
        # Cheap rectangular reject before the trigonometry. `station_view` costs
        # two great-circle computations and a logarithm, and this function is
        # called once per vessel per report interval for eight weeks — half a
        # million times, against sixteen stations. Fifteen of those sixteen are
        # hundreds of kilometres away every time.
        #
        # The margin is deliberately generous (1° ≈ 111 km of latitude, ≈ 105 km
        # of longitude at this latitude band) so the box can never clip a
        # station that the exact range check would have accepted.
        span_deg = st.max_range_km / 100.0
        if abs(lat - st.lat) > span_deg or abs(lon - st.lon) > span_deg:
            continue
        asp = aspect_of(st) if aspect_of is not None else None
        v = station_view(st, lat, lon, length_m, t, aspect_deg=asp,
                         fluctuation_db=fluctuation_db)
        if not v.visible:
            continue
        if best is None or v.snr > best.snr:
            best = v
    return best


def any_coverage(lat: float, lon: float, length_m: float, t: datetime) -> bool:
    """Could ANY station see a target of this size here, ignoring luck?

    Used by the truth ledger to separate "we missed it" from "nothing could have
    seen it". Uses the nominal RCS with no fluctuation and requires the detection
    probability to clear a half — a target the network sees one scan in ten is
    not in coverage in any useful sense.
    """
    v = best_station(lat, lon, length_m, t)
    return v is not None and v.p >= 0.5


def measured_position(st: RadarStation, lat: float, lon: float, rng, *,
                      range_km: float, bearing_deg: float
                      ) -> tuple[float, float, float]:
    """Where the station *reports* the target, and how good that is.

    Error is applied in the sensor's own frame — along range and across it —
    rather than as an isotropic blob in lat/lon, because that is where it
    physically lives. The cross-range term grows with range, so the same vessel
    is reported to ~30 m at 10 km and ~80 m at 50 km, and the correlation stage
    reads that number off the row instead of assuming a constant.
    """
    d_range = rng.gauss(0.0, SIGMA_RANGE_M)
    d_cross = rng.gauss(0.0, math.radians(SIGMA_BEARING_DEG)
                        * max(range_km, 0.1) * 1000.0)
    # Along the line of sight, then perpendicular to it.
    la, lo = destination(lat, lon, bearing_deg, d_range)
    la, lo = destination(la, lo, (bearing_deg + 90.0) % 360.0, d_cross)
    return la, lo, position_sigma_m(range_km)
