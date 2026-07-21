"""Terrestrial AIS connector (roadmap 0.1 #2).

Two inlets, one canonical output:
  - NMEA AIVDM sentences (raw receiver/aggregator streams): real 6-bit
    decoder below for types 1/2/3 (Class A position), 18 (Class B
    position), 5 (static/voyage). Multi-fragment reassembly and checksum
    verification included.
  - JSON position reports (aggregator/NOAA-archive style): field-mapped.

Design decisions that matter downstream:
  - Sentinel values are nulled, not passed through: lat=91, lon=181,
    sog=102.3, cog=360, heading=511 mean 'not available' in ITU-R M.1371.
    Passing them through corrupts every Kalman filter in Phase 2.
  - Dedup keys on (mmsi, ts, lat, lon) but KEEPS receiver multiplicity as
    a count + receiver list: the same report heard by 3 receivers is one
    row; two DIFFERENT positions for one MMSI at one timestamp is NOT a
    duplicate — it is a spoofing tell and both rows survive (Phase 2 2.1:
    'log it, don't discard it').
  - Parser drop accounting is explicit: Phase 0 exit demands <1% drop
    rate, so we count everything we reject and why.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd
import pyarrow as pa

from .. import tiling
from ..provenance import now_iso
from ..schemas import AIS_POSITION

# --- ITU-R M.1371 sentinel values -> null ---
_SENTINELS = {"lat": 91.0, "lon": 181.0, "sog_kn": 102.3, "cog_deg": 360.0,
              "heading_deg": 511.0}


@dataclass
class ParseStats:
    total: int = 0
    parsed: int = 0
    dropped_checksum: int = 0
    dropped_malformed: int = 0
    dropped_unsupported_type: int = 0
    dropped_out_of_aoi: int = 0
    fragments_pending: int = 0
    reasons: dict = field(default_factory=dict)

    @property
    def drop_rate(self) -> float:
        hard_drops = self.dropped_checksum + self.dropped_malformed
        return hard_drops / self.total if self.total else 0.0


# ---------------- NMEA / AIVDM ----------------

def _checksum_ok(sentence: str) -> bool:
    if "*" not in sentence or not sentence.startswith("!"):
        return False
    body, _, cks = sentence[1:].partition("*")
    x = 0
    for ch in body:
        x ^= ord(ch)
    try:
        return x == int(cks[:2], 16)
    except ValueError:
        return False


class _BitReader:
    def __init__(self, payload: str, fill_bits: int):
        bits = []
        for ch in payload:
            v = ord(ch) - 48
            if v > 40:
                v -= 8
            bits.append(f"{v:06b}")
        self.bits = "".join(bits)
        if fill_bits:
            self.bits = self.bits[:-fill_bits] if fill_bits <= len(self.bits) else ""

    def uint(self, start: int, length: int) -> int:
        return int(self.bits[start:start + length], 2)

    def sint(self, start: int, length: int) -> int:
        v = self.uint(start, length)
        if v & (1 << (length - 1)):
            v -= 1 << length
        return v

    def text(self, start: int, length: int) -> str:
        out = []
        for i in range(start, start + length, 6):
            c = self.uint(i, 6)
            out.append(chr(c + 64) if c < 32 else chr(c))
        return "".join(out).replace("@", "").strip()


def _decode_payload(payload: str, fill_bits: int) -> dict | None:
    br = _BitReader(payload, fill_bits)
    if len(br.bits) < 38:
        return None
    mtype = br.uint(0, 6)
    mmsi = br.uint(8, 30)
    if mtype in (1, 2, 3) and len(br.bits) >= 144:
        return {
            "msg_type": mtype, "mmsi": mmsi,
            "nav_status": br.uint(38, 4),
            "sog_kn": br.uint(50, 10) / 10.0,
            "lon": br.sint(61, 28) / 600000.0,
            "lat": br.sint(89, 27) / 600000.0,
            "cog_deg": br.uint(116, 12) / 10.0,
            "heading_deg": float(br.uint(128, 9)),
        }
    if mtype == 18 and len(br.bits) >= 141:
        return {
            "msg_type": 18, "mmsi": mmsi, "nav_status": None,
            "sog_kn": br.uint(46, 10) / 10.0,
            "lon": br.sint(57, 28) / 600000.0,
            "lat": br.sint(85, 27) / 600000.0,
            "cog_deg": br.uint(112, 12) / 10.0,
            "heading_deg": float(br.uint(124, 9)),
        }
    if mtype == 5 and len(br.bits) >= 420:
        return {
            "msg_type": 5, "mmsi": mmsi,
            "imo": br.uint(40, 30),
            "callsign": br.text(70, 42),
            "shipname": br.text(112, 120),
            "ship_type": br.uint(232, 8),
            "dim_bow": br.uint(240, 9), "dim_stern": br.uint(249, 9),
            "dim_port": br.uint(258, 6), "dim_stbd": br.uint(264, 6),
        }
    return {"msg_type": mtype, "mmsi": mmsi, "_unsupported": True}


class AivdmParser:
    """Streaming AIVDM parser with multi-fragment reassembly."""

    def __init__(self, receiver: str, aoi=None):
        self.receiver = receiver
        self.aoi = aoi
        self.stats = ParseStats()
        self._frags: dict[tuple, dict] = {}

    def feed(self, line: str, ts: datetime) -> dict | None:
        """Feed one NMEA line with its receive timestamp. Returns a decoded
        message dict or None (dropped / fragment pending / static msg kept
        separately by caller)."""
        self.stats.total += 1
        line = line.strip()
        if not _checksum_ok(line):
            self.stats.dropped_checksum += 1
            return None
        parts = line.split(",")
        if len(parts) < 7 or parts[0] not in ("!AIVDM", "!AIVDO"):
            self.stats.dropped_malformed += 1
            return None
        try:
            n_frag, i_frag = int(parts[1]), int(parts[2])
            seq, channel, payload = parts[3], parts[4], parts[5]
            fill = int(parts[6].split("*")[0])
        except (ValueError, IndexError):
            self.stats.dropped_malformed += 1
            return None

        if n_frag > 1:
            key = (seq, channel, n_frag)
            slot = self._frags.setdefault(key, {})
            slot[i_frag] = (payload, fill)
            if len(slot) < n_frag:
                self.stats.fragments_pending += 1
                return None
            payload = "".join(slot[i][0] for i in sorted(slot))
            fill = slot[n_frag][1]
            del self._frags[key]

        msg = _decode_payload(payload, fill)
        if msg is None:
            self.stats.dropped_malformed += 1
            return None
        if msg.get("_unsupported"):
            self.stats.dropped_unsupported_type += 1
            return None

        msg["ts"] = ts
        msg["receiver"] = self.receiver
        for k, sentinel in _SENTINELS.items():
            if k in msg and msg[k] is not None and abs(msg[k] - sentinel) < 1e-9:
                msg[k] = None
        if "lat" in msg and self.aoi and msg["lat"] is not None:
            if not self.aoi.contains(msg["lat"], msg["lon"]):
                self.stats.dropped_out_of_aoi += 1
                return None
        self.stats.parsed += 1
        return msg


# ---------------- JSON aggregator inlet ----------------

_JSON_FIELD_MAP = {  # aggregator field -> canonical
    "MMSI": "mmsi", "mmsi": "mmsi",
    "LAT": "lat", "lat": "lat", "latitude": "lat",
    "LON": "lon", "lon": "lon", "longitude": "lon",
    "SOG": "sog_kn", "sog": "sog_kn", "speed": "sog_kn",
    "COG": "cog_deg", "cog": "cog_deg", "course": "cog_deg",
    "Heading": "heading_deg", "heading": "heading_deg",
    "Status": "nav_status", "nav_status": "nav_status",
    "IMO": "imo", "imo": "imo",
    "BaseDateTime": "ts", "timestamp": "ts", "ts": "ts",
    "MessageType": "msg_type",
}


def normalize_json_report(rec: dict, receiver: str) -> dict | None:
    out = {"receiver": receiver}
    for k, v in rec.items():
        ck = _JSON_FIELD_MAP.get(k)
        if ck:
            out[ck] = v
    if "mmsi" not in out or "lat" not in out or "lon" not in out or "ts" not in out:
        return None
    if isinstance(out["ts"], str):
        out["ts"] = datetime.fromisoformat(out["ts"].replace("Z", "+00:00"))
    if out["ts"].tzinfo is None:
        out["ts"] = out["ts"].replace(tzinfo=timezone.utc)
    for k, sentinel in _SENTINELS.items():
        if out.get(k) is not None and abs(float(out[k]) - sentinel) < 1e-9:
            out[k] = None
    return out


# ---------------- Conform + dedup ----------------

def conform(messages: list[dict], *, source: str, source_ref: str) -> pa.Table:
    """Position messages -> canonical AIS_POSITION table with dedup.

    Dedup: identical (mmsi, ts, lat, lon) collapse to one row, receivers
    concatenated. Same (mmsi, ts) with DIFFERENT position is preserved —
    that contradiction is Phase 2/5 spoofing signal.
    """
    rows = [m for m in messages if m.get("lat") is not None and m.get("msg_type") in (1, 2, 3, 18)]
    if not rows:
        return AIS_POSITION.empty_table()
    df = pd.DataFrame(rows)
    for col in ("imo", "nav_status", "heading_deg", "sog_kn", "cog_deg"):
        if col not in df:
            df[col] = None
    df["ts"] = pd.to_datetime(df["ts"], utc=True)

    before = len(df)
    df = (df.groupby(["mmsi", "ts", "lat", "lon"], as_index=False)
            .agg(sog_kn=("sog_kn", "first"), cog_deg=("cog_deg", "first"),
                 heading_deg=("heading_deg", "first"), nav_status=("nav_status", "first"),
                 msg_type=("msg_type", "first"), imo=("imo", "first"),
                 receiver=("receiver", lambda s: "|".join(sorted(set(s)))),
                 n_receipts=("receiver", "size")))
    n_deduped = before - len(df)

    df["h3_cell"] = [tiling.cell(la, lo) for la, lo in zip(df.lat, df.lon)]
    df["source"] = source
    df["source_ref"] = source_ref
    df["acquired_at"] = df["ts"]
    df["ingested_at"] = pd.Timestamp(now_iso())
    from ..config import PIPELINE_VERSION
    df["pipeline_version"] = PIPELINE_VERSION
    df["nav_status"] = df["nav_status"].astype("Int32")
    df["msg_type"] = df["msg_type"].astype("Int32")
    df["n_receipts"] = df["n_receipts"].astype("Int32")
    df["imo"] = df["imo"].astype("Int64")

    tbl = pa.Table.from_pandas(df[[f.name for f in AIS_POSITION]], schema=AIS_POSITION,
                               preserve_index=False)
    tbl = tbl.replace_schema_metadata({"n_deduped": str(n_deduped)})
    return tbl
