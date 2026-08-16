"""What kind of thing a stream of positions is, so the core need not ask.

**Why this file exists.** The track engine, the encounter detector, the gap
classifier and the anomaly library were all written against AIS and all of them
encoded that assumption in the same way: they read a column called `mmsi` and
treated whatever was in it as an identity. That is fine while AIS is the only
positional source. It stops being fine the moment a second one arrives.

A coastal-radar track is position, course and speed with **no identity**. It is
structurally an AIS position report minus the identity fields — which is exactly
the claim CLAUDE.md §4.5 makes about connectors, and exactly the claim that had
never been tested. Testing it found three places where the core was not
source-agnostic at all (see ADR-028); every one of them is fixed by asking a
descriptor rather than by branching on a source name.

**The descriptor carries semantics, not identity of the source.** Nothing
downstream may say `if source == "radar"`. It may ask
`if not source.key_is_identity`, which is a question about what the data *means*
and stays correct when the fourth sensor arrives. That distinction is the whole
point of the file: a `==` on a name is a source-specific hack wearing a
parameter's clothes.

Positional accuracy lives here too, because it is a property of the sensor and
the fusion gate needs it. It is a *default*: a sensor that reports its own
per-observation accuracy (coastal radar does — the cross-range error grows with
range) should carry it on the row, and the gate prefers the row's value.
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = ["TrackSource", "AIS", "RADAR", "SOURCES", "source_by_name"]


@dataclass(frozen=True)
class TrackSource:
    """One positional sensor, described in the terms the core actually needs."""

    #: Short stable name. Lands in `track.source` and in every derived row, so
    #: an analyst can always ask which sensor produced a claim.
    name: str

    #: The column that groups reports into one track. For AIS this is the MMSI;
    #: for radar it is the station-assigned track number. The core never spells
    #: either of those out.
    key_field: str

    #: **Is that key a claim about who this is?** An MMSI is: two hulls
    #: broadcasting one MMSI is a spoofing tell and the track engine is right to
    #: raise it. A radar track number is not: it is a slot in a station's track
    #: table, reused freely, and a collision says nothing about any vessel. Any
    #: rule whose meaning depends on the key being an identity must consult this
    #: rather than assume.
    key_is_identity: bool

    #: Does the sensor observe the vessel's silence at all? AIS does — an AIS
    #: track that stops reporting inside demonstrated coverage is the dark
    #: period the gap classifier exists to find. Radar does not: a radar track
    #: ending means the radar lost it, which is a statement about the radar.
    #: Classifying a radar gap as INTENTIONAL_SILENCE would be the
    #: offshore-silence anti-pattern (CLAUDE.md §6) with a different sensor.
    observes_transmission: bool

    #: 1-sigma position error, metres. A default for gating; a row carrying its
    #: own `position_sigma_m` overrides it.
    position_sigma_m: float

    #: Does an observation carry its own size estimate? A SAR contact does
    #: (pixel extent); a radar plot does (radar cross-section, coarsely); an AIS
    #: report does not — its length comes from the registry, via the identity.
    carries_size_estimate: bool

    #: How long the key may go silent before the next report under it is
    #: presumed to be a *different target*, in seconds.
    #:
    #: **This is the reuse guard, and it is not a constant of the system.** For
    #: AIS it is seven days: an MMSI is welded to a vessel, she may legitimately
    #: be out of reception for days, and the same number turning up again is
    #: almost always the same ship. For a radar track number it is minutes: the
    #: number is a slot in a finite track table, the tracker frees it as soon as
    #: it coasts a target off, and the next acquisition may be anybody.
    #:
    #: Measured on the synthetic picture with the AIS value applied to radar:
    #: single "tracks" of **11,829 plots spanning six weeks**, built by merging
    #: every target that happened to be issued the same recycled number. Two
    #: vessels 20 km apart fifteen minutes apart imply 52 knots, which slips
    #: under the 60-knot hypothesis gate, so the multi-hypothesis splitter did
    #: not catch it either. The correlation then tried to explain one track that
    #: was really two hundred ships, and every real dark vessel in the Gulf of
    #: Kachchh disappeared into it.
    track_break_s: float = 7 * 86400.0

    #: Per-report columns the track engine must carry through onto the built
    #: track's points, beyond the position/velocity ones every sensor has.
    #:
    #: **Declared here rather than discovered.** Carrying every column the input
    #: frame happens to hold would drag the whole provenance envelope and every
    #: H3 resolution onto every smoothed point, which is a lot of memory to
    #: answer a question nobody asked. Naming them means a consumer that needs
    #: `length_est_m` on a radar track finds it, and a typo in a consumer fails
    #: loudly instead of silently reading a column that was never carried.
    carry_columns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.observes_transmission and not self.key_is_identity:
            raise ValueError(
                f"{self.name}: a sensor that observes transmission is hearing a "
                f"broadcast identity, so its key must be an identity. This "
                f"combination would let the gap classifier convict a track it "
                f"cannot name.")


#: Ship-broadcast position reports. The source everything here was written for.
AIS = TrackSource(
    name="ais",
    key_field="mmsi",
    key_is_identity=True,
    observes_transmission=True,
    # AIS positions carry the vessel's own GNSS solution; the residual after
    # Phase 2 smoothing is what the fusion gate already assumed.
    position_sigma_m=60.0,
    carries_size_estimate=False,
    # TRACK_BREAK_DAYS, unchanged. Kept as an explicit number here rather than
    # imported from config so the descriptor reads as one statement about the
    # sensor; config.TRACK_BREAK_DAYS remains the documented constant and a test
    # asserts the two agree.
    track_break_s=7 * 86400.0,
)

#: Coastal surveillance radar tracks: where and how fast, never who.
RADAR = TrackSource(
    name="radar",
    key_field="radar_track_id",
    key_is_identity=False,
    observes_transmission=False,
    # A nominal mid-range figure only. Real accuracy is range-dependent
    # (constant in range, growing in cross-range), and every landed plot carries
    # its own `position_sigma_m` computed from the station geometry.
    position_sigma_m=120.0,
    carries_size_estimate=True,
    # Thirty minutes: comfortably longer than the ~16 minutes a tracker coasts
    # a target before dropping it, and far shorter than the interval over which
    # a busy station recycles the number. Reuse inside half an hour still
    # happens on a crowded station and still has to be caught by the
    # multi-hypothesis speed gate — this makes that the residual case rather
    # than the normal one.
    track_break_s=30 * 60.0,
    # `station_id` so a contact can name which stations held it;
    # `length_est_m` so the size floor has something to test; the rest so an
    # analyst opening a contact can see how strong the echo was and how far out.
    carry_columns=("station_id", "length_est_m", "rcs_dbsm", "snr_db",
                   "range_km", "track_quality"),
)

SOURCES: dict[str, TrackSource] = {s.name: s for s in (AIS, RADAR)}


def source_by_name(name: str) -> TrackSource:
    try:
        return SOURCES[name]
    except KeyError:                                            # noqa: PERF203
        raise ValueError(
            f"unknown track source {name!r}; known: {sorted(SOURCES)}. "
            f"A new sensor is a deliberate edit here — that is what stops the "
            f"track engine and the anomaly library inventing different ideas "
            f"of what it means.") from None
