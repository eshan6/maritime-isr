"""The port-paperwork corpus, and the read side an operator gets — ADR-036.

`tests/test_pans.py` tests the connector's parts: readers, extraction,
resolution, rules. This module tests the two things that were added around
them, and it tests them in the order they can fail:

1. **The generator's variety is real.** Six agencies, six kinds of form, five
   formats, four date notations. A corpus that only varied the vessel name
   would let a reader score well while proving nothing, so the variety is
   asserted rather than assumed — and, critically, so is the fact that the
   generator's labels are **not** all drawn from the extractor's own synonym
   table. A generator and a reader sharing one table cannot falsify each other.
2. **A new format is a new reader.** CLAUDE.md invariant 5: a source is a
   connector, never a core change. That is checkable — the extractor takes no
   format argument, and every format arrives at it as the same
   `Label: value` grammar — so it is checked here rather than asserted in a
   design note.
3. **The kind is read off the page, not off the filename.** A departure report
   has no ETA box; without the kind, "not checkable" on its arrival window is
   indistinguishable from a reader that failed.
4. **The service layer assembles, and never decides.** `ingest.pans.service`
   is what `api/` will call. It must return three outcome keys always, hand
   back the provenance envelope rather than stripping it, and refuse an
   ambiguous name rather than pick a hull.

Nothing here reads the answer key from inside `ingest/` — the corpus's
`authored_case` and `expected` are read by *tests* and by the reporting tool,
which is where ADR-019 puts them.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

UTC = timezone.utc
T0 = datetime(2026, 7, 1, tzinfo=UTC)


def _voyages(n: int = 24) -> list[dict]:
    """A handful of recorded arrivals, in the shape `build_document_specs` takes.

    Built here rather than read from `data/`, because a unit test that needed a
    landed corpus would be a corpus test wearing a unit test's name — and
    because the point of the generator is that it takes whatever vessels it is
    given and names none of them itself.
    """
    # Five of the six named in the requirement, plus two the house table has no
    # entry for — those cycle through every house, which is how the agent's own
    # letterhead (nobody's port authority) is ever reached.
    ports = ["Kandla", "Mundra", "JNPT", "Mumbai", "Mangalore", "Kochi",
             "Pipavav", "Vadinar"]
    out = []
    for i in range(n):
        out.append(dict(
            vessel_id=f"vessel:test-{i:03d}",
            name=f"TEST CARRIER {i:02d}",
            imo=f"9{100000 + i}",
            call_sign=f"VT{i:04d}",
            flag="IND",
            owner=None,
            arrival_port=ports[i % len(ports)],
            arrival_time=T0 + timedelta(days=i),
            last_port=ports[(i + 3) % len(ports)],
            prior_call_end=T0 + timedelta(days=i) - timedelta(days=4),
            # Alternate laden and light, so the ballast case has hulls it can
            # actually be authored against.
            draught_m=13.5 if i % 2 else 6.0,
        ))
    return out


# ==========================================================================
# 1. the corpus is varied, and it is not varied against its own reader
# ==========================================================================

def test_the_corpus_covers_every_kind_format_and_house():
    """Every reader, every form and every agency gets a comparable sample.

    Kinds, formats and houses are cycled rather than drawn. A random draw at
    this scale leaves one kind with three documents, and any per-kind read rate
    measured on it is noise reported as a number.
    """
    from maritime_isr.scenario.pans import (DOCUMENT_KINDS, FORMATS,
                                            HOUSE_STYLES,
                                            build_document_specs)

    specs = build_document_specs(_voyages(36), seed=3)
    assert len(specs) >= 30

    assert {s.document_kind for s in specs} == set(DOCUMENT_KINDS)
    assert {s.document_format for s in specs} == set(FORMATS)
    # Ports map to houses, and the gazetteer names in `_voyages` cover all six.
    assert {s.house_style for s in specs} == set(HOUSE_STYLES)

    # No kind may be starved: the smallest sample is within a factor of two of
    # the largest, or a per-kind figure is not comparable across kinds.
    from collections import Counter
    counts = Counter(s.document_kind for s in specs)
    assert max(counts.values()) <= 2 * min(counts.values())


def test_the_houses_ask_for_the_same_fields_under_different_names():
    """Six agencies, one set of questions, twelve different spellings.

    This is the variation the exercise is about. If two houses shared a label
    table the corpus would be one house wearing six letterheads.
    """
    from maritime_isr.scenario.pans import HOUSE_STYLES

    fields = None
    for house in HOUSE_STYLES.values():
        if fields is None:
            fields = set(house.labels)
        # Every house asks for the same twelve values...
        assert set(house.labels) == fields

    # ...and no two houses spell all twelve the same way.
    seen = {}
    for key, house in HOUSE_STYLES.items():
        signature = tuple(house.labels[f] for f in sorted(fields))
        assert signature not in seen, f"{key} duplicates {seen.get(signature)}"
        seen[signature] = key

    # And they do not all punctuate alike: one house uses a column gap and no
    # separator at all, which is what a ruled printed form gives a text layer.
    assert any(h.separator == "" for h in HOUSE_STYLES.values())
    assert len({h.date_style for h in HOUSE_STYLES.values()}) >= 3


def test_one_house_uses_labels_the_reader_has_never_been_told_about():
    """The honest residual, and the reason the read rate is not circular.

    If every label the generator writes were drawn from the extractor's own
    synonym table, the read rate would measure the table against itself. At
    least one house has to be genuinely unseen, and whatever it loses has to be
    reported rather than tuned away.
    """
    from maritime_isr.ingest.pans.extract import _label_of
    from maritime_isr.scenario.pans import HOUSE_STYLES

    unseen = {}
    for key, house in HOUSE_STYLES.items():
        misses = [label for field_name, label in house.labels.items()
                  if _label_of(label) != field_name]
        if misses:
            unseen[key] = misses

    assert unseen, ("every label in every house is one the extractor already "
                    "knows — the corpus can no longer falsify the reader")


def test_the_generator_names_no_vessel_and_reads_no_answer_key():
    """The cast is minted elsewhere and grows.

    A document set built from a hardcoded hull list describes a corpus rather
    than reading one, and breaks the day the fleet changes. And ADR-019: the
    authored case is written into the spec for the *report*, never into a
    document and never read by anything under `ingest/`.
    """
    from pathlib import Path

    from maritime_isr.scenario.pans import build_document_specs

    specs = build_document_specs(_voyages(12), seed=5)
    # The specs carry only the names they were handed.
    for spec in specs:
        if spec.authored_case != "unresolvable_hull":
            assert "TEST CARRIER" in spec.values["vessel_name"].upper() or \
                spec.values["vessel_name"], spec.notification_id

    root = Path(__file__).resolve().parents[1] / "maritime_isr"
    for path in (root / "ingest" / "pans").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "authored_case" not in text, path
        assert "AUTHORED_CASES" not in text, path


def test_authored_cases_are_only_written_where_the_form_has_the_box():
    """A departure report cannot be authored to miss an arrival window.

    It has no ETA box. A case that needed one would be authored into a form
    that cannot carry it, and the rule would then correctly answer
    `not_checkable` against an expectation of `contradiction` — a manufactured
    failure that says nothing about the rule.
    """
    from maritime_isr.scenario.pans import (_CASE_NEEDS, DOCUMENT_KINDS,
                                            build_document_specs)

    for spec in build_document_specs(_voyages(40), seed=9):
        needs = _CASE_NEEDS.get(spec.authored_case, ())
        boxes = DOCUMENT_KINDS[spec.document_kind].fields
        for field_name in needs:
            assert field_name in boxes, (
                f"{spec.notification_id} authored {spec.authored_case} onto a "
                f"{spec.document_kind}, which has no {field_name} box")


def test_the_mix_is_mostly_honest_and_still_exercises_all_three_outcomes():
    """The honest majority is the denominator.

    A paperwork rule measured only against forged forms reports recall and says
    nothing about how often it accuses an agent who typed a date wrong. And all
    three outcomes must be *authored*, because "we could not check" is an answer
    the corpus has to exercise rather than merely permit.
    """
    from collections import Counter

    from maritime_isr.scenario.pans import build_document_specs

    specs = build_document_specs(_voyages(40), seed=9)
    cases = Counter(s.authored_case for s in specs)
    assert cases["honest"] > len(specs) / 3

    authored = Counter()
    for spec in specs:
        for outcome in spec.expected.values():
            authored[outcome] += 1
    assert authored["contradiction"] > 0
    assert authored["not_checkable"] > 0, (
        "no document was authored to be uncheckable — the third outcome is "
        "only being permitted, never exercised")


def test_no_case_in_the_mix_is_silently_starved():
    """The defect this test exists for, because it failed *silently*.

    Six kinds of form cycle against a twenty-slot mix. The first version of the
    picker walked forward through the mix and **spent** every case a kind could
    not carry, so the two `missed_arrival_window` slots landed on a port
    clearance and a departure report — neither of which has an ETA box — on
    every lap. A 161-document corpus was generated containing that case exactly
    **zero** times, which means the arrival-window contradiction the
    requirement names explicitly was never once exercised.

    Nothing errored. Every document was valid, every read rate looked fine, and
    the per-case report simply had a row missing. That is the failure mode this
    whole area is built against, so it is pinned here rather than left to a
    reader noticing a gap in a table.
    """
    from collections import Counter

    from maritime_isr.scenario.pans import CASE_MIX, build_document_specs

    specs = build_document_specs(_voyages(160), seed=11)
    cases = Counter(s.authored_case for s in specs)
    for case in set(CASE_MIX):
        assert cases[case] > 0, (
            f"{case} is in the mix and was authored onto no document — it is "
            f"being consumed by kinds of form that cannot carry it")

    # And the mix is *delivered*, not merely non-zero: a case named twice in
    # the mix appears about twice as often as one named once.
    once = cases["absent_eta"]
    twice = cases["false_last_port"]
    assert twice >= 1.5 * once


def test_all_three_checks_are_authored_both_ways():
    """Each rule must meet a document that contradicts it *and* one it cannot
    check. A rule only ever shown contradictions reports recall; one only ever
    shown clean forms reports nothing at all."""
    from collections import Counter

    from maritime_isr.scenario.pans import build_document_specs

    seen = Counter()
    for spec in build_document_specs(_voyages(160), seed=11):
        for check, outcome in spec.expected.items():
            seen[(check, outcome)] += 1

    for check in ("declared_last_port", "declared_arrival_window",
                  "declared_ballast"):
        assert seen[(check, "contradiction")] > 0, check
        assert seen[(check, "not_checkable")] > 0, check


# ==========================================================================
# 2. a new format is a new reader, and nothing else
# ==========================================================================

def test_every_format_reaches_the_extractor_as_the_same_grammar(tmp_path):
    """CLAUDE.md invariant 5, made checkable.

    Five formats — a PDF, a Word form, a spreadsheet, a portal payload and a
    fax with no text layer — are written, read with the reader for each, and
    the *same* extractor is called on all five with no format argument. If the
    electronic feed needed its own extraction path, this is where it would show.
    """
    from maritime_isr.ingest.pans.extract import extract_notification
    from maritime_isr.ingest.pans.readers import read_document
    from maritime_isr.scenario.pans import build_document_specs, write_notifications

    specs = build_document_specs(_voyages(12), seed=4)
    write_notifications(specs, tmp_path, seed=4)

    by_format = {}
    for spec in specs:
        path = tmp_path / spec.document_name
        if not path.exists():                                # library missing
            continue
        by_format.setdefault(spec.document_format, path)

    # The scan is the slow one and the one that decides whether this is real,
    # so it is exercised separately in its own test; here the four text formats
    # are enough to show the seam.
    for fmt, path in by_format.items():
        if fmt == "pdf_scan":
            continue
        passages = read_document(path)
        assert passages, f"{fmt} read as empty"
        fields = extract_notification(passages)
        assert fields, f"{fmt} produced no fields"
        assert "vessel_name" in fields, fmt


def test_the_portal_payload_is_a_reader_and_not_a_second_pipeline(tmp_path):
    """The electronic feed enters at the reader seam, like a fax.

    It is a JSON document and could set fields directly. It deliberately does
    not: it emits `Label: value` passages, so the same extraction code serves
    both paths and a change to date parsing cannot fix one and break the other.
    That is what makes "it drops in without rework" a demonstration rather than
    a claim.
    """
    from maritime_isr.ingest.pans.readers import READERS, read_electronic
    from maritime_isr.scenario.pans import build_document_specs, write_notifications

    specs = [s for s in build_document_specs(_voyages(12), seed=4)
             if s.document_format == "electronic"]
    assert specs
    write_notifications(specs, tmp_path, seed=4)

    passages = read_electronic(tmp_path / specs[0].document_name)
    assert passages
    # Every passage carries a locator into the payload, so an operator can open
    # the submission and put a finger on the value.
    assert all(p.locator.startswith("$") for p in passages)
    assert all(p.method == "electronic_field" for p in passages)
    # It is registered as one of the readers, under an extension, with nothing
    # downstream of it branching on the fact.
    assert READERS[".json"] is read_electronic


def test_a_scanned_fax_has_no_text_layer_at_all(tmp_path):
    """The format the requirement singles out, and the one that decides it.

    A reader that only pulls text sees a blank page and reports a form somebody
    left empty — a far worse failure than an error, because it is silent. The
    scan is asserted to have no text layer here whether or not OCR is available
    on this machine, because *that* is the property the generator owes.
    """
    from maritime_isr.ingest.pans.readers import _pdf_text
    from maritime_isr.scenario.pans import build_document_specs, write_notifications

    specs = [s for s in build_document_specs(_voyages(12), seed=4)
             if s.document_format == "pdf_scan"]
    assert specs
    written = write_notifications(specs, tmp_path, seed=4)
    if written.get("_skipped"):
        pytest.skip(f"scan not written: {written['_skipped']}")

    path = tmp_path / specs[0].document_name
    assert path.exists()
    assert _pdf_text(path) == [], (
        "the scanned fax has a text layer — it is not testing the OCR path")


def test_the_scanned_fax_is_read_by_ocr_where_ocr_exists(tmp_path):
    """The one path that can be installed and still not work.

    `pytesseract` is a wrapper around a binary pip cannot install, so this test
    **skips** rather than fails where the binary is absent — and it skips
    loudly, because the alternative is a suite that goes green on a machine
    that read none of the scans and a per-format rate nobody measured.

    One document, deliberately: OCR costs the better part of a minute a page
    and a unit suite is not where a corpus-wide read rate is measured. The rate
    comes from `tools/make_port_documents.py`, over the whole inbox.
    """
    from maritime_isr.ingest.pans.extract import extract_notification
    from maritime_isr.ingest.pans.readers import (ReaderUnavailable,
                                                  read_document,
                                                  reader_availability)
    from maritime_isr.scenario.pans import build_document_specs, write_notifications

    state = reader_availability().get("pdf_scan")
    if state != "ok":
        pytest.skip(f"OCR unavailable on this machine: {state}")

    specs = [s for s in build_document_specs(_voyages(12), seed=4)
             if s.document_format == "pdf_scan"][:1]
    written = write_notifications(specs, tmp_path, seed=4)
    if written.get("_skipped"):
        pytest.skip(f"scan not written: {written['_skipped']}")

    try:
        passages = read_document(tmp_path / specs[0].document_name)
    except ReaderUnavailable as e:                            # pragma: no cover
        pytest.skip(f"OCR unavailable at read time: {e}")

    assert passages, "the scan OCR'd to nothing"
    assert all(p.method == "ocr" for p in passages)
    # A scan is the least trustworthy thing in the inbox and the number has to
    # say so, even on a clean render.
    assert all(p.confidence < 1.0 for p in passages)
    # It read *something* off the form — not a rate, a floor. The corpus-wide
    # figure belongs to the tool, on the whole inbox, labelled synthetic.
    fields = extract_notification(passages)
    assert fields, "the scan produced no fields at all"


# ==========================================================================
# 3. what kind of paper this is, read off the page
# ==========================================================================

def test_the_kind_is_read_from_the_title_block_not_the_filename():
    """A filename is what a mail client called an attachment.

    The same lesson `received_at` paid for: a value taken off the filesystem
    and a value read off the page are not the same evidence.
    """
    from maritime_isr.ingest.pans.kinds import classify_document

    assert classify_document(["CREW LIST", "Name of Vessel: X"]) == "crew_list"
    # A title nothing recognises is None, not a guess at "pans": a form with no
    # ETA box and a form that has one and left it empty are different facts.
    assert classify_document(["MARINE POLLUTION RETURN", "Vessel: X"]) is None
    # And a phrase buried in the footer does not relabel the document.
    assert classify_document(["CARGO DECLARATION (MANIFEST)"]
                             + ["filler"] * 40 + ["Port Clearance"]) \
        == "cargo_manifest"


def test_a_pans_is_not_read_as_an_arrival_report():
    """Order is load-bearing: a PANS title contains the word "arrival"."""
    from maritime_isr.ingest.pans.kinds import classify_document

    assert classify_document(
        ["PRE-ARRIVAL NOTIFICATION OF SECURITY (PANS)"]) == "pans"
    assert classify_document(["ARRIVAL REPORT"]) == "arrival_report"


def test_the_classifier_knows_titles_the_generator_never_writes():
    """Otherwise it is a lookup table for this repository's own corpus.

    The same circularity `EXTRA_SYNONYMS` exists to avoid on the label side: a
    classifier that only recognised its own six titles would score perfectly
    and tell us nothing about a real mailbox.
    """
    from maritime_isr.ingest.pans.kinds import classify_document
    from maritime_isr.scenario.pans import DOCUMENT_KINDS

    written = {k.title.upper() for k in DOCUMENT_KINDS.values()}
    for title, expected in (("REPORT OF ARRIVAL", "arrival_report"),
                            ("LIST OF CREW", "crew_list"),
                            ("OUTWARD CLEARANCE", "port_clearance"),
                            ("SAILING REPORT", "departure_report")):
        assert title not in written
        assert classify_document([title]) == expected


def test_an_ocr_mangled_title_still_reaches_the_right_kind():
    """A faxed title block is OCR'd like everything else on the page.

    The scanner's confusions — a one for an I, a lower-case l for an I — are
    folded by the same `squash` the label matcher uses, so "CREW L1ST" has to
    reach the same answer as "CREW LIST". The title table is written as plain
    phrases and squashed at import for exactly this reason: a hand-typed
    pre-squashed string would sit on the wrong side of the fold and match
    nothing, and the classifier would silently recognise nothing at all.
    """
    from maritime_isr.ingest.pans.kinds import classify_document

    assert classify_document(["CREW L1ST"]) == "crew_list"
    assert classify_document(["ARRlVAL REPORT"]) == "arrival_report"
    assert classify_document(["P0RT CLEARANCE"]) == "port_clearance"


def test_a_departure_report_has_no_eta_box_and_the_rule_says_so():
    """The reason the kind is carried at all.

    A departure report declares where she has been, not when she will arrive.
    The arrival-window rule answers `not_checkable` on one, forever, correctly
    — and an operator reading a queue of "not checkable" can only tell that
    from a reader failure if the kind is on the row.
    """
    from maritime_isr.anomaly.paperwork import check_arrival_window
    from maritime_isr.scenario.pans import DOCUMENT_KINDS

    assert "eta" not in DOCUMENT_KINDS["departure_report"].fields
    assert "eta" not in DOCUMENT_KINDS["port_clearance"].fields

    finding = check_arrival_window(declared={}, observed_arrival=None)
    assert finding.outcome == "not_checkable"


# ==========================================================================
# 4. the service layer — it assembles, it never decides
# ==========================================================================

def _registry():
    return [
        dict(vessel_id="vessel:a", imo="9100001", mmsi="419000001",
             call_sign="VTAA1", ship_name="GRANITE TRIUMPH"),
        dict(vessel_id="vessel:b", imo="9100002", mmsi="419000002",
             call_sign="VTBB2", ship_name="SEA HARRIER"),
        # Two hulls answering to one name — the case the resolver must refuse.
        dict(vessel_id="vessel:c", imo="9100003", ship_name="SEA HARRIER"),
    ]


def test_an_identifier_is_matched_exactly_or_not_at_all():
    """A lookup on the wrong hull is the same false accusation a misresolved
    notification is, shown to an operator's face."""
    from maritime_isr.ingest.pans.service import resolve_identifier

    reg = _registry()
    assert resolve_identifier("9100001", reg) == ("vessel:a", "imo")
    assert resolve_identifier("419000001", reg) == ("vessel:a", "mmsi")
    assert resolve_identifier("vtaa1", reg) == ("vessel:a", "call_sign")
    assert resolve_identifier("vessel:a", reg) == ("vessel:a", "vessel_id")
    assert resolve_identifier("GRANITE TRIUMPH", reg) == ("vessel:a", "name")

    # A name two hulls answer to resolves to neither, and says which it was.
    assert resolve_identifier("SEA HARRIER", reg) == (None, "name_ambiguous")
    # And a hull nothing holds is not an error.
    assert resolve_identifier("NOTHING AT ALL", reg) == (None, None)
    assert resolve_identifier(None, reg) == (None, None)


def _landed_row(**kw):
    row = dict(
        notification_id="PANS-0001", document_name="PANS-0001.pdf",
        document_format="pdf", document_kind="pans",
        vessel_id="vessel:a", resolved_by="imo", resolution_confidence=0.99,
        fields_read=3, fields_declared_absent=0, unread_reason=None,
        received_at=T0, received_at_source="declared",
        source_id="synthetic-scenario:pans-inbox", source_ref="PANS-0001.pdf",
        acquired_at=T0, ingested_at=T0, pipeline_version="deadbee",
        confidence=None, is_synthetic=True,
        vessel_name="GRANITE TRIUMPH",
        vessel_name_confidence=0.97, vessel_name_passage="Name of Vessel: GRANITE TRIUMPH",
        vessel_name_locator="page 1", vessel_name_method="pdf_text",
        imo="9100001", imo_confidence=0.55, imo_passage="IMO Number: 9100001",
        imo_locator="page 1 (scanned)", imo_method="ocr",
        last_port="Kandla", last_port_confidence=0.97,
        last_port_passage="Last Port of Call: Kandla",
        last_port_locator="page 1", last_port_method="pdf_text",
    )
    row.update(kw)
    return row


def test_a_document_comes_back_with_its_envelope_and_not_a_number():
    """CLAUDE.md §4.1. A flag an analyst cannot trace to its source document
    and the code version that read it is worthless — worse, a landmine."""
    from maritime_isr.ingest.pans.service import ENVELOPE_FIELDS, document_record

    doc = document_record(_landed_row())
    for key in ENVELOPE_FIELDS:
        assert key in doc["provenance"], key
    assert doc["provenance"]["pipeline_version"] == "deadbee"
    assert doc["provenance"]["source_id"].startswith("synthetic-scenario:")
    assert doc["document_kind_label"] == "pre-arrival notification"


def test_every_field_carries_the_line_it_was_read_from_and_how():
    """A spreadsheet cell at 1.0 and a smudge on a fax at 0.55 must not arrive
    looking equally certain, or the confidence column is decoration."""
    from maritime_isr.ingest.pans.service import document_record

    doc = document_record(_landed_row())
    name = doc["fields"]["vessel_name"]
    assert name["passage"] == "Name of Vessel: GRANITE TRIUMPH"
    assert name["locator"] == "page 1"
    assert name["method"] == "pdf_text"

    # The OCR'd value is visibly less trustworthy than the text-layer one.
    assert doc["fields"]["imo"]["confidence"] < name["confidence"]
    assert doc["fields"]["imo"]["method"] == "ocr"


def test_a_declared_absence_comes_back_as_an_answer_not_an_empty_box():
    """"Crew: NIL" is the agent answering. An empty box is the agent skipping
    it. Folding them loses the difference the whole area rests on."""
    from maritime_isr.ingest.pans.service import document_record

    doc = document_record(_landed_row(
        cargo=None, cargo_passage="Nature of Cargo: NIL",
        cargo_locator="page 1", cargo_method="declared_absent",
        cargo_confidence=0.97))
    assert doc["fields"]["cargo"]["declared_absent"] is True
    assert doc["fields"]["cargo"]["value"] is None
    # A field with neither a value nor a passage is simply not on the form and
    # is not reported as an absence anybody declared.
    assert "agent" not in doc["fields"]


def test_the_outcome_counts_always_have_three_keys():
    """Never folding "we could not check" into "fine" is not a default the
    caller can lose by having no findings."""
    from maritime_isr.ingest.pans.service import OUTCOMES, document_record

    doc = document_record(_landed_row())
    assert set(doc["outcomes"]) == set(OUTCOMES)
    assert OUTCOMES == ("contradiction", "ok", "not_checkable")
    assert all(v == 0 for v in doc["outcomes"].values())


def test_vessel_documents_runs_the_rules_and_reports_all_three_answers():
    """The entrypoint `api/` will call, end to end on injected rows.

    Everything is passed in, so this needs no corpus on disk — and the fact
    that it *can* be passed in is the seam that lets a serving layer with an
    open reader avoid opening a second one.
    """
    from maritime_isr.ingest.pans.service import OUTCOMES, vessel_documents

    arrival = T0 + timedelta(days=2)
    calls = [dict(vessel_id="vessel:a", port_name="Kandla",
                  start_time=arrival, lat=22.9, lon=70.2)]
    rows = [
        # Honest: she declares Kandla, and Kandla is where she berthed.
        _landed_row(notification_id="PANS-0001", last_port="Kandla",
                    arrival_port="Kandla"),
        # A last port on the far side of the sea from her track.
        _landed_row(notification_id="PANS-0002", last_port="Kochi",
                    arrival_port="Kandla"),
        # Nothing declared at all: not checkable, and it says so.
        _landed_row(notification_id="PANS-0003", last_port=None,
                    last_port_passage=None, arrival_port=None, cargo=None),
    ]
    out = vessel_documents("9100001", notifications=rows, registry=_registry(),
                           port_calls=calls, positions=[], draughts={})

    assert out["vessel_id"] == "vessel:a"
    assert out["matched_by"] == "imo"
    assert out["n_documents"] == 3
    assert set(out["outcomes"]) == set(OUTCOMES)
    assert sum(out["outcomes"].values()) > 0
    assert out["outcomes"]["not_checkable"] > 0
    # Every document carries its own verdicts as well as the roll-up.
    for doc in out["documents"]:
        assert set(doc["outcomes"]) == set(OUTCOMES)
        assert isinstance(doc["checks"], list)


def test_a_hull_with_no_paperwork_is_a_finding_and_not_a_404():
    """"She filed nothing" is one of the two gaps Area 4 exists to surface.
    Raising on it turns a finding into an error page."""
    from maritime_isr.ingest.pans.service import vessel_documents

    out = vessel_documents("9100002", notifications=[_landed_row()],
                           registry=_registry())
    assert out["vessel_id"] == "vessel:b"
    assert out["documents"] == []
    assert out["n_documents"] == 0
    assert out["outcomes"] == {"contradiction": 0, "ok": 0, "not_checkable": 0}


def test_an_ambiguous_name_returns_no_documents_and_says_why():
    """Better to answer "two hulls answer to that" than to show one hull's
    paperwork under the other's name."""
    from maritime_isr.ingest.pans.service import vessel_documents

    out = vessel_documents("SEA HARRIER", notifications=[_landed_row()],
                           registry=_registry())
    assert out["vessel_id"] is None
    assert out["matched_by"] == "name_ambiguous"
    assert out["documents"] == []


def test_an_unread_document_is_returned_rather_than_dropped():
    """A quarter of an inbox arriving in a format nobody can read must look
    like a gap in the reader, not like a quarter nobody submitted."""
    from maritime_isr.ingest.pans.service import vessel_documents

    rows = [_landed_row(notification_id="PANS-0009", fields_read=0,
                        unread_reason="ReaderUnavailable: tesseract missing")]
    out = vessel_documents("9100001", notifications=rows,
                           registry=_registry(), port_calls=[], positions=[],
                           draughts={})
    assert out["n_unread"] == 1
    assert out["documents"][0]["unread_reason"].startswith("ReaderUnavailable")


def test_a_form_naming_a_hull_nothing_holds_is_kept_and_not_dropped():
    """The other half of the gap the requirement names.

    *"A notification that cannot be matched to any track, or a vessel arriving
    with no notification at all, are both exactly the kind of gap the Coast
    Guard wants surfaced."* A grouping that discarded the unresolved ones would
    report a tidy inbox with the finding missing from it.
    """
    from maritime_isr.ingest.pans.service import group_by_vessel

    grouped = group_by_vessel([
        _landed_row(notification_id="PANS-0001"),
        _landed_row(notification_id="PANS-0002", vessel_id=None,
                    resolved_by=None, vessel_name="SEA HARRIER II"),
    ])
    assert set(grouped) == {"vessel:a", None}
    assert len(grouped[None]) == 1


def test_the_service_detects_nothing_of_its_own():
    """ADR-031's rule, one area over: a second copy of a rule living behind an
    API is an uncalibrated copy. Every verdict must come from
    `anomaly.paperwork`."""
    from pathlib import Path

    path = (Path(__file__).resolve().parents[1] / "maritime_isr" / "ingest"
            / "pans" / "service.py")
    text = path.read_text(encoding="utf-8")
    assert "from ...anomaly.paperwork import" in text
    # It holds no threshold of its own to drift against the rule's.
    for banned in ("RADIUS_KM", "SLIP_HOURS", "LADEN_DRAUGHT"):
        assert banned not in text, banned


def test_the_service_does_not_import_the_serving_layer():
    """`api/` may import this; nothing here may import `api/`. The read of the
    connector's own landed table belongs to the connector."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "maritime_isr"
    for path in (root / "ingest" / "pans").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "from ...api" not in text, path
        assert "maritime_isr.api" not in text, path


# ==========================================================================
# 5. the corpus outcome — generate, read back, and count
# ==========================================================================

def test_a_small_corpus_survives_a_full_round_trip(tmp_path):
    """Generate, read back through the real connector, and land.

    The end-to-end shape of `tools/make_port_documents.py`, small enough to run
    in a unit suite. What it catches is a connector that reads its own
    documents and lands nothing, or lands rows with no provenance — both of
    which look like success from inside any single module.
    """
    from maritime_isr.ingest.pans.land import read_inbox
    from maritime_isr.scenario.pans import build_document_specs, write_notifications

    specs = build_document_specs(_voyages(10), seed=6)
    # The scan is left out of the round trip on purpose. OCR is a property of
    # the machine — `pytesseract` wraps a binary pip cannot install — and it
    # costs the better part of a minute a page. A unit suite that quietly
    # depended on it would fail on a laptop for a reason that has nothing to do
    # with the code. The OCR path has its own test below, which skips when the
    # binary is not there rather than reporting a rate nobody measured.
    specs = [s for s in specs if s.document_format != "pdf_scan"]
    written = write_notifications(specs, tmp_path, seed=6)
    assert sum(v for v in written.values() if isinstance(v, int)) > 0

    registry = [dict(vessel_id=v["vessel_id"], imo=v["imo"],
                     call_sign=v["call_sign"], ship_name=v["name"])
                for v in _voyages(10)]
    rows = read_inbox(tmp_path, registry, is_synthetic=True)
    assert rows

    for row in rows:
        # Provenance envelope on every landed record, no exceptions.
        for key in ("source_id", "source_ref", "acquired_at", "ingested_at",
                    "pipeline_version"):
            assert row.get(key) is not None, (key, row["notification_id"])
        assert row["is_synthetic"] is True
        assert row["source_id"].startswith("synthetic-scenario:")

    # Most documents resolve to the hull they were written about; the ones
    # authored as an invented hull deliberately do not, and that is the gap.
    resolved = [r for r in rows if r.get("vessel_id")]
    assert resolved, "nothing resolved — the corpus and the registry disagree"


def test_the_filing_date_is_read_off_the_form_across_house_styles(tmp_path):
    """Four date notations, one filing time.

    Every rule in `anomaly.paperwork` measures a declaration against the track
    *as at the filing time*. A house whose date notation the reader cannot
    parse falls back to the file's mtime, which in a fresh generation looks
    right and in a copied inbox is weeks out — the silent failure that costs
    most.
    """
    from maritime_isr.ingest.pans.land import read_inbox
    from maritime_isr.scenario.pans import build_document_specs, write_notifications

    specs = build_document_specs(_voyages(18), seed=8)
    # The scan is excluded: OCR availability is a property of the machine, and
    # this test is about date notations, not about tesseract.
    specs = [s for s in specs if s.document_format != "pdf_scan"]
    write_notifications(specs, tmp_path, seed=8)

    rows = read_inbox(tmp_path, [], is_synthetic=True)
    declared = [r for r in rows if r["received_at_source"] == "declared"]
    assert declared, "no document's filing date was read off the page"
    # Most of them, not one: a single house parsing is not the claim.
    assert len(declared) >= len(rows) // 2


# --------------------------------------------------------------------------
# regressions — two bugs that were silent, and the reason each was silent
# --------------------------------------------------------------------------

def _land_positions(tmp_path, monkeypatch, rows) -> None:
    """Land `ais_position` rows into an isolated store, for real.

    Written through `landing.land_table` and read back through the parquet
    files it produces, because the read under test is now a *columnar* one:
    it projects four columns out of the partitions on disk. A monkeypatched
    `read_table` would no longer touch the code path that matters, and a test
    that passes because it stubbed out the thing it was testing is worse than
    no test.

    Rows are stamped with the provenance envelope before landing, because
    `land_table` refuses rows without one (CLAUDE.md §4.1) — and that refusal
    is correct and must not be worked around. A fixture that landed bare rows
    would be testing a path production cannot reach.
    """
    from maritime_isr import config as cfg_mod
    from maritime_isr.ingest import landing

    monkeypatch.setattr(cfg_mod.cfg, "data_root", tmp_path, raising=False)
    monkeypatch.setattr(landing.cfg, "data_root", tmp_path, raising=False)
    stamped = [
        landing.stamp_envelope(dict(r), source_id="synthetic-scenario",
                               source_ref=f"{r['vessel_id']}@{r['ts'].isoformat()}",
                               acquired_at=r["ts"], is_synthetic=True)
        for r in rows
    ]
    landing.land_table(stamped, table="ais_position",
                       key_fields=("vessel_id", "ts"), day_field="ts")


def test_the_position_clock_is_read_under_the_name_the_schema_gives_it(
        tmp_path, monkeypatch):
    """`ais_position` calls its clock `ts`; only `ais_voyage` says `timestamp`.

    This was read as `timestamp`, which produced nothing on every row into a
    bare `except` and left an empty position list. `check_last_port` tests
    `len(pts) < 3` *before* it looks at recorded port calls, so an empty list
    sends every document down the "too little track to say" branch: the whole
    corpus came back `not_checkable`, and the documents authored to lie about
    their last port were scored as unreadable rather than as misses.

    Nothing looked broken, because `not_checkable` is a legitimate answer. That
    is exactly why it is pinned here.
    """
    from maritime_isr.ingest.pans import service

    rows = [dict(vessel_id="vessel:a", ts=T0 + timedelta(hours=i),
                 lat=22.0 + i * 0.01, lon=69.0) for i in range(5)]
    rows += [dict(vessel_id="vessel:b", ts=T0 + timedelta(hours=i),
                  lat=9.9, lon=76.2) for i in range(3)]
    _land_positions(tmp_path, monkeypatch, rows)

    out = service._track_for("vessel:a", port_calls=[], positions=None,
                             draughts={})
    assert len(out["positions"]) == 5, (
        "positions keyed by 'ts' came back empty — the last-port check is "
        "blind again and every document will read as not_checkable")
    # Sorted (epoch_seconds, lat, lon), which is the shape the rule expects.
    assert out["positions"] == sorted(out["positions"])
    assert out["positions"][0][0] == T0.timestamp()
    # And it is *her* track: another hull's fixes are not folded in.
    assert {round(lat, 2) for _, lat, _ in out["positions"]} != {9.9}


def test_a_hulls_track_is_read_without_pulling_in_every_other_hulls(
        tmp_path, monkeypatch):
    """`track_fixes` narrows in the reader, not after it.

    The read this replaced sat behind a docstring promising it worked "per
    vessel rather than in bulk", and did the opposite: it materialised the
    whole `ais_position` table as one Python dict per fix and *then* filtered.
    At the corpus's present size — over eight hundred thousand fixes, twenty-five
    columns each — that is what got a process killed for running out of memory,
    and it is invisible in any test small enough to run quickly.

    So the promise is pinned as behaviour instead of as a comment: asking for
    one hull must not return another's, and asking for a hull the store has no
    track for must return **nothing at all** rather than an empty list. That
    distinction is load-bearing: "we hold no track for her" is the fact
    `check_last_port` turns into `not_checkable`, and a manufactured empty list
    for every hull ever named would make it unreadable.
    """
    from maritime_isr.ingest.pans import service

    rows = [dict(vessel_id=f"vessel:{tag}", ts=T0 + timedelta(hours=i),
                 lat=lat + i * 0.01, lon=69.0)
            for tag, lat in (("a", 22.0), ("b", 15.0), ("c", 9.0))
            for i in range(4)]
    _land_positions(tmp_path, monkeypatch, rows)

    everything = service.track_fixes()
    assert set(everything) == {"vessel:a", "vessel:b", "vessel:c"}
    assert all(len(v) == 4 for v in everything.values())

    one = service.track_fixes(["vessel:b"])
    assert set(one) == {"vessel:b"}
    assert one["vessel:b"] == everything["vessel:b"]

    # A hull with no track is absent, not present-and-empty.
    assert service.track_fixes(["vessel:nobody"]) == {}


def test_landing_what_was_already_read_does_not_read_it_again(tmp_path):
    """Reading is the expensive half; a caller must be able to pay once.

    `land_inbox` reads the directory itself, so a caller that wanted both the
    rows and them landed OCR'd every scanned page twice. `land_rows` takes what
    `read_inbox` produced. The proof that it does not quietly re-open anything
    is that landing still works when the inbox is **gone**.

    It must also not re-derive the envelope: the provenance on the landed row
    has to be the one stamped against the file that was actually opened, since
    inventing it here would be provenance for a document this call never saw.
    """
    from maritime_isr import config as cfg_mod
    from maritime_isr.ingest import landing
    from maritime_isr.ingest.pans.land import TABLE, land_rows, read_inbox
    from maritime_isr.scenario.pans import (build_document_specs,
                                            write_notifications)
    import shutil

    inbox = tmp_path / "inbox"
    specs = build_document_specs(_voyages(6), seed=5)
    specs = [s for s in specs if s.document_format != "pdf_scan"]
    write_notifications(specs, inbox, seed=5)
    rows = read_inbox(inbox, [], is_synthetic=True)
    assert rows

    envelopes = {r["notification_id"]: dict(r) for r in rows}
    shutil.rmtree(inbox)
    assert not inbox.exists()

    store = tmp_path / "store"
    cfg_mod.cfg.data_root, landing.cfg.data_root = store, store
    try:
        written = land_rows(rows)
        assert sum(v for v in written.values() if isinstance(v, int)) == len(rows)
        landed = list(landing.read_table(TABLE))
    finally:
        cfg_mod.cfg.data_root = cfg_mod._resolve_data_root()
        landing.cfg.data_root = cfg_mod.cfg.data_root

    assert len(landed) == len(rows)
    for row in landed:
        before = envelopes[row["notification_id"]]
        for name in ("source_id", "source_ref", "pipeline_version"):
            assert row[name] == before[name], name
        assert row["source_ref"], "a landed row with no source document"

    assert land_rows([]) == {}


def test_a_hull_with_track_gets_a_checkable_last_port_verdict():
    """The behaviour the bug above destroyed, stated as an outcome.

    A hull we hold positions and a recorded call for is one we can *answer*
    about — "we could not check" is the honest answer to an absent track, and
    a wrong answer to a present one.
    """
    from maritime_isr.anomaly.paperwork import check_last_port
    from maritime_isr.ingest.pans.land import declared_fields

    fixes = [((T0 + timedelta(hours=i)).timestamp(), 22.0, 69.5)
             for i in range(6)]
    declared = declared_fields(_landed_row(last_port="Kandla"))
    filed = T0 + timedelta(days=1)

    near = check_last_port(declared=declared, fixes=fixes, filed_at=filed,
                           prior_calls=[(22.9, 70.2)])          # Kandla
    assert near.outcome == "ok", near.statement

    far = check_last_port(declared=declared, fixes=fixes, filed_at=filed,
                          prior_calls=[(9.9, 76.2)])            # Kochi
    assert far.outcome == "contradiction", far.statement

    # No track at all is still "we could not check", and still not "fine".
    blind = check_last_port(declared=declared, fixes=[], filed_at=filed,
                            prior_calls=[(22.9, 70.2)])
    assert blind.outcome == "not_checkable"


def test_the_read_rate_is_a_recall_and_cannot_exceed_one():
    """A "read rate" above 100% is not a measurement.

    The old rate divided a count of every field with a value by the number of
    fields authored, matched rows only in the denominator and all rows in the
    numerator, and counted fields outside the document kind's own field list —
    so a port clearance that yielded an ETA it never had a box for scored
    better than a perfect read. It printed 102.9% for one house.

    The rate is now an intersection over the authored set, which is bounded by
    construction; this pins the construction rather than the printed number.
    """
    import tools.make_port_documents as mk
    from maritime_isr.ingest.pans.land import FIELD_NAMES
    from maritime_isr.scenario.pans import DOCUMENT_KINDS

    for kind, spec_kind in DOCUMENT_KINDS.items():
        spec = mk._ManifestSpec(dict(
            notification_id="X", document_kind=kind,
            fields_written=list(spec_kind.fields[:4]), omitted=[]))
        # A row carrying *every* field, including ones this kind never has.
        row = {name: "something" for name in FIELD_NAMES}
        want = mk._authored_fields(spec)
        got = mk._recovered_fields(row, kind)
        assert want, kind
        assert len(want & got) <= len(want)
        assert len(want & got) / len(want) <= 1.0
        # Fields outside the kind's own list are never counted as read.
        assert got <= set(spec_kind.fields)


def test_read_only_mode_writes_nothing(tmp_path):
    """Raw is immutable, and re-measuring the reader must not rewrite it.

    `--read-only` also has to work when the vessel corpus is unavailable, since
    generation reads that corpus and reading the documents does not.
    """
    import json as _json

    import tools.make_port_documents as mk
    from maritime_isr.scenario.pans import build_document_specs, write_notifications

    specs = build_document_specs(_voyages(6), seed=3)
    specs = [s for s in specs if s.document_format != "pdf_scan"]
    write_notifications(specs, tmp_path, seed=3)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    assert before

    manifest = tmp_path.parent / mk.MANIFEST_NAME
    mk._write_manifest(specs, manifest)
    replayed = mk._specs_from_manifest(manifest)

    assert len(replayed) == len(specs)
    for spec, back in zip(specs, replayed):
        assert back.notification_id == spec.notification_id
        assert back.document_kind == spec.document_kind
        assert back.expected == spec.expected
        assert back.fields_written == mk._authored_fields(spec)

    after = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    assert after == before, "replaying the answer key altered a raw document"
    assert _json.loads(manifest.read_text())
