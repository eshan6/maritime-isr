"""Maritime ISR Phase 0 configuration.

AOI v1 per roadmap: Arabian Sea + Indian west-coast EEZ, 5N-25N, 60E-78E.
PIPELINE_VERSION is stamped into provenance on every record. Bump on any
code change that alters outputs — reproducibility depends on it.
"""
from dataclasses import dataclass
from pathlib import Path

PIPELINE_VERSION = "0.7.0"

# ---- Phase 5: anomaly library + risk constants ------------------------
# Each anomaly type ships with its OWN precision gate (roadmap 5.2): an
# anomaly only alerts above its threshold, and thresholds move only as
# measured precision holds (launch posture 3.3, applied per-detector).
ANOMALY_THRESHOLDS = {
    "dark_vessel":          0.50,
    "ais_spoofing":         0.55,
    "dark_rendezvous":      0.50,
    "loitering_sensitive":  0.60,
    "identity_then_anomaly":0.45,
    "port_risk_propagation":0.50,
}
RISK_HALF_LIFE_DAYS = 30.0        # anomaly contributions to risk decay
RISK_SANCTION_HOPS = 3            # graph proximity search depth for risk
GEOFENCE_LOITER_MIN_HOURS = 1.5   # loitering near sensitive geometry
# Feedback loop: a detector may auto-retune only after this many dispositions
FEEDBACK_MIN_DISPOSITIONS = 6

# ---- Phase 4: object graph constants ----------------------------------
GRAPH_DB_NAME = "graph.sqlite"
TRAVERSAL_MAX_NODES = 200        # budget per rule traversal (roadmap 4.4)
TRAVERSAL_MAX_HOPS_OWNERSHIP = 3 # vessel -> org -> org -> org, no deeper
ALERT_MIN_CONFIDENCE = 0.3       # chains weaker than this don't alert

# ---- Phase 3: fusion core constants -----------------------------------
ASSOC_MAX_TRACK_AGE_H = 12        # gate only against tracks reporting within this
ASSOC_GATE_BUFFER_M = 500.0       # added to the uncertainty cone when gating
ASSOC_SCORE_FLOOR = -8.0          # log-score below which "no match" wins
ASSOC_AMBIGUITY_MARGIN = 2.0      # top-2 log-score margin under this => ambiguous
DARK_MIN_LENGTH_M = 20.0          # size floor with margin over the 15-25 m physics floor
DARK_SCORE_THRESHOLD = 0.5        # precision-gated launch threshold (roadmap 3.3)
STATIC_MIN_SCENES = 3             # detections in >= this many scenes to become static
STATIC_MIN_SPAN_DAYS = 7.0        # ...spread over at least this long
STATIC_RADIUS_M = 200.0           # suppression radius around a static object

# ---- Phase 2: AIS track engine constants ------------------------------
# Physical cap on the uncertainty cone: no displacement hypothesis beyond
# what MAX_FEASIBLE_SPEED_KN allows. This cap — not the Kalman covariance —
# dominates gating after long dark periods, and Phase 3 depends on it.
MAX_FEASIBLE_SPEED_KN = 60.0
# A same-MMSI report needing > this implied speed to join an existing
# hypothesis spawns a new hypothesis instead (spoof / reuse / outlier path).
HYPOTHESIS_SPEED_GATE_KN = 60.0
TRACK_BREAK_DAYS = 7          # silence longer than this closes the track (MMSI-reuse guard)
GAP_MIN_MINUTES = 15          # shortest interval that can be called a gap at all
GAP_NOMINAL_MULT = 3.0        # gap = interval > max(GAP_MIN, mult × track's own median cadence)
ENCOUNTER_RADIUS_M = 500.0    # the rendezvous primitive, per roadmap 2.3
ENCOUNTER_MAX_SOG_KN = 2.0
ENCOUNTER_MIN_MINUTES = 15.0  # sustained proximity, not a crossing
LOITER_MAX_SOG_KN = 2.0
LOITER_MIN_HOURS = 2.0
PORT_RADIUS_KM = 8.0          # inside this of a known port, loitering is a berth, not a signal

# H3 resolution 6 ≈ 36 km² cells: coarse enough for cheap spatial joins in
# Phase 3 gating, fine enough that a cell is smaller than a Sentinel-1
# uncertainty cone after a few hours of AIS silence.
H3_RESOLUTION = 6


@dataclass(frozen=True)
class AOI:
    name: str
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float

    def contains(self, lat: float, lon: float) -> bool:
        return (self.lat_min <= lat <= self.lat_max
                and self.lon_min <= lon <= self.lon_max)

    @property
    def wkt(self) -> str:
        return (f"POLYGON(({self.lon_min} {self.lat_min},"
                f"{self.lon_max} {self.lat_min},"
                f"{self.lon_max} {self.lat_max},"
                f"{self.lon_min} {self.lat_max},"
                f"{self.lon_min} {self.lat_min}))")


AOI_V1 = AOI("arabian_sea_v1", lat_min=5.0, lat_max=25.0, lon_min=60.0, lon_max=78.0)

DATA_ROOT = Path(__file__).resolve().parent.parent / "data"
RAW_ROOT = DATA_ROOT / "raw"            # immutable, content-addressed
CONFORMED_ROOT = DATA_ROOT / "conformed"  # parquet, regenerable from raw
CATALOG_DB = DATA_ROOT / "catalog.sqlite"

for p in (RAW_ROOT, CONFORMED_ROOT):
    p.mkdir(parents=True, exist_ok=True)
