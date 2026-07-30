"""Satellite AIS connector (Spire-class) — roadmap 2.2: the only paid data
in the prototype. Collapses coverage-gap ambiguity offshore.

Connector discipline identical to Phase 0: normalize INTO the canonical
AIS_POSITION schema, receiver namespaced 'sat:<feed>' so the coverage model
can separate receiver classes, provenance stamped per record. The track
engine never learns this source exists — that is the point.

Honesty note: the HTTP client below is built against Spire's published
Messages-API shape and unit-tested with injected responses. It has never
been exercised against the live API from this sandbox (no egress, no
subscription). Network verification is a deploy-host task, exactly as with
the Copernicus connector in Phase 0.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Iterable

import pandas as pd
import pyarrow as pa

from ..config import PIPELINE_VERSION
from ..provenance import now_iso
from .. import h3util as tiling, schemas

SPIRE_MESSAGES_URL = "https://api.spire.com/vessels/messages"


def normalize_spire_message(rec: dict, feed: str = "spire") -> dict | None:
    """One Spire message JSON → canonical AIS_POSITION dict, or None."""
    try:
        lat, lon = float(rec["latitude"]), float(rec["longitude"])
    except (KeyError, TypeError, ValueError):
        return None
    ts = rec.get("timestamp") or rec.get("received_at")
    if not ts:
        return None
    return dict(
        mmsi=int(rec.get("mmsi", 0)), imo=rec.get("imo"),
        lat=lat, lon=lon,
        sog_kn=rec.get("speed"), cog_deg=rec.get("course"),
        heading_deg=rec.get("heading"),
        nav_status=rec.get("status"), msg_type=int(rec.get("msg_type", 1)),
        ts=pd.Timestamp(ts).tz_localize("UTC")
           if pd.Timestamp(ts).tzinfo is None else pd.Timestamp(ts),
        h3_cell=tiling.cell(lat, lon),
        receiver=f"sat:{feed}", n_receipts=1)


def conform(messages: Iterable[dict], *, feed: str, source_ref: str) -> pa.Table:
    rows = []
    ing = pd.Timestamp(now_iso())
    for m in messages:
        m = dict(m)
        m.setdefault("receiver", f"sat:{feed}")
        m.update(source=f"ais_satellite:{feed}", source_ref=source_ref,
                 acquired_at=m["ts"], ingested_at=ing,
                 pipeline_version=PIPELINE_VERSION)
        rows.append(m)
    if not rows:
        return schemas.AIS_POSITION.empty_table()
    df = pd.DataFrame(rows)
    for f in schemas.AIS_POSITION.names:
        if f not in df.columns:
            df[f] = None
    return pa.Table.from_pandas(df[schemas.AIS_POSITION.names],
                                schema=schemas.AIS_POSITION, preserve_index=False)


def parse_pass_predictions(payload: str | bytes) -> list[tuple[float, float]]:
    """Provider pass-prediction JSON → [(t0_epoch, t1_epoch), ...] for the
    coverage model's SatPassSchedule. Synthetic generator writes the same
    format, so sandbox and deploy host share one code path."""
    data = json.loads(payload)
    out = []
    for w in data.get("passes", []):
        t0 = datetime.fromisoformat(w["start"]).replace(tzinfo=timezone.utc) \
            if "T" in w["start"] and "+" not in w["start"] else \
            datetime.fromisoformat(w["start"])
        t1 = datetime.fromisoformat(w["end"]).replace(tzinfo=timezone.utc) \
            if "T" in w["end"] and "+" not in w["end"] else \
            datetime.fromisoformat(w["end"])
        out.append((t0.timestamp(), t1.timestamp()))
    return sorted(out)


def fetch_messages(session, token: str, *, t0: str, t1: str,
                   bbox: tuple[float, float, float, float]) -> list[dict]:
    """Deploy-host path. `session` injected for testability (Phase 0 pattern)."""
    params = dict(received_after=t0, received_before=t1,
                  latitude_between=f"{bbox[0]},{bbox[1]}",
                  longitude_between=f"{bbox[2]},{bbox[3]}", limit=1000)
    headers = {"Authorization": f"Bearer {token}"}
    out, cursor = [], None
    while True:
        if cursor:
            params["cursor"] = cursor
        resp = session.get(SPIRE_MESSAGES_URL, params=params, headers=headers)
        resp.raise_for_status()
        body = resp.json()
        out.extend(body.get("data", []))
        cursor = body.get("cursor")
        if not cursor:
            return out
