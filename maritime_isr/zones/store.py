"""Landing and loading the zone layer, and the in-memory index over it.

Zones go through the **same landing layer as every connector** — provenance
envelope, H3 stamping, day-partitioned Parquet, idempotent on the natural key.
A geometry layer that lived in a Python literal would be exempt from every
discipline this project applies to data, and the first question an analyst asks
about a boundary is where it came from and when.

`ZoneIndex` is the read side: a cell → zones map plus the exact geometries, so
"which zones is this position in" is a dict lookup followed by a containment
test on a handful of candidates rather than a sweep over every polygon. That is
the CLAUDE.md §3 join, applied to geography.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Optional, Sequence

from ..ingest.landing import (land_table, read_table, stamp_envelope,
                              stamp_h3)
from ..schemas import utcnow
from .geometry import (INDEX_RES, area_km2, cells_covering, centroid_latlon,
                       contains, geom_from_wkt)
from .model import ZONE_SET_VERSION, Zone, kind_is_line

__all__ = ["ZONE_TABLE", "CELL_TABLE", "land_zones", "load_zones",
           "zone_by_id", "ZoneIndex", "OPERATOR_AUTHORITY",
           "clear_standing_zones"]

ZONE_TABLE = "maritime_zone"
CELL_TABLE = "maritime_zone_cell"

#: What an operator-drawn area carries in `authority`. A constant because three
#: places need to recognise one — the API when it saves, the UI when it colours,
#: and `land_zones` when it decides whether a row may be deleted.
OPERATOR_AUTHORITY = "operator"


def land_zones(zones: Sequence[Zone], *, source_id: str = "zone-layer",
               source_ref: str = ZONE_SET_VERSION,
               is_synthetic: bool = False) -> dict[str, dict[str, int]]:
    """Land zones and their cell index.

    Idempotent on `zone_id`, so rebuilding the standing set does not duplicate
    it. **The cell rows are keyed on (zone_id, h3_r6)**, which means a zone
    whose geometry shrinks leaves stale cells behind — so the caller is
    expected to clear the table when the derivation changes, and
    `zone_set_version` exists to make that visible when they forget.
    """
    now = utcnow()
    zrows, crows = [], []
    for z in zones:
        geom = geom_from_wkt(z.wkt)
        lat, lon = centroid_latlon(geom)
        row = dict(
            zone_id=z.zone_id, zone_set_version=ZONE_SET_VERSION,
            kind=z.kind, name=z.name, facility=z.facility, wkt=z.wkt,
            authority=z.authority, method=z.method, note=z.note,
            area_km2=round(area_km2(geom), 2),
            centroid_lat=round(lat, 6), centroid_lon=round(lon, 6),
            n_cells=len(z.cells), lat=lat, lon=lon)
        stamp_envelope(row, source_id=source_id, source_ref=source_ref,
                       acquired_at=now, confidence=z.confidence,
                       is_synthetic=z.is_synthetic or is_synthetic)
        row = stamp_h3(row)
        # `lat`/`lon` were only carried so `stamp_h3` had something to index;
        # the schema keeps the centroid under its own names.
        row.pop("lat", None)
        row.pop("lon", None)
        zrows.append(row)
        for c in z.cells:
            crow = dict(zone_id=z.zone_id, zone_set_version=ZONE_SET_VERSION,
                        kind=z.kind, h3_r6=c)
            stamp_envelope(crow, source_id=source_id, source_ref=source_ref,
                           acquired_at=now, confidence=z.confidence,
                           is_synthetic=z.is_synthetic or is_synthetic)
            crows.append(crow)

    # Returned per table AND per day partition, which is the shape
    # `ingest.checks.report_landed` needs to explain a gap between rows built
    # and rows landed. Flattening it here would make the honesty check
    # impossible for the caller to run.
    written: dict[str, dict[str, int]] = {}
    if zrows:
        written[ZONE_TABLE] = land_table(
            zrows, table=ZONE_TABLE, key_fields=("zone_id",),
            day_field="acquired_at")
    if crows:
        written[CELL_TABLE] = land_table(
            crows, table=CELL_TABLE, key_fields=("zone_id", "h3_r6"),
            day_field="acquired_at")
    return written


def load_zones(kinds: Optional[Iterable[str]] = None) -> list[Zone]:
    """Every landed zone, with its cell index reattached.

    Cells are read back from `maritime_zone_cell` rather than recomputed,
    because recomputing would make the query answer depend on the code that
    happens to be installed rather than on what was landed — which is the same
    reproducibility rule raw/derived data follows everywhere else here.
    """
    try:
        rows = read_table(ZONE_TABLE)
    except Exception:                                            # noqa: BLE001
        return []
    if not rows:
        return []
    want = set(kinds) if kinds else None
    try:
        cell_rows = read_table(CELL_TABLE)
    except Exception:                                            # noqa: BLE001
        cell_rows = []
    by_zone: dict[str, set[str]] = defaultdict(set)
    for r in cell_rows:
        by_zone[str(r.get("zone_id"))].add(str(r.get("h3_r6")))

    out: list[Zone] = []
    for r in rows:
        kind = str(r.get("kind") or "")
        if want is not None and kind not in want:
            continue
        zid = str(r.get("zone_id"))
        cells = by_zone.get(zid)
        if not cells:
            # A zone with no landed cells would be invisible to every query.
            # Rebuilding the index here is a repair, not a shortcut, and it is
            # loud in the only way a library can be: the zone still works.
            cells = set(cells_covering(geom_from_wkt(str(r.get("wkt")))))
        out.append(Zone(
            zone_id=zid, kind=kind, name=str(r.get("name") or ""),
            wkt=str(r.get("wkt") or ""),
            authority=str(r.get("authority") or ""),
            method=str(r.get("method") or ""),
            confidence=float(r.get("confidence") or 0.0),
            cells=frozenset(cells),
            facility=(str(r["facility"]) if r.get("facility") else None),
            note=str(r.get("note") or ""),
            is_synthetic=bool(r.get("is_synthetic")),
        ))
    return out


def zone_by_id(zone_id: str) -> Optional[Zone]:
    for z in load_zones():
        if z.zone_id == zone_id:
            return z
    return None


class ZoneIndex:
    """Cell → candidate zones, plus the exact geometries.

    Built once and reused. The two-stage test is the whole design: the cell
    lookup is O(1) and over-selects; `contains` is exact and runs on a handful
    of candidates. Either half alone is wrong — the cells are not the geometry,
    and sweeping every polygon for every fix is quadratic in a corpus with two
    hundred thousand positions.
    """

    def __init__(self, zones: Sequence[Zone]):
        self.zones = list(zones)
        self._by_id = {z.zone_id: z for z in self.zones}
        self._geom = {z.zone_id: geom_from_wkt(z.wkt) for z in self.zones}
        self._by_cell: dict[str, list[str]] = defaultdict(list)
        for z in self.zones:
            # A line has no inside. Indexing it here would make it a candidate
            # for a containment test that can only ever answer False, so it is
            # kept out of the membership index and reachable by id.
            if kind_is_line(z.kind):
                continue
            for c in z.cells:
                self._by_cell[c].append(z.zone_id)

    def __len__(self) -> int:
        return len(self.zones)

    def get(self, zone_id: str) -> Optional[Zone]:
        return self._by_id.get(zone_id)

    def geometry(self, zone_id: str):
        return self._geom.get(zone_id)

    def zones_at(self, lat: float, lon: float,
                 kinds: Optional[Iterable[str]] = None) -> list[Zone]:
        """Every zone containing this position, exactly.

        Ordered by render order then name so the answer is stable — an
        analyst comparing two fixes should not see the same two zones swap
        places because a dict iterated differently.
        """
        from .. import h3util as tiling
        cell = tiling.cell(lat, lon, INDEX_RES)
        want = set(kinds) if kinds else None
        hits: list[Zone] = []
        for zid in self._by_cell.get(cell, ()):
            z = self._by_id[zid]
            if want is not None and z.kind not in want:
                continue
            if contains(self._geom[zid], lat, lon):
                hits.append(z)
        hits.sort(key=lambda z: (z.render_order, z.name))
        return hits

    def ids_at(self, lat: float, lon: float,
               kinds: Optional[Iterable[str]] = None) -> frozenset[str]:
        return frozenset(z.zone_id for z in self.zones_at(lat, lon, kinds))

    def of_kind(self, kind: str) -> list[Zone]:
        return [z for z in self.zones if z.kind == kind]


def clear_standing_zones(rebuild_ids) -> int:
    """Drop exactly the zones about to be rewritten, and nothing else.

    **The first version of this took no argument and dropped every zone whose
    authority was not `operator`. That would have deleted an imported
    territorial sea** — a published boundary that came from outside this
    project, cannot be regenerated by it, and on a download-only laptop may
    have cost someone a 50 MB download and a registration form. `zones build`
    would have silently destroyed the one thing in the layer this project is
    not allowed to invent.

    Taking the id set the builder is about to write makes the operation
    precise: it can only remove what it is immediately replacing. An imported
    boundary, an operator geofence, and anything else a future connector lands
    are all untouched because none of them is in that set.

    The clear is still needed. `land_zones` merges cells on
    `(zone_id, h3_r6)`, so a zone whose circle shrank keeps every cell the
    larger circle claimed — measured after the port radii were capped, the cell
    table did not move, 14,270 rows before and after, because none of the old
    rows had been asked to leave.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    from ..ingest.landing import conformed_dir

    drop = set(rebuild_ids)
    if not drop:
        return 0
    removed = 0
    for table in (ZONE_TABLE, CELL_TABLE):
        root = conformed_dir(table)
        if not root.exists():
            continue
        for part in sorted(root.glob("day=*")):
            for f in sorted(part.glob("*.parquet")):
                tbl = pq.read_table(f)
                if "zone_id" not in tbl.column_names:
                    continue
                mask = pa.array([i not in drop for i in tbl["zone_id"].to_pylist()])
                kept = tbl.filter(mask)
                if kept.num_rows == tbl.num_rows:
                    continue
                removed += tbl.num_rows - kept.num_rows
                tmp = f.with_suffix(".parquet.tmp")
                pq.write_table(kept, tmp)
                tmp.replace(f)
    return removed
