"""Connectors #3 and #4: Global Fishing Watch datasets and static
registries (sanctions lists, port DB, ship registry snapshots).

The registry design rule (roadmap 0.1 #4): versioned snapshots, diffed on
refresh, and every derived record carries an *as-of* date. A sanctions
edge without a validity interval is a future false alert — a vessel
delisted in March must not fire a sanctioned-owner chain in June.
"""
from __future__ import annotations

import csv
import hashlib
import io
import uuid
from datetime import date, datetime, timezone

import pandas as pd
import pyarrow as pa

from .. import h3util as tiling
from ..config import PIPELINE_VERSION
from ..provenance import now_iso
from ..schemas import DETECTION, SANCTIONS_ENTRY
from ..storage import catalog as cat, raw


# ---------------- GFW SAR detections -> canonical DETECTION ----------------

def conform_gfw_detections(rows: list[dict], dataset_ref: str) -> pa.Table:
    """GFW published SAR detections -> canonical detection schema.
    This is ground truth for the Phase 1 eval harness and Phase 3
    cross-validation; landing it in the SAME schema as our own detector
    output is what makes 'where do we and GFW disagree' a join."""
    recs = []
    ing = pd.Timestamp(now_iso())
    for r in rows:
        lat, lon = float(r["lat"]), float(r["lon"])
        ts = pd.Timestamp(r["timestamp"], tz="UTC") if pd.Timestamp(r["timestamp"]).tzinfo is None \
            else pd.Timestamp(r["timestamp"])
        recs.append({
            "detection_id": r.get("detection_id") or f"gfw_{uuid.uuid4().hex[:12]}",
            "lat": lat, "lon": lon, "ts": ts,
            "length_m": float(r["length_m"]) if r.get("length_m") not in (None, "") else None,
            "score": float(r.get("score", 1.0)),
            "scene_id": r.get("scene_id"),
            "matched_mmsi": int(r["matched_mmsi"]) if r.get("matched_mmsi") not in (None, "") else None,
            "h3_cell": tiling.cell(lat, lon),
            "source": "gfw_sar", "source_ref": dataset_ref,
            "acquired_at": ts, "ingested_at": ing,
            "pipeline_version": PIPELINE_VERSION,
        })
    df = pd.DataFrame(recs)
    df["matched_mmsi"] = df["matched_mmsi"].astype("Int64")
    return pa.Table.from_pandas(df[[f.name for f in DETECTION]], schema=DETECTION,
                                preserve_index=False)


# ---------------- OFAC SDN (representative sanctions parser) ----------------

def parse_ofac_sdn_csv(payload: bytes) -> list[dict]:
    """OFAC SDN.CSV: positional columns (ent_num, name, type, program, ...).
    Vessels have type 'vessel'; IMO sometimes embedded in remarks."""
    out = []
    reader = csv.reader(io.StringIO(payload.decode("utf-8", errors="replace")))
    for row in reader:
        if len(row) < 4 or row[0] == "ent_num":
            continue
        ent_id, name, etype, program = row[0].strip(), row[1].strip(), row[2].strip().lower(), row[3].strip()
        imo = None
        remarks = row[11] if len(row) > 11 else ""
        for tok in remarks.replace(";", " ").split():
            if tok.isdigit() and len(tok) == 7:
                imo = int(tok)
                break
        out.append({"entry_id": ent_id, "name": name,
                    "entry_type": "vessel" if etype == "vessel" else (etype or "entity"),
                    "imo": imo, "flag": None, "program": program})
    return out


def snapshot_registry(registry: str, payload: bytes, parser, as_of: date) -> dict:
    """Land raw snapshot immutably, diff against previous snapshot, emit
    conformed SANCTIONS_ENTRY rows with validity intervals.

    Diff semantics:
      added   -> new row, valid_from = as_of, valid_to = null
      removed -> prior row closed: valid_to = as_of (delisting is an event)
      changed -> close + open (slowly-changing dimension, type 2)
    """
    sha = hashlib.sha256(payload).hexdigest()
    path, _ = raw.land(registry, f"{registry}_{as_of.isoformat()}.csv", payload,
                       day=as_of.isoformat())
    entries = parser(payload)
    current = {e["entry_id"]: e for e in entries}

    with cat.connect() as con:
        prev = con.execute(
            "SELECT raw_path FROM registry_snapshots WHERE registry=? ORDER BY as_of DESC LIMIT 1",
            (registry,)).fetchone()
        prev_entries = {}
        if prev:
            from pathlib import Path
            prev_entries = {e["entry_id"]: e for e in parser(Path(prev["raw_path"]).read_bytes())}

        added = [k for k in current if k not in prev_entries]
        removed = [k for k in prev_entries if k not in current]
        changed = [k for k in current if k in prev_entries and current[k] != prev_entries[k]]

        con.execute(
            """INSERT OR IGNORE INTO registry_snapshots
               (registry, as_of, sha256, raw_path, n_records, n_added, n_removed, n_changed, registered_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (registry, as_of.isoformat(), sha, str(path), len(entries),
             len(added), len(removed), len(changed), now_iso()))

    ing = pd.Timestamp(now_iso())
    rows = []
    for k, e in current.items():
        rows.append({**e, "registry": registry, "as_of": as_of,
                     "valid_from": as_of if k in added or k in changed else None,
                     "valid_to": None,
                     "source": registry, "source_ref": sha[:12],
                     "acquired_at": pd.Timestamp(datetime.combine(as_of, datetime.min.time(), tzinfo=timezone.utc)),
                     "ingested_at": ing, "pipeline_version": PIPELINE_VERSION})
    for k in removed:
        e = prev_entries[k]
        rows.append({**e, "registry": registry, "as_of": as_of,
                     "valid_from": None, "valid_to": as_of,
                     "source": registry, "source_ref": sha[:12],
                     "acquired_at": pd.Timestamp(datetime.combine(as_of, datetime.min.time(), tzinfo=timezone.utc)),
                     "ingested_at": ing, "pipeline_version": PIPELINE_VERSION})

    df = pd.DataFrame(rows)
    df["imo"] = df["imo"].astype("Int64")
    tbl = pa.Table.from_pandas(df[[f.name for f in SANCTIONS_ENTRY]],
                               schema=SANCTIONS_ENTRY, preserve_index=False)
    return {"table": tbl, "sha256": sha, "raw_path": str(path),
            "n_added": len(added), "n_removed": len(removed), "n_changed": len(changed)}
