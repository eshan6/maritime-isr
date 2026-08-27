"""Pre-arrival notifications: unstructured in, evidence out — ADR-036.

Area 4 of the IDEX Challenge 82 brief. The requirement's difficulty is not the
rules — comparing a declared port against a track is arithmetic — it is that
the input is a mailbox of PDFs, faxes, spreadsheets and Word forms, and that
every value pulled out of one has to be traceable back to the line it came
from.

So the tests run in the order the failures actually happen:

1. **Readers** turn five formats into one grammar, and earn a confidence.
2. **Extraction** finds fields under labels no two agencies spell the same way.
3. **Resolution** attaches a form to a hull, or refuses.
4. **Rules** compare declaration against track, three-valued.
5. **The corpus outcome** — the part that catches a rule which is quietly
   answering "not checkable" for every document it is handed.

Section 5 is not a formality. Four defects in this area survived a green unit
suite and were found only by counting alerts against the authored truth: a
filing time taken from the filesystem, a registry read from one table, a
background corpus contradicting itself by accident, and arrivals judged against
a window predating the record. Each looked like silence, not failure.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

UTC = timezone.utc
T0 = datetime(2026, 7, 1, tzinfo=UTC)


def _field(value, **kw):
    from maritime_isr.schemas import ExtractedField
    kw.setdefault("passage", f"Label: {value}")
    kw.setdefault("locator", "page 1")
    kw.setdefault("method", "pdf_text")
    kw.setdefault("confidence", 0.97)
    return ExtractedField(value=str(value), **kw)


# ==========================================================================
# 1. readers — five formats, one grammar
# ==========================================================================

def test_reader_confidence_is_stratified_by_how_the_value_was_obtained():
    """A spreadsheet cell and an OCR'd fax must not read as equally certain.

    The whole per-field provenance discipline rests on this. If every value
    arrives at 1.0, the confidence column is decoration and an analyst has no
    way to tell a portal field from a guess at a smudge.
    """
    from maritime_isr.ingest.pans.readers import (OCR_MAX_CONFIDENCE,
                                                  OCR_MIN_CONFIDENCE)

    assert 0.0 < OCR_MIN_CONFIDENCE < OCR_MAX_CONFIDENCE < 1.0
    # An OCR'd passage can never reach the certainty of a structured field.
    assert OCR_MAX_CONFIDENCE < 1.0


def test_electronic_feed_produces_passages_not_a_second_pipeline():
    """The portal feed enters at the same seam as a fax.

    It would be easier to let a structured feed set fields directly. It
    deliberately does not: making it emit passages means one extractor serves
    both, so a change to date parsing cannot fix the portal and break the fax.
    """
    import json
    import tempfile
    from pathlib import Path

    from maritime_isr.ingest.pans.readers import read_electronic

    payload = {
        "notificationId": "PANS-TEST",
        "submittedAt": "2026-07-01T09:00:00+00:00",
        "vessel": {"name": "GRANITE TRIUMPH", "imo": "1000007"},
        "voyage": {"lastPort": "Kandla", "eta": "2026-07-03T14:00:00+00:00"},
    }
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "PANS-TEST.json"
        p.write_text(json.dumps(payload), encoding="utf-8")
        passages = read_electronic(p)

    text = {pg.text for pg in passages}
    assert "vessel_name: GRANITE TRIUMPH" in text
    # The filing date is carried through as a field like any other.
    assert any(t.startswith("filed_at: ") for t in text)
    assert all(pg.confidence == 1.0 for pg in passages)
    # Locators point at the portal's own path, so an operator can go and look.
    assert any(pg.locator == "$vessel.name" for pg in passages)


def test_unknown_extension_is_refused_rather_than_read_as_empty():
    """An inbox holds anything. A format nobody wrote a reader for is a gap to
    report, not a notification with no fields."""
    from maritime_isr.ingest.pans.readers import ReaderUnavailable, read_document

    with pytest.raises(ReaderUnavailable):
        read_document("/nonexistent/attachment.rtf")


# ==========================================================================
# 2. extraction — labels no two agencies spell alike
# ==========================================================================

def test_synonyms_and_ocr_mangling_both_resolve_to_one_field():
    from maritime_isr.ingest.pans.extract import _label_of

    for spelling in ("Last Port of Call", "From", "Previous Port",
                     "Port of Departure"):
        assert _label_of(spelling) == "last_port"
    # What a scanner gives you. `l`, `I`, `1` and `|` are one shape to OCR, and
    # "Vessel Name" is the label this has to survive on.
    assert _label_of("Vesse| Name") == "vessel_name"
    assert _label_of("Vessel Name") == "vessel_name"
    assert _label_of("VesseI Name") == "vessel_name"
    # Junk a scanner leaves around a label.
    assert _label_of("2. Vessel Name") == "vessel_name"


def test_the_ocr_fold_never_merges_two_different_labels():
    """The confusion table is aggressive by necessity and safe by check.

    Each entry claims two characters are one letter to a scanner. A fold that
    merged two labels would attach a value to the *wrong* field, which is worse
    than failing to read it — so no squashed form may name more than one field.

    This now covers the extractor-only synonyms too, because that is where a
    widening lands: every label added to `EXTRA_SYNONYMS` is a new chance for
    two fields to collide under the fold, and the check has to grow with the
    table it is checking.
    """
    from maritime_isr.ingest.pans.extract import FIELD_PATTERNS

    owner: dict[str, str] = {}
    for field, forms in FIELD_PATTERNS.items():
        for form in forms:
            assert owner.setdefault(form, field) == field, (
                f"{form!r} names both {owner[form]} and {field}")


def test_eta_is_day_first_in_every_notation():
    """07/12/2026 is 7 December here, and reading it as 12 July would put an
    arrival five months out with no error raised anywhere — the arrival-window
    rule would then fire on a correct notification."""
    from maritime_isr.ingest.pans.extract import parse_eta

    assert parse_eta("07/12/2026 14:30").month == 12
    assert parse_eta("07-12-2026 14:30").month == 12
    assert parse_eta("2026-12-07 14:30").month == 12
    assert parse_eta("7 Dec 2026 1430").month == 12
    # The words a form puts around a time.
    assert parse_eta("12/07/2026 at 14:30 LT") == datetime(
        2026, 7, 12, 14, 30, tzinfo=UTC)


def test_unparsed_value_keeps_its_raw_text_at_reduced_confidence():
    """"The form said something we could not parse" and "the form said nothing"
    are different facts, and an operator has to see which happened."""
    from maritime_isr.ingest.pans.extract import extract_notification
    from maritime_isr.ingest.pans.readers import Passage

    fields = extract_notification([
        Passage("ETA: sometime Tuesday", "page 1", 0.97, "pdf_text")])
    eta = fields["eta"]
    assert eta.value == "sometime Tuesday"
    assert eta.confidence < 0.97
    assert eta.method.endswith("_unparsed")
    assert eta.passage == "ETA: sometime Tuesday"


def test_extraction_takes_no_format_argument():
    """Format-blindness is enforced by the signature, not by convention.

    A format parameter here would be an invitation to branch on it, which is
    how the electronic feed stops being another reader and becomes a second
    pipeline.
    """
    import inspect

    from maritime_isr.ingest.pans.extract import extract_notification

    params = inspect.signature(extract_notification).parameters
    assert list(params) == ["passages"]


# ==========================================================================
# 2b. the wild corpus — forms from agencies the generator has never seen
#
# Everything above this line is measured on documents the generator wrote, and
# the generator writes against the *same* `LABEL_SYNONYMS` table the extractor
# reads. That makes any accuracy figure from the corpus circular: a synonym
# added to the shared table is one the generator immediately starts producing,
# so the reader is never tested on a label it was not told about.
#
# `tests/pans_wild.py` is the other half. Labels, notations and structural
# traps the generator cannot see, scored the same way every time, so a widening
# can be shown to have widened something. **On the synthetic suite** — a harder
# synthetic than the corpus, but still not one real agency's mail.
# ==========================================================================

def _wild_score():
    from maritime_isr.ingest.pans.extract import extract_notification
    from maritime_isr.ingest.pans.readers import Passage

    from tests.pans_wild import score_documents

    return score_documents(extract_notification, Passage)


def test_the_wild_fixtures_are_actually_wild():
    """The harness is only worth anything while the generator cannot see it.

    If these labels drift into `scenario.pans.LABEL_SYNONYMS`, the corpus starts
    writing them and the score goes back to measuring the extractor against its
    own vocabulary. So the fixture set is checked for the property that makes it
    a test: most of what it says is not in the table the generator holds.
    """
    from maritime_isr.ingest.pans.extract import _clean_label, squash
    from maritime_isr.scenario.pans import LABEL_SYNONYMS

    from tests.pans_wild import WILD_DOCUMENTS

    known = {squash(s) for syns in LABEL_SYNONYMS.values() for s in syns}
    labels = set()
    for doc in WILD_DOCUMENTS:
        for line in doc.passages:
            head = line.split(":")[0]
            s = squash(_clean_label(head))
            if s:
                labels.add(s)
    unknown = labels - known
    assert len(unknown) >= 30, (
        f"only {len(unknown)} label(s) here are unknown to the generator; the "
        f"harness has stopped being a test of anything it was not told")


def test_extraction_reads_forms_written_by_agencies_it_has_never_seen():
    """The number this asserts is the point of the whole widening.

    Measured 55/110 before it (50.0%, two misattributions) and 109/110 after,
    **on the synthetic suite**. The floor is set below the measured figure so a
    small honest regression is visible as a number rather than as a red test on
    an unrelated change — but a *misattribution* has no floor at all: see the
    next test.
    """
    r = _wild_score()
    assert r["expected"] >= 100
    assert r["accuracy"] >= 0.95, (
        f"{r['correct']}/{r['expected']} — " + "; ".join(r["failures"]))


def test_no_widening_ever_puts_a_value_on_the_wrong_field():
    """Zero, and it stays zero.

    A field that cannot be read costs a "not checkable" downstream, which is an
    honest answer an analyst can act on. A field read *wrong* costs a
    contradiction nobody wrote — the paperwork rules will compare that value
    against a track and report a vessel for it. Every label added to the
    extractor's vocabulary is a chance to create one, so the wild set asserts
    the count rather than the rate.
    """
    r = _wild_score()
    assert r["misattributed"] == 0, "; ".join(r["misattributions"])


def test_a_label_naming_a_field_we_do_not_model_is_refused():
    """Recognised in order to be refused.

    Every label here contains a word that names a field we *do* extract, and
    without the refusal list each one is a confident wrong answer: a berthing
    draught read as a last port, an office address read as an agent, a ship
    type read as a ship name. The refusal costs a value; the alternative costs
    an alert.
    """
    from maritime_isr.ingest.pans.extract import _label_of

    for label in ("Next Port", "Port of Registry", "Port of Loading",
                  "Vessel Type", "Draught on Arrival", "Agent Address",
                  "Owner's Address", "Date of Departure from Last Port",
                  "Master's Name", "Gross Tonnage", "Cargo Quantity",
                  "Number of Passengers", "Expected Time of Departure",
                  "Crew Nationality"):
        assert _label_of(label) is None, label

    # And a substring of a word is not a label: PORT lives inside TRANSPORT.
    assert _label_of("Transport Document No.") is None


def test_the_refusal_list_and_the_field_list_never_name_the_same_label():
    """The two tables are checked against each other for the same reason the
    fold is checked against itself: a label in both would resolve by whichever
    lookup ran first, and which one that is would be an implementation
    detail deciding whether a value lands on a field or nowhere."""
    from maritime_isr.ingest.pans.extract import _EXACT, _REFUSED

    assert not (set(_EXACT) & _REFUSED)


def test_dates_in_notations_the_generator_never_writes():
    """Eighteen notations, none of them produced by `scenario.pans.eta_text`.

    Read 3 of 18 before this widening and 18 of 18 after, **on the synthetic
    suite**. Day-first throughout, including the two at the end that are only
    unambiguous if you already know the convention.
    """
    from datetime import timezone

    from maritime_isr.ingest.pans.extract import parse_eta

    from tests.pans_wild import WILD_DATES

    for text, want in WILD_DATES:
        got = parse_eta(text)
        assert got is not None, text
        assert got.astimezone(timezone.utc).isoformat() == want, text


def test_an_explicit_absence_is_a_third_answer_and_not_a_value():
    """"The agent wrote NIL" and "the box was never filled in" differ.

    Both leave the rules with nothing to compare, so both end in "not
    checkable" — but only one of them is a thing the form *said*, and an
    operator triaging an inbox needs to see which. So an absence lands as a
    passage with a null value, and never as the string "NIL".

    Reading it as a value would be worse than losing it: "Cargo: NIL" turned
    into a cargo declaration meets `check_declared_ballast`, which would then
    report a contradiction against any laden draught on the strength of an
    agent's shorthand for an empty box.
    """
    from maritime_isr.ingest.pans.extract import (ABSENT_METHOD,
                                                  extract_notification)
    from maritime_isr.ingest.pans.readers import Passage

    from tests.pans_wild import WILD_ABSENCES

    for token in WILD_ABSENCES:
        fields = extract_notification(
            [Passage(f"Cargo: {token}", "page 1", 0.97, "pdf_text")])
        f = fields["cargo"]
        assert f.value is None, token
        assert f.raw == token and f.passage.endswith(token)
        assert f.method.endswith(ABSENT_METHOD)
        # The passage was read perfectly; it is the *answer* that is empty.
        assert f.confidence == 0.97

    # A real ballast declaration is untouched — the rule downstream reads this
    # phrase, and an absence table that swallowed it would silence a check.
    fields = extract_notification(
        [Passage("Cargo: Ballast — no cargo", "page 1", 0.97, "pdf_text")])
    assert fields["cargo"].value == "Ballast — no cargo"


def test_a_declared_absence_is_not_counted_as_a_field_read(tmp_path,
                                                           monkeypatch):
    """`fields_read` gates two alerts, so it has to mean values.

    A document that is a column of dashes has been read and says nothing. If
    its absences counted, it would present as a form full of answers that
    resolve to no hull — which is the `notification_unmatched` alert, raised
    about a document nobody could learn anything from.
    """
    from maritime_isr.ingest.pans import land as land_mod
    from maritime_isr.ingest.pans.readers import Passage

    passages = [Passage("Vessel Name: SEA LEOPARD", "page 1", 1.0, "xlsx_cell"),
                Passage("Crew: NIL", "page 1", 1.0, "xlsx_cell"),
                Passage("Cargo: --", "page 1", 1.0, "xlsx_cell")]
    path = tmp_path / "PANS-X.xlsx"
    path.write_bytes(b"")
    monkeypatch.setattr(land_mod, "read_document", lambda p: passages)
    row = land_mod._row_for(path, [], is_synthetic=True)

    assert row["fields_read"] == 1
    assert row["fields_declared_absent"] == 2
    assert row["crew_count"] is None
    # The passage still lands, because "the agent wrote NIL" is evidence.
    assert row["crew_count_passage"] == "Crew: NIL"
    assert row["crew_count_method"].endswith("declared_absent")


def test_first_reading_wins_but_an_absence_is_upgraded_by_a_real_answer():
    """First-wins is still right, with one exception that is not a hedge.

    Forms repeat labels, and the later mention is usually the worse one —
    "ATLANTIC PIONEER (Berth 4)" in a sign-off block is not a better vessel
    name than the header's. But a *dash* in a header block is not a reading at
    all, and letting it permanently blank a field the form answers further down
    would be first-wins producing an answer worse than either line alone.
    """
    from maritime_isr.ingest.pans.extract import extract_notification
    from maritime_isr.ingest.pans.readers import Passage

    def read(*lines):
        return extract_notification(
            [Passage(t, "page 1", 0.97, "pdf_text") for t in lines])

    fields = read("Vessel: ATLANTIC PIONEER",
                  "Vessel: ATLANTIC PIONEER (Berth 4)")
    assert fields["vessel_name"].value == "ATLANTIC PIONEER"

    fields = read("Call Sign: --", "Radio Call Sign: VTAB4")
    assert fields["call_sign"].value == "VTAB4"

    # …and a real value is never downgraded to an absence by a later dash.
    fields = read("Call Sign: VTAB4", "Call Sign: --")
    assert fields["call_sign"].value == "VTAB4"


def test_a_two_column_row_is_two_fields_and_not_one_long_value():
    """The failure this prevents is silent, which is why it is worth code.

    Split at the first separator, "Vessel Name: NORTH STAR   Call Sign: 3EAB7"
    yields a vessel name of "NORTH STAR Call Sign: 3EAB7" — a value that
    matches no hull, on a field that looks like it was read fine.
    """
    from maritime_isr.ingest.pans.extract import extract_notification
    from maritime_isr.ingest.pans.readers import Passage

    fields = extract_notification([Passage(
        "Ship's Name: NORTH STAR        Radio Callsign: 3EAB7",
        "page 1", 0.97, "pdf_text")])
    assert fields["vessel_name"].value == "NORTH STAR"
    assert fields["call_sign"].value == "3EAB7"

    # The right-hand pair is often a field we do not model. It still has to be
    # found, or the left-hand value swallows it.
    fields = extract_notification([Passage(
        "Flag State: PANAMA          Gross Tonnage: 48,120",
        "page 1", 0.97, "pdf_text")])
    assert fields["flag"].value == "PANAMA"
    assert set(fields) == {"flag"}

    # A colon inside a value is not a column boundary, because the words in
    # front of it do not name a field.
    fields = extract_notification([Passage(
        "Cargo Description: Crude oil: 80,000 MT", "page 1", 0.97, "pdf_text")])
    assert fields["cargo"].value == "Crude oil: 80,000 MT"


def test_an_empty_box_is_not_filled_from_the_label_that_follows_it():
    """A Word table with a blank cell reads as "Crew: Owner", and taking that
    literally puts a company name in the crew count and loses the owner."""
    from maritime_isr.ingest.pans.extract import extract_notification
    from maritime_isr.ingest.pans.readers import Passage

    fields = extract_notification([
        Passage("Crew: Owner: BLUEWATER SHIPPING LTD", "t 1 r 1", 1.0,
                "docx_table"),
        Passage("Crew: Owner", "t 1 r 2", 1.0, "docx_table")])
    assert fields["owner"].value == "BLUEWATER SHIPPING LTD"
    assert "crew_count" not in fields


def test_a_label_broken_across_an_ocr_line_break_is_rejoined():
    """A scanner wraps "Last Port of Call" and the halves resolve to nothing.

    The rejoin is deliberately narrow: only when the line's own label fails,
    and only when the joined text matches a label *exactly*. A containment
    match on a joined heading would read a title block as a field, which is the
    misattribution this file spends most of its length avoiding.
    """
    from maritime_isr.ingest.pans.extract import extract_notification
    from maritime_isr.ingest.pans.readers import Passage

    fields = extract_notification([
        Passage("Last Port of", "page 1 (scanned)", 0.58, "ocr"),
        Passage("Call: Karachi", "page 1 (scanned)", 0.58, "ocr")])
    assert fields["last_port"].value == "Karachi"

    fields = extract_notification([
        Passage("VESSEL PARTICULARS", "page 1", 0.97, "pdf_text"),
        Passage("Type: Bulk Carrier", "page 1", 0.97, "pdf_text")])
    assert fields == {}


def test_a_column_layout_that_lost_its_colons_is_still_read():
    """A PDF text layer frequently drops the punctuation a form was printed
    with. The head is accepted only on an exact label match: with no separator
    there is nothing else telling a label from the first two words of a
    sentence."""
    from maritime_isr.ingest.pans.extract import extract_notification
    from maritime_isr.ingest.pans.readers import Passage

    fields = extract_notification([
        Passage("Vessel Name        GULF SENTINEL", "page 1", 0.97, "pdf_text"),
        Passage("ETA                06 Jul 2026 0930", "page 1", 0.97,
                "pdf_text"),
        Passage("PRE-ARRIVAL NOTIFICATION    OF SHIPS", "page 1", 0.97,
                "pdf_text")])
    assert fields["vessel_name"].value == "GULF SENTINEL"
    assert fields["eta"].value.startswith("2026-07-06T09:30")
    assert set(fields) == {"vessel_name", "eta"}


def test_imo_prefers_the_anchored_then_the_check_digit_valid_reading():
    """Three readings, in the order of what they are worth.

    A form carries official numbers and telephone numbers seven digits wide, so
    the anchor to the literal token IMO comes first. The check digit — the same
    arithmetic `sanctions_match.imo_checksum_ok` applies, reused rather than
    rewritten — only ever *chooses between* candidates. It never rejects the
    only one there is, because a hull whose paperwork carries a broken check
    digit is a finding elsewhere in this system and not a value to discard.
    """
    from maritime_isr.ingest.pans.extract import parse_imo

    assert parse_imo("IMO No. 1000007") == "1000007"
    assert parse_imo("IMO1000007") == "1000007"
    # Two candidates, one anchored.
    assert parse_imo("Official No. 1234560 / IMO 9074729") == "9074729"
    # Two candidates, neither anchored: the check digit decides.
    assert parse_imo("1234560 9074729") == "9074729"
    # One candidate with a broken check digit is still the answer — the
    # checksum chooses, it does not reject.
    assert parse_imo("1234560") == "1234560"
    # What a scanner does to a number, put back — and only accepted because the
    # check digit validates, which is the independent evidence the repair
    # needs.
    assert parse_imo("1OOOOO7") == "1000007"
    assert parse_imo("no number here") is None


def test_call_sign_normalisation_is_lossless_and_refuses_the_rest():
    """The resolver matches call signs by exact equality, deliberately — a
    fuzzy identifier match puts a notification on the wrong hull. So the
    lossless part of the cleanup belongs here, where it can be seen."""
    from maritime_isr.ingest.pans.extract import parse_call_sign

    assert parse_call_sign("A B C 1") == "ABC1"
    assert parse_call_sign("9V-AB-2") == "9VAB2"
    assert parse_call_sign("VTAB4 (VHF 16)") == "VTAB4"
    assert parse_call_sign("9HA4271") == "9HA4271"
    # Not a call sign: kept as raw text by the caller rather than cleaned into
    # something that looks like one.
    assert parse_call_sign("see attached crew list") is None
    assert parse_call_sign("1234567890") is None


def test_crew_count_is_bounded_and_reads_a_number_written_out():
    from maritime_isr.ingest.pans.extract import parse_crew

    assert parse_crew("22") == "22"
    assert parse_crew("22 persons") == "22"
    assert parse_crew("22 (incl. master)") == "22"
    assert parse_crew("Twenty Two") == "22"
    assert parse_crew("eighteen") == "18"
    # Three digits, and the bound is doing work: an unbounded grab reads the
    # first digits of a tonnage or a phone number as a plausible crew.
    assert parse_crew("48120") is None
    assert parse_crew("see crew list attached") is None


def test_confidence_stays_earned_after_every_widening():
    """A widening that read more fields by trusting them more would be a
    regression dressed as an improvement. A spreadsheet cell is 1.0, an OCR'd
    smudge is what tesseract said, and a value the parser could not read is
    halved whatever it arrived on."""
    from maritime_isr.ingest.pans.extract import extract_notification
    from maritime_isr.ingest.pans.readers import Passage

    sheet = extract_notification(
        [Passage("Vessel Name: SEA LEOPARD", "PANS!A5", 1.0, "xlsx_cell")])
    fax = extract_notification(
        [Passage("Vesse| Name: SEA LEOPARD", "page 1 (scanned)", 0.58, "ocr")])
    assert sheet["vessel_name"].confidence == 1.0
    assert fax["vessel_name"].confidence == 0.58
    assert fax["vessel_name"].method == "ocr"

    unreadable = extract_notification(
        [Passage("ETA: sometime Tuesday", "page 1 (scanned)", 0.58, "ocr")])
    assert unreadable["eta"].confidence == 0.29
    assert unreadable["eta"].method == "ocr_unparsed"


# ==========================================================================
# 3. resolution — attach a form to a hull, or refuse
# ==========================================================================

def test_resolution_ladder_prefers_the_stronger_identifier():
    from maritime_isr.ingest.pans.resolve import resolve_notification

    registry = [dict(vessel_id="vessel:a", imo="1000007", call_sign="ABC1",
                     ship_name="GRANITE TRIUMPH")]
    vid, how, conf = resolve_notification({"imo": _field("1000007")}, registry)
    assert (vid, how) == ("vessel:a", "imo") and conf == 0.95
    vid, how, conf = resolve_notification({"call_sign": _field("ABC1")},
                                          registry)
    assert (vid, how) == ("vessel:a", "call_sign") and conf == 0.8
    vid, how, conf = resolve_notification(
        {"vessel_name": _field("M.V. Granite Triumph")}, registry)
    assert (vid, how) == ("vessel:a", "name") and conf == 0.6


def test_a_name_two_hulls_answer_to_resolves_to_neither():
    """Picking either would be a coin flip dressed as an identification, and a
    notification on the wrong hull is a false accusation with paperwork behind
    it."""
    from maritime_isr.ingest.pans.resolve import resolve_notification

    registry = [dict(vessel_id="vessel:a", ship_name="SAGA"),
                dict(vessel_id="vessel:b", ship_name="SAGA")]
    vid, how, conf = resolve_notification({"vessel_name": _field("SAGA")},
                                          registry)
    assert vid is None and how == "name_ambiguous" and conf == 0.0


def test_transposition_stays_unresolved_and_no_space_does_not():
    """Normalisation undoes what is lossless; it does not guess.

    Removing spaces cannot make two different names collide. Edit distance
    would recover "GRANITE TRUIMPH" and would equally recover "GRANITE TRIUMPH
    II", a different ship.
    """
    from maritime_isr.ingest.pans.resolve import resolve_notification

    registry = [dict(vessel_id="vessel:a", ship_name="GRANITE TRIUMPH")]
    vid, _, _ = resolve_notification(
        {"vessel_name": _field("GRANITETRIUMPH")}, registry)
    assert vid == "vessel:a"
    vid, how, _ = resolve_notification(
        {"vessel_name": _field("GRANITE TRUIMPH")}, registry)
    assert vid is None and how is None


def test_registry_is_the_union_of_what_the_system_holds():
    """A hull's identity is not one table's opinion of her.

    `gfw_vessel_identity` is patchy by construction; the same hull may have
    broadcast her IMO in an AIS message 5 every six hours for a month. Reading
    only the registry made the resolver drop to the weakest rung and report
    "no IMO in the form matches a hull we hold" about a form whose IMO matched
    perfectly — a gap in one table reported as a gap in somebody's paperwork.
    That produced 24 unmatched-notification alerts where 1 was authored.
    """
    from maritime_isr.ingest.pans.resolve import (merge_identity_sources,
                                                  resolve_notification)

    registry = [dict(vessel_id="vessel:a", ship_name="SOUTHERN TRADER")]
    broadcast = [dict(vessel_id="vessel:a", imo="1001661")]

    # Registry alone: the IMO on the form matches nothing.
    vid, _, _ = resolve_notification({"imo": _field("1001661")}, registry)
    assert vid is None

    merged = merge_identity_sources(registry, broadcast)
    vid, how, conf = resolve_notification({"imo": _field("1001661")}, merged)
    assert (vid, how, conf) == ("vessel:a", "imo", 0.95)


def test_merge_fills_gaps_and_never_overrides():
    """Sources are passed most-trusted first, and a later one only fills what
    is still empty — so a broadcast identity cannot quietly rewrite a
    registered one."""
    from maritime_isr.ingest.pans.resolve import merge_identity_sources

    merged = merge_identity_sources(
        [dict(vessel_id="vessel:a", imo="1000001", ship_name="ALPHA")],
        [dict(vessel_id="vessel:a", imo="9999999", call_sign="ABC1")],
    )
    row = next(r for r in merged if r["vessel_id"] == "vessel:a")
    assert row["imo"] == "1000001"        # not overridden
    assert row["call_sign"] == "ABC1"     # gap filled
    assert row["ship_name"] == "ALPHA"


# ==========================================================================
# 4. the filing time — read from the form, never from the filesystem
# ==========================================================================

def test_filing_time_is_read_from_the_document():
    """The date on the form, not the attachment's mtime.

    A file's modification time is when somebody scanned, copied or forwarded
    it. Every rule in `anomaly.paperwork` measures a declaration against the
    track *as at the filing time*, and handed a scanning timestamp those rules
    do not fail loudly — they look before a window that has not happened yet,
    find nothing, and return "not checkable" for the whole inbox. That is
    exactly what happened: two of the three checks were dead corpus-wide while
    the stage reported 3,107 fields extracted and looked healthy.
    """
    from maritime_isr.ingest.pans.land import _received_at

    fields = {"filed_at": _field("2026-07-01T09:00:00+00:00")}
    when, how = _received_at(fields, __file__)
    assert how == "declared"
    assert when == datetime(2026, 7, 1, 9, tzinfo=UTC)


def test_filing_time_falls_back_to_mtime_and_says_so():
    """A form with no date is a real thing and an mtime beats nothing. It is
    *labelled*, because a value inferred from the filesystem and one read off
    the page are not the same evidence."""
    from pathlib import Path

    from maritime_isr.ingest.pans.land import _received_at

    when, how = _received_at({}, Path(__file__))
    assert how == "file_mtime"
    assert when.tzinfo is not None


def test_filed_at_is_an_extracted_field_like_any_other():
    from maritime_isr.ingest.pans.extract import extract_notification
    from maritime_isr.ingest.pans.land import FIELD_NAMES
    from maritime_isr.ingest.pans.readers import Passage
    from maritime_isr.scenario.pans import FIELD_ORDERS, LABEL_SYNONYMS

    assert "filed_at" in FIELD_NAMES
    assert "filed_at" in LABEL_SYNONYMS
    # Every form layout carries it, or a document could be written without one.
    assert all("filed_at" in order for order in FIELD_ORDERS)

    fields = extract_notification([
        Passage("Date of Filing: 01/07/2026 09:00", "page 1", 0.97,
                "pdf_text")])
    assert fields["filed_at"].value.startswith("2026-07-01T09:00")
    assert fields["filed_at"].locator == "page 1"


# ==========================================================================
# 5. the rules — three-valued, and "we could not check" is an answer
# ==========================================================================

def test_last_port_contradiction_and_its_two_not_checkable_forms():
    from maritime_isr.anomaly.paperwork import check_last_port

    # She declares Kandla; her track is in the Bay of Bengal.
    far = [(T0.timestamp() - 3600 * i, 13.0, 85.0) for i in range(6)]
    f = check_last_port(declared={"last_port": _field("Kandla")},
                        fixes=far, filed_at=T0)
    assert f.outcome == "contradiction"
    assert f.passage and f.locator      # evidence traces to the line
    assert 0.5 <= f.confidence <= 0.9

    # A port the gazetteer does not hold is not a lie.
    f = check_last_port(declared={"last_port": _field("Nowhere-on-Sea")},
                        fixes=far, filed_at=T0)
    assert f.outcome == "not_checkable"

    # Too little track to say where she had been.
    f = check_last_port(declared={"last_port": _field("Kandla")},
                        fixes=far[:1], filed_at=T0)
    assert f.outcome == "not_checkable"


def test_last_port_is_judged_against_where_she_actually_berthed():
    """The gazetteer holds one pin for a port area tens of km across.

    Measuring a declaration against that pin accused a hull of lying about
    Karachi when her recorded call *was* Karachi, 144 km from the coordinate.
    When we know where she last berthed, that is what "my last port" refers to.
    """
    from maritime_isr.anomaly.paperwork import check_last_port
    from maritime_isr.ports import PORTS

    lat, lon = PORTS["Kandla"]
    far = [(T0.timestamp() - 3600 * i, 13.0, 85.0) for i in range(6)]

    # Her recorded call sits near Kandla even though the track window does not.
    f = check_last_port(declared={"last_port": _field("Kandla")}, fixes=far,
                        filed_at=T0, prior_calls=[(lat + 0.4, lon + 0.4)])
    assert f.outcome == "ok"

    # **Did she call there at all — not "was her most recent call there".** A
    # vessel that sailed from Kandla and then made an unnamed offshore stop has
    # still sailed from Kandla.
    f = check_last_port(declared={"last_port": _field("Kandla")}, fixes=far,
                        filed_at=T0,
                        prior_calls=[(lat + 0.4, lon + 0.4), (13.0, 85.0)])
    assert f.outcome == "ok"

    # No recorded call comes near the port she names.
    f = check_last_port(declared={"last_port": _field("Kandla")}, fixes=far,
                        filed_at=T0, prior_calls=[(13.0, 85.0)])
    assert f.outcome == "contradiction"


def test_a_notification_is_matched_to_the_arrival_it_is_about():
    """The next arrival in time is not the arrival the form is about.

    A notification is filed 24-96 hours ahead and a coastal vessel frequently
    makes another call inside that window. Comparing a declared ETA against
    whichever stop came first afterwards measured one voyage's estimate against
    a different voyage's berthing — thirty honest hulls, every one reported as
    arriving 31-65 hours early, which is the shape of a join error rather than
    of deceit.
    """
    from maritime_isr.anomaly.paperwork import match_arrival

    declared = {"arrival_port": _field("Kandla")}
    intervening = T0 + timedelta(hours=20)
    real = T0 + timedelta(hours=60)
    arrivals = [(intervening, "Mundra"), (real, "Kandla")]

    assert match_arrival(declared, arrivals, T0) == real
    # A call at the declared port *before* filing is a previous voyage.
    assert match_arrival(declared, [(T0 - timedelta(hours=5), "Kandla")],
                         T0) is None
    # A port she never reaches has no arrival to be measured against, and a
    # diverted voyage is not a false declaration about its ETA.
    assert match_arrival(declared, [(real, "Mundra")], T0) is None
    assert match_arrival({"arrival_port": _field("Nowhere")}, arrivals,
                         T0) is None


def test_a_vessel_that_has_not_arrived_is_not_late():
    """The claim on a form is about the future until the arrival happens."""
    from maritime_isr.anomaly.paperwork import check_arrival_window

    f = check_arrival_window(
        declared={"eta": _field((T0 + timedelta(days=2)).isoformat())},
        observed_arrival=None)
    assert f.outcome == "not_checkable"


def test_an_estimate_six_hours_out_is_an_estimate():
    """P5's decoy, pinned. A rule that fires here fires on the whole fleet."""
    from maritime_isr.anomaly.paperwork import (ARRIVAL_SLIP_HOURS,
                                                check_arrival_window)

    eta = T0 + timedelta(days=2)
    f = check_arrival_window(declared={"eta": _field(eta.isoformat())},
                             observed_arrival=eta + timedelta(hours=6))
    assert f.outcome == "ok"
    assert ARRIVAL_SLIP_HOURS >= 24.0

    f = check_arrival_window(declared={"eta": _field(eta.isoformat())},
                             observed_arrival=eta + timedelta(hours=40))
    assert f.outcome == "contradiction"


def test_declared_ballast_against_draught_is_three_valued():
    from maritime_isr.anomaly.paperwork import check_declared_ballast

    f = check_declared_ballast(declared={"cargo": _field("Ballast — no cargo")},
                               draught_m=16.5)
    assert f.outcome == "contradiction"

    f = check_declared_ballast(declared={"cargo": _field("Ballast — no cargo")},
                               draught_m=8.0)
    assert f.outcome == "ok"

    # A named commodity is deliberately NOT checked: separating "cement and
    # riding high" from "lying" needs a cargo model this system does not have,
    # and guessing one would fire on honest voyages.
    f = check_declared_ballast(declared={"cargo": _field("Bulk cement")},
                               draught_m=16.5)
    assert f.outcome == "not_checkable"

    f = check_declared_ballast(declared={"cargo": _field("Ballast — no cargo")},
                               draught_m=None)
    assert f.outcome == "not_checkable"


def test_corpus_and_rule_agree_on_what_laden_means():
    """The generator holds these constants independently, so that a corpus
    built from the rule's own thresholds cannot be mistaken for evidence about
    the rule. Independence is only safe if drift is caught."""
    from maritime_isr.anomaly import paperwork
    from maritime_isr.scenario.scenarios import group_p

    assert group_p.LADEN_DRAUGHT_M == paperwork.LADEN_DRAUGHT_M
    assert set(group_p.BALLAST_PHRASES) == set(paperwork.BALLAST_PHRASES)


def test_background_corpus_never_declares_ballast_against_a_laden_draught():
    """A contradiction between paperwork and track is the product here, so it
    is authored as a scenario and excluded from the background.

    Drawn uniformly, one form in eleven declared "no cargo" and landed on
    whatever hull came next — manufacturing eight contradictions nobody wrote
    and no analyst could be told anything true about.
    """
    from maritime_isr.scenario.scenarios.group_p import (BALLAST_PHRASES,
                                                         LADEN_DRAUGHT_M,
                                                         _honest_cargoes)

    class _Laden:
        draught_m = LADEN_DRAUGHT_M + 4.0

    class _Light:
        draught_m = 6.0

    for cargo in _honest_cargoes(_Laden()):
        assert not any(p in cargo.upper() for p in BALLAST_PHRASES)
    assert any(any(p in c.upper() for p in BALLAST_PHRASES)
               for c in _honest_cargoes(_Light()))


# ==========================================================================
# 6. the arrival gap — only where a duty was owed and we could see it
# ==========================================================================

def _store():
    from maritime_isr.graph.store import GraphStore
    store = GraphStore(":memory:")
    from maritime_isr.anomaly.library import vessel_node_id
    store.upsert_node(vessel_node_id("vessel:a"), "vessel",
                      props=dict(mmsi="419000001"), is_synthetic=True)
    return store


def test_arrival_before_the_record_begins_is_not_a_finding():
    """A form for a vessel berthing on day two was due on day minus two.

    "Nobody filed for her" is a claim about a window we cannot see. Six of the
    first ten alerts this rule produced were vessels that berthed in the
    opening seventy-two hours of the corpus.
    """
    import pandas as pd

    from maritime_isr.anomaly.library import detect_arrival_without_notification

    start = pd.Timestamp("2026-06-04", tz="UTC")
    early = dict(vessel_id="vessel:a", port_name="Mundra",
                 start_time=start + pd.Timedelta(hours=12))
    late = dict(vessel_id="vessel:a", port_name="Mundra",
                start_time=start + pd.Timedelta(days=20))

    fired = detect_arrival_without_notification(
        _store(), [dict(vessel_id="vessel:b")], [early],
        source_ref="t", observed_from=start)
    assert fired == []

    fired = detect_arrival_without_notification(
        _store(), [dict(vessel_id="vessel:b")], [late],
        source_ref="t", observed_from=start)
    assert len(fired) == 1


def test_a_stop_with_no_named_port_owes_no_notification():
    """Pre-arrival notification is a duty owed on arrival at a port. An
    offshore anchorage the port-call detector recorded without a name is not
    one, and reporting it demands paperwork nobody owed."""
    import pandas as pd

    from maritime_isr.anomaly.library import detect_arrival_without_notification

    start = pd.Timestamp("2026-06-04", tz="UTC")
    unnamed = dict(vessel_id="vessel:a", port_name=None,
                   start_time=start + pd.Timedelta(days=20))
    fired = detect_arrival_without_notification(
        _store(), [dict(vessel_id="vessel:b")], [unnamed],
        source_ref="t", observed_from=start)
    assert fired == []


# ==========================================================================
# 7. the discipline the whole area rests on
# ==========================================================================

def test_no_pans_module_reads_the_answer_key():
    """ADR-019: no extractor, resolver or rule may read `scenario_truth`.

    The documents are generated from the corpus; nothing in one names a
    scenario, and nothing that reads one is allowed to consult the truth table.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "maritime_isr"
    for path in list((root / "ingest" / "pans").glob("*.py")) + [
            root / "anomaly" / "paperwork.py"]:
        text = path.read_text(encoding="utf-8")
        assert "scenario_truth" not in text, path
        assert "radar_dark_truth" not in text, path
