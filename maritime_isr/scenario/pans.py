"""Pre-Arrival Notifications, in the formats they actually arrive in — Area 4.

*"Pre-Arrival Notification of Ships data reaches the Coast Guard as PDF, Word
and spreadsheet attachments by email. It contains vital information but cannot
be stored in a structured database or fused with AIS because of its format."*
— the IDEX Challenge 82 brief, Area 4.

And the instruction that governs everything in this module:

> Generate realistic notification documents from your existing scenario truth,
> in the messy formats the requirement names — including the ones that are
> scans, the ones with inconsistent field ordering, and the ones where a human
> typed the vessel name slightly differently than the registry spells it. **A
> clean document set proves nothing, because the entire difficulty here is that
> the input is unstructured.**

So the mess here is authored, deliberately, and each kind of it is a thing that
happens in a real inbox:

* **Four formats.** A text PDF, a scanned PDF that is an image of a page and
  carries no text layer at all, a Word document, a spreadsheet. Plus a fifth
  that is not a document: the structured electronic feed the national logistics
  portal is expected to publish, which the requirement asks the system to stay
  compatible with. It is generated here beside the others precisely so that
  compatibility is demonstrated rather than asserted.
* **Inconsistent field order.** Agencies use their own forms. Half the
  documents here put the last port before the ETA and half after, and one puts
  the cargo at the top because that is what its shipper cares about.
* **Inconsistent field *labels*.** "Last Port of Call", "From", "Previous
  Port", "Port of Departure" all mean one thing. A reader keyed to one spelling
  reads a third of a real corpus.
* **Names typed by hand.** "M.V. GRANITE TRIUMPH", "GRANITE TRIUMPH", "GRANITE
  TRUIMPH" — a prefix, a punctuation choice and a transposition, which is what
  a form filled in at 0300 by an agent's clerk looks like.
* **Missing fields.** Real notifications arrive incomplete. A crew count is
  absent about as often as it is present, and a reader that assumes every field
  is there will read a null as a value.

**What is NOT deliberately corrupted: the truth underneath.** These documents
describe vessels the corpus already contains, and the contradictions between
paperwork and track are authored as *scenarios* (group P), not sprinkled as
noise. A document that is merely garbled is a parsing problem; a document that
says something the track disagrees with is the product.
"""
from __future__ import annotations

import hashlib
import io
import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

__all__ = ["NotificationSpec", "write_notifications", "FORMATS",
           "LABEL_SYNONYMS", "PANS_DIRNAME"]

#: Where the generated documents land, under the data root. They are *inputs*,
#: not conformed rows — the same standing an unread email attachment has — so
#: they sit beside `conformed/` rather than inside it.
PANS_DIRNAME = "pans_inbox"

#: The formats a notification arrives in. `electronic` is the portal feed the
#: requirement names as the future state, and it is generated here so the claim
#: "the electronic feed drops in without rework" can be tested rather than made.
FORMATS = ("pdf", "pdf_scan", "docx", "xlsx", "electronic")

#: What different agencies call the same field. A reader keyed to one spelling
#: reads a fraction of a real corpus, which is why the extractor is written
#: against this table and why the table lives here — the generator and the
#: reader must disagree about labels the way two agencies do, but they must not
#: disagree about which labels *exist*.
LABEL_SYNONYMS: dict[str, tuple[str, ...]] = {
    "vessel_name": ("Vessel Name", "Name of Vessel", "Ship Name", "Vessel"),
    "imo": ("IMO Number", "IMO No.", "IMO", "IMO/Official No."),
    "call_sign": ("Call Sign", "Callsign", "Radio Call Sign", "C/S"),
    "flag": ("Flag", "Flag State", "Nationality", "Flag of Registry"),
    "last_port": ("Last Port of Call", "From", "Previous Port",
                  "Port of Departure", "Last Port"),
    "arrival_port": ("Port of Arrival", "To", "Destination Port",
                     "Arrival Port", "Port"),
    "eta": ("ETA", "Expected Time of Arrival", "Estimated Arrival",
            "Date/Time of Arrival"),
    "cargo": ("Cargo", "Nature of Cargo", "Cargo on Board", "Commodity"),
    "crew_count": ("Crew", "No. of Crew", "Crew on Board", "Total Crew"),
    "owner": ("Owner", "Registered Owner", "Owners", "Beneficial Owner"),
    "agent": ("Agent", "Local Agent", "Ship's Agent", "Agency"),
    "filed_at": ("Date of Filing", "Filed On", "Date of Submission",
                 "Submitted", "Notification Date"),
}

#: Field orders real forms use. Not a shuffle — each is a plausible form layout,
#: because a random permutation per document would be a harder problem than the
#: real one and would test the reader against noise rather than against variety.
#:
#: `filed_at` sits where a form puts it — at the top on some layouts, in the
#: sign-off block at the bottom on others. It is a declared field like any
#: other and is read like one; see `_lines` for why that matters.
FIELD_ORDERS: tuple[tuple[str, ...], ...] = (
    ("vessel_name", "imo", "call_sign", "flag", "last_port", "arrival_port",
     "eta", "cargo", "crew_count", "owner", "agent", "filed_at"),
    ("filed_at", "vessel_name", "imo", "flag", "arrival_port", "eta",
     "last_port", "cargo", "agent", "owner", "crew_count", "call_sign"),
    ("cargo", "vessel_name", "imo", "last_port", "arrival_port", "eta",
     "owner", "agent", "flag", "call_sign", "crew_count", "filed_at"),
    ("filed_at", "imo", "vessel_name", "eta", "arrival_port", "last_port",
     "flag", "crew_count", "cargo", "owner", "agent", "call_sign"),
)


@dataclass
class NotificationSpec:
    """What one notification says. Values are strings, as a form holds them."""

    notification_id: str
    document_format: str
    received_at: datetime
    values: dict = field(default_factory=dict)
    #: The truth this document was written from, for the answer key. **Never
    #: written into the document and never read by any extractor** — same rule
    #: as `scenario_truth` (ADR-019).
    vessel_entity_id: Optional[str] = None
    #: Which fields were deliberately dropped, so the corpus can measure "the
    #: reader found what was there" separately from "the form was complete".
    omitted: tuple = ()

    @property
    def document_name(self) -> str:
        ext = {"pdf": "pdf", "pdf_scan": "pdf", "docx": "docx",
               "xlsx": "xlsx", "electronic": "json"}[self.document_format]
        return f"{self.notification_id}.{ext}"


# --------------------------------------------------------------------------
# how a human types a vessel's name
# --------------------------------------------------------------------------

def mistype(name: str, rng: random.Random) -> tuple[str, str]:
    """A hand-typed rendering of a vessel name, and what kind of mangling it is.

    Four kinds, all of them things a clerk does at three in the morning, and
    each one breaking a *different* naive matcher:

    * a prefix — beats exact match, survives a normaliser that strips prefixes
    * a transposition — beats exact match and prefix stripping alike
    * a dropped space — beats anything tokenising on whitespace
    * nothing at all — the control, and the commonest case in real data

    The mix matters more than any single case: a corpus of only-mangled names
    would make an aggressive fuzzy matcher look good, and an aggressive fuzzy
    matcher is how a notification gets attached to the wrong hull.
    """
    kind = rng.choices(("clean", "prefix", "transpose", "nospace"),
                       weights=(0.55, 0.2, 0.15, 0.10))[0]
    if kind == "clean":
        return name, "clean"
    if kind == "prefix":
        return f"{rng.choice(('M.V. ', 'MV ', 'M/V '))}{name}", "prefix"
    if kind == "nospace":
        return name.replace(" ", ""), "nospace"
    # Transpose two adjacent letters inside a word — TRIUMPH -> TRUIMPH.
    letters = [i for i, c in enumerate(name[:-1])
               if c.isalpha() and name[i + 1].isalpha()]
    if not letters:
        return name, "clean"
    i = rng.choice(letters)
    return name[:i] + name[i + 1] + name[i] + name[i + 2:], "transpose"


# --------------------------------------------------------------------------
# writers, one per format
# --------------------------------------------------------------------------

_TITLE = "PRE-ARRIVAL NOTIFICATION OF SHIPS"
_SUBTITLE = "Form PANS-1  |  submitted under Merchant Shipping rules"


def _lines(spec: NotificationSpec, rng: random.Random) -> list[tuple[str, str]]:
    """(label, value) in this document's own field order, with its own labels.

    **The filing date is written into the document**, and it is written here
    rather than left to each scenario so that no notification can be produced
    without one. When it is absent, the only remaining evidence of when a form
    was filed is the file's modification time — which is when somebody scanned
    or copied it, not when the agent submitted it. Every rule that compares a
    declaration against the track needs the *filing* time, and a rule handed a
    scanning timestamp returns "not checkable" for a whole corpus while looking
    like it is working.
    """
    order = FIELD_ORDERS[rng.randrange(len(FIELD_ORDERS))]
    values = dict(spec.values)
    values.setdefault("filed_at", eta_text(spec.received_at, rng))
    out = []
    for key in order:
        if key in spec.omitted or key not in values:
            continue
        label = rng.choice(LABEL_SYNONYMS[key])
        out.append((label, str(values[key])))
    return out


def _write_pdf(path: Path, spec: NotificationSpec, rng: random.Random) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=A4)
    _, height = A4
    y = height - 70
    c.setFont("Helvetica-Bold", 13)
    c.drawString(60, y, _TITLE)
    y -= 18
    c.setFont("Helvetica", 8)
    c.drawString(60, y, _SUBTITLE)
    y -= 30
    c.setFont("Helvetica", 10)
    for label, value in _lines(spec, rng):
        c.drawString(60, y, f"{label}: {value}")
        y -= 17
    c.save()


def _render_page_image(spec: NotificationSpec, rng: random.Random):
    """A page rendered as pixels — the thing a scanner produces.

    Deliberately imperfect: a slight rotation and a little noise, because a
    scanned page is never square on the glass and a clean render would be an
    OCR problem this project does not have. The tilt is small enough that
    tesseract copes, which is the point — the corpus has to be hard enough to
    be real and not so hard that it measures tesseract instead of the pipeline.
    """
    from PIL import Image, ImageDraw, ImageFont

    W, H = 1400, 1000
    img = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(img)
    try:
        title_font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 30)
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 26)
    except OSError:                                          # pragma: no cover
        title_font = font = ImageFont.load_default()

    d.text((60, 50), _TITLE, fill=20, font=title_font)
    y = 130
    for label, value in _lines(spec, rng):
        d.text((60, y), f"{label}: {value}", fill=30, font=font)
        y += 46
        if y > H - 60:
            break

    img = img.rotate(rng.uniform(-0.7, 0.7), fillcolor=255, resample=Image.BICUBIC)
    return img


def _write_pdf_scan(path: Path, spec: NotificationSpec,
                    rng: random.Random) -> None:
    """A PDF with **no text layer** — an image of a page, as a fax produces.

    This is the format the requirement singles out and the one that decides
    whether the extractor is real. `pypdf` returns an empty string for it, so a
    reader that only knows how to pull text sees a blank document and reports a
    notification with no fields rather than an unreadable one — which is a much
    worse failure than an error, because it looks like a form somebody left
    empty.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    img = _render_page_image(spec, rng)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    w, h = A4
    c = canvas.Canvas(str(path), pagesize=A4)
    c.drawImage(ImageReader(buf), 0, 0, width=w, height=h, preserveAspectRatio=True)
    c.save()


def _write_docx(path: Path, spec: NotificationSpec, rng: random.Random) -> None:
    """Word, and **half of them as a table** rather than as paragraphs.

    Both shapes are common and they need different reading. A reader that walks
    paragraphs finds nothing in a table document, and the failure is silent for
    the same reason the scan's is: it produces an empty form, not an error.
    """
    from docx import Document

    doc = Document()
    doc.add_heading(_TITLE, level=1)
    doc.add_paragraph(_SUBTITLE)
    rows = _lines(spec, rng)
    if rng.random() < 0.5:
        table = doc.add_table(rows=0, cols=2)
        for label, value in rows:
            cells = table.add_row().cells
            cells[0].text = label
            cells[1].text = value
    else:
        for label, value in rows:
            doc.add_paragraph(f"{label}: {value}")
    doc.save(str(path))


def _write_xlsx(path: Path, spec: NotificationSpec, rng: random.Random) -> None:
    """A spreadsheet, with the form starting a few rows down.

    Real submitted spreadsheets have a logo, a blank row and a heading before
    the data starts, so the label column is not column A and the first row is
    not the header. A reader that assumes A1 reads the logo.
    """
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "PANS"
    ws["B2"] = _TITLE
    ws["B3"] = _SUBTITLE
    r = 5 + rng.randrange(0, 3)
    col_label = rng.choice(("A", "B", "C"))
    col_value = chr(ord(col_label) + 1)
    for label, value in _lines(spec, rng):
        ws[f"{col_label}{r}"] = label
        ws[f"{col_value}{r}"] = value
        r += 1
    wb.save(str(path))


def _write_electronic(path: Path, spec: NotificationSpec,
                      _rng: random.Random) -> None:
    """The portal feed: structured, complete, and boring.

    *"Structure ingestion so the electronic feed drops in as an additional
    source without rework, since the requirement asks for that compatibility
    explicitly."* It is generated here so that claim is tested by a reader
    producing the same record as the document readers, rather than argued for
    in a design note.

    Note it keeps the **same hand-typed vessel name** as the documents. A
    structured feed removes format ambiguity; it does not stop an agent typing
    "GRANITE TRUIMPH" into a web form.
    """
    payload = {
        "notificationId": spec.notification_id,
        "submittedAt": spec.received_at.isoformat(),
        "vessel": {
            "name": spec.values.get("vessel_name"),
            "imo": spec.values.get("imo"),
            "callSign": spec.values.get("call_sign"),
            "flag": spec.values.get("flag"),
        },
        "voyage": {
            "lastPort": spec.values.get("last_port"),
            "arrivalPort": spec.values.get("arrival_port"),
            "eta": spec.values.get("eta"),
            "cargo": spec.values.get("cargo"),
        },
        "parties": {
            "owner": spec.values.get("owner"),
            "agent": spec.values.get("agent"),
            "crew": spec.values.get("crew_count"),
        },
    }
    # Omitted fields are absent, not null — a portal that sends `null` and one
    # that omits the key are saying the same thing, and the reader has to cope
    # with the shape that is harder to notice.
    for key in spec.omitted:
        for group in ("vessel", "voyage", "parties"):
            payload[group] = {k: v for k, v in payload[group].items()
                              if v is not None}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


_WRITERS = {
    "pdf": _write_pdf,
    "pdf_scan": _write_pdf_scan,
    "docx": _write_docx,
    "xlsx": _write_xlsx,
    "electronic": _write_electronic,
}


def write_notifications(specs, out_dir: Path, *, seed: int = 7) -> dict:
    """Write every spec to disk in its own format. Returns a per-format count.

    Missing writers are reported rather than raised: a laptop without
    `python-docx` should still produce the other four formats and say which one
    it could not write, because a generator that refuses to run is a generator
    nobody runs.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, int] = {}
    skipped: dict[str, str] = {}
    for spec in specs:
        writer = _WRITERS[spec.document_format]
        rng = random.Random(
            int(hashlib.sha256(spec.notification_id.encode()).hexdigest()[:8], 16)
            ^ seed)
        try:
            writer(out_dir / spec.document_name, spec, rng)
        except ImportError as e:                             # pragma: no cover
            skipped.setdefault(spec.document_format, str(e))
            continue
        written[spec.document_format] = written.get(spec.document_format, 0) + 1
    if skipped:
        written["_skipped"] = skipped                        # pragma: no cover
    return written


def eta_text(when: datetime, rng: random.Random) -> str:
    """An ETA written the way a form holds it, which is never ISO 8601.

    Four renderings, all real: day-first with slashes, day-first with a month
    name, ISO date with a separate time, and a 12-hour clock. A reader that
    parses one of these parses a quarter of an inbox.
    """
    style = rng.randrange(4)
    if style == 0:
        return when.strftime("%d/%m/%Y %H:%M")
    if style == 1:
        return when.strftime("%d %b %Y %H%M") + " LT"
    if style == 2:
        return when.strftime("%Y-%m-%d") + " at " + when.strftime("%H:%M")
    hour = when.strftime("%I:%M %p").lstrip("0")
    return when.strftime("%d-%m-%Y") + " " + hour


def jitter_eta(when: datetime, rng: random.Random,
               *, hours: float = 6.0) -> datetime:
    """A declared ETA near the real arrival, because paperwork is filed early.

    A notification is submitted 24-72 hours out and states an estimate. Making
    the declared time exactly equal the observed arrival would build a corpus
    where any disagreement at all is a finding, and the arrival-window rule
    would then be measuring the generator's precision rather than a vessel's
    honesty.
    """
    return when + timedelta(hours=rng.uniform(-hours, hours))
