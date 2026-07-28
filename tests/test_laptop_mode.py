"""D1 B2 — download-only laptop mode must hold without a deploy host.

These tests encode the operating mode as executable checks: nothing may require
Cloudflare R2, systemd, SNAP or live AIS capture just to import or to run the
doctor, and the default storage backend must be local.
"""
from __future__ import annotations

import importlib
import os
import pkgutil

import pytest

import maritime_isr
from maritime_isr.config import Config
from maritime_isr.infra import laptop_doctor


# --------------------------------------------------------------------------
# import path must be credential-free
# --------------------------------------------------------------------------

def test_every_module_imports_without_credentials(monkeypatch):
    """No module may need a key, a bucket or a daemon merely to be imported.

    If this fails, some module built a client or read an env var at import time
    instead of lazily — that would make the whole package unusable on a laptop.
    """
    for var in (
        "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET",
        "CDSE_USERNAME", "CDSE_PASSWORD", "AISSTREAM_API_KEY", "GFW_API_TOKEN",
        "MISR_STORE_BACKEND",
    ):
        monkeypatch.delenv(var, raising=False)

    failures = []
    for mod in pkgutil.walk_packages(maritime_isr.__path__, "maritime_isr."):
        try:
            importlib.import_module(mod.name)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{mod.name}: {type(exc).__name__}: {exc}")

    assert not failures, "modules requiring credentials at import time:\n" + "\n".join(failures)


# --------------------------------------------------------------------------
# storage backend default
# --------------------------------------------------------------------------

def test_store_backend_defaults_to_local(monkeypatch):
    """Laptop mode has no R2 bucket, so `local` must be the default."""
    monkeypatch.delenv("MISR_STORE_BACKEND", raising=False)
    assert Config().store_backend == "local"


def test_store_backend_still_honours_env(monkeypatch):
    """The deploy host flips this to mirror via env — that must keep working."""
    monkeypatch.setenv("MISR_STORE_BACKEND", "mirror")
    assert Config().store_backend == "mirror"


def test_mirror_to_r2_is_a_noop_when_local(monkeypatch):
    """With backend=local, mirroring must do nothing rather than reach for boto3."""
    monkeypatch.setenv("MISR_STORE_BACKEND", "local")
    from maritime_isr import store

    monkeypatch.setattr(store.cfg, "store_backend", "local", raising=False)
    assert store.mirror_partition_to_r2("ais", "2026-07-28T00") is None


# --------------------------------------------------------------------------
# doctor
# --------------------------------------------------------------------------

def test_doctor_passes_on_this_machine(capsys):
    """The doctor must exit 0 on a machine with the deps installed and no keys.

    A missing API key is a warning, not a failure — keys arrive one at a time.
    """
    rc = laptop_doctor.run()
    out = capsys.readouterr().out
    assert rc == 0, f"doctor reported NOT READY:\n{out}"
    assert "RESULT: READY" in out


def test_doctor_reports_missing_gfw_key_as_warning_not_failure(monkeypatch, capsys):
    monkeypatch.delenv("GFW_API_TOKEN", raising=False)
    rep = laptop_doctor.Report()
    laptop_doctor.check_credentials(rep)
    statuses = {name: status for status, name, _ in rep.rows}
    assert statuses["key GFW_API_TOKEN"] == laptop_doctor.WARN
    assert not rep.failures


def test_doctor_flags_present_gfw_key_as_ok(monkeypatch):
    monkeypatch.setenv("GFW_API_TOKEN", "test-token-not-real")
    rep = laptop_doctor.Report()
    laptop_doctor.check_credentials(rep)
    statuses = {name: status for status, name, _ in rep.rows}
    assert statuses["key GFW_API_TOKEN"] == laptop_doctor.OK


def test_doctor_warns_when_backend_is_not_local(monkeypatch):
    monkeypatch.setattr(laptop_doctor.cfg, "store_backend", "mirror", raising=False)
    rep = laptop_doctor.Report()
    laptop_doctor.check_store_backend(rep)
    assert rep.warnings, "backend=mirror on a laptop should warn"


def test_doctor_core_checks_pass(monkeypatch):
    """Python, libraries, h3 v4 and DuckDB must all actually work."""
    rep = laptop_doctor.Report()
    laptop_doctor.check_python(rep)
    laptop_doctor.check_packages(rep)
    laptop_doctor.check_h3_version(rep)
    laptop_doctor.check_duckdb(rep)
    assert not rep.failures, f"core environment checks failed: {rep.failures}"


def test_doctor_data_dir_check_creates_and_probes(tmp_path, monkeypatch):
    target = tmp_path / "isr-data"
    monkeypatch.setattr(laptop_doctor.cfg, "data_root", target, raising=False)
    rep = laptop_doctor.Report()
    laptop_doctor.check_data_dir(rep)
    assert not rep.failures
    assert target.exists()
    assert not (target / ".doctor_write_probe").exists(), "probe file must be cleaned up"


def test_disk_budget_constant_is_one_gb():
    """The mode caps all downloaded data at under 1 GB."""
    assert laptop_doctor.DISK_BUDGET_BYTES == 1_000_000_000


# --------------------------------------------------------------------------
# parked modules
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "module_name",
    [
        "maritime_isr.ingest.aisstream",
        "maritime_isr.ingest.noaa_ais",
        "maritime_isr.infra.mirror_cron",
        "maritime_isr.process.s1_preprocess",
    ],
)
def test_parked_modules_say_so_in_their_docstring(module_name):
    """Parked code stays in the tree but must announce that it is parked.

    Without the marker, a future session sees working code, assumes it runs on
    the laptop, and wires it into a scheduled path that silently produces
    nothing.
    """
    mod = importlib.import_module(module_name)
    doc = mod.__doc__ or ""
    assert "PARKED" in doc, f"{module_name} is parked but its docstring does not say so"
