"""D1 B4 — registry parser tests (offline, fixture-driven).

The sandbox network policy blocks treasury.gov, un.org, europa.eu and nga.mil,
so these test the parsing and snapshot/diff logic against fixtures rather than
live downloads. The live fetch is verified on Eshan's laptop.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from maritime_isr.ingest import registries as reg

UTC = timezone.utc
AS_OF_1 = datetime(2026, 7, 1, tzinfo=UTC)
AS_OF_2 = datetime(2026, 7, 28, tzinfo=UTC)


@pytest.fixture()
def con(tmp_path, monkeypatch):
    """A throwaway DuckDB connection with the snapshot meta table ready."""
    import duckdb

    c = duckdb.connect(str(tmp_path / "t.duckdb"))
    reg._ensure_snapshot_meta(c)
    yield c
    c.close()


# ==========================================================================
# OFAC
# ==========================================================================

OFAC_CSV = (
    '36,"AEROCARIBBEAN AIRLINES","-0-","CUBA","-0-","-0-","-0-","-0-","-0-","-0-","-0-","-0-"\n'
    '9639,"SEA HARRIER","vessel","IRAN","-0-","ATF123","Cargo","1,900","2,100",'
    '"Panama","BLUEWATER HOLDINGS","Linked to sanctioned entity"\n'
    '9640,"OCEAN PEARL","vessel","IRAN","-0-","ATF999","Tanker","5,000","6,100",'
    '"Iran","MERIDIAN SHIPPING","-0-"\n'
)


def test_parse_ofac_extracts_vessel_fields():
    """OFAC is unusual in naming hulls, not just companies — keep those fields."""
    rows = reg.parse_ofac(OFAC_CSV)
    vessels = [r for r in rows if r["sdn_type"] == "vessel"]
    assert len(vessels) == 2
    v = vessels[0]
    assert v["name"] == "SEA HARRIER"
    assert v["call_sign"] == "ATF123"
    assert v["vessel_flag"] == "Panama"
    assert v["vessel_owner"] == "BLUEWATER HOLDINGS"
    assert v["vessel_type"] == "Cargo"


def test_parse_ofac_treats_dash_zero_dash_as_null():
    """OFAC writes '-0-' for empty. Storing that literal would poison joins."""
    rows = reg.parse_ofac(OFAC_CSV)
    airline = rows[0]
    assert airline["sdn_type"] is None
    assert airline["call_sign"] is None
    assert rows[2]["remarks"] is None


def test_parse_ofac_skips_short_rows():
    assert reg.parse_ofac("1,2\n") == []


def test_ofac_snapshot_is_versioned_and_diffed(con, monkeypatch):
    """Two refreshes must produce two snapshots and a correct added/removed diff."""
    monkeypatch.setattr(reg, "_fetch", lambda *a, **k: OFAC_CSV.encode())
    reg.refresh_ofac(con, AS_OF_1)

    # second pull: one entry removed, one added
    changed = OFAC_CSV.replace(
        '9640,"OCEAN PEARL"', '9999,"NEW HULL"'
    )
    monkeypatch.setattr(reg, "_fetch", lambda *a, **k: changed.encode())
    reg.refresh_ofac(con, AS_OF_2)

    snaps = con.execute(
        "SELECT as_of, n_rows FROM registry_snapshots WHERE source_id='ofac-sdn' ORDER BY as_of"
    ).fetchall()
    assert len(snaps) == 2, "each refresh must add a snapshot, never overwrite"

    added, removed = reg._diff(con, "ofac_sdn", "ofac-sdn", "ent_num", AS_OF_2)
    assert added == 1 and removed == 1


def test_old_snapshot_rows_survive_a_refresh(con, monkeypatch):
    """The whole point of versioning: yesterday's truth is still queryable."""
    monkeypatch.setattr(reg, "_fetch", lambda *a, **k: OFAC_CSV.encode())
    reg.refresh_ofac(con, AS_OF_1)
    reg.refresh_ofac(con, AS_OF_2)

    old = con.execute(
        "SELECT count(*) FROM ofac_sdn WHERE as_of = ?", [AS_OF_1]
    ).fetchone()[0]
    assert old == 3, "the earlier snapshot must remain intact after a later refresh"


def test_every_ofac_row_carries_as_of_and_pipeline_version(con, monkeypatch):
    monkeypatch.setattr(reg, "_fetch", lambda *a, **k: OFAC_CSV.encode())
    reg.refresh_ofac(con, AS_OF_1)
    nulls = con.execute(
        "SELECT count(*) FROM ofac_sdn WHERE as_of IS NULL OR pipeline_version IS NULL"
    ).fetchone()[0]
    assert nulls == 0


# ==========================================================================
# UN
# ==========================================================================

UN_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<CONSOLIDATED_LIST>
  <INDIVIDUALS>
    <INDIVIDUAL>
      <DATAID>6908</DATAID>
      <FIRST_NAME>ABDUL</FIRST_NAME>
      <SECOND_NAME>AZIZ</SECOND_NAME>
      <UN_LIST_TYPE>Al-Qaida</UN_LIST_TYPE>
      <REFERENCE_NUMBER>QDi.001</REFERENCE_NUMBER>
      <LISTED_ON>2001-01-25</LISTED_ON>
      <COMMENTS1>Test individual</COMMENTS1>
    </INDIVIDUAL>
  </INDIVIDUALS>
  <ENTITIES>
    <ENTITY>
      <DATAID>7100</DATAID>
      <FIRST_NAME>SHIPPING LINE CO</FIRST_NAME>
      <UN_LIST_TYPE>DPRK</UN_LIST_TYPE>
      <REFERENCE_NUMBER>KPe.010</REFERENCE_NUMBER>
      <LISTED_ON>2016-11-30</LISTED_ON>
    </ENTITY>
  </ENTITIES>
</CONSOLIDATED_LIST>
"""


def test_parse_un_reads_individuals_and_entities():
    rows = reg.parse_un(UN_XML)
    kinds = {r["entity_kind"] for r in rows}
    assert kinds == {"individual", "entity"}
    assert len(rows) == 2


def test_parse_un_joins_multipart_names():
    rows = reg.parse_un(UN_XML)
    ind = next(r for r in rows if r["entity_kind"] == "individual")
    assert ind["name"] == "ABDUL AZIZ"
    assert ind["reference_number"] == "QDi.001"
    assert ind["listed_on"] == "2001-01-25"


def test_un_snapshot_lands_and_diffs(con, monkeypatch):
    monkeypatch.setattr(reg, "_fetch", lambda *a, **k: UN_XML)
    reg.refresh_un(con, AS_OF_1)
    n = con.execute("SELECT count(*) FROM un_consolidated WHERE as_of=?", [AS_OF_1]).fetchone()[0]
    assert n == 2


# ==========================================================================
# EU
# ==========================================================================

EU_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<export xmlns="http://eu.europa.ec/fpi/fsd/export">
  <sanctionEntity logicalId="13" euReferenceNumber="EU.27.1">
    <regulation programme="UKR" numberTitle="269/2014"/>
    <nameAlias wholeName="EXAMPLE HOLDING LLC"/>
    <identification number="RU123456"/>
  </sanctionEntity>
  <sanctionEntity logicalId="14" euReferenceNumber="EU.27.2">
    <regulation programme="IRN" numberTitle="359/2011"/>
    <nameAlias wholeName="SECOND ENTITY"/>
  </sanctionEntity>
</export>
"""


def test_parse_eu_handles_namespaced_xml():
    """The EU schema is namespaced and has changed shape; parse by local name."""
    rows = reg.parse_eu(EU_XML)
    assert len(rows) == 2
    assert rows[0]["name"] == "EXAMPLE HOLDING LLC"
    assert rows[0]["logical_id"] == "13"
    assert rows[0]["programme"] == "UKR"


def test_eu_snapshot_lands(con, monkeypatch):
    monkeypatch.setattr(reg, "_fetch", lambda *a, **k: EU_XML)
    reg.refresh_eu(con, AS_OF_1)
    n = con.execute("SELECT count(*) FROM eu_consolidated WHERE as_of=?", [AS_OF_1]).fetchone()[0]
    assert n == 2


# ==========================================================================
# WPI
# ==========================================================================

WPI_CSV = (
    "World Port Index Number,Main Port Name,Country Code,Latitude,Longitude,"
    "Harbor Size,Harbor Type\n"
    "48220,MUMBAI,IN,18.9200,72.8300,Large,Coastal Natural\n"
    "48180,KANDLA,IN,23.0167,70.2167,Medium,River Natural\n"
    "53000,ROTTERDAM,NL,51.9500,4.1400,Large,Coastal Natural\n"
)


def test_parse_wpi_reads_positions():
    rows = reg.parse_wpi(WPI_CSV)
    assert len(rows) == 3
    mumbai = next(r for r in rows if r["port_name"] == "MUMBAI")
    assert mumbai["lat"] == pytest.approx(18.92)
    assert mumbai["lon"] == pytest.approx(72.83)
    assert mumbai["country"] == "IN"


def test_parse_wpi_skips_rows_without_a_position():
    bad = "Main Port Name,Latitude,Longitude\nNOWHERE,,\n"
    assert reg.parse_wpi(bad) == []


def test_wpi_rows_are_h3_stamped_and_aoi_flagged(con, monkeypatch):
    """Ports carry H3 cells so 'loitering near a port' is a hash join later."""
    monkeypatch.setattr(reg, "_fetch", lambda *a, **k: WPI_CSV.encode())
    reg.refresh_wpi(con, AS_OF_1)

    rows = con.execute(
        "SELECT port_name, h3_r7, h3_r9, in_aoi FROM wpi_ports WHERE as_of=? ORDER BY port_name",
        [AS_OF_1],
    ).fetchall()
    by_name = {r[0]: r for r in rows}

    assert all(r[1] and r[2] for r in rows), "every port needs res-7 and res-9 cells"
    # Mumbai (18.9N, 72.8E) is inside 5-25N/60-78E; Rotterdam is not.
    assert by_name["MUMBAI"][3] is True
    assert by_name["ROTTERDAM"][3] is False


def test_wpi_h3_matches_the_shared_helper(con, monkeypatch):
    from maritime_isr.h3util import index_both

    monkeypatch.setattr(reg, "_fetch", lambda *a, **k: WPI_CSV.encode())
    reg.refresh_wpi(con, AS_OF_1)
    got = con.execute(
        "SELECT h3_r7, h3_r9 FROM wpi_ports WHERE port_name='MUMBAI' AND as_of=?", [AS_OF_1]
    ).fetchone()
    assert got == index_both(18.92, 72.83)


def test_wpi_accepts_a_zipped_csv(con, monkeypatch):
    import io as _io
    import zipfile

    buf = _io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("UpdatedPub150.csv", WPI_CSV)
    monkeypatch.setattr(reg, "_fetch", lambda *a, **k: buf.getvalue())

    reg.refresh_wpi(con, AS_OF_1)
    n = con.execute("SELECT count(*) FROM wpi_ports WHERE as_of=?", [AS_OF_1]).fetchone()[0]
    assert n == 3


# ==========================================================================
# resilience
# ==========================================================================

def test_one_failing_registry_does_not_stop_the_others(monkeypatch, tmp_path):
    """A gated or moved URL must not cost us the other three sources."""
    import duckdb

    c = duckdb.connect(str(tmp_path / "r.duckdb"))
    monkeypatch.setattr(reg, "connect", lambda *a, **k: c)

    calls = []

    def fake_ofac(con, as_of):
        calls.append("ofac")
        return 1

    def fake_un(con, as_of):
        raise reg.RegistryUnavailable("un: HTTP 403")

    def fake_eu(con, as_of):
        calls.append("eu")
        return 1

    def fake_wpi(con, as_of):
        calls.append("wpi")
        return 1

    monkeypatch.setitem(reg.REFRESHERS, "ofac", fake_ofac)
    monkeypatch.setitem(reg.REFRESHERS, "un", fake_un)
    monkeypatch.setitem(reg.REFRESHERS, "eu", fake_eu)
    monkeypatch.setitem(reg.REFRESHERS, "wpi", fake_wpi)

    rc = reg.run()
    assert rc == 0, "partial success is still success"
    assert set(calls) == {"ofac", "eu", "wpi"}
    c.close()


def test_all_sources_failing_returns_nonzero(monkeypatch, tmp_path):
    import duckdb

    c = duckdb.connect(str(tmp_path / "r2.duckdb"))
    monkeypatch.setattr(reg, "connect", lambda *a, **k: c)

    def boom(con, as_of):
        raise reg.RegistryUnavailable("down")

    for k in list(reg.REFRESHERS):
        monkeypatch.setitem(reg.REFRESHERS, k, boom)

    assert reg.run() == 1
    c.close()


def test_http_error_becomes_registry_unavailable(monkeypatch):
    class FakeResp:
        status_code = 403
        content = b""

    monkeypatch.setattr(reg.requests, "get", lambda *a, **k: FakeResp())
    with pytest.raises(reg.RegistryUnavailable, match="403"):
        reg._fetch("https://example.invalid/x", "test", "x.csv")


def test_all_four_sources_are_registered():
    assert set(reg.REFRESHERS) == {"ofac", "un", "eu", "wpi"}
