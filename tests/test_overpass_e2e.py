"""End-to-end: overpass rows land through the real path, with provenance, and
reach the incident report without acquiring a claim they are not entitled to.

The geometry lives in `test_overpass.py`. This file is about the seam — the
landing contract (CLAUDE.md §4.1), the API attaching evidence to the right
gap, and the wording that leaves the building in the exported report.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from maritime_isr import overpass as ov

UTC = timezone.utc
T0 = datetime(2026, 7, 1, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    from maritime_isr import config as cfg_mod
    from maritime_isr.ingest import landing

    monkeypatch.setattr(cfg_mod.cfg, "data_root", tmp_path, raising=False)
    monkeypatch.setattr(landing.cfg, "data_root", tmp_path, raising=False)
    return tmp_path


def _box(lat_c, lon_c, half_deg):
    la0, la1 = lat_c - half_deg, lat_c + half_deg
    lo0, lo1 = lon_c - half_deg, lon_c + half_deg
    return (f"POLYGON (({lo0} {la0}, {lo1} {la0}, {lo1} {la1}, "
            f"{lo0} {la1}, {lo0} {la0}))")


def _gap(event_id="gap-1", vessel_id="v-1", hours=4.0, synthetic=False):
    return {
        "event_id": event_id, "vessel_id": vessel_id,
        "start_time": T0, "end_time": T0 + timedelta(hours=hours),
        "gap_off_lat": 18.0, "gap_off_lon": 68.0,
        "gap_on_lat": 18.0, "gap_on_lon": 68.3,
        "gap_duration_hours": hours, "gap_implied_speed_kn": 4.0,
        "gfw_intentional_disabling": 1, "is_synthetic": synthetic,
    }


def _scene(scene_id="S1A_X", at_hours=2.0, half=4.0, status="cataloged"):
    return {
        "scene_id": scene_id, "footprint_wkt": _box(18.0, 68.15, half),
        "acquired_at": T0 + timedelta(hours=at_hours),
        "orbit_direction": "ASCENDING", "relative_orbit": 42,
        "mode": "IW", "polarizations": "VV+VH", "status": status,
    }


def _run(monkeypatch, gaps, scenes):
    monkeypatch.setattr(ov, "load_flagged_gaps", lambda **k: gaps)
    monkeypatch.setattr(ov, "load_scenes", lambda: scenes)
    return ov.run()


# ---------------------------------------------------------------------------
# landing
# ---------------------------------------------------------------------------

def test_rows_land_and_are_readable(monkeypatch):
    from maritime_isr.ingest.landing import read_table

    assert _run(monkeypatch, [_gap()], [_scene()]) == 1
    rows = read_table(ov.TABLE)
    assert len(rows) == 1
    assert rows[0]["tier"] == "confirmed"
    assert rows[0]["scene_id"] == "S1A_X"
    assert rows[0]["gap_event_id"] == "gap-1"
    assert rows[0]["vessel_id"] == "v-1"


def test_every_row_carries_the_full_provenance_envelope(monkeypatch):
    from maritime_isr.ingest.landing import read_table

    _run(monkeypatch, [_gap()], [_scene(), _scene("S1A_Y", at_hours=3.0)])
    rows = read_table(ov.TABLE)
    assert rows
    for r in rows:
        for field in ("source_id", "source_ref", "acquired_at",
                      "ingested_at", "pipeline_version"):
            assert r.get(field) is not None, f"{field} missing — CLAUDE.md §4.1"
        assert r["source_id"] == ov.SOURCE_ID
        assert r["is_synthetic"] is False
        # H3 at every project resolution, so this table can join the others.
        for res in (4, 6, 7, 8, 9):
            assert r.get(f"h3_r{res}")


def test_acquired_at_is_the_pass_time_not_the_run_time(monkeypatch):
    """`acquired_at` means when the phenomenon happened (landing.py's rule)."""
    from maritime_isr.ingest.landing import read_table

    _run(monkeypatch, [_gap()], [_scene(at_hours=2.0)])
    r = read_table(ov.TABLE)[0]
    acquired = r["acquired_at"]
    if acquired.tzinfo is None:
        acquired = acquired.replace(tzinfo=UTC)
    assert acquired == T0 + timedelta(hours=2)


def test_synthetic_gap_produces_a_synthetic_row(monkeypatch):
    """ADR-019: the flag and the source id may never disagree."""
    from maritime_isr.ingest.landing import read_table

    _run(monkeypatch, [_gap(synthetic=True)], [_scene()])
    r = read_table(ov.TABLE)[0]
    assert r["is_synthetic"] is True
    assert r["source_id"] == "synthetic-scenario"


def test_relanding_is_idempotent(monkeypatch):
    from maritime_isr.ingest.landing import read_table

    _run(monkeypatch, [_gap()], [_scene()])
    _run(monkeypatch, [_gap()], [_scene()])
    assert len(read_table(ov.TABLE)) == 1


def test_a_gap_nobody_imaged_still_lands_a_row(monkeypatch):
    """Evaluated-and-empty must be distinguishable from never-run (ADR-021)."""
    from maritime_isr.ingest.landing import read_table

    far = _scene("S1A_FAR")
    far["footprint_wkt"] = _box(30.0, 80.0, 0.5)
    _run(monkeypatch, [_gap()], [far])
    rows = read_table(ov.TABLE)
    assert len(rows) == 1
    assert rows[0]["tier"] == "none"
    assert rows[0]["confidence"] is None


# ---------------------------------------------------------------------------
# the loaders, unmocked — the seam the monkeypatched tests above skip over
# ---------------------------------------------------------------------------

def test_load_scenes_reads_the_real_scene_catalog(tmp_path, monkeypatch):
    """`scene_catalog` is a DuckDB table, not a Parquet view — a different read
    path from every other table this module touches, so it gets its own test."""
    import duckdb
    from maritime_isr import db as db_mod

    path = tmp_path / "misr.duckdb"
    con = duckdb.connect(str(path))
    db_mod.ensure_scene_catalog(con)
    con.execute(
        "INSERT INTO scene_catalog (scene_id, footprint_wkt, orbit_direction, "
        "relative_orbit, acquired_at, mode, polarizations, status) VALUES "
        "(?, ?, 'ASCENDING', 42, ?, 'IW', 'VV+VH', 'cataloged')",
        ["S1A_REAL", _box(18.0, 68.15, 4.0), T0 + timedelta(hours=2)])
    # A row with no footprint must be filtered out rather than crashing later.
    con.execute(
        "INSERT INTO scene_catalog (scene_id, footprint_wkt, acquired_at, status) "
        "VALUES ('S1A_NOFP', NULL, ?, 'cataloged')", [T0])
    con.close()

    monkeypatch.setattr(ov, "connect",
                        lambda read_only=False: duckdb.connect(str(path),
                                                               read_only=read_only))
    scenes = ov.load_scenes()
    assert [s["scene_id"] for s in scenes] == ["S1A_REAL"]
    assert scenes[0]["footprint_wkt"].startswith("POLYGON")

    # and it flows straight into an assessment
    r = ov.assess_pass(_gap(), scenes[0])
    assert r["tier"] == "confirmed"


def test_load_scenes_returns_empty_when_no_database_exists():
    """DuckDB refuses read-only on a missing file; that is 'nothing landed',
    not a crash. The `_isolated` fixture points `cfg.data_root` at an empty tmp
    directory, so `cfg.duckdb_path()` resolves to a file that does not exist —
    which is exactly the bare-checkout case."""
    from maritime_isr import db as db_mod

    assert not db_mod.cfg.duckdb_path().exists()
    assert ov.load_scenes() == []


def test_load_flagged_gaps_filters_on_gfw_verdict(monkeypatch):
    """The verdict lands INTEGER on the real corpus, BOOLEAN on the scenario
    one, and NULL on synthetic gaps (OPEN QUESTION #9). All three must behave."""
    rows = [
        dict(_gap("flagged-int"), gfw_intentional_disabling=1),
        dict(_gap("flagged-bool"), gfw_intentional_disabling=True),
        dict(_gap("not-flagged"), gfw_intentional_disabling=0),
        dict(_gap("synthetic-null"), gfw_intentional_disabling=None),
    ]
    monkeypatch.setattr(ov, "read_table", lambda t: rows)
    flagged = {g["event_id"] for g in ov.load_flagged_gaps()}
    assert flagged == {"flagged-int", "flagged-bool"}
    assert len(ov.load_flagged_gaps(flagged_only=False)) == 4


# ---------------------------------------------------------------------------
# what the operator is told
# ---------------------------------------------------------------------------

def test_run_output_refuses_to_imply_a_detection(monkeypatch, capsys):
    _run(monkeypatch, [_gap()], [_scene()])
    out = capsys.readouterr().out.lower()
    assert "no image has been examined" in out
    assert "no vessel has been detected" in out


def test_run_prints_the_scene_shopping_list(monkeypatch, capsys):
    _run(monkeypatch, [_gap()], [_scene("S1A_WANTED")])
    out = capsys.readouterr().out
    assert "S1A_WANTED" in out
    assert "shopping list" in out.lower()


def test_scene_already_downloaded_is_not_on_the_shopping_list(monkeypatch, capsys):
    _run(monkeypatch, [_gap()], [_scene("S1A_HELD", status="raw")])
    out = capsys.readouterr().out
    assert "shopping list" not in out.lower()


def test_an_unwatched_gap_is_reported_as_a_finding_not_a_failure(monkeypatch, capsys):
    far = _scene("S1A_FAR")
    far["footprint_wkt"] = _box(30.0, 80.0, 0.5)
    _run(monkeypatch, [_gap()], [far])
    out = capsys.readouterr().out.lower()
    assert "nobody was watching" in out
    assert "itself a finding" in out


def test_zero_confirmed_explains_the_geometry_rather_than_implying_a_fault(
        monkeypatch, capsys):
    """`partial` is the ordinary outcome; a reader must not read 0 confirmed as
    a broken run. The explanation is printed with the count."""
    far = _scene("S1A_FAR")
    far["footprint_wkt"] = _box(30.0, 80.0, 0.5)
    _run(monkeypatch, [_gap()], [far])
    out = capsys.readouterr().out.lower()
    assert "no confirmed opportunities" in out
    assert "geometry, not a weak result" in out


def test_partial_opportunities_reach_the_shopping_list(monkeypatch, capsys):
    """The regime this tool actually runs in: partial coverage on a long gap.

    Printing only confirmed rows would report an empty shopping list while
    genuinely useful scenes sat in the table.
    """
    gap = _gap(hours=28.0)
    scene = _scene("S1A_PARTIAL", at_hours=14.0, half=1.12)   # ~250 km footprint
    _run(monkeypatch, [gap], [scene])
    out = capsys.readouterr().out
    assert "S1A_PARTIAL" in out
    assert "shopping list" in out.lower()
    assert "% of the searchable area" in out


def test_missing_inputs_name_the_command_to_run(monkeypatch, capsys):
    monkeypatch.setattr(ov, "load_flagged_gaps", lambda **k: [])
    assert ov.run() == 0
    assert "ingest gfw-events" in capsys.readouterr().out

    monkeypatch.setattr(ov, "load_flagged_gaps", lambda **k: [_gap()])
    monkeypatch.setattr(ov, "load_scenes", lambda: [])
    assert ov.run() == 0
    assert "ingest s1" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# the API contract
# ---------------------------------------------------------------------------

def test_imaging_survives_the_findings_response_model():
    """The `/findings` route round-trips every item through `models.Finding`,
    and pydantic drops unknown fields **silently**. Without `imaging` declared
    on `FindingGap` the evidence would reach the serializer and vanish there,
    with nothing failing — so this asserts the field survives the round trip
    rather than trusting that it does.
    """
    from maritime_isr.api import models

    item = {
        "id": "vessel:x", "name": "X", "is_synthetic": False,
        "priority": 1000, "headline": "h", "attribution": "a",
        "basis": [{"signal": "gfw_intentional_disabling", "weight": 1000,
                   "explanation": "e"}],
        "prov": {"source_id": "gfw-gaps", "source_ref": "r",
                 "pipeline_version": "abc"},
        "dark_gaps": [{
            "start_time": T0.isoformat(),
            "attribution": "Global Fishing Watch assessed this gap as "
                           "intentional AIS disabling",
            "imaging": [{"tier": "confirmed", "scene_id": "S1A_X",
                         "coverage_fraction": 1.0, "statement": "s"}],
            "imaging_best_tier": "confirmed",
            "prov": {"source_id": "gfw-gaps", "source_ref": "r",
                     "pipeline_version": "abc"},
        }],
    }
    dumped = models.Finding(**item).model_dump()
    gap = dumped["dark_gaps"][0]
    assert gap["imaging_best_tier"] == "confirmed"
    assert len(gap["imaging"]) == 1
    assert gap["imaging"][0]["scene_id"] == "S1A_X"
    assert gap["imaging"][0]["tier"] == "confirmed"


# ---------------------------------------------------------------------------
# the incident report — the artefact that leaves the building
# ---------------------------------------------------------------------------

def _finding_with_imaging(tier="confirmed", **over):
    opp = {
        "tier": tier, "scene_id": "S1A_X",
        "scene_acquired_at": (T0 + timedelta(hours=2)).isoformat(),
        "hours_into_gap": 2.0, "coverage_fraction": 1.0 if tier == "confirmed" else 0.4,
        "reachable_area_km2": 5000.0, "covered_area_km2": 5000.0,
        "geometry_basis": "lens", "scene_has_pixels": False,
        "orbit_direction": "ASCENDING", "v_max_knots": 20.0,
        "implied_speed_exceeds_vmax": False,
        "statement": "x", "is_synthetic": False, "prov": {},
    }
    opp.update(over)
    return {"dark_gaps": [{"start_time": T0.isoformat(),
                           "end_time": (T0 + timedelta(hours=4)).isoformat(),
                           "duration_hours": 4.0, "imaging": [opp],
                           "imaging_best_tier": tier}],
            "sanctions_is_finding": False}


def test_report_names_the_pass_but_claims_no_detection():
    from maritime_isr.api.report import build_report, render_html

    rep = build_report(vessel={"current": {}, "gaps": []},
                       finding=_finding_with_imaging(), alerts=[], stats={})
    html = render_html(rep)
    assert "Satellite imaging opportunities" in html
    assert "S1A_X" in html
    assert "No image has been examined and no vessel has been detected" in html
    assert "not downloaded" in html


def test_report_caveats_the_unexamined_imagery_in_not_established():
    from maritime_isr.api.report import build_report

    rep = build_report(vessel={"current": {}, "gaps": []},
                       finding=_finding_with_imaging(), alerts=[], stats={})
    joined = " ".join(rep["not_established"]).lower()
    assert "has not been downloaded or examined" in joined


def test_report_says_nobody_was_watching_when_no_pass_exists():
    from maritime_isr.api.report import build_report, render_html

    f = _finding_with_imaging(tier="none")
    rep = build_report(vessel={"current": {}, "gaps": []},
                       finding=f, alerts=[], stats={})
    html = render_html(rep)
    assert "Nobody was" in html or "nobody was" in html
    # And it must not then also claim an opportunity.
    assert "necessarily contained" not in html


def test_report_omits_the_section_entirely_when_nothing_was_computed():
    """No overpass run => no section, rather than an empty one implying zero."""
    from maritime_isr.api.report import build_report, render_html

    rep = build_report(
        vessel={"current": {}, "gaps": []},
        finding={"dark_gaps": [{"start_time": T0.isoformat(),
                                "end_time": None, "duration_hours": 4.0}]},
        alerts=[], stats={})
    html = render_html(rep)
    assert "Satellite imaging opportunities" not in html


def test_partial_pass_caveat_refuses_to_imply_the_vessel_was_imaged():
    """The over-reading this section is most exposed to: a pass clipping the
    edge of a large area is not evidence the vessel is in the picture."""
    from maritime_isr.api.report import build_report

    rep = build_report(vessel={"current": {}, "gaps": []},
                       finding=_finding_with_imaging(tier="partial"),
                       alerts=[], stats={})
    joined = " ".join(rep["not_established"]).lower()
    assert "not established that any image contains the vessel" in joined
    assert "necessarily contained" not in joined


def test_report_separates_the_possible_area_from_the_imaged_area():
    """Two different numbers; showing only the larger would overstate the
    search that actually happened."""
    from maritime_isr.api.report import build_report, render_html

    f = _finding_with_imaging(tier="partial", reachable_area_km2=87263.0,
                              covered_area_km2=8726.0, coverage_fraction=0.1)
    html = render_html(build_report(vessel={"current": {}, "gaps": []},
                                    finding=f, alerts=[], stats={}))
    assert "Could have been in" in html and "Actually imaged" in html
    assert "87263" in html.replace(",", "") and "8726" in html.replace(",", "")


def test_partial_tier_is_not_described_as_containment():
    from maritime_isr.api.report import build_report, render_html

    rep = build_report(vessel={"current": {}, "gaps": []},
                       finding=_finding_with_imaging(tier="partial"),
                       alerts=[], stats={})
    html = render_html(rep)
    assert "necessarily contained" not in html
    assert "covered part of the area" in html
