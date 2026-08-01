# Maritime ISR

A maritime intelligence, surveillance, and reconnaissance prototype that hunts for
**dark vessels** — ships that switch off their AIS transponder to hide — in the
Arabian Sea and Indian west-coast waters. It does this by fusing three kinds of
free, public data:

- **SAR** (satellite radar imagery) — sees ships regardless of weather, day or
  night, whether or not they're broadcasting.
- **AIS** (the position signal ships legally broadcast) — tells us who's who, when
  it's on.
- **Public registries** — sanctions lists, ownership records, port databases.

When the radar sees a ship that the AIS picture can't explain, that's a candidate
dark vessel. The system ranks those candidates by suspicion and attaches a
plain-English chain of evidence to each one.

**New here? Read the docs in this order:** `CLAUDE.md` (the operating rules) →
`ARCHITECTURE.md` (how data flows) → `STATE.md` (what's actually built) →
`GLOSSARY.md` (every term explained) → `DECISIONS.md` (why things are the way they
are). The canonical build plan is `maritime-isr-execution-spec.md`.

---

## Honest capability statement (read this before believing any claim)

> The **code** for the data-landing layer (Phase 0) is written and passes its
> tests **in a sandbox**. **Nothing has yet run on real infrastructure** — the
> deployment VM is not provisioned. No live AIS has been captured on-host, no SAR
> scene preprocessed on-host, and **no dark vessel has been detected on real
> data.** Dark-vessel detection, the first sellable capability, first *exists* at
> Phase 3 and has not been reached.
>
> All accuracy numbers to date come from **synthetic test data with injected
> ground truth.** Real-world precision will be lower and must be re-measured on the
> deploy host before any figure is quoted. When you read "built to do X" here, that
> is **not** the same as "currently doing X." See `CLAUDE.md` §5.

This honesty is a project rule, not modesty. Do not restate prototype status as
production capability.

---

## Repository layout

```
maritime_isr/
  ingest/    connectors — one module per data source
  process/   SAR preprocessing, ship detection, track building, features
  fuse/      association engine + dark-vessel logic (the fusion core)
  graph/     object graph: ontology, edges, event engine, confidence decay
  rules/     anomaly library + risk scoring
  eval/      the permanent evaluation harness
  api/       FastAPI serving layer (Phase 6 — see below)
  inspect/   throwaway inspection dashboards (ugly on purpose, pre-Phase 6)
  infra/     cron entries, VM setup scripts, R2 config
  schemas/   canonical schemas (versioned) + the shared H3 helper
frontend/    React + MapLibre product surface (Phase 6) — Map · Alerts · Vessels · Graph
```

---

## Setup

**Prerequisites**
- Python 3.11+
- An Oracle Cloud always-free ARM VM (4 cores / 24 GB) for anything that runs
  continuously — live AIS capture, preprocessing, the API. **Not yet provisioned;**
  see `STATE.md`.
- ESA SNAP on the VM (installed via the script in `infra/`, memory-capped for
  24 GB). SNAP-on-ARM is unvalidated — see `STATE.md` OPEN QUESTIONS.

**Accounts / keys (all free tiers)**
- Copernicus Data Space (Sentinel-1 downloads)
- Cloudflare R2 (raw scene storage) — bucket + API token
- aisstream.io (live AIS websocket)
- Global Fishing Watch (vessel tracks + published SAR detections)

**Install**
```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
cp .env.example .env        # then fill in credentials (see below)
```

> **GitHub web-upload note:** dotfiles are shipped renamed (`gitignore.txt`,
> `env.example.txt`) so GitHub's web uploader accepts them. After upload, rename
> them back to `.gitignore` and `.env.example` — see `RENAME_AFTER_UPLOAD.md`.

---

## Environment variables (in `.env`)

| Variable | Purpose |
|---|---|
| `MISR_STORE_BACKEND` | `local` \| `r2` \| `mirror` — where stores read/write. `local` during laptop bootstrap; `mirror` on the VM (local + R2 copy of closed partitions). |
| `COPERNICUS_USER` / `COPERNICUS_PASS` | Copernicus Data Space credentials |
| `R2_ACCOUNT_ID` / `R2_ACCESS_KEY` / `R2_SECRET_KEY` / `R2_BUCKET` | Cloudflare R2 |
| `AISSTREAM_KEY` | aisstream.io API key |
| `GFW_TOKEN` | Global Fishing Watch API token |

Never commit `.env`. `.env.example` documents the full set.

---

## Running the entrypoints

CLI shape is `maritime-isr <verb> <target> [options]`. Available so far
(all Phase 0 — verify on-host before trusting):

```bash
# Health check: Python, libraries, DuckDB, data dir, disk budget, API keys
maritime-isr doctor
maritime-isr doctor --snap   # the parked SNAP/pyroSAR checks (deploy host only)

# Sentinel-1 SAR: backfill the AOI (idempotent — re-runs download nothing new)
maritime-isr ingest s1 --days 90

# Preprocess landed scenes (orbit → noise removal → calibration → terrain-correct → COG)
maritime-isr process s1

# Live AIS: run as a systemd service on the VM (see infra/), or foreground to test
maritime-isr ingest ais

# Historical AIS / GFW / registries
maritime-isr ingest noaa
maritime-isr ingest gfw
maritime-isr ingest registries

# Inspection dashboard v0 (open the generated HTML in a browser)
maritime-isr inspect v0
```

Each command's exit test is defined in `maritime-isr-execution-spec.md`. A command
"working in the sandbox" is not the same as its exit test passing on the VM.

---

## Phase 6 — the product surface (API + map UI)

The first screen this project has: a local **FastAPI** backend plus a
**React + MapLibre** frontend, both on `localhost`. Real and scenario data share
every table and are **always shown separately, never blended** (ADR-019); the
scenario rows carry a `SCENARIO` badge and a distinct violet treatment
everywhere they appear.

### Prerequisites

- The corpus landed under `data/` (real, from the ingest connectors, and/or the
  scenario corpus from `maritime-isr scenario generate`).
- The graph populated with alerts, if you want the alert queue and graph views to
  have content: `python tools/run_scenario_pipeline.py`.
- `pip install -e .` plus the API deps: `pip install fastapi uvicorn`.
- Node 18+ for the frontend.

### 1. Start the API (one command)

```bash
python -m maritime_isr.api          # serves 127.0.0.1:8000
```

It reads the conformed Parquet tables through DuckDB and the object graph through
SQLite, and writes nothing except alert dispositions. Auth is a shared token
(`X-API-Token`, default `maritime-isr-dev`, override with `MISR_API_TOKEN`);
CORS is scoped to the dev frontend origin. Interactive docs at
`http://127.0.0.1:8000/docs`.

Endpoints: `/vessels`, `/vessels/{id}`, `/vessels/{id}/track`,
`/vessels/{id}/neighbourhood`, `/alerts`, `/alerts/{id}`,
`/alerts/{id}/disposition`, `/events`, `/scenes`, `/ports`, `/stats`. Every
vessel/edge payload carries `is_synthetic` and the provenance envelope; every
count returns `{real, synthetic}` separately.

### 2. Start the frontend (one command)

```bash
cd frontend && npm install && npm run dev     # serves localhost:5173
```

The dev server proxies `/api` to the backend and injects the auth token, so no
secret lives in the browser and there is no CORS to configure. Point
`MISR_API_URL` / `MISR_API_TOKEN` at a non-default backend if needed. `npm run
build && npm run preview` serves the production build on `:4173` the same way.

Four views: **Map** (AOI framed on the Arabian Sea, toggleable layers, an 8-week
time scrubber with play/pause, click a vessel for its entity panel), **Alerts**
(a short, high-signal queue — each alert's evidence chain rendered as labelled
hops, with confirm/watch/dismiss buttons that persist), **Vessels** (a sortable,
filterable table), and **Graph** (seed-and-expand from one vessel, one hop per
click, never a hairball).

### Verifying against real data

The API is written in a sandbox with no real data, so schema assumptions are
provisional until measured on the machine that holds the corpus. Run the
profiler there and commit its output:

```bash
python tools/api_schema_profile.py     # -> data_profiles/api_schema_profile.json
```

The exercise tests (`tests/test_api_exercise.py`) hit every endpoint against the
landed DuckDB/Parquet corpus and assert non-empty, correctly-shaped results (not
existence checks); they skip cleanly when no corpus is landed.

---

## D1 quickstart — download-only laptop mode

This is the mode to use on a Windows laptop with no server: every source is a
finite download, storage is local Parquet + DuckDB, total data stays under 1 GB.
Read `DATA_SOURCES.md` first — it records what is and is not obtainable, and
why. Run everything from the repo root.

**1. Set up (once).**

```bash
pip install -e ".[dev]"
copy .env.example .env          # PowerShell: Copy-Item .env.example .env
```

Then open `.env` and paste in your free Global Fishing Watch token
(register at <https://globalfishingwatch.org/our-apis/>).

**2. Check the machine is ready.**

```bash
maritime-isr doctor
```

*Success* ends with `RESULT: READY`. Warnings about parked keys (R2, Copernicus,
aisstream) are expected and fine — those need a server we do not have.
*Failure* ends with `RESULT: NOT READY` and lists exactly what to fix.

**3. Download the data.**

```bash
maritime-isr ingest gfw-events --weeks 8     # encounters, loitering, port visits, AIS gaps
maritime-isr ingest gfw-vessels              # identity for every vessel seen above
maritime-isr ingest registries               # OFAC + UN + EU sanctions, WPI ports
maritime-isr ingest s1 --days 56 --catalog-only   # Sentinel-1 scene metadata, no imagery
```

Each is idempotent — running it twice downloads the same window again and lands
no duplicates, so an interrupted run is safe to repeat.

**4. SAR detections (currently degraded — read this).**

GFW's SAR datasets have been offline since 2026-07-03 pending their migration to
Sentinel-1C/1D. When they return:

```bash
maritime-isr ingest gfw --weeks 8            # gridded presence: COUNTS per cell, not contacts
```

Per-detection SAR (with vessel length and AIS-match status) has **no API** — it
is a manual CSV export from GFW's Data Download Portal. Once downloaded:

```bash
maritime-isr ingest gfw-sar-csv --path C:\Users\you\Downloads\sar_detections.csv
```

**5. See what landed.**

```bash
python tools/d1_report.py
```

Prints row count, date range, AOI bounds check and disk size for every table,
plus total usage against the 1 GB budget. `AOI: all inside` is what you want;
`** N OUTSIDE **` means a real bug.

**Want to see it work before you have a token?**

```bash
python tools/d1_smoke.py          # lands FIXTURE data through the real code, then reports
python tools/d1_smoke.py --clean  # removes it
```

Everything the smoke test lands is synthetic. It proves the plumbing runs; it is
not evidence about any real vessel, and no number from it may be quoted.

---

## The AOI

Arabian Sea + Indian west-coast EEZ, bounding box **5°N–25°N, 60°E–78°E.**
Locked in the config loader; do not change it casually — many sanity checks
(expected scene counts, coverage model) assume it.

---

## Cost

Target operating cost is **$0**. The only planned paid feed (Spire satellite AIS)
is stubbed and deferred until a demo justifies it. See `DECISIONS.md` ADR-001 /
ADR-005.
