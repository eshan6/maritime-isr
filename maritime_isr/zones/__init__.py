"""Phase 2.5 — the maritime zone layer (ADR-030).

Where the system stops knowing four hardcoded circles and starts knowing
maritime geography: exclusive economic zone, contiguous zone, territorial sea,
the international maritime boundary line, port limits, anchorages, oil
terminals, shipping lanes — and whatever the operator draws on top of them.

**A zone is not a picture.** The requirement this layer serves is not "draw the
EEZ", it is "tell me who was inside it, when, where they came in and where they
went out". So a zone is a queryable object with a cell index, and a drawn box
is the same kind of object as a statutory boundary — the rest of the system
cannot tell them apart and must not need to.

**The statutory limits are not built here and that is deliberate** — see
`derive.py`. EEZ, contiguous zone, territorial sea and the IMBL arrive through
`ingest/zones.py` from a real published file or they do not arrive at all. The
kinds exist, the analyses that need them are built and tested, and
`anchoring_analysis_status` names the gap out loud instead of letting an empty
result look like a clean one.

Read `geometry.py` first if you are changing anything spatial; it holds the one
containment test and the one cell-index rule, and both exist because getting
them slightly different in two places is how spatial joins silently return
nothing (ADR-015).
"""
from .model import (ZONE_KINDS, Zone, ZoneKind, ZONE_SET_VERSION,
                    kind_is_line, kind_render_order)
from .derive import STATUTORY_KINDS, build_operational_zones
from .geometry import (cells_covering, circle_polygon, contains,
                       corridor_polygon, geom_from_wkt, geom_to_wkt)
from .store import (CELL_TABLE, ZONE_TABLE, land_zones, load_zones,
                    zone_by_id, ZoneIndex)
from .transitions import ZONE_TRANSITION_TABLE, transitions_for_track
from .query import ZoneQuery, who_was_inside
from .analyses import (anchoring_analysis_status,
                       detect_anchored_outside_port_limits, detect_area_visits,
                       detect_lane_deviation, detect_maiden_visit)

__all__ = [
    "Zone", "ZoneKind", "ZONE_KINDS", "ZONE_SET_VERSION",
    "kind_is_line", "kind_render_order",
    "circle_polygon", "corridor_polygon", "cells_covering", "contains",
    "geom_to_wkt", "geom_from_wkt",
    "ZONE_TABLE", "CELL_TABLE", "land_zones", "load_zones", "zone_by_id",
    "ZoneIndex",
    "ZONE_TRANSITION_TABLE", "transitions_for_track",
    "who_was_inside", "ZoneQuery",
    "detect_area_visits", "detect_maiden_visit", "detect_lane_deviation",
    "detect_anchored_outside_port_limits", "anchoring_analysis_status",
    "build_operational_zones", "STATUTORY_KINDS",
]
