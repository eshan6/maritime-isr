# data/ — the local data directory

Everything in here is **generated** and **regenerable**. None of it is committed
(see `.gitignore`). The whole directory must stay **under 1 GB** in laptop mode.

The location is set by `MISR_DATA_ROOT` (default `./data`). Everything resolves
through the storage abstraction (`store.py` / `db.py` / `writer.py`) — no module
hard-codes a path.

---

## Layout

```
data/
├── raw/                     IMMUTABLE. Exactly what the source returned, as it
│                            returned it, content-addressed by hash. Never edit
│                            or delete a file here — every derived table must be
│                            regenerable from raw + the git SHA that made it.
│
├── conformed/               Parquet, canonical schemas, regenerable from raw.
│   └── <table>/             One directory per logical table, e.g.
│                            gfw_events/, gfw_vessels/, sanctions/, wpi_ports/,
│                            s1_scenes/. Partitioned by day or hour.
│                            Enrichment lands here too, same envelope and same
│                            path: sanctioned_vessel_matches/ (ADR-016a) and
│                            sar_imaging_opportunity/ (ADR-026).
│
├── catalog.sqlite           Scene catalog: which Sentinel-1 scenes exist over
│                            the AOI, their footprints and acquisition times.
│
├── misr.duckdb              The DuckDB database. Mostly a thin query layer that
│                            reads the Parquet in conformed/ directly.
│
├── graph.sqlite             Phase 4 object graph (vessels, orgs, edges with
│                            valid_from/valid_to). Accumulates from the day it
│                            is switched on and cannot be backfilled.
│
├── models/                  Trained model artifacts (Phase 1 classifier etc.).
│
├── synthetic_scenes/        Synthetic SAR scenes for the Phase 1-3 test suites.
│
└── synthetic_*.nmea/.json   Synthetic AIS feeds and scene metadata for tests.
```

**The raw / conformed split is the load-bearing part.** Raw is what we were
given; conformed is what we made of it. If a bug is found in the parsing, we fix
the code and re-derive conformed from raw — we never patch conformed in place,
because a "clean" layer that was quietly wrong for weeks is the failure mode the
whole provenance design exists to prevent.

---

## Regenerating the synthetic test data

A fresh clone has no `data/` contents, so the Phase 6 suite will fail with
`FileNotFoundError: data/synthetic_ais_30d.nmea` until you run these. From the
repo root:

```bash
python tools/make_synthetic_scene.py
python tools/make_synthetic_feed.py
python tools/make_synthetic_feed_phase2.py
python tools/make_synthetic_scenes_phase3.py
python tools/make_synthetic_orgworld_phase4.py
```

Then the pipeline, phase by phase (each writes here):

```bash
python tools/run_phase1_synthetic.py
python tools/run_phase2_synthetic.py
python tools/run_phase3_synthetic.py
python tools/run_phase4_synthetic.py
python tools/run_phase5_synthetic.py
python tools/run_phase6_product.py   # builds data/phase6_product_surface.html
```

All of that data is **synthetic** — a proof that the machinery runs, not
evidence about real vessels. No accuracy number from it may be quoted as a real
one (CLAUDE.md §4.6).

---

## Checking size against the budget

```bash
maritime-isr doctor
```

prints `data budget` — how much of the 1 GB allowance is used. On Windows you
can also right-click the `data` folder and check Properties.

---

## What is safe to delete

- Anything under `synthetic_*`, `synthetic_scenes/`, and the `run_phase*`
  outputs — all regenerable from the commands above.
- `misr.duckdb` — regenerable from `conformed/`.
- `conformed/` — regenerable from `raw/` by re-running the connectors.

**Never delete `raw/`.** It is the one thing that cannot be regenerated without
re-downloading, and for sources with a moving window (GFW events age out) it may
not be re-downloadable at all.
