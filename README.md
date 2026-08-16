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
  fusion/    association engine + dark-vessel logic (the fusion core)
  graph/     object graph: ontology, edges, event engine, confidence decay
  rules/     anomaly library + risk scoring
  eval/      the permanent evaluation harness
  api/       FastAPI serving layer (Phase 6 — see below)
  inspect/   throwaway inspection dashboards (ugly on purpose, pre-Phase 6)
  infra/     cron entries, VM setup scripts, R2 config
  schemas/   canonical schemas (versioned) + the shared H3 helper
  ports.py   the one port gazetteer (ADR-023)
  overpass.py  satellite imaging opportunities over AIS gaps (ADR-026)
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

# which AIS gaps a satellite could have imaged (reads landed data, downloads nothing)
maritime-isr overpass

# Inspection dashboard v0 (open the generated HTML in a browser)
maritime-isr inspect v0
```

Each command's exit test is defined in `maritime-isr-execution-spec.md`. A command
"working in the sandbox" is not the same as its exit test passing on the VM.

---

## Phase 6 — the product surface (API + map UI)

The first screen this project has: a local **FastAPI** backend plus a
**React + MapLibre** frontend, both on `localhost`. Real and scenario data share
every table, and **every count is returned split `{real, synthetic}`, never
blended** (ADR-019).

> ⚠ **On screen, an individual scenario vessel is not marked.** The API carries
> `is_synthetic` on every row and splits every total, and the exported incident
> report labels a scenario vessel unmistakably — but the map, the tables and the
> vessel panels render a generated hull exactly like a real one.
> `SyntheticBadge` is a deliberate no-op today. That is a decision worth
> re-taking rather than inheriting: see STATE.md OPEN QUESTION #10. This README
> previously claimed a `SCENARIO` badge and a violet treatment "everywhere they
> appear", which had stopped being true.

### Run the demo — Python only, no Node (the easy path)

The backend serves the pre-built frontend itself, so the whole demo is **one
process and needs only Python**. On Windows, if the `pip` shortcut is broken,
run it through Python: `python -m pip ...`.

```bash
python -m pip install -e ".[api]"                    # FastAPI + uvicorn

# one-time: land the scenario corpus and populate the graph so the alert queue
# and graph views have content (a few minutes — runs the track engine):
python -m maritime_isr.cli scenario generate --seed 7
python tools/run_scenario_pipeline.py

python -m maritime_isr.api                            # serves 127.0.0.1:8000
```

Then open **http://127.0.0.1:8000** in a browser. That's the whole demo — Map,
Findings, Alerts, Vessels, Graph. Nav routes, hard refreshes and pasted deep
links all work; the backend falls back to the app's `index.html` for any
non-`/api` path.

The API reads the conformed Parquet tables through DuckDB and the object graph
through SQLite, and writes nothing except alert dispositions. Auth is a shared
token (`X-API-Token`, default `maritime-isr-dev`, override with `MISR_API_TOKEN`
— injected into the served page so you never type it). JSON lives under `/api`:

| Route | What it serves |
|---|---|
| `/api/vessels`, `/api/vessels/{id}` | the table and one hull's full record |
| `/api/vessels/{id}/track`, `/neighbourhood` | AIS positions; graph neighbours |
| `/api/vessels/{id}/report` | **the one-click incident report** (HTML, or `?format=json`) |
| `/api/findings` | the ranked findings table |
| `/api/alerts` (+ detail, `/disposition`) | the alert queue and analyst feedback |
| `/api/events`, `/api/events/density` | event rows; per-H3-cell counts over the whole corpus |
| `/api/detections` | SAR radar contacts |
| `/api/tracks`, `/api/scenes`, `/api/ports`, `/api/stats` | map layers and headline figures |
| `/api/corpus-window` | just the corpus time span — the map scrubber's only dependency, split out of `/stats` so it is not queued behind the slow calls |
| `/api/graph/all` | every current relationship as one web, most-connected core first, with `truncated` + totals |
| `/api/graph/seeds` | vessels worth opening the graph on, ranked by degree |

Interactive docs at `/docs`. Every vessel/edge payload carries `is_synthetic`
and the provenance envelope; every count returns `{real, synthetic}` separately.

The five views:

- **Map** — AOI framed on the Arabian Sea, an 8-week time scrubber with
  play/pause that animates vessels along their AIS tracks, and toggleable
  layers: events, **event density** (per-H3-cell counts over the *whole* corpus,
  not the page — the plain event layers are capped and say so), **SAR radar
  contacts** (drawn hollow when no AIS track is associated), Sentinel-1
  footprints, ports and alert markers. Click a vessel for its entity panel.
- **Findings** — the ranked table: GFW-assessed intentional-disabling AIS gaps
  first, then sanctions-matched hulls, each row expanding to its evidence.
  Ranking is a sum of **named signals shown with the row**, never a blended
  score.
- **Alerts** — a short, high-signal queue; each alert's evidence chain as
  labelled hops, with confirm/watch/dismiss that persist.
- **Vessels** — a sortable, filterable table.
- **Graph** — opens on the **whole network**: every current relationship drawn
  as one web, framed on the most-connected sanctioned vessel (falling back to
  the most-connected vessel, then any node). The panel states how much of the
  graph is on screen and on what basis the centre was chosen — *"that is where
  the camera starts, not a finding"*. **Dashed links are relationships that have
  ended**; solid ones are current. Seed a vessel to drop into the older
  seed-and-expand mode, one hop per click.

  The web is capped at 1,500 nodes and the panel says so with both totals when
  it truncates. That is a **rendering** limit, not a data limit: the real corpus
  graph is an estimated ~19,000 nodes and no in-browser force layout will draw
  it. Ranking is by **degree**, which is connectedness and not risk.

**The export** is on every findings row and every vessel panel. It downloads a
**self-contained HTML incident report** — no external assets, so it reads the
same offline — carrying the vessel's identity and history, why it was flagged,
the evidence with its attribution, a *"what this report does not establish"*
section, and the provenance chain. It prints to PDF from the browser. A scenario
vessel's report is labelled top and bottom and the filename carries a
`SCENARIO-` prefix.

### Developing the frontend (needs Node)

The committed `frontend/dist/` is what the backend serves. To change the UI,
edit `frontend/src`, run the Vite dev server (hot reload, proxies `/api` to the
backend), then rebuild and commit `dist/`:

```bash
cd frontend && npm install
npm run dev            # localhost:5173, proxies /api to 127.0.0.1:8000
npm run build          # rebuild dist/ — commit it so the Python-only path updates
```

> ⚠ **`npm install` is not optional after pulling.** The Graph view depends on
> `cytoscape-fcose`, added 2026-08-13. Cytoscape's built-in `cose` layout was
> measured at **115 seconds** on 1,409 nodes — a hung tab, not a slow render —
> against roughly 6.5 s for `fcose`. A `git pull` alone leaves the dependency
> missing and `npm run build` fails.

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

**3b. Who was watching? (no download, ADR-026).**

```bash
maritime-isr overpass                        # imaging opportunities over flagged AIS gaps
maritime-isr overpass --all-gaps             # every landed gap, not only GFW-flagged ones
```

This one fetches nothing. It reads the two tables above and asks, for each gap
GFW flagged as intentional AIS disabling: *given where the vessel went dark,
where it reappeared, and how long it was silent, could a Sentinel-1 pass have
photographed it?* Bounding vessel speed gives the area it must have been inside
at the moment of each pass; that area is compared against the scene footprint.

**3c. Coastal radar, and the first dark contacts (no download, ADR-028).**

```bash
maritime-isr scenario generate               # includes the simulated radar picture
maritime-isr radar correlate                 # radar <-> AIS, then the dark contacts
```

A coastal radar station reports *where* and *how fast*, and never *who*. That
absence is the point: a radar track that no AIS broadcast explains is a
candidate dark vessel, and unlike the satellite path this needs no imagery.

**We have no radar feed.** The Indian Coast Guard's Coastal Surveillance
Network is theirs and nothing comparable is public for these waters, so the
picture here is **simulated** — sixteen stations on real coastline, detection
decided by signal-to-noise against the radar horizon, terrain shadows, an
outage, sea clutter and four real fixed installations. Crucially it is generated
from the *same vessel truth* the synthetic AIS is emitted from, so a hull that
appears on radar and not on AIS is one ship with her transponder off rather than
two separate fabrications. It lands through a real connector into the same
table a real feed would use, flagged synthetic.

Measured on that picture: **precision 100%, recall 50%** — four dark contacts,
none of them wrong, four of eight findable dark episodes found. The number that
is *not* flattering is reported too: radar-to-AIS correlation resolves about one
radar track in nine, and the likely cause is how sparsely the generator lands
AIS from anchored vessels rather than the matching itself (STATE.md,
OQ-radar-1).

One sentence this earns and one it does not, stated plainly: *"that contact is
on radar and nothing is broadcasting there"* works. *"Here is where its
transponder went quiet"* is computed for 77 tracks and reaches the alert queue
for none of them — see ADR-028 and STATE.md for why, and what the next fix is.

Building this tested the architecture's central claim — that a new sensor is a
connector and not a rewrite — and found **four places the core silently assumed
AIS**, plus one association defect that had been latent on the satellite path
since Phase 3. ADR-028 has all five.

It is the first determination in this system that is **ours** rather than a
third party's — and it is strictly a statement about **where a satellite was
pointed**. We hold scene metadata, not imagery, so nothing has been examined and
no vessel has been detected. Expect mostly `partial` coverage: at 20 kn the area
a vessel could occupy exceeds one Sentinel-1 footprint about four hours into a
gap, so a full containment needs a short gap or a pass near one of its ends.

Its most useful output is a **shopping list** — named scene ids whose download
would resolve a concrete question about a specific hull.

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
