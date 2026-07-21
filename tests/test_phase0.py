"""Phase 0 acceptance tests. Each test maps to a roadmap criterion or a
design decision that must not silently regress.
"""
import json
from datetime import date, datetime, timezone

import pytest

from maritime_isr.config import AOI_V1
from maritime_isr.connectors import ais, registries, sentinel1
from maritime_isr.storage import raw
from maritime_isr import tiling

TS = datetime(2026, 7, 10, 6, 0, 0, tzinfo=timezone.utc)


# ---- AIVDM decoder: known-answer vectors (checksums computed for these payloads) ----

def _nmea(body: str) -> str:
    x = 0
    for ch in body:
        x ^= ord(ch)
    return f"!{body}*{x:02X}"


def _encode_type1(mmsi, lat, lon, sog, cog, heading, nav=0):
    """Build a synthetic type-1 payload (inverse of the decoder) so the
    decoder is tested round-trip against ITU-R M.1371 bit layout."""
    bits = ""
    def u(v, n): return format(v & ((1 << n) - 1), f"0{n}b")
    bits += u(1, 6) + u(0, 2) + u(mmsi, 30) + u(nav, 4) + u(0, 8)
    bits += u(int(round(sog * 10)), 10) + u(0, 1)
    bits += u(int(round(lon * 600000)), 28) + u(int(round(lat * 600000)), 27)
    bits += u(int(round(cog * 10)), 12) + u(heading, 9) + u(0, 6) + u(0, 2) + u(0, 3) + u(0, 1) + u(0, 19)
    fill = (6 - len(bits) % 6) % 6
    bits += "0" * fill
    payload = ""
    for i in range(0, len(bits), 6):
        v = int(bits[i:i + 6], 2)
        payload += chr(v + 48 if v < 40 else v + 56)
    return payload, fill


def test_aivdm_type1_roundtrip():
    payload, fill = _encode_type1(419001234, lat=18.9, lon=72.8, sog=12.3, cog=245.0, heading=246)
    line = _nmea(f"AIVDM,1,1,,A,{payload},{fill}")
    p = ais.AivdmParser("test_rx", aoi=AOI_V1)
    msg = p.feed(line, TS)
    assert msg is not None
    assert msg["mmsi"] == 419001234
    assert abs(msg["lat"] - 18.9) < 1e-4 and abs(msg["lon"] - 72.8) < 1e-4
    assert abs(msg["sog_kn"] - 12.3) < 0.05 and msg["heading_deg"] == 246


def test_checksum_rejected_and_counted():
    p = ais.AivdmParser("test_rx")
    assert p.feed("!AIVDM,1,1,,A,15MvlfPOh0J>rc>Ir6t>4?vN0<0M,0*00", TS) is None
    assert p.feed("garbage line", TS) is None
    assert p.stats.dropped_checksum == 2
    assert p.stats.drop_rate == 1.0


def test_sentinel_values_nulled():
    payload, fill = _encode_type1(419000001, lat=18.0, lon=70.0, sog=102.3, cog=360.0, heading=511)
    p = ais.AivdmParser("rx", aoi=AOI_V1)
    msg = p.feed(_nmea(f"AIVDM,1,1,,A,{payload},{fill}"), TS)
    assert msg["sog_kn"] is None and msg["cog_deg"] is None and msg["heading_deg"] is None


def test_dedup_collapses_multireceiver_but_keeps_spoof_contradiction():
    base = dict(msg_type=1, mmsi=419000002, lat=15.0, lon=68.0, sog_kn=10.0,
                cog_deg=90.0, heading_deg=90.0, nav_status=0, ts=TS)
    msgs = [
        {**base, "receiver": "rx_a"},
        {**base, "receiver": "rx_b"},                       # same report, second receiver -> collapse
        {**base, "lat": 24.0, "lon": 61.0, "receiver": "rx_c"},  # same MMSI+ts, different position -> KEEP
    ]
    tbl = ais.conform(msgs, source="ais_terrestrial", source_ref="test")
    assert tbl.num_rows == 2
    receivers = set(tbl.column("receiver").to_pylist())
    assert "rx_a|rx_b" in receivers  # multiplicity preserved as evidence
    assert int(tbl.schema.metadata[b"n_deduped"]) == 1
    by_rx = {r["receiver"]: r["n_receipts"] for r in tbl.to_pylist()}
    assert by_rx["rx_a|rx_b"] == 2  # multiplicity survives as a count


def test_raw_store_immutable(tmp_path, monkeypatch):
    monkeypatch.setattr(raw, "RAW_ROOT", tmp_path)
    p1, sha1 = raw.land("test_src", "a.bin", b"payload-1", day="2026-07-01")
    p2, sha2 = raw.land("test_src", "a.bin", b"payload-1", day="2026-07-01")  # idempotent
    assert p1 == p2 and sha1 == sha2
    # divergent content at same address must raise, not overwrite
    fake = p1  # simulate collision by writing different bytes then re-landing
    import os
    os.chmod(fake, 0o644)
    fake.write_bytes(b"corrupted")
    with pytest.raises(raw.RawImmutabilityError):
        raw.land("test_src", "a.bin", b"payload-1", day="2026-07-01")


def test_odata_query_shape():
    url = sentinel1.build_query(AOI_V1, "2026-04-01T00:00:00.000Z", "2026-07-01T00:00:00.000Z")
    assert "SENTINEL-1" in url and "GRDH" in url and "Intersects" in url
    assert "60.0+5.0" in url or "60.0%205.0" in url  # AOI corner present in WKT


def test_odata_parse():
    payload = {"value": [{
        "Id": "abc-123", "Name": "S1A_IW_GRDH_1SDV_20260710T010203",
        "ContentDate": {"Start": "2026-07-10T01:02:03.000Z"},
        "Footprint": "POLYGON((60 5,78 5,78 25,60 25,60 5))",
        "Attributes": [{"Name": "orbitDirection", "Value": "DESCENDING"},
                        {"Name": "relativeOrbitNumber", "Value": 63}],
    }]}
    rows = sentinel1.parse_odata_response(payload)
    assert rows[0]["product_id"] == "abc-123"
    assert rows[0]["orbit_direction"] == "DESCENDING" and rows[0]["relative_orbit"] == 63


def test_sanctions_snapshot_diff(tmp_path, monkeypatch):
    monkeypatch.setattr(raw, "RAW_ROOT", tmp_path / "raw")
    import maritime_isr.storage.catalog as cat
    monkeypatch.setattr(cat, "CATALOG_DB", tmp_path / "cat.sqlite")

    v1 = b'1001,"DARK STAR","vessel","IRAN-EO13902",,,,,,,,"IMO 9123456"\n' \
         b'1002,"SHELL CORP LTD","entity","VENEZUELA",,,,,,,,""\n'
    r1 = registries.snapshot_registry("ofac_sdn", v1, registries.parse_ofac_sdn_csv,
                                      as_of=date(2026, 6, 1))
    assert r1["n_added"] == 2 and r1["n_removed"] == 0
    imos = {r["imo"] for r in r1["table"].to_pylist()}
    assert 9123456 in imos  # IMO extracted from remarks

    # v2: DARK STAR delisted, new vessel added -> delisting closes the interval
    v2 = b'1002,"SHELL CORP LTD","entity","VENEZUELA",,,,,,,,""\n' \
         b'1003,"OCEAN GHOST","vessel","DPRK",,,,,,,,"IMO 9765432"\n'
    r2 = registries.snapshot_registry("ofac_sdn", v2, registries.parse_ofac_sdn_csv,
                                      as_of=date(2026, 7, 1))
    assert r2["n_added"] == 1 and r2["n_removed"] == 1
    closed = [r for r in r2["table"].to_pylist() if r["valid_to"] is not None]
    assert len(closed) == 1 and closed[0]["entry_id"] == "1001"
    assert closed[0]["valid_to"] == date(2026, 7, 1)  # the as-of date the edge must carry


def test_gfw_conform_joins_on_common_grid():
    rows = [{"lat": 16.5, "lon": 67.2, "timestamp": "2026-07-08T01:30:00Z",
             "length_m": 85.0, "matched_mmsi": None, "scene_id": "S1A_x"}]
    tbl = registries.conform_gfw_detections(rows, "gfw_sar_v3_2026Q2")
    cell = tbl.column("h3_cell").to_pylist()[0]
    assert cell == tiling.cell(16.5, 67.2)  # same grid as AIS -> Phase 3 is a hash join


def test_fragment_reassembly():
    # type-5 static message spans 2 fragments; build synthetically
    bits = ""
    def u(v, n): return format(v & ((1 << n) - 1), f"0{n}b")
    def txt(s, n_chars):
        out = ""
        for i in range(n_chars):
            c = s[i] if i < len(s) else "@"
            v = ord(c) - 64 if ord(c) >= 64 else ord(c)
            out += u(v % 64, 6)
        return out
    bits += u(5, 6) + u(0, 2) + u(419000777, 30) + u(0, 2) + u(9312345, 30)
    bits += txt("VTGCALL", 7) + txt("MV TEST VESSEL", 20)
    bits += u(70, 8) + u(120, 9) + u(30, 9) + u(10, 6) + u(12, 6)
    bits += "0" * (424 - len(bits))
    payload = ""
    for i in range(0, len(bits), 6):
        v = int(bits[i:i + 6], 2)
        payload += chr(v + 48 if v < 40 else v + 56)
    half = len(payload) // 2
    p = ais.AivdmParser("rx")
    assert p.feed(_nmea(f"AIVDM,2,1,3,A,{payload[:half]},0"), TS) is None  # pending
    msg = p.feed(_nmea(f"AIVDM,2,2,3,A,{payload[half:]},2"), TS)
    assert msg and msg["msg_type"] == 5
    assert msg["mmsi"] == 419000777 and msg["imo"] == 9312345
    assert msg["shipname"] == "MV TEST VESSEL"
