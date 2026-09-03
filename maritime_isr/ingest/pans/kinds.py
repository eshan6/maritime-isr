"""What kind of paper this is — read off the page, not off the filename.

One mailbox receives more than pre-arrival notifications. It receives arrival
and departure reports, crew lists, cargo manifests and port clearance
certificates, and every one of them arrives as a PDF, a scanned fax, a Word
form, a spreadsheet or a portal payload. **The kind is not the format**: a crew
list and a PANS in the same format are read by the same reader and differ only
in what the form has boxes for.

Knowing the kind matters for one reason, and it is a three-valued-outcomes
reason. A departure report has no ETA box. `anomaly.paperwork` will therefore
answer `not_checkable` for its arrival window, every time, correctly — and
without the kind, an operator reading a queue of "not checkable" cannot tell
"the reader failed on this form" from "this form does not ask that question".
Those are different facts and the whole area rests on not folding them together.

**It is read from the document, not from the filename.** A filename is what
somebody's mail client called an attachment; the title block is what the form
says it is. The same lesson ADR-036 paid for with `received_at`: a value
inferred from the filesystem and a value read off the page are not the same
evidence.

**And it is a guess that is allowed to fail.** A form whose title nothing here
recognises returns `None`, which lands as a null `document_kind`. Guessing
"pans" for anything unrecognised would put a form with no ETA box into the same
bucket as one that has an ETA box and left it empty.
"""
from __future__ import annotations

from typing import Optional

from .extract import squash

__all__ = ["DOCUMENT_KIND_TITLES", "KIND_LABELS", "classify_document",
           "kind_label"]


#: Title phrases that name a kind, in the order they are tried.
#:
#: Deliberately wider than what `scenario.pans` writes: "Report of Arrival",
#: "Outward Clearance" and "List of Crew" are titles real Indian port paperwork
#: carries and the generator never produces. A classifier that only knew its own
#: corpus's six titles would be a lookup table, not a reader — the same
#: circularity `EXTRA_SYNONYMS` exists to avoid on the label side.
#:
#: Order is load-bearing in one place: a pre-arrival notification's title
#: contains the word "arrival", so it is tried before the arrival report.
#:
#: They are written as phrases and squashed at import, never as pre-squashed
#: strings. `squash` folds `L` into `I` for the scanner's sake, so a hand-typed
#: "CREWLIST" here would never match a squashed "CREWIIST" and the classifier
#: would silently recognise nothing — a fold that only works in one direction is
#: the exact defect the confusion table's own docstring records.
DOCUMENT_KIND_TITLES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("pans", ("Pre-Arrival Notification", "Prior Arrival Notification",
              "Pre Arrival Information", "Form PANS")),
    ("crew_list", ("Crew List", "List of Crew", "Crew Particulars",
                   "Crew Declaration")),
    ("cargo_manifest", ("Cargo Declaration", "Cargo Manifest", "Manifest",
                        "Cargo List", "Freight Manifest")),
    ("port_clearance", ("Port Clearance", "Clearance Certificate",
                        "Certificate of Clearance", "Outward Clearance")),
    ("departure_report", ("Departure Report", "Report of Departure",
                          "Departure Declaration",
                          "General Declaration Departure", "Sailing Report")),
    ("arrival_report", ("Arrival Report", "Report of Arrival",
                        "Arrival Declaration",
                        "General Declaration Arrival", "Notice of Arrival")),
)

#: The same table, squashed once at import.
_SQUASHED: tuple[tuple[str, tuple[str, ...]], ...] = tuple(
    (kind, tuple(squash(phrase) for phrase in phrases))
    for kind, phrases in DOCUMENT_KIND_TITLES)

#: What to call each kind when an operator is reading it.
KIND_LABELS: dict[str, str] = {
    "pans": "pre-arrival notification",
    "arrival_report": "arrival report",
    "departure_report": "departure report",
    "crew_list": "crew list",
    "cargo_manifest": "cargo manifest",
    "port_clearance": "port clearance certificate",
}

#: How far into a document a title may sit. A form's title is in its first few
#: lines; a phrase further down is prose, and a manifest that mentioned
#: "clearance" in its footer would otherwise re-label the whole document.
_TITLE_WINDOW = 24


def classify_document(passages) -> Optional[str]:
    """Which kind of paperwork these passages came from, or None.

    Matching is on the same squashed form label matching uses — letters only,
    upper-cased, with the scanner's confusions folded — because the title of a
    faxed form is OCR'd like everything else on it and "CREW UST" has to reach
    the same answer as "CREW LIST".
    """
    for p in list(passages)[:_TITLE_WINDOW]:
        text = squash(getattr(p, "text", p))
        if not text:
            continue
        for kind, patterns in _SQUASHED:
            if any(pattern in text for pattern in patterns):
                return kind
    return None


def kind_label(kind: Optional[str]) -> str:
    """A plain-English name for a kind, for an operator-facing line."""
    if not kind:
        return "document of an unrecognised kind"
    return KIND_LABELS.get(kind, kind.replace("_", " "))
