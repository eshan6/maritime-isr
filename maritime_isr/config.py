"""Config loader. AOI is locked; credentials and paths come from env vars.

Run `python -m maritime_isr.config` to print the resolved AOI and report which
required env vars are present/missing (unit 0.0 exit test).

Storage backend: the live AIS writer runs on the always-on Oracle VM (systemd),
writes hourly Parquet locally, and a cron mirrors closed partitions to R2.
MISR_STORE_BACKEND selects how readers resolve paths:
  local  -> read local disk only
  r2     -> read R2 via duckdb httpfs only
  mirror -> local for hot/recent, R2 for durable (default on the VM)
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class AOI:
    lat_min: float = 5.0
    lat_max: float = 25.0
    lon_min: float = 60.0
    lon_max: float = 78.0
    name: str = "arabian_sea_v1"

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        """(lon_min, lat_min, lon_max, lat_max) — STAC/OData bbox order."""
        return (self.lon_min, self.lat_min, self.lon_max, self.lat_max)

    @property
    def wkt(self) -> str:
        return (
            "POLYGON(("
            f"{self.lon_min} {self.lat_min}, {self.lon_max} {self.lat_min}, "
            f"{self.lon_max} {self.lat_max}, {self.lon_min} {self.lat_max}, "
            f"{self.lon_min} {self.lat_min}))"
        )

    def contains(self, lat: float, lon: float) -> bool:
        return self.lat_min <= lat <= self.lat_max and self.lon_min <= lon <= self.lon_max


ENV_SPEC: dict[str, tuple[str, str]] = {
    "R2_ACCOUNT_ID": ("storage", "Cloudflare account id"),
    "R2_ACCESS_KEY_ID": ("storage", "R2 API token access key"),
    "R2_SECRET_ACCESS_KEY": ("storage", "R2 API token secret"),
    "R2_BUCKET": ("storage", "R2 bucket name for raw scenes/chips"),
    "CDSE_USERNAME": ("copernicus", "Copernicus Data Space login"),
    "CDSE_PASSWORD": ("copernicus", "Copernicus Data Space password"),
    "AISSTREAM_API_KEY": ("aisstream", "free aisstream.io key"),
    "GFW_API_TOKEN": ("gfw", "free GFW API token"),
}


@dataclass
class Config:
    aoi: AOI = field(default_factory=AOI)
    store_backend: str = field(default_factory=lambda: os.getenv("MISR_STORE_BACKEND", "mirror"))
    data_root: Path = field(
        default_factory=lambda: Path(os.getenv("MISR_DATA_ROOT", "./data")).expanduser()
    )
    r2_bucket: str | None = field(default_factory=lambda: os.getenv("R2_BUCKET"))

    def local_parquet_dir(self, store: str) -> Path:
        return self.data_root / "parquet" / store

    def r2_prefix(self, store: str) -> str:
        return f"s3://{self.r2_bucket}/parquet/{store}"

    def duckdb_path(self) -> Path:
        return self.data_root / "misr.duckdb"

    def missing_env(self) -> list[str]:
        return [k for k in ENV_SPEC if not os.getenv(k)]

    def present_env(self) -> list[str]:
        return [k for k in ENV_SPEC if os.getenv(k)]


cfg = Config()


def _main() -> int:
    c = Config()
    print("=" * 60)
    print("Maritime ISR — resolved config")
    print("=" * 60)
    print(f"AOI            : {c.aoi.name}")
    print(f"  lat          : {c.aoi.lat_min} .. {c.aoi.lat_max}")
    print(f"  lon          : {c.aoi.lon_min} .. {c.aoi.lon_max}")
    print(f"  bbox (STAC)  : {c.aoi.bbox}")
    print(f"store backend  : {c.store_backend}")
    print(f"data root      : {c.data_root.resolve()}")
    print(f"r2 bucket      : {c.r2_bucket or '(unset)'}")
    print(f"duckdb path    : {c.duckdb_path()}")
    print("-" * 60)
    present, missing = c.present_env(), c.missing_env()
    print(f"env vars present ({len(present)}):")
    for k in present:
        print(f"  [ok]   {k}")
    print(f"env vars missing ({len(missing)}):")
    for k in missing:
        need, hint = ENV_SPEC[k]
        print(f"  [MISS] {k:<24} needed for {need:<11} - {hint}")
    print("-" * 60)
    if missing:
        print("Some credentials unset. Connectors requiring them will refuse to run.")
        print("Expected at unit 0.0; fill them in as each unit needs them.")
    else:
        print("All known credentials present.")
    print("=" * 60)
    return 0



# =====================================================================
# Prototype constants (synthetic Phases 1-6)
# ---------------------------------------------------------------------
# The live-data path above (Config/cfg, env credentials, R2/duckdb) is
# the execution-spec loader from unit 0.0. The constants below drive the
# synthetic prototype: detection, tracks, fusion, graph, anomaly, product.
# One AOI class serves both: AOI_V1 is the same locked Arabian Sea box.
# =====================================================================

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


AOI_V1 = AOI()  # arabian_sea_v1, 5-25N / 60-78E — same locked box as cfg.aoi


DATA_ROOT = Path(__file__).resolve().parent.parent / "data"
RAW_ROOT = DATA_ROOT / "raw"            # immutable, content-addressed
CONFORMED_ROOT = DATA_ROOT / "conformed"  # parquet, regenerable from raw
CATALOG_DB = DATA_ROOT / "catalog.sqlite"

for p in (RAW_ROOT, CONFORMED_ROOT):
    p.mkdir(parents=True, exist_ok=True)

if __name__ == "__main__":
    sys.exit(_main())
