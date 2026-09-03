"""One vessel's paperwork, assembled — the read side of the connector.

*"Given a vessel identifier, return her documents, the extracted fields with
their source document and confidence, and the paperwork check outcome."*

This is the seam the serving layer calls. It is here rather than in `api/`
because it is a **read of this connector's own landed table**, and the API's
job is to serve it, not to know how a notification is put back together from a
flat Parquet row. `api/` may import this; nothing here imports `api/`.

Three things it will not do, each for a reason the rest of the area already
paid for:

* **It does not detect.** Every verdict comes from `anomaly.paperwork`, whose
  three checks are pure functions with three-valued outcomes. A second copy of
  a rule living behind an API is an uncalibrated copy, and the assistant's
  collectors are forbidden the same thing for the same reason (ADR-031).
* **It does not guess an identity.** A vessel identifier is matched exactly —
  by `vessel_id`, IMO, MMSI or call sign — with the name rung last and refused
  outright when two hulls answer to it. `resolve.resolve_notification` hedges
  toward refusing because a notification on the wrong hull is a false
  accusation with paperwork behind it; a *lookup* on the wrong hull is the same
  accusation shown to an operator.
* **It never folds "we could not check" into "fine".** The outcome counts it
  returns have three keys, always, and a document with nothing checkable comes
  back saying so rather than coming back clean.

Every value it returns carries the passage it was read from, where in the
document that passage sat, how it was read and how confident the reader was —
the per-field provenance ADR-036 exists for. The document row itself carries
the envelope (`source_id`, `source_ref`, `acquired_at`, `ingested_at`,
`pipeline_version`, `confidence`) that CLAUDE.md §4.1 requires of every row in
every store, and it is handed back rather than stripped: an operator who cannot
trace a flag to its source document and the code version that read it has been
shown a number, not evidence.
"""
from __future__ import annotations

from typing import Optional

from .land import FIELD_NAMES, TABLE, declared_fields
from .kinds import kind_label
from .resolve import normalise_vessel_name

__all__ = ["vessel_documents", "document_record", "paperwork_outcomes",
           "group_by_vessel", "OUTCOMES", "ENVELOPE_FIELDS",
           "resolve_identifier", "track_fixes"]

#: The three answers, and the order they are reported in. Written down because
#: a caller that iterated a dict's keys would render them in whatever order the
#: first document happened to produce.
OUTCOMES: tuple[str, ...] = ("contradiction", "ok", "not_checkable")

#: Days of track before a filing that the last-port claim is compared against.
#: Matches `anomaly.library.PAPERWORK_LOOKBACK_DAYS` — the claim on a form is
#: about the voyage she is on, not about her year.
LOOKBACK_DAYS = 10.0

#: Columns of the envelope every landed row carries, handed back with the
#: document rather than dropped on the way to the caller.
ENVELOPE_FIELDS = ("source_id", "source_ref", "acquired_at", "ingested_at",
                   "pipeline_version", "confidence", "is_synthetic")


# --------------------------------------------------------------------------
# identity — exactly, or not at all
# --------------------------------------------------------------------------

def resolve_identifier(identifier, registry) -> tuple[Optional[str], Optional[str]]:
    """(vessel_id, how) for whatever an operator typed into the box.

    The same ladder `resolve.resolve_notification` climbs, and for the same
    reason: an IMO is a permanent hull number, a call sign changes with the
    flag, and a name identifies a hull only when it happens to be unique. A
    name two hulls answer to resolves to neither — the corpus contains exactly
    that case — and the caller is told `name_ambiguous` rather than handed a
    coin flip.

    `registry` is an iterable of dicts with `vessel_id` and any of `imo`,
    `mmsi`, `call_sign`, `ship_name`: the rows the system actually holds,
    nulls and all.
    """
    if identifier in (None, ""):
        return None, None
    text = str(identifier).strip()
    if not text:
        return None, None

    by_id, by_imo, by_mmsi, by_call, by_name = {}, {}, {}, {}, {}
    for row in registry or ():
        vid = row.get("vessel_id")
        if not vid:
            continue
        by_id.setdefault(str(vid), set()).add(vid)
        for key, index in (("imo", by_imo), ("mmsi", by_mmsi)):
            value = str(row.get(key) or "").strip()
            if value:
                index.setdefault(value, set()).add(vid)
        call = str(row.get("call_sign") or "").strip().upper()
        if call:
            by_call.setdefault(call, set()).add(vid)
        name = normalise_vessel_name(row.get("ship_name"))
        if name:
            by_name.setdefault(name.replace(" ", ""), set()).add(vid)

    for how, index, key in (("vessel_id", by_id, text),
                            ("imo", by_imo, text),
                            ("mmsi", by_mmsi, text),
                            ("call_sign", by_call, text.upper())):
        hit = index.get(key)
        if hit and len(hit) == 1:
            return next(iter(hit)), how

    name = normalise_vessel_name(text)
    key = name.replace(" ", "") if name else None
    if key:
        hit = by_name.get(key)
        if hit and len(hit) == 1:
            return next(iter(hit)), "name"
        if hit and len(hit) > 1:
            return None, "name_ambiguous"
    return None, None


# --------------------------------------------------------------------------
# one document, assembled
# --------------------------------------------------------------------------

def document_record(row: dict, findings=()) -> dict:
    """One landed notification row, back into something an operator can read.

    The landed table is flat because a Parquet column cannot hold a nested
    object the query layer would then have to unpack; this is the shape the
    serving layer wants. Every field comes back with its passage, its locator
    and the confidence the *reader* earned for it — a spreadsheet cell at 1.0
    and a smudge on a fax at 0.55 must never arrive looking equally certain.
    """
    fields = {}
    for name in FIELD_NAMES:
        value = row.get(name)
        passage = row.get(f"{name}_passage")
        if value in (None, "") and passage in (None, ""):
            continue
        fields[name] = dict(
            value=(None if value in (None, "") else str(value)),
            confidence=row.get(f"{name}_confidence"),
            passage=passage,
            locator=row.get(f"{name}_locator"),
            method=row.get(f"{name}_method"),
            # A field present with a passage and no value is the agent writing
            # "NIL" — an answer, and not the same fact as an empty box.
            declared_absent=(value in (None, "") and passage not in (None, "")),
        )

    checks = [f.as_dict() if hasattr(f, "as_dict") else dict(f)
              for f in findings]
    counts = {name: 0 for name in OUTCOMES}
    for check in checks:
        outcome = check.get("outcome")
        if outcome in counts:
            counts[outcome] += 1

    kind = row.get("document_kind")
    return dict(
        notification_id=row.get("notification_id"),
        document_name=row.get("document_name"),
        document_format=row.get("document_format"),
        document_kind=kind,
        document_kind_label=kind_label(kind),
        received_at=row.get("received_at"),
        received_at_source=row.get("received_at_source"),
        vessel_id=row.get("vessel_id"),
        resolved_by=row.get("resolved_by"),
        resolution_confidence=row.get("resolution_confidence"),
        fields_read=row.get("fields_read"),
        fields_declared_absent=row.get("fields_declared_absent"),
        unread_reason=row.get("unread_reason"),
        fields=fields,
        checks=checks,
        outcomes=counts,
        provenance={k: row.get(k) for k in ENVELOPE_FIELDS},
    )


# --------------------------------------------------------------------------
# the three checks, over one document
# --------------------------------------------------------------------------

def paperwork_outcomes(row: dict, *, fixes=(), arrivals=(), prior_calls=(),
                       draught_m=None) -> list:
    """`anomaly.paperwork`'s three findings for one landed notification.

    A thin join and nothing else: the rules are pure functions and they stay
    that way. `fixes` is ``(epoch_seconds, lat, lon)``, `arrivals` is
    ``(timestamp, port_name)``, `prior_calls` is ``(lat, lon)`` for the calls
    recorded before she filed.
    """
    from ...anomaly.paperwork import (check_paperwork, match_arrival,
                                      window_before)

    declared = declared_fields(row)
    filed = row.get("received_at")
    track = window_before(list(fixes), filed, days=LOOKBACK_DAYS)
    observed = match_arrival(declared, list(arrivals), filed)
    return check_paperwork(declared=declared, fixes=track, filed_at=filed,
                           observed_arrival=observed, prior_calls=list(prior_calls),
                           draught_m=draught_m)


# --------------------------------------------------------------------------
# the entrypoint
# --------------------------------------------------------------------------

def vessel_documents(identifier, *, notifications=None, port_calls=None,
                     positions=None, draughts=None, registry=None,
                     run_checks: bool = True) -> dict:
    """Every document filed for one vessel, with its fields and its verdicts.

    `identifier` is whatever an operator has: a `vessel_id`, an IMO, an MMSI, a
    call sign, or a name. Everything else is optional and exists so the caller
    can inject what it already holds — a serving layer with an open reader
    should pass its own rows rather than make this open a second one, and a
    test should pass fixtures rather than need a corpus on disk. When a
    collection is not passed it is read from the store through the landing
    abstraction, never from a hard-coded path.

    Returns::

        {"identifier", "vessel_id", "matched_by", "documents": [...],
         "outcomes": {"contradiction": n, "ok": n, "not_checkable": n},
         "n_documents", "n_unread", "n_unresolved"}

    **An identifier that matches no hull is not an error**, and neither is a
    hull with no paperwork. Both come back as an empty document list with the
    reason in `matched_by` — because "she filed nothing" is one of the two gaps
    Area 4 exists to surface, and raising on it would turn a finding into a 404.
    """
    if registry is None or notifications is None:
        loaded = _from_store(need_registry=registry is None,
                             need_notifications=notifications is None)
        registry = registry if registry is not None else loaded["registry"]
        notifications = (notifications if notifications is not None
                         else loaded["notifications"])

    vessel_id, how = resolve_identifier(identifier, registry)
    mine = [r for r in notifications if r.get("vessel_id")
            and r.get("vessel_id") == vessel_id] if vessel_id else []
    mine.sort(key=lambda r: (str(r.get("received_at")),
                             str(r.get("notification_id"))))

    documents = []
    if mine and run_checks:
        if port_calls is None or positions is None or draughts is None:
            extra = _track_for(vessel_id,
                               port_calls=port_calls, positions=positions,
                               draughts=draughts)
            port_calls = extra["port_calls"]
            positions = extra["positions"]
            draughts = extra["draughts"]
        calls = sorted(port_calls or (), key=lambda c: c["start_time"])
        arrivals = [(c["start_time"], c.get("port_name")) for c in calls]
        draught = (draughts or {}).get(vessel_id)
        for row in mine:
            filed = row.get("received_at")
            prior = [(c["lat"], c["lon"]) for c in calls
                     if c.get("lat") is not None and c.get("lon") is not None
                     and filed is not None and c["start_time"] < filed]
            findings = paperwork_outcomes(
                row, fixes=positions or (), arrivals=arrivals,
                prior_calls=prior, draught_m=draught)
            documents.append(document_record(row, findings))
    else:
        documents = [document_record(row) for row in mine]

    totals = {name: 0 for name in OUTCOMES}
    for doc in documents:
        for name in OUTCOMES:
            totals[name] += doc["outcomes"].get(name, 0)

    return dict(
        identifier=(None if identifier is None else str(identifier)),
        vessel_id=vessel_id,
        matched_by=how,
        documents=documents,
        outcomes=totals,
        n_documents=len(documents),
        n_unread=sum(1 for d in documents if d.get("unread_reason")),
        n_unresolved=sum(1 for d in documents if not d.get("vessel_id")),
    )


def _from_store(*, need_registry: bool, need_notifications: bool) -> dict:
    """The landed notifications and the merged identity registry.

    Reads through `ingest.landing`, which resolves paths through the storage
    abstraction — `MISR_STORE_BACKEND` decides where that is, and nothing here
    may know.
    """
    from ..landing import read_table
    from .resolve import merge_identity_sources

    out: dict = {"registry": [], "notifications": []}
    if need_notifications:
        try:
            out["notifications"] = list(read_table(TABLE))
        except Exception:                                     # noqa: BLE001
            out["notifications"] = []
    if need_registry:
        identity, voyage = [], []
        try:
            identity = list(read_table("gfw_vessel_identity"))
        except Exception:                                     # noqa: BLE001
            identity = []
        try:
            voyage = [dict(vessel_id=r.get("vessel_id"), imo=r.get("imo"))
                      for r in read_table("ais_voyage")]
        except Exception:                                     # noqa: BLE001
            voyage = []
        # Most trusted first, fill-only: a gap in one table must not be
        # reported as a gap in somebody's paperwork (ADR-036).
        merged = merge_identity_sources(identity, voyage)
        by_id = {r["vessel_id"]: dict(r) for r in merged}
        for row in identity:
            vid = row.get("vessel_id")
            if vid in by_id:
                for key in ("mmsi", "ship_name", "call_sign"):
                    if row.get(key) and not by_id[vid].get(key):
                        by_id[vid][key] = row[key]
        out["registry"] = list(by_id.values())
    return out


#: The only four columns of `ais_position` the paperwork rules can use: which
#: hull, when, and where. Naming them is not a micro-optimisation — see
#: :func:`track_fixes`.
_FIX_COLUMNS = ("vessel_id", "ts", "lat", "lon")


def track_fixes(vessel_ids=None) -> dict:
    """``{vessel_id: [(epoch_seconds, lat, lon), ...]}``, sorted, from the store.

    **Read one column at a time, one day-partition at a time, and never as
    dicts.** `landing.read_table` materialises every partition of a table as a
    list of Python dicts — for `ais_position` that is one dict of twenty-five
    keys per fix, and the corpus now holds over eight hundred thousand of them.
    A process doing that was killed by the kernel for running out of memory, and
    the read this replaced sat behind a docstring promising it read "per vessel
    rather than in bulk" while doing precisely the opposite: it built the whole
    table and *then* filtered it, so the promise was untrue in the only respect
    that costs anything.

    Projecting the four columns the rules actually consult and holding one day
    at a time keeps the whole read inside a few hundred megabytes whatever the
    corpus grows to, and `vessel_ids` narrows it further for the serving path,
    which asks about one hull.

    Paths come from `landing.table_day_partitions`, so `MISR_STORE_BACKEND`
    still decides where the store is and nothing here knows a path.

    A hull with no fixes is **absent from the result**, not present with an
    empty list: "we hold no track for her" is a fact the last-port check turns
    into `not_checkable`, and inventing an empty list for every hull ever named
    would make that fact unreadable.
    """
    from ..landing import table_day_partitions

    wanted = None if vessel_ids is None else {str(v) for v in vessel_ids}
    out: dict = {}
    try:
        import pyarrow.parquet as pq
    except Exception:                                         # noqa: BLE001
        return out
    for path in table_day_partitions("ais_position"):
        try:
            columns = [c for c in _FIX_COLUMNS
                       if c in pq.read_schema(path).names]
            if len(columns) < len(_FIX_COLUMNS):
                # A partition written before one of these columns existed
                # cannot answer "where was she" — skipped, not guessed at.
                continue
            table = pq.read_table(path, columns=list(_FIX_COLUMNS))
        except Exception:                                     # noqa: BLE001
            continue
        for vid, ts, lat, lon in zip(table.column("vessel_id").to_pylist(),
                                     table.column("ts").to_pylist(),
                                     table.column("lat").to_pylist(),
                                     table.column("lon").to_pylist()):
            if not vid or ts is None or lat is None or lon is None:
                continue
            if wanted is not None and str(vid) not in wanted:
                continue
            try:
                epoch = ts.timestamp()
            except AttributeError:
                continue
            out.setdefault(vid, []).append((epoch, lat, lon))
    for fixes in out.values():
        fixes.sort()
    return out


def _track_for(vessel_id, *, port_calls, positions, draughts) -> dict:
    """The corpus's own record of where she was, for the rules to check against.

    Only what the checks need: her recorded calls, her positions, and the
    draught she broadcast. Read per vessel rather than in bulk, because a
    serving layer answering one operator's question has no business pulling a
    quarter of a million position rows into memory.
    """
    from ..landing import read_table

    out = dict(port_calls=port_calls, positions=positions, draughts=draughts)
    if port_calls is None:
        try:
            out["port_calls"] = [
                dict(vessel_id=r.get("vessel_id"), port_name=r.get("port_name"),
                     start_time=r.get("start_time"), lat=r.get("lat"),
                     lon=r.get("lon"))
                for r in read_table("gfw_port_visits")
                if r.get("vessel_id") == vessel_id
                and r.get("start_time") is not None]
        except Exception:                                     # noqa: BLE001
            out["port_calls"] = []
    if positions is None:
        # **`ais_position` names its clock `ts`** (`schemas.AIS_POSITION`);
        # `ais_voyage` is the table that says `timestamp`. An earlier read asked
        # for `timestamp`, got nothing on every row, and a bare `except` turned
        # that into an empty position list — which `check_last_port` reports as
        # "too little track to say", so every hull came back `not_checkable` and
        # nothing anywhere said why. `track_fixes` reads the column under the
        # name the schema gives it, and reads it columnar rather than pulling
        # the whole table in as dicts, which is what this docstring has always
        # claimed and did not do.
        try:
            out["positions"] = track_fixes([vessel_id]).get(vessel_id, [])
        except Exception:                                     # noqa: BLE001
            out["positions"] = []
    if draughts is None:
        try:
            found = [r.get("draught_m") for r in read_table("ais_voyage")
                     if r.get("vessel_id") == vessel_id
                     and r.get("draught_m") not in (None, "")]
        except Exception:                                     # noqa: BLE001
            found = []
        out["draughts"] = ({vessel_id: float(found[-1])} if found else {})
    return out


def group_by_vessel(rows) -> dict:
    """Every landed notification, grouped by the hull it resolved to.

    The bulk companion to :func:`vessel_documents`, for a report over a whole
    run. Documents that resolved to nothing come back under the key ``None`` —
    they are **not** dropped, because a form naming a ship nothing holds is one
    of the two gaps the requirement names explicitly, and a grouping that
    silently discarded them would report a tidy inbox with the finding missing
    from it.

    It takes no registry and no track: resolution already happened at landing
    time and is on the row. Accepting arguments it does not use would invite a
    caller to pass a registry and believe this had re-resolved anything.
    """
    grouped: dict = {}
    for row in rows:
        grouped.setdefault(row.get("vessel_id"), []).append(row)
    return grouped
