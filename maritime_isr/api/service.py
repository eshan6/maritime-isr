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
from . import report
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
        "listed_entity_type": r.get("listed_entity_type"),
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

    # Risk scoring walks the graph per vessel — cheap for a small corpus, minutes
    # for the 9,184-vessel real one. On a large corpus, score only the vessels
    # worth scoring (alerted or sanctioned); the rest render "—". A small corpus
    # gets the full, cached index so every scenario vessel shows a number.
    if len(current) <= 500:
        risk = gsvc.risk_index()
    else:
        interesting = set(sanctions.keys()) | gsvc.alert_subjects()
        risk = gsvc.risk_index(only=interesting)

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


def list_tracks(*, max_vessels: int = 200, max_points: int = 140) -> dict:
    """Decimated AIS tracks for every vessel that has positions, for the map's
    time animation. Each track is [[lon, lat, epoch_seconds], ...] so the client
    can interpolate a vessel's position at any clock time and animate it moving.

    On the real corpus there is no free AIS, so ais_position is empty and this
    returns an empty list with a note — the honest reason the real map shows no
    moving vessels (ADR-005).
    """
    with open_reader() as reader:
        if not reader.has("ais_position"):
            return {"items": [], "note": "no AIS positions landed"}
        syn_col = "is_synthetic" in reader.columns("ais_position")
        vids = reader.rows(
            "SELECT vessel_id, count(*) AS n FROM ais_position "
            "WHERE lat IS NOT NULL GROUP BY vessel_id ORDER BY n DESC "
            f"LIMIT {int(max_vessels)}")
        items = []
        for row in vids:
            vid = row["vessel_id"]
            cols = "ts, lat, lon" + (", is_synthetic" if syn_col else "")
            pts = reader.rows(
                f"SELECT {cols} FROM ais_position WHERE vessel_id = ? "
                "AND lat IS NOT NULL ORDER BY ts", [vid])
            if not pts:
                continue
            step = max(1, len(pts) // max_points)
            dec = pts[::step]
            # always include the last point so the track ends where it truly ends
            if dec[-1] is not pts[-1]:
                dec.append(pts[-1])
            coords = []
            for p in dec:
                ts = p["ts"]
                epoch = int(ts.timestamp()) if hasattr(ts, "timestamp") else None
                if epoch is None or p["lon"] is None or p["lat"] is None:
                    continue
                coords.append([round(p["lon"], 4), round(p["lat"], 4), epoch])
            if len(coords) < 2:
                continue
            items.append({
                "vessel_id": canonical_id(vid),
                "is_synthetic": bool(pts[0].get("is_synthetic")) if syn_col else False,
                "points": coords,
            })
    note = None if items else "no AIS positions (the real corpus has no free AIS)"
    return {"items": items, "note": note}


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
    """Events across all four kinds, filterable by time and bbox.

    **Truncation is reported, never silent.** `limit` applies per kind, and on
    the real corpus a kind can hold far more than any sane page — 24,153
    loitering events. Returning the first N ordered by `start_time` without
    saying so meant the map drew a chronological prefix of the corpus and
    stopped, which reads as "there were no events after mid-July" rather than
    "you asked for 4,000 of 24,153". `truncated` names every kind that hit the
    cap, with its true total, so the caller can say so or switch to
    :func:`event_density`.
    """
    kinds = kinds or list(_EVENT_TABLES)
    out: list[dict] = []
    truncated: dict[str, dict] = {}
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
            if synthetic is not None and "is_synthetic" in reader.columns(table):
                clauses.append("is_synthetic = ?")
                params.append(synthetic)
            where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
            matching = int(reader.scalar(
                f"SELECT count(*) FROM {table}{where}", list(params)) or 0)
            rows = reader.rows(
                f"SELECT * FROM {table}{where} "
                f"ORDER BY start_time NULLS LAST LIMIT {int(limit)}", params)
            if matching > len(rows):
                truncated[kind] = {"returned": len(rows), "matching": matching}
            out += [_event_model(r, kind) for r in rows]

    count = {"real": sum(1 for e in out if not e["is_synthetic"]),
             "synthetic": sum(1 for e in out if e["is_synthetic"])}
    by_kind: dict[str, dict] = {}
    for e in out:
        c = by_kind.setdefault(e["kind"], {"real": 0, "synthetic": 0})
        c["synthetic" if e["is_synthetic"] else "real"] += 1
    note = None
    if truncated:
        detail = ", ".join(f"{k} {v['returned']:,} of {v['matching']:,}"
                           for k, v in sorted(truncated.items()))
        note = (f"TRUNCATED — showing {detail}. These are the earliest by start "
                "time, not a sample of the window. Use /api/events/density for "
                "counts over the whole corpus.")
    return {"items": out, "count": count, "by_kind": by_kind,
            "truncated": truncated, "note": note}


# --------------------------------------------------------------------------
# findings — the ranked table the landed data actually supports
# --------------------------------------------------------------------------
#
# `graph_report.py` settled what this screen should be: on the real corpus the
# encounter graph is star-shaped (14 encounters across 9,184 vessels, 0 of 126
# sanctions-matched hulls with an encounter neighbour), so a network view has
# nothing to draw and **a ranked table is the product the data supports**.
#
# Two populations feed it, and they are not the same kind of claim:
#
#   * **GFW-assessed AIS disabling.** Global Fishing Watch flagged the gap as
#     intentional. We did not compute it, we have no receiver-coverage model at
#     those positions, and asserting intentional silence outside demonstrated
#     coverage is a false positive by construction (CLAUDE.md §6). The honest
#     sentence is "GFW assessed this gap as intentional disabling" — never "we
#     detected a dark vessel", and `attribution` carries that to the UI.
#   * **Sanctions identity matches.** OFAC/UN/EU decided who is designated and
#     GFW observed the vessel; **our** contribution is the identity match
#     between the two (ADR-018).
#
# There is deliberately **no blended risk number** here. Ranking is an explicit
# ordered tuple of stated facts, and every fact that moved a row up is returned
# in `basis` so an analyst reads the reason rather than trusting a float.

#: Rank weights, highest first. Not a score — an ordering, printed with the row.
_RANK = (
    ("gfw_intentional_disabling", 1000,
     "GFW assessed an AIS gap on this hull as intentional disabling"),
    ("multi_registry", 400,
     "designated by more than one sanctions registry — independent lists agree"),
    ("imo_match", 200,
     "matched on IMO, a permanent hull number that survives renaming"),
    ("name_disagreement", 100,
     "sails under a different name than the sanctions listing — the "
     "identity-laundering signature an IMO match exists to catch"),
    ("flag_disagreement", 50,
     "flagged to a different state than the sanctions listing"),
)


def _event_counts_by_vessel(reader: Reader) -> dict[str, dict[str, int]]:
    """{conformed_vessel_id: {kind: n}} across all four event tables.

    One GROUP BY per table rather than a query per vessel — on the real corpus
    that is four scans instead of ~9,000 round trips.
    """
    out: dict[str, dict[str, int]] = {}
    for kind, table in _EVENT_TABLES.items():
        if not reader.has(table):
            continue
        for r in reader.rows(
                f"SELECT vessel_id, count(*) AS n FROM {table} "
                "WHERE vessel_id IS NOT NULL GROUP BY vessel_id"):
            out.setdefault(r["vessel_id"], {})[kind] = int(r["n"])
    return out


def _ports_by_vessel(reader: Reader) -> dict[str, list[str]]:
    """{conformed_vessel_id: [readable port name, ...]}, most recent first."""
    if not reader.has("gfw_port_visits"):
        return {}
    cols = reader.columns("gfw_port_visits")
    names = [c for c in ("port_name", "visit_port_name", "anchorage_name",
                         "anchorage_top_destination") if c in cols]
    if not names:
        return {}
    expr = "coalesce(" + ", ".join(names) + ")"
    out: dict[str, list[str]] = {}
    for r in reader.rows(
            f"SELECT vessel_id, {expr} AS place, max(start_time) AS last_at "
            "FROM gfw_port_visits WHERE vessel_id IS NOT NULL "
            f"AND {expr} IS NOT NULL GROUP BY vessel_id, place "
            "ORDER BY last_at DESC NULLS LAST"):
        out.setdefault(r["vessel_id"], []).append(r["place"])
    return out


def _identity_by_vessel(reader: Reader) -> dict[str, dict]:
    if not reader.has("gfw_vessel_identity"):
        return {}
    return {r["vessel_id"]: r for r in reader.rows(_CURRENT_IDENTITY_SQL)}


def _flagged_gaps(reader: Reader) -> list[dict]:
    """AIS gaps GFW flagged as intentional disabling.

    `gfw_intentional_disabling` lands as an INTEGER on the real corpus and a
    BOOLEAN on the scenario one, so the filter is written to accept both rather
    than assuming either.
    """
    if not reader.has("gfw_ais_gaps"):
        return []
    if "gfw_intentional_disabling" not in reader.columns("gfw_ais_gaps"):
        return []
    return reader.rows(
        "SELECT * FROM gfw_ais_gaps "
        "WHERE gfw_intentional_disabling IS NOT NULL "
        "AND CAST(gfw_intentional_disabling AS INTEGER) = 1 "
        "ORDER BY start_time DESC NULLS LAST")


def _imaging_by_gap(reader: Reader) -> dict[str, list[dict]]:
    """{gap_event_id: [imaging opportunity rows]}, best tier first.

    Rows where a Sentinel-1 pass was acquired while the vessel was dark. See
    `maritime_isr/overpass.py` — a `confirmed` row means an image exists whose
    footprint necessarily contained the vessel; it does **not** mean anything
    was detected in it, and nothing here may present it as if it did.
    """
    if not reader.has("sar_imaging_opportunity"):
        return {}
    out: dict[str, list[dict]] = {}
    order = {"confirmed": 0, "partial": 1, "none": 2, "unknown": 3}
    for r in reader.rows("SELECT * FROM sar_imaging_opportunity"):
        gid = r.get("gap_event_id")
        if gid:
            out.setdefault(str(gid), []).append(r)
    for rows in out.values():
        rows.sort(key=lambda r: (order.get(r.get("tier"), 9),
                                 -(r.get("coverage_fraction") or 0.0)))
    return out


def _imaging_model(r: dict) -> dict:
    return {
        "tier": r.get("tier"),
        "scene_id": r.get("scene_id") or None,
        "scene_acquired_at": as_iso(r.get("scene_acquired_at")),
        "hours_into_gap": r.get("hours_into_gap"),
        "coverage_fraction": r.get("coverage_fraction"),
        "reachable_area_km2": r.get("reachable_area_km2"),
        "covered_area_km2": r.get("covered_area_km2"),
        "geometry_basis": r.get("geometry_basis"),
        "scene_has_pixels": r.get("scene_has_pixels"),
        "orbit_direction": r.get("orbit_direction"),
        "v_max_knots": r.get("v_max_knots"),
        "implied_speed_exceeds_vmax": r.get("implied_speed_exceeds_vmax"),
        "statement": r.get("statement"),
        "is_synthetic": _is_syn(r),
        "prov": _prov(r),
    }


def _name_key(v) -> Optional[str]:
    if not v:
        return None
    return " ".join(str(v).upper().split())


def list_findings(*, synthetic: Optional[bool] = None,
                  limit: int = 500) -> dict:
    """The ranked findings table. Returns {items, count, basis_legend, notes}."""
    with open_reader() as reader:
        identity = _identity_by_vessel(reader)
        gaps = _flagged_gaps(reader)
        imaging = _imaging_by_gap(reader)
        counts = _event_counts_by_vessel(reader)
        ports = _ports_by_vessel(reader)
        matches: list[dict] = []
        if reader.has("sanctioned_vessel_matches"):
            matches = reader.rows("SELECT * FROM sanctioned_vessel_matches")

    # ---- assemble one entry per vessel, from both populations --------------
    entries: dict[str, dict] = {}

    def entry_for(conformed_vid: str, row: dict) -> dict:
        cid = canonical_id(conformed_vid)
        ident = identity.get(conformed_vid, {})
        e = entries.get(cid)
        if e is None:
            c = counts.get(conformed_vid, {})
            e = entries[cid] = {
                "id": cid,
                "name": ident.get("ship_name") or row.get("ship_name")
                        or row.get("vessel_name"),
                "mmsi": _s(ident.get("mmsi") or row.get("mmsi")),
                "imo": _s(ident.get("imo") or row.get("vessel_imo")),
                "flag": ident.get("flag") or row.get("flag")
                        or row.get("vessel_flag"),
                "vessel_type": ident.get("vessel_class") or ident.get("vessel_type"),
                "event_counts": {k: c.get(k, 0) for k in _EVENT_TABLES},
                "ports": ports.get(conformed_vid, [])[:5],
                "dark_gaps": [],
                "sanctions": [],
                "registries": [],
                "basis": [],
                "_signals": set(),
                "is_synthetic": _is_syn(ident) or _is_syn(row),
                "prov": _prov(ident or row),
            }
        return e

    # 1. GFW-assessed intentional AIS disabling
    for g in gaps:
        vid = g.get("vessel_id")
        if not vid:
            continue
        e = entry_for(vid, g)
        # Imaging opportunities are attached as EVIDENCE on the gap and are
        # deliberately absent from `_RANK`. A satellite having flown overhead
        # says nothing about whether a vessel is suspicious — it says the
        # question is resolvable. Folding actionability into a suspicion score
        # would be the blended number ADR-024 refuses to build.
        opps = [_imaging_model(o) for o in imaging.get(str(g.get("event_id")), [])]
        e["dark_gaps"].append({
            "imaging": opps,
            "imaging_best_tier": opps[0]["tier"] if opps else None,
            "start_time": as_iso(g.get("start_time")),
            "end_time": as_iso(g.get("end_time")),
            "duration_hours": g.get("gap_duration_hours") or g.get("duration_hours"),
            "off_lat": g.get("gap_off_lat"), "off_lon": g.get("gap_off_lon"),
            "on_lat": g.get("gap_on_lat"), "on_lon": g.get("gap_on_lon"),
            "distance_km": g.get("gap_distance_km"),
            "distance_from_shore_km": g.get("start_distance_from_shore_km"),
            # The one sentence that must travel with this row everywhere.
            "attribution": "Global Fishing Watch assessed this gap as "
                           "intentional AIS disabling",
            "is_synthetic": _is_syn(g),
            "prov": _prov(g),
        })
        e["_signals"].add("gfw_intentional_disabling")

    # 2. sanctions findings (candidates are carried but never rank a row up)
    for m in matches:
        vid = m.get("vessel_id")
        if not vid:
            continue
        e = entry_for(vid, m)
        e["sanctions"].append(_match_model(m))
        reg = m.get("registry") or "OFAC"
        if reg not in e["registries"]:
            e["registries"].append(reg)
        if not m.get("is_finding"):
            continue
        if m.get("match_tier") == "imo":
            e["_signals"].add("imo_match")
        # Name and flag disagreement only mean anything when the sanctions list
        # designated a SHIP. A scenario match reached through ownership carries
        # a company in `ofac_name`, and a hull name never equals a company name
        # — scoring that as identity laundering would fire the signal on every
        # such row. Rows landed before the column existed are treated as vessel
        # designations, which is what the real matcher has always produced.
        if (m.get("listed_entity_type") or "vessel") != "vessel":
            continue
        listed_name = _name_key(m.get("ofac_name"))
        our_name = _name_key(e["name"])
        if listed_name and our_name and listed_name != our_name:
            e["_signals"].add("name_disagreement")
        listed_flag = _name_key(m.get("ofac_flag"))
        our_flag = _name_key(e["flag"])
        if listed_flag and our_flag and listed_flag != our_flag:
            e["_signals"].add("flag_disagreement")

    # ---- rank, and say why -------------------------------------------------
    items = []
    for e in entries.values():
        findings = [s for s in e["sanctions"] if s.get("is_finding")]
        if len({s.get("registry") or "OFAC" for s in findings}) > 1:
            e["_signals"].add("multi_registry")

        # A row earns its place only through a finding-grade signal. A
        # name-only sanctions candidate is a lead for the vessels table, not a
        # finding — putting it here would be the alert-fatigue failure ADR-004
        # names outright.
        if not e["_signals"]:
            continue

        priority = 0
        basis = []
        for key, weight, sentence in _RANK:
            if key in e["_signals"]:
                priority += weight
                basis.append({"signal": key, "weight": weight,
                              "explanation": sentence})
        e["priority"] = priority
        e["basis"] = basis
        e["has_dark_gap"] = bool(e["dark_gaps"])
        e["sanctions_is_finding"] = bool(findings)
        e["attribution"] = ("Global Fishing Watch (gap assessment) + "
                            "OFAC/UN/EU (designation); the identity match "
                            "between them is ours")
        e["headline"] = _finding_headline(e)
        e.pop("_signals", None)
        items.append(e)

    if synthetic is not None:
        items = [i for i in items if i["is_synthetic"] == synthetic]

    # Ties broken by observed activity, so two hulls with identical evidence
    # order by how much of them we actually saw rather than arbitrarily.
    items.sort(key=lambda i: (i["priority"], sum(i["event_counts"].values())),
               reverse=True)
    count = {"real": sum(1 for i in items if not i["is_synthetic"]),
             "synthetic": sum(1 for i in items if i["is_synthetic"])}
    return {
        "items": items[:limit],
        "count": count,
        "total_matched": len(items),
        "basis_legend": [{"signal": k, "weight": w, "explanation": s}
                         for k, w, s in _RANK],
        "notes": _findings_notes(items),
    }


def _finding_headline(e: dict) -> str:
    """One plain-English sentence — what a non-engineer reads first.

    Written to be true rather than dramatic: it names who asserted what. The
    demo definition (CLAUDE.md §0) is that a non-engineer can click a vessel and
    read the reason it was flagged, so this sentence is the product.
    """
    name = e["name"] or e["id"]
    # Every clause is a verb phrase with the vessel as its subject, so they
    # compose into one readable sentence rather than a list of fragments.
    bits = []
    if e["dark_gaps"]:
        n = len(e["dark_gaps"])
        bits.append(f"had {n} AIS gap{'' if n == 1 else 's'} that Global "
                    f"Fishing Watch assessed as intentional disabling — the "
                    f"transponder went quiet and GFW judged it deliberate")
    findings = [s for s in e["sanctions"] if s.get("is_finding")]
    if findings:
        regs = " and ".join(sorted({s.get("registry") or "OFAC"
                                    for s in findings}))
        tier = "IMO" if any(s.get("match_tier") == "imo" for s in findings) \
            else "call sign and name"
        if all((s.get("listed_entity_type") or "vessel") != "vessel"
               for s in findings):
            # The designation names a company, not this hull. Saying "matches a
            # sanctions listing" would imply the ship itself is listed.
            owner = next((s.get("ofac_name") for s in findings
                          if s.get("ofac_name")), "a designated entity")
            bits.append(f"is owned or operated by {owner}, designated under "
                        f"{regs} — the hull itself is not listed")
        else:
            bits.append(f"matches a sanctions listing on {regs}, on {tier}")
    if any(b["signal"] == "name_disagreement" for b in e["basis"]):
        listed = next((s.get("ofac_name") for s in findings if s.get("ofac_name")),
                      None)
        if listed:
            bits.append(f"sails as {name} while listed as {listed}")
    if not bits:
        return f"{name} carries no stated basis."
    return f"{name} " + "; ".join(bits) + "."


def _findings_notes(items: list[dict]) -> list[str]:
    notes = []
    real = [i for i in items if not i["is_synthetic"]]
    syn = [i for i in items if i["is_synthetic"]]
    notes.append(
        f"{len(real)} finding(s) from the real corpus, {len(syn)} from the "
        "scenario corpus. Scenario rows are generated and prove the machinery "
        "runs; they are not evidence about real vessels (CLAUDE.md §4.6).")
    dark = [i for i in items if i["has_dark_gap"]]
    if dark:
        notes.append(
            f"{len(dark)} hull(s) carry a GFW intentional-disabling assessment. "
            "That is GFW's finding, carried through with attribution — we did "
            "not detect it. Our own dark-vessel detection needs SAR contacts "
            "matched against AIS tracks and has not produced a result on real "
            "data (CLAUDE.md §5).")
    else:
        notes.append(
            "No GFW intentional-disabling assessment in this corpus. The demo "
            "cannot show a real dark vessel from it.")
    return notes


def build_incident_report(canonical: str) -> Optional[dict]:
    """Everything the one-click incident report needs, for one vessel.

    Returns None when the vessel is unknown, so the route can 404 rather than
    render an empty dossier — a report about nothing is worse than no report.

    A vessel with no finding still gets one. An analyst looking at a hull wants
    to be able to hand over what is known about it, and refusing unless it is
    already flagged would make the export useless in exactly the case where
    someone is trying to establish whether it should be.
    """
    vessel = get_vessel(canonical)
    if vessel is None:
        return None
    # The findings pass is corpus-wide; on the real corpus that is four grouped
    # scans, the same cost as opening the findings page. Cheap enough for a
    # click, and it keeps one definition of what a finding is rather than a
    # second one that could drift from the screen it claims to mirror.
    finding = next((f for f in list_findings(limit=100_000)["items"]
                    if f["id"] == canonical), None)
    alerts = [a for a in gsvc.list_alerts() if a.get("subject") == canonical]
    return report.build_report(vessel=vessel, finding=finding, alerts=alerts,
                               stats={"corpus_window": _corpus_window_only()})


def _corpus_window_only() -> dict:
    """Just the window. `get_stats()` walks the graph and is far too much work
    for the one field the report needs from it."""
    with open_reader() as reader:
        return _corpus_window(reader)


# --------------------------------------------------------------------------
# SAR detections (radar contacts)
# --------------------------------------------------------------------------

def list_detections(*, limit: int = 5000) -> dict:
    """Radar contacts from processed SAR scenes.

    **Everything this returns today is synthetic.** `scenario_detections` is
    written by the scenario generator; no real SAR imagery has been processed
    (Phase 1 is deferred under ADR-017, and GFW's SAR datasets have been offline
    upstream since 2026-07-03). The endpoint exists so the fusion story is
    visible on the map — a radar contact with no AIS beside it is the shape of
    a dark-vessel detection — and so it needs no new wiring when real contacts
    arrive. The note says so rather than letting an empty real split imply the
    pipeline ran and found nothing.
    """
    with open_reader() as reader:
        if not reader.has("scenario_detections"):
            return {"items": [], "count": {"real": 0, "synthetic": 0},
                    "note": "no detection table landed — no SAR scene has been "
                            "processed (Phase 1 deferred, ADR-017)"}
        rows = reader.rows(
            f"SELECT * FROM scenario_detections ORDER BY ts NULLS LAST "
            f"LIMIT {int(limit)}")
    items = [{
        "id": _s(r.get("detection_id")),
        "scene_id": r.get("scene_id"),
        "lat": r.get("lat"),
        "lon": r.get("lon"),
        "ts": as_iso(r.get("ts")),
        "length_m": r.get("length_m"),
        "score": r.get("score"),
        # Null means no AIS track was associated with this contact. That is the
        # dark-vessel shape — but only inside demonstrated receiver coverage,
        # which is why the UI must not label it "dark" on its own (ADR-005).
        "matched_mmsi": _s(r.get("matched_mmsi")),
        "is_synthetic": _is_syn(r),
        "prov": _prov(r),
    } for r in rows]
    count = {"real": sum(1 for d in items if not d["is_synthetic"]),
             "synthetic": sum(1 for d in items if d["is_synthetic"])}
    note = None
    if count["real"] == 0 and count["synthetic"]:
        note = ("all contacts are synthetic — no real SAR scene has been "
                "processed. An unmatched contact is not by itself a dark "
                "vessel: that requires demonstrated AIS reception at the "
                "position (ADR-005).")
    return {"items": items, "count": count, "note": note}


# --------------------------------------------------------------------------
# event density — H3 aggregation for the map
# --------------------------------------------------------------------------

#: H3 resolutions the density endpoint will aggregate at. Res 4 hexes are
#: ~22 km across and res 6 ~3.2 km — coarse enough that 24,153 loitering events
#: become a few hundred cells instead of 24,153 overlapping dots.
DENSITY_RES = {4: "h3_r4", 6: "h3_r6", 7: "h3_r7"}


def event_density(*, res: int = 4, kinds: Optional[list[str]] = None,
                  synthetic: Optional[bool] = None) -> dict:
    """Per-H3-cell event counts, so the map can show the WHOLE corpus.

    The map used to request the first N events and draw them as individual
    dots. On the real corpus that silently truncated: 24,153 loitering events
    ordered by `start_time` against a 4,000-row request meant roughly the last
    five weeks of the window were simply not on screen, with nothing saying so.

    Aggregating server-side fixes both halves of that — the count is over every
    row, not a page, and a few hundred graduated hexagons read as a density
    surface where 24,000 identical dots read as a smear.
    """
    col = DENSITY_RES.get(res)
    if col is None:
        raise ValueError(f"unsupported resolution {res}; "
                         f"choose one of {sorted(DENSITY_RES)}")
    kinds = kinds or list(_EVENT_TABLES)
    cells: dict[str, dict] = {}
    missing_h3: list[str] = []
    with open_reader() as reader:
        for kind in kinds:
            table = _EVENT_TABLES.get(kind)
            if not table or not reader.has(table):
                continue
            if col not in reader.columns(table):
                # ADR-015: rows landed before the five-resolution fix carry only
                # r7 and r9. Say which table, do not silently drop it.
                missing_h3.append(table)
                continue
            clauses = [f"{col} IS NOT NULL"]
            params: list = []
            if synthetic is not None and "is_synthetic" in reader.columns(table):
                clauses.append("is_synthetic = ?")
                params.append(synthetic)
            where = " WHERE " + " AND ".join(clauses)
            for r in reader.rows(
                    f"SELECT {col} AS cell, "
                    "COALESCE(is_synthetic, FALSE) AS syn, "
                    "count(*) AS n, avg(lat) AS lat, avg(lon) AS lon "
                    f"FROM {table}{where} GROUP BY 1, 2", params):
                c = cells.setdefault(r["cell"], {
                    "cell": r["cell"], "lat": r["lat"], "lon": r["lon"],
                    "real": 0, "synthetic": 0, "by_kind": {}})
                n = int(r["n"])
                c["synthetic" if r["syn"] else "real"] += n
                c["by_kind"][kind] = c["by_kind"].get(kind, 0) + n

    items = sorted(cells.values(), key=lambda c: c["real"] + c["synthetic"],
                   reverse=True)
    count = {"real": sum(c["real"] for c in items),
             "synthetic": sum(c["synthetic"] for c in items)}
    note = None
    if missing_h3:
        note = (f"{', '.join(sorted(set(missing_h3)))} carry no {col} — those "
                "rows were landed before ADR-015 and are NOT counted here. "
                "Run `python tools/restamp_h3.py` to recompute the missing "
                "resolutions from lat/lon.")
    return {"items": items, "count": count, "res": res, "note": note}


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
    """Row count split real vs synthetic. Tolerates a table with no
    `is_synthetic` column (the real corpus has such tables) by counting it all
    as real — real rows are exactly what an absent flag means."""
    if not reader.has(table):
        return {"real": 0, "synthetic": 0}
    if "is_synthetic" not in reader.columns(table):
        n = reader.scalar(f"SELECT count(*) FROM {table}") or 0
        return {"real": int(n), "synthetic": 0}
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
    if "is_synthetic" not in reader.columns("gfw_vessel_identity"):
        n = reader.scalar(
            "SELECT count(DISTINCT vessel_id) FROM gfw_vessel_identity") or 0
        return {"real": int(n), "synthetic": 0}
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
            cols = reader.columns("sanctioned_vessel_matches")
            finding_clause = "WHERE is_finding" if "is_finding" in cols else ""
            if "is_synthetic" in cols:
                for r in reader.rows(
                    "SELECT COALESCE(is_synthetic,FALSE) s, count(*) n FROM "
                    f"sanctioned_vessel_matches {finding_clause} GROUP BY 1"):
                    findings["synthetic" if r["s"] else "real"] += int(r["n"])
            else:
                n = reader.scalar(
                    f"SELECT count(*) FROM sanctioned_vessel_matches "
                    f"{finding_clause}") or 0
                findings["real"] = int(n)
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
