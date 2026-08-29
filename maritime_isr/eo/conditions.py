"""Light and weather at a position and a moment — the half of an imaging
opportunity that has nothing to do with geometry.

The requirement asks the system to decide automatically *which* track to point a
camera at, and the brief names the inputs: rank, range and bearing, **adequate
light and weather**, recency, and whether the target is about to become
unobservable. This module supplies the light and the weather.

**Light is computed, not looked up.** Solar elevation from a position and a
timestamp is arithmetic — declination, equation of time, hour angle — so it
needs no data source, no network call and no configuration. That matters because
it is the term that halves the usable day, and a term that large should not
depend on a file somebody has to remember to update.

**Weather is a stand-in, and the seam is named.** Every station in the Coastal
Surveillance Network carries meteorological equipment; a deployment reads
visibility off that sensor. This system has no such feed, so visibility here is
a deterministic function of station and hour with a monsoon-season distribution
behind it — stable across runs at a fixed corpus, never random at read time.
:func:`visibility_km` is the one place that would be replaced by a met
connector, and its docstring says so rather than the fact being buried.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime, timezone

__all__ = ["Illumination", "solar_elevation_deg", "illumination",
           "visibility_km", "BAND_VISIBLE", "BAND_THERMAL",
           "CIVIL_TWILIGHT_DEG", "FULL_DAYLIGHT_DEG"]

#: The two bands a coastal EO head realistically carries. They are not
#: interchangeable and the difference is load-bearing downstream: a thermal
#: image gives a silhouette — length, slenderness, roughly where the
#: superstructure sits — and does **not** give deck detail. Whether a hull has
#: cranes on her deck is precisely what separates a tanker from a bulker, so a
#: night image cannot support that distinction and the classifier is told the
#: band so it does not pretend otherwise.
BAND_VISIBLE = "visible"
BAND_THERMAL = "thermal"

#: Sun elevation above which the visible channel is working normally.
FULL_DAYLIGHT_DEG = 5.0
#: Civil twilight. Below this there is not enough light for a useful visible
#: image and the head switches to thermal.
CIVIL_TWILIGHT_DEG = -6.0

#: Visibility, kilometres, at the extremes of the modelled distribution.
#: The corpus window is 4 June to 25 July, which is the south-west monsoon on
#: this coast: haze, rain squalls and a genuinely poor median. A model that gave
#: every hour 30 km of visibility would flatter the cueing scheduler by removing
#: the constraint the requirement explicitly asks it to weigh.
VISIBILITY_CLEAR_KM = 32.0
VISIBILITY_POOR_KM = 4.0
#: Fraction of hours in the poor tail. Monsoon coast; a judgement, stated.
VISIBILITY_POOR_FRACTION = 0.22


def _stable01(*parts) -> float:
    """A deterministic pseudo-random float in [0,1) from the parts given.

    Not `random`, and not `hash()`: Python's string hash is salted per process,
    so a corpus regenerated at a fixed seed would not be byte-identical between
    runs. The scenario generator learned this the hard way (`scenario/land.py`)
    and the same rule applies to anything whose output lands on disk.
    """
    h = hashlib.sha256("|".join(str(p) for p in parts).encode()).digest()
    return int.from_bytes(h[:8], "big") / float(1 << 64)


def solar_elevation_deg(lat: float, lon: float, when: datetime) -> float:
    """Sun elevation above the horizon, degrees, at a position and a moment.

    The standard low-precision solar position: declination from the day of
    year, the equation of time as a two-term Fourier fit, then the hour angle.
    Accurate to well under a degree, which is far inside anything this module
    claims — the only decisions it feeds are "is the visible channel working"
    and "how much light is there", and both are graded over several degrees.
    """
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    when = when.astimezone(timezone.utc)
    n = when.timetuple().tm_yday + (when.hour + when.minute / 60.0
                                    + when.second / 3600.0) / 24.0
    decl = -23.44 * math.cos(math.radians(360.0 / 365.0 * (n + 10.0)))
    b = math.radians(360.0 * (n - 81.0) / 365.0)
    eot_min = 9.87 * math.sin(2 * b) - 7.53 * math.cos(b) - 1.5 * math.sin(b)
    utc_hours = when.hour + when.minute / 60.0 + when.second / 3600.0
    solar_hours = utc_hours + lon / 15.0 + eot_min / 60.0
    ha = math.radians(15.0 * (solar_hours - 12.0))
    sin_el = (math.sin(math.radians(lat)) * math.sin(math.radians(decl))
              + math.cos(math.radians(lat)) * math.cos(math.radians(decl))
              * math.cos(ha))
    return math.degrees(math.asin(max(-1.0, min(1.0, sin_el))))


def visibility_km(station_id: str, when: datetime) -> float:
    """Horizontal visibility at a station, kilometres.

    **This is where a meteorological connector belongs and there is not one.**
    Every station in the requirement's network carries met equipment; a
    deployment reads visibility, sea state and precipitation off it and this
    function becomes a lookup. Until then it is a deterministic draw per station
    per three-hour block, with a monsoon-season distribution: about a fifth of
    blocks are poor, the rest run from moderate to clear. Deterministic so that
    a corpus regenerated at a fixed seed produces the same cueing plan — a
    scheduler whose decisions change on re-read cannot be argued with.

    Three-hour blocks rather than per-hour because weather is autocorrelated:
    visibility that changed independently every hour would let a target be
    unobservable at 09:00, fine at 10:00 and unobservable again at 11:00, which
    would make the "about to become unobservable" term meaningless noise.
    """
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    when = when.astimezone(timezone.utc)
    block = when.strftime("%Y-%m-%d") + f":{when.hour // 3}"
    u = _stable01("eo_visibility", station_id, block)
    if u < VISIBILITY_POOR_FRACTION:
        # Poor tail: a squall or thick haze. Scaled inside the tail so it is a
        # spread rather than a single bad value.
        frac = u / VISIBILITY_POOR_FRACTION
        return VISIBILITY_POOR_KM + frac * (12.0 - VISIBILITY_POOR_KM)
    frac = (u - VISIBILITY_POOR_FRACTION) / (1.0 - VISIBILITY_POOR_FRACTION)
    return 12.0 + frac * (VISIBILITY_CLEAR_KM - 12.0)


@dataclass(frozen=True)
class Illumination:
    """What the light is doing at a place and a time, and which band follows."""
    solar_elevation_deg: float
    band: str
    #: A [0,1] scaling on image quality from light alone. Full daylight is 1.0;
    #: twilight is degraded because the visible channel is starved; night is the
    #: thermal channel, which works but resolves less.
    light_factor: float
    label: str

    @property
    def is_daylight(self) -> bool:
        return self.solar_elevation_deg >= FULL_DAYLIGHT_DEG

    def as_dict(self) -> dict:
        return {"solar_elevation_deg": round(self.solar_elevation_deg, 2),
                "band": self.band,
                "light_factor": round(self.light_factor, 3),
                "label": self.label}


#: Quality retained on the thermal channel relative to a good visible image.
#: A cooled thermal head at coastal-surveillance grade resolves a hull's outline
#: and roughly where her superstructure is; it does not resolve deck fittings.
#: The number is a working figure and it is here so it can be argued with.
THERMAL_QUALITY_FACTOR = 0.65
#: Twilight: the visible channel still works but is starved.
TWILIGHT_QUALITY_FACTOR = 0.55


def illumination(lat: float, lon: float, when: datetime) -> Illumination:
    """Band and light factor at a position and a moment."""
    el = solar_elevation_deg(lat, lon, when)
    if el >= FULL_DAYLIGHT_DEG:
        return Illumination(el, BAND_VISIBLE, 1.0, "daylight")
    if el >= CIVIL_TWILIGHT_DEG:
        # Ramp across twilight rather than stepping: an image taken at -5° and
        # one taken at +4° are not the same picture, and a step would put a
        # cliff into the cueing score exactly where the scheduler is deciding
        # whether a target is "about to become unobservable".
        span = FULL_DAYLIGHT_DEG - CIVIL_TWILIGHT_DEG
        frac = (el - CIVIL_TWILIGHT_DEG) / span
        return Illumination(
            el, BAND_VISIBLE,
            TWILIGHT_QUALITY_FACTOR + frac * (1.0 - TWILIGHT_QUALITY_FACTOR),
            "twilight")
    return Illumination(el, BAND_THERMAL, THERMAL_QUALITY_FACTOR, "night")
