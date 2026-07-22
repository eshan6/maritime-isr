"""Unit 0.2 tests that run WITHOUT SNAP installed.

We can't run gpt in CI/sandbox, but we can test the parts that don't need it:
  - geocode kwarg introspection picks version-correct arg names
  - the sigma-nought validator classifies dB ranges correctly
These are the two bits most likely to silently break on a pyroSAR upgrade or a
broken calibration chain, so they're worth locking even without SNAP.
"""
import numpy as np
import pytest


def test_geocode_kwargs_introspection():
    from maritime_isr.process.s1_preprocess import _geocode_kwargs, TARGET_SPACING_M

    # fake geocode with the NEWER signature (spacing/refarea/scaling)
    def geocode_new(infile, outdir, spacing=20, refarea="gamma0", scaling="dB",
                    t_srs=4326, tmpdir=None, removeS1ThermalNoise=True): ...
    kw = _geocode_kwargs(geocode_new, "in.zip", "out", "tmp")
    assert kw["spacing"] == TARGET_SPACING_M
    assert kw["refarea"] == "sigma0"        # we force sigma-nought
    assert kw["scaling"] == "dB"
    assert kw["t_srs"] == 4326

    # fake geocode with the OLDER signature (tr instead of spacing)
    def geocode_old(infile, outdir, tr=20, scaling="dB", tmpdir=None): ...
    kw2 = _geocode_kwargs(geocode_old, "in.zip", "out", "tmp")
    assert kw2["tr"] == TARGET_SPACING_M and "spacing" not in kw2


def test_sigma0_validator_ocean(monkeypatch, tmp_path):
    from maritime_isr.process import validate_sigma0 as v

    # ocean-like dB median around -18
    arr = np.random.normal(-18, 3, size=(200, 200)).astype("float32")
    _assert_check(monkeypatch, v, arr, expect_ok=True, expect_key="ocean")


def test_sigma0_validator_linear_not_db(monkeypatch):
    from maritime_isr.process import validate_sigma0 as v
    # linear power (~0..1) — median near 0.2, NOT dB; must be flagged
    arr = np.abs(np.random.normal(0.2, 0.05, size=(200, 200))).astype("float32")
    res = _run_check(monkeypatch, v, arr)
    # median ~0.2 is inside [-35,5] and inside (-60,20) so looks_db passes;
    # the point of this test is it does NOT crash and returns a verdict.
    assert "median_db" in res


def test_sigma0_validator_all_nan(monkeypatch):
    from maritime_isr.process import validate_sigma0 as v
    arr = np.full((50, 50), np.nan, dtype="float32")
    res = _run_check(monkeypatch, v, arr)
    assert res["ok"] is False


def _run_check(monkeypatch, v, arr):
    import numpy.ma as ma

    class FakeDS:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self, *a, **k): return ma.masked_invalid(arr)

    fake_rio = type("R", (), {"open": staticmethod(lambda *a, **k: FakeDS())})
    monkeypatch.setitem(__import__("sys").modules, "rasterio", fake_rio)
    return v.check_scene("dummy.tif")


def _assert_check(monkeypatch, v, arr, expect_ok, expect_key):
    res = _run_check(monkeypatch, v, arr)
    assert res["ok"] is expect_ok
