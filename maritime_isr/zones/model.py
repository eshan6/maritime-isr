"""What a zone is, and what the layer is allowed to claim about one.

**The provenance fields are not decoration here — they are the whole reason
this layer is safe to ship.** Three quite different kinds of geometry live in
one table:

  * limits *derived* by this project from a coastline and a UNCLOS distance,
    which are approximations and must never be presented as surveyed;
  * lines and facilities *transcribed* from published positions, which are as
    good as the publication and no better;
  * areas the *operator drew*, which are exactly as authoritative as the
    operator.

A consumer that cannot tell those apart will eventually put a derived EEZ edge
in front of someone who reads it as a legal boundary. So `authority`,
`method` and `confidence` travel on every row, the API returns them, and the
UI is required to show the derived ones as approximations.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pyarrow as pa

from ..schemas import _PROV

#: Bumped when the *meaning* of the standing zone set changes — a new kind, a
#: changed derivation rule, a corrected boundary. Landed on every row, so a
#: query can say which version of the world it was answered against and two
#: versions can coexist on disk while the corpus is re-derived.
#:
#: This is a version of the ZONE SET, not of the schema. The schema version
#: lives in `schemas.SCHEMA_VERSION` like every other table's.
ZONE_SET_VERSION = "zones-v1"

#: The kinds of zone the layer holds. Kept as a closed vocabulary rather than a
#: free string because the analyses select on it: "anchored outside port
#: limits" has to be able to ask for *port limits* and get port limits, and a
#: typo in a kind name would produce an empty selection and a rule that never
#: fires — the silent-failure shape this project keeps finding.
ZONE_KINDS: tuple[str, ...] = (
    # --- statutory / derived maritime limits ------------------------------
    "eez",                # exclusive economic zone, 200 nm
    "contiguous_zone",    # 24 nm
    "territorial_sea",    # 12 nm
    "imbl",               # international maritime boundary line (a LINE)
    # --- facilities --------------------------------------------------------
    "port_limit",
    "anchorage",
    "oil_terminal",       # terminals and single point moorings
    # --- traffic -----------------------------------------------------------
    "shipping_lane",
    # --- everything else ---------------------------------------------------
    "sensitive_area",     # cable approaches, exercise areas, infrastructure
    "geofence",           # operator-drawn
)

ZoneKind = str

#: Draw order, back to front. A watchkeeper needs to see boundaries without
#: losing the traffic underneath them, which means the big statutory areas are
#: washes at the back and the small facilities are outlines at the front. This
#: lives beside the vocabulary so a new kind cannot be added without someone
#: deciding where it sits.
_RENDER_ORDER: dict[str, int] = {
    "eez": 0,
    "contiguous_zone": 1,
    "territorial_sea": 2,
    "shipping_lane": 3,
    "sensitive_area": 4,
    "port_limit": 5,
    "anchorage": 6,
    "oil_terminal": 7,
    "imbl": 8,
    "geofence": 9,        # the operator's own work goes on top of everything
}

#: Kinds whose geometry is a LINE, not an area. A boundary line has no inside,
#: so "who was in it" is meaningless and the containment test must refuse
#: rather than quietly answer False for every vessel afloat.
_LINE_KINDS = frozenset({"imbl"})


def kind_is_line(kind: str) -> bool:
    return kind in _LINE_KINDS


def kind_render_order(kind: str) -> int:
    return _RENDER_ORDER.get(kind, 50)


@dataclass(frozen=True)
class Zone:
    """One area (or line) with a name, a geometry and a claim about its origin.

    `cells` is the res-6 H3 covering, **dilated by one ring**. It is an index,
    not the geometry: membership is `cell in zone.cells` to get a candidate and
    then an exact containment test to confirm. The dilation is what makes the
    two-stage test sound — a port limit 5 km across is smaller than a res-6
    cell, so an undilated covering can miss the cell a vessel is actually in,
    and the exact test would then never be reached. See `geometry.cells_covering`.
    """
    zone_id: str
    kind: ZoneKind
    name: str
    wkt: str
    #: Who says so. `derived:maritime-isr` for anything this project computed,
    #: a publication name for anything transcribed, `operator` for drawn areas.
    authority: str
    #: How the geometry was arrived at, in one line an analyst can read.
    method: str
    #: How much to trust the *geometry* — not how important the zone is.
    confidence: float
    cells: frozenset[str] = field(default_factory=frozenset)
    #: For facility zones: the gazetteer name this zone belongs to, so a port
    #: limit and its anchorage can be related without string-matching names.
    facility: Optional[str] = None
    #: Free-form, shown in the UI. This is where a caveat that matters goes —
    #: "the Sir Creek terminus is disputed" belongs on the row, not in a
    #: docstring nobody reads at 0300.
    note: str = ""
    is_synthetic: bool = False

    @property
    def render_order(self) -> int:
        return kind_render_order(self.kind)

    @property
    def is_line(self) -> bool:
        return kind_is_line(self.kind)


# --------------------------------------------------------------------------
# landed schemas
# --------------------------------------------------------------------------

#: One row per zone. The geometry rides as WKT rather than as a blob because a
#: human debugging a boundary needs to be able to read it out of the Parquet
#: file, and because DuckDB can parse it without this project shipping a
#: geometry extension.
MARITIME_ZONE = pa.schema([
    pa.field("zone_id", pa.string()),
    pa.field("zone_set_version", pa.string()),
    pa.field("kind", pa.string()),
    pa.field("name", pa.string()),
    pa.field("facility", pa.string()),
    pa.field("wkt", pa.string()),
    pa.field("authority", pa.string()),
    pa.field("method", pa.string()),
    pa.field("note", pa.string()),
    pa.field("area_km2", pa.float64()),
    pa.field("centroid_lat", pa.float64()),
    pa.field("centroid_lon", pa.float64()),
    pa.field("n_cells", pa.int64()),
    pa.field("h3_cell", pa.string()),          # centroid cell, for the map
    *_PROV,
])

#: The join table. One row per (zone, res-6 cell) — this is the hash join
#: CLAUDE.md §3 says the architecture exists to make cheap, applied to the
#: question "which zones is this position in".
MARITIME_ZONE_CELL = pa.schema([
    pa.field("zone_id", pa.string()),
    pa.field("zone_set_version", pa.string()),
    pa.field("kind", pa.string()),
    pa.field("h3_r6", pa.string()),
    *_PROV,
])

#: A vessel crossed a zone boundary. A first-class event, on the same footing
#: as an encounter or a gap, because the behavioural rules have to be able to
#: reason about it and the graph has to be able to hold it.
#:
#: `entry_bearing_deg` / `exit_bearing_deg` are what make "entering from where
#: and leaving to where" answerable — the requirement asks for the direction of
#: travel across the boundary, not merely the fact of it.
ZONE_TRANSITION = pa.schema([
    pa.field("transition_id", pa.string()),
    pa.field("zone_id", pa.string()),
    pa.field("zone_kind", pa.string()),
    pa.field("zone_name", pa.string()),
    pa.field("track_id", pa.string()),
    pa.field("track_key", pa.string()),
    pa.field("track_source", pa.string()),     # ais | radar
    pa.field("mmsi", pa.int64()),              # null when the sensor has none
    pa.field("t_enter", pa.timestamp("us", tz="UTC")),
    pa.field("t_exit", pa.timestamp("us", tz="UTC")),   # null = still inside
    pa.field("dwell_min", pa.float64()),
    pa.field("entry_lat", pa.float64()),
    pa.field("entry_lon", pa.float64()),
    pa.field("entry_bearing_deg", pa.float64()),
    pa.field("exit_lat", pa.float64()),
    pa.field("exit_lon", pa.float64()),
    pa.field("exit_bearing_deg", pa.float64()),
    pa.field("min_sog_kn", pa.float64()),
    pa.field("mean_sog_kn", pa.float64()),
    pa.field("n_fixes", pa.int64()),
    # True when the track's FIRST fix is already inside the zone: we did not
    # see her cross, so the entry position is where we picked her up, not where
    # she came in. Anything reasoning about entry direction must respect this
    # or it will report the middle of a zone as a boundary crossing.
    pa.field("entry_censored", pa.bool_()),
    pa.field("exit_censored", pa.bool_()),
    pa.field("h3_cell", pa.string()),
    *_PROV,
])
