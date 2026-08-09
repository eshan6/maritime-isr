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
