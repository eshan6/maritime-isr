"""The 2.3-year port visit, and what actually caused it.

Profiling the operator's corpus produced `port_call_dwell_hours` with a 75th
percentile of 52 days and a 95th of 20,253 hours. That reads as corrupt data. It
is not: the number was `duration_hours`, GFW's *event span*, being profiled as
though it were time alongside.

A GFW port visit is stitched from up to four sub-events — entry, stop, gap, exit
— and its span covers whichever of them GFW observed. The span is a dwell only
when the vessel actually stopped and the anchorage it entered is the anchorage it
left. Otherwise the same number measures a transit across the port polygon, or an
entry and an exit at two different anchorages with everything between them
unobserved. The mapper that wrote those 3,000 rows recorded none of that, so
there was no way to tell the cases apart and every span read as a dwell.

These tests pin the three things that fix has to get right:

  * the mapper populates `dwell_hours` **only** where the structure supports it,
    and never clamps or corrects `duration_hours`;
  * the generator emits the same mix of structures, so `WHERE dwell_hours IS
    NULL` does not become a synthetic-row detector;
  * a partition whose optional column happens to be all-null does not make the
    table unreadable — the failure that surfaced while building this and would
    have surfaced on the real corpus eventually.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from maritime_isr.ingest import landing
from maritime_isr.ingest.gfw_events import _port_visit_structure, map_event
from maritime_isr.ingest.landing import (land_table, read_table,
                                         reconcile_null_columns)

T = datetime(2026, 6, 18, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    monkeypatch.setattr(landing.cfg, "data_root", tmp_path)
    return tmp_path


def _visit(pv: dict, *, hours: float = 60.0, eid: str = "e1") -> dict:
    return {
        "id": eid, "type": "PORT_VISIT",
        "start": T.isoformat().replace("+00:00", "Z"),
        "end": (T + timedelta(hours=hours)).isoformat().replace("+00:00", "Z"),
        "position": {"lat": 22.4, "lon": 69.1},
        "vessel": {"id": "v1", "ssvid": "419000001", "name": "TEST"},
        "port_visit": pv,
    }


def _anch(aid: str) -> dict:
    return {"id": aid, "name": aid.upper(), "flag": "IND"}


# --------------------------------------------------------------------------
# the mapper
# --------------------------------------------------------------------------

def test_complete_visit_is_a_dwell():
    r = map_event(_visit({
        "confidence": 4,
        "startAnchorage": _anch("ind-sikka"),
        "intermediateAnchorage": _anch("ind-sikka"),
        "endAnchorage": _anch("ind-sikka"),
    }), "port_visits")
    assert r["dwell_hours"] == 60.0
    assert r["visit_has_stop"] is True
    assert r["visit_anchorages_agree"] is True
    assert r["visit_port_source"] == "intermediate"


def test_visit_without_a_stop_has_no_dwell():
    """Entry and exit, nothing observed in between.

    **Not present in the operator's corpus** — measured 2026-07-31, all 3,000
    real port visits carry a stop. Kept because GFW's schema permits it and the
    mapper must not invent a dwell if one ever arrives.
    """
    r = map_event(_visit({
        "confidence": 2,
        "startAnchorage": _anch("ind-sikka"),
        "endAnchorage": _anch("ind-sikka"),
    }), "port_visits")
    assert r["duration_hours"] == 60.0, "the span is GFW's and is not touched"
    assert r["dwell_hours"] is None
    assert r["visit_has_stop"] is False
    # The port is still resolved, from the entry anchorage, so the visit is not
    # dropped on the floor by the graph populator.
    assert r["visit_port_id"] == "ind-sikka"
    assert r["visit_port_source"] == "start"


def test_stitched_visit_across_two_anchorages_has_no_dwell():
    """Entered at Sikka, left from Mundra: the span is not time alongside."""
    r = map_event(_visit({
        "confidence": 3,
        "startAnchorage": _anch("ind-sikka"),
        "intermediateAnchorage": _anch("ind-sikka"),
        "endAnchorage": _anch("ind-mundra"),
    }, hours=20_253.0), "port_visits")
    assert r["duration_hours"] == pytest.approx(20_253.0)
    assert r["dwell_hours"] is None
    assert r["visit_anchorages_agree"] is False


def test_one_anchorage_is_unknown_not_disagreement():
    """"Cannot tell" and "they differ" are different facts."""
    r = map_event(_visit({"confidence": 2,
                          "startAnchorage": _anch("ind-sikka")}), "port_visits")
    assert r["visit_anchorages_agree"] is None
    assert r["dwell_hours"] is None


def test_duration_is_never_clamped():
    """A 2.3-year span survives verbatim. Silently 'fixing' it would be worse.

    The span is what GFW reported. Capping it here would destroy the evidence
    that the source produces these, and would put a magic number in the ingest
    layer that nothing downstream could see or reason about.
    """
    r = map_event(_visit({"confidence": 4,
                          "startAnchorage": _anch("ind-sikka"),
                          "intermediateAnchorage": _anch("ind-sikka"),
                          "endAnchorage": _anch("ind-mundra")},
                         hours=20_253.0), "port_visits")
    assert r["duration_hours"] == pytest.approx(20_253.0)


def test_non_visit_events_do_not_get_visit_assertions():
    """An encounter has no stop; it must not be recorded as having none."""
    r = map_event({
        "id": "g1", "type": "GAP",
        "start": T.isoformat().replace("+00:00", "Z"),
        "position": {"lat": 18.0, "lon": 64.0},
        "vessel": {"id": "v9"},
        "gap": {"durationHours": 30.0},
    }, "gaps")
    assert r["visit_has_stop"] is None
    assert r["visit_anchorages_agree"] is None
    assert r["dwell_hours"] is None


def test_structure_helper_tolerates_a_missing_port_visit_object():
    assert _port_visit_structure({}, 5.0)["dwell_hours"] is None
    assert _port_visit_structure(None, 5.0)["visit_has_stop"] is None


# --------------------------------------------------------------------------
# the all-null column, which made the table unreadable
# --------------------------------------------------------------------------

def test_all_null_partition_does_not_break_the_table(data_root):
    """One sparse column must not make a whole table unqueryable.

    Arrow types a column from the values present, so a day where `dwell_hours`
    is null in every row gets it typed `null` while a day with one real value
    gets `double`. Read together — which is how everything here reads them —
    DuckDB takes the schema from the first file and fails outright. The bug
    does not fire when the column is added; it fires the first time a partition
    comes out empty, which is why it needs a test rather than vigilance.
    """
    duckdb = pytest.importorskip("duckdb")

    def row(day: str, eid: str, dwell):
        r = {"event_id": eid, "start_time": datetime.fromisoformat(day).replace(
                tzinfo=timezone.utc), "lat": 22.4, "lon": 69.1,
             "dwell_hours": dwell, "duration_hours": 12.0}
        landing.stamp_envelope(r, source_id="gfw-events", source_ref=eid,
                               acquired_at=r["start_time"])
        return r

    land_table([row("2026-06-01", "a", None)], table="pv",
               key_fields=("event_id",), day_field="start_time")
    land_table([row("2026-06-02", "b", 30.0)], table="pv",
               key_fields=("event_id",), day_field="start_time")

    glob = str(data_root / "conformed" / "pv" / "day=*" / "part.parquet")
    con = duckdb.connect()
    n = con.execute(f"select count(*) from read_parquet('{glob}')").fetchone()[0]
    assert n == 2
    got = con.execute(
        f"select count(dwell_hours) from read_parquet('{glob}')").fetchone()[0]
    assert got == 1


def test_reconcile_reports_when_it_has_nothing_to_do(data_root):
    """No null-typed columns means no rewrites — this must not churn files."""
    r = {"event_id": "x", "start_time": T, "lat": 1.0, "lon": 60.0,
         "duration_hours": 4.0}
    landing.stamp_envelope(r, source_id="gfw-events", source_ref="x",
                           acquired_at=T)
    land_table([r], table="pv2", key_fields=("event_id",),
               day_field="start_time")
    assert reconcile_null_columns("pv2") == 0


# --------------------------------------------------------------------------
# the generator side: same structure mix, or the corpus is separable
# --------------------------------------------------------------------------

def test_synthetic_visits_are_not_all_dwells():
    """`WHERE dwell_hours IS NULL` must not be a synthetic-row detector.

    This is the null-rate failure family (nulls.py) reached from a third
    direction: the track-level separability test passes the whole time while the
    populations differ on a column instead.
    """
    from maritime_isr.scenario.land import assign_visit_structures
    from maritime_isr.scenario.profile import CorpusProfile

    profile = CorpusProfile.load()
    rows = [{"event_id": f"pv-{i}", "port_id": "anch:sikka",
             "port_name": "Sikka", "duration_hours": 30.0 + i}
            for i in range(200)]
    assign_visit_structures(rows, profile)

    dwells = sum(1 for r in rows if r.get("dwell_hours") is not None)
    target = profile.visit_structure().value["dwell"]
    assert abs(dwells / len(rows) - target) < 0.02, (
        f"{dwells}/{len(rows)} visits are dwells against a target of {target}")


def test_synthetic_visit_fields_are_jointly_possible():
    """Marginally right and jointly impossible is the trap here.

    Masking each column at its own measured rate would hit every marginal and
    still produce a dwell with no stop, or a port name with no anchorage it
    could have come from. Anything downstream that trusts the relationship would
    then break on synthetic data and nowhere else.
    """
    from maritime_isr.scenario.land import assign_visit_structures
    from maritime_isr.scenario.profile import CorpusProfile

    rows = [{"event_id": f"pv-{i}", "port_id": "anch:sikka",
             "port_name": "Sikka", "duration_hours": 30.0}
            for i in range(200)]
    assign_visit_structures(rows, CorpusProfile.load())

    for r in rows:
        stop, agree = r["visit_has_stop"], r["visit_anchorages_agree"]
        assert (r["dwell_hours"] is not None) == bool(stop and agree is True)
        # `port_name` is the stop anchorage's `name`, so it requires a stop AND
        # that GFW named it — measured, GFW names only 54.4% of anchorages, and
        # a synthetic corpus that named all of them would be separable on this
        # column alone.
        assert (r["port_name"] is not None) == bool(
            stop and r["anchorage_name"] is not None)
        assert (r["anchorage_id"] is not None) == bool(stop)
        # The destination survives an unnamed anchorage. This is what lets a
        # demo render a readable place for one GFW never named.
        if stop:
            assert r["anchorage_top_destination"]
        ids = [r[k] for k in ("start_anchorage_id", "anchorage_id",
                              "end_anchorage_id") if r[k]]
        if len(ids) >= 2:
            assert (len(set(ids)) == 1) is (agree is True)
        else:
            assert agree is None


def test_structure_assignment_is_deterministic():
    from maritime_isr.scenario.land import assign_visit_structures
    from maritime_isr.scenario.profile import CorpusProfile

    def run():
        rows = [{"event_id": f"pv-{i}", "port_id": "anch:sikka",
                 "port_name": "Sikka", "duration_hours": 30.0}
                for i in range(120)]
        assign_visit_structures(rows, CorpusProfile.load())
        return [(r["event_id"], r["visit_has_stop"], r["dwell_hours"])
                for r in rows]

    assert run() == run()


def test_landed_synthetic_visits_carry_the_structure_columns(tmp_path,
                                                             monkeypatch):
    """Column parity: a rebuilt real row has these, so a synthetic one must too.

    Otherwise the rebuild that fixes the real side immediately reintroduces
    separability from the other direction — real rows with `start_anchorage_id`,
    synthetic rows without the column at all.
    """
    monkeypatch.setattr(landing.cfg, "data_root", tmp_path)
    from maritime_isr.ingest.gfw_events import map_event as me
    real = me(_visit({"confidence": 4,
                      "startAnchorage": _anch("ind-sikka"),
                      "intermediateAnchorage": _anch("ind-sikka"),
                      "endAnchorage": _anch("ind-sikka")}), "port_visits")

    from maritime_isr.scenario.land import assign_visit_structures
    from maritime_isr.scenario.profile import CorpusProfile
    syn = [{"event_id": "s1", "port_id": "anch:sikka", "port_name": "Sikka",
            "duration_hours": 30.0}]
    assign_visit_structures(syn, CorpusProfile.load())

    structural = {k for k in real
                  if k.startswith(("visit_", "start_anchorage_",
                                   "end_anchorage_", "anchorage_"))
                  or k == "dwell_hours"}
    # `port_visit_id` and the distance context are connector metadata the
    # generator has no analogue for; the structural fields are the ones a
    # filter could separate on.
    missing = sorted(structural - set(syn[0]) - {"visit_port_source"})
    assert not missing, f"synthetic port visits lack {missing}"


# --------------------------------------------------------------------------
# the rebuild tool
# --------------------------------------------------------------------------

def test_rebuild_preserves_synthetic_rows(tmp_path, monkeypatch):
    """The rebuild rewrites partitions wholesale. Synthetic rows must survive.

    They share partitions with real rows by design (ADR-019), so a rebuild that
    forgot them would delete the entire scenario corpus and report success.
    """
    monkeypatch.setattr(landing.cfg, "data_root", tmp_path)
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "rebuild_conformed", "tools/rebuild_conformed.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # A synthetic row and a real row in the same day partition.
    syn = {"event_id": "syn-1", "start_time": T, "lat": 22.4, "lon": 69.1,
           "duration_hours": 30.0}
    landing.stamp_envelope(syn, source_id=landing.SYNTHETIC_SOURCE_ID,
                           source_ref="syn-1", acquired_at=T, is_synthetic=True)
    old_real = {"event_id": "e1", "start_time": T, "lat": 22.4, "lon": 69.1,
                "duration_hours": 60.0}
    landing.stamp_envelope(old_real, source_id="gfw-events", source_ref="e1",
                           acquired_at=T)
    land_table([syn, old_real], table="gfw_port_visits",
               key_fields=("event_id",), day_field="start_time")

    # Raw for the real event only, exactly as `fetch_kind` lands it.
    raw = tmp_path / "raw" / "gfw-events" / "day=2026-06-18"
    raw.mkdir(parents=True)
    (raw / "port_visits_20260601_20260731.json").write_text(json.dumps([
        _visit({"confidence": 4,
                "startAnchorage": _anch("ind-sikka"),
                "intermediateAnchorage": _anch("ind-sikka"),
                "endAnchorage": _anch("ind-sikka")}, eid="e1")]))

    res = mod.rebuild_kind("port_visits", dry_run=False)
    assert res["synthetic"] == 1
    assert res["rebuilt"] == 1
    assert res["orphans"] == 0, "the real row's raw record was present"

    rows = {r["event_id"]: r for r in read_table("gfw_port_visits")}
    assert set(rows) == {"syn-1", "e1"}
    assert rows["syn-1"]["is_synthetic"] is True
    # The real row gained the structure it was missing; the synthetic one is
    # untouched by the rebuild.
    assert rows["e1"]["dwell_hours"] == 60.0
    assert rows["e1"]["visit_confidence"] == 4


def test_rebuild_preserves_ingested_at_but_restamps_pipeline_version(
        tmp_path, monkeypatch):
    """A re-derivation is not a new ingest, but it *is* new code."""
    monkeypatch.setattr(landing.cfg, "data_root", tmp_path)
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "rebuild_conformed2", "tools/rebuild_conformed.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    old = {"event_id": "e1", "start_time": T, "lat": 22.4, "lon": 69.1,
           "duration_hours": 60.0}
    landing.stamp_envelope(old, source_id="gfw-events", source_ref="e1",
                           acquired_at=T)
    old["ingested_at"] = datetime(2026, 6, 19, tzinfo=timezone.utc)
    old["pipeline_version"] = "deadbee"
    land_table([old], table="gfw_port_visits", key_fields=("event_id",),
               day_field="start_time")

    raw = tmp_path / "raw" / "gfw-events" / "day=2026-06-18"
    raw.mkdir(parents=True)
    (raw / "port_visits_a.json").write_text(json.dumps([
        _visit({"confidence": 4, "startAnchorage": _anch("ind-sikka"),
                "intermediateAnchorage": _anch("ind-sikka"),
                "endAnchorage": _anch("ind-sikka")}, eid="e1")]))

    mod.rebuild_kind("port_visits", dry_run=False)
    r = read_table("gfw_port_visits")[0]
    assert r["ingested_at"] == datetime(2026, 6, 19, tzinfo=timezone.utc)
    assert r["pipeline_version"] != "deadbee"


def test_rebuild_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(landing.cfg, "data_root", tmp_path)
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "rebuild_conformed3", "tools/rebuild_conformed.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    old = {"event_id": "e1", "start_time": T, "lat": 22.4, "lon": 69.1,
           "duration_hours": 60.0}
    landing.stamp_envelope(old, source_id="gfw-events", source_ref="e1",
                           acquired_at=T)
    land_table([old], table="gfw_port_visits", key_fields=("event_id",),
               day_field="start_time")
    raw = tmp_path / "raw" / "gfw-events" / "day=2026-06-18"
    raw.mkdir(parents=True)
    (raw / "port_visits_a.json").write_text(json.dumps([
        _visit({"confidence": 4, "startAnchorage": _anch("ind-sikka"),
                "intermediateAnchorage": _anch("ind-sikka"),
                "endAnchorage": _anch("ind-sikka")}, eid="e1")]))

    mod.rebuild_kind("port_visits", dry_run=True)
    assert read_table("gfw_port_visits")[0].get("dwell_hours") is None


def test_orphaned_real_rows_are_kept_not_deleted(tmp_path, monkeypatch):
    """Raw is supposed to be sufficient. Where it is not, say so and keep the row.

    Dropping a real row because its raw record is missing would quietly shrink
    the corpus while the tool reported success — and the count is the evidence
    that "derived data is regenerable" is or is not currently true.
    """
    monkeypatch.setattr(landing.cfg, "data_root", tmp_path)
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "rebuild_conformed4", "tools/rebuild_conformed.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    for eid in ("has-raw", "no-raw"):
        r = {"event_id": eid, "start_time": T, "lat": 22.4, "lon": 69.1,
             "duration_hours": 60.0}
        landing.stamp_envelope(r, source_id="gfw-events", source_ref=eid,
                               acquired_at=T)
        land_table([r], table="gfw_port_visits", key_fields=("event_id",),
                   day_field="start_time")

    raw = tmp_path / "raw" / "gfw-events" / "day=2026-06-18"
    raw.mkdir(parents=True)
    (raw / "port_visits_a.json").write_text(json.dumps([
        _visit({"confidence": 4, "startAnchorage": _anch("ind-sikka"),
                "intermediateAnchorage": _anch("ind-sikka"),
                "endAnchorage": _anch("ind-sikka")}, eid="has-raw")]))

    res = mod.rebuild_kind("port_visits", dry_run=False)
    assert res["orphans"] == 1
    assert {r["event_id"] for r in read_table("gfw_port_visits")} == {
        "has-raw", "no-raw"}


# --------------------------------------------------------------------------
# the graph
# --------------------------------------------------------------------------

def test_visit_without_a_stop_still_becomes_an_edge(tmp_path, monkeypatch):
    """A stop-less visit still becomes an edge.

    `port_id` is read from the stop anchorage alone and the graph populator
    keyed on it, so a visit without a stop produced no `docked-at` edge — it was
    counted as `port_visits_skipped` and never looked at again.

    **This does not currently happen on the operator's corpus.** All 3,000 real
    port visits have a stop with an id, so `port_id` was already 100% populated
    and nothing was being skipped; an earlier claim that ~46% were dropped was
    inferred from `port_name`'s null rate and was wrong (ADR-020 correction).
    The test stays because the fallback is the guard, and a guard with no test
    is a guard nobody knows is broken.
    """
    monkeypatch.setattr(landing.cfg, "data_root", tmp_path)
    from maritime_isr.graph import from_landed
    from maritime_isr.graph.store import GraphStore

    r = map_event(_visit({"confidence": 2,
                          "startAnchorage": _anch("ind-sikka"),
                          "endAnchorage": _anch("ind-sikka")}), "port_visits")
    assert r["port_id"] is None, "no stop means no intermediate anchorage"
    land_table([r], table="gfw_port_visits", key_fields=("event_id",),
               day_field="start_time")

    store = GraphStore(tmp_path / "graph.db")
    n, skipped = from_landed.add_port_visits(store, set())
    assert (n, skipped) == (1, 0)
