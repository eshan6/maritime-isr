"""`maritime-isr doctor` — check that THIS machine can run download-only mode.

Operating mode (D1): a Windows laptop. No Oracle VM, no Cloudflare R2, no
systemd, no ESA SNAP, no always-on AIS capture. Every data source is a finite
download; storage is local Parquet + DuckDB under the data root.

This doctor answers one question in plain English: *can Eshan run the ingest
today, and if not, exactly what does he need to fix?* It checks the things that
actually break a download run — Python version, the libraries, DuckDB actually
opening a database, the data directory being writable, free disk against the
1 GB budget, and whether the API keys each connector needs are present.

It is deliberately non-fatal about credentials: a missing key is reported as
"this connector can't run yet", not as a broken install, because keys arrive one
at a time as each source is wired.

The old SNAP/pyroSAR checks still exist in `process/snap_doctor.py` and are
reachable via `maritime-isr doctor --snap`. They are PARKED under laptop mode:
SNAP is not installed on the laptop and is not needed until SAR imagery is
processed on a deploy host.
"""
from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path

from ..config import CLI, ENV_SPEC, cfg, header_safety, repo_root

# Under laptop mode the whole downloaded corpus must stay under 1 GB.
DISK_BUDGET_BYTES = 1_000_000_000
# Headroom we want free before starting a download run.
MIN_FREE_BYTES = 2_000_000_000

OK, WARN, FAIL = "ok", "warn", "FAIL"

# Credentials that matter on a laptop, and what breaks without each.
# R2_* and CDSE_* are intentionally absent: R2 is parked (no bucket), and the
# Copernicus catalog search we use needs no login (only imagery download does).
LAPTOP_CREDS = {
    "GFW_API_TOKEN": "Global Fishing Watch events / vessel identity / SAR presence",
}
PARKED_CREDS = {
    "R2_ACCESS_KEY_ID": "Cloudflare R2 mirroring — parked, no bucket in laptop mode",
    "R2_SECRET_ACCESS_KEY": "Cloudflare R2 mirroring — parked",
    "R2_ACCOUNT_ID": "Cloudflare R2 mirroring — parked",
    "R2_BUCKET": "Cloudflare R2 mirroring — parked",
    "CDSE_USERNAME": "Copernicus imagery download — parked (catalog search needs no login)",
    "CDSE_PASSWORD": "Copernicus imagery download — parked",
    "AISSTREAM_API_KEY": "live AIS capture — parked, laptop cannot stay on",
}


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:,.1f} {unit}"
        n /= 1024
    return f"{n:,.1f} PB"


def _dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


class Report:
    """Collects check results so we can print one clean summary at the end."""

    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def add(self, status: str, name: str, detail: str = "") -> None:
        self.rows.append((status, name, detail))

    @property
    def failures(self) -> list[tuple[str, str, str]]:
        return [r for r in self.rows if r[0] == FAIL]

    @property
    def warnings(self) -> list[tuple[str, str, str]]:
        return [r for r in self.rows if r[0] == WARN]

    def print(self) -> None:
        width = max((len(n) for _, n, _ in self.rows), default=10)
        for status, name, detail in self.rows:
            mark = {OK: "[ok]  ", WARN: "[warn]", FAIL: "[FAIL]"}[status]
            line = f"  {mark} {name:<{width}}"
            if detail:
                line += f"  {detail}"
            print(line)


# --------------------------------------------------------------------------
# individual checks
# --------------------------------------------------------------------------

def check_python(rep: Report) -> None:
    v = sys.version_info
    detail = f"{v.major}.{v.minor}.{v.micro} on {platform.system()} {platform.machine()}"
    if (v.major, v.minor) >= (3, 11):
        rep.add(OK, "python", detail)
    else:
        rep.add(FAIL, "python", f"{detail} — need 3.11+; install from python.org")


def check_packages(rep: Report) -> None:
    """Import the libraries the download path actually uses."""
    required = {
        "duckdb": "query engine over the landed Parquet",
        "pyarrow": "writes the Parquet files",
        "pandas": "table handling",
        "h3": "the shared spatial grid",
        "requests": "talks to the APIs",
        "pydantic": "validates records before landing",
        # Not optional: without pytz, DuckDB cannot bind a timezone-aware
        # datetime as a query parameter against a TIMESTAMPTZ column, which is
        # exactly what the registry as_of lookups do.
        "pytz": "lets DuckDB compare timezone-aware timestamps",
    }
    for mod, why in required.items():
        try:
            m = __import__(mod)
            ver = getattr(m, "__version__", "?")
            rep.add(OK, f"lib {mod}", f"{ver} — {why}")
        except Exception as e:  # noqa: BLE001
            rep.add(FAIL, f"lib {mod}", f"not importable ({e}) — run: pip install -e .")


def check_h3_version(rep: Report) -> None:
    """h3 v4 renamed the API; mixing v3 and v4 silently breaks every spatial join."""
    try:
        import h3
    except Exception as e:  # noqa: BLE001
        rep.add(FAIL, "h3 api", f"h3 not importable: {e}")
        return
    if hasattr(h3, "latlng_to_cell"):
        try:
            cell = h3.latlng_to_cell(15.0, 68.0, 7)
            rep.add(OK, "h3 api", f"v4 names present, sample res-7 cell {cell}")
        except Exception as e:  # noqa: BLE001
            rep.add(FAIL, "h3 api", f"latlng_to_cell raised {e}")
    else:
        rep.add(
            WARN,
            "h3 api",
            "v3 detected — h3util installs a shim, but pin h3>=4 (see CLAUDE.md §2)",
        )


def check_duckdb(rep: Report) -> None:
    """Actually open a database and run a query — importing is not proof it works."""
    try:
        import duckdb
    except Exception as e:  # noqa: BLE001
        rep.add(FAIL, "duckdb run", f"not importable: {e}")
        return
    try:
        con = duckdb.connect()
        got = con.execute("select 42").fetchone()[0]
        con.close()
        if got == 42:
            rep.add(OK, "duckdb run", "in-memory query returned the right answer")
        else:
            rep.add(FAIL, "duckdb run", f"query returned {got!r}, expected 42")
    except Exception as e:  # noqa: BLE001
        rep.add(FAIL, "duckdb run", f"query failed: {e}")


def check_working_directory(rep: Report) -> None:
    """Say plainly which checkout is in use, and whether you are standing in it.

    Commands work from anywhere because the package is installed in editable
    mode, which is convenient and was also a trap: `.env` resolves from the
    package location while `data/` used to resolve from the current directory,
    so `doctor` could report READY against a data dir that had nothing in it.
    Data root is now anchored to the repo, but the mismatch is still worth
    surfacing — `git pull` in the wrong folder silently does nothing.
    """
    root = repo_root()
    rep.add(OK, "repo root", str(root))
    try:
        here = Path.cwd().resolve()
    except OSError:
        return
    if here == root:
        rep.add(OK, "working dir", "you are in the repo root")
    else:
        rep.add(
            WARN,
            "working dir",
            f"you are in {here}, not the repo. Data still lands in the repo, but "
            f"`git pull` here will fail. Run: cd {root}",
        )


def check_data_dir(rep: Report) -> None:
    """The data root must exist and be writable — this is where everything lands."""
    root = cfg.data_root
    try:
        root.mkdir(parents=True, exist_ok=True)
    except Exception as e:  # noqa: BLE001
        rep.add(FAIL, "data dir", f"cannot create {root}: {e}")
        return

    probe = root / ".doctor_write_probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        rep.add(OK, "data dir", f"{root.resolve()} exists and is writable")
    except Exception as e:  # noqa: BLE001
        rep.add(FAIL, "data dir", f"{root.resolve()} not writable: {e}")


def check_disk(rep: Report) -> None:
    """Free space for the run, and how much of the 1 GB budget is already spent."""
    root = cfg.data_root if cfg.data_root.exists() else Path(".")
    try:
        usage = shutil.disk_usage(root)
    except Exception as e:  # noqa: BLE001
        rep.add(WARN, "disk free", f"could not measure: {e}")
        return

    if usage.free >= MIN_FREE_BYTES:
        rep.add(OK, "disk free", f"{_human(usage.free)} free on the drive holding {root}")
    else:
        rep.add(
            WARN,
            "disk free",
            f"only {_human(usage.free)} free — want {_human(MIN_FREE_BYTES)} headroom",
        )

    used = _dir_size(cfg.data_root)
    pct = 100.0 * used / DISK_BUDGET_BYTES
    detail = f"{_human(used)} of {_human(DISK_BUDGET_BYTES)} budget used ({pct:.1f}%)"
    if used <= DISK_BUDGET_BYTES:
        rep.add(OK, "data budget", detail)
    else:
        rep.add(WARN, "data budget", detail + " — over budget, prune old snapshots")


def check_store_backend(rep: Report) -> None:
    """Laptop mode must be `local`; r2/mirror need a bucket that doesn't exist yet."""
    backend = cfg.store_backend
    if backend == "local":
        rep.add(OK, "store backend", "local — correct for laptop mode")
    else:
        rep.add(
            WARN,
            "store backend",
            f"{backend!r} — laptop mode expects 'local'. "
            "Set MISR_STORE_BACKEND=local (R2 is parked until a deploy host exists)",
        )


def check_credentials(rep: Report) -> None:
    """Report which connectors can run. Missing keys are a warning, not a failure.

    A key that is *present but unusable* is a FAILURE, not a pass. Reporting
    "present" for a token that cannot be encoded into an HTTP header is worse
    than reporting nothing: it sends the operator to run a pull that cannot
    possibly succeed, and the real error surfaces deep inside urllib3.
    """
    for var, what in LAPTOP_CREDS.items():
        raw = os.getenv(var)
        if not raw:
            rep.add(
                WARN,
                f"key {var}",
                f"missing — {what} cannot run. Put it in a .env file at the repo root",
            )
            continue
        ok, why = header_safety(raw)
        if ok:
            rep.add(OK, f"key {var}", f"present and header-safe — {what} can run")
        else:
            rep.add(
                FAIL,
                f"key {var}",
                f"present but UNUSABLE — {why} "
                "Retype or re-paste it into a plain-text editor.",
            )

    for var, why in PARKED_CREDS.items():
        raw = os.getenv(var)
        if not raw:
            rep.add(OK, f"key {var}", f"absent — fine. {why}")
        elif header_safety(raw)[0]:
            rep.add(OK, f"key {var}", "present (not needed in laptop mode)")
        else:
            # Parked, so not a failure today — but it would break on the deploy host.
            rep.add(
                WARN,
                f"key {var}",
                "present but NOT header-safe. Parked, so harmless now; "
                "re-paste it before the deploy host needs it",
            )


def check_env_file(rep: Report) -> None:
    """A .env at the repo root is how credentials reach the process on Windows."""
    root = Path(__file__).resolve().parent.parent.parent
    env = root / ".env"
    example = root / ".env.example"
    if env.exists():
        rep.add(OK, ".env file", f"found at {env}")
    elif example.exists():
        rep.add(
            WARN,
            ".env file",
            f"not found. Copy {example.name} to .env and fill in the keys you have",
        )
    else:
        rep.add(WARN, ".env file", "not found, and no .env.example to copy from")


def check_parked(rep: Report) -> None:
    """State plainly what is switched off, so absence is never read as breakage."""
    for name, why in (
        ("SNAP/pyroSAR", "SAR imagery preprocessing — needs a deploy host"),
        ("systemd services", "live AIS capture — laptop cannot stay on"),
        ("Cloudflare R2", "durable object storage — no bucket provisioned"),
        ("NOAA historical AIS", "US EEZ only — cannot cover the Arabian Sea AOI"),
    ):
        rep.add(OK, f"parked {name}", why)


# --------------------------------------------------------------------------
# entrypoint
# --------------------------------------------------------------------------

def run(snap: bool = False) -> int:
    """Run the laptop checks. `snap=True` runs the parked SNAP checks instead."""
    if snap:
        from ..process.snap_doctor import run as snap_run

        print("Running the PARKED SNAP/pyroSAR checks (deploy-host only).")
        print("These are expected to fail on the laptop — SNAP is not installed there.\n")
        return snap_run()

    print("=" * 72)
    print("Maritime ISR — doctor (download-only laptop mode)")
    print("=" * 72)
    print(f"AOI          : {cfg.aoi.name}  "
          f"{cfg.aoi.lat_min}-{cfg.aoi.lat_max}N  {cfg.aoi.lon_min}-{cfg.aoi.lon_max}E")
    print(f"data root    : {cfg.data_root.resolve()}")
    print(f"duckdb file  : {cfg.duckdb_path()}")
    print("-" * 72)

    rep = Report()
    check_working_directory(rep)
    check_python(rep)
    check_packages(rep)
    check_h3_version(rep)
    check_duckdb(rep)
    check_data_dir(rep)
    check_disk(rep)
    check_store_backend(rep)
    check_env_file(rep)
    check_credentials(rep)
    check_parked(rep)
    rep.print()

    print("-" * 72)
    fails, warns = rep.failures, rep.warnings
    if fails:
        print(f"RESULT: NOT READY — {len(fails)} problem(s) must be fixed:")
        for _, name, detail in fails:
            print(f"  * {name}: {detail}")
        print(f"\nFix those, then run `{CLI} doctor` again.")
        print("=" * 72)
        return 1

    if warns:
        print(f"RESULT: READY, with {len(warns)} thing(s) to be aware of:")
        for _, name, detail in warns:
            print(f"  * {name}: {detail}")
        print("\nNone of these stop the machine from running. A missing API key")
        print("only stops the connector that needs it.")
        print("=" * 72)
        return 0

    print("RESULT: READY — every check passed.")
    print("=" * 72)
    return 0
