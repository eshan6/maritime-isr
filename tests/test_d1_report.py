"""D1 B5 — the report script must tell the truth about what is on disk."""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
UTC = timezone.utc


def _run_report(data_root: Path) -> subprocess.CompletedProcess:
    import os

    return subprocess.run(
        [sys.executable, str(REPO / "tools" / "d1_report.py")],
        env={**os.environ, "MISR_DATA_ROOT": str(data_root)},
        cwd=str(REPO), capture_output=True, text=True, timeout=300,
    )


def test_report_runs_on_an_empty_data_root(tmp_path):
    """A fresh clone with nothing landed must produce guidance, not a crash."""
    r = _run_report(tmp_path)
    assert r.returncode == 0, r.stderr
    assert "not landed yet" in r.stdout
    assert "Nothing landed yet" in r.stdout


def test_report_shows_counts_dates_aoi_and_size(tmp_path, monkeypatch):
    """The four things the report exists to show, on a table we actually land."""
    from maritime_isr import config as cfg_mod
    from maritime_isr.ingest import landing
    from maritime_isr.ingest.landing import land_table, stamp_envelope, stamp_h3

    monkeypatch.setattr(cfg_mod.cfg, "data_root", tmp_path, raising=False)
    monkeypatch.setattr(landing.cfg, "data_root", tmp_path, raising=False)

    rows = []
    for i, day in enumerate((14, 15, 16)):
        r = {"event_id": f"e{i}", "lat": 15.0 + i, "lon": 68.0 + i,
             "start_time": datetime(2026, 6, day, tzinfo=UTC)}
        stamp_h3(r)
        stamp_envelope(r, source_id="gfw-events", source_ref=f"enc:{i}",
                       acquired_at=r["start_time"])
        rows.append(r)
    land_table(rows, table="gfw_encounters", key_fields=("event_id",), day_field="start_time")

    r = _run_report(tmp_path)
    assert r.returncode == 0, r.stderr
    line = next(ln for ln in r.stdout.splitlines() if "gfw_encounters" in ln)
    assert "3" in line, "row count"
    assert "2026-06-14 .. 2026-06-16" in line, "date range"
    assert "all inside" in line, "AOI check"
    assert "KB" in line or "B" in line, "size on disk"
    assert "budget" in r.stdout


def test_report_flags_rows_outside_the_aoi(tmp_path, monkeypatch):
    """AOI leakage is a real bug; the report must shout, not shrug."""
    from maritime_isr import config as cfg_mod
    from maritime_isr.ingest import landing
    from maritime_isr.ingest.landing import land_table, stamp_envelope

    monkeypatch.setattr(cfg_mod.cfg, "data_root", tmp_path, raising=False)
    monkeypatch.setattr(landing.cfg, "data_root", tmp_path, raising=False)

    bad = {"event_id": "outside", "lat": 45.0, "lon": -30.0,
           "start_time": datetime(2026, 6, 14, tzinfo=UTC)}
    stamp_envelope(bad, source_id="gfw-events", source_ref="x",
                   acquired_at=bad["start_time"])
    land_table([bad], table="gfw_encounters", key_fields=("event_id",), day_field="start_time")

    r = _run_report(tmp_path)
    assert "OUTSIDE" in r.stdout, "a row outside the AOI must be reported loudly"


def test_report_totals_against_the_one_gb_budget(tmp_path):
    r = _run_report(tmp_path)
    assert "953.7 MB" in r.stdout or "budget" in r.stdout
    assert "% used" in r.stdout


@pytest.mark.slow
def test_smoke_script_is_idempotent(tmp_path):
    """Two smoke runs must produce identical totals — the idempotency claim."""
    import os
    import shutil

    smoke = REPO / "data" / "_smoke"
    existed = smoke.exists()
    backup = None
    if existed:
        backup = tmp_path / "_smoke_backup"
        shutil.move(str(smoke), str(backup))

    try:
        def run():
            return subprocess.run(
                [sys.executable, str(REPO / "tools" / "d1_smoke.py")],
                cwd=str(REPO), capture_output=True, text=True, timeout=900,
            ).stdout

        def totals(out: str) -> str:
            return "\n".join(ln for ln in out.splitlines()
                             if "connector output rows" in ln or "registry/catalog rows" in ln)

        first = totals(run())
        second = totals(run())
        assert first == second, f"smoke run is not idempotent:\n{first}\nvs\n{second}"
        assert "5,110" in first or "rows" in first
    finally:
        if smoke.exists():
            shutil.rmtree(smoke)
        if backup is not None:
            shutil.move(str(backup), str(smoke))
