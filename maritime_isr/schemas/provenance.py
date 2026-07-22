"""Canonical provenance envelope — non-negotiable from unit 0.1.

Every record in every store carries these fields. This is the Foundry
raw->clean->conformed discipline: you can always answer "where did you come
from, when, and through which pipeline version" for any single row.

Standing rule 2: provenance on every record, confidence on every assertion.
"""
from __future__ import annotations

import functools
import subprocess
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field, field_validator

ENVELOPE_COLUMNS: tuple[str, ...] = (
    "source_id",
    "source_ref",
    "acquired_at",
    "ingested_at",
    "pipeline_version",
    "confidence",
)


@functools.lru_cache(maxsize=1)
def git_sha() -> str:
    """Short git SHA of the code processing this record. 'unknown' outside a checkout."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


def utcnow() -> datetime:
    """Timezone-aware UTC now. Never use naive datetimes anywhere in the repo."""
    return datetime.now(timezone.utc)


class Provenance(BaseModel):
    """The envelope carried by every canonical record."""

    source_id: str = Field(..., description="e.g. copernicus-s1, aisstream, gfw, ofac")
    source_ref: str = Field(..., description="scene ID / message ID / list version")
    acquired_at: datetime = Field(..., description="when the phenomenon was observed (UTC)")
    ingested_at: datetime = Field(default_factory=utcnow, description="when we landed it (UTC)")
    pipeline_version: str = Field(default_factory=git_sha, description="git SHA of processing code")
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)

    @field_validator("acquired_at", "ingested_at")
    @classmethod
    def _must_be_utc_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware UTC (got naive datetime)")
        return v.astimezone(timezone.utc)

    def stamp(self) -> dict:
        """Flatten to a dict of the six envelope columns for row assembly."""
        return {
            "source_id": self.source_id,
            "source_ref": self.source_ref,
            "acquired_at": self.acquired_at,
            "ingested_at": self.ingested_at,
            "pipeline_version": self.pipeline_version,
            "confidence": self.confidence,
        }
