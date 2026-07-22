"""Unit 0.0 exit test: schema round-trips + provenance invariants."""
from datetime import datetime, timezone

import pytest

from maritime_isr.schemas import (
    Detection, DetectionMethod, PositionReport, Provenance,
    SceneCatalogEntry, SceneStatus, ENVELOPE_COLUMNS,
)


def _prov():
    return Provenance(source_id="test", source_ref="x1",
                      acquired_at=datetime(2026, 7, 1, tzinfo=timezone.utc))


def test_position_report_roundtrip():
    pr = PositionReport(mmsi=419000001, lat=15.0, lon=68.0,
                        timestamp=datetime(2026, 7, 1, 12, tzinfo=timezone.utc), prov=_prov())
    d = pr.model_dump()
    pr2 = PositionReport(**d)
    assert pr2.mmsi == pr.mmsi and pr2.lat == pr.lat


def test_scene_roundtrip():
    s = SceneCatalogEntry(scene_id="S1A_X", footprint_wkt="POLYGON((60 5,78 5,78 25,60 25,60 5))",
                          acquired_at=datetime(2026, 7, 1, tzinfo=timezone.utc), prov=_prov())
    assert SceneCatalogEntry(**s.model_dump()).status == SceneStatus.CATALOGED


def test_detection_length_normalized():
    det = Detection(detection_id="d1", scene_id="s1", method=DetectionMethod.CFAR,
                    lat=15.0, lon=68.0, length_m=10.0, width_m=40.0,
                    acquired_at=datetime(2026, 7, 1, tzinfo=timezone.utc), prov=_prov())
    assert det.length_m == 40.0 and det.width_m == 10.0  # swapped to major-axis


def test_naive_datetime_rejected():
    with pytest.raises(ValueError):
        Provenance(source_id="t", source_ref="r", acquired_at=datetime(2026, 7, 1))


def test_mmsi_bounds():
    with pytest.raises(ValueError):
        PositionReport(mmsi=0, lat=1, lon=1,
                       timestamp=datetime(2026, 7, 1, tzinfo=timezone.utc), prov=_prov())


def test_envelope_columns_stable():
    assert ENVELOPE_COLUMNS == ("source_id","source_ref","acquired_at",
                                "ingested_at","pipeline_version","confidence")
    assert set(ENVELOPE_COLUMNS) <= set(_prov().stamp().keys())
