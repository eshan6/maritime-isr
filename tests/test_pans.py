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
