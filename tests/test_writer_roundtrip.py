"""End-to-end: write canonical AIS rows -> Parquet partition -> DuckDB view.

Proves the storage path the whole prototype stands on, with dedup verified
(re-writing identical rows must not duplicate). Uses a tmp data root.
"""
import os
from datetime import datetime, timezone


def test_ais_write_and_query(tmp_path, monkeypatch):
    monkeypatch.setenv("MISR_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("MISR_STORE_BACKEND", "local")
    # reload config singletons bound to new env
    import importlib
    import maritime_isr.config as cfgmod
    importlib.reload(cfgmod)
    import maritime_isr.store as storemod
    importlib.reload(storemod)
    import maritime_isr.writer as wr
    importlib.reload(wr)
    import maritime_isr.db as dbmod
    importlib.reload(dbmod)

    ts = datetime(2026, 7, 1, 12, 30, tzinfo=timezone.utc)
    row = {"mmsi": 419000001, "lat": 15.0, "lon": 68.0, "sog": 12.0, "cog": 90.0,
           "heading": 91.0, "timestamp": ts, "msg_type": 1, "receiver_source": "test",
           "source_id": "test", "source_ref": "419000001", "acquired_at": ts,
           "ingested_at": ts, "pipeline_version": "deadbeef", "confidence": None}

    w1 = wr.write_position_reports([dict(row)], store="ais")
    assert sum(w1.values()) == 1
    # rewrite identical row -> still 1 (dedup)
    w2 = wr.write_position_reports([dict(row)], store="ais")
    assert sum(w2.values()) == 1

    con = dbmod.connect()
    n = con.execute("SELECT count(*) FROM ais").fetchone()[0]
    assert n == 1
    got = con.execute("SELECT mmsi, h3_r7 FROM ais").fetchone()
    assert got[0] == 419000001 and got[1]  # H3 stamped by writer
