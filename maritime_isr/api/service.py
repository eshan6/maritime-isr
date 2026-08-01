"""Domain queries over the conformed Parquet tables (via DuckDB) and the graph.

This is where the response contracts in :mod:`.models` are actually assembled.
Two cross-cutting rules are handled here once, not per endpoint:

  * **The canonical vessel id is the graph node id** ``vessel:gfw:<native>``. The
    conformed tables key on their own ``vessel_id`` (``vessel:spine`` for
    scenario rows, a bare GFW id for real ones), so :func:`_conformed_keys` maps
    a canonical id back to the one or two spellings the tables use. This is the
    same keyspace ADR-022 unified; the API stays on the canonical side of it.
  * **Counts are split real vs synthetic** and never blended, via
    :class:`~.models.SplitCount`.

PROFILE-CHECK markers flag every place a column name or shape was taken from the
synthetic generator rather than measured on the real corpus — verify these first
against ``data_profiles/api_schema_profile.json`` once it lands from the laptop.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from ..schemas.keys import native_vessel_id, vessel_node_id
from . import graph_service as gsvc
from .reader import Reader, as_iso, open_reader

# Envelope columns present on every conformed row (CLAUDE.md §4.1).
_ENVELOPE = ("source_id", "source_ref", "acquired_at", "ingested_at",
             "pipeline_version", "confidence")

GFW = "Global Fishing Watch"


# --------------------------------------------------------------------------
# id mapping + provenance
# --------------------------------------------------------------------------

def canonical_id(conformed_vessel_id: str) -> str:
    """Conformed vessel_id -> canonical graph node id."""
    return vessel_node_id(conformed_vessel_id)


def _native(canonical: str) -> str:
    """Canonical id -> the native token the conformed tables may store."""
    return native_vessel_id(canonical)


def _conformed_keys(canonical: str) -> list[str]:
    """The vessel_id spellings a conformed table might use for this vessel.

    Real GFW rows store the bare native id; scenario rows store it under a
    ``vessel:`` prefix (ADR-022). Matching both is what lets one canonical id
    reach either corpus.
    """
    nat = _native(canonical)
    return [nat, f"vessel:{nat}"]


def _prov(row: dict) -> dict:
    return {
        "source_id": row.get("source_id"),
        "source_ref": row.get("source_ref"),
        "acquired_at": as_iso(row.get("acquired_at")),
        "ingested_at": as_iso(row.get("ingested_at")),
        "pipeline_version": row.get("pipeline_version"),
        "confidence": row.get("confidence"),
    }


def _attribution(source_id: Optional[str]) -> str:
    s = (source_id or "").lower()
    if "gfw" in s:
        return GFW
    if s.startswith("synthetic"):
        return "scenario (synthetic)"
    return source_id or "unknown"


def _is_syn(row: dict) -> bool:
    return bool(row.get("is_synthetic"))


# --------------------------------------------------------------------------
# vessels
# --------------------------------------------------------------------------

_CURRENT_IDENTITY_SQL = """
SELECT * FROM (
  SELECT *, row_number() OVER (
    PARTITION BY vessel_id
    ORDER BY (valid_to IS NULL) DESC, valid_from DESC NULLS LAST,
             ingested_at DESC NULLS LAST
  ) AS _rn
  FROM gfw_vessel_identity
) WHERE _rn = 1
"""


def _sanctions_by_vessel(reader: Reader) -> dict[str, list[dict]]:
    """{canonical_vessel_id: [match, ...]} keyed off sanctioned_vessel_matches."""
    out: dict[str, list[dict]] = {}
    if not reader.has("sanctioned_vessel_matches"):
        return out
    for r in reader.rows("SELECT * FROM sanctioned_vessel_matches"):
        vid = r.get("vessel_id")
        if not vid:
            continue
        out.setdefault(canonical_id(vid), []).append(r)
    return out


def _match_model(r: dict) -> dict:
    return {
        "match_tier": r.get("match_tier"),
        "is_finding": bool(r.get("is_finding")),
        "confidence": r.get("confidence"),
        "ofac_program": r.get("ofac_program"),
        "ofac_name": r.get("ofac_name"),
        "ofac_owner": r.get("ofac_owner"),
        "ofac_ent_num": r.get("ofac_ent_num"),
        "vessel_name": r.get("vessel_name"),
        "vessel_flag": r.get("vessel_flag"),
        "vessel_imo": r.get("vessel_imo"),
        "registry": r.get("registry"),
        "sanctions_as_of": as_iso(r.get("sanctions_as_of")),
        "is_synthetic": _is_syn(r),
    }


def _last_seen_map(reader: Reader) -> dict[str, str]:
    if not reader.has("ais_position"):
        return {}
    rows = reader.rows(
        "SELECT vessel_id, max(ts) AS last_ts FROM ais_position GROUP BY vessel_id")
    return {r["vessel_id"]: as_iso(r["last_ts"]) for r in rows if r.get("last_ts")}


def list_vessels(*, flag: Optional[str] = None, sanctioned: Optional[bool] = None,
                 synthetic: Optional[bool] = None, min_risk: Optional[float] = None,
                 q: Optional[str] = None, limit: int = 500,
                 offset: int = 0) -> dict:
    """The vessels table. Returns {items, count: SplitCount, total_matched}."""
    with open_reader() as reader:
        if not reader.has("gfw_vessel_identity"):
            return {"items": [], "count": {"real": 0, "synthetic": 0},
                    "total_matched": 0}
        current = reader.rows(_CURRENT_IDENTITY_SQL)
        sanctions = _sanctions_by_vessel(reader)
        last_seen = _last_seen_map(reader)
    risk = gsvc.risk_index()

    items = []
    for r in current:
        cid = canonical_id(r["vessel_id"])
        matches = sanctions.get(cid, [])
        vsummary = {
            "id": cid,
            "name": r.get("ship_name"),
            "mmsi": _s(r.get("mmsi")),
            "imo": _s(r.get("imo")),
            "flag": r.get("flag"),
            "vessel_type": r.get("vessel_class") or r.get("vessel_type"),
            "length_m": r.get("length_m"),          # null on ~98.6% of real rows
            "risk_score": risk.get(cid),
            "sanctioned": bool(matches),
            "sanctions_is_finding": any(m.get("is_finding") for m in matches),
            "last_seen": last_seen.get(r["vessel_id"]) or as_iso(r.get("valid_to")),
            "is_synthetic": _is_syn(r),
            "prov": _prov(r),
        }
        items.append(vsummary)

    # filters
    def keep(v: dict) -> bool:
        if flag and (v["flag"] or "").upper() != flag.upper():
            return False
        if sanctioned is not None and v["sanctioned"] != sanctioned:
            return False
        if synthetic is not None and v["is_synthetic"] != synthetic:
            return False
        if min_risk is not None and (v["risk_score"] or 0.0) < min_risk:
            return False
        if q:
            hay = " ".join(str(v.get(k) or "") for k in
                           ("name", "mmsi", "imo", "flag")).lower()
            if q.lower() not in hay:
                return False
        return True

    filtered = [v for v in items if keep(v)]
    # highest risk first — a sparse queue reads best led by signal
    filtered.sort(key=lambda v: (v["risk_score"] or 0.0), reverse=True)

    count = {"real": sum(1 for v in filtered if not v["is_synthetic"]),
             "synthetic": sum(1 for v in filtered if v["is_synthetic"])}
    page = filtered[offset:offset + limit]
    return {"items": page, "count": count, "total_matched": len(filtered)}


def _s(v) -> Optional[str]:
    """Identifiers land as int/str/float across sources; render as clean str."""
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _identity_interval(r: dict) -> dict:
    return {
        "name": r.get("ship_name"),
        "mmsi": _s(r.get("mmsi")),
        "imo": _s(r.get("imo")),
        "flag": r.get("flag"),
        "call_sign": r.get("call_sign"),
        "vessel_class": r.get("vessel_class") or r.get("vessel_type"),
        "length_m": r.get("length_m"),
        "width_m": r.get("width_m"),
        "tonnage_gt": r.get("tonnage_gt"),
        "valid_from": as_iso(r.get("valid_from")),
        "valid_to": as_iso(r.get("valid_to")),
        "superseded": bool(r.get("interval_superseded")),
        "is_synthetic": _is_syn(r),
        "prov": _prov(r),
    }


def get_vessel(canonical: str) -> Optional[dict]:
    keys = _conformed_keys(canonical)
    with open_reader() as reader:
        if not reader.has("gfw_vessel_identity"):
            return None
        ph = ",".join("?" * len(keys))
        rows = reader.rows(
            f"SELECT * FROM gfw_vessel_identity WHERE vessel_id IN ({ph}) "
            f"ORDER BY (valid_to IS NULL) DESC, valid_from DESC NULLS LAST", keys)
        if not rows:
            return None
        current = rows[0]
        sanctions = [m for m in _sanctions_for(reader, canonical)]
        port_calls = _vessel_events(reader, "gfw_port_visits", keys, "port_visit")
        encounters = _vessel_events(reader, "gfw_encounters", keys, "encounter")
        gaps = _vessel_events(reader, "gfw_ais_gaps", keys, "gap")

    risk = gsvc.risk_for(canonical)
    return {
        "id": canonical,
        "current": _identity_interval(current),
        "identity_history": [_identity_interval(r) for r in rows],
        "sanctions": sanctions,
        "risk": risk,
        "port_calls": port_calls,
        "encounters": encounters,
        "gaps": gaps,
        "is_synthetic": _is_syn(current),
        "prov": _prov(current),
    }


def _sanctions_for(reader: Reader, canonical: str) -> list[dict]:
    if not reader.has("sanctioned_vessel_matches"):
        return []
    keys = _conformed_keys(canonical)
    ph = ",".join("?" * len(keys))
    rows = reader.rows(
        f"SELECT * FROM sanctioned_vessel_matches WHERE vessel_id IN ({ph})", keys)
    return [_match_model(r) for r in rows]


def _vessel_events(reader: Reader, table: str, keys: list[str],
                   kind: str) -> list[dict]:
    if not reader.has(table):
        return []
    ph = ",".join("?" * len(keys))
    order = "start_time" if kind != "port_visit" else "start_time"
    rows = reader.rows(
        f"SELECT * FROM {table} WHERE vessel_id IN ({ph}) "
        f"ORDER BY {order} NULLS LAST", keys)
    return [_event_model(r, kind) for r in rows]


def _event_place(r: dict, kind: str) -> Optional[str]:
    if kind == "port_visit":
        # PROFILE-CHECK: real rows may name the anchorage only via
        # anchorage_top_destination (name null on ~46%). Prefer a readable one.
        return (r.get("port_name") or r.get("visit_port_name")
                or r.get("anchorage_name") or r.get("anchorage_top_destination"))
    return r.get("counterpart_name")


def _gap_classification(r: dict) -> Optional[str]:
    v = r.get("gfw_intentional_disabling")
    if v is True:
        return "intentional AIS disabling (GFW assessment)"
    if v is False:
        return "gap, not assessed as intentional"
    return "gap — classification unknown"


def _event_model(r: dict, kind: str) -> dict:
    classification = _gap_classification(r) if kind == "gap" else None
    return {
        "id": _s(r.get("event_id") or r.get("port_visit_id")
                 or f"{kind}:{r.get('vessel_id')}:{r.get('start_time')}"),
        "kind": kind,
        "vessel_id": canonical_id(r["vessel_id"]) if r.get("vessel_id") else None,
        "mmsi": _s(r.get("mmsi")),
        "lat": r.get("lat"),
        "lon": r.get("lon"),
        "start_time": as_iso(r.get("start_time")),
        "end_time": as_iso(r.get("end_time")),
        "duration_hours": r.get("duration_hours"),
        "place": _event_place(r, kind),
        "counterpart_name": r.get("counterpart_name"),
        "distance_from_shore_km": r.get("start_distance_from_shore_km"),
        "classification": classification,
        "attribution": _attribution(r.get("source_id")),
        "is_synthetic": _is_syn(r),
        "prov": _prov(r),
    }


# --------------------------------------------------------------------------
# track
# --------------------------------------------------------------------------

def get_track(canonical: str, *, start: Optional[str] = None,
              end: Optional[str] = None, limit: int = 5000) -> dict:
    keys = _conformed_keys(canonical)
    with open_reader() as reader:
        if not reader.has("ais_position"):
            return {"vessel_id": canonical, "is_synthetic": False,
                    "points": [], "window_start": start, "window_end": end,
                    "note": "no AIS position table landed"}
        ph = ",".join("?" * len(keys))
        clauses = [f"vessel_id IN ({ph})"]
        params: list = list(keys)
        if start:
            clauses.append("ts >= ?")
            params.append(start)
        if end:
            clauses.append("ts <= ?")
            params.append(end)
        where = " AND ".join(clauses)
        rows = reader.rows(
            f"SELECT ts, lat, lon, sog_kn, cog_deg, is_synthetic FROM ais_position "
            f"WHERE {where} ORDER BY ts LIMIT {int(limit)}", params)

    points = [{"ts": as_iso(r["ts"]), "lat": r["lat"], "lon": r["lon"],
               "sog_kn": r.get("sog_kn"), "cog_deg": r.get("cog_deg")}
              for r in rows if r.get("lat") is not None]
    note = None
    if not points:
        # A moving vessel with no AIS is expected offshore (ADR-005): terrestrial
        # reception only, so an open-ocean track lands nothing. Say so, don't
        # render an empty panel as an error.
        note = ("no AIS positions in this window — likely offshore, "
                "outside terrestrial receiver coverage (ADR-005)")
    return {
        "vessel_id": canonical,
        "is_synthetic": bool(rows[0]["is_synthetic"]) if rows else False,
        "window_start": start,
        "window_end": end,
        "points": points,
        "note": note,
    }


# --------------------------------------------------------------------------
# events (map + timeline)
# --------------------------------------------------------------------------

_EVENT_TABLES = {
    "encounter": "gfw_encounters",
    "loitering": "gfw_loitering",
    "port_visit": "gfw_port_visits",
    "gap": "gfw_ais_gaps",
}


def list_events(*, kinds: Optional[list[str]] = None, start: Optional[str] = None,
                end: Optional[str] = None, bbox: Optional[tuple] = None,
                synthetic: Optional[bool] = None, limit: int = 2000) -> dict:
    """Events across all four kinds, filterable by time and bbox."""
    kinds = kinds or list(_EVENT_TABLES)
    out: list[dict] = []
    with open_reader() as reader:
        for kind in kinds:
            table = _EVENT_TABLES.get(kind)
            if not table or not reader.has(table):
                continue
            clauses, params = [], []
            if start:
                clauses.append("start_time >= ?")
                params.append(start)
            if end:
                clauses.append("start_time <= ?")
                params.append(end)
            if bbox:
                lon0, lat0, lon1, lat1 = bbox
                clauses.append("lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?")
                params += [lat0, lat1, lon0, lon1]
            if synthetic is not None:
                clauses.append("is_synthetic = ?")
                params.append(synthetic)
            where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
            rows = reader.rows(
                f"SELECT * FROM {table}{where} "
                f"ORDER BY start_time NULLS LAST LIMIT {int(limit)}", params)
            out += [_event_model(r, kind) for r in rows]

    count = {"real": sum(1 for e in out if not e["is_synthetic"]),
             "synthetic": sum(1 for e in out if e["is_synthetic"])}
    by_kind: dict[str, dict] = {}
    for e in out:
        c = by_kind.setdefault(e["kind"], {"real": 0, "synthetic": 0})
        c["synthetic" if e["is_synthetic"] else "real"] += 1
    return {"items": out, "count": count, "by_kind": by_kind}


# --------------------------------------------------------------------------
# scenes
# --------------------------------------------------------------------------

def list_scenes(*, limit: int = 2000) -> dict:
    with open_reader() as reader:
        if not reader.has("scene_catalog"):
            return {"items": [],
                    "note": "scene_catalog not present (Sentinel-1 catalog is "
                            "landed on the laptop; 636 footprints there)"}
        rows = reader.rows(
            f"SELECT * FROM scene_catalog LIMIT {int(limit)}")
    items = []
    for r in rows:
        items.append({
            "scene_id": r.get("scene_id"),
            "footprint_wkt": r.get("footprint_wkt"),
            "acquired_at": as_iso(r.get("acquired_at")),
            "orbit_direction": r.get("orbit_direction"),
            "relative_orbit": r.get("relative_orbit"),
            "is_synthetic": False,   # scene catalog is real metadata only
            "prov": {
                "source_id": r.get("source_id"),
                "source_ref": r.get("source_ref"),
                "acquired_at": as_iso(r.get("provenance_acquired_at")),
                "ingested_at": as_iso(r.get("ingested_at")),
                "pipeline_version": r.get("pipeline_version"),
                "confidence": r.get("confidence"),
            },
        })
    return {"items": items, "note": None}


# --------------------------------------------------------------------------
# ports (consolidated gazetteer)
# --------------------------------------------------------------------------

def list_ports() -> dict:
    """Consolidated gazetteer: graph port nodes (real + scenario) plus WPI if landed."""
    items: list[dict] = []
    seen: set[str] = set()
    with gsvc.open_graph() as g:
        if g is not None:
            rows = g._con.execute(
                "SELECT node_id, props, is_synthetic FROM nodes "
                "WHERE node_type='port'").fetchall()
            import json as _json
            for nid, props, syn in rows:
                p = _json.loads(props) if isinstance(props, str) else (props or {})
                key = (p.get("name") or nid).lower()
                if key in seen:
                    continue
                seen.add(key)
                items.append({
                    "id": p.get("port_id") or nid,
                    "name": p.get("name"),
                    "flag": p.get("flag"),
                    "lat": p.get("lat"),
                    "lon": p.get("lon"),
                    "source": "graph",
                    "is_synthetic": bool(syn),
                })
    with open_reader() as reader:
        if reader.has("wpi_ports"):
            # PROFILE-CHECK: WPI was 0 rows on the last real run (NGA outage).
            # Columns assumed from registries.py; confirm against the profile.
            for r in reader.rows("SELECT * FROM wpi_ports"):
                name = r.get("port_name") or r.get("name")
                if not name or name.lower() in seen:
                    continue
                seen.add(name.lower())
                items.append({
                    "id": _s(r.get("port_id") or name),
                    "name": name,
                    "flag": r.get("country") or r.get("flag"),
                    "lat": r.get("lat") or r.get("latitude"),
                    "lon": r.get("lon") or r.get("longitude"),
                    "source": "wpi",
                    "is_synthetic": False,
                })
    count = {"real": sum(1 for p in items if not p["is_synthetic"]),
             "synthetic": sum(1 for p in items if p["is_synthetic"])}
    return {"items": items, "count": count}


# --------------------------------------------------------------------------
# stats — everything split real vs synthetic
# --------------------------------------------------------------------------

def _split(reader: Reader, table: str) -> dict:
    if not reader.has(table):
        return {"real": 0, "synthetic": 0}
    rows = reader.rows(
        f"SELECT COALESCE(is_synthetic, FALSE) AS s, count(*) AS n "
        f"FROM {table} GROUP BY 1")
    d = {"real": 0, "synthetic": 0}
    for r in rows:
        d["synthetic" if r["s"] else "real"] += int(r["n"])
    return d


def _distinct_vessels_split(reader: Reader) -> dict:
    if not reader.has("gfw_vessel_identity"):
        return {"real": 0, "synthetic": 0}
    rows = reader.rows(
        "SELECT COALESCE(is_synthetic, FALSE) AS s, count(DISTINCT vessel_id) AS n "
        "FROM gfw_vessel_identity GROUP BY 1")
    d = {"real": 0, "synthetic": 0}
    for r in rows:
        d["synthetic" if r["s"] else "real"] += int(r["n"])
    return d


def get_stats() -> dict:
    with open_reader() as reader:
        vessels = _distinct_vessels_split(reader)
        events = {kind: _split(reader, table)
                  for kind, table in _EVENT_TABLES.items()}
        matches = _split(reader, "sanctioned_vessel_matches")
        findings = {"real": 0, "synthetic": 0}
        if reader.has("sanctioned_vessel_matches"):
            for r in reader.rows(
                "SELECT COALESCE(is_synthetic,FALSE) s, count(*) n FROM "
                "sanctioned_vessel_matches WHERE is_finding GROUP BY 1"):
                findings["synthetic" if r["s"] else "real"] += int(r["n"])
        scenes_real = reader.scalar("SELECT count(*) FROM scene_catalog") \
            if reader.has("scene_catalog") else 0
        window = _corpus_window(reader)
        # length availability — drives the "dimensions not available" UI copy
        length_have = length_total = 0
        if reader.has("gfw_vessel_identity"):
            length_have = reader.scalar(
                "SELECT count(*) FROM (SELECT vessel_id, "
                "max(length_m) m FROM gfw_vessel_identity GROUP BY vessel_id) "
                "WHERE m IS NOT NULL") or 0
            length_total = reader.scalar(
                "SELECT count(DISTINCT vessel_id) FROM gfw_vessel_identity") or 0

    ports = list_ports()["count"]
    # graph
    if gsvc.graph_exists():
        with gsvc.open_graph() as g:
            gc = g.counts_by_synthetic()
            alerts = {"real": gc["alerts"]["real"], "synthetic": gc["alerts"]["synthetic"]}
            gnodes = gc["nodes"]
            gedges = gc["edges_current"]
            by_type: dict[str, dict] = {}
            for a in g.alerts():
                c = by_type.setdefault(a["anomaly_type"] or "unknown",
                                       {"real": 0, "synthetic": 0})
                c["synthetic" if a["is_synthetic"] else "real"] += 1
    else:
        alerts = {"real": 0, "synthetic": 0}
        gnodes = gedges = {"real": 0, "synthetic": 0}
        by_type = {}

    notes = _stats_notes(alerts, length_have, length_total, events)
    return {
        "vessels": vessels,
        "events": events,
        "alerts": alerts,
        "alerts_by_type": by_type,
        "sanctions_matches": matches,
        "sanctions_findings": findings,
        "scenes": {"real": int(scenes_real or 0), "synthetic": 0},
        "ports": ports,
        "graph_nodes": gnodes,
        "graph_edges": gedges,
        "corpus_window": window,
        "notes": notes,
    }


def _corpus_window(reader: Reader) -> dict:
    lo = hi = None
    for table in _EVENT_TABLES.values():
        if not reader.has(table):
            continue
        r = reader.one(f"SELECT min(start_time) a, max(end_time) b FROM {table}")
        if r and r["a"]:
            lo = min(lo, r["a"]) if lo else r["a"]
        if r and r["b"]:
            hi = max(hi, r["b"]) if hi else r["b"]
    if reader.has("ais_position"):
        r = reader.one("SELECT min(ts) a, max(ts) b FROM ais_position")
        if r and r["a"]:
            lo = min(lo, r["a"]) if lo else r["a"]
            hi = max(hi, r["b"]) if hi else r["b"]
    return {"start": as_iso(lo), "end": as_iso(hi)}


def _stats_notes(alerts: dict, length_have: int, length_total: int,
                 events: dict) -> list[str]:
    notes = []
    total_alerts = alerts["real"] + alerts["synthetic"]
    notes.append(
        f"{total_alerts} alert(s) total ({alerts['real']} real, "
        f"{alerts['synthetic']} scenario) — the queue is deliberately short and "
        f"high-signal, not a busy feed.")
    if length_total:
        notes.append(
            f"{length_have} of {length_total} vessels carry a length; the rest "
            f"render dimensions as 'not available' (real corpus is ~98.6% null).")
    enc = events.get("encounter", {})
    if (enc.get("real", 0) + enc.get("synthetic", 0)) < 50:
        notes.append(
            "Encounter graph is thin; vessel-to-vessel structure on real data "
            "runs through shared ports and flags, not encounters.")
    return notes
