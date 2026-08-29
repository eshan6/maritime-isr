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

*A value is not a string.* An ETA arrives in a dozen notations, an IMO arrives
with "IMO" glued to the front, a crew count arrives as "22 persons" or spelled
out in words. Each field has its own parse, and each parse is allowed to fail —
a value that cannot be read is recorded with its raw text and a halved
confidence rather than dropped, because "the form said something we could not
parse" and "the form said nothing" are different facts and an operator needs to
see which one happened.

**And a fourth, which is the one that costs most when it goes wrong.**

*A form carries fields this schema does not model, and they sit next to the ones
it does.* "Next Port", "Port of Registry", "Vessel Type", "Draught on Arrival",
"Agent Address" — every one of them contains a word that names a field we do
extract. Reading a berthing draught as a last port is not a missing value, it is
a **wrong** one: the paperwork rules will compare it against a track and report
a contradiction nobody wrote. So this module carries an explicit list of labels
it recognises *in order to refuse them* (:data:`UNSUPPORTED_LABELS`), and a
label match must beat every unsupported reading of the same text before it
counts. Failing to read a field is recoverable — the rule downstream says "not
checkable", which is an honest answer. Reading the wrong one is not.

**On measuring any of this.** The generator (`scenario.pans`) and this module
share `LABEL_SYNONYMS` deliberately: the corpus and the reader must not disagree
about which labels *exist*. That makes accuracy measured on the corpus circular
— a synonym added there is a synonym the generator then writes. So the widening
here is measured against `tests/pans_wild.py`, a fixture set of labels and
notations **the generator never writes** and cannot see. :data:`EXTRA_SYNONYMS`
lives here rather than in the generator for exactly that reason: adding an entry
must not teach the corpus to write it.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from ...schemas import ExtractedField
from ...scenario.pans import LABEL_SYNONYMS
from ..sanctions_match import imo_checksum_ok
from .readers import normalise_ws

__all__ = ["extract_notification", "FIELD_PATTERNS", "EXTRA_SYNONYMS",
           "UNSUPPORTED_LABELS", "parse_eta", "parse_imo", "parse_crew",
           "parse_call_sign", "squash", "OCR_CONFUSIONS", "ABSENT_METHOD",
           "is_declared_absence"]


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
#: table, and that no label set collides with an unsupported label either. A
#: fold that merged two labels would attach a value to the wrong field, which is
#: worse than failing to read it.
OCR_CONFUSIONS = str.maketrans({
    "0": "O", "1": "I", "5": "S", "8": "B", "|": "I", "!": "I", "¢": "C",
    "L": "I",
})


def squash(text: str) -> str:
    """A label reduced to what survives a bad scan: letters, uppercased."""
    return re.sub(r"[^A-Z]", "",
                  (text or "").upper().translate(OCR_CONFUSIONS))


#: Labels this extractor understands that the generator does **not** write.
#:
#: This is the half of the widening that can be measured. The shared
#: `LABEL_SYNONYMS` table teaches the corpus and the reader the same
#: vocabulary, so an entry added there is one the generator immediately starts
#: producing and the reader is never tested on. Entries here are invisible to
#: the generator, which is what makes `tests/pans_wild.py` a test rather than a
#: rehearsal.
#:
#: Every entry is a label a real agency form uses for a field this schema
#: already carries. None of them is a guess at a *different* field — those go in
#: :data:`UNSUPPORTED_LABELS` and are refused.
EXTRA_SYNONYMS: dict[str, tuple[str, ...]] = {
    "vessel_name": ("Name of Ship", "Ship's Name", "Name of the Vessel",
                    "Vessel's Name", "Vsl Name", "Ship/Vessel Name",
                    "Name of Vessel/Ship", "Vessel Name in Full"),
    "imo": ("IMO Ship Identification Number", "IMO Ship ID", "IMO ID",
            "Lloyd's Register No.", "Lloyds No.", "LR No.", "IMO Reg No",
            "IMO Number of Ship"),
    "call_sign": ("Signal Letters", "Call Signal", "International Call Sign",
                  "Int'l Call Sign", "Radio Callsign", "Callsign/Signal Letters",
                  "Call Sign/Signal Letters"),
    "flag": ("Country of Registry", "Flag Country", "Flag/Nationality",
             "Nationality of Vessel", "Registered Flag", "Country of Flag",
             "Flag Administration"),
    "last_port": ("Port of Origin", "Previous Port of Call", "Last Port Departed",
                  "Sailed From", "Coming From", "Port Sailed From", "From Port",
                  "Port of Last Call", "Last Port/Place of Call",
                  "Departed From", "Origin Port"),
    "arrival_port": ("Port of Destination", "Intended Port of Arrival",
                     "Arriving At", "Port of Entry", "Destination",
                     "Port of Call in India", "Arrival At Port"),
    "eta": ("Expected Date of Arrival", "Estimated Time of Arrival",
            "Est. Time of Arrival", "Date and Time of Arrival",
            "Arrival Date and Time", "Arrival Date/Time", "Date of Arrival",
            "Expected Arrival Date", "ETA at Port", "ETA Port"),
    "cargo": ("Nature of Goods", "Cargo Description", "Description of Cargo",
              "Type of Cargo", "Cargo Details", "Cargo Type", "Goods",
              "Cargo Particulars", "Cargo Carried", "Nature of Cargo on Board"),
    # "Persons on Board" is not strictly the crew — it includes anyone else
    # aboard — but no form that uses it also states a crew count, and a count
    # of souls on board is what the field is read for. It is the one entry here
    # that widens the *meaning* of a field rather than its spelling, and it is
    # written down so that is a decision rather than an accident.
    "crew_count": ("Number of Crew", "No of Persons on Board",
                   "Persons on Board", "Total Persons on Board",
                   "Crew Strength", "Crew Members", "Complement", "POB",
                   "Crew Nos", "Number of Crew on Board"),
    "owner": ("Vessel Owner", "Ship Owner", "Shipowner", "Owner/Operator",
              "Registered Owners", "Name of Owner", "Owner Name",
              "Owner's Name", "Managing Owner", "Vessel Owners"),
    "agent": ("Shipping Agent", "Agent Name", "Name of Agent",
              "Local Shipping Agent", "Port Agent", "Agent/Representative",
              "Agents Name", "Agent's Name", "Agency Name",
              "Handling Agent"),
    "filed_at": ("Filing Date", "Date Filed", "Date of Notification",
                 "Submission Date", "Date Submitted", "Report Date",
                 "Date of Report", "Date & Time of Submission",
                 "Submitted At", "Date of This Report"),
}


#: Labels a real PANS form carries for fields **this schema does not model** —
#: recognised so they can be refused.
#:
#: Every entry here contains a word that names a field we do extract, and
#: without this table each one is a confident wrong answer: "Next Port" and
#: "Port of Registry" read as the arrival port, "Draught on Arrival" and "Date
#: of Departure from Last Port" as the last port, "Vessel Type" as the vessel
#: name, "Agent Address" as the agent. A refusal costs a value the rules would
#: then call "not checkable", which is honest. A misattribution costs a
#: contradiction nobody wrote, which is the alert-fatigue failure ADR-004
#: exists to prevent.
UNSUPPORTED_LABELS: tuple[str, ...] = (
    # identity of a kind we do not carry
    "Vessel Type", "Ship Type", "Type of Vessel", "Type of Ship", "Vessel Class",
    "Vessel Category", "Master", "Master's Name", "Name of Master",
    "Master Name", "Crew List", "Crew Nationality", "Crew Nationalities",
    # dimensions and tonnages
    "Gross Tonnage", "GRT", "Net Tonnage", "NRT", "Deadweight", "DWT",
    "Tonnage", "Length Overall", "LOA", "Beam", "Breadth", "Depth",
    "Draught", "Draft", "Arrival Draught", "Draught on Arrival",
    "Present Draught", "Air Draught", "Maximum Draught", "Summer Draught",
    "Draught Fore", "Draught Aft",
    # ports that are not the two we model
    "Port of Registry", "Port of Loading", "Port of Discharge",
    "Port of Refuge", "Next Port", "Next Port of Call", "Subsequent Port",
    "Berth", "Berth No.", "Berth Number", "Anchorage", "Terminal",
    # departures, which are not arrivals
    "ETD", "Expected Time of Departure", "Date of Departure", "Departure Date",
    "Date of Departure from Last Port", "Last Port Departure Date",
    "Date of Last Port Departure", "Last Port Date", "ETB", "ETS",
    "Time of Departure",
    # cargo arithmetic, which is not the nature of the cargo
    "Cargo Quantity", "Quantity of Cargo", "Cargo Tonnage", "Cargo Weight",
    # contact blocks — an address is not an agent, and reading it as one puts
    # a street into a field the resolver matches hulls on
    "Agent Address", "Agent's Address", "Owner Address", "Owner's Address",
    "Agent Contact", "Agent Telephone", "Agent Email", "Owner Contact",
    "Address", "Telephone", "Email", "Fax", "Contact Number", "Mobile",
    # everything else a form asks for
    "Purpose of Call", "Nature of Call", "Number of Passengers", "Passengers",
    "No. of Passengers", "Security Level", "ISSC", "ISSC Certificate",
    "ISPS Level", "Ballast Water", "Bunkers on Board", "Fresh Water",
    "Last 10 Ports of Call", "Defects", "Waste on Board", "Stowaways",
    "P&I Club", "Classification Society", "Class",
)


def _merged_synonyms() -> dict[str, tuple[str, ...]]:
    out: dict[str, tuple[str, ...]] = {}
    for key, syns in LABEL_SYNONYMS.items():
        out[key] = tuple(syns) + tuple(EXTRA_SYNONYMS.get(key, ()))
    for key, syns in EXTRA_SYNONYMS.items():
        out.setdefault(key, tuple(syns))
    return out


#: field -> squashed label forms that mean it. Built from the synonym table the
#: generator writes against, *plus* the extractor-only table above — so the two
#: cannot drift apart about which labels the corpus contains, while the reader
#: still understands more than the corpus was written with.
FIELD_PATTERNS: dict[str, tuple[str, ...]] = {
    key: tuple(dict.fromkeys(
        [squash(s) for s in syns] + [squash(key.replace("_", " "))]))
    for key, syns in _merged_synonyms().items()
}

#: squashed form -> field, for the exact-match pass.
_EXACT: dict[str, str] = {form: field
                          for field, forms in FIELD_PATTERNS.items()
                          for form in forms}

#: squashed forms that name a field we deliberately do not extract.
_REFUSED: frozenset = frozenset(squash(s) for s in UNSUPPORTED_LABELS)

#: Shortest run of a label that may be matched by containment rather than
#: exactly. Three admits "ETA" and "IMO", which are whole labels in their own
#: right; two would admit "TO" and "CS" and let them match inside anything.
_MIN_RUN = 3

#: How many words of a label a containment match may span. A label longer than
#: this is a sentence, not a field name.
_MAX_RUN_WORDS = 6


def _clean_label(text: str) -> str:
    """A label with the furniture a form puts around it removed.

    Parenthesised notes ("(if any)", "(incl. master)", "(MT)"), leading item
    numbers and bullets, and trailing punctuation. All of it is invisible to
    :func:`squash`, which keeps letters only — but it is *not* invisible to the
    exact-match pass, which compares whole labels, so "Crew (incl. Master)"
    would squash to CREWINCIMASTER and match nothing.
    """
    s = re.sub(r"[（(\[][^)\]）]*[)\]）]", " ", text or "")
    s = re.sub(r"^[\s\d.)\]\-–—*•]+", " ", s)
    s = re.sub(r"[\s:.\-–—*•]+$", "", s)
    return normalise_ws(s)


_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _tokens(text: str) -> list[str]:
    """A label as squashed words.

    Split on whitespace only — **not** on punctuation — because the characters
    OCR confuses (`|`, `!`, `1`) sit *inside* words and splitting on them would
    turn "Vesse|Name" into two tokens neither of which matches anything, which
    is the exact case the confusion table exists for. camelCase is split,
    because a structured feed's keys are labels too ("submittedAt", "shipName")
    and one reader's key style must not need a second pipeline.
    """
    out = []
    for word in _CAMEL_RE.sub(" ", text or "").split():
        s = squash(word)
        if s:
            out.append(s)
    return out


def _runs(tokens: list[str]):
    """Every contiguous run of words in a label, longest first per start."""
    for i in range(len(tokens)):
        for j in range(min(len(tokens), i + _MAX_RUN_WORDS), i, -1):
            yield "".join(tokens[i:j])


def _label_of(candidate: str, *, exact_only: bool = False) -> Optional[str]:
    """Which field this label names, or None.

    Exact match on the squashed whole label first — that is what the vast
    majority of forms give — then a **word-aligned** containment test for the
    junk a form leaves around a label ("2. Vessel Name", "ETA at Pilot
    Station"). Longest match wins, because "Port" is a substring of "Last Port
    of Call" and the shorter one would steal it.

    **Containment is word-aligned rather than substring, and that is a fix
    rather than a refinement.** A plain substring test reads "Transport
    Document No." as a port, because PORT is inside TRANSPORT. Matching only
    whole runs of words cannot do that.

    **An unsupported label beats a supported one of the same length.** "Next
    Port" contains PORT and "Date of Departure from Last Port" contains LAST
    PORT; both are refused, because the value beside them is not the value we
    would be recording.
    """
    cleaned = _clean_label(candidate)
    s = squash(cleaned)
    if not s:
        return None
    if s in _EXACT:
        return _EXACT[s]
    if s in _REFUSED:
        return None
    if exact_only:
        return None
    best_len, best_field = 0, None
    refused_len = 0
    for run in _runs(_tokens(cleaned)):
        if len(run) < _MIN_RUN:
            continue
        if run in _REFUSED and len(run) > refused_len:
            refused_len = len(run)
        field = _EXACT.get(run)
        if field is not None and len(run) > best_len:
            best_len, best_field = len(run), field
    if refused_len >= best_len:
        return None
    return best_field


def _recognised(candidate: str) -> bool:
    """Is this text a label we know at all — including one we refuse?

    Used to find the *second* label/value pair on a line of a two-column form.
    Exact-match only: a value that merely contains a field word ("Oceanic
    Agency Ltd") must not be mistaken for the start of a new field, because
    splitting there would truncate the value it belongs to.
    """
    s = squash(_clean_label(candidate))
    return bool(s) and (s in _EXACT or s in _REFUSED)


# --------------------------------------------------------------------------
# per-field value parsing
# --------------------------------------------------------------------------

#: A run of exactly seven digits. Exactly, because an IMO is seven digits and
#: grabbing the first seven of an eight-digit number invents a hull.
_IMO_RE = re.compile(r"(?<!\d)(\d{7})(?!\d)")
#: Seven digits anchored to the word IMO, with or without anything between —
#: "IMO No. 1000007", "IMO1000007". Anchored wins over any other seven digits
#: on the line, because a form holds registration and licence numbers too.
_IMO_ANCHORED_RE = re.compile(r"IMO[^0-9]{0,10}(\d{7})", re.I)
_INT_RE = re.compile(r"(?<!\d)(\d{1,3})(?!\d)")

#: Digits a scanner reads as letters, and the letters it reads as digits. Only
#: ever applied to a token that is *already* mostly digits — see
#: :func:`_ocr_digits`.
_OCR_DIGITS = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "|": "1",
                             "S": "5", "s": "5", "B": "8", "b": "8"})
_DIGITY_RE = re.compile(r"^[0-9OoIl|SsBb.:/\-]+$")


def _ocr_digits(text: str) -> str:
    """A value with letters a scanner substituted for digits put back.

    Applied token by token and only to tokens that are already mostly digits,
    so "3rd" and "July" are untouched — they contain characters the fold does
    not know, which is what makes the guard cheap and tight. It is only ever
    reached *after* the ordinary parse has failed, so a value that reads fine
    is never rewritten.
    """
    out = []
    for word in (text or "").split():
        if _DIGITY_RE.match(word) and any(c.isdigit() for c in word):
            out.append(word.translate(_OCR_DIGITS))
        else:
            out.append(word)
    return " ".join(out)


#: Notations a form writes a date and time in. Day-first throughout; see
#: :func:`parse_eta`.
_ETA_FORMATS = (
    # ISO and portal shapes
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M",
    "%Y-%m-%d", "%Y/%m/%d",
    # day-first with separators
    "%d/%m/%Y %H:%M", "%d-%m-%Y %H:%M", "%d.%m.%Y %H:%M",
    "%d/%m/%y %H:%M", "%d-%m-%y %H:%M", "%d.%m.%y %H:%M",
    "%d/%m/%Y %I:%M %p", "%d-%m-%Y %I:%M %p", "%d.%m.%Y %I:%M %p",
    "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y",
    "%d/%m/%y", "%d-%m-%y", "%d.%m.%y",
    # day-first with a month name, spaced or hyphenated
    "%d %b %Y %H%M", "%d %B %Y %H%M", "%d %b %Y %H:%M", "%d %B %Y %H:%M",
    "%d %b %Y %I:%M %p", "%d %B %Y %I:%M %p",
    "%d-%b-%Y %H%M", "%d-%b-%Y %H:%M", "%d/%b/%Y %H:%M",
    "%d %b %y %H%M", "%d %b %y %H:%M", "%d-%b-%y %H%M", "%d-%b-%y %H:%M",
    "%d %b %Y", "%d %B %Y", "%d-%b-%Y", "%d %b %y", "%d-%b-%y",
    # time first, which is how a form written by a watchkeeper reads
    "%H%M %d %b %Y", "%H%M %d %B %Y", "%H%M %d %b %y",
    "%H%M %d/%m/%Y", "%H%M %d-%m-%Y", "%H:%M %d/%m/%Y", "%H:%M %d %b %Y",
)

_ORDINAL_RE = re.compile(r"\b(\d{1,2})(st|nd|rd|th)\b", re.I)
_WEEKDAY_RE = re.compile(
    r"\b(mon|tue|tues|wed|weds|thu|thur|thurs|fri|sat|sun)[a-z]*\b\.?,?", re.I)
#: Words a form puts around a time and that carry no information we keep. Note
#: the timezone words are *dropped*, not applied: a form saying "1430 LT" is
#: read as 1430 UTC. That is wrong by up to five and a half hours here and it
#: is deliberate — the alternative is guessing which local time an unnamed
#: agency meant, and every rule that reads these values tolerates hours, not
#: minutes (ARRIVAL_SLIP_HOURS is 24).
_NOISE_RE = re.compile(
    r"\b(at|on|of|LT|LMT|UTC|GMT|IST|SGT|hrs|hours|local|time|approx"
    r"|approximately)\b", re.I)


def parse_eta(raw: str) -> Optional[datetime]:
    """A declared arrival, in whichever notation the form used.

    Real forms hold "12/07/2026 14:30", "12 Jul 2026 1430 LT", "2026-07-12 at
    14:30", "12-07-2026 2:30 PM", "3rd July 2026", "03.07.2026",
    "03-JUL-2026", "0300 hrs 03 Jul 26" and "2026/07/03". Each is unambiguous
    on its own and only one of them is ISO 8601. The cleanup strips the words a
    form puts around a time ("at", "LT", "hrs"), the weekday some forms lead
    with, and the ordinal suffix on the day, and then tries the formats in
    order.

    **Day-first, never month-first.** 07/12/2026 is 7 December in every form
    used in this region, and reading it as 12 July would put a vessel's arrival
    five months out with no error anywhere — the worst kind of wrong, because
    the arrival-window rule would then fire on a correct notification. No
    month-first format appears in the table, so an American form would be
    misread; that is a known limit rather than an oversight, and it is the
    right way round for this AOI.

    **The OCR repair is a last resort and only for digits.** A scanner reads
    "08:30" as "O8:3O"; the fold is applied only after every notation has
    failed, and only to words that are already mostly digits.
    """
    if not raw:
        return None
    text = normalise_ws(str(raw))
    text = _WEEKDAY_RE.sub(" ", text)
    text = _NOISE_RE.sub(" ", text)
    text = _ORDINAL_RE.sub(r"\1", text)
    text = normalise_ws(text).replace(",", "")
    got = _try_formats(text)
    if got is None:
        repaired = _ocr_digits(text)
        if repaired != text:
            got = _try_formats(repaired)
    return got


def _try_formats(text: str) -> Optional[datetime]:
    for fmt in _ETA_FORMATS:
        try:
            dt = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return dt.replace(tzinfo=dt.tzinfo or timezone.utc)
    return None


def parse_imo(raw: str) -> Optional[str]:
    """Seven digits, wherever they sit in "IMO No. 1000007".

    Three readings, in decreasing order of what they are worth:

    1. **Seven digits anchored to the word IMO.** A form carries official
       numbers, registration numbers and telephone numbers, all seven digits
       wide; the anchor is what tells them apart.
    2. **A seven-digit run whose check digit validates.** The seventh digit of
       an IMO is a checksum over the first six (see
       :func:`ingest.sanctions_match.imo_checksum_ok`), and it rejects about
       nine in ten random seven-digit strings — so on a line holding two
       candidates it is strong evidence about which one is the hull number.
    3. **The first seven-digit run.** A hull whose form carries a *broken*
       check digit is a finding elsewhere in this system, not a value to
       discard, so the checksum is only ever used to *choose* between
       candidates and never to reject the only one there is.

    Failing all three, the digits a scanner read as letters are put back — and
    that repair alone is required to validate, because a repaired number is a
    guess and the check digit is the only independent evidence that the guess
    is right.
    """
    if not raw:
        return None
    text = str(raw)
    m = _IMO_ANCHORED_RE.search(text)
    if m:
        return m.group(1)
    candidates = _IMO_RE.findall(text)
    if candidates:
        for c in candidates:
            if imo_checksum_ok(c):
                return c
        return candidates[0]
    repaired = _ocr_digits(text)
    if repaired != text:
        for c in _IMO_RE.findall(repaired):
            if imo_checksum_ok(c):
                return c
    return None


#: Crew counts written out. Small on purpose: a form writes "twenty two", not
#: "one hundred and forty".
_WORD_NUMBERS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90,
}


def parse_crew(raw: str) -> Optional[str]:
    """A head count out of "22", "22 persons", "22 (incl. master)", "Twenty Two".

    Bounded to three digits, and the bound is doing work: an unbounded grab
    reads the first three digits of a tonnage or a telephone number as a crew
    and reports a plausible number that is not one.
    """
    if not raw:
        return None
    text = normalise_ws(str(raw))
    m = _INT_RE.search(text)
    if m:
        return m.group(1)
    total = 0
    seen = False
    for word in re.split(r"[\s\-]+", text.lower()):
        word = re.sub(r"[^a-z]", "", word)
        if word in ("and", ""):
            continue
        if word not in _WORD_NUMBERS:
            return None
        total += _WORD_NUMBERS[word]
        seen = True
    return str(total) if seen and total <= 999 else None


_CALL_SIGN_RE = re.compile(r"[A-Z0-9]{3,8}$")


def parse_call_sign(raw: str) -> Optional[str]:
    """A call sign with the spaces and hyphens a form types into it removed.

    "A B C 1", "9V-AB-2" and "ABC1" are one call sign, and the resolver matches
    on exact string equality — deliberately, because a fuzzy identifier match
    puts a notification on the wrong hull. So the *normalisation* has to happen
    here, where it is lossless, rather than being smuggled into the matcher as
    a similarity threshold.

    A trailing note ("VTAB4 (VHF 16)") is cut at the bracket. Anything that
    does not then look like a call sign is refused, which keeps its raw text
    and a halved confidence rather than being cleaned into something plausible.
    """
    if not raw:
        return None
    text = normalise_ws(str(raw))
    text = re.split(r"[(\[,;]", text)[0]
    compact = re.sub(r"[\s./\-–—]", "", text).upper()
    if not _CALL_SIGN_RE.fullmatch(compact):
        return None
    if not any(c.isalpha() for c in compact):
        return None
    return compact


#: What an agent writes when the answer is "nothing". Compared on letters only
#: — so "N/A", "N.A." and "n/a" are one entry — but **without** the OCR fold
#: :func:`squash` applies. The fold exists for labels, which come from a closed
#: vocabulary where collapsing `L` into `I` is checked to be safe; applying it
#: to values would make NIL and NIT the same word.
_ABSENCE_WORDS = frozenset((
    "NIL", "NILL", "NA", "NONE", "TBA", "TBC", "TOBEADVISED", "TOBECONFIRMED",
    "TOBEINTIMATED", "TOBEDECLARED", "NOTAPPLICABLE", "NOTAPPL",
    "NOTAVAILABLE", "NOTKNOWN", "UNKNOWN", "NOTDECLARED", "SAME",
    "SAMEASABOVE", "ASABOVE", "DITTO",
))

#: Method suffix on a field the form explicitly declined to answer.
ABSENT_METHOD = "declared_absent"


def is_declared_absence(raw) -> bool:
    """Did the agent write "nothing here", as opposed to leaving it blank?

    **This is a three-valued distinction and it is kept as one.** A form that
    says NIL and a form with an empty box are different facts: the first is an
    answer, the second is an omission, and ADR-036's whole rules discipline
    rests on not folding "we could not check" into "it was fine".

    So an absence is recorded as a field with a *passage and no value* — the
    line an operator can look at, and nothing for a rule to compare. It is
    deliberately **not** turned into a value:

    * "Cargo: NIL" could mean "in ballast" or "I dashed the box". Reading it as
      the first would fire `check_declared_ballast` against a laden draught on
      the strength of an agent's shorthand, which is the manufactured
      contradiction ADR-004 exists to prevent. The corpus writes "Ballast — no
      cargo" for a real ballast declaration, and that phrase is untouched here.
    * "Crew: NIL" is not a crew of zero.
    * "Last Port: SAME" is a reference to a line we cannot resolve, not a port.

    The cost is a recall one, stated plainly: a form that means "in ballast"
    and writes NIL will not produce the ballast contradiction it might deserve.
    Precision before recall, as CLAUDE.md §4.4 requires.
    """
    text = normalise_ws(str(raw or ""))
    if not text:
        return False
    if len(text) <= 5 and not any(c.isalnum() for c in text):
        return True                        # "--", "—", "?", "*"
    letters = re.sub(r"[^A-Z]", "", text.upper())
    return letters in _ABSENCE_WORDS and not any(c.isdigit() for c in text)


def _clean_text(raw: str) -> Optional[str]:
    v = normalise_ws(raw)
    return v or None


_PARSERS = {
    "imo": parse_imo,
    "crew_count": parse_crew,
    "call_sign": parse_call_sign,
}


#: Fields whose value is a date/time. Both go through `parse_eta`, which is
#: the only date parser here — a second one would be a second place for the
#: day-first rule to be got wrong, and getting it wrong in either of these puts
#: an arrival five months out with no error raised anywhere.
_DATE_FIELDS = ("eta", "filed_at")

#: Outcomes of reading one value. Three of them, for the same reason the rules
#: have three: "we read it", "we could not read it" and "there was nothing to
#: read" are different facts about a document.
_PARSED, _UNPARSED, _ABSENT = "parsed", "unparsed", "absent"


def _parse_value(field: str, raw: str) -> tuple[Optional[str], str]:
    """(value, outcome) for one field's raw text."""
    if is_declared_absence(raw):
        return None, _ABSENT
    if field in _DATE_FIELDS:
        dt = parse_eta(raw)
        if dt is None:
            return _clean_text(raw), _UNPARSED
        return dt.astimezone(timezone.utc).isoformat(), _PARSED
    parser = _PARSERS.get(field)
    if parser is not None:
        got = parser(raw)
        return (got, _PARSED) if got else (_clean_text(raw), _UNPARSED)
    return _clean_text(raw), _PARSED


_SPLIT_RE = re.compile(r"[:\-–—]\s")
#: Two or more spaces, or a tab: what a PDF text layer leaves where a form had
#: a column boundary and no punctuation.
_GAP_RE = re.compile(r"(?:\t| {2,})")
#: Most pairs a single line can hold. A form is a form, not a paragraph.
_MAX_PAIRS = 6


def _split_pairs(text: str) -> list[tuple[str, str]]:
    """One line into its label/value pairs — usually one, sometimes two.

    **Two-column forms are common and they are silent when read wrong.** A row
    holding "Vessel Name: NORTH STAR    Call Sign: 3EAB7" split at the first
    separator gives the vessel name as "NORTH STAR Call Sign: 3EAB7" — a value
    that will never match a hull, on a field that looks like it was read fine.
    So after the first separator, the text is scanned for a *later* one whose
    preceding words are a label this module recognises, and the line is cut
    there.

    Recognition is exact-match only, and that is the safety property: a value
    that merely contains a field word ("Oceanic Agency Ltd", "Crude oil:
    80,000 MT") is not a label and the line is left whole. The cost is that a
    two-column form using a label nobody here has heard of stays merged, which
    is visible in the value rather than hidden.

    A pair whose value is empty is returned as such — "Crew: Owner: BLUEWATER"
    is a form where the crew box was left blank and the next label follows it,
    and reading "Owner: BLUEWATER" as the crew count is the misattribution this
    whole module is arranged to avoid.
    """
    pairs: list[tuple[str, str]] = []
    rest = text
    while len(pairs) < _MAX_PAIRS:
        m = _SPLIT_RE.search(rest)
        if not m:
            break
        label, rest = rest[:m.start()], rest[m.end():]
        own = _field_of_exact(label)
        cut = _next_label_start(rest, own)
        if cut is None:
            tail = rest.strip()
            if tail and _recognised(tail) and _field_of_exact(tail) != own:
                # The line ends on a label with nothing after it: an empty box
                # whose neighbour follows ("Crew: Owner"). Both are labels and
                # neither has a value; reading the second as the first's value
                # is the misattribution this module is arranged to avoid.
                pairs.extend([(label, ""), (tail, "")])
            else:
                pairs.append((label, rest))
            rest = ""
            break
        pairs.append((label, rest[:cut].strip()))
        rest = rest[cut:]
    return pairs


def _field_of_exact(text: str) -> Optional[str]:
    """The field this text names on an exact match, or None."""
    return _EXACT.get(squash(_clean_label(text)))


def _next_label_start(text: str, own_field: Optional[str]) -> Optional[int]:
    """Where the next label begins inside a value, or None.

    The next separator in the text is found, and the words in front of it are
    tried longest-first as a label. Longest-first matters: in "NORTH STAR Radio
    Callsign:" both "Radio Callsign" and "Callsign" are labels, and taking the
    shorter one would leave "Radio" glued to the vessel's name.

    A candidate naming the *same* field as the label being read is not a column
    boundary — "Cargo: Bulk Goods: 5000 MT" is one cargo declaration with a
    colon in it, and cutting at "Goods" would truncate it to "Bulk".
    """
    for m in _SPLIT_RE.finditer(text):
        head = text[:m.start()]
        starts = [0] + [w.start() for w in re.finditer(r"\S+", head)]
        for start in starts:
            candidate = head[start:]
            if not candidate.strip() or not _recognised(candidate):
                continue
            if not any(c.isalpha() for c in candidate.split()[0]):
                # "21 Draught on Arrival" is a crew count followed by a label,
                # not a label — a leading number is item numbering at the start
                # of a line and part of the value in the middle of one.
                continue
            if own_field is not None and _field_of_exact(candidate) == own_field:
                continue
            return start
    return None


def _gap_pairs(raw: str) -> list[tuple[str, str]]:
    """A line whose columns are separated by whitespace and nothing else.

    A PDF text layer frequently loses the colon a form was printed with, and
    leaves "ETA        06 Jul 2026 0930". The head is accepted **only** on an
    exact label match: without a separator there is nothing else distinguishing
    a label from the first two words of a sentence, and a containment match
    here would read the title block as a field.
    """
    parts = [p for p in _GAP_RE.split(normalise_ws_keep_gaps(raw)) if p.strip()]
    if len(parts) < 2:
        return []
    if _label_of(parts[0], exact_only=True) is None:
        return []
    return [(parts[0], " ".join(p.strip() for p in parts[1:]))]


def normalise_ws_keep_gaps(text: str) -> str:
    """Collapse newlines but keep the column gaps a layout used."""
    return re.sub(r"[^\S \t]+", " ", text or "").strip()


def extract_notification(passages) -> dict:
    """Every field this document yields, each with the passage it came from.

    **It takes no format argument, deliberately.** Extraction is format-blind by
    design — the readers already turned five different files into one grammar,
    and a format parameter here would be an invitation to branch on it, which is
    exactly how the electronic feed would stop being "another reader" and start
    being a second pipeline.

    Returns ``{field_name: ExtractedField}``. Fields the document does not
    mention are simply absent — an empty `ExtractedField` and a missing key
    would say the same thing and the caller should not have to know which. A
    field the document *explicitly* leaves empty ("Crew: NIL") is present with
    a passage and a null value: see :func:`is_declared_absence` for why those
    are not the same fact.

    **First reading of a field wins.** Forms repeat labels (a header and a
    footer, a summary block) and later mentions are usually less complete —
    "ATLANTIC PIONEER (Berth 4)" in a sign-off block is not a better vessel
    name than "ATLANTIC PIONEER" in the header. It is stated rather than left to
    dict ordering because "last wins" and "first wins" differ on real documents
    and the choice should be visible.

    **First reading with a *value* wins**, which is not quite the same rule. A
    field held only as a declared absence is upgraded if a later line answers
    it, because a dash in a header block that permanently blanked a field the
    form does answer further down would be first-wins producing a worse answer
    than either line alone.

    **A label broken across a line break is rejoined.** OCR wraps "Last Port of
    Call" onto two lines, and the rejoin is only attempted when the line's own
    label fails to resolve *and* the joined text matches a label exactly — a
    containment match on a joined heading would read a title block as a field.
    """
    out: dict[str, ExtractedField] = {}
    carry: Optional[str] = None
    for p in passages:
        text = normalise_ws(p.text)
        if not text:
            continue
        pairs = _split_pairs(text)
        if not pairs:
            pairs = _gap_pairs(p.text)
        if not pairs:
            # No separator and no column gap: a heading, a logo line, or the
            # first half of a label the scanner wrapped.
            carry = text
            continue
        for i, (label_raw, value_raw) in enumerate(pairs):
            field = _label_of(label_raw)
            if field is None and i == 0 and carry:
                field = _label_of(f"{carry} {label_raw}", exact_only=True)
            if field is None:
                continue
            held = out.get(field)
            if held is not None and held.value is not None:
                continue
            value, outcome = _parse_value(field, value_raw)
            if outcome == _ABSENT:
                if held is not None:
                    continue
                out[field] = ExtractedField(
                    value=None, raw=normalise_ws(value_raw), passage=text,
                    locator=p.locator, method=f"{p.method}_{ABSENT_METHOD}",
                    confidence=p.confidence)
                continue
            if value is None:
                continue               # an empty box says nothing at all
            # A value the parser could not make sense of is kept with its raw
            # text and a halved confidence: the form said *something*, and an
            # operator who can see the passage can often read what the parser
            # could not.
            clean = outcome == _PARSED
            out[field] = ExtractedField(
                value=str(value),
                raw=normalise_ws(value_raw),
                passage=text,
                locator=p.locator,
                method=(p.method if clean else f"{p.method}_unparsed"),
                confidence=(p.confidence if clean
                            else round(p.confidence * 0.5, 3)),
            )
        carry = None
    return out
