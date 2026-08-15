# ARCHITECTURE.md — Maritime ISR

How data moves from raw sensor feeds to an evidence-backed dark-vessel alert, what
shape it takes at each step, and the contracts each layer relies on the previous
one to honor. Read `CLAUDE.md` first for the invariants; this file is the *how*.

---

## 1. The pipeline in one picture

```
                 RAW (immutable)            NORMALIZED             DERIVED
                 ───────────────            ──────────             ───────
 Sentinel-1 SAR ─► scene files (R2) ──► calibrated COG ──┐
                                        + scene catalog   │
                                                          ├─► CONTACTS ──┐
 [CFAR + CNN] over calibrated scenes ─────────────────────┘  (radar      │
                                                             blips)       │
                                                                          ├─► ASSOCIATION ─► DARK
 aisstream (live AIS) ─► hourly Parquet ─► canonical ──► TRACKS ──────────┘   (SAR↔AIS      CANDIDATES
 NOAA (historical AIS) ─► Parquet ──────► position     (per-vessel         match)              │
                                          reports       memory + gaps                          │
                                                        + features)                            ▼
 GFW (tracks + SAR dets) ─► Parquet ──────────────────────────────────────────────────►  OBJECT GRAPH
 OFAC / UN / EU / WPI ────► versioned registry snapshots ─────────────────────────────►  (vessels,
                                                                                          owners, edges)
                                                                                               │
                                                                                               ▼
                                                                                          ANOMALY RULES
                                                                                          + RISK SCORE
                                                                                               │
                                                                                               ▼
                                                                                          API ─► UI / REPORT
```

Three layers, Foundry-style, never mixed:
- **RAW** — exactly what the source gave us, landed immutably. Never edited.
- **NORMALIZED** — raw mapped into our canonical schemas, provenance stamped, H3
  cells computed. Regenerable from raw.
- **DERIVED** — contacts, tracks, associations, darks, graph edges, scores.
  Regenerable from normalized + code version.

Everything downstream must be reproducible from `raw + git SHA`. That is the whole
anti-corruption strategy.

---

## 2. The provenance envelope (on every record, every store)

Non-negotiable from unit 0.1. Every row in every table carries:

| Field | Meaning |
|---|---|
| `source_id` | which feed, e.g. `copernicus-s1`, `aisstream`, `noaa-ais`, `gfw`, `ofac` |
| `source_ref` | the source's own ID: scene ID, message ID, list version |
| `acquired_at` | when the phenomenon was **observed** (UTC) |
| `ingested_at` | when **we landed it** (UTC) |
| `pipeline_version` | git SHA of the code that produced this record |
| `confidence` | nullable float 0–1, where a confidence is meaningful |

`acquired_at` vs `ingested_at` matters: a SAR scene observed Tuesday but downloaded
Thursday is Tuesday's picture of the world, not Thursday's. Time-based logic keys
off `acquired_at`.

---

## 3. The spatial-index contract (the load-bearing one)

Every located record carries **two H3 cells**, computed at ingest by the single
shared helper in `schemas/`:

- **`h3_r7`** (res 7, ~5 km hex) — the **join key**. AIS positions, SAR contacts,
  and scene footprints all carry it. Phase 3 "which ships could be near this blip?"
  becomes: filter tracks whose recent cells intersect the contact's r7 cell and
  its ring of neighbors. A hash join, not a geometry scan.
- **`h3_r9`** (res 9, ~170 m hex) — **fine matching** when candidates are already
  gated down and we need tighter spatial discrimination.

**Contract rules:**
1. One helper computes cells. Do not scatter `latlng_to_cell` calls through
   modules — versions/params drift and joins silently miss.
2. h3 **v4** API (`latlng_to_cell(lat, lng, res)`). v3 names (`geo_to_h3`) are
   wrong; don't mix.
3. A footprint (scene coverage polygon) is represented as its **set** of r7 cells
   (H3 polyfill), so "which scenes cover this track?" is set membership.
4. Ring queries use `grid_disk` (v4) for neighbor expansion during gating.

If two records that should join don't, the first suspect is **inconsistent H3
computation**, not the matching logic.

---

## 4. Canonical schemas (versioned, in `schemas/`)

Field lists below are the semantic contract; the code is the source of truth for
exact types. All carry the §2 envelope in addition to what's shown.

### 4.1 Position report (AIS) — `schemas/position_report`
`mmsi`, `imo` (nullable), `lat`, `lon`, `sog` (speed over ground, knots),
`cog` (course over ground, deg), `heading` (deg), `nav_status`, `msg_type`,
`h3_r7`, `h3_r9`, `receiver_source`, `timestamp` (= `acquired_at`).
Both live (aisstream) and historical (NOAA) feeds normalize into **this** schema —
downstream never knows which feed a report came from.

### 4.2 SAR scene catalog entry — `schemas/scene_catalog`
`scene_id`, `orbit`, `relative_orbit`, `acquired_at`, `footprint` (polygon),
`footprint_h3_r7` (cell set), `mode` (IW), `polarizations` (VV/VH),
`status` (`raw` → `calibrated` → `detected`), `raw_uri` (R2), `cog_uri` (R2).
The `status` field is a state machine: preprocessing (0.2) flips raw→calibrated;
detection (1.x) flips calibrated→detected.

### 4.3 Detection / contact — `schemas/detection`
`contact_id`, `scene_id`, `lat`, `lon`, `h3_r7`, `h3_r9`, `backscatter_stats`,
`length_est_m`, `width_est_m`, `detector` (`cfar` | `cfar+cnn`), `confidence`.
A "contact" is a radar blip that looks ship-like. It is **not** yet a known ship.

### 4.4 Track — `schemas/track` (Phase 2)
`track_id`, `mmsi`, per-report state with **Kalman-smoothed** position and an
**explicit uncertainty cone that grows with time since last report**, segment
boundaries, and `gap_labels` (see §6). The uncertainty cone is Phase 3's core
input: it defines where a ship *could* be now given where it last was.

### 4.5 Graph edges — `schemas/graph_v1` (Phase 4)
Edge tables in DuckDB. Every edge row: `src`, `dst`, `edge_type`, `provenance`,
`confidence`, `valid_from`, `valid_to`. Object types and edge types listed in
`GLOSSARY.md` and the execution spec unit 4.1. **No naked facts** — the schema
itself forbids an edge without provenance/confidence/time-scope.

---

## 5. Storage layout

- **Raw scenes, chips → Cloudflare R2** (object store, zero egress). Immutable.
- **AIS, detections, tracks, graph edges → Parquet files, queried by DuckDB.**
  Columnar; DuckDB reads Parquet directly, no server.
- **Live AIS write path:** the aisstream consumer writes **hourly Parquet
  partitions to local disk**; a cron job mirrors **closed** partitions to R2
  (never the currently-open one — it's still being appended).
- **Backend selection:** `MISR_STORE_BACKEND` ∈ `local` | `r2` | `mirror`,
  resolved through the storage abstraction (`store.py` / `db.py` / `writer.py`).
  Modules ask the abstraction for a path; they never build one themselves.

Partition scheme keys on time and AOI so DuckDB queries prune to the relevant
files instead of scanning everything.

**Two stores that are not Parquet, and both catch people out.** The Sentinel-1
**scene catalog** is a real DuckDB table in `misr.duckdb`, not a Parquet view,
because its `status` column is mutable (cataloged → raw → calibrated →
detected). The **object graph** is SQLite (`graph.sqlite`). Anything reading
either goes through `db.py` / `graph_service.py` rather than the conformed glob.

**Derived tables landed by enrichment**, alongside the connector outputs, all
carrying the full provenance envelope and landed through the same
`landing.land_table` path:

| Table | Written by | Natural key |
|---|---|---|
| `sanctioned_vessel_matches` | `ingest/sanctions_match.py` (ADR-016a) | vessel_id + registry + ent_num + tier |
| `sar_imaging_opportunity` | `overpass.py` (ADR-026) | gap_event_id + scene_id |

---

## 6. How each layer consumes the previous one

- **Preprocessing (0.2) consumes raw scenes** → calibrated cloud-optimized GeoTIFF
  (COG) in sigma-nought (radar-brightness) units. Chain: apply orbit file → remove
  thermal noise → calibrate to sigma-nought → **terrain-correct (geocoding only,
  no radiometric flattening)** → write COG, flip catalog status. Memory-capped for
  the 24 GB VM (12 G JVM heap, bounded tile cache, 4 threads).
- **Detection (1.x) consumes calibrated scenes** → contacts. Land mask first
  (rasterized GSHHG coastline + buffer — the make-or-break step for false-positive
  rate), then CA-CFAR to propose contacts, then a small CNN to kill false positives
  (sea clutter, azimuth ambiguities, fixed infrastructure).
- **Track builder (2.x) consumes position reports** → tracks with uncertainty
  cones + gap labels + behavioral features.
- **Association (3.1) consumes contacts + track state at scene-acquisition time.**
  Gate (who could be here) → score (position likelihood, SAR-length vs
  registry-length, heading consistency, historical presence) → **global assignment
  (Hungarian/JV)**. Output per contact: `matched(conf)` / `ambiguous(top-k)` /
  `unmatched`.
- **Dark cascade (3.2) consumes unmatched contacts.** Survives only if: (1) not
  explainable by an AIS coverage gap at that time/place, (2) not in the
  self-building static-object layer (rigs, buoys, wrecks — accumulated from
  repeated same-position detections), (3) size above the detectability floor with
  margin. Survivors get a dark score and enter alerts.
- **Overpass geometry consumes the scene catalog + AIS gaps** → imaging
  opportunities (`sar_imaging_opportunity`). Sits outside the phase chain: it
  joins two *landed* sources and needs no preprocessing, no detector and no
  pixels. For each flagged gap it intersects the vessel's reachable region at
  each satellite pass time with that scene's footprint, grading the result
  `confirmed` / `partial` / `none` / `unknown`. **It is the only determination
  in the product that is ours rather than a third party's**, and it claims
  nothing beyond where a satellite was pointed — see ADR-026 and §7.
- **Graph (4.x) consumes everything** → persistent Vessel/Organization/Port
  entities and the edges between them, each provenance- and time-stamped.
- **Rules + risk (5.x) consume the graph** → anomaly alerts and a decomposable
  per-vessel risk score.
- **API/UI (6.x) consume the derived stores + graph** → the operational picture,
  replay, entity pages, and one-click reports.

### 6a. The serving layer as built (ADR-024/025)

`api/` is a **read layer with one write**, and the split inside it is worth
knowing before changing anything:

| Module | Owns |
|---|---|
| `reader.py` | a fresh DuckDB connection per request, with the conformed Parquet tables registered as temp views. A missing table is left unregistered so the endpoint degrades to empty rather than 500. |
| `service.py` | every domain query. This is where the two cross-cutting rules live once instead of per endpoint: the canonical vessel id is the graph node id, and **counts are split real/synthetic, never blended**. |
| `graph_service.py` | the SQLite object graph — neighbourhoods, risk, alerts, dispositions. |
| `report.py` | the incident report: `build_report()` assembles a payload, `render_html()` renders it. Kept apart so the JSON and HTML forms **cannot drift** — both are the one dict, so a caveat added once appears in both. |
| `models.py` | the response contracts. `Provenance` is a required nested model and `SplitCount` has no `total` field, so neither rule can be forgotten by accident. |
| `app.py` | routing, the shared-secret gate, and serving the built frontend so the demo is one Python process. |

Two things the serving layer must never do, both enforced by tests:

- **Never read `scenario_truth`.** It is the evaluation answer key; no serving,
  detection or scoring code may touch it (ADR-019 §d). The product must not show
  an operator the answers.
- **Never present a determination without its author.** GFW assessed the AIS
  gaps, the sanctions registries decided the designations, and *ours* is the
  identity match between them. Both the findings API and the exported report
  carry that attribution as a field, not as prose someone might drop.

**Cheap endpoints exist because request order is a feature.** A browser opens
about six connections per origin, so an eighth request waits. The map's time
scrubber took its window from `/api/stats`, which scans every event table,
groups the sanctions matches, counts scenes, measures length coverage *and*
walks the graph — and it was requested last, behind `/api/tracks` at a measured
3.06 s. `/api/corpus-window` returns the same two aggregates alone, is requested
first, and is cached for the session because a corpus window cannot change while
the process is up. **Any new view-critical field should get the same treatment
rather than being folded into `/stats`.**

**The graph has two read shapes and they are not interchangeable.**
`/api/vessels/{id}/neighbourhood` is seed-and-expand for one hull.
`/api/graph/all` is the whole web — every current relationship, most-connected
core first, capped at `FULL_GRAPH_MAX_NODES` (1,500). The cap is a rendering
limit, not a data limit: the real corpus graph is an estimated ~19,000 nodes,
and cytoscape's built-in `cose` layout was measured at **115 s** on 1,409 nodes
before the frontend moved to `fcose` (~6.5 s). Because the web is nearly always
a subset, the payload carries `total_nodes`, `total_edges` and `truncated`, and
**the UI is required to state them** — a partial web that looks whole is how a
viewer concludes the dataset is sparser than it is.

`/api/graph/all` and `/api/graph/seeds` both rank by **degree**, which is a
presentation choice and changes no stored fact. The focus node it returns
carries a `focus_basis` sentence for the same reason the findings table carries
`basis`: a centred node must not read as a verdict.

**Two read paths over the same events, deliberately.** `/api/events` returns
rows and is capped — so it reports `truncated` per kind with the true total,
because a silent cap once made the map draw a chronological prefix of the real
corpus and stop. `/api/events/density` aggregates per H3 cell **over every
matching row**, which is the only path that describes the whole corpus. Prefer
density for anything that makes a claim about volume or distribution.

---

## 7. Coverage model & the free-path honesty rule

We can only call a silence "intentional" where we can prove we'd have heard the
ship if it were broadcasting. `process/coverage.py` builds an **empirical
reception-density map** on the H3 grid from our own free feeds — where we
actually have ears. `process/gaps.py` labels every track gap as
`coverage-gap` / `intentional-silence` / `unknown`. **Intentional-silence may only
be asserted inside demonstrated coverage.** Offshore, beyond our receivers, gaps
are `unknown` until the paid satellite-AIS connector (Spire) is funded — its
interface (`ingest/spire_stub.py`) exists; its body does not. This is what keeps
the free-path prototype from crying wolf over the open ocean.

---

## 8. Revisit reality (why this is a "picture with gaps")

Sentinel-1 passes over the AOI roughly **every 2–4 days** (worse since the S1-B
loss). This is **not** real-time tracking — it's a persistent picture that updates
sparsely. The architecture treats sparse revisit as a *feature*: it forces the
track-persistence and confidence-decay machinery that a faster (classified-tempo)
feed would later exploit for free. Don't design as if scenes arrive continuously.

---

## 9. Deployment shape (target, not yet live)

FastAPI backend on the Oracle ARM VM, exposed via a Cloudflare tunnel (no inbound
ports opened). React + MapLibre frontend on Vercel's free tier, pointed at the
tunnel. The live AIS consumer runs as a **systemd service** so it survives reboots.
cron drives scheduled jobs (registry refresh, R2 mirroring, nightly fusion). None
of this is running yet — the VM is unprovisioned (see `STATE.md`).
