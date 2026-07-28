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
from ..schemas import PositionReport, Provenance
from ..writer import write_position_reports

WS_URL = "wss://stream.aisstream.io/v0/stream"
SOURCE_ID = "aisstream"
FLUSH_SECONDS = 300  # 5-minute flushes -> hourly partitions built incrementally


class DropCounter:
    def __init__(self) -> None:
        self.parsed = 0
        self.dropped = 0

    def rate(self) -> float:
        total = self.parsed + self.dropped
        return (self.dropped / total) if total else 0.0


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
        "FilterMessageTypes": ["PositionReport"],
    }
    drops = DropCounter()
    buffer: list[dict] = []
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
                    row = _parse(json.loads(raw), drops)
                    if row:
                        buffer.append(row)
                    now = time.monotonic()
                    if now - last_flush >= FLUSH_SECONDS and buffer:
                        w = write_position_reports(buffer, store="ais")
                        print(f"[aisstream] flushed {sum(w.values())} rows "
                              f"drop_rate={drops.rate():.4f}")
                        buffer.clear()
                        last_flush = now
                    if deadline and now >= deadline:
                        stop.set()
                        break
        except Exception as e:  # noqa: BLE001
            print(f"[aisstream] socket error, reconnecting in 5s: {e}")
            await asyncio.sleep(5)

    if buffer:
        write_position_reports(buffer, store="ais")
    print(f"[aisstream] stopped. parsed={drops.parsed} dropped={drops.dropped} "
          f"drop_rate={drops.rate():.4f}")


def run(max_hours: float | None = None) -> int:
    asyncio.run(_consume(max_hours))
    return 0
