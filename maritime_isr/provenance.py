"""Provenance: every record answers 'where did you come from, when, and
through which pipeline version'. Phase 0 exit criterion #3.

A Provenance stamp is attached to every conformed record and every raw
artifact catalog entry. It is data, not metadata-by-convention: the
conformed parquet schemas carry these as first-class columns so Phase 3+
can filter/score by source without joins back to the catalog.
"""
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

from .config import PIPELINE_VERSION


@dataclass(frozen=True)
class Provenance:
    source: str              # e.g. "copernicus_dataspace", "ais_terrestrial:aisstream", "gfw_sar_v3"
    source_ref: str          # upstream identifier: product id, receiver id, dataset version
    acquired_at: str         # when the phenomenon was sensed/reported (ISO 8601 UTC)
    ingested_at: str         # when we landed it (ISO 8601 UTC)
    pipeline_version: str = PIPELINE_VERSION

    def as_dict(self) -> dict:
        return asdict(self)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stamp(source: str, source_ref: str, acquired_at: str) -> Provenance:
    return Provenance(source=source, source_ref=source_ref,
                      acquired_at=acquired_at, ingested_at=now_iso())
