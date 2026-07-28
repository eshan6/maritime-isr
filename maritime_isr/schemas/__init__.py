"""Canonical conformed schemas (pyarrow). These are the contract:
connectors normalize INTO these; Phases 1-5 read FROM these and never
see source-specific shapes. Provenance columns are first-class.
"""
import pyarrow as pa

_PROV = [
    pa.field("source", pa.string()),
    pa.field("source_ref", pa.string()),
    pa.field("acquired_at", pa.timestamp("us", tz="UTC")),
    pa.field("ingested_at", pa.timestamp("us", tz="UTC")),
    pa.field("pipeline_version", pa.string()),
]

# Canonical AIS position report (roadmap 0.1 connector #2)
AIS_POSITION = pa.schema([
    pa.field("mmsi", pa.int64()),
    pa.field("imo", pa.int64()),              # nullable; joined from static msgs later
    pa.field("lat", pa.float64()),
    pa.field("lon", pa.float64()),
    pa.field("sog_kn", pa.float64()),         # speed over ground
    pa.field("cog_deg", pa.float64()),        # course over ground
    pa.field("heading_deg", pa.float64()),
    pa.field("nav_status", pa.int32()),
    pa.field("msg_type", pa.int32()),
    pa.field("ts", pa.timestamp("us", tz="UTC")),
    pa.field("h3_cell", pa.string()),
    pa.field("receiver", pa.string()),        # which feed/receiver heard it — spoofing forensics
    pa.field("n_receipts", pa.int32()),       # how many times heard — coverage/spoof forensics
    *_PROV,
])

# Canonical detection schema — Phase 1 SAR contacts land here; so do GFW's
# published detections (connector #3), which is the point: ground truth and
# our own output are join-compatible from day one.
DETECTION = pa.schema([
    pa.field("detection_id", pa.string()),
    pa.field("lat", pa.float64()),
    pa.field("lon", pa.float64()),
    pa.field("ts", pa.timestamp("us", tz="UTC")),
    pa.field("length_m", pa.float64()),
    pa.field("score", pa.float64()),
    pa.field("scene_id", pa.string()),
    pa.field("matched_mmsi", pa.int64()),     # GFW publishes their AIS match; ours filled in Phase 3
    pa.field("h3_cell", pa.string()),
    *_PROV,
])

# Sanctions/registry record — versioned, as-of dated (roadmap 0.1 connector #4)
SANCTIONS_ENTRY = pa.schema([
    pa.field("registry", pa.string()),
    pa.field("entry_id", pa.string()),
    pa.field("name", pa.string()),
    pa.field("entry_type", pa.string()),      # vessel | entity | individual
    pa.field("imo", pa.int64()),
    pa.field("flag", pa.string()),
    pa.field("program", pa.string()),
    pa.field("as_of", pa.date32()),           # sanctions edges must carry as-of dates
    pa.field("valid_from", pa.date32()),
    pa.field("valid_to", pa.date32()),        # null = still listed (open interval)
    *_PROV,
])

# ---- Phase 2: track engine schemas ------------------------------------
# TRACK is the memory the graph is built from (roadmap Phase 2 preamble).
TRACK = pa.schema([
    pa.field("track_id", pa.string()),
    pa.field("mmsi", pa.int64()),
    pa.field("hypothesis", pa.int32()),        # >0 ⇒ born from duplicate-MMSI splitting
    pa.field("t_start", pa.timestamp("us", tz="UTC")),
    pa.field("t_end", pa.timestamp("us", tz="UTC")),
    pa.field("n_points", pa.int64()),
    pa.field("n_outliers", pa.int64()),
    pa.field("median_report_s", pa.float64()), # the track's own cadence — gap threshold input
    pa.field("fragmented_from", pa.string()),  # prior track_id if MMSI-reuse break
    *_PROV,
])

TRACK_POINT = pa.schema([
    pa.field("track_id", pa.string()),
    pa.field("mmsi", pa.int64()),
    pa.field("ts", pa.timestamp("us", tz="UTC")),
    pa.field("lat", pa.float64()),             # RTS-smoothed
    pa.field("lon", pa.float64()),
    pa.field("sog_kn", pa.float64()),          # smoothed speed
    pa.field("cog_deg", pa.float64()),
    pa.field("sigma_m", pa.float64()),         # 1-σ position uncertainty at this point
    pa.field("quality", pa.string()),          # ok | noisy | outlier (outliers kept, never dropped)
    pa.field("h3_cell", pa.string()),
    *_PROV,
])

# Every gap in every track gets exactly one row here — Phase 2 exit criterion.
TRACK_GAP = pa.schema([
    pa.field("gap_id", pa.string()),
    pa.field("track_id", pa.string()),
    pa.field("mmsi", pa.int64()),
    pa.field("t_start", pa.timestamp("us", tz="UTC")),
    pa.field("t_end", pa.timestamp("us", tz="UTC")),
    pa.field("duration_min", pa.float64()),
    pa.field("gap_type", pa.string()),         # COVERAGE_GAP | SAT_PASS_GAP | INTENTIONAL_SILENCE
    pa.field("confidence", pa.float64()),      # confidence in the assigned type
    pa.field("coverage_along_path", pa.float64()),  # mean P(would-have-heard) on interpolated path
    pa.field("lat_start", pa.float64()),
    pa.field("lon_start", pa.float64()),
    pa.field("lat_end", pa.float64()),
    pa.field("lon_end", pa.float64()),
    pa.field("h3_cell", pa.string()),          # cell at gap midpoint
    *_PROV,
])

# Spoofing tells are logged, never discarded (roadmap 2.1).
SPOOF_EVENT = pa.schema([
    pa.field("event_id", pa.string()),
    pa.field("mmsi", pa.int64()),
    pa.field("event_type", pa.string()),       # DUPLICATE_MMSI | IMPOSSIBLE_KINEMATICS
    pa.field("t_start", pa.timestamp("us", tz="UTC")),
    pa.field("t_end", pa.timestamp("us", tz="UTC")),
    pa.field("track_ids", pa.string()),        # comma-joined participants
    pa.field("max_separation_km", pa.float64()),
    pa.field("detail", pa.string()),
    *_PROV,
])

# The rendezvous primitive (roadmap 2.3): converging <500 m at <2 kn.
ENCOUNTER = pa.schema([
    pa.field("encounter_id", pa.string()),
    pa.field("track_id_a", pa.string()),
    pa.field("track_id_b", pa.string()),
    pa.field("mmsi_a", pa.int64()),
    pa.field("mmsi_b", pa.int64()),
    pa.field("t_start", pa.timestamp("us", tz="UTC")),
    pa.field("t_end", pa.timestamp("us", tz="UTC")),
    pa.field("duration_min", pa.float64()),
    pa.field("min_distance_m", pa.float64()),
    pa.field("mean_sog_kn", pa.float64()),
    pa.field("lat", pa.float64()),             # centroid
    pa.field("lon", pa.float64()),
    pa.field("confidence", pa.float64()),
    pa.field("h3_cell", pa.string()),
    *_PROV,
])

# ---- Phase 3: fusion core schemas --------------------------------------
# One row per SAR contact per scene: the association verdict with evidence.
ASSOCIATION = pa.schema([
    pa.field("association_id", pa.string()),
    pa.field("detection_id", pa.string()),
    pa.field("scene_id", pa.string()),
    pa.field("ts", pa.timestamp("us", tz="UTC")),
    pa.field("status", pa.string()),          # matched | ambiguous | unmatched
    pa.field("track_id", pa.string()),
    pa.field("mmsi", pa.int64()),
    pa.field("confidence", pa.float64()),
    pa.field("position_error_m", pa.float64()),
    pa.field("length_error_rel", pa.float64()),
    pa.field("top_k", pa.string()),           # JSON [(mmsi, score), ...] when ambiguous
    pa.field("in_ais_gap", pa.bool_()),        # SAR-confirmed dark period: the
    pa.field("gap_type", pa.string()),         # contact matches a track currently
                                               # inside a classified AIS gap
    pa.field("h3_cell", pa.string()),
    *_PROV,
])

# Every unmatched contact gets a row here with its cascade verdict —
# suppressions are recorded, never silently dropped, because the analyst
# question "why is this NOT dark" must be answerable.
DARK_CANDIDATE = pa.schema([
    pa.field("candidate_id", pa.string()),
    pa.field("detection_id", pa.string()),
    pa.field("scene_id", pa.string()),
    pa.field("ts", pa.timestamp("us", tz="UTC")),
    pa.field("lat", pa.float64()),
    pa.field("lon", pa.float64()),
    pa.field("length_m", pa.float64()),
    pa.field("status", pa.string()),          # dark_candidate | suppressed_coverage
                                              # | suppressed_static | suppressed_size
                                              # | suppressed_score
    pa.field("dark_score", pa.float64()),
    pa.field("hearable_conf", pa.float64()),  # P(we'd have heard AIS here) — cascade #1
    pa.field("nearest_track_m", pa.float64()),
    pa.field("h3_cell", pa.string()),
    *_PROV,
])

# Self-building layer of fixed installations (rigs, buoys, wrecks).
STATIC_OBJECT = pa.schema([
    pa.field("object_id", pa.string()),
    pa.field("lat", pa.float64()),
    pa.field("lon", pa.float64()),
    pa.field("n_scenes", pa.int64()),
    pa.field("first_seen", pa.timestamp("us", tz="UTC")),
    pa.field("last_seen", pa.timestamp("us", tz="UTC")),
    pa.field("mean_length_m", pa.float64()),
    pa.field("h3_cell", pa.string()),
    *_PROV,
])


# ---------------------------------------------------------------------
# Live-data path schema objects (execution-spec unit 0.0): dataclass
# record types + provenance envelope used by ingest/, process/, writer.
# ---------------------------------------------------------------------
from .provenance import ENVELOPE_COLUMNS, Provenance, git_sha, utcnow
from .records import (
    SCHEMA_VERSION,
    Detection,
    DetectionMethod,
    PositionReport,
    SceneCatalogEntry,
    SceneStatus,
)
