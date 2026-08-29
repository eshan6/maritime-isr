"""Read an inbox of notifications and land them as structured rows.

The connector entrypoint. Walks a directory of whatever arrived, reads each
document with the reader for its format, extracts fields, resolves a vessel, and
lands one row per notification with every field's passage and locator intact.

**Documents that cannot be read are landed too.** A row with no fields and a
recorded reason is the honest record of an attachment nobody could open, and it
is the only way an operator learns that a quarter of the inbox is arriving in a
format the system does not handle. Dropping it produces a smaller inbox that
looks complete.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from ..landing import land_table, stamp_envelope
from .extract import extract_notification
from .readers import ReaderUnavailable, read_document
from .resolve import resolve_notification

__all__ = ["TABLE", "SOURCE_ID", "read_inbox", "land_inbox", "FIELD_NAMES"]

TABLE = "arrival_notification"
SOURCE_ID = "pans-inbox"

FIELD_NAMES = ("vessel_name", "imo", "call_sign", "flag", "last_port",
               "arrival_port", "eta", "cargo", "crew_count", "owner", "agent",
               "filed_at")

#: Extension to the format label carried on the row. `pdf` splits into two at
#: read time — a text layer and a scan are the same extension and a different
#: problem — so the label is decided by which reader actually produced the
#: passages, not by the filename.
_EXT_FORMAT = {".pdf": "pdf", ".docx": "docx", ".xlsx": "xlsx",
               ".json": "electronic"}


def _notification_id(path: Path) -> str:
    return path.stem or hashlib.sha1(str(path).encode()).hexdigest()[:12]


def read_inbox(inbox, registry, *, is_synthetic: bool = False) -> list[dict]:
    """Every document in `inbox`, as landed rows. Order is stable by filename."""
    inbox = Path(inbox)
    if not inbox.exists():
        return []
    rows: list[dict] = []
    for path in sorted(inbox.iterdir()):
        if path.is_dir() or path.suffix.lower() not in _EXT_FORMAT:
            continue
        rows.append(_row_for(path, registry, is_synthetic=is_synthetic))
    return rows


def _received_at(fields: dict, path: Path) -> tuple[datetime, str]:
    """When the form was filed, and how we know.

    **The document is asked first, and the filesystem only when it is silent.**
    A file's modification time is when somebody scanned, copied or forwarded the
    attachment; the date on the form is when the agent filed it. Those differ by
    days on a scan and by weeks on anything that has been through a mailbox
    migration, and every rule in `anomaly.paperwork` measures a declaration
    against the track *as at the filing time*. Handed a scanning timestamp,
    those rules do not fail loudly — they look before a window that has not
    happened yet, find nothing, and return "not checkable" for the whole inbox.
    A clean-looking silence is the most expensive failure this module can have.

    The fallback is kept, because a form with no date on it is a real thing and
    an mtime is better than nothing. It is *labelled*, because a value inferred
    from the filesystem and a value read off the page are not the same evidence
    and a rule quoting one as the other would be overclaiming.
    """
    field = fields.get("filed_at")
    if field is not None and field.value:
        try:
            dt = datetime.fromisoformat(str(field.value))
        except ValueError:
            dt = None
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc), "declared"
    return (datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc),
            "file_mtime")


def _row_for(path: Path, registry, *, is_synthetic: bool) -> dict:
    fmt = _EXT_FORMAT[path.suffix.lower()]
    unread = None
    fields: dict = {}
    try:
        passages = read_document(path)
        fields = extract_notification(passages)
        # A PDF whose passages came from OCR is a scan, whatever its extension
        # says. Recording that distinction is the point of the per-passage
        # `method`: "we OCR'd this" is a fact an operator should see beside a
        # value, not an implementation detail.
        if fmt == "pdf" and any(f.method.startswith("ocr")
                                for f in fields.values()):
            fmt = "pdf_scan"
    except ReaderUnavailable as e:
        unread = str(e)
    except Exception as e:                                    # noqa: BLE001
        # A corrupt attachment is a real thing in a real inbox. It lands as an
        # unread document rather than stopping the run, because one bad fax
        # must not cost the other ninety-nine.
        unread = f"{type(e).__name__}: {e}"

    vessel_id, how, conf = (None, None, 0.0)
    if fields:
        vessel_id, how, conf = resolve_notification(fields, registry)

    received_at, received_from = _received_at(fields, path)
    row = dict(
        notification_id=_notification_id(path),
        document_name=path.name,
        document_format=fmt,
        vessel_id=vessel_id,
        resolved_by=how,
        resolution_confidence=round(conf, 3),
        # **A field the form explicitly left empty is not a field read.** The
        # extractor records "Crew: NIL" as a passage with a null value, because
        # the agent answering "nothing" and the agent skipping the box are
        # different facts (ADR-036's three-valued discipline, one level down).
        # Counting those here would inflate a completeness figure with
        # non-values, and `fields_read` gates two alerts — a document that read
        # nothing but a column of dashes must still count as unreadable.
        fields_read=sum(1 for f in fields.values() if f.value is not None),
        fields_declared_absent=sum(1 for f in fields.values()
                                   if f.value is None),
        unread_reason=unread,
        received_at=received_at,
        received_at_source=received_from,
    )
    for name in FIELD_NAMES:
        f = fields.get(name)
        row[name] = f.value if f else None
        row[f"{name}_confidence"] = round(f.confidence, 3) if f else None
        row[f"{name}_passage"] = f.passage if f else None
        row[f"{name}_locator"] = f.locator if f else None
        row[f"{name}_method"] = f.method if f else None
    # **A synthetic document read by a real connector has to say both things.**
    # This is the first table landed by a connector rather than by the scenario
    # writer, and the two facts it must carry are in tension: `pans-inbox` is
    # honestly which connector produced the row — the whole design is that the
    # connector really runs — while the corpus invariant is that no synthetic
    # row may be mistaken for real. The graph store already solved this with a
    # `synthetic-scenario:` prefix, so the same shape is used here rather than
    # inventing a second convention or losing the connector's name.
    source_id = SOURCE_ID
    if is_synthetic:
        from ...scenario.identifiers import SYNTHETIC_SOURCE_ID
        source_id = f"{SYNTHETIC_SOURCE_ID}:{SOURCE_ID}"
    # **The flag goes in with the source id, not after it.** Setting
    # `is_synthetic` on the row afterwards left `stamp_envelope` believing this
    # was a real-source row carrying a synthetic source id — precisely the
    # drift the check exists to refuse — and it went unnoticed only because the
    # check did not recognise the `synthetic-scenario:` prefix either. Two
    # errors cancelling is not agreement.
    stamp_envelope(row, source_id=source_id, source_ref=path.name,
                   acquired_at=row["received_at"], confidence=None,
                   is_synthetic=bool(is_synthetic))
    row["is_synthetic"] = bool(is_synthetic)
    return row


def land_inbox(inbox, registry, *, is_synthetic: bool = False) -> dict:
    """Read and land. Returns the `land_table` written-counts."""
    rows = read_inbox(inbox, registry, is_synthetic=is_synthetic)
    if not rows:
        return {}
    return land_table(rows, table=TABLE, key_fields=("notification_id",),
                      day_field="received_at")


def declared_fields(row: dict) -> dict:
    """A landed row back into `{field: ExtractedField}` for the rules.

    The rules take extracted fields because they need the passage, and the
    landed table is flat because a Parquet column cannot hold a nested object
    the query layer would then have to unpack. This is the one place that
    knows both shapes.
    """
    from ...schemas import ExtractedField
    out = {}
    for name in FIELD_NAMES:
        value = row.get(name)
        if value in (None, ""):
            continue
        out[name] = ExtractedField(
            value=str(value),
            passage=row.get(f"{name}_passage"),
            locator=row.get(f"{name}_locator"),
            method=row.get(f"{name}_method"),
            confidence=float(row.get(f"{name}_confidence") or 0.0),
        )
    return out
