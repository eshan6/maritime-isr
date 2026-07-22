"""Unit 0.4 — static registries: OFAC SDN, UN/EU consolidated, WPI ports.

Versioned snapshots, diff-on-refresh, as-of dates on every sanctions edge. Each
refresh writes a new immutable snapshot table and records a diff against the
prior snapshot (added/removed entries) — sanctions edges MUST carry as-of dates
(Phase 4 depends on this), so we never overwrite; we version.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

import requests

from ..db import connect
from ..schemas import git_sha, utcnow

OFAC_SDN_CSV = "https://www.treasury.gov/ofac/downloads/sdn.csv"
# WPI (World Port Index) and UN/EU lists are added the same way; OFAC shown as
# the reference implementation. Each source = one _snapshot_table call.


def _ensure_snapshot_meta(con):
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS registry_snapshots (
            source_id VARCHAR, as_of TIMESTAMPTZ, n_rows INTEGER,
            pipeline_version VARCHAR, PRIMARY KEY (source_id, as_of)
        )
        """
    )


def _diff(con, table: str, source_id: str, as_of) -> tuple[int, int]:
    """Compare newest two snapshots of a source; return (added, removed)."""
    versions = con.execute(
        "SELECT DISTINCT as_of FROM registry_snapshots WHERE source_id=? ORDER BY as_of DESC LIMIT 2",
        [source_id],
    ).fetchall()
    if len(versions) < 2:
        return (con.execute(f"SELECT count(*) FROM {table} WHERE as_of=?", [as_of]).fetchone()[0], 0)
    new_v, old_v = versions[0][0], versions[1][0]
    added = con.execute(
        f"SELECT count(*) FROM (SELECT ent_num FROM {table} WHERE as_of=? "
        f"EXCEPT SELECT ent_num FROM {table} WHERE as_of=?)", [new_v, old_v]
    ).fetchone()[0]
    removed = con.execute(
        f"SELECT count(*) FROM (SELECT ent_num FROM {table} WHERE as_of=? "
        f"EXCEPT SELECT ent_num FROM {table} WHERE as_of=?)", [old_v, new_v]
    ).fetchone()[0]
    return added, removed


def refresh_ofac(con) -> None:
    as_of = utcnow()
    print("[registries] fetching OFAC SDN ...")
    resp = requests.get(OFAC_SDN_CSV, timeout=120)
    resp.raise_for_status()
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS ofac_sdn (
            ent_num VARCHAR, name VARCHAR, sdn_type VARCHAR, program VARCHAR,
            as_of TIMESTAMPTZ, pipeline_version VARCHAR
        )
        """
    )
    rows = []
    reader = csv.reader(io.StringIO(resp.text))
    for r in reader:
        if len(r) < 4:
            continue
        rows.append((r[0], r[1], r[2], r[3], as_of, git_sha()))
    con.executemany(
        "INSERT INTO ofac_sdn (ent_num,name,sdn_type,program,as_of,pipeline_version) "
        "VALUES (?,?,?,?,?,?)", rows,
    )
    con.execute(
        "INSERT INTO registry_snapshots VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
        ["ofac-sdn", as_of, len(rows), git_sha()],
    )
    added, removed = _diff(con, "ofac_sdn", "ofac-sdn", as_of)
    print(f"[registries] OFAC snapshot as_of={as_of:%Y-%m-%d} rows={len(rows)} "
          f"(+{added} / -{removed} vs prior)")


def run() -> int:
    con = connect()
    _ensure_snapshot_meta(con)
    refresh_ofac(con)
    print("[registries] done. (UN/EU/WPI add here identically — versioned, diffed.)")
    return 0
