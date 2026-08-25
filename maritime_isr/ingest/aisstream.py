"""Unit 0.3 — aisstream.io live AIS connector.

PARKED: awaiting deploy host. This is a live websocket consumer — it only
yields data while a machine stays connected and listening. Under download-only
laptop mode the laptop cannot stay on, so this connector is not run and not
wired into any scheduled path. The code is correct and is kept intact; it
resumes the moment an always-on host exists. See DATA_SOURCES.md.

WebSocket consumer filtered to the AOI bounding box. Parses PositionReport
messages into the canonical schema, dedups, and writes hourly Parquet
partitions through the shared writer. Runs as a systemd service on the VM so it
survives reboots and doesn't depend on your laptop being on.

Buffering: rows accumulate in memory and flush to the current hour's partition
every FLUSH_SECONDS (or on hour rollover), keeping write amplification low while
bounding data loss on crash to one flush interval. A drop counter tracks parser
failures so the <1% drop-rate exit test is measurable from logs.
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import time
from datetime import datetime, timezone

from ..config import cfg
from ..schemas import PositionReport, Provenance, VoyageDeclaration
from ..writer import write_position_reports
from .checks import landed, report_landed
from .landing import land_table, stamp_h3

WS_URL = "wss://stream.aisstream.io/v0/stream"

#: Conformed table for AIS message 5. Separate from the position store
#: because it is a separate message — see `schemas.records.VoyageDeclaration`.
VOYAGE_TABLE = "ais_voyage"
SOURCE_ID = "aisstream"
FLUSH_SECONDS = 300  # 5-minute flushes -> hourly partitions built incrementally


class DropCounter:
    def __init__(self) -> None:
        self.parsed = 0
        self.dropped = 0

    def rate(self) -> float:
        total = self.parsed + self.dropped
        return (self.dropped / total) if total else 0.0


def _ts_of(meta: dict) -> datetime:
    ts_raw = meta.get("time_utc") or meta.get("Timestamp")
    if isinstance(ts_raw, str):
        ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00").split(" +")[0])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts
    return datetime.now(timezone.utc)


def _parse_eta(eta: dict | None, heard: datetime) -> datetime | None:
    """AIS message 5's ETA, which has no year, resolved against when we heard it.

    Message 5 encodes ETA as month, day, hour, minute and nothing else, so the
    year has to be inferred. The rule here is the one every AIS consumer ends up
    at: take the year we heard it in, and if that puts the arrival more than a
    month in the past, it means next year — a ship declaring 03-01 in December
    is talking about January.

    An unset field is 0 in every position (month 0 means "not available"), and
    hour 24 / minute 60 are the explicit not-available codes. Those are absences,
    not errors, and return None rather than raising: a vessel that declined to
    state an ETA has said something different from a vessel that stated a wrong
    one, and collapsing the two would make the arrival check fire on silence.
    """
    if not eta:
        return None
    month, day = eta.get("Month") or 0, eta.get("Day") or 0
    hour, minute = eta.get("Hour"), eta.get("Minute")
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    if hour is None or minute is None or hour > 23 or minute > 59:
        return None
    for year in (heard.year, heard.year + 1):
        try:
            cand = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
        except ValueError:            # 31 February and friends
            return None
        if (heard - cand).days <= 31:
            return cand
    return None


def _parse_static(msg: dict, drops: DropCounter) -> dict | None:
    """Map an aisstream ShipStaticData envelope to a VoyageDeclaration row.

    This is the half of AIS the system did not consume. Positions say where a
    vessel is; message 5 says where she claims to be going and when she claims
    to arrive, and the brief names the comparison between the two as one of the
    strongest suspicion factors available (Area 2). Dropping the message meant
    the comparison could never be built — a detector with no column to read.
    """
    try:
        if msg.get("MessageType") != "ShipStaticData":
            return None
        sd = msg["Message"]["ShipStaticData"]
        meta = msg.get("MetaData", {})
        ts = _ts_of(meta)
        dest = (sd.get("Destination") or "").strip() or None
        row = VoyageDeclaration(
            mmsi=int(meta.get("MMSI") or sd.get("UserID")),
            imo=(sd.get("ImoNumber") or None),
            timestamp=ts,
            lat=float(meta.get("latitude") if meta.get("latitude") is not None
                      else meta["Latitude"]),
            lon=float(meta.get("longitude") if meta.get("longitude") is not None
                      else meta["Longitude"]),
            destination=dest,
            eta=_parse_eta(sd.get("Eta"), ts),
            draught_m=sd.get("MaximumStaticDraught"),
            ship_type=sd.get("Type"),
            receiver_source=SOURCE_ID,
            prov=Provenance(
                source_id=SOURCE_ID,
                source_ref=f"{meta.get('MMSI') or sd.get('UserID')}:static",
                acquired_at=ts,
            ),
        )
        drops.parsed += 1
        d = row.model_dump()
        d["prov"] = row.prov.stamp()
        # The position path gets its cells from `write_position_reports`;
        # `land_table` does not stamp, so this path stamps its own. A located
        # record without H3 is invisible to every join the architecture rests
        # on (CLAUDE.md §3), and it would be invisible silently.
        stamp_h3(d)
        return _flatten(d)
    except Exception:  # noqa: BLE001
        drops.dropped += 1
        return None


def _parse(msg: dict, drops: DropCounter) -> dict | None:
    """Map an aisstream envelope to a canonical PositionReport row dict."""
    try:
        if msg.get("MessageType") != "PositionReport":
            return None
        pr = msg["Message"]["PositionReport"]
        meta = msg.get("MetaData", {})
        ts_raw = meta.get("time_utc") or meta.get("Timestamp")
        if isinstance(ts_raw, str):
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00").split(" +")[0])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        else:
            ts = datetime.now(timezone.utc)
        row = PositionReport(
            mmsi=int(meta.get("MMSI") or pr.get("UserID")),
            lat=float(pr["Latitude"]),
            lon=float(pr["Longitude"]),
            sog=pr.get("Sog"),
            cog=pr.get("Cog"),
            heading=pr.get("TrueHeading"),
            timestamp=ts,
            msg_type=pr.get("MessageID"),
            receiver_source=SOURCE_ID,
            prov=Provenance(
                source_id=SOURCE_ID,
                source_ref=str(meta.get("MMSI") or pr.get("UserID")),
                acquired_at=ts,
            ),
        )
        drops.parsed += 1
        d = row.model_dump()
        d["prov"] = row.prov.stamp()
        return _flatten(d)
    except Exception:  # noqa: BLE001
        drops.dropped += 1
        return None


def _flatten(d: dict) -> dict:
    prov = d.pop("prov")
    d.update(prov)
    return d


async def _consume(max_hours: float | None) -> None:
    import websockets  # lazy

    api_key = os.getenv("AISSTREAM_API_KEY")
    if not api_key:
        raise RuntimeError("AISSTREAM_API_KEY unset")
    a = cfg.aoi
    sub = {
        "APIKey": api_key,
        "BoundingBoxes": [[[a.lat_min, a.lon_min], [a.lat_max, a.lon_max]]],
        "FilterMessageTypes": ["PositionReport", "ShipStaticData"],
    }
    drops = DropCounter()
    buffer: list[dict] = []
    voyages: list[dict] = []
    last_flush = time.monotonic()
    deadline = (time.monotonic() + max_hours * 3600) if max_hours else None
    stop = asyncio.Event()

    def _sig(*_): stop.set()
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(s, _sig)
        except NotImplementedError:
            pass

    while not stop.is_set():
        try:
            async with websockets.connect(WS_URL, ping_interval=20, max_size=2**22) as ws:
                await ws.send(json.dumps(sub))
                print(f"[aisstream] subscribed AOI={a.name}")
                async for raw in ws:
                    msg = json.loads(raw)
                    row = _parse(msg, drops)
                    if row:
                        buffer.append(row)
                    else:
                        voyage = _parse_static(msg, drops)
                        if voyage:
                            voyages.append(voyage)
                    now = time.monotonic()
                    if now - last_flush >= FLUSH_SECONDS and (buffer or voyages):
                        n = _flush(buffer, voyages)
                        print(f"[aisstream] flushed {n} rows "
                              f"drop_rate={drops.rate():.4f}")
                        last_flush = now
                    if deadline and now >= deadline:
                        stop.set()
                        break
        except Exception as e:  # noqa: BLE001
            print(f"[aisstream] socket error, reconnecting in 5s: {e}")
            await asyncio.sleep(5)

    _flush(buffer, voyages)
    print(f"[aisstream] stopped. parsed={drops.parsed} dropped={drops.dropped} "
          f"drop_rate={drops.rate():.4f}")


def _flush(buffer: list[dict], voyages: list[dict]) -> int:
    """Land both streams and clear both buffers.

    Positions go to the hourly Parquet store, declarations to a day-partitioned
    conformed table — different shapes, different cadences, one flush so a crash
    cannot land one without the other.
    """
    n = 0
    if buffer:
        w = write_position_reports(buffer, store="ais")
        n += sum(w.values())
        buffer.clear()
    if voyages:
        # Announced through the shared reporter, not a bare sum. The landed
        # count is what is on disk; the built count only explains a gap. A
        # connector that lands quietly is the same defect as one that
        # overstates, one notch harder to notice.
        written = land_table(voyages, table=VOYAGE_TABLE,
                             key_fields=("mmsi", "timestamp"),
                             day_field="timestamp")
        report_landed("aisstream voyage", VOYAGE_TABLE, written, len(voyages))
        n += landed(written)
        voyages.clear()
    return n


def run(max_hours: float | None = None) -> int:
    asyncio.run(_consume(max_hours))
    return 0
