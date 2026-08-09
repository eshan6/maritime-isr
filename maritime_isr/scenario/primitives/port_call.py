"""port_call_primitive — approach, wait, berth, dwell, depart.

A port call is not "the vessel was at the port". It is a sequence with distinct
kinematic signatures, and the sequence is what several scenarios turn on:

  approach   — inbound transit, slowing on the run in
  anchorage  — station-keeping in the waiting area, hours to days
  berth      — the short, slow leg from anchorage to the terminal
  dwell      — alongside, moored, speed zero
  departure  — outbound transit, accelerating away

E4 (port-call laundering) is a *sequence* of these with particular origins and
durations; E5 (floating storage) is an anchorage leg that never becomes a berth;
the Mundra congestion decoy is a long anchorage leg with no berth available.
Generating a port call as a single teleport to a point would erase all three.

Anchorage positions are real waiting areas offset from the berths, so a vessel
waiting for Mundra sits where vessels waiting for Mundra actually sit — which
matters because distance-from-port is the feature that separates "anchorage
queue" from "open-water loiter", and it is a field the real corpus carries on
every event.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from ..geography import (ANCHORAGES, PORTS, haversine_m, initial_bearing_deg,
                         interpolate)
from .track import Leg, TrackPoint, VoyagePlan, generate_track


@dataclass
class PortCallSpec:
    """What the call was, as landed. Mirrors GFW's port-visit row shape."""
    port: str
    lat: float
    lon: float
    t_arrive: datetime
    t_depart: datetime
    anchorage_hours: float
    berth_hours: float

    @property
    def duration_h(self) -> float:
        return (self.t_depart - self.t_arrive).total_seconds() / 3600.0


def anchorage_of(port: str) -> tuple[float, float]:
    """The waiting area for a port, or the port itself if none is recorded."""
    return ANCHORAGES.get(port, PORTS[port])


def build_port_call(vessel, port: str, *, arrive_from: tuple[float, float],
                    t_start: datetime, rng, anchorage_hours: float,
                    berth_hours: float,
                    depart_to: tuple[float, float] | None = None,
                    skip_berth: bool = False,
                    initial_course_deg: float | None = None,
                    initial_sog_kn: float = 0.0,
                    ) -> tuple[list[TrackPoint], PortCallSpec]:
    """Build a complete call. `skip_berth` gives floating storage / congestion.

    `arrive_from` is where the vessel is coming from, so the approach bearing is
    a consequence of the voyage rather than an arbitrary choice — a tanker
    arriving at Sikka from the Gulf of Oman comes in on a different heading from
    one arriving from Kochi, and E4's laundering sequence depends on those
    origins being visible.
    """
    if port not in PORTS:
        raise ValueError(f"unknown port {port!r}")
    berth = PORTS[port]
    anch = anchorage_of(port)

    # Route the approach around the coast rather than straight at the anchorage.
    # A direct line from a previous call anywhere down the coast crosses the
    # Saurashtra peninsula, which is how half the corpus ended up on land.
    from ..searoute import sea_route
    legs = [Leg("transit", target=w, speed_kn=vessel.service_kn)
            for w in sea_route(arrive_from, anch)]
    legs += [
        Leg("transit", target=anch, speed_kn=vessel.service_kn),
        Leg("station", duration_h=max(anchorage_hours, 0.2), radius_m=700.0),
    ]
    if not skip_berth:
        legs += [
            # The run in to the berth is slow — pilotage speed, a few knots.
            Leg("transit", target=berth, speed_kn=min(6.0, vessel.service_kn)),
            Leg("moored", duration_h=max(berth_hours, 0.5)),
        ]

    # Departure: head back out along the corridor. When the corridor is not
    # needed the outbound leg still has to be a point *at sea*, and the earlier
    # version dead-reckoned 80 nm along a bearing measured from the berth and
    # then applied from the anchorage — two different origins, so the ray was
    # not the line anything had checked. From the Gulf of Kutch it pointed north
    # and put a fifth of the commercial fleet 150 km into the Rann of Kutch,
    # steaming inland for a day and back. It was invisible to the routing fix
    # because routing can only make a *passage* avoid land; it cannot rescue a
    # destination that is on land.
    #
    # An empty `sea_route` means the direct anchorage-to-origin line has been
    # verified clear, so a point on that line is at sea by construction. Take
    # 80 nm along it, or the origin itself if that is nearer.
    if depart_to is not None:
        out_target = depart_to
    else:
        outbound = sea_route(anch, arrive_from)
        if outbound:
            out_target = outbound[0]
        else:
            d = haversine_m(*anch, *arrive_from)
            out_target = interpolate(
                *anch, *arrive_from,
                min(1.0, 80.0 * 1852.0 / max(d, 1.0)))
    legs.append(Leg("transit", target=out_target, speed_kn=vessel.service_kn))

    plan = VoyagePlan(
        start=arrive_from, start_time=t_start,
        # Aim at the FIRST leg, which is now a corridor waypoint when the
        # approach is routed — aiming at the anchorage made the vessel open on a
        # heading it then had to turn off.
        initial_course_deg=(initial_course_deg if initial_course_deg is not None
                            else initial_bearing_deg(
                                *arrive_from, *(legs[0].target or anch))),
        initial_sog_kn=initial_sog_kn,
        legs=legs,
    )
    points = generate_track(vessel, plan, rng)
    if not points:
        raise ValueError(f"port call at {port} produced no track")

    # Arrival and departure are measured from the track, not assumed: the
    # vessel arrives when it actually gets there.
    inside = [p for p in points if _near(p, anch, 25_000.0)
              or _near(p, berth, 25_000.0)]
    t_arrive = inside[0].t if inside else points[0].t
    t_depart = inside[-1].t if inside else points[-1].t

    spec = PortCallSpec(
        port=port, lat=berth[0], lon=berth[1],
        t_arrive=t_arrive, t_depart=t_depart,
        anchorage_hours=anchorage_hours,
        berth_hours=0.0 if skip_berth else berth_hours,
    )
    return points, spec


def _near(p: TrackPoint, pos: tuple[float, float], radius_m: float) -> bool:
    from ..geography import haversine_m
    return haversine_m(p.lat, p.lon, *pos) <= radius_m


def build_anchorage_stay(vessel, port: str, *, arrive_from: tuple[float, float],
                         t_start: datetime, rng, hours: float,
                         radius_m: float = 900.0
                         ) -> tuple[list[TrackPoint], PortCallSpec]:
    """A long stay at anchor with no berth — floating storage, or congestion.

    Deliberately ambiguous by construction, because the real thing is. Six
    tankers sitting off Gujarat for a month is both ordinary commercial
    behaviour and a documented sanctions-evasion pattern, and a corpus that made
    one of those look different from the other would be teaching the system
    something false.
    """
    return build_port_call(vessel, port, arrive_from=arrive_from,
                           t_start=t_start, rng=rng,
                           anchorage_hours=hours, berth_hours=0.0,
                           skip_berth=True)


def transit_between(vessel, a: tuple[float, float], b: tuple[float, float],
                    t_start: datetime, rng, *, speed_kn: float | None = None,
                    via: list[tuple[float, float]] | None = None
                    ) -> list[TrackPoint]:
    """A plain passage, optionally routed through waypoints."""
    from ..searoute import sea_route
    # An explicit `via` wins — a scenario that routes a vessel deliberately is
    # making a point. Otherwise follow the coastal corridor.
    waypoints = via if via is not None else sea_route(a, b)
    legs = [Leg("transit", target=w, speed_kn=speed_kn or vessel.service_kn)
            for w in waypoints]
    legs.append(Leg("transit", target=b, speed_kn=speed_kn or vessel.service_kn))
    plan = VoyagePlan(start=a, start_time=t_start,
                      initial_course_deg=initial_bearing_deg(
                          *a, *(waypoints[0] if waypoints else b)),
                      legs=legs)
    return generate_track(vessel, plan, rng)


def sequence_ports(vessel, ports: list[str], t_start: datetime, rng, *,
                   start_from: tuple[float, float],
                   dwell_hours: list[float] | None = None,
                   wait_hours: list[float] | None = None
                   ) -> tuple[list[TrackPoint], list[PortCallSpec]]:
    """Chain several calls — the primitive E4's laundering sequence is built on.

    The signal in E4 is the *order* of the calls, not any one of them: a
    high-risk terminal, then two brief clean intermediate calls, then the
    destination. Each individual call is unremarkable, which is the point.
    """
    pts: list[TrackPoint] = []
    specs: list[PortCallSpec] = []
    pos = start_from
    t = t_start
    # Course and speed carry across call boundaries. Without this the vessel
    # came to a dead stop and snapped onto a new heading between every pair of
    # calls — a 165-degree course change inside one 60 s step on a hull limited
    # to 0.25 deg/s.
    cog: float | None = None
    sog = 0.0
    for i, port in enumerate(ports):
        dwell = (dwell_hours[i] if dwell_hours and i < len(dwell_hours)
                 else 20.0)
        wait = (wait_hours[i] if wait_hours and i < len(wait_hours) else 6.0)
        leg, spec = build_port_call(vessel, port, arrive_from=pos, t_start=t,
                                    rng=rng, anchorage_hours=wait,
                                    berth_hours=dwell,
                                    initial_course_deg=cog, initial_sog_kn=sog)
        pts += leg
        specs.append(spec)
        pos = (leg[-1].lat, leg[-1].lon)
        t = leg[-1].t
        cog, sog = leg[-1].cog_deg, leg[-1].sog_kn
    return pts, specs
