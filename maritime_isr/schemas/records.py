"""Canonical record schemas (versioned).

  - PositionReport    (AIS)          -> ingest/aisstream, ingest/noaa_ais
  - RadarTrackReport  (coastal radar)-> ingest/radar
  - SceneCatalogEntry (Sentinel-1)   -> ingest/copernicus, process/s1_preprocess
  - Detection         (SAR contact)  -> process/cfar, process/classifier

Every record embeds the provenance envelope and carries H3 indices (res 7 for
joins, res 9 for fine matching) so Phase 3 spatial joins are cheap.
SCHEMA_VERSION bumps on any breaking field change; Phase 4 migration tests depend on it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from .provenance import Provenance

SCHEMA_VERSION = "v1"


class PositionReport(BaseModel):
    """One canonical AIS position report (live or historical, same schema)."""

    mmsi: int = Field(..., description="Maritime Mobile Service Identity")
    imo: Optional[int] = Field(default=None)
    lat: float = Field(..., ge=-90.0, le=90.0)
    lon: float = Field(..., ge=-180.0, le=180.0)
    sog: Optional[float] = Field(default=None, description="speed over ground, knots")
    cog: Optional[float] = Field(default=None, ge=0.0, le=360.0)
    heading: Optional[float] = Field(default=None, description="true heading, 0-359 or 511=n/a")
    timestamp: datetime = Field(..., description="position report time (UTC)")
    msg_type: Optional[int] = Field(default=None, description="AIS message type 1-27")
    receiver_source: Optional[str] = Field(default=None)
    h3_r7: Optional[str] = Field(default=None)
    h3_r9: Optional[str] = Field(default=None)
    prov: Provenance

    @field_validator("timestamp")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("timestamp must be tz-aware UTC")
        return v.astimezone(timezone.utc)

    @field_validator("mmsi")
    @classmethod
    def _mmsi_range(cls, v: int) -> int:
        if not (0 < v < 1_000_000_000):
            raise ValueError(f"MMSI out of 9-digit space: {v}")
        return v


class VoyageDeclaration(BaseModel):
    """What a vessel *says* about the voyage she is on — AIS message 5.

    ITU-R M.1371 message 5 ("static and voyage related data") is broadcast every
    six minutes alongside the position reports, and it carries three things no
    position report does: where she says she is **going**, when she says she
    will **arrive**, and how deep she says she is **loaded**. All three are
    typed in by hand on the bridge and none of them is validated between the
    keyboard and the air.

    **This is a separate table and not columns on `PositionReport`** because it
    is a separate message with its own cadence, its own failure modes and its
    own nullity. A vessel transmits a hundred positions for every voyage
    declaration; folding them together would either duplicate the declaration a
    hundred times or leave 99% of the position rows carrying a null nobody could
    interpret. The real connector (`ingest/aisstream`) receives them as distinct
    message types and lands them as distinct rows.

    The IDEX Challenge 82 brief, Area 2, asks for exactly what this enables:
    *"Compare the destination the vessel declares against the destination its
    behaviour implies, and against where it has historically gone — a declared
    destination that the track has never been consistent with is one of the
    strongest and simplest suspicion factors available. Do the same with
    declared arrival time against plausible arrival time given current position
    and speed."*

    `destination` is free text as broadcast. It is deliberately **not**
    normalised here: "JNPT", "NHAVA SHEVA", "IN JNP" and "JNPT >> SIKKA" are all
    things real transmitters send, and resolving them to a gazetteer entry is a
    judgement with a confidence, which belongs downstream where a confidence can
    be attached to it. Landing a cleaned value would throw away the evidence an
    analyst needs to see.
    """

    mmsi: int = Field(..., description="Maritime Mobile Service Identity")
    imo: Optional[int] = Field(default=None)
    timestamp: datetime = Field(..., description="when the declaration was heard")
    # A declaration is a *located* record: it was heard somewhere, by something,
    # at a moment. CLAUDE.md 3 makes that non-negotiable — anything with a
    # position carries its H3 cells, computed the same way as every other table,
    # or the joins the whole architecture rests on silently miss it.
    lat: float = Field(..., ge=-90.0, le=90.0,
                       description="where she was when she said it")
    lon: float = Field(..., ge=-180.0, le=180.0)
    destination: Optional[str] = Field(
        default=None, description="free text, exactly as broadcast")
    eta: Optional[datetime] = Field(
        default=None,
        description="declared arrival. AIS message 5 carries month/day/hour/"
                    "minute with no year, so the year is inferred by the "
                    "connector and a declaration can be ambiguous across a "
                    "year boundary")
    draught_m: Optional[float] = Field(
        default=None, description="declared maximum static draught, metres")
    nav_status: Optional[int] = Field(default=None)
    ship_type: Optional[int] = Field(
        default=None, description="AIS ship-and-cargo type code, 0-99")
    receiver_source: Optional[str] = Field(default=None)
    h3_r7: Optional[str] = Field(default=None)
    h3_r9: Optional[str] = Field(default=None)
    prov: Provenance

    @field_validator("timestamp")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("timestamp must be tz-aware UTC")
        return v.astimezone(timezone.utc)

    @field_validator("mmsi")
    @classmethod
    def _mmsi_range(cls, v: int) -> int:
        if not (0 < v < 1_000_000_000):
            raise ValueError(f"MMSI out of 9-digit space: {v}")
        return v


class RadarTrackReport(BaseModel):
    """One track report from one coastal surveillance radar station.

    **There is no identity field, and that is the point.** A coastal radar
    measures range, bearing, speed and echo strength. It cannot tell you which
    ship it is looking at, which is precisely why a radar track that no AIS
    track explains is a candidate dark vessel — the product's headline claim.

    `radar_track_id` is the station's own track number, namespaced by station.
    It is a *slot in a track table*, not a name: stations reuse numbers, and a
    target crossing between stations gets a different number from each. Nothing
    downstream may treat a collision on it as a spoofing tell the way it rightly
    treats a duplicate MMSI (see `schemas.sources.TrackSource.key_is_identity`).

    `length_est_m` is derived from `rcs_dbsm`, not measured. Radar cross-section
    fluctuates by several dB look to look, so a single plot's length estimate is
    worth roughly a size *class*; the median over a long track is worth rather
    more, because averaging beats the fluctuation down. Consumers that need a
    size gate should use the track-level aggregate and not this field.
    """

    report_id: str = Field(..., description="deterministic: station|track|ts")
    station_id: str
    radar_track_id: str = Field(
        ..., description="station-assigned track number — NOT an identity")
    lat: float = Field(..., ge=-90.0, le=90.0)
    lon: float = Field(..., ge=-180.0, le=180.0)
    sog_kn: Optional[float] = Field(default=None, ge=0.0)
    cog_deg: Optional[float] = Field(default=None, ge=0.0, le=360.0)
    timestamp: datetime = Field(..., description="report time (UTC)")
    range_km: Optional[float] = Field(default=None, ge=0.0)
    bearing_deg: Optional[float] = Field(default=None, ge=0.0, le=360.0)
    position_sigma_m: Optional[float] = Field(
        default=None, ge=0.0,
        description="1-σ position error at this range — travels with the row "
                    "because it is not a constant of the sensor")
    rcs_dbsm: Optional[float] = Field(default=None)
    length_est_m: Optional[float] = Field(default=None, ge=0.0)
    snr_db: Optional[float] = Field(default=None)
    track_quality: Optional[int] = Field(default=None, ge=0, le=100)
    h3_r7: Optional[str] = Field(default=None)
    h3_r9: Optional[str] = Field(default=None)
    prov: Provenance

    @field_validator("timestamp")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("timestamp must be tz-aware UTC")
        return v.astimezone(timezone.utc)

    @field_validator("radar_track_id")
    @classmethod
    def _namespaced(cls, v: str) -> str:
        """A bare integer is refused.

        Two stations both numbering their tracks from 1 is the normal case, so
        an un-namespaced track number silently merges two unrelated targets into
        one track. Requiring the station prefix at the schema boundary means
        that mistake cannot reach the track engine.
        """
        if ":" not in v:
            raise ValueError(
                f"radar_track_id {v!r} is not namespaced by station. Station "
                f"track numbers are only unique within a station; use "
                f"'<station_id>:<number>'.")
        return v


class SceneStatus(str, Enum):
    CATALOGED = "cataloged"
    RAW = "raw"
    CALIBRATED = "calibrated"
    DETECTED = "detected"
    FAILED = "failed"


class SceneCatalogEntry(BaseModel):
    """One Sentinel-1 GRD scene tracked through raw->calibrated->detected."""

    scene_id: str = Field(..., description="Copernicus product identifier")
    footprint_wkt: str = Field(..., description="footprint polygon, WKT, EPSG:4326")
    orbit_direction: Optional[str] = Field(default=None)
    relative_orbit: Optional[int] = Field(default=None)
    acquired_at: datetime = Field(..., description="sensing start (UTC)")
    mode: str = Field(default="IW")
    polarizations: str = Field(default="VV+VH")
    status: SceneStatus = Field(default=SceneStatus.CATALOGED)
    status_detail: Optional[str] = Field(default=None)
    raw_uri: Optional[str] = Field(default=None)
    calibrated_uri: Optional[str] = Field(default=None)
    prov: Provenance

    @field_validator("acquired_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("acquired_at must be tz-aware UTC")
        return v.astimezone(timezone.utc)


class DetectionMethod(str, Enum):
    CFAR = "cfar"
    CNN = "cnn"
    GFW = "gfw"


class Detection(BaseModel):
    """One SAR contact candidate. Position + backscatter + size estimate."""

    detection_id: str = Field(..., description="deterministic id: scene_id + pixel loc")
    scene_id: str
    method: DetectionMethod
    lat: float = Field(..., ge=-90.0, le=90.0)
    lon: float = Field(..., ge=-180.0, le=180.0)
    mean_sigma0_db: Optional[float] = Field(default=None)
    max_sigma0_db: Optional[float] = Field(default=None)
    length_m: Optional[float] = Field(default=None, ge=0.0)
    width_m: Optional[float] = Field(default=None, ge=0.0)
    vessel_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    h3_r7: Optional[str] = Field(default=None)
    h3_r9: Optional[str] = Field(default=None)
    acquired_at: datetime = Field(..., description="scene acquisition instant (UTC)")
    prov: Provenance

    @field_validator("acquired_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("acquired_at must be tz-aware UTC")
        return v.astimezone(timezone.utc)

    @model_validator(mode="after")
    def _length_ge_width(self) -> "Detection":
        if self.length_m is not None and self.width_m is not None:
            if self.width_m > self.length_m:
                self.length_m, self.width_m = self.width_m, self.length_m
        return self
