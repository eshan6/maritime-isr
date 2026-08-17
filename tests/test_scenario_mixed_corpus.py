"""The case the sandbox could never reach: real and synthetic in one partition.

Every other scenario test ran against tables holding **only** synthetic rows,
because the real corpus lives on the operator's laptop and was never in the
build environment. That leaves the interesting path unexercised: on his machine,
scenario rows land into day partitions that **already contain real rows**, so
`land_table` reads the existing file, merges, and rewrites it through
`pa.Table.from_pylist`.

Three ways that can go wrong, and each has a test here:

  1. **Arrow type conflict.** A column holding a string in real rows and an int
     in synthetic ones raises on conversion — and the write that fails is a
     write to a partition containing real data.
  2. **Pre-migration partitions.** Real rows landed before `is_synthetic`
     existed have no such column. They must read back as real, not as null, and
     must survive the merge unchanged.
  3. **`clear()` damaging real data.** It rewrites partitions to drop synthetic
     rows. Real rows must come back byte-identical, because conformed is only
     regenerable by re-deriving from raw.

Real rows here are *fabricated to the shape the connectors produce*, read off
`ingest/gfw_events.py` and `ingest/gfw_vessels.py`. That is a stand-in, not the
real thing, and it is exactly as good as my reading of those modules — which is
why `tools/corpus_profile.py` now captures the true schemas and null rates. When
that profile lands, these fixtures should be checked against it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from maritime_isr.ingest import landing
from maritime_isr.ingest.landing import (SYNTHETIC_SOURCE_ID, land_table,
                                         read_table, split_real_synthetic,
                                         stamp_envelope, stamp_h3)

DAY = "2026-06-18"
T = datetime(2026, 6, 18, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    """Point the landing layer at a throwaway data root."""
    monkeypatch.setattr(landing.cfg, "data_root", tmp_path)
    return tmp_path


def _real_encounter_row(i: int) -> dict:
    """A row shaped like `ingest/gfw_events.py` produces, WITHOUT is_synthetic.

    Deliberately omits the flag: partitions landed before the migration do not
    have the column, and that is the state of every real partition on the
    operator's machine today.
    """
    return {
        "event_id": f"real-enc-{i}",
        "event_kind": "encounters",
        "event_type": "ENCOUNTER",
        "start_time": T + timedelta(hours=i),
        "end_time": T + timedelta(hours=i + 3),
        "duration_hours": 3.0,
        "lat": 19.5 + i * 0.01,
        "lon": 67.2 + i * 0.01,
        # GFW's ssvid and imo arrive as STRINGS — the type most likely to
        # collide with a generator that used ints.
        "vessel_id": f"gfwid{i:04d}",
        "mmsi": f"41910000{i}",
        "imo": "9164263",
        "ship_name": "REAL VESSEL",
        "flag": "IND",
        "vessel_type": "CARGO",
        "counterpart_vessel_id": f"gfwid9{i:03d}",
        "counterpart_mmsi": f"41920000{i}",
        "counterpart_name": "REAL COUNTERPART",
        "counterpart_flag": "PAN",
        "encounter_type": "CARRIER_FISHING",
        "start_distance_from_port_km": 120.5,
        "end_distance_from_shore_km": 88.25,
        "gfw_confidence_raw": "4",
        "source_id": "gfw-events",
        "source_ref": f"real-enc-{i}",
        "acquired_at": T + timedelta(hours=i),
        "ingested_at": T,
        "pipeline_version": "abc1234",
        "confidence": None,
        "h3_r4": "84604b9ffffffff", "h3_r6": "86604ba17ffffff",
        "h3_r7": "87604ba17ffffff", "h3_r8": "88604ba171fffff",
        "h3_r9": "89604ba1717ffff",
    }


def _land_real_partition(root, table: str, rows: list[dict]) -> None:
    """Write a pre-migration real partition directly, bypassing land_table."""
    d = root / "conformed" / table / f"day={DAY}"
    d.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), d / "part.parquet",
                   compression="zstd")


def _synthetic_encounter_row(i: int) -> dict:
    row = {
        "event_id": f"syn-enc-{i}",
        "event_kind": "encounters",
        "event_type": "ENCOUNTER",
        "start_time": T + timedelta(hours=i),
        "end_time": T + timedelta(hours=i + 2),
        "duration_hours": 2.0,
        "lat": 18.2 + i * 0.01,
        "lon": 63.4 + i * 0.01,
        "vessel_id": "vessel:spine",
        "mmsi": "999000000",
        "imo": "1000009",
        "ship_name": "GULF TRADER",
        "flag": "PAN",
        "vessel_type": "Suezmax",
        "counterpart_vessel_id": "vessel:receiver_alpha",
        "counterpart_mmsi": "999000001",
        "counterpart_name": "OCEAN VOYAGER",
        "counterpart_flag": "COM",
        # Columns the real rows do not have at all.
        "min_separation_m": 62.4,
        "mean_separation_m": 71.8,
    }
    stamp_envelope(row, source_id=SYNTHETIC_SOURCE_ID,
                   source_ref=f"syn-enc-{i}", acquired_at=T,
                   is_synthetic=True)
    return stamp_h3(row)


# --------------------------------------------------------------------------

def test_pre_migration_real_rows_read_back_as_real(data_root):
    """A partition with no is_synthetic column must not read as null."""
    _land_real_partition(data_root, "gfw_encounters",
                         [_real_encounter_row(i) for i in range(5)])
    rows = read_table("gfw_encounters")
    assert len(rows) == 5
    assert all(r["is_synthetic"] is False for r in rows), (
        "rows landed before the migration must default to real on read — "
        "leaving them null pushes the decision onto every call site")
    real, syn = split_real_synthetic(rows)
    assert (len(real), len(syn)) == (5, 0)


def test_synthetic_rows_merge_into_a_real_partition(data_root):
    """The path the operator will actually hit, and the one never exercised."""
    _land_real_partition(data_root, "gfw_encounters",
                         [_real_encounter_row(i) for i in range(5)])

    written = land_table([_synthetic_encounter_row(i) for i in range(3)],
                         table="gfw_encounters", key_fields=("event_id",),
                         day_field="start_time")
    # This call landed 3 rows and the partition holds 8. Both numbers matter and
    # they are not the same number: asserting 8 here used to be the only check,
    # which quietly encoded "landed" as "partition size after the merge" — the
    # reading that let an import of 5 territorial seas announce `landed 68`.
    assert written[DAY] == 3, f"this call landed 3 rows, got {written}"
    assert written.partition_rows[DAY] == 8, (
        f"the 5 real rows must survive the merge, got {written.partition_rows}")
    assert written.replaced[DAY] == 0, "synthetic keys must not collide with real ones"

    rows = read_table("gfw_encounters")
    real, syn = split_real_synthetic(rows)
    assert (len(real), len(syn)) == (5, 3)

    # The real rows must be untouched, values and all.
    by_id = {r["event_id"]: r for r in real}
    for i in range(5):
        r = by_id[f"real-enc-{i}"]
        assert r["mmsi"] == f"41910000{i}"
        assert r["imo"] == "9164263"
        assert r["flag"] == "IND"
        assert r["source_id"] == "gfw-events"
        assert r["duration_hours"] == 3.0
        # A column only the synthetic rows carry must be null here, not absent
        # or defaulted to something that looks like a measurement.
        assert r.get("min_separation_m") is None

    for s in syn:
        assert s["source_id"] == SYNTHETIC_SOURCE_ID
        assert s["is_synthetic"] is True


def test_a_type_conflict_between_real_and_synthetic_is_loud(data_root):
    """If a column's type disagrees, the failure must be an error, not silence.

    This is the specific mechanism that would break a merge on the operator's
    machine: GFW lands `mmsi` as a string, and a generator that emitted an int
    would raise here rather than quietly coercing. The test pins the behaviour
    so a future change cannot turn it into a silent cast.
    """
    _land_real_partition(data_root, "gfw_encounters",
                         [_real_encounter_row(0)])
    bad = _synthetic_encounter_row(0)
    bad["mmsi"] = 999000000            # int where the real rows hold a string

    with pytest.raises(Exception) as exc:
        land_table([bad], table="gfw_encounters", key_fields=("event_id",),
                   day_field="start_time")

    # This assertion used to accept `"int" in str(exc)`, which Arrow's bare
    # "Expected bytes, got a 'int' object" satisfies while naming neither the
    # table nor the column. It passed green while the operator got a message he
    # could do nothing with. The column name is the whole point of the check.
    msg = str(exc.value)
    assert "mmsi" in msg, f"a type conflict must name the column; got {msg}"
    assert "gfw_encounters" in msg, f"...and the table; got {msg}"
    assert "str" in msg and "int" in msg, (
        f"...and both sides of the disagreement; got {msg}")


def test_clear_removes_synthetic_and_leaves_real_intact(data_root):
    """`clear()` rewrites partitions — real rows must survive byte-identical."""
    from maritime_isr.scenario.run import clear

    _land_real_partition(data_root, "gfw_encounters",
                         [_real_encounter_row(i) for i in range(5)])
    land_table([_synthetic_encounter_row(i) for i in range(3)],
               table="gfw_encounters", key_fields=("event_id",),
               day_field="start_time")

    before = {r["event_id"]: dict(r) for r in read_table("gfw_encounters")
              if not r["is_synthetic"]}
    assert len(before) == 5

    removed = clear()
    assert removed.get("gfw_encounters") == 3, removed

    after = {r["event_id"]: dict(r) for r in read_table("gfw_encounters")}
    assert set(after) == set(before), "clear() lost or added real rows"
    for k, v in before.items():
        assert after[k] == v, (
            f"clear() altered real row {k}: {before[k]} -> {after[k]}. "
            f"Conformed is only regenerable by re-deriving from raw.")


def test_clear_on_a_partition_of_only_real_rows_is_a_no_op(data_root):
    from maritime_isr.scenario.run import clear
    _land_real_partition(data_root, "gfw_encounters",
                         [_real_encounter_row(i) for i in range(4)])
    path = data_root / "conformed" / "gfw_encounters" / f"day={DAY}" / "part.parquet"
    mtime = path.stat().st_mtime
    removed = clear()
    assert "gfw_encounters" not in removed
    assert path.exists()
    assert path.stat().st_mtime == mtime, (
        "clear() rewrote a partition containing no synthetic rows — it must "
        "leave real-only partitions alone entirely")


def test_analytics_tools_exclude_synthetic_rows():
    """The quotable numbers must stay real-only (hard ban on blending).

    `review_matches.py` prints the 98-vessel figure that goes into STATE.md and
    external material. After the generator runs, the same table holds scenario
    matches; counting them into that figure turns a measured finding into a
    fabricated one.
    """
    import ast
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    for name in ("review_matches.py", "graph_report.py",
                 "analytic_rename_gap.py"):
        src = (repo / "tools" / name).read_text(encoding="utf-8")
        tree = ast.parse(src)
        # `read_table` may appear exactly once: inside the real_rows helper.
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                 and n.func.id == "read_table"]
        assert len(calls) <= 1, (
            f"tools/{name} calls read_table {len(calls)} times — every quotable "
            f"count must go through real_rows() so synthetic rows are excluded")
        assert "def real_rows(" in src, (
            f"tools/{name} has no real_rows() helper; it would blend scenario "
            f"rows into a quoted figure")


def test_the_generator_conforms_to_the_real_corpus_rather_than_breaking(data_root):
    """`scenario generate` died on the operator's laptop and took everything.

    His corpus holds real GFW events; the generator emitted a column with a
    different Python type; pyarrow refused the merged partition with `Expected
    bytes, got a 'int' object`. Generation aborted, so there was no AIS corpus,
    which meant no radar correlation and no graph — three empty views, one
    cause.

    The scenario is a connector (CLAUDE.md §4.5) and must map into the
    canonical schema. The types already landed are that schema, observed.
    """
    from maritime_isr.scenario.land import conform_to_landed_types

    _land_real_partition(data_root, "gfw_encounters", [_real_encounter_row(0)])

    syn = _synthetic_encounter_row(0)
    syn["mmsi"] = 999000000                  # int where the real rows hold str

    changed = conform_to_landed_types([syn], "gfw_encounters")
    assert "mmsi" in changed, f"mmsi should have been conformed; got {changed}"
    assert syn["mmsi"] == "999000000", "conforming must preserve the value"

    # And the merge that used to abort now completes.
    land_table([syn], table="gfw_encounters", key_fields=("event_id",),
               day_field="start_time")
    rows = read_table("gfw_encounters")
    real, synth = split_real_synthetic(rows)
    assert (len(real), len(synth)) == (1, 1)


def test_conforming_is_a_no_op_without_a_landed_corpus(data_root):
    """On a bare machine the scenario's own types become the schema."""
    from maritime_isr.scenario.land import conform_to_landed_types

    syn = _synthetic_encounter_row(0)
    syn["mmsi"] = 999000000
    assert conform_to_landed_types([syn], "gfw_encounters") == []
    assert syn["mmsi"] == 999000000, "nothing to conform to, nothing changed"


def test_an_irreconcilable_type_still_raises_with_the_column_named(data_root):
    """Conforming must not become a silent cast for things it cannot map."""
    from maritime_isr.scenario.land import conform_to_landed_types

    _land_real_partition(data_root, "gfw_encounters", [_real_encounter_row(0)])
    syn = _synthetic_encounter_row(0)
    syn["duration_hours"] = "not-a-number"   # float column, unparseable string

    conform_to_landed_types([syn], "gfw_encounters")
    with pytest.raises(Exception) as exc:
        land_table([syn], table="gfw_encounters", key_fields=("event_id",),
                   day_field="start_time")
    assert "duration_hours" in str(exc.value), str(exc.value)
