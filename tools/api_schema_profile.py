"""Dump a full, sanitised schema+stats profile of the landed corpus for the API build.

**Run this on the machine that holds the real data (the laptop), then commit the
output.** It is the contract the Phase 6 API is built against: the sandbox where
the API code is written has no real data (that is permanent — see STATE.md and
ADR-013), so every schema assumption in the API is provisional until this profile
lands and confirms it.

    python tools/api_schema_profile.py

*Success* looks like one block per table ending in
`wrote data_profiles/api_schema_profile.json`.

For every table — conformed Parquet, DuckDB registry, and the object graph — it
records:

  * column names and concrete types,
  * total row count, split real vs synthetic (ADR-019 — never a blended total),
  * null rate per column,
  * min/max for date and numeric columns,
  * distinct-value counts (and the top few) for low-cardinality categoricals,
  * 3-5 sanitised sample rows.

**Sanitisation is not cosmetic.** Anything that identifies a specific vessel,
person or company — MMSI, IMO, ship name, call sign, owner, address — is redacted
to a shape token (`<str:11>`, `<imo:7digits>`) that preserves the *shape* the API
needs without carrying the value. Places, flags, vessel types, programs, ports
and coordinates are kept, because they are what the map and the filters render
and none of them fingerprints a hull.

**Absence is reported, never disguised as zero (ADR-021).** A missing store or an
unreadable table produces an explicit `{"ok": false, "reason": ...}` block, so a
zero in this profile is always a measurement and never a silent breakage.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from maritime_isr.config import PIPELINE_VERSION, cfg, repo_root   # noqa: E402

OUT_PATH = repo_root() / "data_profiles" / "api_schema_profile.json"

#: How many sample rows to emit per table.
N_SAMPLES = 5
#: A column with at most this many distinct values is treated as a categorical
#: and gets a value-count breakdown (unless it is an identifier).
CATEGORICAL_MAX_DISTINCT = 40

# --------------------------------------------------------------------------
# sanitisation
# --------------------------------------------------------------------------

#: Substrings that mark a column as identifying a specific hull, person or
#: company. A column whose lower-cased name contains any of these is redacted in
#: sample rows and never gets a value-count breakdown. Kept deliberately narrow:
#: `flag`, `vessel_type`, `vessel_class`, `program`, `port_name`, `top_destination`
#: are NOT here because they describe a category or a place, not an identity.
_IDENT_TOKENS = (
    "mmsi", "imo", "call_sign", "callsign",
    "ship_name", "normalised_name", "vessel_id",
    "counterpart_name", "counterpart_vessel", "counterpart_mmsi",
    "owner", "address", "registered_agent", "successor",
    "ofac_name", "entity_id", "target_entity",
    "event_id", "detection_id", "port_visit_id", "scene_id",
    "ent_num", "entry_id", "org_id", "receiver",
)
#: Exact column names that are identifying even though they miss the tokens above
#: (short, generic names on the scenario org/sanctions tables).
_IDENT_EXACT = {"name", "src", "dst", "notes", "entity_ids", "source_ref"}


def is_identifying(column: str) -> bool:
    c = column.lower()
    if c in _IDENT_EXACT:
        return True
    return any(tok in c for tok in _IDENT_TOKENS)


def _redact_scalar(column: str, value):
    """Replace an identifying value with a shape token; pass everything else through."""
    if value is None:
        return None
    if not is_identifying(column):
        # Coordinates are not identifying but are pinned coarsely so a sample
        # row cannot be reverse-geocoded to one berth.
        cl = column.lower()
        if cl in ("lat", "lon") or cl.endswith("_lat") or cl.endswith("_lon"):
            try:
                return round(float(value), 1)
            except (TypeError, ValueError):
                return value
        return _jsonable(value)
    s = str(value)
    if s.isdigit():
        return f"<{len(s)}digits>"
    return f"<str:{len(s)}>"


def _jsonable(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return f"<bytes:{len(value)}>"
    return value


def _sanitise_row(row: dict) -> dict:
    return {k: _redact_scalar(k, v) for k, v in row.items()}


# --------------------------------------------------------------------------
# duckdb helpers — one engine profiles both Parquet globs and DuckDB tables
# --------------------------------------------------------------------------

def _profile_relation(con, relation: str, *, columns: list[tuple[str, str]]) -> dict:
    """Profile a DuckDB relation (a table name or a `read_parquet(...)` expr).

    `columns` is a list of (name, duckdb_type). Returns the per-table block.
    """
    n_rows = con.execute(f"SELECT count(*) FROM {relation}").fetchone()[0]
    block: dict = {"n_rows": int(n_rows), "columns": {}}

    # real vs synthetic split, only if the flag column is present
    colnames = {c for c, _ in columns}
    if "is_synthetic" in colnames:
        rows = dict(con.execute(
            f"SELECT COALESCE(is_synthetic, FALSE), count(*) "
            f"FROM {relation} GROUP BY 1").fetchall())
        block["by_synthetic"] = {
            "real": int(rows.get(False, 0)) + int(rows.get(0, 0)),
            "synthetic": int(rows.get(True, 0)) + int(rows.get(1, 0)),
        }

    for name, dtype in columns:
        q = f'"{name}"'
        col: dict = {"type": dtype}
        if n_rows:
            n_null = con.execute(
                f"SELECT count(*) FROM {relation} WHERE {q} IS NULL").fetchone()[0]
            col["null_rate"] = round(n_null / n_rows, 4)
        else:
            col["null_rate"] = None

        dl = dtype.lower()
        is_num = any(t in dl for t in
                     ("int", "double", "float", "decimal", "hugeint", "real"))
        is_time = any(t in dl for t in ("timestamp", "date", "time"))
        identifying = is_identifying(name)

        if n_rows and (is_num or is_time) and not identifying:
            lo, hi = con.execute(
                f"SELECT min({q}), max({q}) FROM {relation}").fetchone()
            col["min"] = _jsonable(lo)
            col["max"] = _jsonable(hi)

        if n_rows and not identifying and not is_time:
            ndist = con.execute(
                f"SELECT approx_count_distinct({q}) FROM {relation}").fetchone()[0]
            col["approx_distinct"] = int(ndist)
            if ndist and ndist <= CATEGORICAL_MAX_DISTINCT and not is_num:
                top = con.execute(
                    f"SELECT {q} AS v, count(*) AS n FROM {relation} "
                    f"WHERE {q} IS NOT NULL GROUP BY 1 ORDER BY n DESC LIMIT 12"
                ).fetchall()
                col["top_values"] = {str(v): int(n) for v, n in top}
        elif n_rows and identifying:
            col["approx_distinct"] = int(con.execute(
                f"SELECT approx_count_distinct({q}) FROM {relation}").fetchone()[0])
            col["identifying"] = True

        block["columns"][name] = col

    # sanitised samples
    if n_rows:
        cols_csv = ", ".join(f'"{c}"' for c, _ in columns)
        sample = con.execute(
            f"SELECT {cols_csv} FROM {relation} USING SAMPLE {N_SAMPLES} ROWS"
        ).fetchall()
        names = [c for c, _ in columns]
        block["samples"] = [
            _sanitise_row(dict(zip(names, r))) for r in sample]
    return block


def _describe_columns(con, relation: str) -> list[tuple[str, str]]:
    rows = con.execute(f"DESCRIBE SELECT * FROM {relation}").fetchall()
    return [(r[0], r[1]) for r in rows]


# --------------------------------------------------------------------------
# the three stores
# --------------------------------------------------------------------------

def profile_conformed(con) -> dict:
    """Every Parquet table under data/conformed/<table>/day=*/part.parquet."""
    root = cfg.data_root / "conformed"
    out: dict = {}
    if not root.is_dir():
        return {"_store": {"ok": False,
                           "reason": f"no conformed store at {root}"}}
    tables = sorted(p.name for p in root.iterdir() if p.is_dir())
    for t in tables:
        glob = str(root / t / "day=*" / "part.parquet")
        # union_by_name + a null-typed-column-tolerant read: this is exactly the
        # all-null-column landmine reconcile_null_columns exists for (ADR-020).
        rel = (f"read_parquet('{glob}', union_by_name=true)")
        try:
            cols = _describe_columns(con, rel)
            out[t] = _profile_relation(con, rel, columns=cols)
            print(f"  conformed {t:<26} "
                  f"rows={out[t]['n_rows']:>7,} cols={len(cols)}")
        except Exception as e:                                    # noqa: BLE001
            out[t] = {"ok": False, "reason": f"unreadable: {e}"}
            print(f"  conformed {t:<26} UNREADABLE — {e}")
    return out


def profile_duckdb(con) -> dict:
    """DuckDB-resident tables: sanctions registries + scene catalog."""
    have = {r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables").fetchall()}
    wanted = ("ofac_sdn", "un_consolidated", "eu_consolidated",
              "wpi_ports", "scene_catalog", "registry_snapshots")
    out: dict = {}
    for t in wanted:
        if t not in have:
            out[t] = {"ok": False,
                      "reason": "table not present in misr.duckdb "
                                "(connector has not run on this machine)"}
            print(f"  duckdb    {t:<26} absent")
            continue
        try:
            cols = _describe_columns(con, t)
            out[t] = _profile_relation(con, t, columns=cols)
            print(f"  duckdb    {t:<26} rows={out[t]['n_rows']:>7,} "
                  f"cols={len(cols)}")
        except Exception as e:                                    # noqa: BLE001
            out[t] = {"ok": False, "reason": f"unreadable: {e}"}
            print(f"  duckdb    {t:<26} UNREADABLE — {e}")
    return out


def profile_graph() -> dict:
    """Object graph: node/edge type breakdowns, real/synthetic split, and one
    sanitised sample neighbourhood so the API's traversal shape is verifiable."""
    from maritime_isr.config import DATA_ROOT, GRAPH_DB_NAME
    from maritime_isr.graph import GraphStore

    gpath = DATA_ROOT / GRAPH_DB_NAME
    if not gpath.exists():
        return {"ok": False,
                "reason": f"no graph at {gpath} — run graph populate first"}
    g = GraphStore(gpath)
    try:
        out: dict = {
            "counts_by_synthetic": g.counts_by_synthetic(),
            "node_types": {
                "real": g.n_nodes_by_type(is_synthetic=False),
                "synthetic": g.n_nodes_by_type(is_synthetic=True),
            },
            "edge_types": {
                "real": g.n_edges_by_type(is_synthetic=False),
                "synthetic": g.n_edges_by_type(is_synthetic=True),
            },
            "ontology_version": g.ontology_version(),
            "edge_registry": g.edge_registry(),
        }
        # a sample neighbourhood: pick the vessel with the most out-edges
        row = g._con.execute(
            "SELECT src, count(*) n FROM edges WHERE src LIKE 'vessel:%' "
            "GROUP BY src ORDER BY n DESC LIMIT 1").fetchone()
        if row:
            seed = row[0]
            edges = g.edges(seed, direction="out")
            out["sample_neighbourhood"] = {
                "seed_node_type": (g.node(seed) or {}).get("node_type"),
                "n_out_edges": len(edges),
                "edges": [
                    {"edge_type": e.edge_type,
                     "dst_type": (g.node(e.dst) or {}).get("node_type"),
                     "confidence": round(e.base_confidence, 3),
                     "t_start": e.t_start, "t_end": e.t_end,
                     "is_synthetic": e.is_synthetic}
                    for e in edges[:15]],
            }
        # alerts shape (no subject/evidence text — those identify)
        alerts = g.alerts()
        out["alerts"] = {
            "n_total": len(alerts),
            "by_synthetic": {
                "real": sum(1 for a in alerts if not a["is_synthetic"]),
                "synthetic": sum(1 for a in alerts if a["is_synthetic"]),
            },
            "by_type": _count(a["anomaly_type"] for a in alerts),
            "by_disposition": _count(a["disposition"] for a in alerts),
            "sample_evidence_shape": (
                _evidence_shape(alerts[0]["evidence"]) if alerts else None),
        }
        return out
    finally:
        g.close()


def _count(it) -> dict:
    d: dict = {}
    for v in it:
        d[str(v)] = d.get(str(v), 0) + 1
    return d


def _evidence_shape(evidence) -> object:
    """The KEYS of an evidence hop, not its values — enough to build the UI
    against, nothing that identifies a vessel."""
    if isinstance(evidence, list) and evidence:
        first = evidence[0]
        if isinstance(first, dict):
            return {"n_hops": len(evidence), "hop_keys": sorted(first.keys())}
        return {"n_hops": len(evidence), "hop_type": type(first).__name__}
    return {"n_hops": 0}


def main() -> int:
    import duckdb

    from maritime_isr.db import connect

    print("=" * 68)
    print("Maritime ISR — API schema profile")
    print("=" * 68)
    print(f"data root : {cfg.data_root}")
    print(f"duckdb    : {cfg.duckdb_path()}")
    print("-" * 68)

    # Use the project's own read-only DuckDB connection so store wiring and
    # view registration match what the API will see.
    try:
        con = connect(read_only=True)
    except (duckdb.Error, OSError):
        # no misr.duckdb yet — a fresh in-memory engine still profiles Parquet
        con = duckdb.connect()

    # DuckDB's statistics_propagation optimizer constant-folds min()/max() from
    # Parquet column statistics at plan time, and on some single-partition
    # files that path hits an internal index-out-of-range assertion (observed on
    # duckdb 1.5.x reading sanctioned_vessel_matches / scenario_sanctions). It
    # is a planner bug, not a data problem — disabling that one optimizer makes
    # min()/max() run normally at execution time. The API's read layer disables
    # it for the same reason.
    try:
        con.execute("SET disabled_optimizers='statistics_propagation'")
    except duckdb.Error:
        pass

    profile = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "pipeline_version": PIPELINE_VERSION,
        "note": "Sanitised schema+stats profile for the Phase 6 API. "
                "Identifying values are redacted to shape tokens.",
        "conformed": profile_conformed(con),
        "duckdb": profile_duckdb(con),
        "graph": profile_graph(),
    }
    con.close()

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(profile, indent=1, default=str))
    print("-" * 68)
    print(f"wrote {OUT_PATH.relative_to(repo_root())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
