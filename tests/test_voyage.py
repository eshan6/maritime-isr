"""Does what she declares match what she does? — ADR-035.

The clearest thing the Section-3 brief asked for that the system did not have,
and the reason was upstream of any rule: **nothing landed a declared
destination.** AIS message 5 carries it, the generator emitted no message 5, and
the live connector dropped every message that was not a position report. This
module tests the column, the connector, the rules and the corpus outcome, in
that order — because a detector with nothing to read is the failure mode the
whole exercise is about.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

UTC = timezone.utc
T0 = datetime(2026, 7, 1, tzinfo=UTC)


# ==========================================================================
# resolving what a transmitter actually sends
# ==========================================================================

def test_destination_resolves_names_aliases_and_route_notation():
    from maritime_isr.anomaly.voyage import resolve_destination as rd

    assert rd("JNPT") == "JNPT"
    assert rd("kandla") == "Kandla"
    assert rd("NHAVA SHEVA") == "JNPT"          # the same port, another name
    assert rd("INNSA") == "JNPT"                # UN/LOCODE
    assert rd("JNPT>>SIKKA") == "JNPT"          # route: the next port first
    assert rd("Mumbai VIA Kochi") == "Mumbai"


def test_destination_refuses_rather_than_guesses():
    """A wrong resolution is worse than none, and this is why there is no fuzzy
    matching.

    A missed resolution costs a finding. A wrong one tells a watchkeeper a ship
    is lying about a port we picked for her. "KANDLA" and "KANDIA" are one edit
    apart, and so are plenty of genuinely different places.
    """
    from maritime_isr.anomaly.voyage import resolve_destination as rd

    for text in ("", None, "SOMEWHERE", "KANDIA", "FOR ORDERS", "SEA",
                 "MUMBAI ANCH"):
        assert rd(text) is None, text


# ==========================================================================
# the arithmetic check
# ==========================================================================

def _feasible(lat, lon, dest, hours):
    from maritime_isr.anomaly.voyage import check_arrival_feasible
    return check_arrival_feasible(lat=lat, lon=lon, declared_at=T0,
                                  destination=dest,
                                  eta=T0 + timedelta(hours=hours))


def test_an_arrival_no_hull_could_make_is_a_contradiction():
    f = _feasible(14.6, 73.35, "Kandla", 9.0)     # ~1,100 km in nine hours
    assert f.is_contradiction
    assert f.detail["shortfall_hours"] > 6.0


def test_an_achievable_passage_is_not():
    f = _feasible(14.6, 73.35, "Kandla", 80.0)
    assert f.outcome == "ok"


def test_a_late_vessel_is_not_a_liar():
    """The defect this test exists for fired on 41 innocent hulls.

    Required speed is distance over *remaining* time, so as the remaining time
    goes to zero the required speed goes to infinity: a vessel an hour from her
    berth on a two-day-old ETA "needs 200 knots". She is late, which is the
    commonest thing at sea, and nobody retypes an ETA once it slips.
    """
    near = _feasible(22.0, 69.0, "Kandla", 0.5)     # 170 km out, 30 min left
    assert near.outcome == "ok"

    expired = _feasible(22.0, 69.0, "Kandla", -3.0)  # ETA passed three hours ago
    assert expired.outcome == "not_checkable", (
        "an expired ETA has stopped making a claim about the future")


def test_a_destination_we_cannot_place_is_not_checkable():
    f = _feasible(14.6, 73.35, "FOR ORDERS", 1.0)
    assert f.outcome == "not_checkable"
    assert not f.is_contradiction


# ==========================================================================
# the behavioural check
# ==========================================================================

def _fixes(start_lat, start_lon, dlat, dlon, n=24, step_h=1.0):
    return [(T0.timestamp() + i * step_h * 3600.0,
             start_lat + dlat * i, start_lon + dlon * i) for i in range(n)]


def test_steaming_away_from_the_declared_port_is_a_contradiction():
    from maritime_isr.anomaly.voyage import check_heading_agrees

    # Kandla is north-west of Karnataka; she goes south.
    f = check_heading_agrees(destination="Kandla",
                             fixes=_fixes(14.3, 73.75, -0.12, 0.04))
    assert f.is_contradiction
    assert f.detail["away_fraction"] >= 0.8


def test_heading_towards_it_is_not():
    from maritime_isr.anomaly.voyage import check_heading_agrees

    f = check_heading_agrees(destination="Kandla",
                             fixes=_fixes(14.3, 73.75, 0.30, -0.05))
    assert f.outcome == "ok"


def test_too_little_track_says_so_rather_than_guessing():
    from maritime_isr.anomaly.voyage import check_heading_agrees

    f = check_heading_agrees(destination="Kandla",
                             fixes=_fixes(14.3, 73.75, -0.12, 0.04, n=4,
                                          step_h=0.5))
    assert f.outcome == "not_checkable"


# ==========================================================================
# the connector — the path real data will take
# ==========================================================================

def test_the_connector_parses_message_five():
    """`ShipStaticData` was dropped on the floor before ADR-035.

    The live connector filtered to `PositionReport` and returned None for
    everything else, so the destination field never reached a table and the
    comparison the brief asks for could not be built at all.
    """
    from maritime_isr.ingest.aisstream import DropCounter, _parse_static

    msg = {
        "MessageType": "ShipStaticData",
        "MetaData": {"MMSI": 419123456, "latitude": 18.9, "longitude": 72.8,
                     "time_utc": "2026-07-01T00:00:00Z"},
        "Message": {"ShipStaticData": {
            "ImoNumber": 9074729, "Destination": "  NHAVA SHEVA  ",
            "MaximumStaticDraught": 12.4, "Type": 70,
            "Eta": {"Month": 7, "Day": 3, "Hour": 6, "Minute": 30}}},
    }
    row = _parse_static(msg, DropCounter())
    assert row is not None
    assert row["mmsi"] == 419123456
    assert row["destination"] == "NHAVA SHEVA", "kept as broadcast, not cleaned"
    assert row["eta"] == datetime(2026, 7, 3, 6, 30, tzinfo=UTC)
    assert row["h3_r7"] and row["h3_r9"], "a located record carries its cells"


def test_an_unset_eta_is_an_absence_and_not_a_zero():
    """Message 5 encodes "not available" as 0/24/60, and those are silences.

    A vessel that declined to state an ETA has said something different from one
    that stated a wrong one. Collapsing the two would make the arrival check
    fire on silence.
    """
    from maritime_isr.ingest.aisstream import _parse_eta

    assert _parse_eta(None, T0) is None
    assert _parse_eta({"Month": 0, "Day": 0, "Hour": 24, "Minute": 60}, T0) is None
    assert _parse_eta({"Month": 2, "Day": 31, "Hour": 6, "Minute": 0}, T0) is None


def test_an_eta_without_a_year_resolves_across_the_boundary():
    """Message 5 has month/day/hour/minute and no year, so one is inferred."""
    from maritime_isr.ingest.aisstream import _parse_eta

    december = datetime(2026, 12, 20, tzinfo=UTC)
    got = _parse_eta({"Month": 1, "Day": 4, "Hour": 12, "Minute": 0}, december)
    assert got == datetime(2027, 1, 4, 12, 0, tzinfo=UTC), (
        "a ship declaring 04 January in December means next year")


# ==========================================================================
# the corpus
# ==========================================================================

@pytest.fixture(scope="module")
def declarations():
    from maritime_isr.api.reader import open_reader
    with open_reader() as r:
        if not r.has("ais_voyage"):
            pytest.skip("no landed ais_voyage — run scenario generate")
        return r.rows("SELECT * FROM ais_voyage")


def test_the_corpus_carries_a_large_honest_population(declarations):
    """Without one, a contradiction has no denominator.

    A voyage rule tested only against liars measures recall and says nothing
    about precision, which is the binding constraint (ADR-004).
    """
    from maritime_isr.anomaly.voyage import resolve_destination

    hulls = {d["vessel_id"] for d in declarations}
    assert len(declarations) > 500 and len(hulls) > 50, (
        f"only {len(declarations)} declarations over {len(hulls)} hulls")
    placed = [d for d in declarations if resolve_destination(d["destination"])]
    assert len(placed) / len(declarations) > 0.9, (
        "most declared destinations should resolve to a port we hold")


def test_the_two_liars_are_caught_and_the_diverted_hull_is_not(declarations):
    """F13 and F14 fire; F15 is diverted honestly and must stay quiet."""
    from maritime_isr.anomaly.voyage import check_arrival_feasible

    def worst_eta_finding(key):
        mine = [d for d in declarations if d["vessel_id"] == f"vessel:{key}"]
        if not mine:
            pytest.skip(f"vessel:{key} not in this corpus")
        out = []
        for d in mine:
            import pandas as pd
            t = pd.Timestamp(d["timestamp"], tz="UTC") if not hasattr(
                d["timestamp"], "tzinfo") else d["timestamp"]
            eta = d["eta"]
            out.append(check_arrival_feasible(
                lat=d["lat"], lon=d["lon"], declared_at=t,
                destination=d["destination"], eta=eta))
        return out

    assert any(f.is_contradiction for f in worst_eta_finding("impossible_eta")), (
        "F14 declares an arrival no hull could make")
    assert not any(f.is_contradiction
                   for f in worst_eta_finding("diverted_honestly")), (
        "F15's arrival times are all achievable; only her port changed")


def test_the_detector_reads_timestamps_at_the_right_scale():
    """A `timestamp[us]` column divided as if it were nanoseconds.

    The naive `astype("int64") // 1e9` compresses time by a factor of a
    thousand. Nineteen hours of track came out as sixty-eight seconds, so the
    heading check answered "not enough track to say which way she went" about
    the one hull in the corpus written to steam the wrong way — a detector
    reporting a clean picture it had not looked at.

    `tracks.kalman.epoch_s` exists because this exact bug atomised every track
    once before. This test is here so the third time is caught by a machine.
    """
    import numpy as np
    import pandas as pd

    from maritime_isr.tracks.kalman import epoch_s

    start = pd.Timestamp("2026-07-01T00:00:00Z")
    for unit in ("ns", "us"):
        ts = pd.Series(pd.date_range(start, periods=20, freq="1h")
                       ).dt.tz_convert("UTC").astype(f"datetime64[{unit}, UTC]")
        span_h = (epoch_s(ts)[-1] - epoch_s(ts)[0]) / 3600.0
        assert np.isclose(span_h, 19.0), (
            f"{unit} column produced a span of {span_h:.3f} h, not 19")


def test_a_vessel_at_anchor_is_not_steaming_away():
    """The defect this test exists for fired on eleven honest hulls.

    A ship at anchor yaws through most of the compass over a tide, so every
    step is "more than 100 degrees off the bearing to the port" and the away
    fraction comes out at a perfect 1.0 — on a question that should never have
    been asked. Every one of the eleven was swinging on her cable in an
    anchorage while still broadcasting the destination she was waiting to enter.
    """
    import math

    from maritime_isr.anomaly.voyage import check_heading_agrees

    # 24 hours of yawing inside a 2 km circle off Sikka, going nowhere.
    swing = [(T0.timestamp() + i * 3600.0,
              22.50 + 0.01 * math.sin(i * 1.3),
              69.74 + 0.01 * math.cos(i * 1.3)) for i in range(24)]
    f = check_heading_agrees(destination="Sikka", fixes=swing)
    assert f.outcome == "not_checkable", (
        f"a vessel at anchor was called a liar: {f.statement}")
    assert f.detail["mean_kn"] < 3.0
