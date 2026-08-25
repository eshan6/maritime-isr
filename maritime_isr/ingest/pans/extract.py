"""Passages to fields — labels, synonyms, and the values behind them.

Knows nothing about file formats. Everything here operates on the
`Label: value` grammar the readers produce, which is what lets one extractor
serve a scanned fax and a portal feed alike.

**Three things make this harder than a regex, and all three are in real data.**

*Agencies name fields differently.* "Last Port of Call", "From", "Previous
Port" and "Port of Departure" all mean one thing. A reader keyed to one
spelling reads a fraction of an inbox and reports the rest as missing.

*OCR mangles labels as well as values.* "Vesse| Name" and "IMO Numbcr" are what
a fax gives you. So label matching is done on a squashed form — letters only,
uppercased — and a small confusion table folds the substitutions a scanner
actually makes. It is a *small* table on purpose: every entry is a claim that
two strings are one label, and a loose one attaches a value to the wrong field.

*A value is not a string.* An ETA arrives in four notations, an IMO arrives with
"IMO" glued to the front, a crew count arrives as "22 persons". Each field has
its own parse, and each parse is allowed to fail — a value that cannot be read
is recorded with its raw text and a confidence of zero rather than dropped,
because "the form said something we could not parse" and "the form said
nothing" are different facts and an operator needs to see which one happened.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from ...schemas import ExtractedField
from ...scenario.pans import LABEL_SYNONYMS
from .readers import normalise_ws

__all__ = ["extract_notification", "FIELD_PATTERNS", "parse_eta", "parse_imo",
           "squash", "OCR_CONFUSIONS"]


#: Characters OCR substitutes for one another, folded before label matching.
#: Deliberately tiny. Each entry says "these two are the same letter to a
#: scanner", and a generous table would start matching labels that differ.
#:
#: **`L` folds too, and it is the entry that matters most.** `I`, `l`, `1` and
#: `|` are one shape to a scanner, and the fold has to be to a single
#: representative or it only works in one direction: mapping `|`→`I` alone
#: leaves "Vesse| Name" squashing to VESSEINAME while "Vessel Name" squashes to
#: VESSELNAME, so the *most common label in the corpus* fails to match on
#: exactly the format — a scan — that the fold exists for.
#:
#: Folding `L` into `I` is aggressive, so it is checked rather than assumed:
#: `tests/test_pans.py` asserts no two fields' label sets collide under this
#: table. A fold that merged two labels would attach a value to the wrong
#: field, which is worse than failing to read it.
OCR_CONFUSIONS = str.maketrans({
    "0": "O", "1": "I", "5": "S", "8": "B", "|": "I", "!": "I", "¢": "C",
    "L": "I",
})


def squash(text: str) -> str:
    """A label reduced to what survives a bad scan: letters, uppercased."""
    return re.sub(r"[^A-Z]", "",
                  (text or "").upper().translate(OCR_CONFUSIONS))


#: field -> squashed label forms that mean it. Built from the same synonym
#: table the generator writes against, so the two cannot drift apart about
#: which labels exist — while still disagreeing per document about which one is
#: used, which is the variation being tested.
FIELD_PATTERNS: dict[str, tuple[str, ...]] = {
    key: tuple(squash(s) for s in syns) + (squash(key.replace("_", " ")),)
    for key, syns in LABEL_SYNONYMS.items()
}


def _label_of(candidate: str) -> Optional[str]:
    """Which field this label names, or None.

    Exact match on the squashed form first, then a containment test for the
    junk a scanner leaves around a label ("2. Vessel Name"). Longest match
    wins, because "Last Port" is a substring of nothing but "Port" is a
    substring of "Last Port of Call" and the shorter one would steal it.
    """
    s = squash(candidate)
    if not s:
        return None
    for field, forms in FIELD_PATTERNS.items():
        if s in forms:
            return field
    best: tuple[int, Optional[str]] = (0, None)
    for field, forms in FIELD_PATTERNS.items():
        for form in forms:
            if len(form) >= 4 and form in s and len(form) > best[0]:
                best = (len(form), field)
    return best[1]


# --------------------------------------------------------------------------
# per-field value parsing
# --------------------------------------------------------------------------

_IMO_RE = re.compile(r"(\d{7})")
_INT_RE = re.compile(r"(\d{1,3})")

_ETA_FORMATS = (
    "%d/%m/%Y %H:%M", "%d-%m-%Y %H:%M", "%Y-%m-%d %H:%M",
    "%d %b %Y %H%M", "%d %B %Y %H%M", "%d %b %Y %H:%M",
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d",
    "%d/%m/%Y %I:%M %p", "%d-%m-%Y %I:%M %p",
)


def parse_eta(raw: str) -> Optional[datetime]:
    """A declared arrival, in whichever of four notations the form used.

    Real forms hold "12/07/2026 14:30", "12 Jul 2026 1430 LT", "2026-07-12 at
    14:30" and "12-07-2026 2:30 PM". Each is unambiguous on its own and none is
    ISO 8601. The cleanup strips the words a form puts around a time ("at",
    "LT", "hrs") and then tries the formats in order.

    **Day-first, never month-first.** 07/12/2026 is 7 December in every form
    used in this region, and reading it as 12 July would put a vessel's arrival
    five months out with no error anywhere — the worst kind of wrong, because
    the arrival-window rule would then fire on a correct notification.
    """
    if not raw:
        return None
    text = normalise_ws(str(raw))
    text = re.sub(r"\b(at|LT|UTC|IST|hrs|hours)\b", " ", text,
                  flags=re.IGNORECASE)
    text = normalise_ws(text).replace(",", "")
    for fmt in _ETA_FORMATS:
        try:
            dt = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return dt.replace(tzinfo=dt.tzinfo or timezone.utc)
    return None


def parse_imo(raw: str) -> Optional[str]:
    """Seven digits, wherever they sit in "IMO No. 1000007"."""
    if not raw:
        return None
    m = _IMO_RE.search(str(raw))
    return m.group(1) if m else None


def parse_crew(raw: str) -> Optional[str]:
    if not raw:
        return None
    m = _INT_RE.search(str(raw))
    return m.group(1) if m else None


def _clean_text(raw: str) -> Optional[str]:
    v = normalise_ws(raw)
    return v or None


_PARSERS = {
    "imo": parse_imo,
    "crew_count": parse_crew,
}


#: Fields whose value is a date/time. Both go through `parse_eta`, which is
#: the only date parser here — a second one would be a second place for the
#: day-first rule to be got wrong, and getting it wrong in either of these puts
#: an arrival five months out with no error raised anywhere.
_DATE_FIELDS = ("eta", "filed_at")


def _parse_value(field: str, raw: str) -> tuple[Optional[str], bool]:
    """(value, parsed_cleanly). A failed parse keeps the raw text."""
    if field in _DATE_FIELDS:
        dt = parse_eta(raw)
        if dt is None:
            return _clean_text(raw), False
        return dt.astimezone(timezone.utc).isoformat(), True
    parser = _PARSERS.get(field)
    if parser is not None:
        got = parser(raw)
        return (got, True) if got else (_clean_text(raw), False)
    return _clean_text(raw), True


_SPLIT_RE = re.compile(r"[:\-–—]\s")


def extract_notification(passages) -> dict:
    """Every field this document yields, each with the passage it came from.

    **It takes no format argument, deliberately.** Extraction is format-blind by
    design — the readers already turned five different files into one grammar,
    and a format parameter here would be an invitation to branch on it, which is
    exactly how the electronic feed would stop being "another reader" and start
    being a second pipeline.

    Returns ``{field_name: ExtractedField}``. Fields the document does not
    mention are simply absent — an empty `ExtractedField` and a missing key
    would say the same thing and the caller should not have to know which.

    **First reading of a field wins.** Forms repeat labels (a header and a
    footer, a summary block) and later mentions are usually less complete. It
    is stated rather than left to dict ordering because "last wins" and "first
    wins" differ on real documents and the choice should be visible.
    """
    out: dict[str, ExtractedField] = {}
    for p in passages:
        text = normalise_ws(p.text)
        if not text:
            continue
        parts = _SPLIT_RE.split(text, maxsplit=1)
        if len(parts) != 2:
            # No separator: a heading, a logo line, a stray OCR fragment.
            continue
        label_raw, value_raw = parts[0], parts[1]
        field = _label_of(label_raw)
        if field is None or field in out:
            continue
        value, clean = _parse_value(field, value_raw)
        if value is None:
            continue
        # A value the parser could not make sense of is kept with its raw text
        # and a halved confidence: the form said *something*, and an operator
        # who can see the passage can often read what the parser could not.
        conf = p.confidence if clean else round(p.confidence * 0.5, 3)
        out[field] = ExtractedField(
            value=str(value),
            raw=normalise_ws(value_raw),
            passage=text,
            locator=p.locator,
            method=(p.method if clean else f"{p.method}_unparsed"),
            confidence=conf,
        )
    return out
