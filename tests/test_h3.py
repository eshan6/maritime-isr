"""H3 helper tests: both resolutions, determinism, enrichment."""
from maritime_isr.h3util import cell_r7, cell_r9, index_both, enrich_position, R7, R9
import h3


def test_resolutions():
    c7, c9 = index_both(15.0, 68.0)
    assert h3.get_resolution(c7) == R7
    assert h3.get_resolution(c9) == R9


def test_determinism():
    assert cell_r7(15.0, 68.0) == cell_r7(15.0, 68.0)
    assert cell_r9(15.0, 68.0) == cell_r9(15.0, 68.0)


def test_enrich():
    d = enrich_position({"lat": 15.0, "lon": 68.0})
    assert d["h3_r7"] and d["h3_r9"] and d["h3_r7"] != d["h3_r9"]
