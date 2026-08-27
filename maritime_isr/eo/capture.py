"""A capture: an image bound to a track, and landed as evidence.

*"Capture tagging. An image, once captured, binds to a track with time, bearing,
range and station, and becomes evidence attached to the vessel — the requirement
names imagery as one of three evidence types on a Vessel of Interest."*

**The binding is the product, not the picture.** A photograph in a folder is
worth nothing to a watchkeeper; a photograph that is attached to track
`SYN-MUM:0223`, taken at 14:22 from Mumbai at 8.4 km on a bearing of 214°,
against a hull that declares herself a trawler, is evidence. So every field of
that sentence is a column here, with the full provenance envelope on the row
(CLAUDE.md §4.1) and H3 cells at the target's position so the capture joins to
everything else the system holds about that patch of water.

The seam where a real camera goes
---------------------------------
:class:`CaptureSource` is the whole of this module's contact with hardware.
Given a tasking — a camera, a bearing, a moment — it returns what was in frame.
In a deployment that implementation talks to a camera; in this build it is the
scenario's own simulator, which knows what is out there because it *is* the
world generator. Everything on this side of the seam is code that would run
unchanged against real imagery, and `capture_mode` on every row says which side
produced it, because a row that cannot tell an operator whether a real lens was
involved is the overclaim this project treats as cardinal (CLAUDE.md §5).

**The simulated camera is perfect at detecting presence, and a real one is
not.** When it is slewed onto a sea-clutter track it reports empty water, every
time. That resolves a false radar track, which is a genuine and valuable use of
a camera — and it is also the one thing in this area the simulation makes
easier than reality, so "the camera saw nothing" is recorded on the capture and
counted, and is deliberately **not** promoted to an alert. Turning it into one
would need a measured false-negative rate for a camera that does not exist.
"""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol, Sequence

from ..config import PIPELINE_VERSION
from .appearance import Appearance, observe
from .classify import ImageClassifier, ImageVerdict, LibraryEntry, ReferenceLibrary
from .cue import CuePlan, Tasking

__all__ = ["EOCapture", "ObservedTarget", "CaptureSource", "TABLE", "SOURCE_ID",
           "run_captures", "land_captures", "publish_captures",
           "capture_rows_to_library"]

TABLE = "eo_capture"
SOURCE_ID = "eo-camera"

#: What every row in this build carries. The alternative value is `live`, and
#: nothing has ever written it.
MODE_SIMULATED = "simulated"


@dataclass
class ObservedTarget:
    """What was actually in frame when the camera was slewed and fired.

    ``present=False`` is a real and useful answer — the camera looked and there
    was nothing there — and is distinct from a capture that never happened.
    """
    present: bool
    appearance: Optional[Appearance] = None
    length_m: Optional[float] = None
    heading_deg: Optional[float] = None
    #: "vessel" | "fixed_object" | "empty_water" | "unknown". Descriptive of
    #: what the frame contained, never a verdict about anybody.
    target_kind: str = "unknown"
    note: str = ""


class CaptureSource(Protocol):
    """Point the camera and take the picture.

    The only interface in this package that touches hardware. A deployment
    implements it against a camera controller; this build implements it in
    `scenario/eo.py` against the simulated world.
    """

    name: str
    mode: str

    def capture(self, tasking: Tasking) -> ObservedTarget:
        ...


@dataclass
class EOCapture:
    """One image, bound to one track, with everything needed to use it."""
    capture_id: str
    tasking_id: str
    taken_at: datetime
    camera_id: str
    station_id: str
    station: str
    subject_id: str
    track_id: str
    lat: float
    lon: float
    range_km: float
    bearing_deg: float
    aspect_deg: Optional[float]
    band: str
    image_quality: float
    visibility_km: float
    solar_elevation_deg: float
    target_present: bool
    target_kind: str
    #: There is no file. The column exists so a deployment has somewhere to put
    #: the object key, and so that its emptiness here is visible rather than
    #: implied.
    image_ref: Optional[str] = None
    capture_mode: str = MODE_SIMULATED
    source_name: str = ""
    observed: Optional[Appearance] = None
    verdict: Optional[ImageVerdict] = None
    cue_priority: float = 0.0
    cue_sentence: str = ""
    is_synthetic: bool = False
    pipeline_version: str = PIPELINE_VERSION

    @property
    def imaged_type(self) -> Optional[str]:
        return self.verdict.imaged_type if self.verdict else None

    def statement(self) -> str:
        """The sentence a watchkeeper reads under the (absent) picture."""
        when = self.taken_at.strftime("%d %b %H:%M")
        where = (f"{self.station} camera, {self.range_km:.1f} km on "
                 f"{self.bearing_deg:.0f}°")
        if not self.target_present:
            return (f"{when}: {where} — camera slewed and the frame was empty. "
                    f"Nothing was there to photograph.")
        if self.verdict is None or not self.verdict.is_claim:
            why = (self.verdict.not_classifiable if self.verdict
                   else "no classifier was supplied")
            return (f"{when}: {where} — image taken, no type claimed ({why}).")
        v = self.verdict
        ident = ""
        if v.identity_subject:
            ident = (f" Matches a hull imaged before ({v.identity_subject}), "
                     f"confidence {v.identity_confidence:.2f}.")
        return (f"{when}: {where} — images as a {v.imaged_type.replace('_', ' ')}"
                f" at confidence {v.confidence:.2f}"
                f" ({v.band} band, image quality {self.image_quality:.2f})."
                f"{ident}")

    def as_row(self) -> dict:
        v = self.verdict
        obs = self.observed
        return {
            "capture_id": self.capture_id,
            "tasking_id": self.tasking_id,
            "taken_at": self.taken_at,
            "camera_id": self.camera_id,
            "station_id": self.station_id,
            "station": self.station,
            "subject_id": self.subject_id,
            "track_id": self.track_id,
            "lat": self.lat, "lon": self.lon,
            "range_km": round(self.range_km, 3),
            "bearing_deg": round(self.bearing_deg, 2),
            "aspect_deg": (None if self.aspect_deg is None
                           else round(self.aspect_deg, 2)),
            "band": self.band,
            "image_quality": round(self.image_quality, 4),
            "visibility_km": round(self.visibility_km, 2),
            "solar_elevation_deg": round(self.solar_elevation_deg, 2),
            "target_present": bool(self.target_present),
            "target_kind": self.target_kind,
            "image_ref": self.image_ref,
            "capture_mode": self.capture_mode,
            "source_name": self.source_name,
            "cue_priority": round(self.cue_priority, 4),
            "cue_sentence": self.cue_sentence,
            "statement": self.statement(),
            "imaged_type": v.imaged_type if v else None,
            "type_confidence": (round(float(v.confidence), 4) if v else None),
            "fine_type": v.fine_type if v else None,
            # What the label rules out, as the model that produced it means it.
            # Landed rather than re-derived downstream: a Parquet column cannot
            # hold a set, and a rule that recomputed it against a different
            # model's vocabulary is the defect `families_of_imaged` documents.
            "imaged_families": (",".join(sorted(v.imaged_families))
                                if v and v.imaged_families else None),
            "not_classifiable": v.not_classifiable if v else None,
            "identity_subject": v.identity_subject if v else None,
            "identity_confidence": (round(float(v.identity_confidence), 4)
                                    if v else None),
            "identity_basis": v.identity_basis if v else None,
            "model_name": v.model_name if v else None,
            "model_provenance": v.model_provenance if v else None,
            "observed_length_m": (round(obs.length_m, 2) if obs else None),
            "observed_length_reliable": (bool(obs.length_reliable) if obs
                                         else None),
            "observed_deck_readable": (bool(obs.deck_readable) if obs else None),
            "observed_length_beam_ratio": (round(obs.length_beam_ratio, 4)
                                           if obs else None),
            "observed_superstructure": (round(obs.superstructure_position, 4)
                                        if obs else None),
            "observed_freeboard_ratio": (round(obs.freeboard_ratio, 5)
                                         if obs else None),
            "observed_deck_clutter": (round(obs.deck_clutter, 4) if obs
                                      else None),
            "observed_mast_count": (round(obs.mast_count, 3) if obs else None),
            "pipeline_version": self.pipeline_version,
        }


def _capture_id(t: Tasking) -> str:
    body = f"{t.tasking_id}|{t.camera_id}|{t.subject_id}|{t.at.isoformat()}"
    return "eoc_" + hashlib.sha1(body.encode()).hexdigest()[:12]


def run_captures(plan: CuePlan, *, source: CaptureSource,
                 classifier: ImageClassifier,
                 library: Optional[ReferenceLibrary] = None,
                 is_synthetic: bool = False) -> list[EOCapture]:
    """Execute a tasking order: slew, capture, classify, file in the library.

    **The library grows as the plan runs, and the order matters.** A hull imaged
    in slot 3 is in the library by slot 40, which is what lets the second look
    at her be recognised as the same ship. Running the classifier over a
    finished pile of captures instead would make every identification depend on
    an accident of iteration order, and would make re-recognition — the half of
    the requirement that says "to specific identity where a vessel has been
    imaged before" — untestable.
    """
    library = library if library is not None else ReferenceLibrary()
    out: list[EOCapture] = []
    for task in sorted(plan.taskings, key=lambda t: (t.slot_index, t.camera_id)):
        obs = source.capture(task)
        if obs is None:
            # The source declined: no image exists. Distinct from a frame that
            # was empty, and it must not land a row — a capture record asserts
            # that a camera looked, and inventing one for a target the source
            # could not model would put an observation in the corpus that never
            # happened (ADR-021's absence-versus-breakage rule).
            continue
        cid = _capture_id(task)
        vw = (task.why or {}).get("view") or {}
        cap = EOCapture(
            capture_id=cid, tasking_id=task.tasking_id, taken_at=task.at,
            camera_id=task.camera_id, station_id=task.station_id,
            station=task.station, subject_id=task.subject_id,
            track_id=task.track_id, lat=task.lat, lon=task.lon,
            range_km=task.range_km, bearing_deg=task.bearing_deg,
            aspect_deg=task.aspect_deg, band=task.band,
            image_quality=task.expected_quality,
            visibility_km=float(vw.get("visibility_km") or 0.0),
            solar_elevation_deg=float(
                (vw.get("illumination") or {}).get("solar_elevation_deg") or 0.0),
            target_present=bool(obs.present),
            target_kind=obs.target_kind,
            capture_mode=getattr(source, "mode", MODE_SIMULATED),
            source_name=getattr(source, "name", "unknown"),
            cue_priority=task.priority, cue_sentence=task.sentence,
            is_synthetic=bool(is_synthetic or task.is_synthetic))

        if obs.present and obs.appearance is not None:
            # Deterministic per capture: the same corpus and the same plan
            # produce the same image noise, so a cueing change can be attributed
            # rather than confounded with a fresh roll of the dice.
            rng = random.Random(int(hashlib.sha1(cid.encode()).hexdigest()[:8],
                                    16))
            seen = observe(obs.appearance, aspect_deg=task.aspect_deg,
                           quality=task.expected_quality, band=task.band,
                           rng=rng)
            cap.observed = seen
            cap.verdict = classifier.classify(
                seen, quality=task.expected_quality, band=task.band,
                library=library, known_subject=task.subject_id)
            if cap.verdict.is_claim:
                library.add(LibraryEntry(
                    subject_id=task.subject_id, appearance=seen,
                    capture_id=cid, at=task.at.timestamp(),
                    quality=task.expected_quality,
                    label=cap.verdict.imaged_type or ""))
        out.append(cap)
    return out


def capture_rows_to_library(rows: Sequence[dict]) -> ReferenceLibrary:
    """Rebuild a library from landed capture rows.

    A deployment's library outlives one run. Reconstructing it from the landed
    table rather than keeping it in a pickle means the library is inspectable,
    is covered by the same provenance discipline as everything else, and cannot
    drift out of agreement with the captures it claims to be built from.
    """
    lib = ReferenceLibrary()
    for r in rows:
        if not r.get("target_present") or r.get("observed_length_m") is None:
            continue
        if not r.get("imaged_type"):
            continue
        lib.add(LibraryEntry(
            subject_id=str(r.get("subject_id")),
            appearance=Appearance(
                length_m=float(r["observed_length_m"]),
                length_beam_ratio=float(r.get("observed_length_beam_ratio") or 6.0),
                superstructure_position=float(r.get("observed_superstructure") or 0.5),
                freeboard_ratio=float(r.get("observed_freeboard_ratio") or 0.09),
                deck_clutter=float(r.get("observed_deck_clutter") or 0.5),
                mast_count=float(r.get("observed_mast_count") or 1.0),
                length_reliable=bool(r.get("observed_length_reliable", True)),
                deck_readable=bool(r.get("observed_deck_readable", True))),
            capture_id=str(r.get("capture_id")),
            at=0.0, quality=float(r.get("image_quality") or 0.0),
            label=str(r.get("imaged_type") or "")))
    return lib


def land_captures(captures: Sequence[EOCapture], *,
                  source_id: str = SOURCE_ID,
                  is_synthetic: bool = False) -> dict[str, int]:
    """Land captures into the conformed layer, envelope and H3 on every row.

    ``source_id`` follows the convention `ingest/pans/land.py` established for
    exactly this case: a connector that really runs over synthetic inputs is
    landed as ``synthetic-scenario:eo-camera``, so the row says honestly both
    which component produced it and that the corpus behind it is not real. The
    envelope stamper refuses any other combination (ADR-036).
    """
    from ..ingest.landing import land_table, stamp_envelope, stamp_h3

    rows = []
    for cap in captures:
        row = cap.as_row()
        stamp_envelope(row, source_id=source_id, source_ref=cap.camera_id,
                       acquired_at=cap.taken_at,
                       confidence=(float(cap.verdict.confidence)
                                   if cap.verdict and cap.verdict.is_claim
                                   else None),
                       is_synthetic=bool(is_synthetic))
        row["is_synthetic"] = bool(is_synthetic or cap.is_synthetic)
        rows.append(stamp_h3(row))
    if not rows:
        return {}
    return land_table(rows, table=TABLE, key_fields=("capture_id",),
                      day_field="taken_at")


def publish_captures(store, captures: Sequence[EOCapture]) -> dict[str, int]:
    """Put the captures into the object graph, bound to their subjects.

    Three things are asserted and each is a separate claim:

      * the capture **exists** — an `eo_capture` node, its own type for the same
        reason `notification` is (ADR-036): it is an artifact, not a ship, and
        hanging it off an invented vessel node is the shadow-stub failure
        ADR-022 exists to prevent;
      * it **depicts** a subject — which track it binds to;
      * it was **captured by** a camera — which sensor took it.

    A capture whose subject the graph has never heard of is skipped rather than
    minted, the same rule `detect_identity_contradiction` follows: the graph
    populator creates subjects, evidence does not.
    """
    from ..graph.ontology import EDGE_TYPES_V1, NODE_TYPES_V1
    from ..graph.store import SYNTHETIC_SOURCE_PREFIX

    # A graph built before Area 5 existed has an ontology that predates these
    # types — the registry is seeded once, as data, and is not re-read from the
    # constants. Registering them here rather than assuming is the documented
    # migration path (`migrate_add_node_type`), and it means an existing store
    # gains the imagery layer instead of raising "unknown node type".
    if "eo_capture" in NODE_TYPES_V1 and "eo_capture" not in store.node_registry():
        store.migrate_add_node_type("eo_capture")
    for name in ("depicts", "captured-by"):
        if name in EDGE_TYPES_V1 and name not in store.edge_registry():
            store.migrate_add_edge_type(name, EDGE_TYPES_V1[name])

    written = {"nodes": 0, "depicts": 0, "captured-by": 0}
    for cap in captures:
        subject = store.node(cap.subject_id)
        if subject is None:
            continue
        # The edge store refuses a row whose synthetic flag and source
        # disagree, which is the one thing keeping the corpora apart
        # (ADR-019). Same prefix convention `graph.store` already uses.
        src_name = (f"{SYNTHETIC_SOURCE_PREFIX}:eo_loop" if cap.is_synthetic
                    else "eo_loop")
        node_id = f"eo_capture:{cap.capture_id}"
        store.upsert_node(
            node_id, "eo_capture",
            props=dict(camera_id=cap.camera_id, station=cap.station,
                       taken_at=cap.taken_at.isoformat(),
                       range_km=round(cap.range_km, 2),
                       bearing_deg=round(cap.bearing_deg, 1),
                       band=cap.band,
                       image_quality=round(cap.image_quality, 3),
                       target_present=bool(cap.target_present),
                       imaged_type=cap.imaged_type,
                       capture_mode=cap.capture_mode,
                       statement=cap.statement()),
            is_synthetic=bool(cap.is_synthetic))
        written["nodes"] += 1

        sensor_id = f"sensor:{cap.camera_id}"
        if store.node(sensor_id) is None:
            store.upsert_node(sensor_id, "sensor",
                              props=dict(kind="electro_optical",
                                         station_id=cap.station_id,
                                         name=f"{cap.station} EO camera",
                                         simulated=cap.capture_mode
                                         == MODE_SIMULATED),
                              is_synthetic=bool(cap.is_synthetic))
        t = cap.taken_at.timestamp()
        store.add_edge(
            "depicts", node_id, cap.subject_id, t_start=t, t_end=None,
            confidence=(float(cap.verdict.confidence)
                        if cap.verdict and cap.verdict.is_claim else 0.5),
            source=src_name, source_ref=cap.capture_id, observed_at=t,
            props=dict(imaged_type=cap.imaged_type,
                       target_present=bool(cap.target_present),
                       image_quality=round(cap.image_quality, 3)),
            is_synthetic=bool(cap.is_synthetic))
        written["depicts"] += 1
        store.add_edge(
            "captured-by", node_id, sensor_id, t_start=t, t_end=None,
            confidence=0.99, source=src_name, source_ref=cap.camera_id,
            observed_at=t,
            props=dict(range_km=round(cap.range_km, 2),
                       bearing_deg=round(cap.bearing_deg, 1), band=cap.band),
            is_synthetic=bool(cap.is_synthetic))
        written["captured-by"] += 1
    return written
