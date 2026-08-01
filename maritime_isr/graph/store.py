"""GraphStore (roadmap 4.1/4.3/4.4) — sqlite-backed object graph.

Design rules, enforced in code:
  - No naked facts: every edge carries provenance (source, source_ref,
    pipeline_version), base confidence, observed_at, and a time scope
    [t_start, t_end). Writes REJECT edges missing any of these.
  - Append-only: re-asserting an edge writes a new row with a newer
    observed_at; reads resolve to the latest assertion per (type,src,dst)
    unless history is requested. Nothing is ever mutated or deleted —
    reproducibility discipline, unchanged since Phase 0.
  - Decay on READ: confidence(t) = base × 0.5^((t-observed_at)/half_life)
    for state edges. Functional, idempotent, no batch job to forget, and
    yesterday's query is reproducible by passing yesterday's clock.
  - Ontology is data: types live in the ontology table; migration = insert
    + version bump. add_edge validates against the LIVE registry, so a
    migrated type works immediately with zero recompute of existing rows.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass, field

from ..config import DATA_ROOT, GRAPH_DB_NAME, PIPELINE_VERSION
from .ontology import EDGE_TYPES_V1, NODE_TYPES_V1, ONTOLOGY_VERSION, validate_edge

#: The source that marks a row as scenario data. Must agree with the
#: `is_synthetic` flag on every row — see `_check_synthetic_agreement`.
SYNTHETIC_SOURCE_PREFIX = "synthetic-scenario"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes(
  node_id TEXT PRIMARY KEY, node_type TEXT NOT NULL,
  props TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL, updated_at REAL NOT NULL,
  is_synthetic INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS edges(
  rowid INTEGER PRIMARY KEY AUTOINCREMENT,
  edge_type TEXT NOT NULL, src TEXT NOT NULL, dst TEXT NOT NULL,
  t_start REAL NOT NULL, t_end REAL,
  base_confidence REAL NOT NULL, observed_at REAL NOT NULL,
  source TEXT NOT NULL, source_ref TEXT NOT NULL,
  pipeline_version TEXT NOT NULL, props TEXT NOT NULL DEFAULT '{}',
  is_synthetic INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_edges_src ON edges(src, edge_type);
CREATE INDEX IF NOT EXISTS ix_edges_dst ON edges(dst, edge_type);
CREATE TABLE IF NOT EXISTS ontology(
  version INTEGER NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL,
  params TEXT NOT NULL, added_at REAL NOT NULL,
  UNIQUE(kind, name));
CREATE TABLE IF NOT EXISTS events(
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_type TEXT NOT NULL, subject TEXT NOT NULL, ts REAL NOT NULL,
  payload TEXT NOT NULL DEFAULT '{}', processed INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS alerts(
  alert_id TEXT PRIMARY KEY, rule TEXT NOT NULL, subject TEXT NOT NULL,
  ts REAL NOT NULL, confidence REAL NOT NULL,
  evidence TEXT NOT NULL, disposition TEXT NOT NULL DEFAULT 'open',
  anomaly_type TEXT, score REAL, props TEXT NOT NULL DEFAULT '{}',
  disposed_at REAL, disposed_by TEXT,
  is_synthetic INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS dispositions(
  disp_id INTEGER PRIMARY KEY AUTOINCREMENT,
  alert_id TEXT NOT NULL, anomaly_type TEXT NOT NULL,
  label TEXT NOT NULL, score REAL, ts REAL NOT NULL, analyst TEXT);
"""


@dataclass
class Edge:
    edge_type: str
    src: str
    dst: str
    t_start: float
    t_end: float | None
    base_confidence: float
    observed_at: float
    source: str
    source_ref: str
    props: dict = field(default_factory=dict)
    rowid: int | None = None
    #: Scenario data (ADR-019). Always agrees with `source`.
    is_synthetic: bool = False

    def confidence(self, at: float | None = None,
                   half_life_days: float | None = None) -> float:
        """Decay-on-read. State edges rot without re-observation; event
        edges hold their base confidence forever."""
        if half_life_days is None:
            return self.base_confidence
        at = time.time() if at is None else at
        dt_days = max(0.0, at - self.observed_at) / 86400.0
        return self.base_confidence * 0.5 ** (dt_days / half_life_days)


class GraphStore:
    def __init__(self, db_path=None):
        self.db_path = str(db_path or (DATA_ROOT / GRAPH_DB_NAME))
        self._con = sqlite3.connect(self.db_path)
        self._con.executescript(_SCHEMA)
        self._migrate_is_synthetic()
        self._seed_ontology()

    # ---------------- migration ----------------
    def _migrate_is_synthetic(self) -> None:
        """Add `is_synthetic` to a graph built before it existed.

        **Zero-recompute, by construction.** SQLite's ADD COLUMN with a constant
        DEFAULT is a schema-only operation: no existing row is read, rewritten
        or touched, and the default is materialised on read. Every pre-existing
        row is real data, and False is the correct value for all of them.

        This is what lets a populated real graph — 17,562 nodes and 20,026
        current edges as measured on 2026-07-30 — gain the flag without being
        rebuilt, which matters because the graph accumulates history that cannot
        be regenerated (ADR-011).
        """
        for table in ("nodes", "edges", "alerts"):
            cols = {r[1] for r in self._con.execute(
                f"PRAGMA table_info({table})")}
            if "is_synthetic" not in cols:
                self._con.execute(
                    f"ALTER TABLE {table} "
                    f"ADD COLUMN is_synthetic INTEGER NOT NULL DEFAULT 0")
        # The index is created HERE, after the column exists — not in _SCHEMA.
        # `executescript(_SCHEMA)` runs first in __init__, so an index on
        # is_synthetic declared there would be executed against a
        # pre-migration database that does not have the column yet, and
        # opening any existing graph.sqlite would raise
        # `no such column: is_synthetic`. Caught by the migration test, which
        # is precisely the case it was written to cover.
        self._con.execute(
            "CREATE INDEX IF NOT EXISTS ix_edges_syn ON edges(is_synthetic)")
        self._con.commit()

    @staticmethod
    def _check_synthetic_agreement(is_synthetic: bool, source: str) -> None:
        """The flag and the source must never disagree. See ADR-019.

        Enforced at every write rather than audited afterwards: a graph where
        some rows are flagged synthetic but sourced real is one where no split
        can be trusted, and nothing in a row count would show it.
        """
        syn_source = str(source or "").startswith(SYNTHETIC_SOURCE_PREFIX)
        if bool(is_synthetic) != syn_source:
            raise ValueError(
                f"is_synthetic={is_synthetic} disagrees with source={source!r} "
                f"— scenario rows must carry source "
                f"'{SYNTHETIC_SOURCE_PREFIX}...' and only those rows may set "
                f"the flag")

    # ---------------- ontology ----------------
    def _seed_ontology(self) -> None:
        cur = self._con.execute("SELECT COUNT(*) FROM ontology")
        if cur.fetchone()[0] == 0:
            now = time.time()
            for n in NODE_TYPES_V1:
                self._con.execute(
                    "INSERT INTO ontology VALUES (?,?,?,?,?)",
                    (ONTOLOGY_VERSION, "node", n, "{}", now))
            for name, spec in EDGE_TYPES_V1.items():
                self._con.execute(
                    "INSERT INTO ontology VALUES (?,?,?,?,?)",
                    (ONTOLOGY_VERSION, "edge", name, json.dumps(spec), now))
            self._con.commit()

    def edge_registry(self) -> dict[str, dict]:
        return {name: json.loads(params) for name, params in self._con.execute(
            "SELECT name, params FROM ontology WHERE kind='edge'")}

    def node_registry(self) -> set[str]:
        return {n for (n,) in self._con.execute(
            "SELECT name FROM ontology WHERE kind='node'")}

    def ontology_version(self) -> int:
        return self._con.execute(
            "SELECT MAX(version) FROM ontology").fetchone()[0]

    def migrate_add_edge_type(self, name: str, spec: dict) -> int:
        """Roadmap 4.5 migration path: register a new edge type at runtime.
        Existing rows are untouched by construction (append-only + ontology
        as data); the new version number is the only global change."""
        v = self.ontology_version() + 1
        self._con.execute("INSERT INTO ontology VALUES (?,?,?,?,?)",
                          (v, "edge", name, json.dumps(spec), time.time()))
        self._con.commit()
        return v

    def migrate_add_node_type(self, name: str) -> int:
        v = self.ontology_version() + 1
        self._con.execute("INSERT INTO ontology VALUES (?,?,?,?,?)",
                          (v, "node", name, "{}", time.time()))
        self._con.commit()
        return v

    # ---------------- nodes ----------------
    def upsert_node(self, node_id: str, node_type: str,
                    props: dict | None = None,
                    is_synthetic: bool = False) -> None:
        """Create or merge a node.

        A node that already exists as real is **never** demoted to synthetic by
        a later synthetic write. That asymmetry is deliberate: a scenario port
        or flag_state node legitimately coincides with a real one (Sikka is
        Sikka), and letting the second write flip the flag would relabel real
        structure as scenario data. The reverse — a synthetic node later touched
        by a real write — promotes to real for the same reason.
        """
        if node_type not in self.node_registry():
            raise ValueError(f"unknown node type {node_type!r}")
        now = time.time()
        row = self._con.execute(
            "SELECT props, is_synthetic FROM nodes WHERE node_id=?",
            (node_id,)).fetchone()
        if row is None:
            self._con.execute("INSERT INTO nodes VALUES (?,?,?,?,?,?)",
                              (node_id, node_type,
                               json.dumps(props or {}), now, now,
                               1 if is_synthetic else 0))
        else:
            merged = {**json.loads(row[0]), **(props or {})}
            # min(): once real, always real.
            flag = min(int(row[1] or 0), 1 if is_synthetic else 0)
            self._con.execute(
                "UPDATE nodes SET props=?, updated_at=?, is_synthetic=? "
                "WHERE node_id=?",
                (json.dumps(merged), now, flag, node_id))
        self._con.commit()

    def node(self, node_id: str) -> dict | None:
        r = self._con.execute(
            "SELECT node_id, node_type, props FROM nodes WHERE node_id=?",
            (node_id,)).fetchone()
        return (dict(node_id=r[0], node_type=r[1], props=json.loads(r[2]))
                if r else None)

    def n_nodes(self, node_type: str | None = None) -> int:
        q = "SELECT COUNT(*) FROM nodes"
        return self._con.execute(
            q + (" WHERE node_type=?" if node_type else ""),
            (node_type,) if node_type else ()).fetchone()[0]

    # ---------------- edges ----------------
    def add_edge(self, edge_type: str, src: str, dst: str, *,
                 t_start: float, t_end: float | None = None,
                 confidence: float, observed_at: float | None = None,
                 source: str, source_ref: str,
                 props: dict | None = None,
                 is_synthetic: bool = False) -> None:
        """No naked facts: provenance + confidence + time scope required."""
        if not source or not source_ref:
            raise ValueError("edge rejected: provenance (source, source_ref) "
                             "is not optional")
        self._check_synthetic_agreement(is_synthetic, source)
        sn, dn = self.node(src), self.node(dst)
        if sn is None or dn is None:
            raise ValueError(f"edge endpoints must exist: {src} -> {dst}")
        validate_edge(edge_type, sn["node_type"], dn["node_type"],
                      self.edge_registry())
        self._con.execute(
            "INSERT INTO edges(edge_type,src,dst,t_start,t_end,"
            "base_confidence,observed_at,source,source_ref,pipeline_version,"
            "props,is_synthetic) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (edge_type, src, dst, t_start, t_end, confidence,
             observed_at if observed_at is not None else time.time(),
             source, source_ref, PIPELINE_VERSION,
             json.dumps(props or {}), 1 if is_synthetic else 0))
        self._con.commit()

    def _rows_to_edges(self, rows) -> list[Edge]:
        return [Edge(edge_type=r[1], src=r[2], dst=r[3], t_start=r[4],
                     t_end=r[5], base_confidence=r[6], observed_at=r[7],
                     source=r[8], source_ref=r[9],
                     props=json.loads(r[11]), rowid=r[0],
                     is_synthetic=bool(r[12]) if len(r) > 12 else False)
                for r in rows]

    def edges(self, node_id: str, edge_type: str | None = None,
              direction: str = "out", as_of: float | None = None,
              history: bool = False) -> list[Edge]:
        """Latest assertion per (type,src,dst), time-scope filtered, unless
        history=True (the full append-only record)."""
        col = {"out": "src", "in": "dst"}[direction]
        q = f"SELECT rowid,edge_type,src,dst,t_start,t_end,base_confidence," \
            f"observed_at,source,source_ref,pipeline_version,props," \
            f"is_synthetic FROM edges WHERE {col}=?"
        args: list = [node_id]
        if edge_type:
            q += " AND edge_type=?"
            args.append(edge_type)
        if as_of is not None:
            q += " AND observed_at<=? AND t_start<=? AND (t_end IS NULL OR t_end>?)"
            args += [as_of, as_of, as_of]
        rows = self._rows_to_edges(self._con.execute(q, args).fetchall())
        if history:
            return rows
        latest: dict[tuple, Edge] = {}
        for e in sorted(rows, key=lambda e: e.observed_at):
            latest[(e.edge_type, e.src, e.dst)] = e
        return list(latest.values())

    def edge_confidence(self, e: Edge, at: float | None = None) -> float:
        spec = self.edge_registry().get(e.edge_type, {})
        return e.confidence(at=at, half_life_days=spec.get("half_life_days"))

    def close_edge(self, edge_type: str, src: str, dst: str,
                   t_end: float, *, source: str, source_ref: str) -> None:
        """Close a time scope by RE-ASSERTING with t_end set (append-only:
        the open assertion stays in history; latest-wins resolution sees
        the closed one). This is how identified-as becomes the roadmap's
        formerly-identified-as."""
        prev = [e for e in self.edges(src, edge_type) if e.dst == dst]
        if not prev:
            raise ValueError(f"no open {edge_type} {src}->{dst} to close")
        p = prev[-1]
        self.add_edge(edge_type, src, dst, t_start=p.t_start, t_end=t_end,
                      confidence=p.base_confidence, source=source,
                      source_ref=source_ref, props=p.props,
                      is_synthetic=p.is_synthetic)

    def n_edges(self, is_synthetic: bool | None = None) -> int:
        if is_synthetic is None:
            return self._con.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        return self._con.execute(
            "SELECT COUNT(*) FROM edges WHERE is_synthetic=?",
            (1 if is_synthetic else 0,)).fetchone()[0]

    def counts_by_synthetic(self) -> dict:
        """Node, edge and alert counts split real vs synthetic.

        **Never returns a blended total on its own.** Every count that could be
        quoted externally has to be splittable by this flag (ADR-019), so the
        split is the primitive and any total is the caller's explicit sum.
        """
        out: dict[str, dict[str, int]] = {}
        for table in ("nodes", "edges", "alerts"):
            rows = dict(self._con.execute(
                f"SELECT is_synthetic, COUNT(*) FROM {table} "
                f"GROUP BY is_synthetic").fetchall())
            out[table] = dict(real=int(rows.get(0, 0)),
                              synthetic=int(rows.get(1, 0)))
        # Current (latest-per-triple) edges, which is what reads resolve to.
        cur = dict(self._con.execute(
            """
            SELECT e.is_synthetic, COUNT(*)
            FROM edges e
            JOIN (SELECT edge_type, src, dst, MAX(rowid) AS rid
                  FROM edges
                  WHERE (edge_type, src, dst, observed_at) IN
                        (SELECT edge_type, src, dst, MAX(observed_at)
                         FROM edges GROUP BY edge_type, src, dst)
                  GROUP BY edge_type, src, dst) k ON e.rowid = k.rid
            GROUP BY e.is_synthetic
            """).fetchall())
        out["edges_current"] = dict(real=int(cur.get(0, 0)),
                                    synthetic=int(cur.get(1, 0)))
        return out

    def n_nodes_by_type(self, is_synthetic: bool | None = None) -> dict:
        q = "SELECT node_type, COUNT(*) FROM nodes"
        args = ()
        if is_synthetic is not None:
            q += " WHERE is_synthetic=?"
            args = (1 if is_synthetic else 0,)
        return dict(self._con.execute(q + " GROUP BY node_type", args))

    def n_edges_by_type(self, is_synthetic: bool | None = None) -> dict:
        q = "SELECT edge_type, COUNT(*) FROM edges"
        args = ()
        if is_synthetic is not None:
            q += " WHERE is_synthetic=?"
            args = (1 if is_synthetic else 0,)
        return dict(self._con.execute(q + " GROUP BY edge_type", args))

    def edges_checksum(self) -> str:
        """Byte-level checksum of every edge row — the migration test's
        zero-recompute proof."""
        h = hashlib.sha256()
        for row in self._con.execute(
                "SELECT * FROM edges ORDER BY rowid"):
            h.update(repr(row).encode())
        return h.hexdigest()

    # ---------------- events & alerts ----------------
    def emit(self, event_type: str, subject: str, ts: float,
             payload: dict | None = None) -> None:
        self._con.execute(
            "INSERT INTO events(event_type,subject,ts,payload) VALUES (?,?,?,?)",
            (event_type, subject, ts, json.dumps(payload or {})))
        self._con.commit()

    def pending_events(self) -> list[dict]:
        return [dict(event_id=r[0], event_type=r[1], subject=r[2], ts=r[3],
                     payload=json.loads(r[4]))
                for r in self._con.execute(
                    "SELECT event_id,event_type,subject,ts,payload "
                    "FROM events WHERE processed=0 ORDER BY event_id")]

    def mark_processed(self, event_id: int) -> None:
        self._con.execute("UPDATE events SET processed=1 WHERE event_id=?",
                          (event_id,))
        self._con.commit()

    def add_alert(self, alert_id: str, rule: str, subject: str, ts: float,
                  confidence: float, evidence: list[dict],
                  anomaly_type: str | None = None, score: float | None = None,
                  props: dict | None = None,
                  is_synthetic: bool | None = None) -> None:
        """Record an alert.

        `is_synthetic` is normally left None and **derived from the subject**,
        because the detector that raises an alert has no business knowing which
        corpus a vessel came from — asking it to pass the flag would be handing
        it exactly the distinction it must not use. Deriving it here, after the
        decision has been made, keeps the detector blind while still letting the
        alert be split real from synthetic for reporting.
        """
        if is_synthetic is None:
            is_synthetic = self._subject_is_synthetic(subject)
        self._con.execute(
            "INSERT OR IGNORE INTO alerts(alert_id,rule,subject,ts,"
            "confidence,evidence,anomaly_type,score,props,is_synthetic) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (alert_id, rule, subject, ts, confidence, json.dumps(evidence),
             anomaly_type, score, json.dumps(props or {}),
             1 if is_synthetic else 0))
        self._con.commit()

    def _subject_is_synthetic(self, subject: str) -> bool:
        row = self._con.execute(
            "SELECT is_synthetic FROM nodes WHERE node_id=?",
            (subject,)).fetchone()
        return bool(row[0]) if row else False

    def dispose(self, alert_id: str, label: str, *, analyst: str = "analyst",
                at: float | None = None) -> None:
        """Analyst verdict on an alert: confirm | dismiss | watch. Captured
        as a label in the dispositions ledger — this is the proprietary
        feedback loop's raw material (roadmap 5.1). Nothing is overwritten
        destructively: the alert row records the latest disposition, the
        ledger keeps every one."""
        if label not in ("confirm", "dismiss", "watch"):
            raise ValueError(f"disposition must be confirm|dismiss|watch, "
                             f"got {label!r}")
        row = self._con.execute(
            "SELECT anomaly_type, score FROM alerts WHERE alert_id=?",
            (alert_id,)).fetchone()
        if row is None:
            raise ValueError(f"no alert {alert_id!r}")
        at = time.time() if at is None else at
        self._con.execute(
            "INSERT INTO dispositions(alert_id,anomaly_type,label,score,ts,"
            "analyst) VALUES (?,?,?,?,?,?)",
            (alert_id, row[0], label, row[1], at, analyst))
        self._con.execute(
            "UPDATE alerts SET disposition=?, disposed_at=?, disposed_by=? "
            "WHERE alert_id=?", (label, at, analyst, alert_id))
        self._con.commit()

    def dispositions(self, anomaly_type: str | None = None) -> list[dict]:
        q = ("SELECT disp_id,alert_id,anomaly_type,label,score,ts,analyst "
             "FROM dispositions")
        args = ()
        if anomaly_type:
            q += " WHERE anomaly_type=?"
            args = (anomaly_type,)
        return [dict(disp_id=r[0], alert_id=r[1], anomaly_type=r[2],
                     label=r[3], score=r[4], ts=r[5], analyst=r[6])
                for r in self._con.execute(q + " ORDER BY ts", args)]

    def alerts(self, anomaly_type: str | None = None,
               disposition: str | None = None,
               is_synthetic: bool | None = None) -> list[dict]:
        q = ("SELECT alert_id,rule,subject,ts,confidence,evidence,"
             "disposition,anomaly_type,score,props,is_synthetic FROM alerts")
        clauses, args = [], []
        if anomaly_type:
            clauses.append("anomaly_type=?"); args.append(anomaly_type)
        if disposition:
            clauses.append("disposition=?"); args.append(disposition)
        if is_synthetic is not None:
            clauses.append("is_synthetic=?")
            args.append(1 if is_synthetic else 0)
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        return [dict(alert_id=r[0], rule=r[1], subject=r[2], ts=r[3],
                     confidence=r[4], evidence=json.loads(r[5]),
                     disposition=r[6], anomaly_type=r[7], score=r[8],
                     props=json.loads(r[9]), is_synthetic=bool(r[10]))
                for r in self._con.execute(q + " ORDER BY ts", args)]

    def close(self) -> None:
        self._con.close()
