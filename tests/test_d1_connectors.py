"""D1 B3 — connector tests.

These run entirely offline against fixtures shaped like real GFW responses. The
sandbox has no GFW credentials and its network policy blocks the GFW host, so
nothing here contacts the live API — that verification happens on Eshan's laptop
(CLAUDE.md §5: this is "built, unverified on host").

What these tests actually protect:
  * the provenance envelope is refused-if-missing, not silently skipped
  * H3 stamping happens at ingest, from the one shared helper
  * re-running a pull merges instead of duplicating
  * AOI scoping is enforced on every source
  * gridded SAR aggregates never masquerade as individual contacts
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from maritime_isr.config import AOI_V1
from maritime_isr.ingest import gfw, gfw_client, gfw_events, gfw_vessels
from maritime_isr.ingest.landing import (
    land_table,
    read_table,
    stamp_envelope,
    stamp_h3,
    table_day_partitions,
)

UTC = timezone.utc
T0 = datetime(2026, 6, 15, 8, 30, tzinfo=UTC)

# A point inside the AOI (Arabian Sea) and one well outside it (North Atlantic).
IN_AOI = (15.5, 68.2)
OUT_AOI = (45.0, -30.0)


@pytest.fixture(autouse=True)
def _isolated_data_root(tmp_path, monkeypatch):
    """Point every landing call at a temp dir so tests never touch real data."""
    from maritime_isr import config as cfg_mod
    from maritime_isr.ingest import landing

    monkeypatch.setattr(landing.cfg, "data_root", tmp_path, raising=False)
    monkeypatch.setattr(cfg_mod.cfg, "data_root", tmp_path, raising=False)
    return tmp_path


# ==========================================================================
# landing layer
# ==========================================================================

def _row(acquired_at=T0, **over):
    r = {"lat": IN_AOI[0], "lon": IN_AOI[1], "event_id": "e1", "value": 1}
    r.update(over)
    stamp_h3(r)
    stamp_envelope(r, source_id="test", source_ref=str(r["event_id"]),
                   acquired_at=acquired_at)
    return r


def test_land_table_refuses_rows_without_provenance():
    """A row with no envelope must be rejected loudly, not landed quietly."""
    naked = {"event_id": "x", "lat": 15.0, "lon": 68.0, "acquired_at": T0}
    with pytest.raises(ValueError, match="provenance"):
        land_table([naked], table="t_naked", key_fields=("event_id",))


def test_envelope_has_all_six_columns():
    r = _row()
    for col in ("source_id", "source_ref", "acquired_at", "ingested_at",
                "pipeline_version", "confidence"):
        assert col in r, f"envelope missing {col}"
    assert r["pipeline_version"], "pipeline_version must record the processing git SHA"


def test_envelope_rejects_naive_datetime():
    with pytest.raises(ValueError, match="timezone-aware"):
        stamp_envelope({}, source_id="t", source_ref="r",
                       acquired_at=datetime(2026, 6, 15, 8, 30))  # no tzinfo


def test_h3_is_stamped_at_ingest():
    r = _row()
    assert r["h3_r7"] and r["h3_r9"]
    assert r["h3_r7"] != r["h3_r9"], "res-7 and res-9 cells must differ"


def test_h3_matches_the_shared_helper():
    """Connectors must not hand-roll their own latlng_to_cell calls."""
    from maritime_isr.h3util import index_both

    r = _row()
    assert (r["h3_r7"], r["h3_r9"]) == index_both(*IN_AOI)


def test_h3_skipped_when_row_has_no_position():
    r = {"event_id": "no-pos"}
    stamp_h3(r)
    assert "h3_r7" not in r


def test_land_table_is_idempotent_on_rerun():
    """Landing the same window twice must converge, not duplicate."""
    rows = [_row(event_id=f"e{i}") for i in range(5)]
    land_table(rows, table="t_idem", key_fields=("event_id",))
    land_table(rows, table="t_idem", key_fields=("event_id",))
    assert len(read_table("t_idem")) == 5


def test_land_table_updates_existing_row_on_rerun():
    land_table([_row(event_id="e1", value=1)], table="t_upd", key_fields=("event_id",))
    land_table([_row(event_id="e1", value=2)], table="t_upd", key_fields=("event_id",))
    got = read_table("t_upd")
    assert len(got) == 1 and got[0]["value"] == 2


def test_land_table_partitions_by_day():
    rows = [
        _row(event_id="a", acquired_at=T0),
        _row(event_id="b", acquired_at=T0 + timedelta(days=1)),
        _row(event_id="c", acquired_at=T0 + timedelta(days=2)),
    ]
    land_table(rows, table="t_part", key_fields=("event_id",))
    assert len(table_day_partitions("t_part")) == 3


def test_land_table_handles_ragged_optional_fields():
    """Rows where an optional field is present in some and absent in others."""
    a = _row(event_id="a")
    b = _row(event_id="b")
    b["extra_only_here"] = "x"
    land_table([a, b], table="t_ragged", key_fields=("event_id",))
    got = read_table("t_ragged")
    assert len(got) == 2
    assert all("extra_only_here" in r for r in got)


def test_land_table_empty_input_is_noop():
    assert land_table([], table="t_empty", key_fields=("event_id",)) == {}


# ==========================================================================
# gfw_client
# ==========================================================================

def test_missing_token_raises_with_actionable_message(monkeypatch):
    monkeypatch.delenv("GFW_API_TOKEN", raising=False)
    with pytest.raises(gfw_client.GFWAuthError) as e:
        gfw_client.token()
    msg = str(e.value)
    assert "GFW_API_TOKEN" in msg and ".env" in msg, "error must tell the operator what to do"


def test_aoi_geojson_is_a_closed_polygon_covering_the_aoi():
    gj = gfw_client.aoi_geojson(AOI_V1)
    assert gj["type"] == "Polygon"
    ring = gj["coordinates"][0]
    assert ring[0] == ring[-1], "GeoJSON ring must close"
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    assert min(lons) == AOI_V1.lon_min and max(lons) == AOI_V1.lon_max
    assert min(lats) == AOI_V1.lat_min and max(lats) == AOI_V1.lat_max


# ==========================================================================
# gfw_events
# ==========================================================================

def _encounter(**over):
    ev = {
        "id": "enc-1",
        "type": "ENCOUNTER",
        "start": "2026-06-15T08:30:00Z",
        "end": "2026-06-15T12:00:00Z",
        "position": {"lat": IN_AOI[0], "lon": IN_AOI[1]},
        "confidence": "4",
        "vessels": [
            {"id": "v-aaa", "ssvid": "419000123", "name": "SEA HARRIER",
             "flag": "IND", "type": "FISHING", "imo": "9111222"},
            {"id": "v-bbb", "ssvid": "422000456", "name": "GULF CARRIER",
             "flag": "IRN", "type": "CARRIER"},
        ],
        "encounter": {"type": "FISHING-CARRIER"},
    }
    ev.update(over)
    return ev


def test_map_event_extracts_core_fields():
    r = gfw_events.map_event(_encounter(), "encounters")
    assert r is not None
    assert r["event_id"] == "enc-1"
    assert r["event_kind"] == "encounters"
    assert r["mmsi"] == "419000123"
    assert r["ship_name"] == "SEA HARRIER"
    assert r["flag"] == "IND"


def test_map_event_captures_the_encounter_counterpart():
    """The other ship in a rendezvous is the point of an encounter."""
    r = gfw_events.map_event(_encounter(), "encounters")
    assert r["counterpart_vessel_id"] == "v-bbb"
    assert r["counterpart_mmsi"] == "422000456"
    assert r["counterpart_name"] == "GULF CARRIER"
    assert r["encounter_type"] == "FISHING-CARRIER"


def test_map_event_computes_duration():
    r = gfw_events.map_event(_encounter(), "encounters")
    assert r["duration_hours"] == pytest.approx(3.5)


def test_map_event_maps_gfw_confidence_onto_the_envelope():
    """GFW's 2/3/4 confidence must survive as a 0-1 confidence, not be dropped."""
    high = gfw_events.map_event(_encounter(confidence="4"), "encounters")
    low = gfw_events.map_event(_encounter(confidence="2"), "encounters")
    assert high["confidence"] == 0.9
    assert low["confidence"] == 0.3
    assert high["gfw_confidence_raw"] == "4"


def test_map_event_rejects_out_of_aoi():
    ev = _encounter(position={"lat": OUT_AOI[0], "lon": OUT_AOI[1]})
    assert gfw_events.map_event(ev, "encounters") is None


def test_map_event_rejects_record_with_no_id_or_time():
    assert gfw_events.map_event({"position": {"lat": 15.0, "lon": 68.0}}, "gaps") is None


def test_map_event_stamps_h3_and_provenance():
    r = gfw_events.map_event(_encounter(), "encounters")
    assert r["h3_r7"] and r["h3_r9"]
    assert r["source_id"] == "gfw-events"
    assert r["source_ref"] == "encounters:enc-1"
    assert r["acquired_at"] == datetime(2026, 6, 15, 8, 30, tzinfo=UTC)


def test_all_four_event_kinds_are_configured():
    assert set(gfw_events.EVENT_SPECS) == {"encounters", "loitering", "port_visits", "gaps"}
    for kind, spec in gfw_events.EVENT_SPECS.items():
        assert spec["dataset"].startswith("public-global-")
        assert spec["table"].startswith("gfw_")


def test_gap_event_maps_and_lands():
    gap = {
        "id": "gap-9",
        "type": "GAP",
        "start": "2026-06-20T02:00:00Z",
        "end": "2026-06-21T06:00:00Z",
        "position": {"lat": 12.0, "lon": 65.0},
        "confidence": "3",
        "vessel": {"id": "v-ccc", "ssvid": "419777888", "name": "NIGHT RUNNER", "flag": "IND"},
        "gap": {"distanceKm": 240.0, "impliedSpeedKnots": 8.4},
    }
    r = gfw_events.map_event(gap, "gaps")
    assert r["gap_distance_km"] == 240.0
    assert r["gap_implied_speed_kn"] == 8.4
    land_table([r], table="gfw_ais_gaps", key_fields=("event_id",), day_field="start_time")
    assert len(read_table("gfw_ais_gaps")) == 1


# ==========================================================================
# gfw SAR — the aggregate/contact distinction
# ==========================================================================

def test_grid_cell_is_flagged_as_aggregate():
    """A grid cell is a COUNT. It must never be mistakable for a contact."""
    cell = {"lat": IN_AOI[0], "lon": IN_AOI[1], "date": "2026-06-15", "detections": 7}
    r = gfw.map_grid_cell(cell)
    assert r["is_aggregate"] is True
    assert r["detection_count"] == 7
    assert "cell_lat" in r and "cell_lon" in r


def test_grid_cells_land_in_a_separate_table_from_detections():
    """Guards against feeding aggregates to the Phase 3 association engine."""
    assert gfw.GRID_TABLE != gfw.DETECTION_TABLE
    assert "grid" in gfw.GRID_TABLE


def test_grid_cell_rejects_out_of_aoi():
    cell = {"lat": OUT_AOI[0], "lon": OUT_AOI[1], "date": "2026-06-15", "detections": 3}
    assert gfw.map_grid_cell(cell) is None


def test_portal_row_is_flagged_as_a_real_contact():
    r = gfw.map_portal_row(
        {"lat": "15.5", "lon": "68.2", "timestamp": "2026-06-15T08:30:00Z",
         "length_m": "84.2", "matched": "false", "presence_score": "0.93"}, 0)
    assert r["is_aggregate"] is False
    assert r["length_m"] == 84.2
    assert r["matched_to_ais"] is False
    assert r["confidence"] == 0.93


def test_portal_row_accepts_column_aliases():
    """Portal column names have changed between releases; aliases must work."""
    r = gfw.map_portal_row(
        {"latitude": "15.5", "longitude": "68.2",
         "detect_timestamp": "2026-06-15 08:30:00", "vessel_length_m": "50"}, 0)
    assert r is not None and r["length_m"] == 50.0


def test_portal_row_parses_matched_variants():
    for raw, want in (("true", True), ("1", True), ("matched", True),
                      ("false", False), ("0", False), ("unmatched", False)):
        r = gfw.map_portal_row(
            {"lat": "15.5", "lon": "68.2", "timestamp": "2026-06-15T08:30:00Z",
             "matched": raw}, 0)
        assert r["matched_to_ais"] is want, f"{raw!r} should map to {want}"


def test_portal_row_synthesises_a_deterministic_id_when_absent():
    args = {"lat": "15.5", "lon": "68.2", "timestamp": "2026-06-15T08:30:00Z"}
    a = gfw.map_portal_row(dict(args), 0)
    b = gfw.map_portal_row(dict(args), 1)
    assert a["detection_id"] == b["detection_id"], "id must be content-derived, not index-derived"


def test_portal_csv_import_is_idempotent(tmp_path):
    csv_text = (
        "lat,lon,timestamp,length_m,matched\n"
        "15.5,68.2,2026-06-15T08:30:00Z,84.2,false\n"
        "16.1,69.0,2026-06-15T08:31:00Z,32.0,true\n"
        "45.0,-30.0,2026-06-15T08:32:00Z,50.0,true\n"   # out of AOI, must be dropped
    )
    path = tmp_path / "sar.csv"
    path.write_text(csv_text, encoding="utf-8")

    assert gfw.import_portal_csv(path) == 0
    assert gfw.import_portal_csv(path) == 0          # re-run
    rows = read_table(gfw.DETECTION_TABLE)
    assert len(rows) == 2, "out-of-AOI row must be dropped and re-runs must not duplicate"


def test_portal_csv_missing_file_returns_error(tmp_path):
    assert gfw.import_portal_csv(tmp_path / "nope.csv") == 1


def test_sar_gridded_degrades_gracefully_when_dataset_offline(monkeypatch):
    """The SAR datasets are offline upstream; that must not crash the run."""
    def boom(*a, **k):
        raise gfw_client.GFWUnavailable("SAR offline pending Sentinel-1C/1D migration")

    monkeypatch.setattr(gfw, "fetch_gridded", boom)
    assert gfw.run_gridded(weeks=8) == 0


def test_sar_gridded_degrades_gracefully_without_a_token(monkeypatch):
    monkeypatch.delenv("GFW_API_TOKEN", raising=False)

    def boom(*a, **k):
        raise gfw_client.GFWAuthError("no token")

    monkeypatch.setattr(gfw, "fetch_gridded", boom)
    assert gfw.run_gridded(weeks=8) == 0


# ==========================================================================
# gfw_vessels — time-scoped identity
# ==========================================================================

VESSEL_PAYLOAD = {
    "registryInfo": [
        {"ssvid": "419000123", "imo": "9111222", "shipname": "SEA HARRIER",
         "callsign": "ATF123", "flag": "IND", "lengthM": 84.2, "tonnageGt": 1900,
         "geartypes": ["TRAWLERS"], "sourceCode": ["IMO"],
         "transmissionDateFrom": "2019-01-01T00:00:00Z",
         "transmissionDateTo": "2023-06-30T00:00:00Z"},
        {"ssvid": "419000999", "imo": "9111222", "shipname": "OCEAN PEARL",
         "callsign": "ATF999", "flag": "PAN", "lengthM": 84.2,
         "sourceCode": ["IMO"],
         "transmissionDateFrom": "2023-07-01T00:00:00Z",
         "transmissionDateTo": "2026-07-01T00:00:00Z"},
    ],
    "registryOwners": [
        {"name": "BLUEWATER HOLDINGS", "flag": "ARE", "ssvid": "419000123",
         "sourceCode": ["REG"], "dateFrom": "2019-01-01T00:00:00Z",
         "dateTo": "2023-06-30T00:00:00Z"},
        {"name": "MERIDIAN SHIPPING", "flag": "PAN", "ssvid": "419000999",
         "sourceCode": ["REG"], "dateFrom": "2023-07-01T00:00:00Z", "dateTo": None},
    ],
    "selfReportedInfo": [
        {"ssvid": "419000123", "shipname": "SEA HARRIER", "flag": "IND",
         "firstTransmissionDate": "2019-02-01T00:00:00Z",
         "lastTransmissionDate": "2023-06-01T00:00:00Z"},
    ],
}


def test_identity_rows_are_time_scoped():
    rows = gfw_vessels.map_identity_rows("v-aaa", VESSEL_PAYLOAD)
    assert rows, "expected identity intervals"
    for r in rows:
        assert r["valid_from"] is not None, "every identity fact needs valid_from"
        assert "valid_to" in r


def test_identity_rows_keep_registry_and_self_reported_separate():
    """Self-reported identity disagreeing with the registry is itself a signal."""
    rows = gfw_vessels.map_identity_rows("v-aaa", VESSEL_PAYLOAD)
    kinds = {r["record_kind"] for r in rows}
    assert kinds == {"registry", "self_reported"}


def test_identity_captures_a_rename_and_reflag():
    rows = [r for r in gfw_vessels.map_identity_rows("v-aaa", VESSEL_PAYLOAD)
            if r["record_kind"] == "registry"]
    names = {r["ship_name"] for r in rows}
    flags = {r["flag"] for r in rows}
    assert names == {"SEA HARRIER", "OCEAN PEARL"}
    assert flags == {"IND", "PAN"}


def test_owner_rows_carry_valid_from_and_to():
    rows = gfw_vessels.map_owner_rows("v-aaa", VESSEL_PAYLOAD)
    assert len(rows) == 2
    open_ended = [r for r in rows if r["valid_to"] is None]
    assert len(open_ended) == 1, "the current owner has no end date"
    assert open_ended[0]["owner_name"] == "MERIDIAN SHIPPING"


def test_current_row_counts_identity_churn():
    """Name/flag/MMSI churn is a reason to look, so it must be counted."""
    r = gfw_vessels.map_current_row("v-aaa", VESSEL_PAYLOAD)
    assert r["n_distinct_names"] == 2      # SEA HARRIER -> OCEAN PEARL
    assert r["n_distinct_flags"] == 2      # IND -> PAN (self-reported IND is a repeat)
    assert r["n_distinct_mmsi"] == 2       # 419000123 -> 419000999
    assert r["n_owners"] == 2


def test_current_row_none_when_no_identity():
    assert gfw_vessels.map_current_row("v-empty", {}) is None


def test_vessel_ids_are_harvested_from_events_including_counterparts():
    r = gfw_events.map_event(_encounter(), "encounters")
    land_table([r], table="gfw_encounters", key_fields=("event_id",), day_field="start_time")
    ids = gfw_vessels.vessel_ids_from_events()
    assert "v-aaa" in ids and "v-bbb" in ids, "both sides of an encounter must be fetched"


def test_vessel_rows_carry_provenance():
    for rows in (gfw_vessels.map_identity_rows("v-aaa", VESSEL_PAYLOAD),
                 gfw_vessels.map_owner_rows("v-aaa", VESSEL_PAYLOAD)):
        for r in rows:
            assert r["source_id"] == "gfw-vessels"
            assert r["pipeline_version"]


# ==========================================================================
# cross-connector invariants
# ==========================================================================

@pytest.mark.parametrize("module", [gfw, gfw_events, gfw_vessels])
def test_connectors_declare_a_source_id(module):
    assert getattr(module, "SOURCE_ID", "").startswith("gfw")


def test_no_connector_writes_into_the_fusion_stores():
    """Connectors land their own tables; fusion adapts them later, not now."""
    import inspect

    for module in (gfw, gfw_events, gfw_vessels):
        src = inspect.getsource(module)
        assert "write_position_reports" not in src, (
            f"{module.__name__} must not write into the live AIS store")
        assert "write_detections" not in src, (
            f"{module.__name__} must not write into the detections store — "
            "a schema adapter is a separate, deliberate step")


# ==========================================================================
# mapping gaps found on the first live run, 2026-07-29
#
# These fixtures are copied from ACTUAL landed payloads, not invented. The
# previous fixtures encoded my own assumptions and so could not catch a wrong
# field name — which is exactly how these four gaps survived to production.
# ==========================================================================

LIVE_GAP = {
    "id": "bad1088734ad2b7ac86d16d9e72e6e0e",
    "type": "gap",
    "start": "2026-06-08T01:45:37.000Z",
    "end": "2026-06-08T14:04:36.000Z",
    "position": {"lat": 10.1007, "lon": 60.6156},
    "distances": {"startDistanceFromPortKm": 1015.078, "endDistanceFromPortKm": 1054.43575,
                  "startDistanceFromShoreKm": 691, "endDistanceFromShoreKm": 742},
    "gap": {"distanceKm": "53.329081410422006", "durationHours": 12.3,
            "impliedSpeedKnots": "2.34108945769118", "intentionalDisabling": True,
            "positions12HoursBeforeSat": "14", "positionsPerDaySatReception": 104.86736686956927,
            "offPosition": {"lat": 10.10, "lon": 60.61},
            "onPosition": {"lat": 10.42, "lon": 60.93}},
    "vessel": {"id": "324afd84e-ee92-ee99-0bf7-c56a66f99624", "ssvid": "636026120",
               "flag": None, "name": None, "type": None},
}

LIVE_PORT_VISIT = {
    "id": "b3f54b1b1e183603802b107d3a0766b0",
    "type": "port_visit",
    "start": "2012-01-04T06:18:14.000Z",
    "end": "2026-06-06T12:01:31.000Z",
    "position": {"lat": 22.5032, "lon": 69.6557},
    "distances": {"startDistanceFromPortKm": 0, "endDistanceFromPortKm": 0,
                  "startDistanceFromShoreKm": 9, "endDistanceFromShoreKm": 5},
    "port_visit": {
        "confidence": "3", "durationHrs": 126413.7213888889,
        "visitId": "cc6a1dcf47308613d2cbe43685a4d297",
        "startAnchorage": {"anchorageId": "39572851", "id": "ind-mundra", "name": "MUNDRA",
                           "flag": "IND", "lat": 22.74, "lon": 69.70, "atDock": True,
                           "distanceFromShoreKm": 2},
        "intermediateAnchorage": {"anchorageId": "3956df43", "id": "ind-vadinar",
                                  "name": "VADINAR", "flag": "IND", "lat": 22.35, "lon": 69.72},
        "endAnchorage": {"anchorageId": "3956df43", "id": "ind-vadinar", "name": "VADINAR",
                         "flag": "IND", "lat": 22.35, "lon": 69.72},
    },
    "vessel": {"id": "d88dfa283-31bc-8e02-088c-206c25c73d59", "ssvid": "419759000",
               "flag": "IND", "name": None, "type": "other"},
}


def test_gap_captures_gfw_intentional_disabling():
    """GFW's own dark-vessel judgement was being dropped entirely."""
    r = gfw_events.map_event(LIVE_GAP, "gaps")
    assert r["gfw_intentional_disabling"] is True


def test_gap_captures_gfw_coverage_evidence():
    """The basis for GFW's judgement — how much they could actually hear.

    Per CLAUDE.md §6 we must never re-assert a gap as our own finding, so
    keeping GFW's evidence alongside their verdict is what makes that possible.
    """
    r = gfw_events.map_event(LIVE_GAP, "gaps")
    assert r["gap_positions_12h_before_sat"] == 14.0
    assert r["gap_positions_per_day_sat"] == pytest.approx(104.867, rel=1e-4)


def test_gap_coerces_gfw_string_numerics():
    """GFW returns distanceKm and impliedSpeedKnots as STRINGS."""
    r = gfw_events.map_event(LIVE_GAP, "gaps")
    assert isinstance(r["gap_distance_km"], float)
    assert r["gap_distance_km"] == pytest.approx(53.329, rel=1e-4)
    assert isinstance(r["gap_implied_speed_kn"], float)


def test_gap_captures_off_and_on_positions():
    r = gfw_events.map_event(LIVE_GAP, "gaps")
    assert r["gap_off_lat"] == 10.10
    assert r["gap_on_lon"] == 60.93


@pytest.mark.parametrize("payload,kind", [(LIVE_GAP, "gaps"), (LIVE_PORT_VISIT, "port_visits")])
def test_distances_are_captured_on_every_event_type(payload, kind):
    """This block is what makes loitering interpretable, and removes the WPI
    dependency (ADR-016). It is present on every event type."""
    r = gfw_events.map_event(payload, kind)
    assert r["start_distance_from_port_km"] is not None
    assert r["end_distance_from_shore_km"] is not None


def test_port_visit_confidence_is_found_where_it_actually_lives():
    """Only port visits carry confidence, nested at port_visit.confidence.

    Reading ev["confidence"] wrote null on ~99% of rows while the envelope
    looked populated.
    """
    r = gfw_events.map_event(LIVE_PORT_VISIT, "port_visits")
    assert r["gfw_confidence_raw"] == "3"
    assert r["confidence"] == 0.6


def test_gap_and_loitering_have_no_event_level_confidence():
    """Confirms the negative: absence here is GFW's, not a mapping bug."""
    r = gfw_events.map_event(LIVE_GAP, "gaps")
    assert r["gfw_confidence_raw"] is None
    assert r["confidence"] is None


def test_port_visit_captures_all_three_anchorages():
    """3,000 port visits x 3 anchorages is a port gazetteer for the AOI."""
    r = gfw_events.map_event(LIVE_PORT_VISIT, "port_visits")
    assert r["start_anchorage_id"] == "ind-mundra"
    assert r["start_anchorage_name"] == "MUNDRA"
    assert r["start_anchorage_lat"] == 22.74
    assert r["start_anchorage_at_dock"] is True
    assert r["anchorage_id"] == "ind-vadinar"
    assert r["end_anchorage_name"] == "VADINAR"
    assert r["port_visit_id"] == "cc6a1dcf47308613d2cbe43685a4d297"


def test_anchorage_fields_are_present_even_when_absent():
    """Stable schema: a gap has no anchorages but must still have the columns,
    or Parquet partitions land with different schemas and the union breaks."""
    r = gfw_events.map_event(LIVE_GAP, "gaps")
    for f in ("start_anchorage_id", "anchorage_name", "end_anchorage_lat"):
        assert f in r and r[f] is None


def test_live_payloads_still_land_cleanly():
    from maritime_isr.ingest.landing import land_table, read_table

    rows = [gfw_events.map_event(LIVE_GAP, "gaps")]
    land_table(rows, table="gfw_ais_gaps", key_fields=("event_id",), day_field="start_time")
    got = read_table("gfw_ais_gaps")
    assert len(got) == 1
    assert got[0]["gfw_intentional_disabling"] is True
