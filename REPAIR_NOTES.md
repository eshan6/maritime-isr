# Repair pack — wiring fix for the two-lineage merge

## What broke
The repo merges two builds sharing the `maritime_isr` package: the
live-data pipeline (execution-spec units 0.0-0.5) and the synthetic
prototype (Phases 1-6). GitHub's web uploader left the *wiring files* as
whichever version landed last, in both directions:

- `config.py`, `cli.py`, `schemas/__init__.py`, `__init__.py` kept the
  unit-0.0 versions → every phase test failed at import
  (`AOI_V1`, `PIPELINE_VERSION`, `ANOMALY_THRESHOLDS` missing)
- `graph/__init__.py` was the empty unit-0.0 skeleton → `GraphStore`
  unimportable
- `requirements.txt`/`pyproject.toml` kept the prototype versions →
  live-path deps (duckdb, websockets, pydantic, boto3, shapely) missing

## What this pack contains (8 files, same paths as the repo)
- `maritime_isr/config.py` — MERGED: unit-0.0 live loader (AOI, ENV_SPEC,
  Config/cfg, `python -m maritime_isr.config`) + all prototype constants
  (`AOI_V1 = AOI()`, PIPELINE_VERSION, phase 2-5 constants)
- `maritime_isr/cli.py` — MERGED: one dispatcher, both command sets
  (live: config/doctor/preprocess/validate/ingest; prototype:
  dark-vessels/alerts/risk/graph-query/...)
- `maritime_isr/schemas/__init__.py` — MERGED: prototype pyarrow schemas +
  live dataclass re-exports (records/provenance)
- `maritime_isr/__init__.py` — docstring covering both lineages, v0.7.0
- `maritime_isr/graph/__init__.py` — restored prototype re-exports
- `pyproject.toml`, `requirements.txt` — union of both dependency sets
- `README.md` — merged: Part I (live pipeline) + Part II (synthetic
  prototype), quickstarts for both

## Verified before packaging
Overlaid on an exact copy of `main` (downloaded from GitHub), synthetic
data regenerated, then:
- `pytest tests/ --ignore=tests/test_phase6.py` → **94 passed** (76
  prototype + 18 execution-spec)
- `pytest tests/test_phase6.py` → 7 passed, 1 skipped (HTML check skips
  until `run_phase6_product.py` is run — expected)
- merged CLI lists both command sets

## How to apply (GitHub web UI)
1. Unzip this pack.
2. Repo → "Add file" → "Upload files".
3. Drag in the CONTENTS of the unzipped folder (the `maritime_isr/`
   folder + the 4 root files). Same-path files are overwritten in the
   commit.
4. Commit message suggestion:
   `fix: merge wiring files for live + prototype lineages (config, cli, schemas, graph init, deps, README)`
