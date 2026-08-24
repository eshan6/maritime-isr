"""Saying something useful about a contact nobody can name — Area 3.

*"Correlate against AIS and classify what does not correlate. A radar track with
no AIS explanation is a dark contact, and a dark contact with an inferred type
and activity is a far stronger product than a dark contact alone. 'Unidentified
contact' is a position. 'Probable fishing vessel, loitering, no transponder,
inside territorial waters' is intelligence."* — the IDEX Challenge 82 brief,
Area 3.

That last sentence is the whole module. Everything needed to produce it already
exists and was never assembled: the correlation cascade (ADR-028) decides which
radar tracks have no AIS explanation, `tracks.vessel_type` infers a class from
motion, `tracks.activity` infers a behaviour from the same motion, and the zone
layer (ADR-030) knows which waters she is in. This joins them onto one object.

**It profiles, it does not detect.** Whether a contact is dark was decided by
the cascade and is not revisited here — a second opinion on darkness would be a
second, uncalibrated copy of a rule that already exists (the same rule the
assistant layer follows, CLAUDE.md §7). What this adds is description, and
description is allowed to be wrong in a way a detection is not: a type at 0.6
confidence on an unidentified hull is a lead for a watchkeeper, and it is
labelled as one.

**Every claim degrades independently.** No type model, no type — the activity
and the zone still stand. Too short a track for a speed distribution, no type —
the position and the darkness still stand. A profile is built from whatever is
available and says which parts are missing, because a contact with a position
and nothing else is still the finding; the description is what makes it
actionable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

from ..config import PIPELINE_VERSION

__all__ = ["ContactProfile", "profile_contact", "profile_contacts"]


@dataclass
class ContactProfile:
    """What can be said about an unidentified contact, and how sure of each."""
    track_id: str
    track_key: str
    #: "dark_candidate", "ambiguous", … — the cascade's verdict, carried, not
    #: recomputed.
    correlation_status: Optional[str] = None
    vessel_type: Optional[str] = None
    type_confidence: float = 0.0
    type_reason: str = ""
    activity: Optional[str] = None
    activity_confidence: float = 0.0
    activity_reason: str = ""
    lat: Optional[float] = None
    lon: Optional[float] = None
    at: Optional[float] = None
    length_m: Optional[float] = None
    zones: list[str] = field(default_factory=list)
    #: Which claims could not be made, and why. The honest half.
    gaps: list[str] = field(default_factory=list)
    is_synthetic: bool = False
    pipeline_version: str = PIPELINE_VERSION

    def sentence(self) -> str:
        """The one line the brief asks for, built from what is actually held.

        Reads as a description of a target, in the order a watchkeeper needs it:
        what she probably is, what she is doing, that nothing is broadcasting,
        and where she is. Any part the system could not establish is simply
        absent from the sentence rather than filled with a hedge.
        """
        bits: list[str] = []
        if self.vessel_type:
            hedge = ("Probable" if self.type_confidence < 0.75 else "Likely")
            bits.append(f"{hedge} {self.vessel_type.replace('_', ' ')}")
        else:
            bits.append("Unidentified contact")
        if self.activity and self.activity != "unclassified":
            bits.append(self.activity.replace("_", " "))
        bits.append("no transponder")
        if self.zones:
            bits.append("inside " + ", ".join(self.zones))
        if self.length_m:
            bits.append(f"about {float(self.length_m):.0f} m")
        return ", ".join(bits) + "."

    def as_dict(self) -> dict:
        return {
            "track_id": self.track_id, "track_key": self.track_key,
            "correlation_status": self.correlation_status,
            "statement": self.sentence(),
            "vessel_type": self.vessel_type,
            "type_confidence": round(float(self.type_confidence), 3),
            "type_reason": self.type_reason,
            "activity": self.activity,
            "activity_confidence": round(float(self.activity_confidence), 3),
            "activity_reason": self.activity_reason,
            "lat": self.lat, "lon": self.lon, "at": self.at,
            "length_m": self.length_m,
            "zones": list(self.zones),
            "gaps": list(self.gaps),
            "is_synthetic": self.is_synthetic,
            "pipeline_version": self.pipeline_version,
        }


def profile_contact(track, *, type_model=None, zone_index=None,
                    correlation_status: Optional[str] = None,
                    length_m: Optional[float] = None,
                    is_synthetic: bool = False) -> ContactProfile:
    """Describe one unidentified track from its motion and its position.

    `type_model` is a fitted :class:`~..tracks.vessel_type.VesselTypeModel`, or
    None. `zone_index` is a `zones.ZoneIndex`, or None. Both are optional and
    their absence is recorded as a gap rather than silently producing a thinner
    profile that looks the same as a confident one.
    """
    from ..tracks.activity import classify_activity, dominant_activity, \
        classify_activity_segments

    prof = ContactProfile(
        track_id=getattr(track, "track_id", ""),
        track_key=str(getattr(track, "track_key", "")),
        correlation_status=correlation_status,
        length_m=length_m, is_synthetic=is_synthetic)

    # ---- activity, from motion -----------------------------------------
    # The dominant behaviour over the track, not the whole-track average: a
    # contact that transited and then stopped is loitering *now*, and "what is
    # she doing" means the second one.
    segments = classify_activity_segments(track)
    act = dominant_activity(segments) or classify_activity(track)
    prof.activity = act.activity
    prof.activity_confidence = act.confidence
    prof.activity_reason = act.reason
    prof.lat, prof.lon, prof.at = act.lat, act.lon, act.t_end
    if act.activity == "unclassified":
        prof.gaps.append(
            "No activity could be named: " + (act.reason or "insufficient track"))

    # ---- type, from the same motion -------------------------------------
    if type_model is None:
        prof.gaps.append(
            "No vessel-type model was supplied, so no type is inferred. Train "
            "one on the AIS-identified fleet (`tracks.vessel_type.train`).")
    else:
        v = type_model.classify(track)
        if v.is_claim:
            prof.vessel_type = v.vessel_type
            prof.type_confidence = v.confidence
            prof.type_reason = v.reason
        else:
            prof.gaps.append(f"No type claimed: {v.reason}")

    # ---- where she is ----------------------------------------------------
    if zone_index is None:
        prof.gaps.append(
            "No zone layer was supplied, so the waters she is in are not "
            "named. Note that the four statutory limits are absent by "
            "decision (ADR-030) and never appear here.")
    elif prof.lat is not None and prof.lon is not None:
        try:
            from ..zones.geometry import contains as _in_zone
            for kind in ("sensitive_area", "geofence", "port_area",
                         "anchorage"):
                for z in zone_index.of_kind(kind):
                    if _in_zone(zone_index.geometry(z.zone_id),
                                prof.lat, prof.lon):
                        prof.zones.append(z.name)
        except Exception:                                        # noqa: BLE001
            prof.gaps.append("The zone layer could not be queried.")
    return prof


def profile_contacts(tracks: Sequence, *, type_model=None, zone_index=None,
                     status_of=None, length_of=None,
                     synthetic_of=None) -> list[ContactProfile]:
    """Profile a set of unidentified tracks.

    `status_of`, `length_of` and `synthetic_of` are callables from a track to
    the cascade's verdict, the estimated length, and the corpus flag. Passed in
    rather than looked up here so this module keeps no opinion about where those
    facts live — the same reason the assistant layer takes its store as an
    argument.
    """
    out = []
    for tr in tracks:
        out.append(profile_contact(
            tr, type_model=type_model, zone_index=zone_index,
            correlation_status=(status_of(tr) if status_of else None),
            length_m=(length_of(tr) if length_of else None),
            is_synthetic=(synthetic_of(tr) if synthetic_of else False)))
    return out
