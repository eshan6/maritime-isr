# Maritime ISR

**A fusion intelligence platform for the Indian Ocean.** Maritime ISR ingests open
and commercial sensor feeds, resolves detections into a persistent object graph
of vessels and their relationships, and surfaces dark-vessel and
anomalous-behavior alerts with full evidence chains — built entirely on
free/open data first, designed so every paid or classified feed later is a
connector, not a rewrite.

> **All data in this repository is synthetic.** No live sensor feeds,
> subscriptions, or real vessel data are connected. Every metric below is
> measured on a deterministic synthetic suite with injected ground truth, and
> is a *proof of functionality*. Real-world accuracy will differ and must be
> re-measured on a deploy host. This honesty statement also appears inside the
> product surface itself.

## What's here (Phases 0–6)

| Phase | Milestone | What it does | Tests |
|---|---|---|---|
| 0 | Pipes | Immutable raw store, canonical schemas, provenance on every record | ✅ |
| 1 | Eyes | SAR ship detection (CFAR + classifier), eval harness | ✅ |
| 2 | Memory | AIS track engine: Kalman smoothing, gap classification, encounters | ✅ |
| 3 | Fusion | SAR↔AIS association, dark-vessel cascade — *the product* | ✅ |
| 4 | Graph | Object graph, ownership + sanctions, identity persistence, rule engine | ✅ |
| 5 | Judgment | Six precision-gated anomaly detectors, risk scoring, feedback loop | ✅ |
| 6 | Demo | Analyst product surface: map, replay, entity pages, alerts, reports | ✅ |

**84 tests pass across all six phases.** Pipeline version 0.7.0.

## Quickstart

```bash
# 1. install
pip install -r requirements.txt          # or: pip install -e .

# 2. generate synthetic inputs (all regenerable from code)
python tools/make_synthetic_scene.py
python tools/make_synthetic_feed_phase2.py
python tools/make_synthetic_scenes_phase3.py
python tools/make_synthetic_orgworld_phase4.py

# 3. run the pipeline, phase by phase
python tools/run_phase1_synthetic.py
python tools/run_phase2_synthetic.py
python tools/run_phase3_synthetic.py
python tools/run_phase4_synthetic.py
python tools/run_phase5_synthetic.py

# 4. build the analyst product surface (the laptop-rotation demo)
python tools/run_phase6_product.py
#    then open data/phase6_product_surface.html in any browser

# run the tests
pytest tests/
```

The CLI exposes the pipeline directly (`maritime-isr --help` after `pip install -e .`),
including `maritime-isr dark-vessels --near Porbandar`, `maritime-isr alerts`,
`maritime-isr risk --mmsi <n>`, and `maritime-isr graph-query --mmsi <n>`.

## Repository layout

```
maritime_isr/          the platform package
  config.py         AOI, grid, pipeline version, all tunable constants
  schemas/          canonical conformed schemas (append-only discipline)
  storage/          immutable raw store, parquet, catalog
  connectors/       Sentinel-1, AIS (terrestrial + satellite), registries
  detect/           Phase 1 — SAR ship detection
  tracks/           Phase 2 — Kalman track engine, coverage model, encounters
  fusion/           Phase 3 — SAR↔AIS association + dark-vessel cascade
  graph/            Phase 4 — object graph, ontology, identity, rules
  anomaly/          Phase 5 — anomaly library, risk scoring, feedback loop
  product/          Phase 6 — operational-picture snapshot builder
  eval/             per-phase evaluation harnesses + shared ledger
tools/            data generators + phase runners + product-surface assets
tests/            per-phase acceptance tests
data/             generated artifacts (git-ignored; see data/README.md)
```

---

# Detailed phase documentation

The sections below document each phase's design, acceptance criteria, and
honesty caveats in full.

# Maritime ISR — Phases 0–1: Data Foundations + SAR Ship Detection

Phase 0 (plumbing) verified complete — 10/10 acceptance tests. Phase 1 built on top,
pipeline version bumped 0.1.0 → 0.2.0. 23/23 tests total.

## Layout

```
maritime_isr/
  config.py                 AOI v1 (5–25N, 60–78E), H3 res-6 grid, pipeline version
  provenance.py             source/source_ref/acquired/ingested/version stamp — on every record
  tiling.py                 common H3 grid: detections, tracks, footprints all join on cell id
  schemas/                  canonical conformed schemas (AIS_POSITION, DETECTION, SANCTIONS_ENTRY)
  storage/raw.py            immutable content-addressed raw store; overwrite = exception
  storage/catalog.py        SQLite metadata catalog: artifacts, scene lifecycle, registry snapshots
  storage/conformed.py      partitioned zstd parquet writer, auto-registered in catalog
  connectors/sentinel1.py   Copernicus OData discover→download→calibrate; SNAP graph XML embedded
  connectors/ais.py         AIVDM 6-bit decoder (types 1/2/3/18/5), fragment reassembly,
                            checksum verification, sentinel-null handling, spoof-preserving dedup
  connectors/registries.py  GFW detections → canonical schema; sanctions snapshots with
                            SCD-2 diffing and as-of validity intervals
  cli.py                    backfill-scenes / ingest-ais / snapshot-sanctions / status
tools/make_synthetic_feed.py  72h synthetic AOI feed (dark vessel, spoofed MMSI, corruption)
tools/dashboard_template.html Week-4 inspection dashboard template
tests/test_phase0.py          10 acceptance tests
```

## Run

```bash
pip install pyarrow pandas h3 shapely pytest
python -m pytest tests/ -q                                   # 10 passed
python tools/make_synthetic_feed.py data/feed.nmea           # offline exercise
python -m maritime_isr.cli ingest-ais data/feed.nmea --receiver rx_mumbai
python -m maritime_isr.cli snapshot-sanctions data/sdn.csv --as-of 2026-07-15
python -m maritime_isr.cli status
```

On the deploy host (with Copernicus credentials + egress), cron runs:
```bash
python -m maritime_isr.cli backfill-scenes --days 90 --download   # initial backfill
python -m maritime_isr.cli backfill-scenes --days 4 --download    # daily keep-current
```

## Phase 0 exit criteria — status

| Criterion | Status |
|---|---|
| One command backfills 90d Sentinel-1, keeps current | ✅ code complete; **network-verified pending deploy** — the sandbox has no egress to catalogue.dataspace.copernicus.eu. Discover/download/parse are unit-tested against real OData response shape with injected fetch. |
| 30+ days continuous AIS, <1% parser drop, deduplicated | ✅ mechanism proven: 0.505% drop on a 31,907-sentence feed with injected corruption, every drop categorized. 30-day continuous accumulation starts when the live inlet is pointed at a real aggregator. |
| Every record answers where/when/which pipeline version | ✅ 100% provenance completeness measured on conformed output; first-class columns, not sidecar metadata. |
| Week-4 inspection dashboard (visibility rule 4) | ✅ `phase0_inspection_dashboard.html` — AOI frame, tracks + scene footprints painting in, time scrubber. |

## Design decisions Phase 2/3 will lean on

- **Same-MMSI/same-timestamp contradictions survive dedup** with receiver
  attribution and `n_receipts` multiplicity — the spoofing tell is preserved
  as evidence, not cleaned away.
- **Dark segments are visible in the conformed data** as inter-report gaps
  with the vessel displaced on reappearance — Phase 2 gap classification
  reads straight off this table.
- **Sanctions rows carry validity intervals** (SCD-2): DARK STAR delisted
  2026-07-15 closes with `valid_to`, so a Phase 4 sanctioned-under edge
  cannot fire after delisting.
- **One H3 grid across AIS, detections, footprints** — Phase 3 gating is a
  hash join on (cell, time bucket), widened by k-ring, no runtime geometry.

## Known gaps (deliberate, tracked)

1. SNAP calibration runs on the deploy host only (multi-GB Java toolchain);
   the graph XML is version-controlled here so sigma0 output is reproducible
   from raw + repo.
2. Type-5 static messages are decoded and counted but not yet folded into a
   vessel-statics table — that fold-in is the first task of Phase 2/4
   identity persistence; the decoder is done.
3. WPI port DB and UN/EU list parsers: same snapshot/diff machinery as OFAC,
   parsers to be added when real files are in hand (each is ~30 lines
   against `snapshot_registry`).
4. SQLite catalog → Postgres swap is behind one module boundary
   (`storage/catalog.py`); nothing else speaks SQL.


---

# Phase 1 — SAR Ship Detection (roadmap weeks 4–10)

## New layout

```
maritime_isr/detect/
  scene.py        Sigma0Scene container; GeoTIFF loader (deploy host, rasterio
                  guarded) and .npz loader — the detector never sees file formats
  landmask.py     GLOBE 1 km land mask on the scene pixel grid + seaward dilation
                  buffer (default 750 m): breakwaters/aquaculture/geolocation error
                  all die here. Moored-vessel invisibility inside the buffer is a
                  *declared* capability boundary (AIS-dense zone; Phase 2/3 carry it)
  cfar.py         Detector v1: CA-CFAR, alpha from design PFA under exponential
                  clutter, O(N) box-filter clutter mean, blob→Contact with position,
                  backscatter stats, length/width/orientation from second moments
  classifier.py   Detector v2: deterministic azimuth-ambiguity ghost pass (physics,
                  can't regress with a retrain) + gradient-boosted discriminator on
                  8 inspectable chip features. `.score()` is the only contract
  cnn.py          Deploy-host torch CNN (same .score() contract) + xView3 training
                  recipe — swap gated by the harness, invisible to the pipeline
  pipeline.py     scene → mask → CFAR → discriminator → conformed DETECTION parquet
                  → catalog PUBLISHED. Threshold recorded per run; Phase 3 can
                  re-cut the operating point without re-detection
maritime_isr/eval/
  harness.py      Permanent fixture: Hungarian matching (greedy banned, same reason
                  as Phase 3), P/R/F1, length MAE, FP per 1000 km²; every run
                  appended to the catalog eval ledger; regression gate vs prior best
  xview3.py       xView3-SAR label adapter incl. exclusion-radius convention — the
                  deploy host runs the *identical* harness against the benchmark
tools/make_synthetic_scene.py   physics-honest synthetic suite at real AOI coords
                  (gamma clutter ENL 4.4, wind field, ships 12–33 dB, ghosts at
                  −14.6 dB from bright parents, wave spikes, real Gujarat coastline)
tools/run_phase1_synthetic.py   train discriminator → process held-out → harness
                  → ledger → dashboard snapshot
tests/test_phase1.py            13 acceptance tests
phase1_inspection_dashboard.html  Week-10 inspection view (visibility track)
```

## Run

```bash
python tools/run_phase1_synthetic.py            # full exercise, writes ledger+snapshot
python -m pytest tests/ -q                      # 23 passed (Phase 0 + Phase 1)
python -m maritime_isr.cli process-scenes data/synthetic_scenes --model data/models/discriminator_v2.pkl
python -m maritime_isr.cli eval-report               # release-gate memory
```

Deploy host: cron `process-scenes` on the SNAP output directory after each
`backfill-scenes` cycle; train `detect/cnn.py` on xView3 + SSDD chips and swap the
model artifact **only** when the harness clears the regression gate.

## Phase 1 exit criteria — status

| Criterion | Status |
|---|---|
| ≥0.75 F1 on vessel detection | ✅ **0.894 F1 (P 0.913 / R 0.875)** on the held-out synthetic suite through the full automatic pipeline. xView3 number is **deploy-host pending** (no sandbox egress to the dataset); the adapter, harness, metrics, and gate that will produce it are built and tested — the benchmark run is an execution, not a build. |
| FP density: full-scene contact review <10 min | ✅ mechanism: 6.94 FP/1000 km² on a spike-dense synthetic suite ≈ 1 FP per 12×12 km scene. Honest caveat: suite FP density is deliberately adversarial and small-scene; the real-scene number gets measured on the first live S1 scenes and gated thereafter. |
| Fully automatic, scene→published <2 h, no human touch | ✅ `process-scenes` runs land-mask→CFAR→discriminator→conformed parquet→catalog PUBLISHED at ~0.1 s/synthetic scene; full S1 scene compute is minutes, leaving the 2 h budget to download+SNAP. Zero human touch, threshold recorded per run. |
| Week-10 inspection view (visibility rule 2) | ✅ `phase1_inspection_dashboard.html` — held-out scenes with boxed detections vs truth, live F1/FP scoreboard, eval ledger. |

## What the eval ledger already proves

Two rows in `eval_runs`: F1 0.791 → 0.894. The delta is the extended-target
self-masking fix (CFAR guard window was smaller than a 220 m ship; its own energy
inflated the clutter estimate). Found by the harness, fixed, regression-tested
(`test_extended_target_does_not_self_mask`). This is principle 3 working as designed.

## Decisions Phase 2/3 will lean on

- Detections land in the **same DETECTION schema as GFW's** — Phase 3's
  cross-validation ("where do we and GFW disagree") is a join, not a project.
- `score` + recorded publish threshold → Phase 3 re-cuts the operating point
  (high-precision alerts vs high-recall gating) without re-detection.
- Rigs/fixed infrastructure are *exclusion*, not FP — the repeated-same-position
  detections are exactly the seed data for the Phase 3 self-building static layer.
- Ghost suppression is physics-first: the deterministic pass survives any model swap.

## Known gaps (deliberate, tracked)

1. xView3 F1 measured on deploy host (data egress); harness identical, gate armed.
2. CNN (`cnn.py`) trains on deploy host; sandbox discriminator holds the contract.
3. Real-scene FP density and the ~20 hand-labeled AOI scenes (roadmap 1.3) are
   deploy-host work — the labeling UI is the Phase 2 inspection dashboard's job.

---

## Phase 2 — AIS Track Engine (pipeline 0.3.0)

Detections without tracks are photographs; tracks are the memory the graph is
built from. New package `maritime_isr/tracks/` + `maritime_isr/connectors/satais.py`.

### What was built
- **Track builder** (`tracks/builder.py`): multi-hypothesis segmentation per
  MMSI. Duplicate-MMSI simultaneous broadcast → two tracks + a merged
  `DUPLICATE_MMSI` episode (logged, never discarded). Isolated impossible
  jumps → `quality='outlier'` points kept on the main track. Silence >7 days
  → track break with `fragmented_from` lineage (MMSI-reuse guard).
- **Kalman + RTS smoother** (`tracks/kalman.py`): CV model on a local metric
  plane. `TrackState.uncertainty_radius_m(t)` = min(Kalman 95%, 60 kn cone) —
  the explicit uncertainty growth that is Phase 3's gating input.
- **Coverage model** (`tracks/coverage.py`): empirical, per (H3 res-4 cell ×
  hour × receiver class). Terrestrial coverage is a **ratio** — of every
  vessel heard at all in a neighborhood-window, the fraction heard
  terrestrially — so the satellite feed supplies negative evidence at
  receiver-ring boundaries. This is the honest map of where silence is
  meaningful.
- **Gap classifier**: every gap in every track gets exactly one label —
  `COVERAGE_GAP | SAT_PASS_GAP | INTENTIONAL_SILENCE` — with confidence.
  Precision-first conviction rule: INTENTIONAL_SILENCE requires ≥2 full
  satellite passes missed over a demonstrably covered path. Measured on the
  synthetic suite, every terrestrial-only conviction was a ring-edge false
  positive, so terrestrial evidence raises confidence but cannot convict
  alone. Cost: sub-2-pass darks deep inside terrestrial rings are missed —
  a stated recall sacrifice per the roadmap 3.3 posture.
- **Encounter detector** (`tracks/features.py`): the rendezvous primitive
  (<500 m, <2 kn, ≥15 min sustained), H3 res-7 bucket join. Behavioral
  features per track (loitering, drift, port calls) — the Phase 4
  fingerprint seed.
- **Satellite AIS connector** (`connectors/satais.py`): Spire-class,
  normalizes into the same canonical schema (`receiver='sat:<feed>'`), pass
  predictions feed the coverage model. HTTP path built and unit-shaped but
  **never exercised against the live API** — deploy-host task.
- CLI: `python -m maritime_isr.cli build-tracks [--sat-passes passes.json]`.

### Phase 2 exit criteria — status (SYNTHETIC 30-day suite)
| Criterion | Target | Measured | Status |
|---|---|---|---|
| 30-day continuous track store | — | 31 tracks, 30 days, conformed+cataloged | ✅ |
| Track fragmentation | <10% | **0.0%** (0/21 truth segments) | ✅ |
| Every gap labeled with type+confidence | 100% | 1,034/1,034 (2 INTENTIONAL, 6 COVERAGE, 1,026 SAT_PASS) | ✅ |
| Gap label accuracy vs injected truth | — | **100%** (3/3 truth dark/coverage periods) | ✅ |
| Rendezvous candidate precision | >70% | **100%** (3/3, 0 FP vs engineered negatives) | ✅ |
| Duplicate-MMSI spoof tell | logged | 1 merged episode, both tracks kept | ✅ |
| Feed drop rate (Phase 0 criterion held) | <1% | 0.454% | ✅ |

**Honesty box:** every number above is measured on the deterministic
synthetic 30-day feed (`tools/make_synthetic_feed_phase2.py`, seed 7) with
injected truth. No real AIS has flowed through this system. The synthetic
world is sparser than the real Arabian Sea and its receiver model is
idealized; real-feed precision will be lower and must be re-measured on the
deploy host before any number is quoted outward. The eval ledger
(`suite=phase2_tracks_synthetic`) gates regressions from here on.

### Run it
```
python tools/make_synthetic_feed_phase2.py   # regenerate feed + truth (deterministic)
python tools/run_phase2_synthetic.py         # end-to-end + eval + ledger + snapshot
python -m pytest tests/                      # 38 tests, Phases 0–2
```
Inspection dashboard: `data/phase2_inspection_dashboard.html` (self-contained).

---

## Phase 3 — Entity Resolution: The Fusion Core (pipeline 0.4.0)

"This is the product. Everything before feeds it; everything after consumes
it." New package `maritime_isr/fusion/`.

### What was built
- **SAR↔AIS association engine** (`fusion/associate.py`): per-scene
  probabilistic matching. Gate = min(Kalman 95% radius, effective-speed
  cone over ANCHOR staleness) — two hard-won lessons encoded: staleness
  measured against the track's end goes negative mid-track and manufactures
  phantom darks; a raw 60 kn cone on stale tracks gates half the ocean and
  manufactures ambiguity. Scoring: position + registry-length + historical
  presence, with a HARD length cut at 2.5σ (soft penalties let rigs match
  merchants). Assignment: global Hungarian/JV over the scene, never greedy.
  Verdicts: matched / ambiguous (top-k) / unmatched.
- **Gap-confirmed dark periods**: a vessel detected mid-dark-period
  correctly matches its own stale track — the right output is the match
  flagged `in_ais_gap` (SAR physically confirms the silent ship), not a
  phantom unmatched contact. Uses Phase 2's classified gaps directly.
- **Dark-vessel cascade** (`fusion/dark.py`): spoof-ambiguity → static →
  coverage → size → precision-gated score. Every suppression is a recorded
  verdict — "why is this NOT dark" is answerable. The static-object layer
  self-builds from recurring unmatched detections and CLAIMS its contacts
  before vessels can match them (with a matched-fraction guard so berthed
  transmitters never staticize). Hearability: terrestrial ratio model
  locally; satellite = feed-health (AOI-wide receipts ±3 h) × pass-in-
  lookback — local receipt density is NOT required, because a lone dark
  ship in empty ocean is the paradigm case, not an excuse.
- **THE demo query** (roadmap 3.5): 
  `python -m maritime_isr.cli dark-vessels --near Porbandar --date 2026-06-18`
  — port name or lat/lon, radius, `--all` for the why-not-dark view.

### Phase 3 exit criteria — status (SYNTHETIC suite: 35 scenes, 30 days)
| Criterion | Target | Measured | Status |
|---|---|---|---|
| Nightly run fully automatic | scene→dark, no human | one command, end to end | ✅ |
| Association accuracy (non-ambiguous) | ≥85% | **96.9%** (65 scored) | ✅ |
| Dark-vessel precision | ≥70% | **100%** (6 flagged, all true) | ✅ |
| Dark-vessel recall | reported | 75% of above-floor ghosts (1 below floor) | — |
| Gap-confirmed dark periods | — | 2/2 dark-window detections flagged | ✅ |
| Static layer catches installations | — | 100% of rig detections suppressed | ✅ |
| Clutter alerts leaked | — | 0 | ✅ |
| The demo query | reproducible | `dark-vessels --near ... --date ...` | ✅ |

**Honesty box:** all numbers from the deterministic synthetic suite
(feed seed 7, scenes seed 21) with injected truth: 2 ghost vessels, 3 rigs,
~126 clutter blobs, engineered dark windows. The synthetic ocean is sparser
and cleaner than the real Arabian Sea: real SAR length estimates are worse,
real AIS is filthier, and real dark-vessel precision **will be lower** and
must be re-measured on live data (roadmap 3.5's two-week live sample)
before any number is quoted outward. xView3 benchmarking — the roadmap's
association benchmark — requires downloading the dataset on the deploy
host; the harness interface (`maritime_isr/eval/xview3.py`) is ready for it.

### Run it
```
python tools/make_synthetic_feed_phase2.py      # world + truth (feed byte-identical to Phase 2)
python tools/make_synthetic_scenes_phase3.py    # SAR scenes + truth
python tools/run_phase3_synthetic.py            # nightly run + eval + ledger
python -m maritime_isr.cli dark-vessels --near "10.0,63.5" --date 2026-06-18 --radius-km 400
python -m pytest tests/                         # 52 tests, Phases 0–3
```
Inspection dashboard: `data/phase3_inspection_dashboard.html`.

---

## Phase 4 — Object Graph & Ontology (pipeline 0.5.0)

Where detections become intelligence. The moat is accumulated edges, not
algorithms — the graph starts accumulating the day this module first runs,
and edge history cannot be backfilled. New package `maritime_isr/graph/`.

### What was built
- **Ontology v1 as data** (`graph/ontology.py`): 13 node types, 9 edge
  types, registered in a versioned table — adding a type is an insert +
  version bump, not a schema change. Decay policy distinguishes STATE
  edges (owned-by rots without re-observation, half-life per type) from
  EVENT edges (met-with is a fact; facts recede in time-scope, they don't
  fade).
- **GraphStore** (`graph/store.py`): append-only assertions — no naked
  facts (provenance + confidence + time scope REQUIRED, writes reject
  otherwise), decay computed on read (idempotent, and yesterday's query is
  reproducible with yesterday's clock), as-of queries, latest-assertion
  resolution over full history.
- **Identity persistence** (`graph/identity.py`): the hull (IMO) is the
  entity; MMSI/name/flag are time-scoped identified-as edges. Registry
  diffs close old identities (→ the roadmap's formerly-identified-as, the
  laundering edge) and emit identity_changed events. `resolve_mmsi(m, t)`
  answers "which hull was broadcasting this at time t" — tracks land on
  the right vessel across an MMSI swap.
- **Event engine + rules** (`graph/rules.py`): ingest emits events; rules
  traverse with cycle protection and node budgets. The canonical chain
  works end to end — met-with → owned-by (≤3 hops) → sanctioned-under
  as-of event time → alert with the full evidence chain, confidence =
  weakest link. Second rule (sanctioned-owner dark-gap) proves the engine
  generalizes.
- CLI: `graph-query --mmsi N` (the entity page in text), `alerts`
  (evidence chains).

### Phase 4 exit criteria — status (SYNTHETIC world)
| Criterion | Target | Measured | Status |
|---|---|---|---|
| Every AIS-active vessel an entity w/ history | 100% | **100%** (31/31 tracks attached) | ✅ |
| Sanctioned-owner rendezvous chain | fires organically + on inject | 1-hop ✅ 2-hop ✅ inject ✅ | ✅ |
| Ontology migration, zero recompute | byte-identical prior rows | **PASS** (checksum-verified) | ✅ |
| Alert precision / recall vs expected | — | **100% / 100%** (3/3) | ✅ |
| Identity changes as first-class events | — | 3/3 (rename, reflag, MMSI swap) | ✅ |
| Cycle robustness | terminate, no alert | shell-loop survived, silent | ✅ |
| As-of sanctions | no retroactive conviction | expired decoy never alerted | ✅ |

**Honesty box:** the rendezvous alerts are "organic" in the precise sense
that the encounters were derived by the track engine from the simulated
feed — but ownership, sanctions, and registry history are scripted files
standing in for corporate registries and OFAC. Real ownership data is
dirty, multilingual, and evasive by design; entity matching there (name
matching alone) is a hard problem this phase has NOT solved — it consumed
clean keys. Confidence values on ownership edges are asserted, not
learned. Both are deploy-host-and-beyond work, and the graph's decay +
provenance machinery is built precisely so that dirty data degrades
gracefully instead of silently.

### Run it
```
python tools/make_synthetic_orgworld_phase4.py   # ownership + sanctions + registry history
python tools/run_phase4_synthetic.py             # build graph, fire rules, acceptance + ledger
python -m maritime_isr.cli alerts                     # evidence chains
python -m maritime_isr.cli graph-query --mmsi 419100004   # entity page: rename/reflag history
python -m pytest tests/                          # 65 tests, Phases 0–4
```
Inspection dashboard: `data/phase4_inspection_dashboard.html`.

---

## Phase 5 — Analytics, Alerting & Anomaly Library (pipeline 0.6.0)

Six precision-gated anomaly detectors over the object graph, a composite
risk score that decomposes into its evidence, and the analyst-disposition
feedback loop — the data asset a competitor cloning the architecture can't
replicate, because the labels are the asset. New package `maritime_isr/anomaly/`.

### What was built
- **Anomaly library** (`anomaly/library.py`): six detectors, each shipped
  behind its own precision gate (`config.ANOMALY_THRESHOLDS`), each
  emitting a scored, evidence-carrying, disposable alert:
  dark_vessel, ais_spoofing, dark_rendezvous, loitering_sensitive
  (geofence layer over cables/pipelines/exercise areas), 
  identity_then_anomaly (rename/reflag → dark within N days — the
  laundering sequence), port_risk_propagation.
- **Alert framework + disposition workflow** (`graph/store.py`): every
  alert carries anomaly_type, score, evidence chain, and a
  confirm/dismiss/watch disposition. Dispositions land in a ledger — the
  proprietary feedback loop's raw material.
- **Feedback loop** (`anomaly/feedback.py`): accumulated dispositions
  retune a detector's threshold, with the precision/recall delta MEASURED
  before and after. Simple and auditable by design (the labels are the
  moat, not the model). Guarded by a minimum-disposition count against
  overfitting to a handful of clicks.
- **Composite risk** (`anomaly/risk.py`): per-vessel score = weighted sum
  of anomaly history (decayed), sanction proximity (graph hops), flag
  opacity, and fingerprint deviation. `risk_score` returns the full
  decomposition — the score always equals the sum of its named
  components, enforced in test. Explainable by construction because an
  unexplainable score is unsellable to a navy and an insurer alike.
- **Weekly summary**: alerts-by-type + top-risk + disposition health,
  generated with zero human effort.
- CLI: `anomalies [--type]`, `risk [--mmsi N]` (with decomposition),
  `feedback [--retune TYPE]`.

### Phase 5 exit criteria — status (SYNTHETIC suite)
| Criterion | Target | Measured | Status |
|---|---|---|---|
| Six anomaly types live, each precision-gated | 6 | **6/6**, each with measured score+evidence | ✅ |
| Disposition feedback improves ≥1 detector | measured delta | dark_vessel **+14%** precision (86→100%), recall traded 100→67% | ✅ |
| Risk score decomposes into evidence | exact | score == Σ named components (test-enforced) | ✅ |
| Risk ordering sane | sanctioned>clean | dark+sanctioned merchant ranks #1 | ✅ |
| Weekly anomaly summary, zero human effort | auto | `phase5_weekly_summary.json` | ✅ |

**Honesty box:** the dispositions here are SIMULATED from ground truth to
exercise the loop end to end; on the deploy host they come from real
analysts, and that human-labeled stream IS the compounding asset this
phase stands up. Two detectors (dark_rendezvous, identity_then_anomaly)
needed scenario injects at the graph level because the byte-frozen Phase 2
feed — which Phases 2–4 depend on — happens not to contain a one-party-dark
rendezvous or a reflag-then-dark hull; the injects are truthful
constructions of those patterns, not fudged detector outputs. Risk weights
are policy, asserted not learned. Everything is synthetic-suite; real
precision per detector must be re-measured on live dispositions.

### Run it
```
python tools/run_phase5_synthetic.py          # detectors + dispositions + feedback + risk + summary
python -m maritime_isr.cli anomalies               # the alert queue
python -m maritime_isr.cli risk --mmsi 419100002   # the decomposed risk score
python -m maritime_isr.cli feedback --retune dark_vessel   # the loop closing
python -m pytest tests/                        # 76 tests, Phases 0–5
```
Inspection dashboard: `data/phase5_inspection_dashboard.html`.

---

## Phase 6 — Product Surface (pipeline 0.7.0)

The layer that turns the platform into the laptop-rotation demo and a
usable analyst tool. A single self-contained HTML product surface,
rendered in a deliberately plain white-and-blue enterprise SaaS style —
because credibility with a naval watch officer *is* the aesthetic. New
package `maritime_isr/product/`.

### What was built
- **Operational picture** (`Map`): live AOI map — AIS tracks, matched
  contacts, dark-vessel candidates, receiver rings — with a time-scrubber
  that replays any window (60 frames over 30 days), play button included.
  "Watch this ship go dark and meet that one."
- **Alert queue** (`Alerts`): triage table sorted open-first by score;
  every alert opens to its full evidence chain rendered as a readable
  path, with one-click confirm / dismiss / watch dispositions feeding the
  Phase 5 loop.
- **Entity pages** (`Vessels`): per-vessel identity history (current vs
  former MMSI/name/flag — the laundering view), behavioral fingerprint,
  risk score with its full decomposition as a stacked bar, alerts, and the
  graph neighborhood as a readable chain.
- **Risk board**: composite risk ranked, each row's bar decomposing into
  named contributions — the explainable-by-construction score, on screen.
- **Reports**: one-click incident report from any alert and vessel report
  from any entity page — imagery reference, evidence chain, confidence
  statement — downloadable. This is the artifact a watch officer forwards.
- **Provenance** page: the full data lineage and the synthetic-data
  honesty statement, in the product itself.
- **Snapshot builder** (`product/snapshot.py`): runs Phases 2-5 and
  serializes one operational-picture JSON; the surface renders entirely
  from it, so the demo runs on a laptop against the live synthetic backend
  with no rehearsed data.

### Phase 6 exit criteria — status
| Criterion | Target | Status |
|---|---|---|
| Non-builder can open picture → find last week's darks → open one → read why → export, unassisted | < 5 min | ✅ every step present & wired (test-enforced) |
| Full demo runs on a laptop against live backend, no rehearsed data | yes | ✅ one self-contained HTML off the snapshot |
| Operational picture with replay | yes | ✅ map + 60-frame time-scrubber |
| Entity pages with risk decomposition | yes | ✅ identity, fingerprint, decomposed risk, neighborhood |
| Evidence-chain view + one-click disposition | yes | ✅ |
| One-click incident report | yes | ✅ incident + vessel reports, downloadable |

**Honesty box:** the surface is styled to look like finished enterprise
software, but it is a proof-of-functionality skin over the synthetic
pipeline. Dispositions are session-only (no write-back to a live store in
the demo file). Reports export as text; the roadmap's PDF-with-SAR-chip is
deploy-host work. Map tiles need network (CARTO); everything else works
offline. All data is synthetic — stated on the Provenance page inside the
product.

### Run it
```
python tools/run_phase6_product.py            # build snapshot + product surface
# then open data/phase6_product_surface.html in any browser
python -m pytest tests/test_phase6.py          # demo-path completeness
```
