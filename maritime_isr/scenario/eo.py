"""The simulated camera — what a lens at a station would actually see.

This is the far side of the :class:`~maritime_isr.eo.capture.CaptureSource` seam,
and it is the only module in the build that is allowed to know what is out
there. It knows because it *is* the world generator, exactly as
`scenario/radar.py` is: the radar simulator walks every vessel's motion and asks
which station could have heard her, and this does the same for the cameras.

Everything on the other side of the seam — the cueing scheduler, the capture
record, the library, the mismatch rule — has no access to any of it, and
`tests/test_area5.py` asserts that no module under `eo/` names this table.

**Why the appearance is landed rather than computed on demand.** A capture
happens at pipeline time, long after generation, and by then the world object is
gone. Regenerating it to take one photograph would cost minutes. So generation
writes down, once, what each hull physically looks like and what is behind each
radar track number — the stand-in for the photons a lens would collect — and the
camera source reads that back. It is the same shape as `radar_track_report`
carrying an RCS: a sensor product, precomputed because the sensor is simulated.

**What this table is not.** It carries no scenario id, no truth class, no
expectation about detection. It says "a hull of these dimensions and this form
is at that bearing", which is a fact about the world and not an answer key.
`scenario_truth` remains the answer key and this module does not read it.

**The one thing the simulation makes easier than reality, stated plainly.** A
camera slewed onto a sea-clutter track here reports empty water, every time. A
real camera in a real swell does not have a 100% presence-detection rate. That
is why "the camera saw nothing" is recorded and counted and is deliberately not
promoted to an alert (ADR-037).
"""
from __future__ import annotations

from collections import Counter
from datetime import timezone

from ..eo.appearance import descriptor_for
from ..eo.capture import ObservedTarget
from ..ingest.landing import land_table, stamp_envelope
from .identifiers import SYNTHETIC_SOURCE_ID

__all__ = ["TABLE", "build_appearance_rows", "land_appearances",
           "SimulatedCameraSource", "KIND_VESSEL", "KIND_FIXED", "KIND_NONE"]

#: Deliberately named `scenario_` and deliberately **not** registered in
#: `api.reader.CONFORMED_TABLES`. It is the camera simulator's own world model;
#: a serving layer that could read it would be reading the world rather than the
#: sensor.
TABLE = "scenario_eo_appearance"

KIND_VESSEL = "vessel"
KIND_FIXED = "fixed_object"
KIND_NONE = "empty_water"

#: A single point mooring or a light float, as a camera sees it: short, squat,
#: no superstructure to speak of, and unmistakably not a ship. Getting this
#: right matters because the four fixed targets in the radar picture report
#: every quarter of an hour for eight weeks and are the hardest ordinary false
#: positive the dark-contact queue has (group R6).
_FIXED_FORM = dict(vessel_class="fixed_object", beam_m=None, draught_m=None)


def _stamp(row: dict, *, source_ref: str, acquired_at) -> dict:
    stamp_envelope(row, source_id=SYNTHETIC_SOURCE_ID, source_ref=source_ref,
                   acquired_at=acquired_at.astimezone(timezone.utc),
                   confidence=None, is_synthetic=True)
    row["is_synthetic"] = True
    return row


def _appearance_columns(app) -> dict:
    return {
        "app_length_m": round(app.length_m, 2),
        "app_length_beam_ratio": round(app.length_beam_ratio, 4),
        "app_superstructure": round(app.superstructure_position, 4),
        "app_freeboard_ratio": round(app.freeboard_ratio, 5),
        "app_deck_clutter": round(app.deck_clutter, 4),
        "app_mast_count": round(app.mast_count, 3),
    }


def build_appearance_rows(world) -> list[dict]:
    """One row per hull, and one per radar track number.

    Two key spaces because a camera is cued at two kinds of subject. A hull the
    system has identified is named by her entity id; a radar contact nobody can
    name is known only by the station's track number, and the camera is slewed
    to that track's bearing. Keying on both is what lets one source serve both
    without the loop ever learning which is which.
    """
    rows: list[dict] = []

    from ..schemas.keys import vessel_node_id

    for v in world.vessels.values():
        # **The hull's physical class, never what she broadcasts.** A camera
        # photographs steel. The two differ for exactly the Area 5 scenarios
        # (`cast.DECLARED_CLASS_OVERRIDES`) and that difference is the finding.
        app = descriptor_for(v.vessel_class, length_m=v.length_m,
                             beam_m=v.beam_m, draught_m=v.draught_m)
        # **Keyed by the canonical node id, not the generator's entity id.** The
        # cueing loop names a hull by the key the identity table publishes
        # (ADR-022); keying this table by the scenario's own id put the camera
        # and the scheduler in two key spaces, and the simulator answered "I
        # hold no model of what is at that bearing" for every named hull.
        rows.append(_stamp(dict(
            target_key=vessel_node_id(v.entity_id), key_kind="vessel",
            target_kind=KIND_VESSEL, physical_class=v.vessel_class,
            length_m=round(float(v.length_m), 2),
            **_appearance_columns(app)),
            source_ref=f"{v.entity_id}:appearance", acquired_at=world.t0))

    radar = getattr(world, "radar", None)
    if radar is None or not radar.plots:
        return rows

    # What each station track number was actually looking at. Taken as the modal
    # truth over the plots rather than the first, because a track number is
    # recycled and a stray plot at the join must not decide what the camera sees
    # for the whole track.
    by_track: dict[str, Counter] = {}
    for p in radar.plots:
        by_track.setdefault(p.radar_track_id, Counter())[
            (p.truth_kind, p.truth_entity_id)] += 1

    from .radar_network import FIXED_TARGETS
    fixed = {f.target_id: f for f in FIXED_TARGETS}

    for track_id, counts in sorted(by_track.items()):
        (kind, entity), _n = counts.most_common(1)[0]
        if kind == "vessel" and entity in world.vessels:
            v = world.vessels[entity]
            app = descriptor_for(v.vessel_class, length_m=v.length_m,
                                 beam_m=v.beam_m, draught_m=v.draught_m)
            rows.append(_stamp(dict(
                target_key=track_id, key_kind="radar_track",
                target_kind=KIND_VESSEL, physical_class=v.vessel_class,
                length_m=round(float(v.length_m), 2),
                **_appearance_columns(app)),
                source_ref=f"{track_id}:appearance", acquired_at=world.t0))
        elif kind == "fixed" and entity in fixed:
            f = fixed[entity]
            app = descriptor_for("fixed_object", length_m=f.length_m,
                                 beam_m=f.length_m * 0.8)
            rows.append(_stamp(dict(
                target_key=track_id, key_kind="radar_track",
                target_kind=KIND_FIXED, physical_class=f.kind,
                length_m=round(float(f.length_m), 2),
                **_appearance_columns(app)),
                source_ref=f"{track_id}:appearance", acquired_at=world.t0))
        else:
            # Sea clutter. The camera is slewed to the bearing and there is
            # nothing there — which is a real and useful answer about a radar
            # track, and the single most valuable thing a camera does for a
            # picture whose dominant false-positive source is clutter.
            rows.append(_stamp(dict(
                target_key=track_id, key_kind="radar_track",
                target_kind=KIND_NONE, physical_class=None, length_m=None,
                app_length_m=None, app_length_beam_ratio=None,
                app_superstructure=None, app_freeboard_ratio=None,
                app_deck_clutter=None, app_mast_count=None),
                source_ref=f"{track_id}:appearance", acquired_at=world.t0))
    return rows


def land_appearances(rows: list[dict]) -> dict:
    if not rows:
        return {}
    return land_table(rows, table=TABLE, key_fields=("target_key",),
                      day_field="acquired_at")


class SimulatedCameraSource:
    """Point the simulated camera and report what was in frame.

    Implements :class:`~maritime_isr.eo.capture.CaptureSource`. A deployment
    replaces this class with a driver talking to a camera controller and nothing
    else in the loop changes — that is the entire purpose of the seam, and it is
    why this object holds no reference to the scheduler, the classifier or the
    rule.
    """

    name = "scenario-camera-sim"
    mode = "simulated"

    def __init__(self, rows):
        self.by_key: dict[str, dict] = {}
        for r in rows:
            key = r.get("target_key")
            if key:
                self.by_key[str(key)] = r
        self.misses = 0

    @staticmethod
    def _key_for(tasking) -> str:
        from ..schemas.keys import vessel_node_id

        subject = str(tasking.subject_id or "")
        if subject.startswith("vessel:"):
            # Canonicalised on both sides — `vessel_node_id` is idempotent, so
            # this accepts a raw entity id and a node id alike and lands on one
            # key space either way.
            return vessel_node_id(subject)
        if subject.startswith("contact:"):
            # `contact:<sensor>:<track key>` — the track key for a radar contact
            # is the station's own track number, which is what this table holds.
            return subject.split(":", 2)[-1]
        return str(tasking.track_id or subject)

    def capture(self, tasking):
        row = self.by_key.get(self._key_for(tasking))
        if row is None:
            # **Not "empty water".** The simulator has no model of what is at
            # that bearing, which is a gap in the simulation and not an
            # observation about the sea. Reporting it as an empty frame would
            # manufacture a finding about a radar track out of our own missing
            # data — the failure ADR-021 names: a check that cannot tell absence
            # from breakage is not a check.
            self.misses += 1
            return None
        if row.get("target_kind") == KIND_NONE:
            return ObservedTarget(present=False, target_kind=KIND_NONE,
                                  note="the frame was empty")
        from ..eo.appearance import Appearance
        app = Appearance(
            length_m=float(row["app_length_m"]),
            length_beam_ratio=float(row["app_length_beam_ratio"]),
            superstructure_position=float(row["app_superstructure"]),
            freeboard_ratio=float(row["app_freeboard_ratio"]),
            deck_clutter=float(row["app_deck_clutter"]),
            mast_count=float(row["app_mast_count"]))
        return ObservedTarget(present=True, appearance=app,
                              length_m=row.get("length_m"),
                              target_kind=row.get("target_kind") or KIND_VESSEL)
