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
        "ofac_owner": "BLUEWATER HOLDINGS", "ofac_imo": "9111222",
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
    assert sm.normalise_imo("9111222") == "9111222"
    assert sm.normalise_imo("IMO 9111222") == "9111222"
    assert sm.normalise_imo("911122") is None      # six digits
    assert sm.normalise_imo("91112223") is None    # eight digits
    assert sm.normalise_imo(None) is None


def test_normalise_call_sign_strips_separators():
    assert sm.normalise_call_sign("atf-123") == "ATF123"


# ==========================================================================
# precedence — the load-bearing behaviour
# ==========================================================================

def test_imo_match_wins_and_is_a_finding():
    by_imo, by_cs, by_name = sm.build_indexes([_ofac()])
    hit = sm.match_one({"imo": "9111222", "ship_name": "SOMETHING ELSE"},
                       by_imo, by_cs, by_name)
    assert hit is not None
    _, tier = hit
    assert tier == "imo"
    assert sm.TIER_CONFIDENCE["imo"] >= sm.FINDING_THRESHOLD


def test_call_sign_match_when_imo_absent():
    by_imo, by_cs, by_name = sm.build_indexes([_ofac()])
    hit = sm.match_one({"call_sign": "ATF123"}, by_imo, by_cs, by_name)
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
    assert (sm.TIER_CONFIDENCE["imo"]
            > sm.TIER_CONFIDENCE["call_sign"]
            > sm.TIER_CONFIDENCE["name"])


def test_stronger_tier_is_not_inflated_by_weaker_agreement():
    """A name that also agrees adds nothing to an IMO match."""
    by_imo, by_cs, by_name = sm.build_indexes([_ofac()])
    both = sm.match_one({"imo": "9111222", "call_sign": "ATF123",
                         "ship_name": "SEA HARRIER"}, by_imo, by_cs, by_name)
    imo_only = sm.match_one({"imo": "9111222"}, by_imo, by_cs, by_name)
    assert both[1] == imo_only[1] == "imo"


def test_no_match_returns_none():
    by_imo, by_cs, by_name = sm.build_indexes([_ofac()])
    assert sm.match_one({"imo": "9999999", "ship_name": "UNRELATED"},
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
           '"Panama","BLUEWATER HOLDINGS","Vessel Registration Identification IMO 9111222"\n')
    reg._fetch = lambda *a, **k: csv.encode()
    reg.refresh_ofac(con, AS_OF)

    rows = sm.load_ofac_vessels(con, AS_OF)
    assert len(rows) == 1
    assert rows[0]["ofac_imo"] == "9111222"
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
        {"vessel_id": "v-imo", "imo": "9111222", "ship_name": "RENAMED HULL",
         "call_sign": None, "mmsi": "419000001", "flag": "IND"},
        {"vessel_id": "v-name", "imo": None, "ship_name": "OCEAN PEARL",
         "call_sign": None, "mmsi": "419000002", "flag": "PAN"},
        {"vessel_id": "v-clean", "imo": "9999999", "ship_name": "HONEST TRADER",
         "call_sign": "ZZZ999", "mmsi": "419000003", "flag": "IND"},
    ])

    con = duckdb.connect(str(tmp_path / "m.duckdb"))
    reg._ensure_snapshot_meta(con)
    csv = ('9639,"SEA HARRIER","vessel","IRAN","-0-","ATF123","Cargo","1,900","2,100",'
           '"Panama","BLUEWATER HOLDINGS","IMO 9111222"\n'
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

    _land_identity([{"vessel_id": "v1", "imo": "9111222", "ship_name": "X",
                     "call_sign": None, "mmsi": "1", "flag": "IND"}])
    con = duckdb.connect(str(tmp_path / "p.duckdb"))
    reg._ensure_snapshot_meta(con)
    monkeypatch.setattr(reg, "_fetch", lambda *a, **k: (
        '9639,"SEA HARRIER","vessel","IRAN","-0-","CS1","Cargo","1","2",'
        '"Panama","OWNER","IMO 9111222"\n').encode())
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
    assert "Run `maritime-isr ingest gfw-vessels` first" in capsys.readouterr().out


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
