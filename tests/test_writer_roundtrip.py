"""End-to-end: write canonical AIS rows -> Parquet partition -> DuckDB view.

Proves the storage path the whole prototype stands on, with dedup verified
(re-writing identical rows must not duplicate). Uses a tmp data root.

**Why this redirects the config singleton in place rather than reloading it.**
The first version of this test did `importlib.reload(maritime_isr.config)` to
pick up a tmp `MISR_DATA_ROOT`. Reloading rebinds `config.cfg` to a *new*
`Config` object, but every module that did `from ..config import cfg` — the
landing layer among them — keeps a reference to the old one. From that point on
the process has two config objects, and `monkeypatch.setattr(config.cfg,
"data_root", tmp)` in any later test silently redirects nothing: the writer
still reads the stale object and lands rows in the operator's real `data/`.
That is not hypothetical; it is how test fixtures ended up in the real zone
layer, and it only shows up in full-suite ordering because this module sorts
last.

`store`, `writer` and `db` all read `cfg.<attr>` at call time — none of them
caches a derived path at import — so there is nothing a reload buys here.
Setting the attributes on the one singleton keeps the process to exactly one
config object and lets monkeypatch undo it.
"""
from datetime import datetime, timezone


def test_ais_write_and_query(tmp_path, monkeypatch):
    import maritime_isr.config as cfgmod
    import maritime_isr.db as dbmod
    import maritime_isr.writer as wr

    monkeypatch.setattr(cfgmod.cfg, "data_root", tmp_path)
    monkeypatch.setattr(cfgmod.cfg, "store_backend", "local")

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
