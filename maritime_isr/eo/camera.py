"""Where the cameras are, what each one can see, and how good the picture is.

The requirement puts an electro-optical camera at every radar station, so that
is where these sit: **co-located with the radar, on the same tower, behind the
same headland.** That is not a modelling convenience — it decides the shape of
the whole area. A camera can only be pointed at something the station already
holds, its terrain shadows are the radar's terrain shadows, and its useful range
is much shorter than the radar's, which is exactly why cueing has to choose.

**These are not the positions of any real installation.** They inherit the
scenario radar network's coordinates, which are plausible coastal sites and are
documented in `scenario/radar_network.py` as explicitly not the Coastal
Surveillance Network's site list. Anything built on them is flagged synthetic
and says so, the same posture `assistant/recommend.py` takes.

The quality model
-----------------
An image is useful for classification when the hull covers enough pixels and
the atmosphere has not eaten the contrast. Both are physics rather than taste:

* **Pixels on target** falls as ``length x |sin(aspect)| / range``. A ship seen
  bow-on presents a fraction of her length, which is why aspect is carried on
  every capture and why the classifier is allowed to refuse a head-on look.
* **Contrast** falls exponentially with range over visibility — the same
  extinction that makes a coastline vanish in haze.
* **Light** scales both, and at night the band changes (`conditions`).

The product of the three is the ``quality`` on a view, and it is the number the
scheduler trades against suspicion. A camera that can technically see a target
but would return twenty pixels of grey is not an imaging opportunity.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional, Sequence

from .conditions import BAND_THERMAL, Illumination, illumination, visibility_km

__all__ = ["EOCamera", "CameraView", "default_camera_network",
           "cameras_for_stations", "view", "MIN_CAPTURE_QUALITY",
           "DAY_RANGE_KM", "NIGHT_RANGE_KM"]


#: Useful range of a coastal EO head against a merchant hull, daylight, km.
#:
#: A judgement with a reason, matching `assistant.recommend.EO_USEFUL_RANGE_KM`
#: deliberately — the assistant tells an operator "a camera can reach her" and
#: this module decides whether to task one, and the two disagreeing would be a
#: product that recommends an action it then declines to take. A stabilised
#: long-range camera on a 30 m tower holds a large ship out to the horizon; the
#: image stops being good enough to *classify* well before that, and
#: classification is the point of pointing one.
DAY_RANGE_KM = 20.0

#: Useful range on the thermal channel. Shorter, because a thermal head at this
#: grade has coarser angular resolution than the visible channel it shares a
#: mount with.
NIGHT_RANGE_KM = 9.0

#: Angular resolution, milliradians per pixel. 0.1 mrad/px puts a 100 m hull at
#: 20 km on about 50 pixels — which is roughly where hull classification from a
#: silhouette stops working, and is why `DAY_RANGE_KM` is 20 rather than 40.
MRAD_PER_PIXEL = 0.10
#: Thermal channel, coarser.
MRAD_PER_PIXEL_THERMAL = 0.25

#: Pixels along the hull below which there is nothing to classify, and above
#: which more pixels stop helping.
PX_FLOOR = 18.0
PX_GOOD = 160.0

#: Atmospheric extinction coefficient against visibility. Koschmieder's law puts
#: 2% contrast at exactly one visibility distance, which is the *detection*
#: limit for a black object; a classification-grade image degrades faster than a
#: bare detection but not by the full Koschmieder constant, so 1.6 is used and
#: named rather than a coefficient chosen to make the numbers look good.
EXTINCTION_K = 1.6

#: Below this quality a capture is not worth the slew. Set from what the
#: classifier needs rather than from taste: `classify.MIN_CLASSIFY_QUALITY` is
#: 0.35, and a scheduler that tasked cameras onto images no model would accept
#: would report a busy network and an empty library.
MIN_CAPTURE_QUALITY = 0.20

#: Camera height above sea level, metres. Below the radar antenna on the same
#: tower — the optical head sits under the array.
CAMERA_HEIGHT_M = 30.0

#: How fast the head slews, degrees per second, and how long it must dwell to
#: settle and take a usable frame. Both bind only at short slot lengths, which
#: is exactly when a scheduler stops being a ranked list and starts being a
#: schedule.
SLEW_RATE_DEG_S = 12.0
MIN_DWELL_S = 45.0


def _hav_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def angular_difference(a: float, b: float) -> float:
    """Smallest angle between two bearings, degrees, in [0,180]."""
    return abs((a - b + 180.0) % 360.0 - 180.0)


@dataclass(frozen=True)
class EOCamera:
    """One electro-optical head, on one coastal tower.

    ``masked_sectors`` are (from, to) arcs in degrees true swept clockwise in
    which the view is blocked. They are the *radar's* shadow sectors, because
    the headland that blocks the array blocks the lens under it — inheriting
    them rather than inventing a second set is what keeps the camera's coverage
    map honest against the radar's.
    """
    camera_id: str
    station_id: str
    name: str
    lat: float
    lon: float
    height_m: float = CAMERA_HEIGHT_M
    day_range_km: float = DAY_RANGE_KM
    night_range_km: float = NIGHT_RANGE_KM
    masked_sectors: tuple[tuple[float, float], ...] = ()
    slew_rate_deg_s: float = SLEW_RATE_DEG_S
    min_dwell_s: float = MIN_DWELL_S
    offline: Optional[tuple[datetime, datetime]] = None
    #: Every camera in this build is simulated. The flag travels onto every
    #: capture and every tasking so the synthetic marker is on the surface and
    #: not merely in the database (the Section-3 brief's standing caution).
    is_synthetic: bool = True

    def is_up(self, t: datetime) -> bool:
        return not (self.offline and self.offline[0] <= t < self.offline[1])

    def masked(self, bearing_deg: float) -> bool:
        b = bearing_deg % 360.0
        for lo, hi in self.masked_sectors:
            lo, hi = lo % 360.0, hi % 360.0
            if lo <= hi:
                if lo <= b <= hi:
                    return True
            elif b >= lo or b <= hi:
                return True
        return False

    def range_limit_km(self, band: str) -> float:
        return self.night_range_km if band == BAND_THERMAL else self.day_range_km


@dataclass(frozen=True)
class CameraView:
    """What one camera could get of one target right now, and why not if not."""
    camera: EOCamera
    range_km: float
    bearing_deg: float
    #: Angle between the target's heading and the line of sight, 0-180. 90 is
    #: broadside and is the look worth having.
    aspect_deg: Optional[float]
    band: str
    illumination: Illumination
    visibility_km: float
    pixels_on_target: float
    quality: float
    observable: bool
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "camera_id": self.camera.camera_id,
            "station_id": self.camera.station_id,
            "station": self.camera.name,
            "range_km": round(self.range_km, 2),
            "bearing_deg": round(self.bearing_deg, 1),
            "aspect_deg": (None if self.aspect_deg is None
                           else round(self.aspect_deg, 1)),
            "band": self.band,
            "illumination": self.illumination.as_dict(),
            "visibility_km": round(self.visibility_km, 1),
            "pixels_on_target": round(self.pixels_on_target, 1),
            "quality": round(self.quality, 3),
            "observable": self.observable,
            "reason": self.reason,
            "is_synthetic": self.camera.is_synthetic,
        }


def cameras_for_stations(stations: Iterable, *, is_synthetic: bool = True
                         ) -> tuple[EOCamera, ...]:
    """One camera per station, inheriting position, mast and terrain shadows.

    Takes any station-shaped object with ``station_id``, ``name``, ``lat``,
    ``lon``, ``shadow_sectors`` and ``offline`` — so a deployment supplies its
    own network without editing this module, which is the same connector
    posture the rest of the system takes toward its sources.
    """
    out = []
    for st in stations:
        out.append(EOCamera(
            camera_id=f"EO-{st.station_id}",
            station_id=st.station_id,
            name=st.name,
            lat=st.lat, lon=st.lon,
            masked_sectors=tuple(getattr(st, "shadow_sectors", ()) or ()),
            offline=getattr(st, "offline", None),
            is_synthetic=is_synthetic))
    return tuple(out)


def default_camera_network() -> tuple[EOCamera, ...]:
    """The sixteen cameras this build has, one per simulated radar station.

    Imported lazily and from the scenario package on purpose: this is the only
    station network that exists here, it is explicitly not anybody's real site
    list, and every object built from it carries ``is_synthetic=True`` so the
    fact travels rather than being remembered.
    """
    from ..scenario.radar_network import STATIONS
    return cameras_for_stations(STATIONS, is_synthetic=True)


def _pixels(length_m: float, range_km: float, aspect_deg: Optional[float],
            band: str) -> float:
    """Pixels along the hull as the camera sees her, after foreshortening."""
    mrad = MRAD_PER_PIXEL_THERMAL if band == BAND_THERMAL else MRAD_PER_PIXEL
    # A ship seen bow-on presents a fraction of her length. Floored at 0.12
    # rather than 0: a hull end-on still shows her bridge and her beam, so the
    # image does not literally vanish — it stops carrying length, which is the
    # classifier's problem and is handled there rather than by pretending the
    # camera saw nothing.
    foreshorten = (1.0 if aspect_deg is None
                   else max(abs(math.sin(math.radians(aspect_deg))), 0.12))
    apparent_m = max(float(length_m or 0.0), 1.0) * foreshorten
    range_m = max(range_km, 0.05) * 1000.0
    return apparent_m / (range_m * mrad / 1000.0)


def _size_quality(px: float) -> float:
    if px <= PX_FLOOR:
        return 0.0
    if px >= PX_GOOD:
        return 1.0
    return (math.log(px) - math.log(PX_FLOOR)) / (math.log(PX_GOOD)
                                                  - math.log(PX_FLOOR))


def view(camera: EOCamera, *, lat: float, lon: float, when: datetime,
         length_m: Optional[float], heading_deg: Optional[float] = None
         ) -> CameraView:
    """Geometry, light, weather and expected image quality for one look.

    Returns a view whether or not it is observable — a refusal with a reason is
    what lets the cueing scheduler explain why a target was *not* tasked, which
    is half of what makes the automation trustworthy (the suppression discipline
    of ADR-028 and ADR-031).
    """
    rng_km = _hav_km(camera.lat, camera.lon, lat, lon)
    brg = _bearing_deg(camera.lat, camera.lon, lat, lon)
    ill = illumination(lat, lon, when)
    vis = visibility_km(camera.station_id, when)
    aspect = (None if heading_deg is None
              else angular_difference(heading_deg, brg))
    px = _pixels(length_m or 0.0, rng_km, aspect, ill.band)

    atmos = math.exp(-EXTINCTION_K * rng_km / max(vis, 0.5))
    quality = max(0.0, min(1.0, _size_quality(px) * atmos * ill.light_factor))

    def _no(reason: str) -> CameraView:
        return CameraView(camera, rng_km, brg, aspect, ill.band, ill, vis, px,
                          0.0, False, reason)

    if not camera.is_up(when):
        return _no("camera offline")
    limit = camera.range_limit_km(ill.band)
    if rng_km > limit:
        return _no(f"{rng_km:.1f} km, beyond {ill.band} useful range "
                   f"{limit:.0f} km")
    if camera.masked(brg):
        return _no(f"bearing {brg:.0f}° is inside a terrain-masked sector")
    if quality < MIN_CAPTURE_QUALITY:
        return _no(f"image would be worth {quality:.2f} — "
                   f"{px:.0f} px on the hull in {vis:.0f} km visibility, "
                   f"{ill.label}")
    return CameraView(camera, rng_km, brg, aspect, ill.band, ill, vis, px,
                      quality, True, "")


def best_view(cameras: Sequence[EOCamera], *, lat: float, lon: float,
              when: datetime, length_m: Optional[float],
              heading_deg: Optional[float] = None) -> Optional[CameraView]:
    """The best observable view of a target across a network, or None.

    Used for the look-ahead that answers "is she about to become unobservable" —
    the assignment itself never uses this, because collapsing the network to its
    best camera before assigning is exactly the greedy move that double-books
    one camera and leaves the rest idle (CLAUDE.md §6, one domain along).
    """
    best: Optional[CameraView] = None
    for cam in cameras:
        # Cheap rectangular reject before the trigonometry, as in
        # `radar_network.best_station`: this runs once per candidate per camera
        # per slot and fifteen of sixteen cameras are hundreds of km away.
        span = (cam.day_range_km / 100.0) + 0.05
        if abs(lat - cam.lat) > span or abs(lon - cam.lon) > span:
            continue
        v = view(cam, lat=lat, lon=lon, when=when, length_m=length_m,
                 heading_deg=heading_deg)
        if not v.observable:
            continue
        if best is None or v.quality > best.quality:
            best = v
    return best
