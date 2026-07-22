"""Config/AOI tests: bbox order, containment, env reporting."""
from maritime_isr.config import Config, AOI


def test_bbox_order():
    a = AOI()
    assert a.bbox == (60.0, 5.0, 78.0, 25.0)  # lon_min,lat_min,lon_max,lat_max


def test_contains():
    a = AOI()
    assert a.contains(15.0, 68.0)
    assert not a.contains(0.0, 0.0)


def test_env_reporting(monkeypatch):
    for k in ("R2_ACCOUNT_ID","R2_ACCESS_KEY_ID","R2_SECRET_ACCESS_KEY","R2_BUCKET",
              "CDSE_USERNAME","CDSE_PASSWORD","AISSTREAM_API_KEY","GFW_API_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    c = Config()
    assert len(c.missing_env()) == 8
    monkeypatch.setenv("GFW_API_TOKEN", "x")
    assert "GFW_API_TOKEN" in Config().present_env()


def test_wkt_closed_ring():
    w = AOI().wkt
    assert w.startswith("POLYGON((") and w.endswith("))")
    assert w.count(",") == 4  # 5 points -> 4 commas, ring closed
