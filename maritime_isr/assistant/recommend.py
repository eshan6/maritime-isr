"""What the watchkeeper should do next, tied to the factor that motivated it.

*"Then the part that makes it an assistant rather than a report: recommended
next actions."* — the Section-3 brief, Area 1.

The easy version of this feature is a lookup table from factor kind to a verb,
rendered confidently. It would be worse than nothing. Two things stop that here:

**Feasibility is computed, not asserted.** "Call her on VHF" is not advice if
she is 300 km beyond the nearest station, and "cue a camera" is not advice if no
camera can see that far. Range is worked out from the station network's real
geometry using the same horizon function the radar model uses, and an infeasible
action is returned *with its reason* rather than suppressed — an operator who
learns that the system knows why an option is unavailable trusts the options it
does offer.

**Capability is stated plainly.** Several of these actions belong to areas of
the Section-3 brief that are not built: the automatic EO cueing loop is Area 5,
arrival-notification ingestion is Area 4, radio is Area 6. Each recommendation
therefore carries ``performed_by`` and ``system_capability``, and the honest
answer for most of them today is "this is an instruction to a human; the system
cannot carry it out". Presenting an unbuilt capability as if the system had it
is the one failure this project treats as cardinal (CLAUDE.md §5).

**Nothing here is autonomous.** The system proposes; the human decides. There is
no path from this module to an action.
"""
from __future__ import annotations

import math
from typing import Optional, Sequence

from .catalog import spec
from .model import Factor, Recommendation

__all__ = ["recommend", "ACTIONS", "nearest_station"]


#: Useful range of a coastal electro-optical camera against a merchant hull, km.
#:
#: A judgement with a reason, not a measured figure: a stabilised long-range
#: coastal camera on a 30 m tower can hold a large ship out to the horizon
#: (~25 km), but the image stops being good enough to *classify* a hull type
#: well before that, and classification is the point of cueing one. 20 km is a
#: conservative working figure and it is here rather than buried in a function
#: so it can be argued with. There is no real camera in this system; see
#: `system_capability` on the recommendation.
EO_USEFUL_RANGE_KM = 20.0

#: Height of a ship's VHF antenna above the waterline, metres. Sets the far side
#: of the line-of-sight calculation.
VHF_SHIP_ANTENNA_M = 15.0


def _hav_km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def nearest_station(lat: Optional[float], lon: Optional[float]) -> Optional[dict]:
    """The closest coastal station to a position, with its ranges.

    Reads the scenario station network, which is the only one that exists.
    Everything it returns is flagged synthetic and any recommendation built on
    it says so — a coverage claim is the most persuasive thing this system can
    make and there is no real Coastal Surveillance Network behind it.
    """
    if lat is None or lon is None:
        return None
    try:
        from ..scenario.radar_network import (STATIONS, radar_horizon_km,
                                              target_height_m)
    except Exception:                                            # noqa: BLE001
        return None
    best = None
    for s in STATIONS:
        d = _hav_km(lat, lon, s.lat, s.lon)
        if best is None or d < best[0]:
            best = (d, s)
    if best is None:
        return None
    d, s = best
    vhf = radar_horizon_km(s.antenna_height_m, VHF_SHIP_ANTENNA_M)
    return {
        "station_id": s.station_id, "name": s.name,
        "distance_km": round(d, 1),
        "eo_range_km": EO_USEFUL_RANGE_KM,
        "vhf_range_km": round(vhf, 1),
        "radar_range_km": round(min(
            s.max_range_km,
            radar_horizon_km(s.antenna_height_m, target_height_m(100.0))), 1),
        "is_synthetic": True,
    }


#: Every action the assistant can propose. `performed_by` and `capability` are
#: the honesty fields; `base_priority` orders the queue before feasibility.
ACTIONS: dict[str, dict] = {
    "call_vhf": dict(
        base_priority=90, performed_by="operator",
        headline="Call her on VHF and ask her to identify",
        capability="Not built. Radio is Area 6 of the Section-3 brief; this "
                   "system holds no VHF audio and cannot transcribe a reply."),
    "cue_eo_camera": dict(
        base_priority=85, performed_by="operator",
        headline="Point a camera at her and get an image",
        capability="Not built. Automatic EO cueing and image classification "
                   "are Area 5 of the Section-3 brief; today a watchkeeper "
                   "slews the camera by hand."),
    "dispatch_patrol": dict(
        base_priority=70, performed_by="operator",
        headline="Consider tasking a surface unit",
        capability="Outside this system entirely — an operational decision "
                   "for the control room, offered as context, not advice."),
    "escalate": dict(
        base_priority=80, performed_by="operator",
        headline="Escalate to the regional control room",
        capability="Outside this system. The one-click incident report "
                   "exports everything on this page for the handover."),
    "check_registry": dict(
        base_priority=60, performed_by="system",
        headline="Check her identity against the registries held",
        capability="Built and running. The system holds GFW vessel identity "
                   "and the OFAC, UN and EU designation lists, versioned with "
                   "as-of dates."),
    "compare_own_history": dict(
        base_priority=55, performed_by="system",
        headline="Compare this against her own history",
        capability="Partly built. The system holds her landed track, port "
                   "calls and zone transitions and can show them; a learned "
                   "per-vessel behavioural baseline is Area 2 and does not "
                   "exist yet."),
    "check_imaging_opportunity": dict(
        base_priority=65, performed_by="system",
        headline="Check whether a satellite was overhead during the silence",
        capability="Built and running (ADR-026). It answers whether an image "
                   "exists whose footprint contained her — it does not mean "
                   "anybody has looked at it."),
    "query_zone_history": dict(
        base_priority=45, performed_by="system",
        headline="Pull who else has been inside that area",
        capability="Built and running (ADR-030). Zone entry and exit are "
                   "landed events and are queryable."),
    "check_arrival_notification": dict(
        base_priority=50, performed_by="operator",
        headline="Check her pre-arrival notification against what the track shows",
        capability="Not built. Arrival-notification ingestion is Area 4 of "
                   "the Section-3 brief; the paperwork reaches the Coast Guard "
                   "as email attachments and this system holds none of it."),
    "monitor": dict(
        base_priority=20, performed_by="system",
        headline="Keep her on watch",
        capability="Built. Marking the alert `watch` keeps it in the queue and "
                   "feeds the disposition ledger."),
}


def _feasibility(action: str, position: dict) -> tuple[bool, str, dict]:
    """Can this actually be done, from where the subject is?

    Only the two range-limited actions are tested; everything else is either a
    query over data already held or an operational decision outside this
    system's reach, and inventing a feasibility test for those would be theatre.
    """
    if action not in ("call_vhf", "cue_eo_camera", "dispatch_patrol"):
        return True, "", {}

    st = nearest_station(position.get("lat"), position.get("lon"))
    if st is None:
        return False, ("No position for this subject, so range to a station "
                       "cannot be worked out."), {}

    limit = {"call_vhf": st["vhf_range_km"],
             "cue_eo_camera": st["eo_range_km"],
             "dispatch_patrol": st["radar_range_km"]}[action]
    ok = st["distance_km"] <= limit
    what = {"call_vhf": "VHF line of sight",
            "cue_eo_camera": "useful camera range",
            "dispatch_patrol": "radar cover"}[action]
    if ok:
        why = (f"{st['name']} is {st['distance_km']:.0f} km away, inside "
               f"{what} of about {limit:.0f} km.")
    else:
        why = (f"Nearest station is {st['name']} at {st['distance_km']:.0f} km, "
               f"beyond {what} of about {limit:.0f} km. "
               + ("An out-of-range contact is an argument for a surface unit "
                  "or a satellite tasking, not for a radio call."
                  if action == "call_vhf" else
                  "Nothing ashore can see her from here."))
    return ok, why, {"station": st}


def recommend(factors: Sequence[Factor], *, position: dict,
              subject_kind: str, name: str) -> list[Recommendation]:
    """Propose next actions for a subject, each tied to its motivating factors.

    One recommendation per distinct action, even when several factors call for
    the same one — a watchkeeper reads a list of things to do, not a
    cross-product. Every factor that asked for it is named in
    ``because_factors``, which is what makes the reasoning inspectable rather
    than oracular.
    """
    wanted: dict[str, list[Factor]] = {}
    for f in factors:
        for action in spec(f.kind).actions:
            wanted.setdefault(action, []).append(f)

    out: list[Recommendation] = []
    for action, causes in wanted.items():
        meta = ACTIONS.get(action)
        if meta is None:
            continue
        feasible, why, detail = _feasibility(action, position or {})

        causes = sorted(causes, key=lambda f: -(f.points or f.confidence))
        top = causes[0]
        # The rationale names the strongest factor that asked for this and how
        # many others agreed. "Because of X, and two other factors" is a
        # sentence; a list of six factor ids is not.
        others = len(causes) - 1
        also = (f", and {others} other factor{'' if others == 1 else 's'} on "
                f"this subject" if others else "")
        rationale = (f"Because of {spec(top.kind).label.lower()}"
                     f"{also}. {spec(top.kind).blurb.capitalize()}.")

        # Priority follows the evidence: an action asked for by a factor
        # carrying half the score outranks the same action asked for by a
        # weak one. Infeasible actions sink to the bottom but stay visible.
        weightiest = max(float(f.points or f.confidence) for f in causes)
        priority = int(meta["base_priority"] + 40 * weightiest)
        if not feasible:
            priority -= 60

        out.append(Recommendation(
            action=action,
            headline=meta["headline"],
            rationale=rationale,
            because_factors=[f.factor_id for f in causes],
            priority=priority,
            performed_by=meta["performed_by"],
            feasible=feasible,
            feasibility=why,
            system_capability=meta["capability"],
            detail=detail))

    out.sort(key=lambda r: (-r.priority, r.action))
    return out
