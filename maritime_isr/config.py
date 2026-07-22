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


if __name__ == "__main__":
    sys.exit(_main())
