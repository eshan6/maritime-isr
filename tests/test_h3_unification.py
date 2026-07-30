"""ADR-015 — one H3 helper, every resolution computed from coordinates.

These tests exist because the defect they prevent is invisible: two helpers at
different resolutions produce different cell ids, so joins between them return
nothing while every row count looks healthy.
"""
from __future__ import annotations

import pathlib

import h3
import pytest

from maritime_isr import h3util

REPO = pathlib.Path(__file__).resolve().parent.parent
PKG = REPO / "maritime_isr"


# ==========================================================================
# there is exactly one helper
# ==========================================================================

def test_tiling_module_is_gone():
    """The duplicate helper must be deleted, not merely unused."""
    assert not (PKG / "tiling.py").exists(), (
        "tiling.py is back. Two H3 helpers is the ADR-015 defect."
    )


#: Modules allowed to touch the h3 library directly, with the reason.
#: `laptop_doctor` verifies that the h3 **v4 API name exists** — going through
#: h3util would defeat the check, since h3util installs a v3 fallback shim and
#: would therefore report success on a v3 install (CLAUDE.md §2 pins v4).
_H3_DIRECT_ALLOWED = {"h3util.py", "laptop_doctor.py"}


def test_no_module_computes_cells_outside_the_helper():
    """Only h3util may compute cells. One grid, one code path (ADR-003)."""
    offenders = []
    for p in PKG.rglob("*.py"):
        if p.name in _H3_DIRECT_ALLOWED:
            continue
        src = p.read_text(encoding="utf-8")
        if "latlng_to_cell" in src or "geo_to_h3" in src:
            offenders.append(p.relative_to(REPO))
    assert not offenders, (
        "these modules compute H3 cells themselves instead of using h3util: "
        f"{offenders}"
    )


def test_nothing_derives_a_coarse_cell_from_a_fine_one():
    """cell_to_parent disagrees with direct computation for ~7% of positions.

    Banned outright rather than used carefully — the resulting bug is
    position-dependent, intermittent and invisible in aggregate counts.
    """
    offenders = []
    for p in PKG.rglob("*.py"):
        src = p.read_text(encoding="utf-8")
        # the docstring in h3util explains the ban, so allow the word there
        if "cell_to_parent(" in src:
            offenders.append(p.relative_to(REPO))
    assert not offenders, f"parent-derivation found in {offenders} — see ADR-015"


def test_helper_offers_no_parent_function():
    assert not hasattr(h3util, "parent"), "the helper must not offer derivation"
    assert not hasattr(h3util, "cell_to_parent")


# ==========================================================================
# the measurement behind the ban
# ==========================================================================

def test_parent_and_direct_computation_actually_disagree():
    """Guards the *premise* of ADR-015, not just its conclusion.

    If a future h3 release made the hierarchy geometrically nested, this test
    fails and the ADR should be revisited rather than silently obeyed.
    """
    import random

    rng = random.Random(20260729)
    mismatch = 0
    n = 20_000
    for _ in range(n):
        lat = rng.uniform(5.0, 25.0)
        lon = rng.uniform(60.0, 78.0)
        if h3.cell_to_parent(h3util.cell(lat, lon, 7), 6) != h3util.cell(lat, lon, 6):
            mismatch += 1
    rate = mismatch / n
    assert rate > 0.01, (
        f"parent-vs-direct disagreement is only {rate:.3%}. If H3 changed, "
        "revisit ADR-015 rather than assuming the ban is still needed."
    )


# ==========================================================================
# resolutions
# ==========================================================================

def test_all_five_project_resolutions_are_declared():
    """4 coverage, 6 fusion gating, 7 join key, 8 static clustering, 9 fine."""
    assert h3util.RESOLUTIONS == (4, 6, 7, 8, 9)


def test_default_matches_the_old_tiling_default():
    """The unification must not silently re-tune the fusion core.

    tiling.cell defaulted to res 6; changing that would shift fusion behaviour
    and invalidate the baselines for a reason unrelated to the refactor.
    """
    from maritime_isr.config import H3_RESOLUTION

    assert h3util.DEFAULT_RES == 6 == H3_RESOLUTION


def test_index_all_returns_every_resolution():
    got = h3util.index_all(15.5, 68.2)
    assert set(got) == {f"h3_r{r}" for r in h3util.RESOLUTIONS}
    assert len(set(got.values())) == len(h3util.RESOLUTIONS), "cells must be distinct"


def test_each_resolution_is_computed_independently():
    """Every value must equal a direct computation at that resolution."""
    lat, lon = 8.720358, 65.061535  # a known parent/direct disagreement point
    got = h3util.index_all(lat, lon)
    for r in h3util.RESOLUTIONS:
        assert got[f"h3_r{r}"] == h3.latlng_to_cell(lat, lon, r)


def test_index_both_still_returns_the_adr003_pair():
    r7, r9 = h3util.index_both(15.5, 68.2)
    assert r7 == h3util.cell(15.5, 68.2, 7)
    assert r9 == h3util.cell(15.5, 68.2, 9)


def test_neighbors_is_the_same_function_as_disk():
    """Two names, one implementation — not two implementations."""
    assert h3util.neighbors is h3util.disk


# ==========================================================================
# the join that was broken
# ==========================================================================

def test_ingest_rows_carry_the_resolution_fusion_joins_on():
    """The actual ADR-015 failure: ingest stamped 7/9, fusion joins on 6."""
    from maritime_isr.ingest.landing import stamp_h3

    row = {"lat": 15.5, "lon": 68.2}
    stamp_h3(row)
    assert "h3_r6" in row, "without res 6, ingest tables cannot join fusion tables"
    assert row["h3_r6"] == h3util.cell(15.5, 68.2, h3util.DEFAULT_RES)


def test_stamped_cells_match_what_the_fusion_core_would_compute():
    """End to end: a landed ingest row and a fusion-side cell must agree."""
    from maritime_isr.ingest.landing import stamp_h3

    lat, lon = 18.9732, 71.6237  # from a real landed loitering event
    row = {"lat": lat, "lon": lon}
    stamp_h3(row)
    assert row["h3_r6"] == h3util.cell(lat, lon)      # fusion default
    assert row["h3_r8"] == h3util.cell(lat, lon, 8)   # static clustering
    assert row["h3_r4"] == h3util.cell(lat, lon, 4)   # coverage model


def test_stamp_h3_leaves_positionless_rows_alone():
    from maritime_isr.ingest.landing import stamp_h3

    row = {"event_id": "no-position"}
    stamp_h3(row)
    assert not any(k.startswith("h3_r") for k in row)


def test_footprint_cells_survived_the_migration():
    """It only existed in tiling.py; losing it would break scene joins."""
    poly = [(5.0, 60.0), (5.0, 61.0), (6.0, 61.0), (6.0, 60.0)]
    cells = h3util.footprint_cells(poly, 5)
    assert cells and all(isinstance(c, str) for c in cells)
