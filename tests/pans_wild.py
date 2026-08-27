"""Arrival-notification documents the generator has never seen — ADR-036.

**Why this file exists, and why it is not in `maritime_isr/`.**

The corpus generator (`scenario.pans`) and the extractor (`ingest.pans.extract`)
share one `LABEL_SYNONYMS` table. That is deliberate — the two must not disagree
about which labels *exist*, or the corpus stops being a fair test of the
resolver — but it makes any extraction score measured on that corpus circular:
adding a synonym to the shared table teaches the generator to write it, so the
extractor is never measured on a label it was not told about in advance.

These documents are the other half. Every one of them is written the way an
agency the project has never received a form from would write it: labels the
generator does not hold, values in notations it does not produce, and the
structural traps a real inbox contains — a two-column form, a label split across
an OCR line break, an empty value followed by the next label, a value with a
colon in it, a field the schema does not model sitting next to one it does.

**They live in `tests/` on purpose.** Nothing under `maritime_isr/` may import
this module. The moment the generator can see these labels they stop being wild
and the number they produce goes back to being circular.

**Every figure derived from this file is on the synthetic suite** (CLAUDE.md
§4.6) — it is a harder synthetic than the corpus, not real mail. Real agency
forms will be worse, and this number must be re-measured against real
attachments before it is quoted anywhere outside the project.

The expectations are what a *correct* extractor would return. Some of them the
extractor does not yet meet; those are the honest residual, and the harness
reports them rather than the fixture being softened to match the code.
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: Expected-value sentinel: the agent wrote something that explicitly means
#: "nothing here" — NIL, N/A, a dash, TBA. That is a *different fact* from a
#: field nobody filled in, and the extractor is expected to record the passage
#: while declining to invent a value from it.
ABSENT = "<declared-absent>"


@dataclass(frozen=True)
class WildDoc:
    """One document nobody in `maritime_isr/` has seen, and its answer key."""

    name: str
    #: What this document is testing. Read by the harness's failure report, so
    #: a regression names the kind of form it broke on rather than an index.
    why: str
    passages: tuple[str, ...]
    #: field -> the value a correct extractor returns, or `ABSENT`.
    #: **Any field extracted that is not a key here counts as a
    #: misattribution** — a value on the wrong field is the failure this whole
    #: fixture set exists to catch, and it is worse than reading nothing.
    expect: dict = field(default_factory=dict)
    method: str = "pdf_text"
    confidence: float = 0.97


WILD_DOCUMENTS: tuple[WildDoc, ...] = (

    WildDoc(
        name="numbered_all_caps",
        why="a numbered ALL-CAPS form; ordinal date, crew in persons, a call "
            "sign broken up with hyphens",
        passages=(
            "PRE-ARRIVAL NOTIFICATION OF SHIPS",
            "1. NAME OF SHIP: PACIFIC DAWN",
            "2. LLOYD'S REGISTER NO.: 9074729",
            "3. SIGNAL LETTERS: 9V-AB-2",
            "4. COUNTRY OF REGISTRY: SINGAPORE",
            "5. PORT OF ORIGIN: JEBEL ALI",
            "6. PORT OF DESTINATION: MUNDRA",
            "7. EXPECTED DATE OF ARRIVAL: 3rd July 2026 0600 hrs",
            "8. NATURE OF GOODS: BULK CEMENT",
            "9. NUMBER OF CREW: 22 PERSONS",
            "10. VESSEL OWNER: PACIFIC DAWN SHIPPING LTD",
            "11. SHIPPING AGENT: SEAWAYS AGENCIES PVT LTD",
            "12. FILING DATE: 01.07.2026",
        ),
        expect={
            "vessel_name": "PACIFIC DAWN",
            "imo": "9074729",
            "call_sign": "9VAB2",
            "flag": "SINGAPORE",
            "last_port": "JEBEL ALI",
            "arrival_port": "MUNDRA",
            "eta": "2026-07-03T06:00:00+00:00",
            "cargo": "BULK CEMENT",
            "crew_count": "22",
            "owner": "PACIFIC DAWN SHIPPING LTD",
            "agent": "SEAWAYS AGENCIES PVT LTD",
            "filed_at": "2026-07-01T00:00:00+00:00",
        },
    ),

    WildDoc(
        name="ocr_fax",
        why="a scanned fax: letters read as bars, a label split across a line "
            "break, and a cargo value with a colon in it",
        method="ocr",
        confidence=0.58,
        passages=(
            "PRE-ARRIVAI NOTIFICATION OF SHIPS",
            "Name of the Vesse|: SOUTHERN TRADER",
            "IMO Numbcr: 1OOOOO7",
            "Last Port of",
            "Call: Karachi",
            "Port of Arriva|: Mumbai",
            "ETA: 03-JUL-2026 0600",
            "Cargo Description: Crude oil: 80,000 MT",
            "Crew Strength: 19",
            "Fiag State: Iiberia",
            "Agents Name: Coastal Marine Services",
            "Fiiing Date: 30 Jun 2026 1100",
        ),
        expect={
            "vessel_name": "SOUTHERN TRADER",
            "imo": "1000007",
            "last_port": "Karachi",
            "arrival_port": "Mumbai",
            "eta": "2026-07-03T06:00:00+00:00",
            "cargo": "Crude oil: 80,000 MT",
            "crew_count": "19",
            "flag": "Iiberia",
            "agent": "Coastal Marine Services",
            "filed_at": "2026-06-30T11:00:00+00:00",
        },
    ),

    WildDoc(
        name="two_column",
        why="a two-column form: every line holds two label/value pairs, and "
            "half the right-hand ones are fields this schema does not model",
        passages=(
            "Ship's Name: NORTH STAR                Radio Callsign: 3EAB7",
            "Nationality of Vessel: PANAMA          Gross Tonnage: 48,120",
            "Last Port Departed: Fujairah           Next Port: Sohar",
            "Expected Date of Arrival: 05/07/2026 18:00    "
            "Port of Destination: Kandla",
            "Cargo Type: Containers                 Master's Name: A. Ferreira",
            "Number of Crew: 21                     Draught on Arrival: 11.2 m",
            "Shipping Agent: Gateway Marine         Filing Date: 02/07/2026 09:15",
        ),
        expect={
            "vessel_name": "NORTH STAR",
            "call_sign": "3EAB7",
            "flag": "PANAMA",
            "last_port": "Fujairah",
            "eta": "2026-07-05T18:00:00+00:00",
            "arrival_port": "Kandla",
            "cargo": "Containers",
            "crew_count": "21",
            "agent": "Gateway Marine",
            "filed_at": "2026-07-02T09:15:00+00:00",
        },
    ),

    WildDoc(
        name="explicit_absences",
        why="the agent wrote NIL, N/A, a dash, SAME and TBA — none of which is "
            "a value, and none of which is the same fact as a blank box",
        method="xlsx_cell",
        confidence=1.0,
        passages=(
            "Vessel: ATLANTIC PIONEER",
            "IMO No.: 1000019",
            "Call Sign: --",
            "Flag: PANAMA",
            "Cargo Details: NIL",
            "Crew Members: N/A",
            "Previous Port of Call: SAME",
            "Port of Entry: Mundra",
            "E.T.A.: TBA",
            "Registered Owners: Pioneer Shipping",
            "Radio Call Sign: VTAB4",
            "Date Submitted: 2026-07-01 08:00",
        ),
        expect={
            "vessel_name": "ATLANTIC PIONEER",
            "imo": "1000019",
            # Written absent in the header, filled in further down. The later
            # real value has to win, or a dash in a header block silently
            # blanks a field the form does answer.
            "call_sign": "VTAB4",
            "flag": "PANAMA",
            "cargo": ABSENT,
            "crew_count": ABSENT,
            "last_port": ABSENT,
            "arrival_port": "Mundra",
            "eta": ABSENT,
            "owner": "Pioneer Shipping",
            "filed_at": "2026-07-01T08:00:00+00:00",
        },
    ),

    WildDoc(
        name="empty_value_and_unmodelled_fields",
        why="a label whose value is empty followed by the next label, plus six "
            "fields the schema does not carry sitting where it would be easy "
            "to read them as ones it does",
        method="docx_table",
        confidence=1.0,
        passages=(
            "Crew: Owner: BLUEWATER SHIPPING LTD",
            "Vessel Name: SEA LEOPARD",
            "Agent Address: 14 Marine Drive, Kochi",
            "Port of Registry: Valletta",
            "Vessel Type: Bulk Carrier",
            "Date of Departure from Last Port: 28/06/2026",
            "Transport Document No.: TD-99201",
            "From: Salalah",
            "To: Mundra",
            "ETA at Pilot Station: 04 Jul 2026 0300",
            "Notification Date: 01-07-2026 06:00",
        ),
        expect={
            "owner": "BLUEWATER SHIPPING LTD",
            "vessel_name": "SEA LEOPARD",
            "last_port": "Salalah",
            "arrival_port": "Mundra",
            "eta": "2026-07-04T03:00:00+00:00",
            "filed_at": "2026-07-01T06:00:00+00:00",
        },
    ),

    WildDoc(
        name="bilingual_headings",
        why="an Indian form with Devanagari beside the English; the extractor "
            "has to be script-blind rather than know Hindi",
        passages=(
            "पोत का नाम / Name of Vessel: SAGAR KANYA",
            "ध्वज / Flag: INDIA",
            "अंतिम बंदरगाह / Last Port: Kochi",
            "आगमन बंदरगाह / Port of Arrival: Mumbai",
            "ETA / अनुमानित आगमन: 2026/07/04 22:10",
            "IMO: IMO1000021",
            "चालक दल / Crew: 20",
            "Filed On: 02 Jul 2026 1600",
        ),
        expect={
            "vessel_name": "SAGAR KANYA",
            "flag": "INDIA",
            "last_port": "Kochi",
            "arrival_port": "Mumbai",
            "eta": "2026-07-04T22:10:00+00:00",
            "imo": "1000021",
            "crew_count": "20",
            "filed_at": "2026-07-02T16:00:00+00:00",
        },
    ),

    WildDoc(
        name="units_and_punctuation",
        why="units and notes in the label, spaces inside a call sign, a dash "
            "for a separator, and an ordinal filing date",
        passages=(
            "Vessel Name (in full).: OCEANIC SPIRIT",
            "IMO No .: 1000033",
            "Call Sign : A B C 1",
            "Cargo on Board (MT): Iron Ore",
            "Crew (incl. Master): 22 (incl. master)",
            "Last Port of Call - Mumbai",
            "PORT OF ARRIVAL: KANDLA",
            "eta: 03.07.2026 14:30",
            "OWNER'S NAME: Oceanic Spirit Ltd",
            "AGENCY: Blue Anchor Agencies",
            "Date of Notification: 1st July 2026",
        ),
        expect={
            "vessel_name": "OCEANIC SPIRIT",
            "imo": "1000033",
            "call_sign": "ABC1",
            "cargo": "Iron Ore",
            "crew_count": "22",
            "last_port": "Mumbai",
            "arrival_port": "KANDLA",
            "eta": "2026-07-03T14:30:00+00:00",
            "owner": "Oceanic Spirit Ltd",
            "agent": "Blue Anchor Agencies",
            "filed_at": "2026-07-01T00:00:00+00:00",
        },
    ),

    WildDoc(
        name="spelled_out_counts",
        why="a crew count written in words, a time written before its date, "
            "and a two-digit year",
        method="docx_paragraph",
        confidence=1.0,
        passages=(
            "Name of Ship: MORNING GLORY",
            "Persons on Board: Eighteen",
            "Coming From: Bandar Abbas",
            "Arriving At: Sikka",
            "Estimated Time of Arrival: 0300 hrs 03 Jul 26",
            "Signal Letters: EPCD9",
            "Flag Country: PANAMA",
            "Owner/Operator: Glory Maritime",
            "Cargo Particulars: Ballast — no cargo",
            "Report Date: 30/06/2026",
        ),
        expect={
            "vessel_name": "MORNING GLORY",
            "crew_count": "18",
            "last_port": "Bandar Abbas",
            "arrival_port": "Sikka",
            "eta": "2026-07-03T03:00:00+00:00",
            "call_sign": "EPCD9",
            "flag": "PANAMA",
            "owner": "Glory Maritime",
            # A real ballast declaration, one line after a document full of
            # NILs elsewhere in this fixture set: absence handling must not
            # swallow the phrase the ballast rule actually reads.
            "cargo": "Ballast — no cargo",
            "filed_at": "2026-06-30T00:00:00+00:00",
        },
    ),

    WildDoc(
        name="column_layout_no_colons",
        why="a PDF text layer that lost its colons — the label and value are "
            "separated by whitespace and nothing else",
        passages=(
            "Vessel Name        GULF SENTINEL",
            "IMO                1000045",
            "Last Port          Duqm",
            "Port of Arrival    Mundra",
            "ETA                06 Jul 2026 0930",
            "Crew               17",
            "Agent              Sentinel Marine Agency",
            "Date of Filing     04 Jul 2026 0700",
        ),
        expect={
            "vessel_name": "GULF SENTINEL",
            "imo": "1000045",
            "last_port": "Duqm",
            "arrival_port": "Mundra",
            "eta": "2026-07-06T09:30:00+00:00",
            "crew_count": "17",
            "agent": "Sentinel Marine Agency",
            "filed_at": "2026-07-04T07:00:00+00:00",
        },
    ),

    WildDoc(
        name="header_and_footer_repeats",
        why="the same labels in a header block and again in a sign-off block; "
            "first reading wins and the footer's berth note must not overwrite "
            "the name",
        passages=(
            "Vessel: ATLANTIC PIONEER",
            "IMO: 1000019",
            "Port: Kandla",
            "Date of Filing: 05/07/2026 10:00",
            "--- for port use only ---",
            "Vessel: ATLANTIC PIONEER (Berth 4)",
            "Port of Arrival: Mundra",
        ),
        expect={
            "vessel_name": "ATLANTIC PIONEER",
            "imo": "1000019",
            "arrival_port": "Kandla",
            "filed_at": "2026-07-05T10:00:00+00:00",
        },
    ),

    WildDoc(
        name="damaged_tokens",
        why="the residual: OCR that damages the only token carrying the label, "
            "and digits read as letters inside a time",
        method="ocr",
        confidence=0.45,
        passages=(
            "Vesse| Name: EASTERN LIGHT",
            "Numbcr of Crcw: 19",
            "Fiag: MAITA",
            "IMO Numbcr: 1000057",
            "Iast Port of Caii: Fujairah",
            "Port of Arriva|: Sohar",
            "ETA: 05/07/2026 O8:3O",
            "Date of Fiiing: 01 Jul 2026 0900",
        ),
        expect={
            "vessel_name": "EASTERN LIGHT",
            "crew_count": "19",
            # Values are not repaired the way labels are: "MAITA" is what the
            # scanner read and what an operator will see beside the passage.
            "flag": "MAITA",
            "imo": "1000057",
            "last_port": "Fujairah",
            "arrival_port": "Sohar",
            "eta": "2026-07-05T08:30:00+00:00",
            "filed_at": "2026-07-01T09:00:00+00:00",
        },
    ),

    WildDoc(
        name="portal_variant",
        why="a second portal's export: same structured certainty, different "
            "key names, values already ISO",
        method="electronic_field",
        confidence=1.0,
        passages=(
            "shipName: DESERT ROSE",
            "imoNumber: IMO 1000063",
            "callSign: A6E-2291",
            "flagState: UAE",
            "portOfDeparture: Jebel Ali",
            "portOfArrival: Kandla",
            "estimatedTimeOfArrival: 2026-07-08T04:30:00+00:00",
            "cargoOnBoard: Containers",
            "crewOnBoard: 23",
            "registeredOwner: Rose Shipping FZE",
            "localAgent: Gulf Agency Co",
            "submittedAt: 2026-07-06T11:00:00+00:00",
        ),
        expect={
            "vessel_name": "DESERT ROSE",
            "imo": "1000063",
            "call_sign": "A6E2291",
            "flag": "UAE",
            "last_port": "Jebel Ali",
            "arrival_port": "Kandla",
            "eta": "2026-07-08T04:30:00+00:00",
            "cargo": "Containers",
            "crew_count": "23",
            "owner": "Rose Shipping FZE",
            "agent": "Gulf Agency Co",
            "filed_at": "2026-07-06T11:00:00+00:00",
        },
    ),
)


#: Date and time notations, one per line, none of which the generator writes.
#: Kept separate from the documents so a date failure names the notation rather
#: than the form it happened to sit in.
WILD_DATES: tuple[tuple[str, str], ...] = (
    ("3rd July 2026", "2026-07-03T00:00:00+00:00"),
    ("1st July 2026 0600", "2026-07-01T06:00:00+00:00"),
    ("22nd June 2026 18:45", "2026-06-22T18:45:00+00:00"),
    ("03.07.2026", "2026-07-03T00:00:00+00:00"),
    ("03.07.2026 14:30", "2026-07-03T14:30:00+00:00"),
    ("03-JUL-2026", "2026-07-03T00:00:00+00:00"),
    ("03-JUL-2026 0600", "2026-07-03T06:00:00+00:00"),
    ("0300 hrs 03 Jul 26", "2026-07-03T03:00:00+00:00"),
    ("2026/07/03", "2026-07-03T00:00:00+00:00"),
    ("2026/07/03 22:10", "2026-07-03T22:10:00+00:00"),
    ("03/07/26 14:30", "2026-07-03T14:30:00+00:00"),
    ("03 Jul 26 1430", "2026-07-03T14:30:00+00:00"),
    ("Fri 03 Jul 2026 14:30", "2026-07-03T14:30:00+00:00"),
    ("03 July 2026", "2026-07-03T00:00:00+00:00"),
    ("2026-07-03 14:30 IST", "2026-07-03T14:30:00+00:00"),
    ("03/07/2026 02:30 PM", "2026-07-03T14:30:00+00:00"),
    # Day-first, always. Reading this as 12 July would put an arrival five
    # months out with nothing raising an error.
    ("07/12/2026 14:30", "2026-12-07T14:30:00+00:00"),
    ("07.12.2026", "2026-12-07T00:00:00+00:00"),
)


#: Values that mean "the agent answered, and the answer is nothing".
WILD_ABSENCES: tuple[str, ...] = (
    "NIL", "nil", "N/A", "n/a", "N.A.", "NA", "None", "NONE", "--", "---",
    "-", "TBA", "TBC", "To be advised", "To be confirmed", "Not applicable",
    "SAME", "As above", "Unknown", "Not known",
)


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------

def score_documents(extract_notification, Passage, docs=WILD_DOCUMENTS) -> dict:
    """Run the extractor over the wild set and count what it got.

    Four outcomes per expected field, and the third and fourth are not the
    same failure at all:

    * **correct** — the field was read and the value is right.
    * **missed** — the field was not read. Costly, recoverable: the rule
      downstream says "not checkable", which is an honest answer.
    * **wrong_value** — the field was read and the value is wrong.
    * **misattributed** — a field was read that the document does not answer.
      This is the one that matters. A value on the wrong field is a
      confident error, and the rules downstream will compare it against a
      track and report a contradiction nobody wrote.
    """
    correct = missed = wrong = 0
    misattributed: list[str] = []
    failures: list[str] = []
    expected = 0

    for doc in docs:
        passages = [Passage(text, "page 1", doc.confidence, doc.method)
                    for text in doc.passages]
        got = extract_notification(passages)
        for name, want in doc.expect.items():
            expected += 1
            f = got.get(name)
            if f is None:
                missed += 1
                failures.append(f"{doc.name}: {name} MISSED (want {want!r})")
                continue
            if want is ABSENT:
                if f.value is None:
                    correct += 1
                else:
                    wrong += 1
                    failures.append(
                        f"{doc.name}: {name} invented {f.value!r} from an "
                        f"explicit absence")
                continue
            if f.value == want:
                correct += 1
            else:
                wrong += 1
                failures.append(
                    f"{doc.name}: {name} = {f.value!r}, want {want!r}")
        for name in got:
            if name not in doc.expect:
                misattributed.append(
                    f"{doc.name}: {name} = {got[name].value!r} — this document "
                    f"does not answer that field ({doc.why})")

    return dict(expected=expected, correct=correct, missed=missed,
                wrong_value=wrong, misattributed=len(misattributed),
                accuracy=correct / expected if expected else 0.0,
                misattributions=misattributed, failures=failures)
