# Maritime ISR — Execution Spec (Free-Path Prototype)

**What this document is:** the roadmap restructured into build units Claude can implement one session at a time. Each unit says exactly what Claude writes, what you (Eshan) do, what data/credentials it needs, and the test that proves it's done. When we start a session, we point at a unit ID (e.g. "do 0.2") and build it.

**Division of labor, fixed for the whole project:**
- **Claude writes:** all code, schemas, configs, tests, eval harnesses, dashboards, and docs — delivered as files you commit to the repo.
- **You do:** account signups, API keys, running code on your machine / the Oracle VM, deploying, and reporting back outputs/errors. You are the hands on infrastructure; Claude cannot reach your VM.
- **Session protocol:** each session = one unit (occasionally two small ones). You paste back run results; Claude debugs against them. A unit isn't closed until its exit test passes on *your* machine, not just in Claude's sandbox.

**Stack (locked, from the free-path plan):**
- Language: Python 3.11+. Repo: single monorepo.
- Storage: Cloudflare R2 (raw scenes, chips) + DuckDB over Parquet (AIS, detections, tracks, graph edges). No other databases until a unit forces one.
- Spatial index: **H3, resolution 7** (~5 km cells) for joins; res 9 for fine matching. Decided now, used everywhere.
- SAR preprocessing: pyroSAR wrapping ESA SNAP.
- Orchestration: cron + Python entrypoints. No Airflow.
- Frontend: React + MapLibre GL, deployed on Vercel free tier, talking to a FastAPI backend on the Oracle VM.
- AOI v1: Arabian Sea + Indian west-coast EEZ — bounding box **5°N–25°N, 60°E–78°E**.

**Canonical provenance envelope (every record in every store carries this — non-negotiable from unit 0.1):**
```
source_id          # e.g. "copernicus-s1", "aisstream", "gfw", "ofac"
source_ref         # scene ID / message ID / list version
acquired_at        # when the phenomenon was observed (UTC)
ingested_at        # when we landed it (UTC)
pipeline_version   # git SHA of the code that processed it
confidence         # nullable float 0–1 where applicable
```

**Repo layout (created in unit 0.0):**
```
maritime_isr/
  ingest/        # connectors: one module per source
  process/       # SAR preprocessing, detection, tracks
  fuse/          # association engine, dark-vessel logic
  graph/         # ontology, edge store, event engine, decay
  rules/         # anomaly library
  eval/          # the permanent harness
  api/           # FastAPI serving layer
  ui/            # React/MapLibre (Vercel-deployable)
  inspect/       # throwaway inspection dashboards
  infra/         # cron entries, VM setup scripts, R2 config
  schemas/       # canonical schemas, versioned
```

---

## PHASE 0 — Pipes (free data landing automatically)

### 0.0 Repo skeleton + schemas
- **Claude writes:** repo scaffold above; canonical schemas (`schemas/`): position report, SAR scene catalog entry, detection, provenance envelope; H3 helper module; config loader (AOI, credentials via env vars).
- **You do:** create GitHub repo, commit, set up Python env locally.
- **Exit test:** `pytest` green on schema round-trip tests; `python -m maritime_isr.config` prints resolved AOI + checks env vars.

### 0.1 Copernicus Sentinel-1 GRD connector
- **Claude writes:** `ingest/copernicus.py` — STAC/OData query by AOI+window, scene catalog table (footprint, orbit, timestamp, status), resumable downloader to R2, idempotent (re-runs never duplicate).
- **You do:** free Copernicus Data Space account; R2 bucket + API token; run a 7-day backfill, then the 90-day backfill.
- **Data cost:** $0.
- **Exit test:** `maritime-isr ingest s1 --days 90` completes; catalog shows expected scene count for the AOI (sanity: revisit every 2–4 days → roughly 25–45 scenes/90 days per relative orbit crossing the box); re-run downloads nothing new.

### 0.2 SNAP preprocessing chain
- **Claude writes:** `process/s1_preprocess.py` via pyroSAR: orbit file → thermal noise removal → calibration to sigma-nought → terrain correction → output cloud-optimized GeoTIFF + updated catalog status. Memory-capped for the 24 GB VM.
- **You do:** install SNAP on the VM (Claude provides the install script); run on 3 test scenes, then batch.
- **Exit test:** 3 scenes preprocessed end-to-end; pixel values in plausible sigma-nought dB range over open water; catalog status transitions raw→calibrated. **(Known fiddliest unit — budget a full session for debugging SNAP.)**

### 0.3 AIS live connector
- **Claude writes:** `ingest/aisstream.py` — WebSocket consumer for aisstream.io filtered to AOI, NMEA/JSON parse to canonical position reports, dedup (MMSI+timestamp+position hash), hourly Parquet partitions, drop-rate counter, systemd service file so it survives reboots.
- **You do:** free aisstream.io key; run as a service on the VM; leave it running.
- **Exit test:** 72h continuous capture, <1% parser drop rate, dedup verified, partitions queryable in DuckDB.

### 0.4 AIS historical + GFW + registries
- **Claude writes:** `ingest/noaa_ais.py` (historical archives → same schema), `ingest/gfw.py` (API client for vessel tracks + published SAR detections over our AOI), `ingest/registries.py` (OFAC SDN, UN/EU consolidated lists, WPI ports — versioned snapshots, diff-on-refresh with as-of dates).
- **You do:** free GFW API key; run each once; add cron entries for registry refresh.
- **Exit test:** GFW SAR detections for our AOI queryable; sanctions tables carry as-of dates; a re-run produces a diff, not a duplicate.

### 0.5 Inspection dashboard v0 (the week-4 map)
- **Claude writes:** `inspect/v0/` — single self-contained HTML+JS artifact: AOI frame, live-ish AIS tracks from the Parquet store, S1 scene footprints painting in. Deliberately ugly.
- **You do:** open it, confirm the pipes are visibly flowing.
- **Exit test:** you can watch yesterday's AIS tracks and see which scenes cover them. **Phase 0 closed.**

---

## PHASE 1 — Eyes (SAR ship detection)

### 1.1 Land mask + CFAR detector
- **Claude writes:** `process/landmask.py` (GSHHG coastline → rasterized mask per scene with buffer; the unit most likely to make or break FP rates), `process/cfar.py` (two-parameter CA-CFAR on sigma-nought, contact candidates with position, backscatter stats, length/width estimate from the blob).
- **You do:** run over 5 preprocessed scenes; paste back contact counts and a few chips.
- **Exit test:** contacts over open water look ship-like; no contact floods from coastline/islets on visual review.

### 1.2 xView3 training data + chip pipeline
- **Claude writes:** `ingest/xview3.py` (download/organize), `process/chips.py` (chip extractor around CFAR candidates, label joiner against xView3 truth + GFW weak labels).
- **You do:** download xView3 (large — needs disk), run chip extraction.
- **Exit test:** labeled chip dataset materialized with train/val/test split by scene (never by chip — leakage).

### 1.3 CNN false-positive killer
- **Claude writes:** `process/classifier.py` — small CNN (ResNet-18-class) over chips, binary vessel/not-vessel, trained on xView3 + SSDD; inference wired after CFAR so the pipeline emits classified contacts.
- **You do:** train on the VM (CPU-feasible at this model size; overnight run) or a free Colab GPU session; paste metrics.
- **Exit test:** measurable FP reduction vs CFAR-only on held-out scenes.

### 1.4 Evaluation harness (permanent fixture)
- **Claude writes:** `eval/harness.py` — matched-detection P/R/F1, length-error, FP per 1,000 km²; runs against held-out xView3 + a 20-scene hand-label set (Claude builds the labeling mini-tool, you label ~20 AOI scenes with it — a few hours of your time, and it's the highest-value manual work in the project). Every model change runs it; results logged per git SHA.
- **Exit test (Phase 1 exit):** **F1 ≥ 0.75 on xView3 held-out**; scene-to-contacts fully automatic in <2h; inspection dashboard v1 shows boxed detections + live scoreboard.

---

## PHASE 2 — Memory (AIS track engine) — parallel with Phase 1

### 2.1 Track builder
- **Claude writes:** `process/tracks.py` — per-MMSI segmentation; handles MMSI reuse, simultaneous duplicate MMSIs (logged as spoof-tells, never discarded), impossible-speed splits; Kalman smoothing with **explicit uncertainty growth since last report** (this cone is Phase 3's core input).
- **Exit test:** 30-day track store; fragmentation <10% on a 50-track hand-checked sample (Claude builds the check UI, you eyeball).

### 2.2 Coverage model + gap classification
- **Claude writes:** `process/coverage.py` — empirical reception-density model of our free feeds on the H3 grid (where do we actually have ears); `process/gaps.py` — every track gap labeled coverage-gap / **intentional-silence** / unknown, with confidence. **Free-path honesty rule encoded here:** intentional-silence may only be asserted inside demonstrated coverage; offshore gaps default to unknown until the Spire connector is funded.
- **Claude also writes:** `ingest/spire_stub.py` — the satellite-AIS connector interface against the canonical schema, unimplemented body. Phase 3 codes against the interface.
- **Exit test:** every gap in every track labeled; coverage map renders on inspection dashboard.

### 2.3 Behavioral features
- **Claude writes:** `process/features.py` — speed/heading profiles, loitering episodes, drift signatures, port-call sequences (via WPI join), **rendezvous candidates** (two tracks <500 m at <2 kn).
- **Exit test (Phase 2 exit):** rendezvous detector precision >70% on a reviewed sample; features materialized per track — these become the Phase 4 fingerprint.

---

## PHASE 3 — Fusion (the product)

### 3.1 Association engine
- **Claude writes:** `fuse/associate.py` — per scene: gating (which tracks could physically be at each contact given uncertainty cone + max speed), scoring (position likelihood, SAR-length vs registry-length compatibility, heading consistency, historical cell presence), **global assignment via Hungarian/JV** across the whole scene (greedy manufactures phantom darks — banned). Output per contact: matched(conf) / ambiguous(top-k) / unmatched.
- **Exit test:** association accuracy ≥85% on xView3's non-ambiguous matched-AIS contacts.

### 3.2 Dark-vessel filter cascade
- **Claude writes:** `fuse/dark.py` — unmatched contact must survive: (1) not explainable by the 2.2 coverage model, (2) not in the static-object layer (self-building from repeated same-position detections — rigs, buoys, wrecks), (3) size above detectability floor with margin. Survivors get a dark score.
- **Precision policy hard-coded:** thresholds set so ≥7 of 10 alerts survive your review, recall sacrificed. Recall may only rise while measured precision holds.
- **Exit test:** two-week live sample; you review every dark alert in the disposition tool; **precision ≥70%**.

### 3.3 Nightly end-to-end + GFW cross-validation
- **Claude writes:** `fuse/pipeline.py` (cron: new scene → contacts → association → darks, zero touch), `eval/gfw_xval.py` (our darks vs GFW's over the AOI; every disagreement categorized bug-or-edge).
- **Exit test (Phase 3 exit = M3):** *"dark vessels off Porbandar last Tuesday"* is a reproducible query; inspection dashboard v3 plots dark candidates. **This is the first demo-able product output.**

---

## PHASE 4 — Graph (detections become intelligence)

### 4.1 Ontology v1 as edge tables
- **Claude writes:** `graph/ontology.py` + `schemas/graph_v1` — objects: Vessel, Organization, Person(minimal), Port, Voyage, Encounter, Detection, Track, Alert, Source. Edges: owned-by, operated-by, flagged-to, docked-at, met-with, detected-by, resolved-from, sanctioned-under, formerly-identified-as. **Every edge row: provenance + confidence + valid_from/valid_to.** DuckDB tables, versioned schema, migration test included (add an edge type with zero recompute).
- **Exit test:** migration test passes; no naked facts possible by schema constraint.

### 4.2 Identity persistence + fingerprints
- **Claude writes:** `graph/identity.py` — fold registries + AIS static messages + detection history into persistent Vessel entities; MMSI swaps / renames / reflags recorded as first-class events (formerly-identified-as edges); behavioral fingerprint from 2.3 features + detection history. **Graph starts accumulating this unit — it cannot be backfilled.**
- **Exit test:** every AIS-active AOI vessel is an entity with registry + track + detection history attached.

### 4.3 Confidence decay
- **Claude writes:** `graph/decay.py` — per-edge-type half-lives, nightly decay job, floor thresholds that retire edges.
- **Exit test:** synthetic stale edge decays on schedule; retired edges excluded from traversals.

### 4.4 Graph event engine + the canonical chain
- **Claude writes:** `graph/events.py` — detections/track-updates/registry-diffs emit events; rules subscribe and traverse (1–2 hops, cycle protection, traversal budgets). The canonical chain implemented as rule #1: *A met-with B → B owned-by O → O sanctioned-under OFAC → A risk jumps → alert with full evidence chain.*
- **Exit test (Phase 4 exit = M4):** chain fires on synthetic injects **and** at least one organic real case; inspection dashboard v4: click a dark vessel → its graph neighborhood.

---

## PHASE 5 — Judgment (anomaly library + feedback loop)

### 5.1 Alert framework + dispositions
- **Claude writes:** `rules/alerts.py` — alert = rule + evidence chain + confidence + disposition (confirm/dismiss/watch); dispositions stored as labels (**the proprietary feedback loop**); watchlists = saved graph queries as standing subscriptions.

### 5.2 Anomaly rules, shipped one at a time, each precision-gated
1. Dark vessel (graph-enriched) — exists, now consumes vessel history
2. AIS spoofing (impossible kinematics, duplicate MMSI, AIS-says-here/SAR-says-empty)
3. Dark rendezvous (SAR encounter, ≥1 party silent)
4. Loitering near sensitive geometry (geofence layer: cables, pipelines, ports)
5. Identity-change-then-anomaly sequence
6. Port-call risk propagation
- **Per rule:** Claude writes it, you review a live sample, precision measured before the next one ships.

### 5.3 Risk score
- **Claude writes:** `rules/risk.py` — composite score decomposable into its evidence chains by construction.
- **Exit test (Phase 5 exit = M5):** six rules live with measured precision; one detector retrained on your dispositions with a shown delta on the harness; weekly Indian-Ocean anomaly summary auto-generated.

---

## PHASE 6 — The Demo (product surface)

### 6.1 API layer
- **Claude writes:** `api/` — FastAPI: tracks, contacts, darks, entities, alerts, graph-neighborhood, replay-window endpoints; token auth; CORS for the Vercel origin.
- **You do:** run on the VM, expose via Cloudflare tunnel (free, no open ports).

### 6.2 Operational picture
- **Claude writes:** `ui/` — React + MapLibre: live AOI map (tracks, SAR contacts, dark candidates, alert markers), **time-scrubber replay** (the demo's killer feature), entity pages (identity history, fingerprint, detections, neighborhood, decomposed risk score).
- **You do:** deploy to Vercel, point at the tunnel.

### 6.3 Analyst workflow + reporting
- **Claude writes:** alert queue with triage states, evidence-chain renderer, one-click disposition (feeds 5.1), graph explorer, one-click incident report (imagery chip + track plot + chain + confidence statement) as PDF.
- **Exit test (Phase 6 exit = M6):** **you, unassisted, in under 5 minutes:** open picture → find last week's darks → open one → read why it's flagged → export the report. Demo runs live on a laptop, no rehearsed data.

---

## Deferred (stubs exist, no build until funded/justified)
- Spire satellite AIS (connector interface from 2.2) — the one paid feed
- VIIRS night-lights, Sentinel-2 optical (free — first Phase 7 connectors, prove the "connector not rewrite" claim)
- Commercial SAR tasking, RF geolocation (paid, spec-only)
- Bay of Bengal AOI, on-prem hardening

## Standing rules (every session, every unit)
1. Raw immutable; everything downstream regenerable from raw + git SHA.
2. Provenance envelope on every record; confidence + time-scope on every edge.
3. Harness runs on every model change; results logged per SHA. No exceptions.
4. High precision before high recall for anything you review.
5. New source = new connector to canonical schema; fusion core never learns source hacks. Any connector that forces a core change is a Phase 3 bug — fix before it calcifies.
6. Inspection dashboards get zero polish before Phase 6.

## Session sequence (suggested)
| # | Unit(s) | # | Unit(s) |
|---|---|---|---|
| 1 | 0.0 + 0.1 | 8 | 2.2 + 2.3 |
| 2 | 0.2 (SNAP — full session) | 9 | 3.1 |
| 3 | 0.3 + 0.4 | 10 | 3.2 + 3.3 |
| 4 | 0.5 + start 1.1 | 11 | 4.1 + 4.2 |
| 5 | 1.1 + 1.2 | 12 | 4.3 + 4.4 |
| 6 | 1.3 + 1.4 | 13–15 | 5.1–5.3 (one rule per sitting) |
| 7 | 2.1 | 16–18 | 6.1–6.3 |

~18 working sessions to M6, plus your labeling/review time and pipeline wall-clock (backfills, training runs, the two-week precision sample) running between sessions.
