"""Mirror closed (previous-hour) Parquet partitions to R2. Cron entrypoint.

PARKED: awaiting deploy host. Laptop mode runs MISR_STORE_BACKEND=local with no
Cloudflare R2 bucket provisioned, so there is nothing to mirror to. Kept intact
for the deploy host. See DATA_SOURCES.md.

Only mirrors partitions strictly older than the current hour (the current hour
is still being written by the live consumer). Idempotent: skips keys already in R2.
"""
from __future__ import annotations

from datetime import timedelta

from ..config import cfg
from ..db import PARQUET_STORES
from ..schemas import utcnow
from ..store import mirror_partition_to_r2


def main() -> int:
    if cfg.store_backend == "local":
        print("[mirror] backend=local, nothing to mirror")
        return 0
    now = utcnow()
    # mirror the last 6 closed hours (cheap; catches any gaps from downtime)
    for h in range(1, 7):
        hour_key = (now - timedelta(hours=h)).strftime("%Y-%m-%dT%H")
        for store in PARQUET_STORES:
            uri = mirror_partition_to_r2(store, hour_key)
            if uri:
                print(f"[mirror] {store} {hour_key} -> {uri}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
