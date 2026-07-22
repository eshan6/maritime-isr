"""Canonical record schemas (versioned).

  - PositionReport    (AIS)          -> ingest/aisstream, ingest/noaa_ais
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
