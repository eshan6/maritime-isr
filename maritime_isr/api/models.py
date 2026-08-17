"""API response contracts.

These are the shapes the frontend is built against. Two rules from the operating
contract are encoded structurally here rather than left to discipline:

  * **Every object carrying vessel or edge data exposes `is_synthetic` and the
    full provenance envelope** (ADR-019, CLAUDE.md §4.1). The envelope is a
    required nested model, not a scatter of optional fields, so a row cannot be
    serialised without it.
  * **Counts are never blended.** Every count is a `SplitCount(real, synthetic)`;
    there is no `total` field anywhere. A caller that wants a total sums the two
    explicitly and owns that decision.

Sanctions carry their match tier and confidence explicitly, and a boolean
`is_finding`, so a name-only candidate can never be rendered as a confirmed
finding (ADR-018). Dark/gap determinations carry an `attribution` string naming
their source, because on this corpus that source is Global Fishing Watch and the
UI must say so, not imply our own detection (ADR-017/018).
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


# --------------------------------------------------------------------------
# envelopes shared by every provenanced record
# --------------------------------------------------------------------------

class Provenance(BaseModel):
    """The six-field envelope on every landed row (CLAUDE.md §4.1)."""
    source_id: Optional[str] = None
    source_ref: Optional[str] = None
    acquired_at: Optional[str] = None
    ingested_at: Optional[str] = None
    pipeline_version: Optional[str] = None
    confidence: Optional[float] = None


class SplitCount(BaseModel):
    """A count that is always real vs synthetic, never a blended total (ADR-019)."""
    real: int = 0
    synthetic: int = 0


# --------------------------------------------------------------------------
# vessels
# --------------------------------------------------------------------------

class SanctionsMatch(BaseModel):
    """A vessel↔OFAC match, with the tier that decides finding vs candidate.

    `is_finding` is true only for an IMO or call-sign+name match (ADR-018). A
    name-only row is a *candidate*: same shape, `is_finding=False`, lower
    confidence. The UI must key its red treatment on `is_finding`, not on the
    mere presence of this object.
    """
    match_tier: Optional[str] = None
    is_finding: bool = False
    confidence: Optional[float] = None
    ofac_program: Optional[str] = None
    ofac_name: Optional[str] = None
    ofac_owner: Optional[str] = None
    ofac_ent_num: Optional[str] = None
    vessel_name: Optional[str] = None
    vessel_flag: Optional[str] = None
    vessel_imo: Optional[str] = None
    registry: Optional[str] = None
    #: What the sanctions list designated — "vessel" (the hull itself is
    #: listed) or "organisation" (the hull is reached through its owner). It
    #: decides what `ofac_name` holds, and therefore whether a name difference
    #: is the identity-laundering signal or just a ship not being a company.
    listed_entity_type: Optional[str] = None
    sanctions_as_of: Optional[str] = None
    is_synthetic: bool = False


class VesselSummary(BaseModel):
    """One row in the vessels table and the map's vessel layer."""
    id: str                          # canonical graph node id, e.g. vessel:gfw:spine
    name: Optional[str] = None
    mmsi: Optional[str] = None
    imo: Optional[str] = None
    flag: Optional[str] = None
    vessel_type: Optional[str] = None
    length_m: Optional[float] = None       # null on ~98.6% of the real corpus
    risk_score: Optional[float] = None     # null when the graph has no node for it
    sanctioned: bool = False               # any match at all (finding OR candidate)
    sanctions_is_finding: bool = False     # a confirmed finding specifically
    last_seen: Optional[str] = None
    is_synthetic: bool = False
    prov: Provenance


class IdentityInterval(BaseModel):
    """One row of identity history, time-scoped (CLAUDE.md §4.3)."""
    name: Optional[str] = None
    mmsi: Optional[str] = None
    imo: Optional[str] = None
    flag: Optional[str] = None
    call_sign: Optional[str] = None
    vessel_class: Optional[str] = None
    length_m: Optional[float] = None
    width_m: Optional[float] = None
    tonnage_gt: Optional[float] = None
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    superseded: bool = False
    is_synthetic: bool = False
    prov: Provenance


class RiskComponent(BaseModel):
    weight: float
    value: float
    weighted: float


class RiskEvidence(BaseModel):
    kind: str
    detail: Optional[str] = None
    disposition: Optional[str] = None
    contribution: float


class RiskDecomposition(BaseModel):
    """A risk score is never a naked number — it ships with its parts."""
    risk_score: float
    components: dict[str, RiskComponent]
    evidence: list[RiskEvidence]


class VesselEvent(BaseModel):
    """A port call, encounter or gap on a vessel's detail page."""
    kind: str                        # port_visit | encounter | gap | loitering
    lat: Optional[float] = None
    lon: Optional[float] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    duration_hours: Optional[float] = None
    place: Optional[str] = None            # port/anchorage name where known
    counterpart_name: Optional[str] = None
    distance_from_shore_km: Optional[float] = None
    classification: Optional[str] = None   # gap: intentional-disabling vs coverage
    attribution: Optional[str] = None      # who determined this (e.g. GFW)
    is_synthetic: bool = False
    prov: Provenance


class VesselDetail(BaseModel):
    id: str
    current: IdentityInterval
    identity_history: list[IdentityInterval]
    sanctions: list[SanctionsMatch]
    risk: Optional[RiskDecomposition] = None
    port_calls: list[VesselEvent]
    encounters: list[VesselEvent]
    gaps: list[VesselEvent]
    is_synthetic: bool = False
    prov: Provenance


class TrackPoint(BaseModel):
    ts: str
    lat: float
    lon: float
    sog_kn: Optional[float] = None
    cog_deg: Optional[float] = None


class VesselTrack(BaseModel):
    vessel_id: str
    is_synthetic: bool = False
    window_start: Optional[str] = None
    window_end: Optional[str] = None
    points: list[TrackPoint]
    note: Optional[str] = None       # e.g. "no AIS — offshore, no terrestrial reception"


# --------------------------------------------------------------------------
# graph neighbourhood
# --------------------------------------------------------------------------

class GraphNode(BaseModel):
    id: str
    node_type: str
    label: Optional[str] = None
    is_synthetic: bool = False
    props: dict = {}


class GraphEdge(BaseModel):
    source: str
    target: str
    edge_type: str
    confidence: float
    t_start: Optional[str] = None
    t_end: Optional[str] = None
    is_synthetic: bool = False


class Neighbourhood(BaseModel):
    seed: str
    hops: int
    truncated: bool                  # was the traversal budget hit?
    budget: int
    nodes: list[GraphNode]
    edges: list[GraphEdge]


# --------------------------------------------------------------------------
# alerts
# --------------------------------------------------------------------------

class EvidenceHop(BaseModel):
    edge: Optional[str] = None
    src: Optional[str] = None
    dst: Optional[str] = None
    confidence: Optional[float] = None
    t_start: Optional[str] = None
    t_end: Optional[str] = None
    source: Optional[str] = None
    detail: Optional[str] = None
    props: dict = {}


class Alert(BaseModel):
    id: str
    rule: Optional[str] = None
    anomaly_type: Optional[str] = None
    subject: str                     # vessel node id
    subject_name: Optional[str] = None
    ts: Optional[str] = None
    confidence: Optional[float] = None
    score: Optional[float] = None
    disposition: str = "open"
    evidence: list[EvidenceHop]
    is_synthetic: bool = False


class Disposition(BaseModel):
    alert_id: str
    disposition: str


# --------------------------------------------------------------------------
# events / scenes / ports / stats
# --------------------------------------------------------------------------

class Event(BaseModel):
    """A map/timeline event. `id` is stable enough to key a map feature."""
    id: str
    kind: str                        # encounter | loitering | port_visit | gap
    vessel_id: Optional[str] = None
    mmsi: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    duration_hours: Optional[float] = None
    place: Optional[str] = None
    distance_from_shore_km: Optional[float] = None
    classification: Optional[str] = None
    attribution: Optional[str] = None
    is_synthetic: bool = False
    prov: Provenance


class DensityCell(BaseModel):
    """Events aggregated into one H3 cell, so the map can show the whole corpus.

    Counts are over every matching row, not a page — that is the point of this
    shape. `lat`/`lon` are the mean event position inside the cell, which is
    good enough to place a graduated marker and avoids shipping hex geometry.
    """
    cell: str
    lat: Optional[float] = None
    lon: Optional[float] = None
    real: int = 0
    synthetic: int = 0
    by_kind: dict[str, int] = {}


class Detection(BaseModel):
    """A radar contact from a processed SAR scene.

    `matched_mmsi` null means no AIS track was associated. That is the *shape*
    of a dark vessel, not a dark vessel: asserting intentional silence requires
    demonstrated receiver coverage at the position (ADR-005, CLAUDE.md §6), so
    the UI must not label an unmatched contact dark on its own.
    """
    id: Optional[str] = None
    scene_id: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    ts: Optional[str] = None
    length_m: Optional[float] = None
    score: Optional[float] = None
    matched_mmsi: Optional[str] = None
    is_synthetic: bool = False
    prov: Provenance


class FindingBasis(BaseModel):
    """One stated reason a finding ranks where it does.

    Ranking is an ordered list of facts, not a blended score, and every fact
    that moved a row up is returned with it. An analyst reads the reason rather
    than trusting a float (CLAUDE.md §4.1 — a flag you cannot trace is worse
    than no flag).
    """
    signal: str
    weight: int
    explanation: str


class ImagingOpportunity(BaseModel):
    """A Sentinel-1 pass acquired while a vessel's AIS was off (ADR-026).

    **The one determination in this payload that is ours** rather than a third
    party's — and it is strictly about where a satellite was pointed. `tier`
    `confirmed` means an image exists whose footprint necessarily contained the
    vessel; it does **not** mean anything was found in it, because the pixels
    are not downloaded and nobody has looked. `coverage_fraction` is a fraction
    of **area**, never a probability that the vessel was seen.

    `statement` carries the sentence written once in `overpass.py` so the API,
    the UI and the exported report cannot drift into three different phrasings
    of the same claim.
    """
    tier: str
    scene_id: Optional[str] = None
    scene_acquired_at: Optional[str] = None
    hours_into_gap: Optional[float] = None
    coverage_fraction: Optional[float] = None
    reachable_area_km2: Optional[float] = None
    covered_area_km2: Optional[float] = None
    geometry_basis: Optional[str] = None
    scene_has_pixels: Optional[bool] = None
    orbit_direction: Optional[str] = None
    v_max_knots: Optional[float] = None
    implied_speed_exceeds_vmax: Optional[bool] = None
    statement: Optional[str] = None
    is_synthetic: bool = False
    prov: Optional[Provenance] = None


class FindingGap(BaseModel):
    """An AIS gap GFW assessed as intentional disabling.

    `attribution` is required reading: this is **GFW's** assessment carried
    through, not our detection. We did not compute it and have no coverage
    model at these positions (CLAUDE.md §6).

    `imaging` is the separate, ours-not-theirs layer: which satellites could
    have photographed the vessel during this silence. It is evidence attached
    to the gap and deliberately does not contribute to the finding's rank —
    see ADR-026(d).
    """
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    duration_hours: Optional[float] = None
    off_lat: Optional[float] = None
    off_lon: Optional[float] = None
    on_lat: Optional[float] = None
    on_lon: Optional[float] = None
    distance_km: Optional[float] = None
    distance_from_shore_km: Optional[float] = None
    attribution: str
    imaging: list[ImagingOpportunity] = []
    imaging_best_tier: Optional[str] = None
    is_synthetic: bool = False
    prov: Provenance


class Finding(BaseModel):
    """One row of the ranked findings table — the M6 demo's primary product.

    This is the screen `graph_report.py` concluded the landed data supports: on
    the real corpus the encounter graph is star-shaped, so a network view has
    nothing to draw and a ranked list does. `headline` is the plain-English
    sentence a non-engineer reads first, and it names who asserted what.
    """
    id: str
    name: Optional[str] = None
    mmsi: Optional[str] = None
    imo: Optional[str] = None
    flag: Optional[str] = None
    vessel_type: Optional[str] = None
    priority: int
    headline: str
    attribution: str
    basis: list[FindingBasis]
    has_dark_gap: bool = False
    sanctions_is_finding: bool = False
    registries: list[str] = []
    dark_gaps: list[FindingGap] = []
    sanctions: list[SanctionsMatch] = []
    event_counts: dict[str, int] = {}
    ports: list[str] = []
    is_synthetic: bool = False
    prov: Provenance


class Scene(BaseModel):
    scene_id: str
    footprint_wkt: Optional[str] = None
    acquired_at: Optional[str] = None
    orbit_direction: Optional[str] = None
    relative_orbit: Optional[int] = None
    is_synthetic: bool = False
    prov: Provenance


class Port(BaseModel):
    id: str
    name: Optional[str] = None
    flag: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    source: str                      # graph | scenario | wpi
    is_synthetic: bool = False


class RadarStation(BaseModel):
    """One coastal radar site and the two rings that bracket what it holds.

    There is no single coverage radius — the radar horizon depends on how tall
    the *target* is, so a 250 m tanker is held roughly twice as far out as a
    15 m skiff. Shipping both bounds is the only honest way to draw the ring;
    a single circle would either promise skiff coverage the site does not have
    or hide tanker coverage it does.
    """
    station_id: str
    name: str
    lat: float
    lon: float
    max_range_km: float
    range_small_km: float            # horizon for a ~15 m target
    range_large_km: float            # horizon for a ~250 m target
    shadow_sectors: list[list[float]] = []
    is_synthetic: bool = True


class RadarContact(BaseModel):
    """A radar track with no matching broadcaster — a dark candidate.

    `went_dark_at/lat/lon` are populated only for a contact that WAS correlated
    to an MMSI and then lost it: that pair is "here is where its transponder
    went quiet", and it is null for a target that never transmitted at all.
    Nulls here are meaningful, not missing data.
    """
    candidate_id: Optional[str] = None
    radar_track_id: Optional[str] = None
    station_ids: Optional[str] = None
    ts: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    length_m: Optional[float] = None
    status: Optional[str] = None
    dark_score: Optional[float] = None
    hearable_conf: Optional[float] = None
    dark_minutes: Optional[float] = None
    correlation_status: Optional[str] = None
    went_dark_at: Optional[str] = None
    went_dark_lat: Optional[float] = None
    went_dark_lon: Optional[float] = None
    mmsi: Optional[int] = None
    is_synthetic: bool = True


class MaritimeZone(BaseModel):
    """One zone, with the claim about its origin attached.

    `authority`, `method`, `confidence` and `note` are returned on every zone
    and are not optional decoration. Three quite different kinds of geometry
    share this table — published boundaries, working circles this project drew
    at the right scale, and areas the operator drew — and a client that cannot
    tell them apart will eventually render a 10 km circle labelled "port area"
    as though it were a declared limit.
    """
    zone_id: str
    kind: str
    name: str
    facility: Optional[str] = None
    authority: str
    method: str
    note: str = ""
    confidence: float
    is_line: bool = False
    render_order: int = 50
    n_cells: int = 0
    geometry: dict                    # GeoJSON
    is_synthetic: bool = False


class ZoneVisitRow(BaseModel):
    """One vessel's presence in one zone.

    `entry_censored` says the track was already inside when it began, so the
    entry position is where we picked her up rather than where she crossed.
    A client that ignores it will draw a boundary crossing in open water.
    """
    track_id: Optional[str] = None
    track_key: Optional[str] = None
    track_source: Optional[str] = None
    mmsi: Optional[int] = None
    t_enter: Optional[str] = None
    t_exit: Optional[str] = None
    dwell_min: Optional[float] = None
    entry_lat: Optional[float] = None
    entry_lon: Optional[float] = None
    entry_bearing_deg: Optional[float] = None
    exit_lat: Optional[float] = None
    exit_lon: Optional[float] = None
    exit_bearing_deg: Optional[float] = None
    entry_censored: bool = False
    exit_censored: bool = False
    min_sog_kn: Optional[float] = None
    mean_sog_kn: Optional[float] = None
    n_fixes: Optional[int] = None
    is_synthetic: bool = False


class GeofenceRequest(BaseModel):
    """An area the operator drew. GeoJSON geometry, because that is what a map
    hands back and converting it twice is two chances to lose a ring."""
    name: str
    geometry: dict
    note: str = ""


class Stats(BaseModel):
    """Every figure the demo could quote, split real vs synthetic (ADR-019)."""
    vessels: SplitCount
    events: dict[str, SplitCount]    # by kind
    alerts: SplitCount
    alerts_by_type: dict[str, SplitCount]
    sanctions_matches: SplitCount
    sanctions_findings: SplitCount
    scenes: SplitCount
    ports: SplitCount
    graph_nodes: SplitCount
    graph_edges: SplitCount
    corpus_window: dict[str, Optional[str]]
    notes: list[str]
