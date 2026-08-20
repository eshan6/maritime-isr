"""Coastal radar as a source — ADR-028.

**Every test here exercises a code path.** The brief this work was built to says
so explicitly, and this repository has already paid for the alternative: six
defects once passed green because the tests asserted files existed and nothing
ever called them. So there is no test below that checks a module imports, a
constant is set, or a function is defined. Each one runs the thing and asserts
something about what came out.

The suite is organised around the three claims the build makes:

  1. **A new sensor is a connector, not a rewrite.** The track engine, the
     encounter detector, the feature extractor, the association engine and the
     dark cascade all run over radar-sourced tracks — the same functions, called
     with a different descriptor.
  2. **The core stopped assuming identity.** The four places where it did are
     each pinned by a test that fails if the assumption comes back.
  3. **A radar track nothing explains becomes a dark contact**, and one that
     something does explain does not.
"""
from __future__ import annotations


import numpy as np
import pandas as pd
import pytest

from maritime_isr.config import (RADAR_NEIGHBOURHOOD_RES, TRACK_BREAK_DAYS)
from maritime_isr.fusion.associate import associate_scene
from maritime_isr.fusion.dark import build_static_layer, dark_cascade
from maritime_isr.fusion.radar_ais import correlate_radar
from maritime_isr.ingest import radar as radar_conn
from maritime_isr.schemas.sources import AIS, RADAR, source_by_name
from maritime_isr.tracks import build_tracks
from maritime_isr.tracks.coverage import CoverageModel, classify_gaps
from maritime_isr.tracks.features import detect_encounters, extract_features

T0 = pd.Timestamp("2026-06-01 00:00:00", tz="UTC")


# --------------------------------------------------------------------------
# fixtures — small synthetic pictures, built here so the tests are fast and
# do not depend on the corpus having been generated
# --------------------------------------------------------------------------

def _radar_rows(track_id, n=40, step_s=300, lat0=21.6, lon0=69.4,
                dlat=0.004, dlon=0.004, station="SYN-POR", length=140.0,
                t_start=T0, sigma=60.0, sog=11.0, cog=45.0):
    """One clean radar track, in the conformed `radar_track_report` shape."""
    rows = []
    for i in range(n):
        rows.append(dict(
            report_id=f"{station}:{int(t_start.timestamp())}:{i}",
            station_id=station,
            radar_track_id=track_id,
            ts=t_start + pd.Timedelta(seconds=i * step_s),
            lat=lat0 + i * dlat, lon=lon0 + i * dlon,
            sog_kn=sog, cog_deg=cog, range_km=20.0, bearing_deg=200.0,
            position_sigma_m=sigma,
            rcs_dbsm=radar_conn.rcs_dbsm_from_length(length),
            length_est_m=length, snr_db=14.0, track_quality=80))
    return pd.DataFrame(rows)


def _ais_rows(mmsi, n=40, step_s=300, lat0=21.6, lon0=69.4, dlat=0.004,
              dlon=0.004, t_start=T0, receiver="ter:test", sog=11.0):
    """An AIS track following the same path — the correlatable case."""
    return pd.DataFrame([dict(
        mmsi=mmsi, lat=lat0 + i * dlat, lon=lon0 + i * dlon, sog_kn=sog,
        cog_deg=45.0, heading_deg=45.0, nav_status=0, msg_type=1,
        ts=t_start + pd.Timedelta(seconds=i * step_s), receiver=receiver,
        n_receipts=1) for i in range(n)])


def _cov(df):
    return CoverageModel(df["ts"].min().timestamp()).fit(df)


# ==========================================================================
# 1. the connector
# ==========================================================================

def test_connector_conforms_and_derives_what_the_feed_omits():
    """A feed record with only geometry still lands a complete canonical row."""
    row = radar_conn.conform_plot(dict(
        station_id="SYN-MUM", radar_track_id="SYN-MUM:0042",
        ts="2026-06-10T04:00:00Z", lat=18.6, lon=72.4, sog_kn=11.2,
        cog_deg=190.0, range_km=32.0, bearing_deg=210.0,
        rcs_dbsm=radar_conn.rcs_dbsm_from_length(140.0)))
    # position accuracy derived from range, length derived from cross-section
    assert row["position_sigma_m"] == pytest.approx(
        radar_conn.position_sigma_m(32.0))
    assert row["length_est_m"] == pytest.approx(140.0, rel=1e-6)
    assert row["ts"].tzinfo is not None


def test_connector_refuses_an_unnamespaced_track_number():
    """Two stations both numbering from 1 must not merge into one track."""
    with pytest.raises(Exception):
        radar_conn.conform_plot(dict(
            station_id="SYN-MUM", radar_track_id="7",
            ts="2026-06-10T04:00:00Z", lat=18.6, lon=72.4))


def test_position_accuracy_degrades_with_range():
    """The cross-range term is what makes the picture worse further out."""
    near, far = (radar_conn.position_sigma_m(r) for r in (10.0, 50.0))
    assert far > near * 1.8, (near, far)


def test_rcs_length_round_trips():
    """The simulator's forward model and the connector's inverse must agree, so
    a bug in one is a bug in both and cannot flatter the size gate."""
    for length in (12.0, 40.0, 120.0, 330.0):
        db = radar_conn.rcs_dbsm_from_length(length)
        assert radar_conn.length_m_from_rcs_dbsm(db) == pytest.approx(
            length, rel=1e-9)


def test_landing_stamps_provenance_and_h3(tmp_path, monkeypatch):
    from maritime_isr.config import cfg
    monkeypatch.setattr(cfg, "data_root", tmp_path, raising=False)
    df = _radar_rows("SYN-POR:0001", n=4)
    written = radar_conn.land_plots(df.to_dict("records"),
                                    source_id="synthetic-scenario",
                                    is_synthetic=True)
    assert sum(written.values()) == 4
    from maritime_isr.ingest.landing import read_table
    rows = read_table(radar_conn.TABLE)
    assert len(rows) == 4
    for r in rows:
        assert r["source_id"] == "synthetic-scenario" and r["is_synthetic"]
        assert r["h3_r7"] and r["h3_r9"], "H3 must be stamped at ingest"
        assert r["acquired_at"] is not None and r["pipeline_version"]


# ==========================================================================
# 2. the core stopped assuming identity
#
# One test per place it did. Each fails if the assumption returns.
# ==========================================================================

def test_track_engine_groups_on_the_source_key_and_carries_no_mmsi():
    tracks, spoofs = build_tracks(_radar_rows("SYN-POR:0001"), source=RADAR)
    assert tracks, "the track engine produced nothing from radar reports"
    tr = tracks[0]
    assert tr.source is RADAR
    assert tr.track_key == "SYN-POR:0001"
    assert tr.mmsi is None, "a radar track must not claim an MMSI"
    assert tr.has_identity is False
    assert not spoofs, "an identity-less key cannot produce a spoofing tell"


def test_duplicate_track_number_is_not_a_spoofing_tell():
    """Two targets sharing a recycled station track number is housekeeping.

    The same shape under one MMSI *is* a spoofing tell, and the AIS branch below
    proves the behaviour is still there for the source that means it.
    """
    a = _radar_rows("SYN-POR:0007", n=20, lat0=21.6, lon0=69.4)
    b = _radar_rows("SYN-POR:0007", n=20, lat0=21.9, lon0=69.9)
    _tracks, spoofs = build_tracks(pd.concat([a, b]), source=RADAR)
    assert not spoofs, f"radar produced identity events: {spoofs}"

    a2 = _ais_rows(123456789, n=20, lat0=21.6, lon0=69.4)
    b2 = _ais_rows(123456789, n=20, lat0=21.9, lon0=69.9)
    _t2, spoofs2 = build_tracks(pd.concat([a2, b2]), source=AIS)
    assert any(s["event_type"] == "DUPLICATE_MMSI" for s in spoofs2), (
        "the AIS spoof rule must be unchanged — this is the control")


def test_reuse_guard_follows_the_sensor():
    """A station recycles a track number in minutes; an MMSI belongs to a hull.

    Applying AIS's seven days to radar merged two hundred targets into single
    twelve-thousand-plot tracks (ADR-028).
    """
    assert AIS.track_break_s == TRACK_BREAK_DAYS * 86400
    assert RADAR.track_break_s < 3600, RADAR.track_break_s

    early = _radar_rows("SYN-POR:0009", n=6, t_start=T0)
    later = _radar_rows("SYN-POR:0009", n=6, lat0=21.6, lon0=69.4,
                        t_start=T0 + pd.Timedelta(hours=6))
    tracks, _ = build_tracks(pd.concat([early, later]), source=RADAR)
    assert len(tracks) == 2, (
        "a six-hour silence on a recycled track number must split the track, "
        f"got {len(tracks)}")
    assert any(t.fragmented_from for t in tracks), "lineage must be recorded"


def test_gap_classifier_refuses_a_sensor_that_hears_nothing():
    """Labelling a radar dropout INTENTIONAL_SILENCE would convict a vessel on
    evidence about our own receiver."""
    df = _radar_rows("SYN-POR:0001")
    tracks, _ = build_tracks(df, source=RADAR)
    model = _cov(_ais_rows(111111111))
    with pytest.raises(ValueError, match="BROADCAST"):
        classify_gaps(tracks[0], model)


def test_encounter_detector_fires_between_two_radar_tracks():
    """The `mmsi_a == mmsi_b` self-pair guard was `None == None` on radar, so
    every radar-to-radar pair was discarded and the detector could not fire at
    all. This is the regression: two contacts alongside must be an encounter."""
    a = _radar_rows("SYN-VEN:0001", n=24, lat0=15.900, lon0=73.500,
                    dlat=0.0, dlon=0.0, sog=0.4)
    b = _radar_rows("SYN-VEN:0002", n=24, lat0=15.9022, lon0=73.500,
                    dlat=0.0, dlon=0.0, sog=0.4)
    tracks, _ = build_tracks(pd.concat([a, b]), source=RADAR)
    assert len(tracks) == 2
    encs = detect_encounters(tracks)
    assert encs, "the encounter detector produced nothing from radar tracks"
    e = encs[0]
    assert e["mmsi_a"] is None and e["mmsi_b"] is None
    assert e["track_source"] == "radar"
    assert e["min_distance_m"] < 500.0


def test_alerts_land_on_a_contact_node_not_an_invented_hull(tmp_path):
    """`resolve_mmsi(store, None)` would mint `vessel:mmsi:None` — a node that
    resolves, passes a presence check, and is a different fiction per track."""
    from maritime_isr.graph import GraphStore, track_subject_id

    tracks, _ = build_tracks(_radar_rows("SYN-POR:0001"), source=RADAR)
    store = GraphStore(tmp_path / "g.sqlite")
    try:
        nid = track_subject_id(store, tracks[0], at=T0.timestamp())
        assert nid.startswith("contact:radar:"), nid
        node = store.node(nid)
        assert node is not None and node["node_type"] == "contact"
        assert node["props"]["named"] is False

        # ...and an AIS track still resolves to a hull, unchanged.
        ais, _ = build_tracks(_ais_rows(987654321), source=AIS)
        vid = track_subject_id(store, ais[0], at=T0.timestamp())
        assert vid.startswith("vessel:"), vid
    finally:
        store.close()


# ==========================================================================
# 3. every behavioural detector runs over radar-sourced tracks
#
# The brief asks for a test that proves the detector RAN, not that the file
# exists. Each of these asserts on output that could only come from having
# executed over a radar track.
# ==========================================================================

def _loitering_radar_tracks():
    """A contact holding station inside the Mumbai High sensitive zone."""
    return build_tracks(_radar_rows(
        "SYN-MUM:0100", n=60, step_s=300, lat0=19.30, lon0=71.30,
        dlat=0.0, dlon=0.0, sog=0.4, station="SYN-MUM"), source=RADAR)[0]


def test_feature_extraction_runs_over_a_radar_track():
    f = extract_features(_loitering_radar_tracks()[0])
    assert f["track_source"] == "radar"
    assert f["mmsi"] is None and f["track_key"]
    assert f["n_loiter_episodes"] >= 1, (
        "a five-hour station-keep produced no loiter episode")


def test_sensitive_loitering_detector_fires_on_a_radar_track(tmp_path):
    from maritime_isr.anomaly.library import detect_sensitive_loitering
    from maritime_isr.graph import GraphStore

    store = GraphStore(tmp_path / "g.sqlite")
    try:
        fired = detect_sensitive_loitering(store, _loitering_radar_tracks(),
                                           source_ref="test")
        assert fired, "loitering_sensitive produced no alert on radar tracks"
        alert = store.alerts()[0]
        assert alert["subject"].startswith("contact:radar:")
        assert alert["props"]["sensor"] == "radar"
        # the evidence chain must name the sensor, or an analyst cannot tell
        # a radar loiter from an AIS one
        assert alert["evidence"][0]["props"]["sensor"] == "radar"
    finally:
        store.close()


def test_port_risk_detector_runs_over_a_radar_track(tmp_path):
    from maritime_isr.anomaly.library import detect_port_risk
    from maritime_isr.graph import GraphStore
    from maritime_isr.ports import PORTS

    lat, lon = PORTS["Karachi"]
    tracks, _ = build_tracks(_radar_rows(
        "SYN-JAK:0004", n=30, lat0=lat, lon0=lon, dlat=0.0, dlon=0.0,
        sog=0.5, station="SYN-JAK"), source=RADAR)
    store = GraphStore(tmp_path / "g.sqlite")
    try:
        fired = detect_port_risk(store, tracks, source_ref="test")
        assert fired, "port_risk_propagation produced no alert on radar tracks"
        a = store.alerts()[0]
        assert a["subject"].startswith("contact:radar:")
        assert a["props"]["sensor"] == "radar"
    finally:
        store.close()


# ==========================================================================
# 4. correlation and the dark cascade
# ==========================================================================

def test_a_radar_track_with_a_matching_ais_track_correlates():
    rad, _ = build_tracks(_radar_rows("SYN-POR:0001", n=40), source=RADAR)
    ais_df = _ais_rows(999000001, n=40)
    ais, _ = build_tracks(ais_df, source=AIS)
    out = correlate_radar(rad, ais, _cov(ais_df), {999000001: 140.0})
    c = out.correlations[0]
    assert c["status"] == "correlated", c
    assert c["mmsi"] == 999000001
    assert c["support"] > 0.8, c["support"]
    assert not [v for v in out.verdicts if v["status"] == "dark_candidate"], (
        "a correlated track must not produce a dark contact")


def test_a_radar_track_with_nothing_on_ais_becomes_a_dark_contact():
    """The headline claim, end to end through the real cascade."""
    rad, _ = build_tracks(_radar_rows("SYN-POR:0001", n=40), source=RADAR)
    # Another vessel on a parallel course ~11 km away. Near enough to share the
    # coverage model's res-4 cell — so `hearable` has evidence this region is
    # heard at all, and the cascade is not suppressing on coverage for a reason
    # unrelated to the test — and far enough that the association gate cannot
    # confuse the two.
    other = _ais_rows(999000002, n=40, lat0=21.70, lon0=69.50)
    ais, _ = build_tracks(other, source=AIS)
    out = correlate_radar(rad, ais, _cov(other), {})
    c = out.correlations[0]
    assert c["status"] == "dark", c
    assert c["support"] == 0.0
    darks = [v for v in out.verdicts if v["status"] == "dark_candidate"]
    assert darks, f"no dark contact; verdicts were {[v['status'] for v in out.verdicts]}"
    d = darks[0]
    assert d["length_m"] == pytest.approx(140.0, rel=0.2)
    assert d["dark_score"] > 0.5


def test_every_suppression_is_a_recorded_verdict():
    """'Why is this NOT dark' must be answerable from the store."""
    rad, _ = build_tracks(_radar_rows("SYN-POR:0001", n=8), source=RADAR)
    other = _ais_rows(999000002, n=40, lat0=21.9, lon0=69.9)
    ais, _ = build_tracks(other, source=AIS)
    out = correlate_radar(rad, ais, _cov(other), {})
    # an eight-plot track is 35 minutes — below the two-hour persistence gate
    assert out.verdicts, "a suppressed contact must still produce a verdict row"
    assert out.verdicts[0]["status"] == "suppressed_transient", out.verdicts[0]


def test_the_static_layer_absorbs_a_fixed_installation():
    """A mooring reported for weeks must become an object, and a lane must not.

    The layer has shipped since Phase 3 and never had an input: the SAR corpus
    holds six contacts in total. This is the first time it is exercised.
    """
    from maritime_isr.config import (RADAR_STATIC_MIN_SCENES,
                                     RADAR_STATIC_RADIUS_M, RADAR_STATIC_RES)
    rng = np.random.default_rng(7)
    mooring, lane = [], []
    for day in range(30):
        for k in range(4):
            t = T0 + pd.Timedelta(days=day, hours=6 * k)
            # the mooring: same spot, every day, with radar-grade scatter
            mooring.append(dict(
                detection_id=f"m{day}-{k}", scene_id=f"radar-day-{day:02d}",
                ts=t, lat=22.410 + rng.normal(0, 0.0009),
                lon=69.610 + rng.normal(0, 0.0009), length_m=90.0))
        if day % 9 == 0:                       # a ship on a lane, now and then
            lane.append(dict(
                detection_id=f"l{day}", scene_id=f"radar-day-{day:02d}",
                ts=T0 + pd.Timedelta(days=day), lat=22.100, lon=68.600,
                length_m=200.0))
    objs = build_static_layer(mooring + lane,
                              radius_m=RADAR_STATIC_RADIUS_M,
                              res=RADAR_STATIC_RES,
                              min_scenes=RADAR_STATIC_MIN_SCENES)
    assert len(objs) == 1, [(o["lat"], o["lon"], o["n_scenes"]) for o in objs]
    assert objs[0]["lat"] == pytest.approx(22.410, abs=0.002)


def test_a_contact_beside_a_broadcaster_is_not_dark():
    """The product's sentence, enforced: 'nothing is broadcasting there'.

    An anchored merchant lands one AIS receipt every fifty minutes and is seen
    by radar every five, so she stops *associating* while plainly still
    transmitting. Twelve of fifteen false positives on the full picture were
    exactly this.
    """
    # Coverage evidence in the same region, so the cascade reaches the gate
    # under test rather than stopping at `suppressed_coverage`. `tracks` is the
    # AIS picture — that is what the isolation term measures against.
    ais_df = _ais_rows(999000009, n=40, lat0=21.70, lon0=69.50)
    model = _cov(ais_df)
    tracks, _ = build_tracks(ais_df, source=AIS)
    base = dict(detection_id="d1", scene_id="radar:SYN-POR", ts=T0,
                lat=21.6, lon=69.4, length_m=140.0, n_looks=12, score=0.85)
    lone = dark_cascade([dict(base, excess_contacts=1)], model, [], tracks,
                        require_excess_contacts=True)
    beside = dark_cascade([dict(base, excess_contacts=0)], model, [], tracks,
                          require_excess_contacts=True)
    assert lone[0]["status"] == "dark_candidate", lone[0]
    assert beside[0]["status"] == "suppressed_not_isolated", beside[0]
    # ...and the gate is off by default, so the SAR path is untouched
    sar = dark_cascade([dict(base, excess_contacts=0)], model, [], tracks)
    assert sar[0]["status"] == "dark_candidate", sar[0]


def test_association_declines_a_hypothesis_compatible_with_half_the_ocean():
    """The volume-normalisation defect: a stale track used to explain anything.

    A track whose last report is hours old has an uncertainty cone kilometres
    wide. Scoring the position term against that cone made the match *more*
    confident the less was known — measured matches at 36, 61, 77, 131 and
    187 km. The floor must win instead.
    """
    ais_df = _ais_rows(999000003, n=20, step_s=180)     # ends at T0 + 57 min
    tracks, _ = build_tracks(ais_df, source=AIS)
    scene = dict(scene_id="s", ts=str(T0 + pd.Timedelta(hours=8)),
                 detections=[dict(detection_id="d0", lat=22.6, lon=70.4,
                                  length_m=140.0, score=0.9)])
    out = associate_scene(scene, tracks, {})
    assert out[0]["status"] == "unmatched", out[0]


def test_the_census_uses_the_shared_h3_grid():
    """The contacts-vs-broadcasters count is a hash join on the project grid,
    not a distance sweep — CLAUDE.md §3."""
    from maritime_isr import h3util
    a = h3util.cell(21.6, 69.4, RADAR_NEIGHBOURHOOD_RES)
    b = h3util.cell(21.6001, 69.4001, RADAR_NEIGHBOURHOOD_RES)
    assert a == b, "neighbouring positions must share a census cell"
    # `neighbors` is an H3 disk: the centre cell plus its six neighbours.
    ring = h3util.neighbors(a, 1)
    assert len(ring) == 7 and a in ring, ring


# ==========================================================================
# 5. the simulated network
# ==========================================================================

def test_detection_range_falls_off_with_target_size():
    """Coverage that is uniform would flatter the system: everything
    unexplained would be dark, and the hard question never asked."""
    from maritime_isr.scenario.radar_network import (p_detect, snr_db,
                                                     rcs_dbsm_from_length)
    small = p_detect(snr_db(rcs_dbsm_from_length(12.0), 25.0))
    large = p_detect(snr_db(rcs_dbsm_from_length(300.0), 25.0))
    assert small < 0.1 < 0.9 < large, (small, large)


def test_shadow_sectors_and_outages_make_holes():
    from maritime_isr.scenario.radar_network import STATIONS, STATIONS_BY_ID
    assert any(s.shadow_sectors for s in STATIONS), (
        "a network with no terrain shadow is not a coverage model")
    st = STATIONS_BY_ID["SYN-MUM"]
    assert st.shadowed(0.0) or st.shadowed(45.0), (
        "Prongs Reef looks west; the city is behind it")
    assert not st.shadowed(250.0)


def test_every_station_is_inside_the_aoi():
    from maritime_isr.config import AOI_V1
    from maritime_isr.scenario.radar_network import STATIONS
    for s in STATIONS:
        assert AOI_V1.contains(s.lat, s.lon), s


def test_the_radar_picture_derives_from_the_same_vessel_truth():
    """One vessel truth, two sensors. A plot must sit on the hull's integrated
    position, not on an independently invented one."""
    from maritime_isr.scenario.geography import haversine_m
    from maritime_isr.scenario.primitives.track import point_at
    from maritime_isr.scenario.profile import CorpusProfile
    from maritime_isr.scenario.radar import generate_radar_picture
    from maritime_isr.scenario.world import ScenarioWorld
    from maritime_isr.scenario.cast import build_cast
    from maritime_isr.scenario.scenarios import group_r

    w = ScenarioWorld.new(7, CorpusProfile.load())
    build_cast(w)
    group_r.r1_coastal_dark_runner(w)
    rep = generate_radar_picture(w)
    plots = [p for p in rep.plots if p.truth_entity_id == "vessel:coast_runner"]
    assert plots, "the coastal dark runner produced no radar plots"
    truth = w.track_of("vessel:coast_runner")
    worst = 0.0
    for p in plots[:200]:
        tp = point_at(truth, p.ts)
        worst = max(worst, haversine_m(p.lat, p.lon, tp.lat, tp.lon))
    assert worst < 2000.0, (
        f"a plot sat {worst:.0f} m from the vessel's true position — the two "
        f"sensors are no longer describing one ship")


def test_the_truth_ledger_refuses_to_ask_for_the_impossible():
    """An episode explainable by AIS coverage must not be expected to fire, and
    a naval unit entitled to be dark must not either."""
    from maritime_isr.scenario.radar_truth import (CAUSE_OUT_OF_RECEPTION,
                                                   RadarDarkEpisode)
    kw = dict(episode_id="e", entity_id="vessel:x",
              t_start=T0.to_pydatetime(),
              t_end=(T0 + pd.Timedelta(hours=2)).to_pydatetime(),
              lat=21.0, lon=69.0, lat_min=21.0, lat_max=21.1,
              lon_min=69.0, lon_max=69.1, length_m=100.0,
              cause=CAUSE_OUT_OF_RECEPTION, n_plots=10, duration_min=120.0,
              station_ids="SYN-POR")
    with pytest.raises(ValueError, match="out-of-coverage"):
        RadarDarkEpisode(**kw, explainable_by_coverage=True,
                         expected_detection=True)
    with pytest.raises(ValueError, match="naval"):
        RadarDarkEpisode(**kw, unavoidable_false_positive=True,
                         expected_detection=True)


def test_source_descriptor_refuses_an_incoherent_sensor():
    from maritime_isr.schemas.sources import TrackSource
    with pytest.raises(ValueError, match="must be an identity"):
        TrackSource(name="bad", key_field="k", key_is_identity=False,
                    observes_transmission=True, position_sigma_m=1.0,
                    carries_size_estimate=False)
    assert source_by_name("radar") is RADAR
    with pytest.raises(ValueError, match="unknown track source"):
        source_by_name("lidar")


def test_dark_rendezvous_fires_on_two_radar_contacts(tmp_path):
    """The Build 1 acceptance item: *every* behavioural detector runs on radar.

    This one could not, and the reason was structural rather than a threshold.
    `detect_dark_rendezvous` reads encounters and associations; the pipeline
    handed it AIS encounters only, so radar meetings were computed, printed and
    then dropped. Its silence read as "radar found no rendezvous" when it had
    never been asked.

    Two contacts alongside, one of them explained by AIS and one not, is the
    ship-to-ship signature — and neither party has an MMSI, so the subject has
    to resolve to contact nodes rather than to a vessel meeting itself.
    """
    from maritime_isr.anomaly.library import detect_dark_rendezvous
    from maritime_isr.graph import GraphStore

    a = _radar_rows("SYN-VEN:0001", n=24, lat0=15.900, lon0=73.500,
                    dlat=0.0, dlon=0.0, sog=0.4)
    b = _radar_rows("SYN-VEN:0002", n=24, lat0=15.9022, lon0=73.500,
                    dlat=0.0, dlon=0.0, sog=0.4)
    tracks, _ = build_tracks(pd.concat([a, b]), source=RADAR)
    encs = detect_encounters(tracks)
    assert encs, "fixture problem: no radar encounter to reason about"

    # An unmatched contact inside the encounter footprint — what the rule reads
    # as "one party here is explaining nothing".
    assoc = [dict(status="unmatched", ts=T0 + pd.Timedelta(minutes=30),
                  lat=15.9011, lon=73.500, in_ais_gap=False,
                  detection_id="d0", track_id=None)]
    store = GraphStore(tmp_path / "g.sqlite")
    try:
        fired = detect_dark_rendezvous(store, encs, assoc, source_ref="test")
        assert fired, "dark_rendezvous produced nothing from radar encounters"
        al = store.alerts()[0]
        assert al["subject"].startswith("contact:radar:"), al["subject"]
        assert al["props"]["counterpart"].startswith("contact:radar:")
        assert al["subject"] != al["props"]["counterpart"], (
            "both parties resolved to the same node — a vessel meeting itself")
        # the node must EXIST, not merely be named
        assert store.node(al["subject"]) is not None
    finally:
        store.close()


def test_dark_rendezvous_ignores_an_unmatched_contact_on_the_other_coast(tmp_path):
    """The locality repair, stated as a test rather than as a threshold.

    `gap_party` used to be computed over every unmatched association in the AOI
    within twelve hours, with no distance test at all — so the rule asked "was
    anything, anywhere, dark today?" and fired on every meeting in the picture.
    It went unnoticed while SAR supplied six unmatched contacts in total; coastal
    radar supplies tens of thousands, and seed 7 produced 667 alerts.

    Same encounter as the test above, same twelve-hour window, one difference:
    the silent contact is 300 km away off the other side of the peninsula. There
    is no evidence any party to THIS meeting was dark, so there is no finding.
    """
    from maritime_isr.anomaly.library import detect_dark_rendezvous
    from maritime_isr.graph import GraphStore

    a = _radar_rows("SYN-VEN:0001", n=24, lat0=15.900, lon0=73.500,
                    dlat=0.0, dlon=0.0, sog=0.4)
    b = _radar_rows("SYN-VEN:0002", n=24, lat0=15.9022, lon0=73.500,
                    dlat=0.0, dlon=0.0, sog=0.4)
    tracks, _ = build_tracks(pd.concat([a, b]), source=RADAR)
    encs = detect_encounters(tracks)
    assert encs, "fixture problem: no radar encounter to reason about"

    far = [dict(status="unmatched", ts=T0 + pd.Timedelta(minutes=30),
                lat=18.60, lon=72.60, in_ais_gap=True,
                detection_id="d-far", track_id=None)]
    store = GraphStore(tmp_path / "g.sqlite")
    try:
        assert detect_dark_rendezvous(store, encs, far, source_ref="test") == []
        assert store.alerts() == []
    finally:
        store.close()


def test_an_ambiguous_track_reaches_the_cascade_and_is_judged_there():
    """A partly-explained track must produce a verdict, not an absence.

    The gate into the cascade read `("dark", "correlated_then_dark")`, so a
    track whose support fell between the two thresholds was dropped before any
    filter saw it. Measured on seed 7, two of the seven findable episodes had no
    verdict row anywhere in the store — indistinguishable, from the outside,
    from episodes that were never in the picture.

    The fixture is the literal meaning of the word: a target held stationary for
    three hours, with TWO broadcasters passing through it. The first sits on it
    for the opening three epochs and then leaves north; the second arrives,
    holds for three epochs, and leaves east; the last six epochs nothing is
    anywhere near. Neither hull wins enough of the track to be the answer and
    both win enough to be a candidate — which is the state `ambiguous` names,
    and the state the gate used to discard.

    Note the two competitors are what keeps it out of the `correlated_then_dark`
    branch too: the lead is split evenly, so no single AIS track explains most
    of the run-up, and the transition story is correctly not told.

    The claim under test is that a verdict EXISTS and can be traced back to its
    track, not that the verdict is any particular one. Which way the cascade
    decides is the cascade's business and is covered by its own tests; being
    asked at all is what regressed.
    """
    # A stationary radar target: 36 fixes over three hours in one place.
    rad, _ = build_tracks(
        _radar_rows("SYN-POR:0001", n=36, dlat=0.0, dlon=0.0, sog=0.3),
        source=RADAR)

    # First broadcaster: on the target for fixes 0-8, then away north.
    a = _ais_rows(999000003, n=36, dlat=0.0, dlon=0.0, sog=0.3)
    a.loc[9:, "lat"] = [21.6 + 0.01 * (i - 8) for i in range(9, 36)]
    # Second: closing from the east, on the target for fixes 9-17, then away.
    b = _ais_rows(999000004, n=36, dlat=0.0, dlon=0.0, sog=6.0)
    b.loc[:8, "lon"] = [69.50 - 0.01 * i for i in range(9)]
    b.loc[9:17, "lon"] = 69.40
    b.loc[18:, "lon"] = [69.40 + 0.01 * (i - 17) for i in range(18, 36)]
    both = pd.concat([a, b], ignore_index=True)
    ais, _ = build_tracks(both, source=AIS)
    out = correlate_radar(rad, ais, _cov(both),
                          {999000003: 140.0, 999000004: 140.0})

    c = out.correlations[0]
    assert c["status"] == "ambiguous", (c["status"], c["support"])
    assert 0.20 <= c["support"] < 0.55, c["support"]
    assert out.verdicts, (
        "an ambiguous track with a dark run produced no verdict at all — the "
        "cascade never saw it, so 'why is this not dark' has no answer")
    v = out.verdicts[0]
    assert v["status"], "a verdict with no status is not an answer"
    assert v["correlation_id"] == c["correlation_id"], (
        "the verdict cannot be traced back to the radar track that produced it")


# ==========================================================================
# 10. the serving layer — a watchkeeper has to be able to SEE this
# ==========================================================================
#
# The radar path was CLI-only through its first merge: the correlation ran, the
# verdicts landed, and the only way to read either was a terminal. These tests
# drive the real FastAPI app through its real routes, because an endpoint that
# is only known to import is not an endpoint anybody can use.

def _client():
    from fastapi.testclient import TestClient

    from maritime_isr.api.app import create_app
    from maritime_isr.api.settings import settings
    c = TestClient(create_app())
    c.headers.update({"X-API-Token": settings.token})
    return c


def test_station_endpoint_serves_two_rings_and_flags_every_row_synthetic():
    """The coverage map is the most persuasive picture here and the most
    dangerous to serve unlabelled: there is no real radar behind it.

    Two rings, not one, because the radar horizon depends on target height — a
    single circle would either promise skiff coverage a station does not have or
    hide tanker coverage it does.
    """
    r = _client().get("/api/radar/stations")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"]["real"] == 0
    assert body["items"], "no stations served"
    for s in body["items"]:
        assert s["is_synthetic"] is True, s["station_id"]
        assert 0 < s["range_small_km"] <= s["range_large_km"] <= s["max_range_km"]
        assert 5.0 <= s["lat"] <= 25.0 and 60.0 <= s["lon"] <= 78.0


def test_contact_endpoint_defaults_to_survivors_and_can_show_suppressions():
    """'Why is this NOT dark' has to be answerable from the product.

    Skipped rather than asserted-empty when nothing is landed: this reads the
    real store, and a checkout that has not run `radar correlate --write` has
    nothing to serve. Asserting an empty list would pass in exactly the case the
    test exists to check.
    """
    c = _client()
    r = c.get("/api/radar/contacts")
    assert r.status_code == 200, r.text
    body = r.json()
    if body.get("note"):
        pytest.skip("no landed radar correlation in this checkout")
    assert all(i["status"] == "dark_candidate" for i in body["items"])

    every = c.get("/api/radar/contacts", params={"status": "all", "limit": 2000})
    assert every.status_code == 200
    kinds = {i["status"] for i in every.json()["items"]}
    assert kinds - {"dark_candidate"}, (
        "no suppressed verdict is reachable through the API — the cascade is a "
        "black box from the operator's side")


def test_track_endpoint_decimates_and_keeps_points_in_time_order():
    r = _client().get("/api/radar/tracks",
                      params={"max_tracks": 5, "max_points": 20})
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    if not items:
        pytest.skip("no landed radar_track_report in this checkout")
    for t in items:
        assert len(t["points"]) <= 20, "decimation did not hold"
        assert t["n_points"] >= len(t["points"])
        epochs = [p[2] for p in t["points"]]
        assert epochs == sorted(epochs), "points are not in time order"
        for lon, lat, _ts in t["points"]:
            assert 60.0 <= lon <= 78.0 and 5.0 <= lat <= 25.0


def test_epoch_reads_a_naive_timestamp_as_utc():
    """Parquet hands back tz-naive timestamps, and `.timestamp()` on one applies
    the HOST's local zone — which would slide every radar track on the map by
    the machine's offset, silently, and differently on Eshan's laptop than in
    the sandbox."""
    import datetime as dt

    from maritime_isr.api.service import _epoch
    want = int(dt.datetime(2026, 6, 25, 12, 36, tzinfo=dt.timezone.utc).timestamp())
    assert _epoch(dt.datetime(2026, 6, 25, 12, 36)) == want
    assert _epoch(pd.Timestamp("2026-06-25 12:36", tz="UTC")) == want
    assert _epoch("2026-06-25T12:36:00Z") == want
    assert _epoch(None) == 0


# ==========================================================================
# 9. the pipeline lands the correlation it computed
# ==========================================================================

def test_landing_the_correlation_is_what_makes_radar_visible(tmp_path, monkeypatch):
    """The Radar view reads landed tables, not a terminal.

    Correlating takes minutes over thousands of epochs, so no request can do it
    on demand — the result has to be on disk like every other derived product.
    This drives that whole path: correlate, land, then ask the API the question
    the view asks.
    """
    from maritime_isr.api import service
    from maritime_isr.config import cfg
    from maritime_isr.fusion.radar_ais import land_correlation
    from maritime_isr.ingest.landing import SYNTHETIC_SOURCE_ID

    monkeypatch.setattr(cfg, "data_root", tmp_path)

    rad, _ = build_tracks(_radar_rows("SYN-POR:0001", n=40), source=RADAR)
    other = _ais_rows(999000002, n=40, lat0=21.70, lon0=69.50)
    ais, _ = build_tracks(other, source=AIS)
    out = correlate_radar(rad, ais, _cov(other), {})
    assert [v for v in out.verdicts if v["status"] == "dark_candidate"], (
        "fixture produced no dark contact; nothing to land")

    # Empty before, so the assertion after cannot pass on stale data.
    assert service.list_radar_contacts()["items"] == []

    landed = land_correlation(out, source_id=SYNTHETIC_SOURCE_ID,
                              is_synthetic=True)
    assert sum(landed.values()) > 0, landed

    served = service.list_radar_contacts()
    assert served["items"], (
        "the correlation landed but the Radar view still sees nothing")
    assert not served.get("note"), served.get("note")
    assert all(r["is_synthetic"] for r in served["items"])


def test_the_pipeline_lands_its_radar_stage_rather_than_printing_it():
    """A grep-shaped guard, because the omission was grep-shaped.

    `run_radar_stage` computed the correlation, printed the numbers and threw
    the object away. Every other stage lands its output, so nothing looked
    wrong: the stage reported real figures and read as complete, while the
    Radar view stayed empty after a full successful run. An operator followed
    the documented sequence, got an empty tab, and reasonably concluded the
    feature was broken.

    Asserting on the source is weak, and it is the right weakness here — the
    alternative is running a multi-minute pipeline over a full corpus inside
    the unit suite. What it catches is exactly what happened: the call going
    missing again.
    """
    import pathlib

    src = (pathlib.Path(__file__).resolve().parent.parent
           / "tools" / "run_scenario_pipeline.py").read_text(encoding="utf-8")
    stage = src.split("def run_radar_stage")[1].split("\ndef ")[0]
    assert "land_correlation(" in stage, (
        "run_radar_stage computes the correlation but never lands it — the "
        "Radar view reads the landed table and will be empty after a full "
        "pipeline run")
