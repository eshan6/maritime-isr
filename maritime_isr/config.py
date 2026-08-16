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
import unicodedata
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


# Invisible characters that copy-paste drags along and that are never a
# legitimate part of a credential. Stripped before use.
_INVISIBLE = dict.fromkeys(
    map(ord, "​‌‍⁠﻿­"), None
)


def sanitize_secret(value: str) -> tuple[str, list[str]]:
    """Repair a credential mangled by copy-paste. Returns (cleaned, notes).

    Pasting a token out of a rendered surface — a chat window, a PDF, a styled
    web page — can substitute ASCII with Unicode look-alikes that are visually
    identical and functionally useless. HTTP headers are latin-1 only, so a
    token full of full-width characters raises UnicodeEncodeError deep inside
    urllib3, long after the point where the mistake was made.

    Three repairs, all lossless for a genuine ASCII secret:
      * remove zero-width and soft-hyphen characters
      * turn non-breaking and other Unicode spaces into ordinary spaces, then strip
      * NFKC-normalise, which maps full-width and compatibility forms back to
        ASCII (U+FF45 FULLWIDTH LATIN SMALL LETTER E -> 'e')

    An already-clean ASCII value is returned unchanged with no notes.
    """
    notes: list[str] = []

    cleaned = value.translate(_INVISIBLE)
    if cleaned != value:
        notes.append("removed zero-width/invisible characters")

    despaced = "".join(
        " " if unicodedata.category(c) == "Zs" else c for c in cleaned
    ).strip()
    if despaced != cleaned.strip():
        notes.append("converted non-breaking spaces")
    cleaned = despaced

    normalised = unicodedata.normalize("NFKC", cleaned)
    if normalised != cleaned:
        notes.append("normalised full-width/look-alike characters to ASCII (NFKC)")
    cleaned = normalised

    return cleaned, notes


def header_safety(value: str) -> tuple[bool, str]:
    """Can this value be sent in an HTTP header? Returns (ok, plain-English why not).

    HTTP headers are latin-1 encoded. `requests` does not check, so a bad value
    surfaces as a UnicodeEncodeError from `http.client.putheader` with no clue
    about which credential caused it.
    """
    try:
        value.encode("latin-1")
        return True, ""
    except UnicodeEncodeError:
        bad = sorted({c for c in value if ord(c) > 0xFF})
        sample = ", ".join(
            f"{c!r} (U+{ord(c):04X} {unicodedata.name(c, 'unnamed')})" for c in bad[:3]
        )
        return False, (
            f"{len(bad)} distinct non-Latin-1 character(s), e.g. {sample}. "
            "This usually means the value was copied from a rendered page or "
            "chat window that substituted look-alike Unicode for plain ASCII."
        )


def load_dotenv(path: Path | None = None, *, override: bool = False) -> int:
    """Load KEY=VALUE pairs from a `.env` file at the repo root into os.environ.

    Every connector reads credentials with `os.getenv`, and `.env.example` tells
    the operator to copy it to `.env` — but nothing was actually reading that
    file, so a correctly-filled `.env` had no effect and every key looked
    missing. This closes that gap.

    Real environment variables win by default: if a key is already set in the
    shell, the `.env` value does not clobber it. Pass `override=True` to
    reverse that.

    Deliberately dependency-free — a dozen lines here beats adding python-dotenv
    for a file format this simple. Returns the number of keys set.
    """
    env_path = Path(path) if path else Path(
        os.getenv("MISR_ENV_FILE") or (Path(__file__).resolve().parent.parent / ".env")
    )
    if not env_path.is_file():
        return 0

    n = 0
    try:
        # utf-8-sig: PowerShell's `Set-Content -Encoding utf8` writes a BOM,
        # which would otherwise corrupt the first key name.
        text = env_path.read_text(encoding="utf-8-sig")
    except OSError:
        return 0

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        # Trailing inline comment, e.g. `MISR_STORE_BACKEND=local  # or mirror`.
        # Requires whitespace before the '#' so a '#' inside a secret survives.
        if " #" in value:
            value = value.split(" #", 1)[0].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if not key or not value:
            continue  # blank values mean "not set", not "set to empty"

        # Repair copy-paste damage, and say so — a silent repair would hide a
        # real problem, and a silent non-repair costs an hour of debugging in
        # urllib3. See sanitize_secret().
        value, notes = sanitize_secret(value)
        if notes:
            print(f"[config] repaired {key} from .env: {'; '.join(notes)}")

        if override or key not in os.environ:
            os.environ[key] = value
            n += 1
    return n


# Load before Config() is constructed below — its field defaults read os.getenv
# at instantiation time, so the .env must already be in the environment.
load_dotenv()


def repo_root() -> Path:
    """The checkout this package was installed from. `.env` and `data/` live here."""
    return Path(__file__).resolve().parent.parent


def _resolve_data_root() -> Path:
    """Where landed data goes. Relative paths resolve against the REPO ROOT.

    They used to resolve against the current working directory, which meant
    running `python -m maritime_isr.cli` from somewhere else silently used a
    different data directory — while `.env` was still found via the package
    location. The two disagreed and nothing complained: doctor reported READY
    against `C:\\Users\\eshan\\data` while the real data sat in the repo.

    An absolute MISR_DATA_ROOT is honoured as-is.
    """
    raw = Path(os.getenv("MISR_DATA_ROOT", "./data")).expanduser()
    return raw if raw.is_absolute() else (repo_root() / raw).resolve()


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
    # Default is `local`: the current operating mode is a Windows laptop with no
    # Oracle VM and no R2 bucket (STATE.md OPEN QUESTION #3). `mirror` was the
    # old default and assumed the VM existed; on a laptop it makes every reader
    # try to resolve s3:// paths against a bucket that isn't configured. Flip
    # this to `mirror` on the deploy host once R2 is wired.
    store_backend: str = field(default_factory=lambda: os.getenv("MISR_STORE_BACKEND", "local"))
    data_root: Path = field(default_factory=lambda: _resolve_data_root())
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
# `dataclass` and `Path` are already imported at the top of this file. The
# duplicate import that used to sit here re-bound both names to themselves —
# dead code, and the only finding ruff's undefined-name/redefinition rules
# report across the whole package.

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

#: Reference position precision for the association score, metres.
#:
#: The score is a log-likelihood *ratio*, so it needs a scale to be a ratio
#: against. At exactly this σ the volume-normalisation term is zero and the
#: score is the bare distance term — which is what the score used to be, so the
#: well-constrained case is unchanged and `ASSOC_SCORE_FLOOR` keeps its meaning.
#: Above it, agreement is discounted for the size of the area searched; a track
#: whose position is only known to 360 km cannot explain anything, which is the
#: whole point (see `fusion.associate._score`).
#:
#: 200 m is roughly Sentinel-1 geolocation plus smoothing residual, and roughly
#: a coastal radar plot at 40 km. It is a statement about what "a good position
#: agreement" means for the sensors this system has, not a tuned number.
ASSOC_SIGMA_REF_M = 200.0
DARK_MIN_LENGTH_M = 20.0          # size floor with margin over the 15-25 m physics floor
DARK_SCORE_THRESHOLD = 0.5        # precision-gated launch threshold (roadmap 3.3)
STATIC_MIN_SCENES = 3             # detections in >= this many scenes to become static
STATIC_MIN_SPAN_DAYS = 7.0        # ...spread over at least this long
STATIC_RADIUS_M = 200.0           # suppression radius around a static object

# ---- Coastal radar correlation (ADR-028) ------------------------------
# Radar reuses the fusion core rather than duplicating it, so what lives here
# are the *sensor's* parameters, not a second set of rules.

#: How wide a correlation epoch is. Every radar track visible in the epoch
#: competes for every live AIS track at once, through the same Hungarian
#: assignment the SAR path uses — so the epoch is the unit of global
#: assignment and it must be wide enough to hold the whole picture and narrow
#: enough to locate a transponder shutdown in time. Fifteen minutes puts the
#: "went dark at" position within about 3 nm at merchant speeds.
RADAR_CORRELATION_EPOCH_S = 900.0

#: Fraction of a radar track's epochs one AIS track must win to explain it, and
#: the fraction below which nothing is claimed at all. Between the two the
#: track is AMBIGUOUS: something is near it often enough to be a candidate and
#: not often enough to be the answer, and the precision-first posture says
#: report that rather than convict either way.
RADAR_SUPPORT_CORRELATED = 0.55
RADAR_SUPPORT_AMBIGUOUS = 0.20

#: A dark contact must rest on at least this many unexplained epochs and span
#: at least this long. These are the persistence gate, and the cascade records
#: what they suppress as `suppressed_transient` rather than dropping it.
#:
#: **Two hours is a product decision, not a fitted curve.** Below it, three
#: things are indistinguishable: a vessel genuinely dark for a short while, a
#: tracker dropping and reacquiring a target at the edge of cover, and a sea
#: clutter return. An analyst cannot task anything on a ninety-minute-old
#: hole either — by the time a boat is on its way the contact is stale.
#: Measured on the synthetic picture at 40 minutes: 20 contacts, precision
#: 40%, and nine of the twelve false positives were brief holes of exactly
#: that kind. ADR-004 makes precision a product policy with a number attached,
#: and the recall this costs is recorded rather than hidden: it drops the one
#: findable episode whose dark run was under two hours, and that vessel is
#: still detected on a different station's track.
RADAR_DARK_MIN_EPOCHS = 4
RADAR_DARK_MIN_MINUTES = 120.0
RADAR_MIN_LOOKS = 4

#: The static-object layer's clustering geometry, for radar.
#:
#: STATIC_RADIUS_M is 200 m, which is right for Sentinel-1's ~60 m geolocation
#: and *narrower than a coastal radar's own position error at range*. Left
#: alone, a mooring buoy's plots scatter past the radius, the cluster is
#: rejected on spread, and every fixed installation in the picture is promoted
#: to a dark vessel — the top of the queue becomes four mooring buoys. The
#: cell resolution moves with it: at res 8 (~460 m across) one buoy's plots
#: straddle several cells and none of them accumulates.
RADAR_STATIC_RADIUS_M = 600.0
RADAR_STATIC_RES = 7

#: Distinct days a contact must recur on to be a fixed installation.
#:
#: STATIC_MIN_SCENES is 3, which is right for a satellite that revisits every
#: six days and wrong for a sensor that never stops looking. Measured on the
#: synthetic picture: at 3 the layer produced 58 "installations", four of which
#: were the real moorings and the rest **shipping lanes** — three ships crossing
#: one cell on three days is not a fixed object. An installation is present on
#: essentially every day the radar is up; 20 of ~51 days is a wide margin below
#: that and far above anything traffic produces.
RADAR_STATIC_MIN_SCENES = 20

#: The neighbourhood the contacts-versus-broadcasters count is taken over.
#:
#: H3 res 7 cells are ~5 km2 (about 1.2 km across); with the surrounding ring
#: the neighbourhood is roughly 3.5 km wide. **Res 6 was tried first and was
#: measurably too wide**: at ~10 km across, a contact in the Gulf of Kachchh is
#: counted against every transmitter in the anchorage several kilometres away,
#: so the census came out negative for genuinely isolated vessels and recall
#: fell to 38%. The neighbourhood has to be the scale at which "here" means
#: here.
#:
#: This is the H3 join the whole architecture is built around (CLAUDE.md 3):
#: same cell means candidate, and the count is a hash join rather than a
#: distance sweep over every pair.
RADAR_NEIGHBOURHOOD_RES = 7

#: How far either side of an epoch an AIS receipt still counts as evidence that
#: something is broadcasting here.
#:
#: **Wider than the epoch, deliberately, and this is the whole trick.** The
#: contact census is instantaneous — radar reports every five minutes, so an
#: epoch holds every contact present. The broadcaster census cannot be: a vessel
#: at anchor lands one AIS receipt roughly every fifty minutes, so asking "was
#: she heard in this fifteen-minute epoch" answers no for two epochs in three
#: and manufactures an excess contact out of her own reporting schedule. An hour
#: either side spans the anchored reporting interval with margin.
RADAR_CENSUS_WINDOW_EPOCHS = 4

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

#: Same idea as PORT_RADIUS_KM, one step out to sea: inside this of a
#: **designated anchorage**, a stopped vessel is queueing for a berth.
#:
#: A separate constant because a berth and an anchorage are different sizes.
#: PORT_RADIUS_KM is drawn around a terminal and 8 km covers its approaches;
#: a designated waiting area sits 15-30 km offshore of the terminal it serves
#: and vessels spread out across it, so a radius drawn on the berth never
#: reaches it. That is not a hypothetical — Kandla's anchorage is 30 km from
#: the Kandla berth coordinate, so every vessel waiting there produced an
#: unsuppressed loiter episode inside the Kandla pipeline geofence.
#:
#: 10 km is the size of a designated anchorage area, not a number tuned until
#: the false positives went away. Sizing it on the observed alerts would be
#: fitting the detector to this corpus.
ANCHORAGE_RADIUS_KM = 10.0

# H3 resolution 6 ≈ 36 km² cells: coarse enough for cheap spatial joins in
# Phase 3 gating, fine enough that a cell is smaller than a Sentinel-1
# uncertainty cone after a few hours of AIS silence.
# Kept as a name only. The single source of truth for resolutions is
# h3util.RESOLUTIONS / h3util.DEFAULT_RES (ADR-015) — do not add resolution
# constants here, and do not hard-code an integer resolution in any module.
H3_RESOLUTION = 6  # == h3util.R6 / h3util.DEFAULT_RES


AOI_V1 = AOI()  # arabian_sea_v1, 5-25N / 60-78E — same locked box as cfg.aoi


DATA_ROOT = Path(__file__).resolve().parent.parent / "data"
RAW_ROOT = DATA_ROOT / "raw"            # immutable, content-addressed
CONFORMED_ROOT = DATA_ROOT / "conformed"  # parquet, regenerable from raw
CATALOG_DB = DATA_ROOT / "catalog.sqlite"

for p in (RAW_ROOT, CONFORMED_ROOT):
    p.mkdir(parents=True, exist_ok=True)

if __name__ == "__main__":
    sys.exit(_main())
