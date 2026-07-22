"""Storage abstraction. Nothing downstream hard-codes a path or a backend.

Design (from streaming + laptop-off + free constraints):
  * The live AIS consumer runs on the always-on Oracle VM and writes hourly
    Parquet partitions to LOCAL disk (fast, no per-write egress, independent of
    your laptop).
  * A cron mirrors *closed* (previous-hour) partitions to R2 for durability.
  * Readers resolve via MISR_STORE_BACKEND: local | r2 | mirror.

boto3 is imported lazily so unit 0.0 has zero heavy deps.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional

from .config import cfg


def local_partition_path(store: str, hour_key: str) -> Path:
    """Local path for an hourly partition, e.g. store='ais', hour='2026-07-21T13'."""
    d = cfg.local_parquet_dir(store) / f"hour={hour_key}"
    d.mkdir(parents=True, exist_ok=True)
    return d / "part.parquet"


def glob_for_reader(store: str) -> str:
    """DuckDB-readable glob for a logical store, honoring the backend."""
    if cfg.store_backend == "r2":
        return f"{cfg.r2_prefix(store)}/hour=*/part.parquet"
    return str(cfg.local_parquet_dir(store) / "hour=*" / "part.parquet")


def _r2_client():
    """Lazy boto3 S3 client pointed at the R2 endpoint. Raises if creds missing."""
    import boto3
    account = os.environ["R2_ACCOUNT_ID"]
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )


def r2_put_file(local_path: str | Path, key: str) -> str:
    """Upload a local file to R2 under `key`. Returns the s3:// URI. Idempotent by key."""
    client = _r2_client()
    bucket = os.environ["R2_BUCKET"]
    client.upload_file(str(local_path), bucket, key)
    return f"s3://{bucket}/{key}"


def r2_key_exists(key: str) -> bool:
    """True if an object already exists — the idempotency check for downloaders."""
    import botocore
    client = _r2_client()
    bucket = os.environ["R2_BUCKET"]
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except botocore.exceptions.ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def mirror_partition_to_r2(store: str, hour_key: str) -> Optional[str]:
    """Mirror one closed local partition to R2. No-op if backend is 'local'."""
    if cfg.store_backend == "local":
        return None
    local = local_partition_path(store, hour_key)
    if not local.exists():
        return None
    key = f"parquet/{store}/hour={hour_key}/part.parquet"
    if r2_key_exists(key):
        return None
    return r2_put_file(local, key)


def raw_scene_key(scene_id: str) -> str:
    """Content-addressed R2 key for a raw Sentinel-1 product."""
    return f"raw/s1/{scene_id}.zip"


def stage_local(local_path: str | Path, store: str, hour_key: str) -> Path:
    """Copy an already-built parquet file into the local partition slot."""
    dest = local_partition_path(store, hour_key)
    shutil.copy2(local_path, dest)
    return dest
