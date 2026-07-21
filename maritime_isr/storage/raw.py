"""Raw landing zone. Write-once, content-addressed, never mutated.

Layout: raw/<source>/<yyyy-mm-dd>/<sha256-prefix>_<name>
An attempt to land identical bytes twice is a no-op (idempotent backfills).
An attempt to overwrite a path with different bytes raises — that is
corruption by definition, not a use case.
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path

from ..config import RAW_ROOT


class RawImmutabilityError(RuntimeError):
    pass


def land(source: str, name: str, payload: bytes,
         day: str | None = None) -> tuple[Path, str]:
    """Land raw bytes. Returns (path, sha256). Idempotent on identical
    content; refuses on divergent content at the same address."""
    digest = hashlib.sha256(payload).hexdigest()
    day = day or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    d = RAW_ROOT / source / day
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{digest[:12]}_{name}"
    if path.exists():
        existing = hashlib.sha256(path.read_bytes()).hexdigest()
        if existing != digest:
            raise RawImmutabilityError(
                f"raw collision at {path}: existing sha {existing[:12]} != new {digest[:12]}")
        return path, digest  # idempotent re-land
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, path)          # atomic
    os.chmod(path, 0o444)          # belt-and-braces read-only
    return path, digest
