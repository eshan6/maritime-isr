"""ADR-016a — direct OFAC vessel matching.

The thing these tests exist to protect is the **match precedence gradient**:
IMO > call sign > name, with a name-only match being a candidate rather than a
finding. Collapsing that gradient — treating a name collision as a sanctions
hit — is the false positive that destroys analyst trust (ADR-004), and it would
be easy to introduce by "simplifying" the matcher later.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from maritime_isr.ingest import sanctions_match as sm

UTC = timezone.utc
AS_OF = datetime(2026, 7, 29, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    from maritime_isr import config as cfg_mod
    from maritime_isr.ingest import landing

    monkeypatch.setattr(cfg_mod.cfg, "data_root", tmp_path, raising=False)
    monkeypatch.setattr(landing.cfg, "data_root", tmp_path, raising=False)
    return tmp_path


def _ofac(**over):
    r = {
        "ofac_ent_num": "9639", "ofac_name": "SEA HARRIER", "ofac_program": "IRAN",
        "ofac_call_sign": "ATF123", "ofac_vessel_type": "Cargo", "ofac_tonnage": "1,900",
        "ofac_gross_tonnage": "2,100", "ofac_flag": "Panama",
        "ofac_owner": "BLUEWATER HOLDINGS", "ofac_imo": "9111228",
        "sanctions_as_of": AS_OF,
    }
    r.update(over)
    r["_name_key"] = sm.normalise_name(r["ofac_name"])
    r["_cs_key"] = sm.normalise_call_sign(r["ofac_call_sign"])
    return r


# ==========================================================================
# normalisation
# ==========================================================================

def test_normalise_name_uppercases_and_strips_punctuation():
    assert sm.normalise_name("Sea  Harrier") == "SEA HARRIER"
    assert sm.normalise_name("SEA-HARRIER") == "SEA HARRIER"


def test_normalise_name_handles_gfw_angle_bracket_artefact():
    """Live GFW data contains names like 'ADEL>K>QAYYUM'.

    Punctuation must become a space, not vanish — deleting it would fuse the
    words into ADELKQAYYUM and never match anything.
    """
    assert sm.normalise_name("ADEL>K>QAYYUM") == "ADEL K QAYYUM"


def test_normalise_name_drops_vessel_prefix_noise():
    assert sm.normalise_name("M/V SEA HARRIER") == "SEA HARRIER"
    assert sm.normalise_name("MT OCEAN PEARL") == "OCEAN PEARL"


def test_normalise_name_returns_none_for_noise_only():
    assert sm.normalise_name("M/V") is None
    assert sm.normalise_name("") is None
    assert sm.normalise_name(None) is None


def test_normalise_imo_accepts_only_seven_digits():
    """A loose IMO match would be reported at 0.95 confidence — a confident
    error is worse than no answer."""
    assert sm.normalise_imo("9111228") == "9111228"
    assert sm.normalise_imo("IMO 9111228") == "9111228"
    assert sm.normalise_imo("911122") is None      # six digits
    assert sm.normalise_imo("91112283") is None    # eight digits
    assert sm.normalise_imo(None) is None


# ---- IMO check digit ------------------------------------------------------

def test_imo_checksum_accepts_real_imo_numbers():
    """Real IMOs taken from the live OFAC match set (2026-07-29 snapshot)."""
    for real in ("9179842", "9220641", "9369722", "9349289", "9161003",
                 "9305207", "9284154", "9264893", "8400945", "8818843",
                 "8519966", "9008108", "9034705"):
        assert sm.imo_checksum_ok(real), f"{real} is a real IMO and must pass"


def test_imo_checksum_rejects_the_overwhelming_majority_of_random_digits():
    """Quantifies what the check is worth, rather than assuming it.

    If this ever drops, the checksum has stopped being evidence and the 0.95
    tier is resting on less than we claim it is.
    """
    import random

    rng = random.Random(20260730)
    n = 20_000
    passing = sum(1 for _ in range(n)
                  if sm.imo_checksum_ok(f"{rng.randrange(1_000_000, 10_000_000)}"))
    rate = passing / n
    assert rate < 0.15, f"checksum only rejects {1 - rate:.1%} of random digits"


def test_normalise_imo_rejects_a_failing_check_digit():
    """Seven digits is not enough. 9111222 has the wrong check digit."""
    assert sm.imo_checksum_ok("9111222") is False
    assert sm.normalise_imo("9111222") is None
    # ...but the review tool must still be able to see what was rejected.
    assert sm.normalise_imo("9111222", require_checksum=False) == "9111222"


def test_a_checksum_failure_cannot_reach_the_finding_tier():
    """The end the checksum exists to serve: no 0.95 row on a bad number."""
    by_imo, by_cs, by_name = sm.build_indexes([_ofac(ofac_imo="9111222")])
    hit = sm.match_one({"imo": "9111222", "ship_name": "SOMETHING ELSE"},
                       by_imo, by_cs, by_name)
    assert hit is None, "a bad-check-digit IMO must not produce an IMO finding"


def test_normalise_call_sign_strips_separators():
    assert sm.normalise_call_sign("atf-123") == "ATF123"


# ==========================================================================
# precedence — the load-bearing behaviour
# ==========================================================================

def test_imo_match_wins_and_is_a_finding():
    by_imo, by_cs, by_name = sm.build_indexes([_ofac()])
    hit = sm.match_one({"imo": "9111228", "ship_name": "SOMETHING ELSE"},
                       by_imo, by_cs, by_name)
    assert hit is not None
    _, tier = hit
    assert tier == "imo"
    assert sm.TIER_CONFIDENCE["imo"] >= sm.FINDING_THRESHOLD


def test_call_sign_alone_is_a_candidate_not_a_finding():
    """Call signs are flag-state assigned, reused after reassignment, and short
    ones collide internationally. Alone, one is a lead."""
    by_imo, by_cs, by_name = sm.build_indexes([_ofac()])
    hit = sm.match_one({"call_sign": "ATF123"}, by_imo, by_cs, by_name)
    assert hit[1] == "call_sign"
    assert sm.TIER_CONFIDENCE["call_sign"] < sm.FINDING_THRESHOLD, (
        "a call-sign-only match must never reach finding confidence"
    )


def test_call_sign_plus_name_agreement_is_a_finding():
    """Two independent identifiers agreeing is enough to assert."""
    by_imo, by_cs, by_name = sm.build_indexes([_ofac()])
    hit = sm.match_one({"call_sign": "ATF123", "ship_name": "M/V Sea Harrier"},
                       by_imo, by_cs, by_name)
    assert hit[1] == "call_sign_name"
    assert sm.TIER_CONFIDENCE["call_sign_name"] >= sm.FINDING_THRESHOLD


def test_call_sign_with_a_disagreeing_name_stays_a_candidate():
    """A different name is the collision case the demotion exists for."""
    by_imo, by_cs, by_name = sm.build_indexes([_ofac()])
    hit = sm.match_one({"call_sign": "ATF123", "ship_name": "OCEAN PEARL"},
                       by_imo, by_cs, by_name)
    assert hit[1] == "call_sign"


def test_a_missing_name_does_not_demote_below_call_sign():
    """Absence of a name is not evidence against; it just fails to promote."""
    by_imo, by_cs, by_name = sm.build_indexes([_ofac(ofac_name=None)])
    hit = sm.match_one({"call_sign": "ATF123", "ship_name": "SEA HARRIER"},
                       by_imo, by_cs, by_name)
    assert hit[1] == "call_sign"


def test_name_only_match_is_a_candidate_not_a_finding():
    """The whole point of the gradient."""
    by_imo, by_cs, by_name = sm.build_indexes([_ofac()])
    hit = sm.match_one({"ship_name": "SEA HARRIER"}, by_imo, by_cs, by_name)
    assert hit[1] == "name"
    assert sm.TIER_CONFIDENCE["name"] < sm.FINDING_THRESHOLD, (
        "a name-only match must never reach finding confidence"
    )


def test_confidence_is_strictly_ordered_by_tier():
    confs = [sm.TIER_CONFIDENCE[t] for t in sm.TIER_ORDER]
    assert confs == sorted(confs, reverse=True), (
        f"TIER_ORDER {sm.TIER_ORDER} does not match the confidences {confs}"
    )
    assert set(sm.TIER_ORDER) == set(sm.TIER_CONFIDENCE), (
        "every tier needs a confidence and a place in the order"
    )


def test_the_finding_threshold_falls_between_call_sign_and_call_sign_name():
    """Where the line sits is the policy. Stating it as a test makes moving it
    a deliberate act rather than a side effect of tuning a number."""
    assert (sm.TIER_CONFIDENCE["call_sign"]
            < sm.FINDING_THRESHOLD
            <= sm.TIER_CONFIDENCE["call_sign_name"])
    findings = {t for t in sm.TIER_ORDER
                if sm.TIER_CONFIDENCE[t] >= sm.FINDING_THRESHOLD}
    assert findings == {"imo", "call_sign_name"}


def test_stronger_tier_is_not_inflated_by_weaker_agreement():
    """A name that also agrees adds nothing to an IMO match."""
    by_imo, by_cs, by_name = sm.build_indexes([_ofac()])
    both = sm.match_one({"imo": "9111228", "call_sign": "ATF123",
                         "ship_name": "SEA HARRIER"}, by_imo, by_cs, by_name)
    imo_only = sm.match_one({"imo": "9111228"}, by_imo, by_cs, by_name)
    assert both[1] == imo_only[1] == "imo"


def test_no_match_returns_none():
    by_imo, by_cs, by_name = sm.build_indexes([_ofac()])
    assert sm.match_one({"imo": "9999993", "ship_name": "UNRELATED"},
                        by_imo, by_cs, by_name) is None


# ==========================================================================
# ambiguity
# ==========================================================================

def test_ambiguous_name_keys_are_dropped_not_resolved_arbitrarily():
    """Two sanctioned entities sharing a normalised name.

    Picking one would attach a specific entity, program and owner to a vessel
    on no evidence at all.
    """
    a = _ofac(ofac_ent_num="1", ofac_name="OCEAN STAR", ofac_imo=None,
              ofac_call_sign=None)
    b = _ofac(ofac_ent_num="2", ofac_name="Ocean  Star", ofac_imo=None,
              ofac_call_sign=None)
    by_imo, by_cs, by_name = sm.build_indexes([a, b])
    assert "OCEAN STAR" not in by_name
    assert sm.match_one({"ship_name": "OCEAN STAR"}, by_imo, by_cs, by_name) is None


def test_distinct_names_both_indexed():
    a = _ofac(ofac_ent_num="1", ofac_name="OCEAN STAR", ofac_imo=None, ofac_call_sign=None)
    b = _ofac(ofac_ent_num="2", ofac_name="SEA HARRIER", ofac_imo=None, ofac_call_sign=None)
    _, _, by_name = sm.build_indexes([a, b])
    assert "OCEAN STAR" in by_name and "SEA HARRIER" in by_name


# ==========================================================================
# OFAC loading
# ==========================================================================

def test_ofac_imo_is_extracted_from_remarks(tmp_path):
    """OFAC has no IMO column; when present it sits in free-text remarks."""
    import duckdb

    from maritime_isr.ingest import registries as reg

    con = duckdb.connect(str(tmp_path / "t.duckdb"))
    reg._ensure_snapshot_meta(con)
    csv = ('9639,"SEA HARRIER","vessel","IRAN","-0-","ATF123","Cargo","1,900","2,100",'
           '"Panama","BLUEWATER HOLDINGS","Vessel Registration Identification IMO 9111228"\n')
    reg._fetch = lambda *a, **k: csv.encode()
    reg.refresh_ofac(con, AS_OF)

    rows = sm.load_ofac_vessels(con, AS_OF)
    assert len(rows) == 1
    assert rows[0]["ofac_imo"] == "9111228"
    assert rows[0]["ofac_owner"] == "BLUEWATER HOLDINGS"
    con.close()


def test_only_vessel_rows_are_loaded(tmp_path):
    """OFAC is mostly people and companies; we want the hulls."""
    import duckdb

    from maritime_isr.ingest import registries as reg

    con = duckdb.connect(str(tmp_path / "t2.duckdb"))
    reg._ensure_snapshot_meta(con)
    csv = ('36,"AN AIRLINE","-0-","CUBA","-0-","-0-","-0-","-0-","-0-","-0-","-0-","-0-"\n'
           '9639,"SEA HARRIER","vessel","IRAN","-0-","ATF123","Cargo","1,900","2,100",'
           '"Panama","OWNER LTD","-0-"\n')
    reg._fetch = lambda *a, **k: csv.encode()
    reg.refresh_ofac(con, AS_OF)

    rows = sm.load_ofac_vessels(con, AS_OF)
    assert len(rows) == 1 and rows[0]["ofac_name"] == "SEA HARRIER"
    con.close()


# ==========================================================================
# end to end
# ==========================================================================

def _land_identity(rows):
    from maritime_isr.ingest.landing import land_table, stamp_envelope

    out = []
    for r in rows:
        r = dict(r)
        r.setdefault("valid_from", datetime(2020, 1, 1, tzinfo=UTC))
        r.setdefault("valid_to", None)
        r.setdefault("record_kind", "registry")
        stamp_envelope(r, source_id="gfw-vessels",
                       source_ref=f"{r['vessel_id']}:registry:0",
                       acquired_at=r["valid_from"])
        out.append(r)
    land_table(out, table="gfw_vessel_identity",
               key_fields=("vessel_id", "record_kind", "mmsi", "ship_name", "valid_from"),
               day_field="valid_from")


def test_end_to_end_lands_matches_with_tiers(tmp_path, monkeypatch, capsys):
    import duckdb

    from maritime_isr.ingest import registries as reg
    from maritime_isr.ingest.landing import read_table

    _land_identity([
        {"vessel_id": "v-imo", "imo": "9111228", "ship_name": "RENAMED HULL",
         "call_sign": None, "mmsi": "419000001", "flag": "IND"},
        {"vessel_id": "v-name", "imo": None, "ship_name": "OCEAN PEARL",
         "call_sign": None, "mmsi": "419000002", "flag": "PAN"},
        {"vessel_id": "v-clean", "imo": "9999993", "ship_name": "HONEST TRADER",
         "call_sign": "ZZZ999", "mmsi": "419000003", "flag": "IND"},
    ])

    con = duckdb.connect(str(tmp_path / "m.duckdb"))
    reg._ensure_snapshot_meta(con)
    csv = ('9639,"SEA HARRIER","vessel","IRAN","-0-","ATF123","Cargo","1,900","2,100",'
           '"Panama","BLUEWATER HOLDINGS","IMO 9111228"\n'
           '9640,"OCEAN PEARL","vessel","IRAN","-0-","ATF999","Tanker","5,000","6,100",'
           '"Iran","MERIDIAN SHIPPING","-0-"\n')
    monkeypatch.setattr(reg, "_fetch", lambda *a, **k: csv.encode())
    reg.refresh_ofac(con, AS_OF)
    monkeypatch.setattr(sm, "connect", lambda *a, **k: con)

    assert sm.run() == 0
    matches = {r["vessel_id"]: r for r in read_table(sm.MATCH_TABLE)}

    # matched on IMO despite a different name — that is the point of using IMO
    assert matches["v-imo"]["match_tier"] == "imo"
    assert matches["v-imo"]["is_finding"] is True
    assert matches["v-imo"]["ofac_owner"] == "BLUEWATER HOLDINGS"

    # matched on name only — candidate, not finding
    assert matches["v-name"]["match_tier"] == "name"
    assert matches["v-name"]["is_finding"] is False

    # the clean vessel must not appear at all
    assert "v-clean" not in matches

    out = capsys.readouterr().out
    assert "CANDIDATES, not findings" in out, "the caveat must be printed, not implied"
    con.close()


def test_every_match_row_carries_provenance(tmp_path, monkeypatch):
    import duckdb

    from maritime_isr.ingest import registries as reg
    from maritime_isr.ingest.landing import read_table

    _land_identity([{"vessel_id": "v1", "imo": "9111228", "ship_name": "X",
                     "call_sign": None, "mmsi": "1", "flag": "IND"}])
    con = duckdb.connect(str(tmp_path / "p.duckdb"))
    reg._ensure_snapshot_meta(con)
    monkeypatch.setattr(reg, "_fetch", lambda *a, **k: (
        '9639,"SEA HARRIER","vessel","IRAN","-0-","CS1","Cargo","1","2",'
        '"Panama","OWNER","IMO 9111228"\n').encode())
    reg.refresh_ofac(con, AS_OF)
    monkeypatch.setattr(sm, "connect", lambda *a, **k: con)
    sm.run()

    for r in read_table(sm.MATCH_TABLE):
        assert r["source_id"] == "ofac-vessel-match"
        assert r["pipeline_version"]
        assert r["confidence"] == sm.TIER_CONFIDENCE[r["match_tier"]]
        assert r["sanctions_as_of"] is not None, "must record WHICH snapshot matched"
    con.close()


def test_no_identity_landed_is_a_clear_message(capsys):
    assert sm.run() == 0
    # The hint names `python -m maritime_isr.cli`, not the `maritime-isr`
    # console script: that script only exists after a `pip install`, and
    # naming a command the operator does not have is the defect ADR/PR #36
    # fixed. This assertion was left behind pointing at the old wording.
    assert ("Run `python -m maritime_isr.cli ingest gfw-vessels` first"
            in capsys.readouterr().out)


def test_zero_matches_is_reported_as_a_real_result(tmp_path, monkeypatch, capsys):
    """No overlap is a finding about the data, not a failure of the code."""
    import duckdb

    from maritime_isr.ingest import registries as reg

    _land_identity([{"vessel_id": "v1", "imo": "1234567", "ship_name": "CLEAN SHIP",
                     "call_sign": "AAA111", "mmsi": "1", "flag": "IND"}])
    con = duckdb.connect(str(tmp_path / "z.duckdb"))
    reg._ensure_snapshot_meta(con)
    monkeypatch.setattr(reg, "_fetch", lambda *a, **k: (
        '9639,"SEA HARRIER","vessel","IRAN","-0-","ATF123","Cargo","1","2",'
        '"Panama","OWNER","-0-"\n').encode())
    reg.refresh_ofac(con, AS_OF)
    monkeypatch.setattr(sm, "connect", lambda *a, **k: con)

    assert sm.run() == 0
    assert "not a failure" in capsys.readouterr().out
    con.close()


def test_matcher_does_not_touch_the_fusion_core():
    """CLAUDE.md §4.5 — enrichment lives on the ingest side."""
    import inspect

    src = inspect.getsource(sm)
    for forbidden in ("from ..fusion", "from ..graph", "import fusion", "import graph"):
        assert forbidden not in src, f"matcher must not reach into {forbidden!r}"


def test_reported_count_is_what_landed_not_what_was_built(tmp_path, monkeypatch, capsys):
    """First live run printed "landed 173" when 127 landed — a 36% overstatement.

    The natural key is (vessel_id, ofac_ent_num, match_tier), so one hull
    matching one OFAC entity via both its registry and self-reported identity
    records collapses to a single row.
    """
    import duckdb

    from maritime_isr.ingest import registries as reg
    from maritime_isr.ingest.landing import read_table

    # same vessel, same IMO, two identity records -> two candidate rows, one landed
    _land_identity([
        {"vessel_id": "v1", "imo": "9111228", "ship_name": "OLD NAME",
         "call_sign": None, "mmsi": "1", "flag": "IND", "record_kind": "registry"},
        {"vessel_id": "v1", "imo": "9111228", "ship_name": "NEW NAME",
         "call_sign": None, "mmsi": "2", "flag": "PAN", "record_kind": "self_reported"},
    ])
    con = duckdb.connect(str(tmp_path / "d.duckdb"))
    reg._ensure_snapshot_meta(con)
    monkeypatch.setattr(reg, "_fetch", lambda *a, **k: (
        '9639,"SEA HARRIER","vessel","IRAN","-0-","CS1","Cargo","1","2",'
        '"Panama","OWNER","Registration Identification IMO 9111228"\n').encode())
    reg.refresh_ofac(con, AS_OF)
    monkeypatch.setattr(sm, "connect", lambda *a, **k: con)

    sm.run()
    out = capsys.readouterr().out
    landed = len(read_table(sm.MATCH_TABLE))

    assert landed == 1, "both identity records match the same entity — one row"
    assert f"landed {landed} match" in out, f"reported count must equal landed count:\n{out}"
    assert "2 built" in out, "the gap between built and landed must be explained, not hidden"
    assert "landed 2 match" not in out, "the built count must never be the headline"
    con.close()


# ==========================================================================
# UN and EU registries
#
# Neither list has a vessel record type, a call-sign column or a flag column.
# Everything below exists to keep that difference from being flattened into
# "another OFAC" — which would turn thousands of company and person names into
# vessel name-match candidates.
# ==========================================================================

def _un_xml(*entities: str) -> bytes:
    body = "".join(entities)
    return f"<CONSOLIDATED_LIST>{body}</CONSOLIDATED_LIST>".encode()


def _un_entity(data_id: str, name: str, comments: str = "",
               list_type: str = "DPRK") -> str:
    return (f"<ENTITY><DATAID>{data_id}</DATAID>"
            f"<FIRST_NAME>{name}</FIRST_NAME>"
            f"<UN_LIST_TYPE>{list_type}</UN_LIST_TYPE>"
            f"<COMMENTS1>{comments}</COMMENTS1></ENTITY>")


def _un_individual(data_id: str, name: str) -> str:
    return (f"<INDIVIDUAL><DATAID>{data_id}</DATAID>"
            f"<FIRST_NAME>{name}</FIRST_NAME>"
            f"<UN_LIST_TYPE>DPRK</UN_LIST_TYPE></INDIVIDUAL>")


def _un_con(tmp_path, monkeypatch, xml: bytes, name: str = "un.duckdb"):
    import duckdb

    from maritime_isr.ingest import registries as reg

    con = duckdb.connect(str(tmp_path / name))
    reg._ensure_snapshot_meta(con)
    monkeypatch.setattr(reg, "_fetch", lambda *a, **k: xml)
    reg.refresh_un(con, AS_OF)
    return con


# ---- free-text IMO extraction --------------------------------------------

def test_imo_extraction_requires_the_imo_keyword():
    """A bare 7-digit number is not an IMO.

    Sanctions free text is full of registration, passport and licence numbers
    of exactly seven digits. Anchoring on the literal token is what stops a
    passport number becoming a 0.95-confidence hull match.
    """
    assert sm.extract_imo_from_text("IMO 9111228") == "9111228"
    assert sm.extract_imo_from_text("Passport No. 9111228") is None


def test_imo_extraction_still_validates_the_check_digit():
    """Anchoring and the checksum are independent checks; both must hold."""
    assert sm.extract_imo_from_text("IMO 9111228") == "9111228"
    assert sm.extract_imo_from_text("IMO 9111229") is None, "check digit fails"


def test_imo_extraction_tolerates_separators_between_keyword_and_digits():
    assert sm.extract_imo_from_text("IMO No. 9111228") == "9111228"
    assert sm.extract_imo_from_text("IMO: 9111228") == "9111228"


def test_imo_extraction_does_not_reach_across_a_long_run_of_text():
    """The keyword must be near the digits, or it is not labelling them."""
    assert sm.extract_imo_from_text("IMO listed, see annex, reference 9111228") is None


# ---- the vessel-marker gate ----------------------------------------------

def test_vessel_marker_admits_text_that_names_a_ship():
    assert sm.looks_like_vessel("Vessel flying the flag of Panama")
    assert sm.looks_like_vessel("Crude oil tanker, gross tonnage 60,000")


def test_vessel_marker_rejects_an_ordinary_company():
    assert not sm.looks_like_vessel("KOREA KUMSAN TRADING CORPORATION", "Pyongyang")


def test_un_individuals_are_dropped(tmp_path, monkeypatch):
    """A person is not a hull, and a vessel name matching a person's name is
    pure collision — the exact false positive ADR-004 is about."""
    con = _un_con(tmp_path, monkeypatch, _un_xml(
        _un_individual("100", "SEA HARRIER"),
        _un_entity("200", "SEA HARRIER", "Vessel, flag Panama"),
    ))
    rows = sm.load_un_vessels(con, AS_OF)
    assert len(rows) == 1
    assert rows[0]["ofac_ent_num"] == "200"
    con.close()


def test_un_entity_without_vessel_evidence_is_not_name_matchable(tmp_path, monkeypatch):
    """A trading company keeps its designation but must not enter the name
    index — otherwise every vessel sharing a word with it becomes a hit."""
    con = _un_con(tmp_path, monkeypatch, _un_xml(
        _un_entity("300", "OCEAN STAR TRADING CORPORATION", "Pyongyang office"),
    ))
    rows = sm.load_un_vessels(con, AS_OF)
    assert rows == [], "no IMO and no vessel marker -> not a candidate hull at all"
    con.close()


def test_un_entity_with_an_imo_is_loaded_even_without_a_vessel_marker(tmp_path, monkeypatch):
    """An IMO is a hull number and nothing else carries one, so it is its own
    evidence that the row names a ship."""
    con = _un_con(tmp_path, monkeypatch, _un_xml(
        _un_entity("400", "ANONYMOUS HOLDING", "Registration IMO 9111228"),
    ))
    rows = sm.load_un_vessels(con, AS_OF)
    assert len(rows) == 1 and rows[0]["ofac_imo"] == "9111228"
    con.close()


def test_un_row_carries_no_call_sign_so_it_cannot_reach_the_top_finding_tier(
        tmp_path, monkeypatch):
    """UN has no call-sign column. `call_sign_name` must be unreachable rather
    than accidentally satisfied by a null matching a null."""
    con = _un_con(tmp_path, monkeypatch, _un_xml(
        _un_entity("500", "SEA HARRIER", "Vessel, flag Panama"),
    ))
    rows = sm.load_un_vessels(con, AS_OF)
    assert rows[0]["_cs_key"] is None
    by_imo, by_cs, by_name = sm.build_indexes(rows)
    assert by_cs == {}, "a null call sign must not become an index key"

    vessel = {"vessel_id": "v1", "imo": None, "ship_name": "SEA HARRIER",
              "call_sign": None}
    hit = sm.match_one(vessel, by_imo, by_cs, by_name)
    assert hit is not None and hit[1] == "name", "name tier only"
    assert sm.TIER_CONFIDENCE["name"] < sm.FINDING_THRESHOLD
    con.close()


def test_un_registry_is_stamped_on_the_designation(tmp_path, monkeypatch):
    con = _un_con(tmp_path, monkeypatch, _un_xml(
        _un_entity("600", "SEA HARRIER", "Vessel, flag Panama"),
    ))
    assert sm.load_un_vessels(con, AS_OF)[0]["registry"] == "UN"
    con.close()


def test_missing_un_table_returns_empty_rather_than_raising(tmp_path):
    """UN is refreshed independently of OFAC; a corpus with only OFAC landed
    must still match, not 500."""
    import duckdb

    con = duckdb.connect(str(tmp_path / "bare.duckdb"))
    from maritime_isr.ingest import registries as reg
    reg._ensure_snapshot_meta(con)
    assert sm.load_un_vessels(con) == []
    assert sm.load_eu_vessels(con) == []
    con.close()


# ---- cross-registry behaviour --------------------------------------------

def test_indexes_are_built_per_registry_so_un_cannot_drop_an_ofac_name_key(
        tmp_path, monkeypatch):
    """The number that must not move.

    A shared name index would see the same name in OFAC and UN, call it
    ambiguous, and drop it — changing OFAC's published match count for a reason
    that has nothing to do with OFAC. Matching runs per registry to prevent it.
    """
    _land_identity([
        {"vessel_id": "v1", "imo": None, "ship_name": "SEA HARRIER",
         "call_sign": None, "mmsi": "1", "flag": "IND"},
    ])
    import duckdb

    from maritime_isr.ingest import registries as reg
    from maritime_isr.ingest.landing import read_table

    con = duckdb.connect(str(tmp_path / "x.duckdb"))
    reg._ensure_snapshot_meta(con)
    monkeypatch.setattr(reg, "_fetch", lambda *a, **k: (
        '9639,"SEA HARRIER","vessel","IRAN","-0-","CS1","Cargo","1","2",'
        '"Panama","OWNER","-0-"\n').encode())
    reg.refresh_ofac(con, AS_OF)
    monkeypatch.setattr(reg, "_fetch", lambda *a, **k: _un_xml(
        _un_entity("700", "SEA HARRIER", "Vessel, flag Panama")))
    reg.refresh_un(con, AS_OF)
    monkeypatch.setattr(sm, "connect", lambda *a, **k: con)

    sm.run()
    rows = read_table(sm.MATCH_TABLE)
    regs = sorted(r["registry"] for r in rows)
    assert regs == ["OFAC", "UN"], (
        f"both registries must land their own row, got {regs}")
    con.close()


def test_the_same_hull_in_two_registries_lands_two_rows(tmp_path, monkeypatch, capsys):
    """Two independent lists naming one hull is corroboration. Collapsing it to
    one row throws away the strongest evidence this module can produce."""
    _land_identity([
        {"vessel_id": "v1", "imo": "9111228", "ship_name": "SEA HARRIER",
         "call_sign": None, "mmsi": "1", "flag": "IND"},
    ])
    import duckdb

    from maritime_isr.ingest import registries as reg
    from maritime_isr.ingest.landing import read_table

    con = duckdb.connect(str(tmp_path / "corr.duckdb"))
    reg._ensure_snapshot_meta(con)
    monkeypatch.setattr(reg, "_fetch", lambda *a, **k: (
        '9639,"SEA HARRIER","vessel","IRAN","-0-","CS1","Cargo","1","2",'
        '"Panama","OWNER","Registration IMO 9111228"\n').encode())
    reg.refresh_ofac(con, AS_OF)
    monkeypatch.setattr(reg, "_fetch", lambda *a, **k: _un_xml(
        _un_entity("800", "SEA HARRIER", "Vessel, IMO 9111228, flag Panama")))
    reg.refresh_un(con, AS_OF)
    monkeypatch.setattr(sm, "connect", lambda *a, **k: con)

    sm.run()
    rows = read_table(sm.MATCH_TABLE)
    assert len(rows) == 2, "one row per designating registry"
    assert all(r["match_tier"] == "imo" and r["is_finding"] for r in rows)
    assert "MORE THAN ONE registry" in capsys.readouterr().out
    con.close()


def test_each_row_names_the_list_it_came_from_in_its_provenance(
        tmp_path, monkeypatch):
    """A finding an analyst cannot trace to a specific published list is not
    traceable (CLAUDE.md §4.1)."""
    _land_identity([
        {"vessel_id": "v1", "imo": "9111228", "ship_name": "SEA HARRIER",
         "call_sign": None, "mmsi": "1", "flag": "IND"},
    ])
    import duckdb

    from maritime_isr.ingest import registries as reg
    from maritime_isr.ingest.landing import read_table

    con = duckdb.connect(str(tmp_path / "prov.duckdb"))
    reg._ensure_snapshot_meta(con)
    monkeypatch.setattr(reg, "_fetch", lambda *a, **k: _un_xml(
        _un_entity("900", "SEA HARRIER", "Vessel, IMO 9111228")))
    reg.refresh_un(con, AS_OF)
    monkeypatch.setattr(sm, "connect", lambda *a, **k: con)

    sm.run()
    row = read_table(sm.MATCH_TABLE)[0]
    assert row["source_id"] == "un-vessel-match"
    assert "UN" in row["source_ref"]
    con.close()


def test_ofac_rows_keep_their_original_source_id(tmp_path, monkeypatch):
    """Adding UN and EU must not restamp provenance on rows OFAC already owns."""
    _land_identity([
        {"vessel_id": "v1", "imo": "9111228", "ship_name": "SEA HARRIER",
         "call_sign": None, "mmsi": "1", "flag": "IND"},
    ])
    import duckdb

    from maritime_isr.ingest import registries as reg
    from maritime_isr.ingest.landing import read_table

    con = duckdb.connect(str(tmp_path / "ofacprov.duckdb"))
    reg._ensure_snapshot_meta(con)
    monkeypatch.setattr(reg, "_fetch", lambda *a, **k: (
        '9639,"SEA HARRIER","vessel","IRAN","-0-","CS1","Cargo","1","2",'
        '"Panama","OWNER","Registration IMO 9111228"\n').encode())
    reg.refresh_ofac(con, AS_OF)
    monkeypatch.setattr(sm, "connect", lambda *a, **k: con)

    sm.run()
    assert read_table(sm.MATCH_TABLE)[0]["source_id"] == "ofac-vessel-match"
    con.close()


def test_match_rows_carry_the_vessel_side_identity_fields(tmp_path, monkeypatch):
    """The API and the findings screen read `vessel_name`/`vessel_flag`/
    `vessel_imo`. The matcher used to write only `ship_name`/`flag`/`imo`, so
    the sanctions panel rendered blank vessel fields on the real corpus while
    looking correct on the scenario corpus."""
    _land_identity([
        {"vessel_id": "v1", "imo": "9111228", "ship_name": "SEA HARRIER",
         "call_sign": None, "mmsi": "1", "flag": "IND"},
    ])
    import duckdb

    from maritime_isr.ingest import registries as reg
    from maritime_isr.ingest.landing import read_table

    con = duckdb.connect(str(tmp_path / "vfields.duckdb"))
    reg._ensure_snapshot_meta(con)
    monkeypatch.setattr(reg, "_fetch", lambda *a, **k: (
        '9639,"SEA HARRIER","vessel","IRAN","-0-","CS1","Cargo","1","2",'
        '"Panama","OWNER","Registration IMO 9111228"\n').encode())
    reg.refresh_ofac(con, AS_OF)
    monkeypatch.setattr(sm, "connect", lambda *a, **k: con)

    sm.run()
    row = read_table(sm.MATCH_TABLE)[0]
    assert row["vessel_name"] == "SEA HARRIER"
    assert row["vessel_flag"] == "IND"
    assert row["vessel_imo"] == "9111228"
    con.close()
