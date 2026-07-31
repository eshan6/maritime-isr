"""The demo gate, and the de-biased duration measurement behind it.

Two things are tested here.

**`tools/data_health.py`** grades the data on disk, not the code's intentions,
and exits non-zero on anything that would make a demo state something false. It
only earns that authority if its blockers actually fire, so each one gets a
fixture that trips it and a fixture that does not.

**The containment filter in `tools/corpus_profile.py`.** The connector asks GFW
for events *overlapping* a window, which over-samples long events in direct
proportion to their length — a fourteen-year port visit overlaps every possible
window, a twelve-hour one only overlaps if it falls inside. Any duration
quantile over the whole table inherits that bias, which is how
`port_call_dwell_hours` came out with a p95 of 2.3 years while every visit in it
was structurally sound. Restricting to visits that began *and* ended inside the
window removes it exactly. That is arithmetic rather than a theory about GFW, so
it can be tested directly.
"""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone

import pytest

from maritime_isr.ingest import landing
from maritime_isr.ingest.landing import land_table, stamp_envelope, stamp_h3

T = datetime(2026, 6, 18, 9, 0, tzinfo=timezone.utc)


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    monkeypatch.setattr(landing.cfg, "data_root", tmp_path)
    (tmp_path / "raw").mkdir()
    return tmp_path


def _visit_row(eid: str, *, start=T, hours=30.0, synthetic=False,
               confidence=0.9, **extra) -> dict:
    r = {"event_id": eid, "event_kind": "port_visits",
         "start_time": start, "end_time": start + timedelta(hours=hours),
         "duration_hours": hours, "lat": 22.43, "lon": 69.84,
         "vessel_id": "v1", "port_id": "ind-sikka", **extra}
    stamp_h3(r)
    stamp_envelope(
        r,
        source_id="synthetic-scenario" if synthetic else "gfw-events",
        source_ref=eid, acquired_at=start, confidence=confidence,
        is_synthetic=synthetic)
    return r


# --------------------------------------------------------------------------
# data_health blockers must actually fire
# --------------------------------------------------------------------------

def test_clean_corpus_has_no_blockers(data_root, capsys):
    health = _load("data_health_a", "tools/data_health.py")
    land_table([_visit_row(f"e{i}") for i in range(5)],
               table="gfw_port_visits", key_fields=("event_id",),
               day_field="start_time")
    assert health.main() == 0
    assert "NOT DEMO-READY" not in capsys.readouterr().out


def test_missing_h3_is_a_blocker(data_root, capsys):
    """Without res 6 the ingest and fusion tables cannot be joined at all."""
    health = _load("data_health_b", "tools/data_health.py")
    r = _visit_row("e1")
    del r["h3_r6"]
    land_table([r], table="gfw_port_visits", key_fields=("event_id",),
               day_field="start_time")
    assert health.main() == 1
    out = capsys.readouterr().out
    assert "H3 coverage" in out and "restamp_h3" in out


def test_missing_provenance_is_a_blocker(data_root, capsys):
    health = _load("data_health_c", "tools/data_health.py")
    r = _visit_row("e1")
    r["pipeline_version"] = None
    land_table([r], table="gfw_port_visits", key_fields=("event_id",),
               day_field="start_time")
    assert health.main() == 1
    assert "provenance envelope" in capsys.readouterr().out


def test_flag_and_source_disagreement_is_a_blocker(data_root, capsys):
    """Two markers for one fact are only safer if they cannot drift.

    `stamp_envelope` refuses to produce this, so a row like it means something
    wrote the table directly — and every real-vs-synthetic split downstream is
    then wrong in a way no row count reveals.
    """
    health = _load("data_health_d", "tools/data_health.py")
    r = _visit_row("e1")
    r["is_synthetic"] = True          # source_id still says gfw-events
    land_table([r], table="gfw_port_visits", key_fields=("event_id",),
               day_field="start_time")
    assert health.main() == 1
    assert "flag/source disagreement" in capsys.readouterr().out


def test_missing_raw_store_is_a_blocker(tmp_path, monkeypatch, capsys):
    """No raw means nothing downstream is reproducible (CLAUDE.md §4.2)."""
    monkeypatch.setattr(landing.cfg, "data_root", tmp_path)
    health = _load("data_health_e", "tools/data_health.py")
    land_table([_visit_row("e1")], table="gfw_port_visits",
               key_fields=("event_id",), day_field="start_time")
    assert health.main() == 1
    assert "raw store missing" in capsys.readouterr().out


def test_multi_year_span_warns_but_does_not_block(data_root, capsys):
    """It is a presentation hazard, not a falsehood — so it must not block.

    Whether these spans are genuine long stays over-sampled by an overlap query
    or something else is open (ADR-020). Blocking on an open question would
    train people to pass the flag.
    """
    health = _load("data_health_f", "tools/data_health.py")
    land_table([_visit_row("long", hours=24 * 843)] +
               [_visit_row(f"e{i}") for i in range(3)],
               table="gfw_port_visits", key_fields=("event_id",),
               day_field="start_time")
    assert health.main() == 0
    out = capsys.readouterr().out
    assert "multi-year spans" in out
    assert "843" in out


def test_round_row_count_is_flagged_as_a_possible_cap(data_root, capsys):
    """"3,000 port visits in the Arabian Sea" is a claim about a page limit."""
    health = _load("data_health_g", "tools/data_health.py")
    land_table([_visit_row(f"e{i}") for i in range(1000)],
               table="gfw_port_visits", key_fields=("event_id",),
               day_field="start_time")
    assert health.main() == 0
    assert "possible result cap" in capsys.readouterr().out


def test_synthetic_rows_are_excluded_from_the_real_checks(data_root, capsys):
    """A synthetic row must never be able to make real data look healthy."""
    health = _load("data_health_h", "tools/data_health.py")
    bad = _visit_row("real-1")
    del bad["h3_r6"]
    land_table([bad, _visit_row("syn-1", synthetic=True, confidence=None)],
               table="gfw_port_visits", key_fields=("event_id",),
               day_field="start_time")
    assert health.main() == 1, "the real row's missing cell must still blocker"
    out = capsys.readouterr().out
    assert "1 real, 1 synthetic" in out


# --------------------------------------------------------------------------
# the length-bias filter
# --------------------------------------------------------------------------

def _raw_window(root, w0: str, w1: str) -> None:
    d = root / "raw" / "gfw-events" / "day=2026-07-30"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"port_visits_{w0}_{w1}.json").write_text(json.dumps([]))


def test_query_window_is_recovered_from_the_raw_filenames(data_root):
    profile = _load("corpus_profile_a", "tools/corpus_profile.py")
    _raw_window(data_root, "20260530", "20260731")
    win = profile.query_window()
    assert win is not None
    assert win[0] == datetime(2026, 5, 30, tzinfo=timezone.utc)
    assert win[1] == datetime(2026, 7, 31, tzinfo=timezone.utc)


def test_no_raw_means_no_window_rather_than_a_guess(data_root):
    """A guessed window would silently produce a wrong de-biased figure."""
    profile = _load("corpus_profile_b", "tools/corpus_profile.py")
    assert profile.query_window() is None


def test_containment_filter_removes_the_length_bias(data_root, capsys):
    """The straddlers carry the whole tail; the contained subset must not.

    45 ordinary calls inside the window plus 6 running back to 2012 — roughly
    the 13% straddling share the real corpus shows. Unfiltered, the p95 lands
    in the tail. Contained, it must not, and that difference is the entire fix.
    """
    profile = _load("corpus_profile_c", "tools/corpus_profile.py")
    _raw_window(data_root, "20260530", "20260731")

    rows = [_visit_row(f"e{i}",
                       start=datetime(2026, 6, 1, tzinfo=timezone.utc)
                       + timedelta(days=i % 20),
                       hours=18.0, dwell_hours=18.0) for i in range(45)]
    rows += [_visit_row(f"straddler{i}",
                        start=datetime(2012, 1, 4, tzinfo=timezone.utc),
                        hours=24 * 5267, dwell_hours=24.0 * 5267)
             for i in range(6)]
    land_table(rows, table="gfw_port_visits", key_fields=("event_id",),
               day_field="start_time")
    land_table([_visit_row("id1")], table="gfw_vessel_identity",
               key_fields=("event_id",), day_field="start_time")

    built = profile.build_profile()
    dwell = built["distributions"]["port_call_dwell_hours"]
    span = built["distributions"]["port_visit_span_hours"]

    assert dwell["n_rows"] == 45, "the straddlers must be excluded"
    assert dwell["quantiles"]["0.95"] == pytest.approx(18.0)
    assert "window-contained" in dwell["source_table"]

    # The unfiltered span is still recorded, and still carries the tail — the
    # point is that the two are separately visible, not that the tail is hidden.
    assert span["n_rows"] == 51
    assert span["quantiles"]["0.95"] > 24 * 100
    assert "length-biased" in span["source_table"]


def test_without_a_window_the_profiler_says_it_cannot_debias(data_root, capsys):
    """Silently falling back to the biased figure is the failure to avoid."""
    profile = _load("corpus_profile_d", "tools/corpus_profile.py")
    land_table([_visit_row("e1", dwell_hours=18.0)], table="gfw_port_visits",
               key_fields=("event_id",), day_field="start_time")
    profile.build_profile()
    out = capsys.readouterr().out
    assert "CANNOT be de-biased" in out


# --------------------------------------------------------------------------
# the OFAC lookup must never report "0" when it means "I did not run"
# --------------------------------------------------------------------------

def test_ofac_lookup_explains_a_missing_database(tmp_path, monkeypatch):
    """"Zero designated vessels" and "the lookup failed" are different facts.

    The first version returned a bare `set()` on every failure path, so the
    profiler printed `0 vessel IMO(s)` on a machine holding 1,516 designated
    vessels and the collision guard ran against a denominator of 121 while
    reporting success. This is the same failure family as the green `doctor`
    that masked three separate faults (STATE.md): a check that cannot tell
    absence from breakage.
    """
    from maritime_isr import config
    from maritime_isr.ingest.ofac_lookup import ofac_snapshot

    monkeypatch.setattr(config.cfg, "data_root", tmp_path)
    snap = ofac_snapshot()
    assert not snap.ok
    assert snap.imos == set()
    assert "UNAVAILABLE" in snap.describe()
    # The reason has to be actionable, not just present.
    assert "duckdb" in snap.reason.lower() or "database" in snap.reason.lower()


def test_ofac_lookup_names_the_tables_it_looked_for(tmp_path, monkeypatch):
    """A miss must say what it tried, so a renamed table is a one-line fix."""
    duckdb = pytest.importorskip("duckdb")
    from maritime_isr import config
    from maritime_isr.ingest import ofac_lookup

    monkeypatch.setattr(config.cfg, "data_root", tmp_path)
    con = duckdb.connect(str(tmp_path / "misr.duckdb"))
    con.execute("CREATE TABLE something_else (a INTEGER)")
    con.close()

    snap = ofac_lookup.ofac_snapshot()
    assert not snap.ok
    assert "ofac_sdn" in snap.reason, "must name what it looked for"
    assert "something_else" in snap.reason, "must name what it found"


def test_ofac_lookup_reads_imos_out_of_free_text(tmp_path, monkeypatch):
    """OFAC has no IMO column; the number is regexed out of `remarks`."""
    duckdb = pytest.importorskip("duckdb")
    from maritime_isr import config
    from maritime_isr.ingest import ofac_lookup

    monkeypatch.setattr(config.cfg, "data_root", tmp_path)
    con = duckdb.connect(str(tmp_path / "misr.duckdb"))
    con.execute("CREATE TABLE ofac_sdn (sdn_type VARCHAR, remarks VARCHAR)")
    con.execute("INSERT INTO ofac_sdn VALUES "
                "('vessel', 'Vessel Registration Identification IMO 9164263'),"
                "('individual', 'DOB 1970; passport 1234567')")
    con.close()

    snap = ofac_lookup.ofac_snapshot()
    assert snap.ok, snap.reason
    assert "9164263" in snap.imos
    assert snap.table == "ofac_sdn"


def test_ofac_lookup_falls_back_when_the_type_column_is_named_oddly(
        tmp_path, monkeypatch):
    """Better to scan the whole table than to return zero and call it a count.

    A person's remarks will not carry a valid IMO check digit, so the vessel
    filter is an optimisation. Silently returning nothing because the type
    column was renamed is the outcome to avoid.
    """
    duckdb = pytest.importorskip("duckdb")
    from maritime_isr import config
    from maritime_isr.ingest import ofac_lookup

    monkeypatch.setattr(config.cfg, "data_root", tmp_path)
    con = duckdb.connect(str(tmp_path / "misr.duckdb"))
    con.execute("CREATE TABLE ofac_sdn (kind_of_thing VARCHAR, remarks VARCHAR)")
    con.execute("INSERT INTO ofac_sdn VALUES ('boat', 'IMO 9164263')")
    con.close()

    snap = ofac_lookup.ofac_snapshot()
    assert snap.ok, snap.reason
    assert "9164263" in snap.imos


def test_read_only_connect_works_at_all(tmp_path, monkeypatch):
    """`connect(read_only=True)` must return a connection, not raise.

    It raised on every call because `_register_views` used a plain
    `CREATE OR REPLACE VIEW`, which writes to the database file and is refused
    in read-only mode. Every read-only consumer therefore got an exception, and
    the one that swallowed it published `0 vessel IMO(s)` on a corpus holding
    1,516 designated vessels. The views are TEMP now.
    """
    duckdb = pytest.importorskip("duckdb")
    from maritime_isr import config, db

    monkeypatch.setattr(config.cfg, "data_root", tmp_path)
    duckdb.connect(str(config.cfg.duckdb_path())).close()   # create the file

    con = db.connect(read_only=True)
    try:
        assert con.execute("SELECT 1").fetchone() == (1,)
    finally:
        con.close()
