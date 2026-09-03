"""Read-only surfaces for the Section-3 capabilities that had no way to the screen.

Most of what this file serves already existed in Python and was reachable only
from a terminal or from the pipeline's stdout. A capability nobody can see is
not in the product — the same argument `RadarView` was built on — and the ones
gathered here are the ones where the *honest* half is the valuable half:

  * `anomaly/identity.py`, `voyage.py`, `paperwork.py`, `imagery.py` all return
    **three** outcomes. `contradiction`, `ok`, and `not_checkable`. Only the
    first ever became an alert, so the other two never left the process, and a
    surface that shows contradictions alone presents silence as a clean bill of
    health (ADR-032 (b), ADR-036 §4). Everything here carries all three, and the
    counts of all three, so "we could not look" is visible at the scale it
    actually occurs at.
  * `tracks/activity.py` has `unclassified` as a first-class output and it is
    served as one.
  * `baselines.is_unusual` is three-valued for the same reason, and its third
    state covers most of the sea.
  * `fusion/contact_profile.py` assembles the sentence Area 3 exists to
    produce, and it profiles rather than re-deciding darkness.
  * `eo/` decided which track a camera was pointed at and **why**, and landed
    that reasoning on every capture row. **There is no camera.** Every row says
    so and so does every response here.

**What this module does NOT do.** It never re-implements a rule. Every verdict
below is produced by calling the module that owns it, with landed rows as
inputs. A second copy of a rule in the serving layer is the "collector that
started detecting" mistake ADR-031 forbids the assistant, and it applies as
hard to an API.

**Two honest limitations, stated here because they are stated on the screen.**

1. The tracks assembled by :func:`_track_from_rows` are the **landed fixes**,
   not the Kalman-smoothed states the pipeline classifies. The classifier is
   the same function; its input is one stage rawer, so a verdict here can
   differ from the pipeline's on a noisy track. Every response that uses one
   says `track_basis: "landed fixes, unsmoothed"`.
2. `tracks/vessel_type.py` has no landed model. Nothing in the shipped pipeline
   trains one, so the vocabulary and the confusion matrix that ADR-033 calls
   the product are **measured on request** and cached for the life of the
   process, over a bounded slice of the corpus that the response names.
"""
from __future__ import annotations

from typing import Any, Optional

from ..assistant.attribution import origin_of
from ..config import CLI
from .reader import Reader, as_iso, open_reader

__all__ = [
    "CHECK_GROUPS", "vessel_checks", "checks_coverage", "vessel_motion",
    "contact_profile", "eo_captures", "eo_summary", "vessel_type_model",
    "interaction_capability",
]

#: The three outcomes every rule module in `anomaly/` returns, in the order a
#: reader wants them: what was wrong, what was fine, what could not be asked.
OUTCOMES = ("contradiction", "ok", "not_checkable")


def _zero_counts() -> dict:
    return {o: 0 for o in OUTCOMES}


def _tally(findings) -> dict:
    counts = _zero_counts()
    for f in findings:
        outcome = f.get("outcome") if isinstance(f, dict) else f.outcome
        if outcome in counts:
            counts[outcome] += 1
    return counts


# --------------------------------------------------------------------------
# attribution: origin is an outside body, derivation is what we then did
# --------------------------------------------------------------------------
#
# ADR-038's split, applied per rule family. `origin` names the feed or register
# an operator could go and check independently; `derivation` names what this
# system did to those facts to reach the verdict on screen. A derived claim
# carried under its source's name is the source asserting something it never
# said, so the two are separate fields and both travel to the client.

CHECK_GROUPS: dict[str, dict] = {
    "identity": {
        "label": "Declared identity",
        "question": "Is what she says about herself self-consistent?",
        "area": "Area 2",
        "adr": "ADR-032",
        "module": "anomaly/identity.py",
        "origin": "AIS static reports and Global Fishing Watch vessel identity "
                  "records",
        "derivation": "derived by this system: the IMO check digit was "
                      "recomputed from the first six digits, the MMSI's "
                      "Maritime Identification Digits were read against the ITU "
                      "allocation for the flag she declares, and her broadcast "
                      "name, call sign and type were compared field by field "
                      "against the registry record held for her",
        "boundary": "The MID table is deliberately partial. An unallocated "
                    "prefix returns not checkable rather than a guess, because "
                    "one wrong row would cost this rule the only thing it has.",
    },
    "voyage": {
        "label": "Declared voyage",
        "question": "Does her declared destination and arrival time match her "
                    "track?",
        "area": "Area 2",
        "adr": "ADR-035",
        "module": "anomaly/voyage.py",
        "origin": "AIS voyage declarations (message 5) and AIS position reports",
        "derivation": "derived by this system: the passage from her position to "
                      "the port she named was measured against the time she has "
                      "left, and every fix between the declaration and her "
                      "stated arrival was tested for whether she was ever "
                      "heading there",
        "boundary": "An expired arrival time is not tested at all. The question "
                    "the rule asks is forward looking, and a ship running late "
                    "is the commonest thing at sea.",
    },
    "paperwork": {
        "label": "Arrival notification",
        "question": "Does the form her agent filed match what she did?",
        "area": "Area 4",
        "adr": "ADR-036",
        "module": "anomaly/paperwork.py",
        "origin": "Pre-arrival notifications filed by agents, read from the "
                  "documents as received",
        "derivation": "derived by this system: the declared last port was "
                      "compared against the calls recorded for her before "
                      "filing, the declared arrival against the call she "
                      "actually made at the port named, and a declaration of no "
                      "cargo against the draught she was broadcasting",
        "boundary": "Declared cargo in general is not checked. A part-laden "
                    "hull, an honest ballast voyage and a lie look the same "
                    "from motion; only the arithmetic case is claimed.",
    },
    "imagery": {
        "label": "Camera against declared type",
        "question": "Does a photograph agree with the type she declares?",
        "area": "Area 5",
        "adr": "ADR-037",
        "module": "anomaly/imagery.py",
        "origin": "Electro-optical captures — SIMULATED, no camera exists",
        "derivation": "derived by this system: the classifier's label was "
                      "reduced to the set of AIS ship-type families it leaves "
                      "open, and her declared type was tested for membership of "
                      "that set",
        "boundary": "A single disagreeing look is never a finding. Two decisive "
                    "looks must agree AND be a majority of the looks that "
                    "decided anything either way.",
    },
}


def _group_shell(key: str) -> dict:
    spec = CHECK_GROUPS[key]
    return {
        "key": key,
        "label": spec["label"],
        "question": spec["question"],
        "area": spec["area"],
        "adr": spec["adr"],
        "module": spec["module"],
        "origin": spec["origin"],
        "derivation": spec["derivation"],
        "boundary": spec["boundary"],
        "counts": _zero_counts(),
        "findings": [],
        "note": None,
    }


def _finding_dict(f, **extra) -> dict:
    """A rule module's finding, flattened for the wire.

    `as_dict` differs between the three modules — `voyage` and `paperwork`
    splat `detail` into the top level, `identity` nests it — so the fields the
    client needs are named explicitly here rather than spread.
    """
    out = {
        "check": f.check,
        "outcome": f.outcome,
        "confidence": round(float(f.confidence), 3),
        "statement": f.statement,
        "detail": dict(getattr(f, "detail", {}) or {}),
    }
    # `paperwork` findings carry the passage the value was read from and where
    # in the document it sat. That is the whole of Area 4's evidence bar: a
    # field an analyst cannot put a finger on is not usable as evidence.
    if getattr(f, "passage", None):
        out["passage"] = f.passage
    if getattr(f, "locator", None):
        out["locator"] = f.locator
    out.update(extra)
    return out


# --------------------------------------------------------------------------
# id helpers
# --------------------------------------------------------------------------

def _conformed_keys(canonical: str) -> list[str]:
    from .service import _conformed_keys as keys
    return keys(canonical)


def _canonical(conformed: str) -> str:
    from .service import canonical_id
    return canonical_id(conformed)


def _in_clause(n: int) -> str:
    return ",".join(["?"] * n)


# --------------------------------------------------------------------------
# a track the classifiers can read, built from landed fixes
# --------------------------------------------------------------------------

class _FixTrack:
    """The minimum a `tracks/` classifier reads, over landed fixes.

    `tracks.activity` and `tracks.vessel_type` take a track and touch exactly
    two things on it: `points`, a frame carrying ts / lat / lon / sog_kn /
    cog_deg / quality, and the two id attributes. Nothing here reimplements a
    classifier; this only assembles the input so the real one can run on demand
    against what is landed.

    **It is one stage rawer than what the pipeline classifies.** A `BuiltTrack`
    carries Kalman-smoothed states and an outlier flag earned by the filter;
    these are the fixes as they were received, with every point marked usable.
    A noisy track can therefore classify differently here, which is why every
    response built from one says so.
    """

    BASIS = ("landed fixes, unsmoothed — the track engine's Kalman filter has "
             "not been run over these points")

    def __init__(self, track_id: str, track_key: str, points, mmsi=None):
        self.track_id = track_id
        self.track_key = track_key
        self.points = points
        self.mmsi = mmsi


def _frame(rows: list[dict]):
    """A points frame from landed rows, or None if there is not enough."""
    import pandas as pd

    keep = [r for r in rows
            if r.get("lat") is not None and r.get("lon") is not None
            and r.get("ts") is not None]
    if len(keep) < 4:
        return None
    df = pd.DataFrame({
        "ts": pd.to_datetime([r["ts"] for r in keep], utc=True),
        "lat": [float(r["lat"]) for r in keep],
        "lon": [float(r["lon"]) for r in keep],
        # A missing speed or course is filled with zero rather than dropped:
        # dropping the fix would silently shorten the track, and the
        # classifiers already treat a stationary reading as a stationary
        # reading. Where a whole track has neither, the verdict comes back
        # `unclassified`, which is the honest answer.
        "sog_kn": [float(r.get("sog_kn") or 0.0) for r in keep],
        "cog_deg": [float(r.get("cog_deg") or 0.0) for r in keep],
        "quality": ["ok"] * len(keep),
    }).sort_values("ts").reset_index(drop=True)
    return df


def _ais_track(reader: Reader, vessel_id: str, *, limit: int = 6000):
    """Her landed AIS fixes as a track, or None."""
    if not reader.has("ais_position"):
        return None
    keys = _conformed_keys(vessel_id)
    cols = "ts, lat, lon"
    have = reader.columns("ais_position")
    if "sog_kn" in have:
        cols += ", sog_kn"
    if "cog_deg" in have:
        cols += ", cog_deg"
    rows = reader.rows(
        f"SELECT {cols} FROM ais_position "
        f"WHERE vessel_id IN ({_in_clause(len(keys))}) AND lat IS NOT NULL "
        f"ORDER BY ts LIMIT {int(limit)}", keys)
    df = _frame(rows)
    if df is None:
        return None
    return _FixTrack(f"ais:{vessel_id}", vessel_id, df)


# --------------------------------------------------------------------------
# 1. the four rule families, for one subject
# --------------------------------------------------------------------------

def _identity_rows(reader: Reader, keys: list[str]) -> Optional[dict]:
    """Her current broadcast row with the registry attestation attached.

    The same shape `tools/run_scenario_pipeline._current_identities` builds, for
    one hull rather than the fleet. **The two are separate copies of one query
    and can drift**; the pipeline is not importable (it is a script, not a
    package), so this is stated rather than shared. If the identity check ever
    reports differently here and there, this is the first place to look.

    Taking the most recent row per hull and stopping is the defect that ADR-032
    records: it collapses her broadcast identity and her registry entry into
    whichever sorted first, so the consistency check has nothing to compare
    against and answers "cannot check" for the whole fleet.
    """
    if not reader.has("gfw_vessel_identity"):
        return None
    rows = reader.rows(
        "SELECT * FROM ("
        "  SELECT *, row_number() OVER ("
        "    PARTITION BY vessel_id, record_kind"
        "    ORDER BY (valid_to IS NULL) DESC, valid_from DESC NULLS LAST"
        "  ) AS _rn FROM gfw_vessel_identity"
        f"  WHERE vessel_id IN ({_in_clause(len(keys))})"
        ") WHERE _rn = 1", keys)
    return _fold_identity(rows)


#: Record kinds that are the hull talking about herself. Everything else is a
#: third party attesting to who she is, and the disagreement between the two is
#: the signal the consistency check exists to find.
_BROADCAST_KINDS = ("ais_static", "broadcast", "ais")


def _fold_identity(rows: list[dict]) -> Optional[dict]:
    by_kind = {r.get("record_kind"): r for r in rows if r.get("vessel_id")}
    if not by_kind:
        return None
    broadcast = next((by_kind[k] for k in _BROADCAST_KINDS if k in by_kind), None)
    attestations = {k: v for k, v in by_kind.items() if k not in _BROADCAST_KINDS}
    if broadcast is None:
        broadcast = next(iter(by_kind.values()))
        attestations = {}
    row = dict(broadcast)
    if attestations:
        att = next(iter(attestations.values()))
        row["registry"] = {"name": att.get("ship_name"),
                           "call_sign": att.get("call_sign"),
                           "vessel_class": att.get("vessel_class")}
        row["registry_record_kind"] = att.get("record_kind")
    return row


def _identity_group(reader: Reader, keys: list[str]) -> dict:
    from ..anomaly.identity import check_identity

    g = _group_shell("identity")
    row = _identity_rows(reader, keys)
    if row is None:
        g["note"] = ("No identity record is landed for this subject, so nothing "
                     "about her declared identity can be checked.")
        return g
    findings = check_identity(
        mmsi=row.get("mmsi"), imo=row.get("imo"), flag=row.get("flag"),
        name=row.get("ship_name"), call_sign=row.get("call_sign"),
        vessel_class=row.get("vessel_class"), registry=row.get("registry"))
    g["findings"] = [_finding_dict(f) for f in findings]
    g["counts"] = _tally(g["findings"])
    g["subject_row"] = {
        "mmsi": _s(row.get("mmsi")), "imo": _s(row.get("imo")),
        "flag": row.get("flag"), "ship_name": row.get("ship_name"),
        "call_sign": row.get("call_sign"),
        "vessel_class": row.get("vessel_class"),
        "registry": row.get("registry"),
        "registry_record_kind": row.get("registry_record_kind"),
    }
    if not row.get("registry"):
        g["note"] = ("Only one identity record kind is held for her, so there is "
                     "nothing to compare her broadcast name, call sign and type "
                     "against. That is a gap in the record, not a clean result.")
    return g


#: How many of her declarations are checked on one request. A hull broadcasts a
#: message 5 every few hours over an eight-week corpus; checking all of them
#: would be a hundred verdicts on one screen saying the same thing. The most
#: recent are the ones an operator is asking about.
MAX_DECLARATIONS = 6


def _voyage_group(reader: Reader, keys: list[str], track) -> dict:
    from ..anomaly.voyage import check_voyage
    from ..tracks.kalman import epoch_s

    g = _group_shell("voyage")
    if not reader.has("ais_voyage"):
        g["note"] = ("No AIS voyage declarations are landed. She has not been "
                     "heard to declare a destination, which is not the same as "
                     "her declaration being consistent.")
        return g
    rows = reader.rows(
        "SELECT timestamp, destination, eta, lat, lon, draught_m, nav_status "
        f"FROM ais_voyage WHERE vessel_id IN ({_in_clause(len(keys))}) "
        f"ORDER BY timestamp DESC LIMIT {MAX_DECLARATIONS}", keys)
    if not rows:
        g["note"] = ("She has never been heard to declare a destination in this "
                     "record.")
        return g

    fixes: list[tuple] = []
    if track is not None:
        pts = track.points
        fixes = list(zip(epoch_s(pts["ts"]).tolist(),
                         pts["lat"].tolist(), pts["lon"].tolist()))

    out: list[dict] = []
    for row in rows:
        declared_at = row.get("timestamp")
        after = [f for f in fixes
                 if declared_at is not None and f[0] >= declared_at.timestamp()]
        if row.get("lat") is None or row.get("lon") is None:
            continue
        findings = check_voyage(
            lat=float(row["lat"]), lon=float(row["lon"]),
            declared_at=declared_at, destination=row.get("destination"),
            eta=row.get("eta"), fixes=after)
        for f in findings:
            out.append(_finding_dict(
                f,
                declared={"destination": row.get("destination"),
                          "eta": as_iso(row.get("eta")),
                          "declared_at": as_iso(declared_at),
                          "draught_m": row.get("draught_m"),
                          "nav_status": row.get("nav_status")}))
    g["findings"] = out
    g["counts"] = _tally(out)
    if track is None:
        g["note"] = ("No landed positions for her, so the heading check has "
                     "nothing to read and answers not checkable.")
    g["declarations_checked"] = len(rows)
    return g


def _paperwork_group(reader: Reader, keys: list[str], track) -> dict:
    g = _group_shell("paperwork")
    if not reader.has("arrival_notification"):
        g["note"] = ("No arrival notifications are landed. Run "
                     f"`{CLI} scenario generate` (or point the inbox at real "
                     "documents) before reading this as a clean inbox.")
        return g
    rows = reader.rows(
        "SELECT * FROM arrival_notification "
        f"WHERE vessel_id IN ({_in_clause(len(keys))}) "
        "ORDER BY received_at DESC LIMIT 6", keys)
    if not rows:
        g["note"] = ("No arrival notification in this record names her. That is "
                     "itself a finding when she berthed — the detector for it "
                     "is `arrival_without_notification` and it reaches the "
                     "watch queue as an alert.")
        return g

    import pandas as pd

    from ..anomaly.paperwork import check_paperwork, match_arrival, window_before
    from ..ingest.pans.land import declared_fields
    from ..tracks.kalman import epoch_s

    fixes: list[tuple] = []
    if track is not None:
        pts = track.points
        fixes = list(zip(epoch_s(pts["ts"]).tolist(),
                         pts["lat"].tolist(), pts["lon"].tolist()))
    calls = _port_calls(reader, keys)
    draught = _last_draught(reader, keys)

    out: list[dict] = []
    for row in rows:
        declared = declared_fields(row)
        filed = pd.Timestamp(row.get("received_at"))
        if filed.tzinfo is None:
            filed = filed.tz_localize("UTC")
        before = [(c[2], c[3]) for c in calls
                  if c[0] < filed and c[2] is not None and c[3] is not None]
        observed = match_arrival(declared, [(t, name) for t, name, _, _ in calls],
                                 filed)
        findings = check_paperwork(
            declared=declared, fixes=window_before(fixes, filed),
            filed_at=filed, observed_arrival=observed, prior_calls=before,
            draught_m=draught)
        for f in findings:
            out.append(_finding_dict(
                f,
                document={"name": row.get("document_name"),
                          "format": row.get("document_format"),
                          "filed_at": as_iso(row.get("received_at")),
                          "filed_at_source": row.get("received_at_source")}))
    g["findings"] = out
    g["counts"] = _tally(out)
    g["notifications_checked"] = len(rows)
    return g


def _port_calls(reader: Reader, keys: list[str]) -> list[tuple]:
    """(when, port name, lat, lon) for every call the record holds, in order."""
    if not reader.has("gfw_port_visits"):
        return []
    import pandas as pd

    cols = reader.columns("gfw_port_visits")
    name_col = ("port_name" if "port_name" in cols
                else "port" if "port" in cols else "NULL")
    try:
        rows = reader.rows(
            f"SELECT start_time, {name_col} AS port_name, lat, lon "
            f"FROM gfw_port_visits WHERE vessel_id IN ({_in_clause(len(keys))}) "
            "ORDER BY start_time", keys)
    except Exception:                                            # noqa: BLE001
        return []
    out = []
    for r in rows:
        if r.get("start_time") is None:
            continue
        out.append((pd.Timestamp(r["start_time"]), r.get("port_name"),
                    r.get("lat"), r.get("lon")))
    return out


def _last_draught(reader: Reader, keys: list[str]) -> Optional[float]:
    if not reader.has("ais_voyage"):
        return None
    v = reader.scalar(
        f"SELECT draught_m FROM ais_voyage WHERE vessel_id IN ({_in_clause(len(keys))}) "
        "AND draught_m IS NOT NULL ORDER BY timestamp DESC LIMIT 1", keys)
    return float(v) if v is not None else None


def _imagery_group(reader: Reader, keys: list[str], declared_class) -> dict:
    g = _group_shell("imagery")
    caps = _capture_rows(reader, keys)
    if caps is None:
        g["note"] = ("No electro-optical captures are landed. The loop is built "
                     "and has never run against a camera, because there is no "
                     "camera.")
        return g
    if not caps:
        g["note"] = ("No camera was ever pointed at her. The cue ledger says "
                     "why for every slot it declined.")
        return g

    from ..anomaly.imagery import check_declared_type

    out: list[dict] = []
    for row in caps:
        verdict = _verdict_view(row)
        f = check_declared_type(
            declared_class=declared_class, verdict=verdict,
            quality=float(row.get("image_quality") or 0.0),
            band=row.get("band"))
        out.append(_finding_dict(f, capture=_capture_view(row)))
    g["findings"] = out
    g["counts"] = _tally(out)
    g["captures"] = len(caps)
    g["simulated"] = True
    g["note"] = ("Every capture below is SIMULATED. No lens exists and no image "
                 "has ever been examined; the loop consumes six numeric "
                 "measurements a vision model would extract from a photograph.")
    return g


class _RowVerdict:
    """The classifier's landed verdict, in the shape `anomaly.imagery` reads.

    Read off the capture row, never re-derived. `imaged_families` in particular
    is landed because a label means what the model that emitted it meant by it,
    and recomputing it against a different model's vocabulary is the defect
    ADR-037 records as accusing 36% of an honest fleet.
    """

    def __init__(self, row: dict):
        self.band = row.get("band") or "visible"
        self.imaged_type = row.get("imaged_type")
        self.fine_type = row.get("fine_type")
        self.confidence = float(row.get("type_confidence") or 0.0)
        fams = row.get("imaged_families")
        self.imaged_families = (frozenset(str(fams).split(","))
                                if fams else frozenset())
        self.not_classifiable = row.get("not_classifiable")
        self.model_name = row.get("model_name")
        self.model_provenance = row.get("model_provenance")
        self.identity_subject = row.get("identity_subject")
        self.identity_confidence = row.get("identity_confidence")
        self.identity_basis = row.get("identity_basis")

    @property
    def is_claim(self) -> bool:
        return bool(self.imaged_type) and not self.not_classifiable


def _verdict_view(row: dict) -> _RowVerdict:
    return _RowVerdict(row)


def _capture_rows(reader: Reader, keys: list[str]) -> Optional[list[dict]]:
    if not reader.has("eo_capture"):
        return None
    subjects = list(keys) + [_canonical(k) for k in keys]
    subjects = list(dict.fromkeys(subjects))
    return reader.rows(
        f"SELECT * FROM eo_capture WHERE subject_id IN ({_in_clause(len(subjects))}) "
        "ORDER BY taken_at DESC LIMIT 40", subjects)


def _capture_view(row: dict) -> dict:
    """One capture, as the screen needs it. `simulated` is not optional."""
    return {
        "capture_id": row.get("capture_id"),
        "taken_at": as_iso(row.get("taken_at")),
        "station": row.get("station"),
        "camera_id": row.get("camera_id"),
        "range_km": row.get("range_km"),
        "bearing_deg": row.get("bearing_deg"),
        "band": row.get("band"),
        "image_quality": row.get("image_quality"),
        "visibility_km": row.get("visibility_km"),
        "solar_elevation_deg": row.get("solar_elevation_deg"),
        "target_present": bool(row.get("target_present")),
        "target_kind": row.get("target_kind"),
        "imaged_type": row.get("imaged_type"),
        "fine_type": row.get("fine_type"),
        "type_confidence": row.get("type_confidence"),
        "imaged_families": ([f for f in str(row["imaged_families"]).split(",")]
                            if row.get("imaged_families") else []),
        "not_classifiable": row.get("not_classifiable"),
        "identity_subject": row.get("identity_subject"),
        "identity_confidence": row.get("identity_confidence"),
        "identity_basis": row.get("identity_basis"),
        "model_name": row.get("model_name"),
        "model_provenance": row.get("model_provenance"),
        "statement": row.get("statement"),
        # Why the camera was pointed here rather than somewhere else. ADR-037
        # calls this the valuable part of the area, and it was landed on every
        # row and never shown.
        "cue_sentence": row.get("cue_sentence"),
        "cue_priority": row.get("cue_priority"),
        # Never inferred, never defaulted: the row's own answer to "was a real
        # lens involved". `image_ref` is empty because there is no file.
        "capture_mode": row.get("capture_mode") or "simulated",
        "image_ref": row.get("image_ref"),
        "source_name": row.get("source_name"),
        "subject_id": row.get("subject_id"),
        "track_id": row.get("track_id"),
        "lat": row.get("lat"), "lon": row.get("lon"),
    }


def _declared_class(reader: Reader, keys: list[str]) -> Optional[str]:
    if not reader.has("gfw_vessel_identity"):
        return None
    return reader.scalar(
        "SELECT vessel_class FROM gfw_vessel_identity "
        f"WHERE vessel_id IN ({_in_clause(len(keys))}) AND vessel_class IS NOT NULL "
        "ORDER BY (valid_to IS NULL) DESC, valid_from DESC NULLS LAST LIMIT 1",
        keys)


def vessel_checks(subject_id: str) -> dict:
    """Every rule-module verdict this system holds about one subject.

    All three outcomes, always. A response carrying only contradictions would
    let a screen render a hull nobody could check as a hull that passed.
    """
    keys = _conformed_keys(subject_id)
    with open_reader() as reader:
        track = _ais_track(reader, subject_id)
        declared = _declared_class(reader, keys)
        groups = [
            _identity_group(reader, keys),
            _voyage_group(reader, keys, track),
            _paperwork_group(reader, keys, track),
            _imagery_group(reader, keys, declared),
        ]
    totals = _zero_counts()
    for g in groups:
        for k in OUTCOMES:
            totals[k] += g["counts"][k]
    return {
        "subject_id": subject_id,
        "declared_vessel_class": declared,
        "groups": groups,
        "totals": totals,
        "outcomes": list(OUTCOMES),
        "track_basis": _FixTrack.BASIS if track is not None else None,
        "note": (None if any(g["findings"] for g in groups) else
                 "Nothing about this subject could be checked. That is a "
                 "statement about the record, not about the vessel."),
    }


# --------------------------------------------------------------------------
# 2. the same checks, corpus wide — where "not checkable" is really visible
# --------------------------------------------------------------------------

#: Rows read for a coverage sweep. The point of the sweep is the shape of the
#: three-way split, and that shape is stable long before the corpus is
#: exhausted. `scanned` and `total` are both reported so a partial sweep can
#: never be read as a complete one.
COVERAGE_LIMIT = 4000


def checks_coverage() -> dict:
    """The three-way split of every row-wise check, over the landed corpus.

    **Only the checks that read one row are swept here.** The behavioural halves
    — was she ever heading towards the port she named, was she ever near the
    port her agent declared — need her whole track, and running that for the
    fleet is a pipeline job rather than an HTTP request. They are named in
    `per_subject_only` so their absence from these counts is visible rather
    than inferred.
    """
    groups: list[dict] = []
    with open_reader() as reader:
        groups.append(_coverage_identity(reader))
        groups.append(_coverage_voyage(reader))
        groups.append(_coverage_paperwork(reader))
        groups.append(_coverage_imagery(reader))
    return {
        "groups": groups,
        "outcomes": list(OUTCOMES),
        "per_subject_only": [
            {"check": "declared_destination_agrees", "group": "voyage",
             "why": "needs every fix between the declaration and her stated "
                    "arrival"},
            {"check": "declared_last_port", "group": "paperwork",
             "why": "needs her recorded calls and her track before filing"},
            {"check": "declared_arrival_window", "group": "paperwork",
             "why": "needs the call she made at the port the form names"},
        ],
        "note": ("Percentages are of the rows swept, not of the fleet. A check "
                 "that is not checkable on most of a corpus has told you "
                 "almost nothing, and this is where that shows."),
    }


def _coverage_shell(key: str, **extra) -> dict:
    g = _group_shell(key)
    g.pop("findings")
    g.update(extra)
    return g


def _coverage_identity(reader: Reader) -> dict:
    from ..anomaly.identity import check_identity, summarise

    g = _coverage_shell("identity", scanned=0, total=0, by_check={})
    if not reader.has("gfw_vessel_identity"):
        g["note"] = "No identity records are landed."
        return g
    rows = reader.rows(
        "SELECT * FROM ("
        "  SELECT *, row_number() OVER ("
        "    PARTITION BY vessel_id, record_kind"
        "    ORDER BY (valid_to IS NULL) DESC, valid_from DESC NULLS LAST"
        "  ) AS _rn FROM gfw_vessel_identity) WHERE _rn = 1")
    by_vessel: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("vessel_id"):
            by_vessel.setdefault(r["vessel_id"], []).append(r)
    g["total"] = len(by_vessel)
    findings = []
    for vid, kind_rows in list(by_vessel.items())[:COVERAGE_LIMIT]:
        row = _fold_identity(kind_rows)
        if row is None:
            continue
        g["scanned"] += 1
        findings += check_identity(
            mmsi=row.get("mmsi"), imo=row.get("imo"), flag=row.get("flag"),
            name=row.get("ship_name"), call_sign=row.get("call_sign"),
            vessel_class=row.get("vessel_class"), registry=row.get("registry"))
    g["counts"] = _tally([{"outcome": f.outcome} for f in findings])
    g["by_check"] = summarise(findings)
    g["unit"] = "hull"
    return g


def _coverage_voyage(reader: Reader) -> dict:
    from ..anomaly.voyage import check_arrival_feasible

    g = _coverage_shell("voyage", scanned=0, total=0, by_check={})
    g["checks_swept"] = ["declared_eta_feasible"]
    if not reader.has("ais_voyage"):
        g["note"] = "No AIS voyage declarations are landed."
        return g
    g["total"] = int(reader.scalar("SELECT count(*) FROM ais_voyage") or 0)
    rows = reader.rows(
        "SELECT timestamp, destination, eta, lat, lon FROM ais_voyage "
        f"WHERE lat IS NOT NULL ORDER BY timestamp DESC LIMIT {COVERAGE_LIMIT}")
    findings = []
    for r in rows:
        g["scanned"] += 1
        findings.append(check_arrival_feasible(
            lat=float(r["lat"]), lon=float(r["lon"]),
            declared_at=r.get("timestamp"), destination=r.get("destination"),
            eta=r.get("eta")))
    g["counts"] = _tally([{"outcome": f.outcome} for f in findings])
    g["by_check"] = {"declared_eta_feasible": dict(g["counts"])}
    g["unit"] = "declaration"
    return g


def _coverage_paperwork(reader: Reader) -> dict:
    g = _coverage_shell("paperwork", scanned=0, total=0, by_check={})
    g["checks_swept"] = ["declared_ballast"]
    if not reader.has("arrival_notification"):
        g["note"] = "No arrival notifications are landed."
        return g
    from ..anomaly.paperwork import check_declared_ballast
    from ..ingest.pans.land import declared_fields

    g["total"] = int(reader.scalar("SELECT count(*) FROM arrival_notification") or 0)
    rows = reader.rows("SELECT * FROM arrival_notification "
                       f"ORDER BY received_at DESC LIMIT {COVERAGE_LIMIT}")
    draughts = _draught_index(reader)
    findings = []
    for r in rows:
        g["scanned"] += 1
        findings.append(check_declared_ballast(
            declared=declared_fields(r),
            draught_m=draughts.get(r.get("vessel_id"))))
    g["counts"] = _tally([{"outcome": f.outcome} for f in findings])
    g["by_check"] = {"declared_ballast": dict(g["counts"])}
    g["unit"] = "notification"
    return g


def _draught_index(reader: Reader) -> dict:
    if not reader.has("ais_voyage"):
        return {}
    rows = reader.rows(
        "SELECT vessel_id, max(draught_m) AS d FROM ais_voyage "
        "WHERE draught_m IS NOT NULL GROUP BY vessel_id")
    return {r["vessel_id"]: float(r["d"]) for r in rows if r.get("d") is not None}


def _coverage_imagery(reader: Reader) -> dict:
    g = _coverage_shell("imagery", scanned=0, total=0, by_check={})
    g["simulated"] = True
    if not reader.has("eo_capture"):
        g["note"] = ("No electro-optical captures are landed. There is no "
                     "camera; the loop lands simulated captures when the "
                     "pipeline runs it.")
        return g
    from ..anomaly.imagery import check_declared_type

    g["total"] = int(reader.scalar("SELECT count(*) FROM eo_capture") or 0)
    rows = reader.rows("SELECT * FROM eo_capture ORDER BY taken_at DESC "
                       f"LIMIT {COVERAGE_LIMIT}")
    declared = _declared_class_index(reader)
    findings = []
    for r in rows:
        g["scanned"] += 1
        subject = r.get("subject_id")
        findings.append(check_declared_type(
            declared_class=declared.get(subject) or declared.get(
                _native_of(subject)),
            verdict=_verdict_view(r),
            quality=float(r.get("image_quality") or 0.0),
            band=r.get("band")))
    g["counts"] = _tally([{"outcome": f.outcome} for f in findings])
    g["by_check"] = {"imagery_declared_type": dict(g["counts"])}
    g["unit"] = "capture"
    return g


def _native_of(subject_id) -> str:
    if not subject_id:
        return ""
    from ..schemas.keys import native_vessel_id
    try:
        return native_vessel_id(str(subject_id))
    except Exception:                                            # noqa: BLE001
        return str(subject_id)


def _declared_class_index(reader: Reader) -> dict:
    if not reader.has("gfw_vessel_identity"):
        return {}
    rows = reader.rows(
        "SELECT vessel_id, any_value(vessel_class) AS c FROM gfw_vessel_identity "
        "WHERE vessel_class IS NOT NULL GROUP BY vessel_id")
    out = {}
    for r in rows:
        out[r["vessel_id"]] = r["c"]
        out[_canonical(r["vessel_id"])] = r["c"]
        out[_native_of(r["vessel_id"])] = r["c"]
    return out


# --------------------------------------------------------------------------
# 3. what she is doing, from motion alone
# --------------------------------------------------------------------------

def vessel_motion(subject_id: str, *, window_hours: float = 6.0) -> dict:
    """Activity classification, the area baseline, and the projection.

    Three capabilities that all read motion and nothing else, gathered because
    an operator asks one question of them: what is she doing, is that normal
    here, and where does that put her next.
    """
    from ..tracks.activity import (activity_features, classify_activity,
                                   classify_activity_segments,
                                   dominant_activity)

    with open_reader() as reader:
        track = _ais_track(reader, subject_id)
        if track is None:
            return {"subject_id": subject_id, "available": False,
                    "note": ("No landed positions for this subject, so nothing "
                             "can be said about her motion."),
                    "activity": None, "episodes": [], "baseline": None,
                    "projection": None}
        whole = classify_activity(track)
        episodes = list(classify_activity_segments(track,
                                                   window_hours=window_hours))
        dominant = dominant_activity(episodes) or whole
        feats = activity_features(track)
        baseline = _baseline_at(whole.lat, whole.lon, feats.get("sog_p90"))
        projection = _projection_for(track)

    return {
        "subject_id": subject_id,
        "available": True,
        "track_basis": _FixTrack.BASIS,
        "n_points": feats.get("n_points"),
        "span_hours": round((feats.get("span_minutes") or 0.0) / 60.0, 1),
        "activity": _activity_view(dominant),
        "whole_track": _activity_view(whole),
        "episodes": [_activity_view(a) for a in episodes[:24]],
        "n_episodes": len(episodes),
        "vocabulary_note": (
            "`unclassified` is a first-class answer here, not a failure. A "
            "confident wrong activity costs more than an admitted gap."),
        "origin": origin_of("ais_position"),
        "derivation": ("derived by this system: her landed fixes were resampled "
                       "and her speed, turn rate, straightness and spread "
                       "measured over each window, then matched against the "
                       "activity rules. Nothing reads a sensor name, which is "
                       "what lets the same rules answer for a radar track."),
        "baseline": baseline,
        "projection": projection,
    }


def _activity_view(a) -> dict:
    """One classified activity, as the screen needs it.

    `unclassified` is flagged explicitly rather than left for the client to
    infer from a string comparison. It is a first-class output of the rule set
    — "nothing in the rules describes this motion" — and a surface that dropped
    it or drew it as a pale version of a real label would be reporting a gap as
    a finding.
    """
    d = a.as_dict()
    d["t_start_iso"] = _iso_epoch(d.get("t_start"))
    d["t_end_iso"] = _iso_epoch(d.get("t_end"))
    d["unclassified"] = a.activity == "unclassified"
    # The kinematics behind the label, so a disagreeing analyst reads numbers
    # rather than arguing with a verdict. Trimmed to what a person reads.
    feats = d.pop("features", {}) or {}
    d["measurements"] = {k: feats[k] for k in
                         ("sog_median", "sog_p90", "turn_rate_deg_min",
                          "straightness", "spread_m", "span_minutes",
                          "in_waiting_area", "nearest_port")
                         if k in feats}
    return d


def _iso_epoch(v):
    if v is None:
        return None
    from datetime import datetime, timezone
    try:
        return datetime.fromtimestamp(float(v), tz=timezone.utc).isoformat()
    except Exception:                                            # noqa: BLE001
        return None


def _baseline_at(lat, lon, sog_p90) -> Optional[dict]:
    """What normal looks like where she is — or that we have no opinion.

    The `None` return from `baselines.is_unusual` is not folded into "ordinary".
    A cell we have not watched enough is a cell we cannot judge, and rendering
    those as normal would report every unmonitored patch of ocean as clean.
    """
    from .. import baselines as bl

    if lat is None or lon is None:
        return None
    try:
        rows = bl.load_baselines()
    except Exception:                                            # noqa: BLE001
        rows = []
    index = bl.BaselineIndex(rows)
    coverage = index.coverage()
    if not rows:
        return {"state": "no_layer", "coverage": coverage,
                "statement": ("No area baselines are landed, so there is no "
                              "local normal to compare her against. Run "
                              f"`{CLI} baselines derive`."),
                "cell": None}
    cell = index.at(float(lat), float(lon))
    verdict = (bl.is_unusual(index, lat=float(lat), lon=float(lon),
                             metric="sog_kn", value=float(sog_p90),
                             percentile=95)
               if sog_p90 is not None else None)
    if verdict is None:
        return {
            "state": "no_opinion",
            "coverage": coverage,
            "cell": (cell.as_dict() if cell else None),
            "statement": (
                "We have not watched this cell enough to have an opinion about "
                "what is normal here." if cell is None or not cell.usable else
                "Her speed could not be measured, so there is nothing to "
                "compare against this cell's normal."),
        }
    return {
        "state": "unusual" if verdict["unusual"] else "ordinary",
        "coverage": coverage,
        "cell": (cell.as_dict() if cell else None),
        "statement": verdict["statement"],
        "detail": verdict,
    }


#: Leads at which the cone is reported on a subject page. Not a drawn path —
#: the map draws that — but the numbers behind it, so "how sure were you" is
#: answerable in words as well as in pixels.
PROJECTION_LEADS_H = (1.0, 3.0, 6.0)


def _projection_for(track) -> Optional[dict]:
    from ..tracks.kalman import epoch_s
    from ..tracks.projection import CONE_GROWTH_M_PER_HOUR, project_from

    pts = track.points
    if len(pts) < 2:
        return None
    t = epoch_s(pts["ts"])
    made_at = float(t[-1])
    lat = float(pts["lat"].iloc[-1])
    lon = float(pts["lon"].iloc[-1])
    sog = float(pts["sog_kn"].iloc[-1])
    cog = float(pts["cog_deg"].iloc[-1])
    steps = []
    for lead in PROJECTION_LEADS_H:
        p = project_from(lat=lat, lon=lon, sog_kn=sog, cog_deg=cog,
                         made_at=made_at, valid_for=made_at + lead * 3600.0,
                         track_id=None, track_source="ais")
        steps.append({"lead_hours": lead, "lat": round(p.lat, 5),
                      "lon": round(p.lon, 5),
                      "radius_km": round(p.radius_m / 1000.0, 2),
                      "confidence": round(p.confidence, 3)})
    return {
        "made_at": _iso_epoch(made_at),
        "from": {"lat": round(lat, 5), "lon": round(lon, 5),
                 "sog_kn": round(sog, 2), "cog_deg": round(cog, 1)},
        "steps": steps,
        # The growth rate is READ from the projection module rather than
        # written out here. `tracks/projection.py` owns the number; a sentence
        # in the serving layer that quotes it from memory becomes a lie the
        # first time somebody retunes the cone, and it would be a quiet one.
        "basis": ("Dead reckoning from her last landed fix: she holds course "
                  "and speed. The cone grows at "
                  f"{CONE_GROWTH_M_PER_HOUR / 1852.0:.2g} nm per hour of lead "
                  "and is capped by what a hull could physically do."),
        # The half that has to be on the screen and not in a tooltip.
        "caveat": ("This is an expectation, not a suspicion signal. Departure "
                   "from a projection flags 98% of the fleet at any usable "
                   "threshold, because every vessel alters course at every "
                   "waypoint, so this system deliberately does not carry it as "
                   "a factor on the watch list (ADR-032)."),
        "origin": origin_of("ais_position"),
        "derivation": ("derived by this system: dead reckoning from her last "
                       "fix, with an uncertainty cone widening with the lead"),
    }


# --------------------------------------------------------------------------
# 4. the contact nobody can name
# --------------------------------------------------------------------------

def contact_profile(candidate_id: str) -> Optional[dict]:
    """Profile one radar dark contact: type, activity, zone, and the gaps.

    *"'Unidentified contact' is a position. 'Probable fishing vessel, loitering,
    no transponder, inside territorial waters' is intelligence."*

    **It profiles, it does not detect.** The darkness verdict is the cascade's
    and is carried through untouched; nothing here re-decides it.
    """
    from ..fusion.contact_profile import profile_contact
    from ..ingest.landing import read_table
    from ..ingest.radar import TABLE as RADAR_TABLE
    from ..fusion.radar_ais import CONTACT_TABLE

    try:
        contacts = read_table(CONTACT_TABLE)
    except Exception:                                            # noqa: BLE001
        contacts = []
    row = next((c for c in contacts
                if str(c.get("candidate_id")) == str(candidate_id)), None)
    if row is None:
        return None

    try:
        plots = read_table(RADAR_TABLE)
    except Exception:                                            # noqa: BLE001
        plots = []
    tid = str(row.get("radar_track_id"))
    mine = [p for p in plots if str(p.get("radar_track_id")) == tid]
    mine.sort(key=lambda p: str(p.get("ts")))
    df = _frame(mine)
    if df is None:
        return {
            "candidate_id": candidate_id,
            "radar_track_id": tid,
            "available": False,
            "note": ("Too few landed radar plots for this track to describe "
                     "her motion. The cascade's verdict stands unchanged; only "
                     "the description is missing."),
            "correlation_status": row.get("correlation_status"),
            "status": row.get("status"),
        }
    track = _FixTrack(tid, tid, df)
    zone_index = _zone_index()
    prof = profile_contact(
        track, type_model=None, zone_index=zone_index,
        correlation_status=row.get("correlation_status"),
        length_m=row.get("length_m"),
        is_synthetic=bool(row.get("is_synthetic")))
    out = prof.as_dict()
    out.update({
        "candidate_id": candidate_id,
        "available": True,
        "status": row.get("status"),
        "track_basis": _FixTrack.BASIS,
        "origin": origin_of("radar"),
        "derivation": ("derived by this system: the cascade's verdict was "
                       "carried through unchanged, and her activity and the "
                       "waters she is in were read from her radar motion and "
                       "the zone layer"),
        "profiles_not_detects": (
            "This describes a contact the cascade already called dark. It never "
            "revisits that verdict."),
    })
    return out


def _zone_index():
    """The zone layer, or None. Absence is a gap the profile records, not an
    error: `profile_contact` says "no zone layer was supplied" rather than
    quietly producing a thinner profile that looks the same as a confident one.
    """
    try:
        from ..zones.store import ZoneIndex, load_zones
        zones = load_zones()
        return ZoneIndex(zones) if zones else None
    except Exception:                                            # noqa: BLE001
        return None


# --------------------------------------------------------------------------
# 5. the electro-optical loop
# --------------------------------------------------------------------------

#: Said on every EO response and rendered on the image placeholder itself, not
#: in a footnote. There is no camera; a surface that showed a capture without
#: saying so would be the overclaim CLAUDE.md §5 treats as cardinal.
EO_SIMULATED = (
    "SIMULATED. No camera exists in this system. Every capture was produced by "
    "the scenario simulator through the CaptureSource seam, there is no image "
    "file behind it, and the classifier consumed six numeric measurements "
    "rather than pixels."
)


def eo_captures(*, subject_id: Optional[str] = None, limit: int = 100) -> dict:
    """Landed captures, newest first, each with the reason it was taken."""
    with open_reader() as reader:
        if not reader.has("eo_capture"):
            return {"items": [], "count": {"real": 0, "synthetic": 0},
                    "simulated": True, "disclosure": EO_SIMULATED,
                    "note": ("No electro-optical captures are landed. The "
                             "cueing, tagging, classification and mismatch rule "
                             "are built; the camera is not.")}
        if subject_id:
            rows = _capture_rows(reader, _conformed_keys(subject_id)) or []
        else:
            rows = reader.rows("SELECT * FROM eo_capture ORDER BY taken_at DESC "
                               f"LIMIT {int(limit)}")
        syn = "is_synthetic" in reader.columns("eo_capture")
        real = sum(1 for r in rows if syn and not r.get("is_synthetic"))
    return {
        "items": [_capture_view(r) for r in rows],
        "count": {"real": real, "synthetic": len(rows) - real},
        "simulated": True,
        "disclosure": EO_SIMULATED,
        "note": None,
    }


def eo_summary() -> dict:
    """What the loop did, and the two things it cannot do.

    The cue *reasoning* is the deliverable here. A watchkeeper who cannot find
    out why the system did not look at something goes back to slewing the
    camera by hand, so the sentence behind each tasking is landed and served.
    """
    with open_reader() as reader:
        if not reader.has("eo_capture"):
            return {"available": False, "simulated": True,
                    "disclosure": EO_SIMULATED,
                    "note": ("No captures are landed, so the loop has not been "
                             "run over this corpus."),
                    "stations": [], "bands": [], "totals": {}}
        totals = reader.one(
            "SELECT count(*) AS captures, "
            "       count(DISTINCT subject_id) AS subjects, "
            "       count(DISTINCT station) AS stations, "
            "       sum(CASE WHEN target_present THEN 0 ELSE 1 END) AS empty_frames, "
            "       sum(CASE WHEN imaged_type IS NOT NULL THEN 1 ELSE 0 END) AS claimed, "
            "       avg(image_quality) AS mean_quality, "
            "       avg(range_km) AS mean_range_km, "
            "       min(taken_at) AS first_at, max(taken_at) AS last_at "
            "FROM eo_capture") or {}
        stations = reader.rows(
            "SELECT station, count(*) AS n, avg(image_quality) AS q, "
            "       avg(range_km) AS r FROM eo_capture "
            "GROUP BY station ORDER BY n DESC LIMIT 40")
        bands = reader.rows(
            "SELECT band, count(*) AS n, avg(image_quality) AS q "
            "FROM eo_capture GROUP BY band ORDER BY n DESC")
        modes = reader.rows(
            "SELECT capture_mode, count(*) AS n FROM eo_capture "
            "GROUP BY capture_mode")
        recent = reader.rows("SELECT * FROM eo_capture ORDER BY taken_at DESC "
                             "LIMIT 12")
    return {
        "available": True,
        "simulated": True,
        "disclosure": EO_SIMULATED,
        "totals": {
            "captures": int(totals.get("captures") or 0),
            "subjects": int(totals.get("subjects") or 0),
            "stations": int(totals.get("stations") or 0),
            "empty_frames": int(totals.get("empty_frames") or 0),
            "type_claimed": int(totals.get("claimed") or 0),
            "mean_quality": _r(totals.get("mean_quality"), 3),
            "mean_range_km": _r(totals.get("mean_range_km"), 2),
            "first_at": as_iso(totals.get("first_at")),
            "last_at": as_iso(totals.get("last_at")),
        },
        "stations": [{"station": s["station"], "captures": int(s["n"]),
                      "mean_quality": _r(s["q"], 3),
                      "mean_range_km": _r(s["r"], 2)} for s in stations],
        "bands": [{"band": b["band"], "captures": int(b["n"]),
                   "mean_quality": _r(b["q"], 3)} for b in bands],
        "capture_modes": [{"mode": m["capture_mode"], "captures": int(m["n"])}
                          for m in modes],
        "recent": [_capture_view(r) for r in recent],
        "empty_frame_note": (
            "An empty frame is recorded and counted and is deliberately never "
            "promoted to an alert. The simulated camera detects presence "
            "perfectly and a real one does not, so a rule built on 'the camera "
            "saw nothing' would be calibrated against a false-negative rate "
            "this project does not have."),
        "cue_note": (
            "Each capture carries the sentence that won it a camera. Cueing is "
            "one global assignment per slot over cameras against candidates, "
            "not a ranked list handed out greedily."),
    }


def _r(v, n):
    return None if v is None else round(float(v), n)


def _s(v) -> Optional[str]:
    if v is None:
        return None
    s = str(v)
    return s[:-2] if s.endswith(".0") else s


# --------------------------------------------------------------------------
# 6. vessel type from motion — the vocabulary IS the product
# --------------------------------------------------------------------------

#: The measurement, cached for the life of the process. Training is a
#: measurement run, not a query: it builds a track per hull and fits a model,
#: and repeating it per request would make the page a stress test.
_TYPE_MODEL_CACHE: dict[str, Any] = {}

#: How many hulls a measurement run reads. Bounded because this runs inside an
#: HTTP request; the bound and the count actually used are both reported, so a
#: partial measurement can never be read as a corpus-wide one.
TYPE_MAX_VESSELS = 160


def vessel_type_model(*, compute: bool = False,
                      max_vessels: int = TYPE_MAX_VESSELS) -> dict:
    """The merged vocabulary and the confusion matrix behind it.

    ADR-033's central claim is that the output vocabulary is **derived from the
    measured confusion matrix, not declared**: any pair of classes mistaken for
    each other more than `CONFUSION_MERGE_THRESHOLD` of the time is merged, and
    the merged label is published. A laden bulker and a laden product tanker at
    13 knots on a great-circle course are doing the same thing, so motion cannot
    separate them and the system says `merchant` rather than picking one. That
    refusal is the product and it is what this endpoint exists to show.

    Nothing in the shipped pipeline trains or lands a model, so this measures on
    request. Without `compute` it returns the contract and says no measurement
    has been made, which is the honest default for a page load.
    """
    from ..tracks import vessel_type as vt

    contract = {
        "module": "tracks/vessel_type.py",
        "adr": "ADR-033",
        "area": "Area 3",
        "features": list(vt.FEATURE_NAMES),
        "merge_threshold": vt.CONFUSION_MERGE_THRESHOLD,
        "min_confidence": vt.MIN_CONFIDENCE,
        "min_track_points": vt.MIN_TRACK_POINTS,
        "split_rule": (
            "Split by hull, never by track. Tracks from one vessel on both "
            "sides of the split let the model memorise her rather than her "
            "class, which is the same hazard as splitting an image dataset by "
            "chip rather than by scene."),
        "sensor_blind": (
            "No feature reads an identifier, a message rate or a sensor name, "
            "which is what lets a model trained on AIS tracks answer for a "
            "radar contact."),
        "vocabulary_rule": (
            "The output vocabulary is read off the measured confusion matrix. "
            "Classes mistaken for each other more than "
            f"{vt.CONFUSION_MERGE_THRESHOLD:.0%} of the time are merged and "
            "published under one coarse label. If a later feature genuinely "
            "separates them, the groups shrink on their own."),
        "origin": origin_of("ais_position"),
        "derivation": ("derived by this system: a model was fitted to motion "
                       "features over held-out hulls, and its output vocabulary "
                       "read off its own confusion matrix"),
    }
    if not compute:
        cached = _TYPE_MODEL_CACHE.get("result")
        if cached:
            return dict(cached, cached=True)
        return dict(contract, status="not_measured", measured=None,
                    note=("No vessel-type model is landed by any pipeline "
                          "stage, so nothing has been measured on this corpus. "
                          "Ask for a measurement run to fit one over the "
                          "landed AIS fleet and read the vocabulary off the "
                          "result."))
    cached = _TYPE_MODEL_CACHE.get("result")
    if cached:
        return dict(cached, cached=True)

    measured = _measure_type_model(max_vessels=max_vessels)
    result = dict(contract, **measured, cached=False)
    if measured.get("status") == "measured":
        _TYPE_MODEL_CACHE["result"] = result
    return result


def _measure_type_model(*, max_vessels: int) -> dict:
    """Fit a model over the landed AIS fleet and report what it can separate."""
    from ..tracks import vessel_type as vt

    with open_reader() as reader:
        if not reader.has("ais_position") or not reader.has("gfw_vessel_identity"):
            return {"status": "unavailable", "measured": None,
                    "note": ("A measurement needs landed AIS positions and a "
                             "declared class per hull. One or both is absent, "
                             "so nothing can be fitted and nothing is claimed.")}
        classes = _declared_class_index(reader)
        vids = [r["vessel_id"] for r in reader.rows(
            "SELECT vessel_id, count(*) AS n FROM ais_position "
            "WHERE lat IS NOT NULL GROUP BY vessel_id ORDER BY n DESC "
            f"LIMIT {int(max_vessels)}")]
        labelled = []
        for vid in vids:
            klass = classes.get(vid) or classes.get(_canonical(vid))
            if not klass:
                continue
            track = _ais_track(reader, _canonical(vid), limit=4000)
            if track is None:
                continue
            labelled.append((str(vid), str(klass), track))

    if len(labelled) < 12:
        return {"status": "insufficient", "measured": None,
                "n_hulls": len(labelled),
                "note": (f"Only {len(labelled)} hull(s) carry both landed "
                         "positions and a declared class. A confusion matrix "
                         "over that is noise, so none is reported.")}
    try:
        model = vt.train(labelled)
    except Exception as exc:                                     # noqa: BLE001
        return {"status": "failed", "measured": None,
                "note": f"The measurement run did not complete: {exc}"}
    if model is None:
        # `train` returns None rather than a model whose accuracy is an
        # artefact of a six-track test set. That refusal is carried through
        # rather than dressed up as an error.
        return {"status": "insufficient", "measured": None,
                "n_hulls": len(labelled),
                "note": ("Not enough labelled track to measure anything. The "
                         "trainer declined to fit rather than report an "
                         "accuracy that would be an artefact of a tiny "
                         "held-out set.")}

    report = model.report()
    cm = report.get("confusion_matrix") or {}
    return {
        "status": "measured",
        "n_hulls": len(labelled),
        "max_vessels": max_vessels,
        "measured": {
            "fine_accuracy": report.get("accuracy_fine"),
            "coarse_accuracy": report.get("accuracy_coarse"),
            "fine_classes": report.get("classes"),
            "vocabulary": report.get("coarse_vocabulary"),
            "cannot_separate": report.get("cannot_separate"),
            "confusion": cm,
            "labels": sorted({k for k in cm}
                             | {c for row in cm.values() for c in row}),
            "n_train_tracks": report.get("n_train_tracks"),
            "n_test_tracks": report.get("n_test_tracks"),
            "caveat": report.get("caveat"),
        },
        "note": ("Measured in this process over the landed corpus, "
                 f"{len(labelled)} hull(s), split by hull. Synthetic corpus "
                 "figures. Real performance will be lower and must be "
                 "re-measured on a deploy host."),
    }


# --------------------------------------------------------------------------
# 7. vessel to vessel — what the detector names, and what it found
# --------------------------------------------------------------------------

def interaction_capability() -> dict:
    """The four relative-motion behaviours, their gates, and what fired.

    The pair search itself is a pipeline job — precomputing waiting-area cells
    and sweeping every pair across the corpus is minutes, not an HTTP request —
    so this reports the detector's own contract and reads the alerts it landed
    rather than re-running it.
    """
    from ..tracks import interactions as ix

    from . import graph_service as gsvc

    alerts = [a for a in gsvc.list_alerts()
              if a.get("anomaly_type") == "vessel_interaction"]
    return {
        "module": "tracks/interactions.py",
        "adr": "ADR-033",
        "area": "Area 3",
        "behaviours": [
            {"kind": "rendezvous",
             "what": "two hulls closing and holding station together"},
            {"kind": "steaming_in_company",
             "what": "courses agreeing and separation stable over hours"},
            {"kind": "shadowing",
             "what": "one holding a constant bearing astern of the other"},
            {"kind": "transfer",
             "what": "alongside at near-zero speed for a sustained period"},
        ],
        "gates": {
            "min_minutes": ix.MIN_MINUTES,
            "max_separation_m": ix.MAX_SEPARATION_M,
            "alongside_m": ix.ALONGSIDE_M,
            "same_course_deg": ix.SAME_COURSE_DEG,
            "underway_min_kn": ix.UNDERWAY_MIN_KN,
        },
        "n_alerts": len(alerts),
        "alerts": alerts[:20],
        "origin": origin_of("ais_position"),
        "derivation": ("derived by this system: pairs of tracks were compared "
                       "on their relative motion — how the separation behaves, "
                       "whether the courses agree, whether one holds a bearing "
                       "astern — after anchorage pairs were excluded by cell"),
        "measured_note": (
            "Swept over the combined picture, this detector produced zero "
            "findings at its gate: the corpus contains no formation that "
            "persists past an hour, and the eight found at a looser gate were "
            "all background traffic sharing a lane. That is a fact about the "
            "corpus, not a capability claim."),
        "boundary": (
            "The scenario's transfer counterparties are dark by design, so a "
            "ship-to-ship transfer is not observable as sustained close "
            "co-location in this data and is driven end to end by fixtures "
            "instead."),
    }
