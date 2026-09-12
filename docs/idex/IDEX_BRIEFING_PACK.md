# Maritime ISR — Source Briefing Pack for the iDEX Submission

**Purpose of this file.** This is *not* a submission document. It is the complete
factual record of what the Maritime ISR project is, what has been built, what has
been measured, and what has deliberately not been built — assembled so that a
separate session can write the iDEX Annexure-1 and Annexure-2 from it without
needing access to the codebase.

**Target challenge:** iDEX **DISC 14, Challenge 82**, Indian Coast Guard —
integration of AI/ML into Coastal Surveillance Network (CSN) software.

**Repository:** `eshan6/maritime-isr` · package `maritime_isr` · version `0.7.0`.

---

# PART 0 — INSTRUCTIONS FOR THE SESSION USING THIS PACK

Read this part before writing anything.

### 0.1 The two documents to produce

| Document | Template | Required sections |
|---|---|---|
| **Annexure-1** | "Proposed Solution Template (Open Challenge)" | Cover fields: (1) Applicant Name, (2) Startup/MSME Name *(NA for individual innovator)*, (3) Challenge Title, (4) Project Duration in months, (5) Contact & Email Id. Then: **1.** Brief Summary of the proposed Solution (**≤ 250 words**) · **2.** Key Technology(s) Used (**not more than 6 in total**, ~50 key words) · **3.** Deliverable(s) — a table with columns *Sr No · Deliverable Name · Brief Description* · **4.** Proposed Timeline(s) in months, preferably phase-wise in tabular format. |
| **Annexure-2** | "Proposed Technical Solution (Detailed)" | **Technical Architecture & Approach** (core components, workflow, key technologies) · **Innovation** (unique features, proprietary methods) · **Implementation & Feasibility** (development, integration, scalability, deployment) · **Challenges & Mitigation** · **Visuals & Supporting Data** (diagrams and data) · **Any other relevant details**. |

Both templates say "preferably on Company's letterhead (if available)".
Annexure-1's own guidance for section 1 asks for: a concise overview, key features
and innovation, implementation and impact, and clarity/brevity with no jargon.

### 0.2 Non-negotiable honesty rules

The project operates under a written contract (`CLAUDE.md`) that these documents
must not breach. A submission that overclaims is worse than one that underclaims,
because the first technical conversation with the ICG will expose it.

1. **Distinguish "built to do" from "currently doing."** Code that exists and
   passes tests is not the same as a capability exercised on real data. Almost
   nothing in this project is in the second category.
2. **Every number in this pack is measured on a synthetic corpus** with injected
   ground truth, unless the line explicitly says "live". Label them. Never present
   a synthetic figure as an operational one.
3. **No dark vessel has ever been detected on real operational data.** The code to
   do it exists and is measured synthetically.
4. **The deployment host does not exist yet.** Nothing has run on the target VM.
5. **Area 3.3 (VHF ASR/NLP) is not built.** Describe it as a proposed build, never
   in the present tense.
6. **The project name is "Maritime ISR" / `maritime_isr`.** An older planning
   document uses the codename "Bastion"; that name is retired and must not appear.

### 0.3 Facts only the applicant can supply

- Applicant's legal name and whether applying as an **individual innovator** or a
  registered **startup/MSME** (changes cover field 2 and eligibility framing).
- Phone number.
- The **exact challenge title** as printed on the iDEX portal. The brief's own
  opening line is *"The following areas have been selected for integration of
  AI/ML in CSN software"*, so the title is close to *"Integration of AI/ML in
  Coastal Surveillance Network (CSN) Software"* — but confirm it.
- The full text of the "Existing Solution (if any)" row, which names the incumbent
  vendor. Only partially captured (see §1.8).

### 0.4 The numbering trap

**The challenge's own numbering (3.1–3.6) is NOT the "Area 1–6" numbering used
internally in this project's decision records.** They do not line up. Always use
the challenge's numbering in the submission. Mapping table in §1.7.

---

# PART 1 — THE CHALLENGE

## 1.1 Context given in the brief

The Indian Coast Guard operates the **Coastal Surveillance Network (CSN)** — a
chain of coastal radar stations with co-located electro-optical (EO) cameras and
VHF radio, feeding Regional Operating Stations (ROS), Regional Operating Centres
(ROC) and a Control Centre, where the data is fused and correlated on software
developed by an existing vendor.

The challenge brief states: *"The following areas have been selected for
integration of AI/ML in CSN software."* Six areas follow.

## 1.2 — 3.1 Classification of Radar Data *(verbatim)*

> Presently, Radar Data provides kinematics and positional information of the
> vessel. It does not classify the type of vessel, activities and interaction
> between vessels. Hence, AI/ML software is required for classification of type,
> activities and interactions between vessels at sea.

## 1.3 — 3.2 Automatic Detection, Classification by EO Sensor and Association with Radar/AIS Track *(verbatim)*

> Presently, the EO sensors are manual and required to be operated by
> watchkeepers. There is no automatic capturing of image of Radar/AIS track,
> classification of type & identity w.r.t. library, tagging to track and alerting
> on mismatch if any. Hence, AI/ML software is required for automatic capturing
> without operator intervention, tagging to track, building image library for
> classification of type, activities, interactions between vessels and alert on
> mismatch/interaction.

## 1.4 — 3.3 Multilingual ASR & NLP Solution for VHF Radio communication *(verbatim)*

> VHF Radio is installed on all Radar Stations and software has inbuilt feature
> to record the conversation. Presently, there is no software to covert the
> analogue voice to digital and text form. Hence, multilingual ASR & NLP Solution
> is required to convert all Indian languages (especially used in coastal states)
> voice to digital form in English which can be searched by key word to generate
> intelligence form open broadcast.

## 1.5 — 3.4 Predictive Analysis for AIS tracks *(verbatim)*

> Presently, AIS Data provides kinematics, positional information and some static
> information of the vessel as transmitted on AIS. However, the software is not
> able to classify authenticity of static information transmitted on AIS and
> activities of the vessels. Hence, AI/ML software is required for anomaly
> detection in static information and vessel activities based on track.

## 1.6 — 3.5 Retrieval Augment Generation (RAG) Application for PANS Data *(verbatim)*

> Presently, PANS data is being rendered in PDF, Word, XLS format on mail to ICG.
> The PANS data has vital information but could not be stored is structured
> database and fused to AIS database due to format. Hence, AI based RAG
> Application for converting Unstructured data (PANS and other sources) to
> structured database and fusing the information to AIS data for generating risk
> intelligence. Further, software should be compatible for integration of e-PANS
> from National Logistics Portal (Marine) of Indian Port Association as and when
> available.

*(PANS = Pre-Arrival Notification of Ships.)*

## 1.7 — 3.6 AI Based MDA Assistant for Helping Watchkeeper to Investigate Suspicious Vessels *(verbatim, partially occluded in source)*

> Presently, watchkeeper investigate the vessel over radio for suspicious
> movement. However, operator may miss certain activities of the vessel. Hence,
> AI based MDA Assistant may be desi[gned to surface] factors of suspicion for
> investigation of the ves[sel … and a record] of AI based investigation if
> required.

*(MDA = Maritime Domain Awareness. Two fragments were covered by a UI overlay in
the source screenshot; the sense is unambiguous but do not quote this one verbatim
in the submission.)*

## 1.8 Existing solution row *(incomplete)*

> The large volume of data generated by static sensors fitted all along the coast
> is being transferred to ROSs, ROCs and Control Centre for fusion and correlation
> on a software developed by …

The vendor name is cut off in the available screenshot. Worth obtaining: an
Annexure-2 integration section written against the named incumbent system is
stronger than a generic one.

## 1.9 Challenge-area → internal-record mapping

**Use the left column in the submission. The right column exists only so that a
reader of this repository can find the detail.**

| Challenge | Internal name | Decision record | Build status |
|---|---|---|---|
| **3.1** Classification of radar data | "Area 3" | ADR-033 | Built, measured (synthetic) |
| **3.2** EO auto-capture, classify, tag, alert | "Area 5" | ADR-037 | Loop built and measured; **no camera** |
| **3.3** Multilingual VHF ASR & NLP | "Area 6" | — | **Not built** |
| **3.4** Predictive analysis for AIS tracks | "Area 2" | ADR-032, ADR-035, ADR-042 | Built, measured (synthetic) |
| **3.5** RAG application for PANS data | "Area 4" | ADR-036 | Built, measured (synthetic); retrieval built, generation deliberately absent |
| **3.6** AI-based MDA assistant | "Area 1" | ADR-031, ADR-038 | Built, measured (synthetic) |

---

# PART 2 — WHAT THE PROJECT IS

## 2.1 One paragraph

Maritime ISR is a maritime intelligence, surveillance and reconnaissance
prototype whose original purpose is to find **dark vessels** — ships that switch
off their AIS transponder to hide — in the Arabian Sea and Indian west-coast
waters, by fusing satellite radar imagery (SAR), ship-broadcast position data
(AIS), coastal radar, and public registries (sanctions lists, port databases).
Its output is a ranked, evidence-backed list of vessels behaving suspiciously,
with a full chain of *why* attached to each one. Over 2026 it grew to cover five
of the six capability areas this ICG challenge names.

## 2.2 The product definition it was built against

> On a laptop, against a live backend with no rehearsed data, a non-engineer can
> open a map of the Indian Ocean, find last week's dark vessels, click one, read
> the plain-English reason it was flagged, and export a one-click incident
> report — in under five minutes.

That definition is met end to end on the synthetic corpus.

## 2.3 Area of interest

Latitude 5°N – 25°N, longitude 60°E – 78°E. The Arabian Sea and the Indian west
coast, from roughly the Gulf of Kutch down past Kochi.

## 2.4 Codebase scale (measured 2026-09)

| | |
|---|---|
| Python modules | **190** |
| Python lines | **60,305** |
| Test files | **46** |
| Test lines | **20,562** |
| Frontend source files | 17 |
| Frontend lines | 9,107 |
| Documentation lines (root markdown) | 12,920 |
| Architecture decision records | **42** (ADR-001 … ADR-042) |
| Full test suite, most recent recorded run | **967 passed, 37 skipped** |

Note on the suite: one later run recorded 966 passed / 64 skipped / 1 failed,
the single failure being an empty port gazetteer in the build sandbox, confirmed
pre-existing and environmental. Quote "≈ 970 tests" rather than a precise figure
if precision is not needed, and never present the suite as evidence the system
works on real data — it is evidence the code does what its authors intended on
fixtures.

---

# PART 3 — TECHNOLOGY STACK

## 3.1 Core stack with the reason each was chosen

| Choice | Reason on record |
|---|---|
| **Python 3.11+**, single monorepo | The whole pipeline is data plumbing plus ML, all Python-native. No polyglot overhead for a small team. |
| **DuckDB over Parquet**, not PostgreSQL | Columnar analytical queries over AIS / detection / track tables are the entire workload. DuckDB reads Parquet directly with **no server to run, back up, or licence**. A database server is operational tax until concurrent writers force it. |
| **Uber H3, resolution 7 for joins, resolution 9 for fine matching** | The load-bearing decision. See §4.2. |
| **pyroSAR driving ESA SNAP's `gpt` with XML graphs** — *not* the `snappy` Python bridge | `snappy` is notoriously brittle to install and version-pin. Driving SNAP's command-line tool with XML graphs is reproducible and scriptable. |
| **scipy `linear_sum_assignment`** (Jonker-Volgenant / Hungarian) | Global optimal assignment for radar↔AIS association *and* for EO camera cueing. Greedy matching is banned in both. |
| **scikit-learn** | Motion-feature classifiers for vessel type and activity. |
| **scikit-image** | SAR chip processing and CFAR support. |
| **Pydantic v2** | Canonical record schemas with validation at the landing boundary. |
| **shapely + pyshp** | Geometry and reading published maritime-boundary shapefiles. `pyshp` is pure Python, so installing on a Windows laptop stays a download rather than a compiler problem — no GDAL, no fiona, no geopandas. |
| **FastAPI + uvicorn** | Serving layer. Optional dependency group, so the ingest path carries no web dependency. |
| **React + MapLibre GL** | Operator surface. MapLibre is open source with no key requirement. |
| **cron + Python entry points**, not Airflow | Orchestration here is "run these scripts on a schedule". Airflow is a server, a database and a UI to operate for no benefit at this scale. |
| **Tesseract** (via `pytesseract`) | OCR for scanned arrival notifications. Optional and *separately reported*, because it wraps a binary pip cannot install. |
| **python-docx / openpyxl / pypdf / reportlab / pillow** | Reading and generating the document formats the PANS requirement names. Optional group; the extractor **degrades per format rather than per install** — a machine without one reader still reads the others and *says which it could not open*. |

## 3.2 Deployment and hosting (target shape)

| Component | Target |
|---|---|
| Compute | Oracle Cloud always-free ARM VM, 4 cores / 24 GB RAM — **$0 standing cost**, enough RAM for SNAP. **Not yet provisioned.** |
| Object storage | Cloudflare R2 — zero egress fees, which matters because SAR scenes are large and get re-read. |
| API exposure | Cloudflare tunnel — exposes the VM API without opening inbound ports. |
| Frontend hosting | Vercel free tier. |
| Live AIS capture | systemd service, so it survives reboots. |
| Storage backend | Environment-selected: `local` \| `r2` \| `mirror`. Path resolution goes through a storage abstraction, never hard-coded paths. `mirror` writes locally and copies closed partitions to object storage. |

**For an ICG deployment this matters:** the whole stack runs on-premise with the
backend set to `local`. No component needs outbound internet at run time except
optional satellite-catalogue refresh, which degrades cleanly offline. There is no
database server and no cloud dependency, so data sovereignty is a configuration
value rather than an architectural problem.

## 3.3 Six technologies for Annexure-1's "not more than 6" limit

Suggested consolidation (each bundles related libraries under one honest heading):

1. **H3 hierarchical hexagonal spatial indexing** (res 7 ≈ 5 km, res 9 ≈ 170 m)
2. **DuckDB over columnar Parquet** — zero-server analytical lakehouse with an immutable raw tier
3. **Global assignment (Jonker-Volgenant / Hungarian) with Kalman track filtering**
4. **Motion-only ML classifiers with confusion-matrix-derived class vocabularies** (scikit-learn)
5. **Multi-format document extraction (PDF text layer, Tesseract OCR, DOCX/XLSX) behind one format-blind extractor, with retrieval-grounded question answering**
6. **CFAR + CNN ship detection on Sentinel-1 SAR via pyroSAR/ESA SNAP**

---

# PART 4 — ARCHITECTURE

## 4.1 The three-layer pipeline

```
        RAW (immutable)              NORMALISED                 DERIVED
        ───────────────              ──────────                 ───────

 CSN coastal radar ──► radar track reports ──┐
 AIS dynamic (msg 1/2/3) ──► position ───────┤
 AIS static  (msg 5)     ──► voyage          │
                             declarations    ├──► TRACKS ──┐
 Sentinel-1 SAR ──► scene files ──► calibrated│   (per-hull │
                    (immutable)    sigma0 COG │    memory,  │
                                   + CFAR     │    gaps,    │
                                   + CNN ──► CONTACTS       │
                                              │             ├──► ASSOCIATION ──► DARK-VESSEL
 PANS attachments ──► passages ──► arrival ───┤   (global assignment)   CASCADE
 (PDF/scan/DOCX/XLSX)              records    │                              │
 e-PANS portal feed ──────────────────────────┤                              │
 EO station cameras ──► captures bound ───────┤                              ▼
                        to a track            │                        OBJECT GRAPH
 OFAC / UN / EU / WPI ──► versioned ──────────┘                   (vessels, orgs, contacts,
                          snapshots                                zones — every edge carries
 VHF audio [NOT BUILT] ──► transcripts ────────────────────────►   confidence + valid_from/to)
                                                                             │
                                                                             ▼
                                                                   ANOMALY RULES + RISK SCORE
                                                                             │
                                                                             ▼
                                                          RANKED VESSELS OF INTEREST
                                                        (decomposed score · evidence · narration)
                                                                             │
                                                                             ▼
                                                      API ──► OPERATOR UI + INCIDENT REPORT
```

- **RAW** — exactly what the source gave, landed immutably, never edited.
- **NORMALISED** — raw mapped into canonical versioned schemas, provenance
  stamped, H3 cells computed. Regenerable from raw.
- **DERIVED** — contacts, tracks, associations, dark verdicts, graph edges,
  scores. Regenerable from normalised plus the code version.

**Everything downstream is reproducible from `raw + git SHA`.** That is the whole
anti-corruption strategy: when a defect is found, the fix is a re-run, not
archaeology across a mutated database.

## 4.2 H3 — the one decision everything depends on

Every located record — AIS fix, radar contact, SAR detection, scene footprint,
camera arc, port polygon, zone — is stamped at ingest with the H3 cell it falls
in, at **resolution 7** (hexagons ≈ 5 km across) and **resolution 9** (≈ 170 m),
using one shared helper.

Why it matters more than any other single choice: the Phase-3 question *"which
ships could this radar blip be?"* is otherwise a runtime geometry problem whose
cost grows with the square of the picture. On a shared grid it becomes a **hash
join** — same cell, candidate, done.

The contract: anything landing a located record computes its cells through the
shared helper. Hand-rolled per-module calls are a defect, because two versions of
the index **silently miss matches** rather than failing loudly. (The project is
pinned to h3 v4 and uses the v4 `latlng_to_cell` API; v3 names are a known
silent-breakage source.)

## 4.3 The provenance envelope — on every record, in every store

| Field | Meaning |
|---|---|
| `source_id` | which connector produced it |
| `source_ref` | the identifier *at the source* — message, scene, document, portal record |
| `acquired_at` | when the source observed it |
| `ingested_at` | when we landed it |
| `pipeline_version` | the exact git SHA that processed it |
| `confidence` | what the producing method is entitled to claim (nullable) |

**If provenance cannot be stamped, the record is not landed.** The reason is
operational: the product *is* trust. A flag an analyst cannot trace to a source
and a code version is worthless, and worse than worthless at the point where a
boarding has to be justified.

## 4.4 Attribution — origin vs derivation (ADR-038)

A separate layer answers *"and who says that?"*:

- **`origin`** — the outside body a fact came from (a registry, an agency, a sensor).
- **`derivation`** — what this system then did to it.

Attribution is attached **at serialisation**, so naming a source an operator could
independently check is a structural guarantee rather than a convention every
developer has to remember. **This system's own storage is never a source** —
`graph / events` names a table in this repository, not an authority.

## 4.5 Hard invariants

1. **Provenance envelope on every record, no exceptions.**
2. **Raw is immutable; everything downstream is reproducible.**
3. **Confidence on every assertion, time-scope on every edge.** In the object
   graph no naked facts: every edge carries provenance, a confidence, and
   `valid_from` / `valid_to`. Ownership ends; sanctions have as-of dates; a fact
   true last year may be false now. A stale fact asserted as current is how a
   system embarrasses itself in front of an operator.
4. **High precision before high recall, for anything analyst-facing.** Tuned so
   that of every 10 alerts **≥ 7 survive human review**, even if that means
   missing half the real dark vessels. Recall rises only as measured precision
   holds. This is stated product policy (ADR-004), not a tuning accident.
5. **Every source is a connector, never a core change.** New data source = new
   module in `ingest/` mapping into the canonical schema. The fusion core must
   never learn a source-specific hack. Any connector that forces a change in the
   core is a design bug **in the core**.
6. **Synthetic ≠ real.** Never quote a synthetic number as a real one.

## 4.6 Banned approaches (and why) — useful as Annexure-2 "innovation" material

| Anti-pattern | Why it is wrong here |
|---|---|
| **Greedy per-contact matching in association** | Matching each radar contact to its nearest track one at a time double-assigns tracks and **manufactures phantom dark vessels**: a real ship gets "used up" by a nearby contact, leaving her true contact looking unmatched. Global assignment across the whole scene instead. |
| **Radiometric terrain flattening in SNAP over ocean** | Triggers SNAP's DEM-tile download, which hangs. Over open ocean there is no terrain to flatten. Terrain correction here is **geocoding only**. |
| **Asserting "intentional silence" offshore** | A ship with no AIS where there is **no receiver coverage** is not dark — we cannot hear her. Offshore gaps default to `unknown`. Calling an out-of-coverage gap "dark" is a false positive by construction. |
| **Splitting an ML dataset by chip, or by track** | Chips from one scene, or tracks from one hull, leak across the split and inflate metrics. Split by **scene**, and by **hull**. |
| **Treating an MMSI collision or impossible-speed jump as a data error** | Two ships broadcasting one MMSI, or a ship teleporting, is a **spoofing tell**. Log it as a first-class signal, never silently drop it. |
| **Backfilling the object graph later** | The graph accumulates edge history from the day it turns on and **cannot be backfilled**. This is why it turns on early even at prototype accuracy. |

---

# PART 5 — REPOSITORY LAYOUT AND MODULE INVENTORY

```
maritime_isr/
  ingest/      one module per source; lands raw + normalises to canonical schema
    copernicus.py     Sentinel-1 GRD scene catalog + download
    aisstream.py      live AIS websocket consumer (dynamic + ShipStaticData/msg 5)
    noaa_ais.py       historical AIS (parked)
    gfw*.py           Global Fishing Watch events, vessels, SAR presence
    registries.py     OFAC / UN / EU sanctions + WPI ports, versioned snapshots
    sanctions_match.py  identity matching against designations
    ofac_lookup.py    OFAC-specific resolution
    radar.py          coastal radar track reports
    zones.py          maritime zone connector
    landing.py        the landing boundary: provenance stamping + validation
    checks.py         AOI bounds, schema, envelope checks
    pans/             arrival-notification connector (ADR-036)
      readers.py        one per format: PDF, scanned PDF, DOCX, XLSX, electronic
      extract.py        the FORMAT-BLIND extractor over Label:value passages
      kinds.py          what kind of paper this is — read off the page, not the filename
      resolve.py        attach to a hull, or REFUSE — never fuzzy-match
      land.py, service.py
  process/     SAR preprocessing
    s1_preprocess.py  the SNAP chain via pyroSAR gpt XML graphs
    snap_doctor.py    environment check
    validate_sigma0.py  dB-range sanity (the unit's exit test)
  detect/      SAR ship detection
    landmask.py, cfar.py, cnn.py, classifier.py, scene.py, pipeline.py
  tracks/      track engine and motion analytics
    builder.py        AIS positions -> per-vessel tracks
    kalman.py         filtering + uncertainty radius (feeds association gating)
    coverage.py       receiver coverage model + gap classification
    features.py       behavioural features
    activity.py       activity classification FROM MOTION ONLY
    vessel_type.py    vessel type FROM MOTION ONLY (radar and AIS alike)
    interactions.py   company, shadowing, converging-and-holding, transfer pattern
    projection.py     forward projection + uncertainty cone
    route_prior.py    route-aware flow field (ADR-042)
    prediction_eval.py  the projection's own measurement harness
  fusion/      THE fusion core — must stay source-agnostic
    associate.py      SAR/radar <-> AIS association, global assignment
    dark.py           the dark-vessel filter cascade
    radar_ais.py      radar-specific correlation path
    contact_profile.py  what an uncorrelated contact IS (profiles, never re-decides darkness)
  anomaly/     the detector library plus pure rule modules
    library.py        14 detectors
    identity.py       is her declared identity self-consistent
    voyage.py         does her declared destination and ETA match her track
    paperwork.py      does her arrival notification match her track
    imagery.py        does a camera image agree with her declared type
    risk.py           risk score + weights
    feedback.py       dispositions retune detectors
  eo/          the electro-optical loop (ADR-037)
    cue.py            THE scheduler — which track a camera sees, when, and why
    camera.py         geometry + image-quality model
    conditions.py     light and weather
    capture.py        the image bound to a track, landed as evidence
    classify.py       swappable classifier + reference library
    appearance.py     the numeric stand-in for pixels
  assistant/   the MDA assistant (ADR-031)
    build.py          assembles the ranked Vessel of Interest
    catalog.py        the factor catalog: 6 families, 23 factor kinds, stated weights
    score.py          noisy-OR + exact log-space decomposition
    narrate.py        sentences a duty officer can read aloud on a radio call
    recommend.py      next actions with computed feasibility and stated capability
    qa.py             grounded question answering, closed intent set, no generation
    attribution.py    origin vs derivation
    model.py          the object
  graph/       object graph
    ontology.py       node and edge types as DATA (registered rows, versioned)
    store.py          edge store
    from_landed.py    build edges from landed tables
    identity.py       identity persistence + fingerprints
    rules.py          traversal rules (ownership chains, designation propagation)
    ingest.py
  zones/       maritime zone layer (ADR-030)
  scenario/    the synthetic world generator (writes the corpus AND the answer key)
  eval/        the permanent evaluation harness
    harness.py, fusion.py, tracks.py, anomaly.py, graph.py, xview3.py
  api/         FastAPI serving layer
    service.py, analysis.py, graph_service.py, reader.py, report.py, models.py
  inspect/     throwaway inspection dashboards (ugly on purpose)
  infra/       cron entries, VM setup scripts, R2 config
  schemas/     canonical versioned schemas + the shared H3 helper
  storage/, store.py, db.py, writer.py   storage abstraction
  baselines.py   per-area behavioural baselines (ADR-032)
  coastline.py   distance from land, from a shared 1 km mask (NOT bathymetry)
  ports.py       the one port gazetteer (ADR-023)
  overpass.py    satellite imaging opportunities over AIS gaps (ADR-026)
  h3util.py      THE shared H3 helper
  provenance.py  the envelope
frontend/      React + MapLibre — six tabs
```

**Note for the writer:** an empty `fuse/` package also exists and is unused; and
`fusion/contact_profile.py` sits in `fusion/` despite profiling rather than
fusing, which is recorded as an open question. Neither belongs in a submission.

---

# PART 6 — CANONICAL SCHEMAS AND THE OBJECT GRAPH

## 6.1 Record schemas (Pydantic, versioned — `SCHEMA_VERSION = "v1"`)

| Schema | What it holds |
|---|---|
| `PositionReport` | AIS/radar dynamic position: MMSI, lat/lon, SOG, COG, heading, timestamp, H3 res-7 and res-9 cells, full provenance envelope. |
| `VoyageDeclaration` | AIS message 5: declared destination, ETA, draught, ship type, dimensions, call sign, IMO. **A table of its own, not columns on the position report** — a vessel sends a hundred positions per voyage declaration; folding them together either repeats the declaration a hundred times or leaves 99% of rows carrying a null nobody can interpret. |
| `ExtractedField` | One value read from a document, with its passage, locator, method and earned confidence. |
| `ArrivalNotification` | A PANS / arrival report / crew list / cargo manifest / port clearance as structured fields, each an `ExtractedField`. |
| `RadarTrackReport` | A coastal-radar track report: position, kinematics, measured length, station, quality. |
| `SceneCatalogEntry` + `SceneStatus` | Sentinel-1 scene metadata and its processing state. |
| `Detection` + `DetectionMethod` | A SAR contact: position, length, confidence, how it was found. |

## 6.2 Object graph ontology

**The ontology is data, not code** — node and edge types are registered rows in a
versioned ontology table, so adding a type is an insert plus a version bump, not a
deployment. Edges are typed as either **state** (asserting a condition over a time
span) or **event** (a thing that happened at a moment).

Edge types include: `owned-by`, `operated-by`, `managed-by`, `flagged-to`,
`docked-at`, `met-with`, `detected-by`, `correlates-with`, `resolved-from`,
`sanctioned-under`, `identified-as`, `entered-zone`, `deviated-from-lane`,
`anchored-outside-limits`, `loiter-in-zone`, `depicts`, `captured-by`.

Node types include vessel, contact, detection, track, organization, person, port,
flag_state, sensor, identity, zone, eo_capture.

**Every edge carries confidence, provenance, and validity dates.** Confidence
decays with age on a 30-day half-life for risk contributions.

**A defect worth citing as evidence of the discipline:** the corpus asserts
`managed-by` at confidence 0.55 with the note *"inferred from a shared
correspondence address — a candidate, never a finding"*. The graph builder was
silently coercing all 74 of these to `owned-by` — the strongest claim the ontology
can make about a hull — and rebuilding the properties from the coerced kind, so
the original was unrecoverable. That is the system overclaiming about its own
data. Fixing the ontology then made the edges *disappear* instead, because
ontology seeding only ran on an empty table and rejected edges were being
swallowed by a caught exception. **An entire class of relationship vanished and
the run said nothing.** Both fixed: seeding is additive, rejected edges are
reported. Verified after: 74 `managed-by` edges land, `owned-by` drops from 163
to 89.

## 6.3 Maritime zone layer (ADR-030)

Zone kinds: `eez` (200 nm), `contiguous_zone` (24 nm), `territorial_sea` (12 nm),
`imbl` (a **line**, not an area), `port_limit`, `anchorage`, `oil_terminal`
(terminals and single point moorings), `shipping_lane`, `sensitive_area` (cable
approaches, exercise areas, infrastructure), `geofence` (operator-drawn).

Zone entry and exit are **landed events**, not runtime computations. Each kind has
a defined render order so a new kind cannot be added without someone deciding
where it sits visually.

**The four statutory limits are absent by decision** and arrive only through the
connector — the project refuses to invent a maritime boundary. There is also an
explicit refusal to treat crossing the IMBL as ground-truth wrongdoing: the line
is disputed, and a corpus that labelled a crossing as an offence would bake a
contested legal claim into the answer key, where measurement would then reward a
detector for making it.

---

# PART 7 — CAPABILITY BY CHALLENGE AREA

> Every figure in this part is measured on the **synthetic corpus** unless a line
> says "live". None is a measurement on operational ICG data.

## 7.1 — 3.1 Classification of Radar Data

**Modules:** `tracks/vessel_type.py`, `tracks/activity.py`,
`tracks/interactions.py`, `fusion/contact_profile.py`. **Record:** ADR-033.

### Approach

- Every feature is **motion**: speed distribution, turn rate, course persistence,
  stop-and-go structure, sinuosity, dwell geometry.
- **No feature reads an identifier, a message rate, or a sensor name.** A test
  asserts the feature vector is **byte-identical** for the same track presented as
  AIS and as radar. This is why a model trained on AIS-labelled tracks can be
  applied to unlabelled radar tracks — and it is the structural answer to the
  requirement that the same behaviours be recognisable from either sensor.
- **Split by hull, never by track.** Tracks from one vessel on both sides of a
  split let the model memorise her instead of her class.

### The published vocabulary is derived, not declared

A hand-written list of coarse classes is a claim about the world; what the system
is entitled to make is a claim about its own model. `confusable_groups` reads the
measured confusion matrix and merges any pair mistaken for each other more than
**25%** of the time. If a later feature genuinely separates two classes, the
groups shrink on their own and nothing needs editing.

### Measured, 209 tracks, held-out hulls

| | |
|---|---|
| Fine-grained accuracy | **65%** |
| **Coarse (derived vocabulary) accuracy** | **90%** |
| Derived vocabulary | `fishing`, `general_cargo`, `merchant`, `reefer` |
| Provably inseparable from motion | `[Aframax, bulker, product_tanker]`, `[Suezmax, VLCC]` |
| Fishing | 14/16 — a third of the speed and three times the turn rate |
| Reefer | 5/5 |

**The tanker/bulker/cargo cluster is not separable from motion and never will
be**, because a laden bulker and a laden product tanker at 13 knots on a
great-circle course are doing the same thing. Saying so *is* the product.

### Interactions

Four kinds: **company**, **shadowing**, **converging-and-holding**, **transfer
pattern**.

The separation threshold was set by measurement, not taste: across two corpus
draws, eleven coincidental close pairs were observed and **none closer than
5,337 m**, while three authored relationships all sat **inside 4,245 m**. Claims
are therefore made only inside **2.5 nm**. A fishing fleet transiting together is
a formation by every naive geometric test and by no useful one.

On the base corpus at the 120-minute gate the interaction detectors produced
**0 findings — reported, not tuned away.** After a purpose-built group of sixteen
hulls writing the situations the corpus never contained, `vessel_interaction`
fires 6 times. `transfer_pattern` is **unvalidatable on this corpus** because the
scenario's counterparties are dark by design.

### Contact profiling

A radar contact that correlates to nothing is the case the requirement most needs
answered, and *"unidentified contact"* is the least useful thing to put in front
of a watchkeeper. The profiler returns an inferred type, an inferred activity and
the zone she is in, each with its own confidence. Produced on all 8 dark contacts
in the corpus.

**It profiles and never re-decides darkness** — the dark cascade owns that
verdict. A second module quietly re-deciding it would be an uncalibrated
duplicate of a rule that already exists.

## 7.2 — 3.2 EO sensor: auto-capture, classification, tagging, mismatch alert

**Modules:** `eo/cue.py`, `camera.py`, `conditions.py`, `capture.py`,
`classify.py`, `appearance.py`. **Record:** ADR-037.

### The framing that decides the build

Four things are asked for and **only one of them needs pixels**. Capture without
operator intervention, bind the image to a track, classify against a library,
alert on disagreement — three are control and fusion logic. Image classification
is the commodity half of the problem; the loop around it is the defensible half.
So the loop is built and the classifier sits behind a replaceable interface.

### Cueing is a global assignment per slot, not a ranked list

The obvious build sorts tracks by suspicion and gives each its best camera. That
is greedy per-target matching — banned one domain along in the association core
for the identical reason. The three most suspicious contacts are frequently inside
one station's arc, so a greedy pass hands that station's camera to all three,
breaks the tie arbitrarily, and leaves fifteen cameras idle. Each slot is solved
with `linear_sum_assignment` over cameras × candidates.

### Priority

```
priority = (0.55 · suspicion + 0.30 · information gain + 0.15 · staleness)
           × expected image quality
```

- *Suspicion* dominates because that is the requirement's own framing.
- *Information gain* — what a photograph would actually resolve — is the term a
  naive build omits; without it the network spends every slot re-photographing the
  top of the list and never looks at the other fifty tracks. A contact nobody can
  name scores **1.00**; a hull whose declared identity no image has checked
  **0.55**; a hull an image already agreed with **0.10**; a hull an image already
  *disagreed* with **0.85** — which is what sends the camera back for the
  corroborating second look.
- *Staleness* is smallest, because "we have not looked at her lately" is not by
  itself a reason to look.

`PRIORITY_FLOOR = 0.30` is **arithmetic rather than tuning**: it is exactly the
priority of an ordinary unsuspected hull whose identity no image has ever checked
(`0.30 × 0.55 + 0.15 × 1.0 = 0.315`).

Below-floor targets are not simply dropped — they stay in the same global
assignment under a penalty larger than any value spread, so they take only a
camera nothing else could use, are marked `opportunistic` on the tasking, and
utilisation is reported **split** so an opportunistic fill cannot inflate the
number measuring real demand.

### The camera and quality model

Cameras are co-located with the radar, on the same tower, behind the same
headland. That is not a modelling convenience — it decides the shape of the area:
a camera can only be pointed at something the station already holds, its terrain
shadows are the radar's terrain shadows, and its useful range is much shorter than
the radar's, which is exactly why cueing has to choose.

Image quality is physics, not taste:

- **Pixels on target** falls as `length × |sin(aspect)| / range`. A ship seen
  bow-on presents a fraction of her length, which is why aspect is carried on
  every capture and why the classifier may refuse a head-on look.
- **Contrast** falls exponentially with range over visibility — the same
  extinction that makes a coastline vanish in haze.
- **Light** scales both, and at night the band changes.

### The classifier is genuinely swappable, demonstrated rather than asserted

Two implementations ship — `PrototypeClassifier` (uses every feature an image
carries) and `SilhouetteClassifier` (restricted to what an outline gives) — and
`tests/test_area5.py` **defines a third inside the test file** and substitutes it
into the running loop. All three produce bound, landed captures; the verdicts
differ; nothing in the cueing, tagging or mismatch rule changes between runs.

There are **no pixels**: `eo/appearance.py` defines the six measurements a vision
model would extract from a photograph — length, slenderness, where the
superstructure sits, freeboard ratio, deck clutter, mast count — and the loop
consumes those. That is what makes the interface concrete: a customer's model
drops in by producing the same six numbers from real imagery.

### The mismatch rule

Compares at the **AIS ship-type family** level and **requires corroboration**.
Both halves were forced by measurement:

- `MIN_CONTRADICTED_SHARE = 0.5` — contradicting looks must be a majority of the
  looks that decided anything.
- `MIN_CORROBORATING_CAPTURES = 2`.
- `MIN_MISMATCH_QUALITY = 0.45`, `MIN_MISMATCH_CONFIDENCE = 0.62`.

**Result: false accusations fell to 0 – 0.36% per look.** On the corpus: **2
mismatch alerts, both on hulls authored to declare a false type, zero false
positives.**

### Six defects found, four visible only as numbers — strong Annexure-2 material

1. **Confidence did not track accuracy, so the model refused 84% of good images.**
   A hand-set softmax temperature had the classifier picking the right fine class
   **96%** of the time while reporting mean confidence **0.35** — below its own
   0.50 bar. A confidence that does not track the hit rate is decoration, and the
   whole thesis is that an operator calibrates trust against it. The temperature
   is now **fitted** so mean reported confidence equals measured accuracy under
   the capture's own conditions.
2. **Observation noise had no floors, so the model could "tell" a Suezmax from an
   Aframax.** Error falling to nearly nothing in a perfect image claims a
   photograph measures "how cluttered is her deck" to three decimals. It does not.
   With near-zero floors, separability reported an **eleven-class vocabulary** —
   exactly the *"long list of classes it guesses at"* the brief warns against,
   reached by flattering the sensor. Irreducible per-feature noise floors added.
3. **The type-level merge was the wrong question, and 22 honest hulls in 1,500
   were accused.** The 25% confusability bar is right for *describing* a contact
   and wrong for *accusing* a named hull — calling a bulker a general cargo ship
   is a harmless slip inside one family; calling her a product tanker is the
   difference between silence and an alert. A second merge pass unions any pair
   mistaken across an **AIS family boundary** more than 5% of the time.
4. **A merged label was read as bounding nothing**, silently discarding the
   brief's own headline example. `merchant` still rules out every family it does
   not contain, so a hull broadcasting "fishing vessel" while imaging as a
   merchant *has* been contradicted.
5. **The rule read another model's label in the default model's vocabulary and
   accused 36% of an honest fleet.** A label means what the model that emitted it
   meant by it, so the classifier now publishes its own family set on the verdict.
   **This defect is why the swap test earns its place** — invisible with one
   classifier, immediate with two.
6. **Sister ships are not separable in six numbers.** Two observations of the same
   hull sit **0.12** apart at the median and **0.18** at p90; the *closest pair of
   different hulls* sits **0.11** apart. The distributions overlap and no radius
   separates them. Identification is therefore offered on **margin** — when a hull
   is distinctive against the library — and refused when she is one of a class.

### Honest status and capability boundaries

- **There is no camera.** Every capture runs through a simulated `CaptureSource`
  behind the same interface a real head will use, and **every landed row says
  so**: `capture_mode='simulated'`, empty `image_ref`, and the model's own
  provenance string recording that it has never seen an image.
- **A head useful to ~20 km can only produce imagery good enough to *contradict* a
  declared identity inside ~8 km** in this coast's monsoon visibility. "Can see
  her" and "can prove something about her" are different ranges. Recorded rather
  than tuned away.
- **Utilisation is 4.5%**, with **89,480 deferrals** attributable to
  `no_camera_in_reach`. That is a 20 km lens against the Arabian Sea — physics,
  not a scheduling defect — and it is reported as such. (Before the opportunistic
  fill, cameras sat idle in 115,028 slots while 46,527 reachable targets were
  refused under the floor.)

## 7.3 — 3.3 Multilingual VHF ASR & NLP

**STATUS: NOT BUILT.** This is the one area of six with no prototype.

The system already reflects the absence honestly rather than papering over it:

- `radio` exists as a **declared-and-empty** evidence family in the assistant's
  factor catalog, listed rather than omitted so the product can *state* the hole
  rather than imply completeness.
- Asked about radio traffic, the question answerer replies: *"No radio audio or
  transcript is held. Multilingual VHF speech recognition is not implemented."*
  It does not produce a plausible sentence.

### Proposed build (for the submission, marked as to-build)

1. **Channel-segmented capture and voice-activity detection.** The station
   software already records per channel; the module segments on speech activity
   and discards silence and squelch before any model runs.
2. **Multilingual ASR.** Fine-tune open Indic speech models on maritime VHF audio
   for the principal coastal-state languages plus accented maritime English.
   Maritime VHF is favourable in one respect — the vocabulary is small,
   procedural and heavily templated (call signs, positions, courses, intentions,
   standard phrases) — and unfavourable in another: narrow-band analogue audio,
   heavy noise, and code-switching mid-transmission.
3. **Normalisation to searchable English**, keeping the original-language
   transcript alongside the translation, because a translation is a *derivation*
   and the original is the *evidence*.
4. **Entity and intent extraction** — call signs, spoken MMSI and vessel names,
   spoken positions, declared destination and intention — under the same
   three-valued discipline as every other rule.
5. **Linkage to a track**, by transmission time, the receiving station's arc, and
   any spoken position. Linkage that cannot be made confidently is a **finding**,
   not a guess.
6. **The sixth evidence family.** Extracted claims enter the assistant as `radio`
   factors — for example, a destination declared on VHF that contradicts both the
   AIS declaration and the observed track.

**Why the rest of the system makes this comparatively cheap:** the hard parts of
turning a transcript into intelligence — evidence with provenance, three-valued
outcomes, a decomposable score, linkage to a track, a place on the operator's
screen — already exist and are shared with the other five areas. This area adds an
acoustic front end and an extractor, not a new pipeline.

## 7.4 — 3.4 Predictive analysis for AIS tracks

**Modules:** `anomaly/identity.py`, `anomaly/voyage.py`, `tracks/activity.py`,
`tracks/projection.py`, `tracks/route_prior.py`, `baselines.py`, `coastline.py`.
**Records:** ADR-032, ADR-035, ADR-042.

### (a) Identity authenticity is arithmetic before it is inference

Two checks carry most of the value and neither needs a model.

- **IMO check digit** — a checksum over the first six digits. It rejects **90.3%
  of random seven-digit strings**, a figure *verified inside the test suite*
  rather than quoted, because that number is the rule's whole justification.
- **MMSI Maritime Identification Digits vs declared flag** — MIDs are allocated by
  the ITU to a flag administration, so a hull broadcasting a Panamanian prefix
  while declaring an Indian flag states two incompatible things about itself in
  one message stream.
- **Registry consistency** (name, call sign, vessel type) — promoted from a buried
  score component to a rule of its own, with confidences that differ by field and
  say why: a call sign is issued with the flag and changes only when the flag
  does; a name changes on sale and registries lag.

`MID_TO_FLAG` is **deliberately partial.** This rule's entire value is that it
almost never produces a false positive, and the fastest way to destroy that is one
wrong row. An unallocated MID produces `not_checkable` — no claim at all — rather
than a guess.

### (b) Three outcomes, not two

`contradiction` / `ok` / `not_checkable`, on every rule in `anomaly/`.

An absent IMO is a gap in the record; reporting it as a contradiction would fire
on most of an honest corpus. And a surface has to distinguish *"we looked and she
is fine"* from *"we could not look"*. Summaries report all three, because a check
that is `not_checkable` on 95% of a corpus has told you almost nothing, and a
report of contradictions alone presents that silence as a clean bill of health.

**This is arguably the single most transferable design idea in the project.**

### (c) Declared voyage (ADR-035)

AIS message 5 landed as its own table for the first time: **3,091 declarations
over 131 hulls**, honest by default. Two rules:

- an arrival **no hull could physically make**;
- a destination **she never steered towards**.

Result: **2 alerts, both true positives, zero false positives.**

Real distance-from-shore (a shared 1 km coastline mask) replaced an earlier
port-distance proxy. **Operating depth is still absent and is not faked** —
bathymetry is not approximated from anything.

### (d) Activity classification

Lives in `tracks/` and reads motion only, so radar and AIS get the same answer.
The requirement for area 3.1 says outright that if they do not, that is a defect
in the design — so it is structural rather than a matter of keeping two models in
step.

### (e) Forward projection, and the discipline of not promoting it (ADR-042)

Route-aware projection — a flow field fitted to observed traffic, conditioned on
the vessel's present heading. Held-out hulls, median position error:

| Horizon | Dead reckoning | Route-aware | Change |
|---|---|---|---|
| 3 h | 4.70 nm | **2.91 nm** | −38% |
| 6 h | 16.56 nm | **9.92 nm** | −40% |
| 6 h, on established lane | — | — | **−47%** |

**The decisive conditioner was heading**, not vessel type and not the hull's own
history. An unconditioned flow field measured *worse than dead reckoning* (3 h:
5.20 vs 4.70), because a cell holding a waypoint holds both the inbound and the
outbound course, and asked which is hers it returns the one nearest her present
heading — the inbound one — and steers her straight through the corner. The hull's
own history turned out to be the **weakest** of four conditioners, available for
about one projection in sixteen.

**It is deliberately NOT a suspicion factor, and the mechanism is why.** As a
discriminator its precision is **0.09 – 0.33 against a base rate of 0.15** — at or
below chance — where policy requires 0.70. The gain is at the median (−26% to
−43% at p50) and almost absent at the tail (−1% to −6% at p90), and **the
uncertainty cone is sized at p90**. A predictor that improves where the cone is
not cannot tighten the cone and therefore cannot discriminate better. The
route arm also flags *more* of the fleet than dead reckoning at every operating
point. `test_projection_is_not_a_registered_suspicion_factor` stands, so a future
contributor cannot quietly promote it.

It travels as an **assertion an officer can act on** — where she will be in three
hours — not as an accusation.

### (f) Per-area behavioural baselines

Derived from landed positions and landed as an **inspectable artifact**: what
normal speed, course dispersion, dwell and traffic density look like in *this*
cell. Baselines **report distributions and never decide**; a rule reads them.

### (g) Coverage honesty

A vessel with no AIS where there is no receiver coverage is **not dark** — we
cannot hear her. Intentional silence may only be asserted inside demonstrated
coverage; outside it the answer is `unknown` until a paid satellite-AIS feed is
funded.

### (h) Track-departure detection

Built, measured — and **deliberately not a suspicion factor**, because it fires on
87–98% of the fleet. A "detector" that flags nearly everything is a description of
the ocean, not a finding.

### (i) Known measurement limit

`check_mmsi_flag` and `check_mmsi_form` are **unmeasurable on synthetic data by
construction**: the reserved 999 MMSI block that stops a synthetic hull wearing a
real vessel's identity also makes a genuine flag contradiction unbuildable. Both
have fixture coverage for agreement, contradiction, unknown MID, reserved AtoN
prefix, wrong length and the project's own block — but **their precision must be
measured on real landed AIS.**

## 7.5 — 3.5 RAG application for PANS data

**Modules:** `ingest/pans/*`, `anomaly/paperwork.py`. **Record:** ADR-036.

### The framing

Everything upstream of this area consumes feeds that are already records. **This
one consumes a mailbox.** A clean document set proves nothing, because the entire
difficulty is that the input is unstructured — so the test corpus generates the
*mess* as well as the truth.

### One record shape for every format, including the electronic one

Readers exist per format; all produce the **same** intermediate `Label: value`
passages; one **format-blind extractor** consumes those; one resolver attaches the
record to a hull.

**The e-PANS portal feed is another reader on the same extractor, not a second
pipeline.** That is what makes *"the electronic feed drops in without rework"* a
property demonstrated by shared code rather than asserted in a design note — a
change to date parsing cannot fix the portal and break the fax, because there is
only one date parser.

### The document kind is read off the page, not the filename

A mailbox receives more than PANS: arrival and departure reports, crew lists,
cargo manifests, port clearance certificates. **The kind is not the format** — a
crew list and a PANS in the same format are read by the same reader and differ
only in what the form has boxes for.

Kind matters for a three-valued reason: a departure report has no ETA box, so the
paperwork rule correctly answers `not_checkable` for its arrival window every
time — and without the kind, an operator reading a queue of "not checkable" cannot
tell *"the reader failed on this form"* from *"this form does not ask that
question"*. Those are different facts.

And it is read from the **title block**, not the filename: a filename is what
somebody's mail client called an attachment.

### Per-field provenance with a locator an analyst can point at

Every value carries the passage it came from, **where in the document** that was —
`page 1 (scanned)`, `PANS!A5`, `table 2 row 3` — the method, and a confidence
**earned by that method**: 1.0 for a spreadsheet cell, 0.97 for a PDF text layer,
whatever Tesseract itself reports (floored and capped) for OCR. *A character
offset satisfies a schema and helps nobody.*

### The corpus

**381 port documents**, written and read back through the real connector:

- **Five formats:** PDF, scanned PDF with no text layer, DOCX, XLSX, electronic.
- **Six kinds:** PANS, arrival reports, departure reports, crew lists, cargo
  manifests, port clearances.
- **Six house styles** on letterheads modelled on real ones: Deendayal Kandla,
  JNPA Nhava Sheva, Mundra, NMPA Mangalore, Cochin, and an agent's form.
- **224 honest documents against 157 authored to lie in eight specific ways.**
- The answer key is written **outside the inbox**, deliberately.

### Measured

| | |
|---|---|
| Documents unread | **0 of 381** |
| Overall field recall | **85.7%** (2,872 of 3,351) |
| Electronic feed | 99.4% |
| DOCX | 88.2% |
| XLSX | 84.5% |
| PDF (text layer) | 79.6% |
| PDF (scanned, OCR) | 76.7% |
| Resolution to a hull | **308 correct · 3 WRONG · 70 declined** |
| Paperwork rule outcomes | 50 contradiction · 172 ok · 711 not_checkable |

**The finding worth acting on: recall is not evenly distributed by issuing
house.** Across formats the spread is 77%–99%. Across agencies it is not — four
houses read at **99%+**, **Cochin reads at 43.1%** and Mangalore at **72.4%**. The
extractor is called format-blind and is; it is **not house-blind**, and one
agency's layout defeats it. The claim "a new source drops in without rework" is
true for four houses out of six on this corpus. Logged as an open question, not
averaged away.

Also measured separately: against labels and value formats the generator never
writes (an independent fixture set, because the corpus shares a synonym table with
the extractor and any accuracy taken on it is circular), extractor hardening moved
**50.0% → 99.1% correct with misattributions 2 → 0**. The two it had been getting
wrong are the failure that matters — a value landing on the *wrong* field.

### Non-resolution is a finding, in both directions

A form naming a hull nothing holds, and a hull berthing with no form. **There is
no fuzzy name matching**: normalisation recovers what is lossless (prefixes,
punctuation, a dropped space), and a transposition stays unresolved — because edit
distance would resolve `GRANITE TRUIMPH` to `GRANITE TRIUMPH` and would *equally*
resolve `GRANITE TRIUMPH II`, a different ship. **70 declines are the design
working; 3 wrong resolutions are the design failing quietly**, and each is a
document attached to the wrong ship. Logged as an open defect.

### Rules fusing paperwork to AIS

- `false_last_port` — a declared last port the vessel never visited.
- `missed_arrival_window` — arrival outside the declared window.
- declared **ballast** condition against a **laden draught**.

Rule-level recall against the answer key: `false_last_port` fired on **15 of 27**
authored lies, `missed_arrival_window` on **24 of 29** — a recall gap in the
*checks themselves*, distinct from the extraction gap. Reported, open.

**On declared cargo the general case is deliberately not built.**

### On "RAG" specifically — read this before writing the submission

The requirement names RAG. What is built is the **retrieval and citation half**,
and that is where the safety lives: an answer is assembled from retrieved rows and
**no fact that is not in a retrieved row can reach the text.**

The **generation half is deliberately absent from the answer path.** The question
answerer matches a closed set of intents, retrieves, and assembles. It is duller
than a language model and it **cannot confabulate**, which is the right trade for
the one surface an officer calibrates their trust against. `QuestionAnswerer` is
an interface, so a generative model can be substituted later under the same
contract with the same citation requirement.

Frame this as a **stronger reading of the requirement**, not a shortfall: the hard
and dangerous part of a RAG system in an operational setting is grounding, and
that is the part that exists.

## 7.6 — 3.6 AI-based MDA assistant

**Modules:** `assistant/*`, `/api/voi*`, CLI `maritime-isr voi`, the Watch tab.
**Records:** ADR-031, ADR-038.

### (a) The subject of a Vessel of Interest need not be a vessel

Measured: **52 of 55 alerts land on a `contact:` or `detection:` node, not a
hull.** A target nobody can name is precisely what makes a finding, so the object
ranks *subjects* and says which kind each is. **Requiring a named hull would have
discarded the dark-vessel path — the capability the requirement most needs — from
its own queue.**

### (b) The score decomposes exactly, or it is not a score

Factors combine as a noisy-OR over independent evidence:

```
score = 1 − Π(1 − wₖ · cₖ)
```

That does not decompose additively, but its logarithm does, exactly, so the result
is allocated back in log space:

```
points_k = score × ln(1 − sₖ) / Σ ln(1 − sⱼ)     and     Σ points == score
```

to floating point. The identity is **asserted in the tests**, because it is the
whole claim. An operator who reads *"0.81, of which 0.42 is the sanctions
designation and 0.39 the dark contact"* can argue with the system; one who reads
*"0.81, driven mainly by sanctions"* cannot.

### (c) Repeats combine two different ways, and conflating them overstates confidence

Four loitering episodes are four things that happened and each raises the claim
(noisy-OR). A designation arriving from the landed match table **and** from walking
the graph's ownership chain is **one fact seen twice** — and combining it as two
independent observations took **19 hulls to 0.97 confidence** on the first build: a
system that sounds *more* certain the more places it looks. `FactorSpec.repeats`
distinguishes `occurrences` from `restatement`; the second takes the maximum and
keeps the corroboration as evidence.

### (d) Recommendations state capability and compute feasibility

*"Call her on VHF"* is not advice if she is beyond the nearest station, so range is
worked out from the station network's geometry using the same horizon function the
radar model uses, and an infeasible action is returned **with its reason** rather
than hidden. Every recommendation carries `performed_by` and `system_capability`,
and for most the honest answer today is *"this instructs a human; the system
cannot do it"*.

**Nothing here is autonomous: there is no path from this module to an action.**

*(Note: the "Recommended actions" panel was removed from the Watch screen by
operator instruction. The module still builds it, the API still returns it, the
factor catalog is still keyed on those actions, and the incident report still
prints it.)*

### (e) The question answerer has no generative step, and that is the design

A question matches one of a closed set of intents; the intent retrieves; the answer
is assembled from the rows that came back. **No fact that is not in a retrieved row
can reach the text.**

Three outcomes are kept distinct, and the middle one is most of the value:

- **`answered`**
- **`no_data`** — understood, and the system holds nothing, phrased as a statement
  about the **record** rather than about the vessel.
- **`unsupported`** — about something this system does not carry, **naming which
  area of the build would carry it.** Asked "what cargo is she carrying?", it says
  cargo arrives on the arrival notification. Asked about VHF, it names the ASR area
  and says it is not implemented.

### (f) Six evidence families, keyed to the challenge's own areas

| Family | Label | What it reads | Status |
|---|---|---|---|
| `motion` | Motion and behaviour | what she is doing, from radar or AIS alike | populated |
| `identity` | Declared identity | whether what she says about herself holds together | populated |
| `network` | Ownership and designation | who controls this hull, who has been designated | populated |
| `paperwork` | Arrival notifications | what the paperwork declares vs what the track shows | populated |
| `imagery` | Electro-optical | what a camera saw vs what the transponder claims | populated |
| `radio` | Radio traffic | what was said on VHF vs what the track shows | **declared and EMPTY** |

A `family_coverage` function reports which families a given picture actually
contains — **the honest version of a progress bar.** An officer is never shown a
complete-looking assessment that silently omits an entire class of evidence.

### (g) The factor catalog — 23 factor kinds with stated weights

The weights are **policy and they are visible**. There is no learned black box,
because an unexplainable score is unsellable to a navy and to an insurer alike. A
weight answers: "how much does this kind of fact, at full confidence, move a
subject up the queue", in [0,1].

| Factor kind | Family | Weight |
|---|---|---|
| `sanctions_designation` | network | 0.95 |
| `transponder_shutdown` | motion | 0.90 |
| `identity_then_anomaly` | identity | 0.90 |
| `dark_vessel` | motion | 0.85 |
| `ais_spoofing` | identity | 0.85 |
| `dark_rendezvous` | motion | 0.80 |
| `identity_contradiction` | identity | 0.75 |
| `paperwork_contradiction` | paperwork | 0.75 |
| `assessed_ais_disabling` | motion | 0.70 |
| `voyage_contradiction` | identity | 0.70 |
| `sanctioned_ownership` | network | 0.70 |
| `imagery_type_mismatch` | imagery | 0.70 |
| `loitering_sensitive` | motion | 0.60 |
| `vessel_interaction` | motion | 0.60 |
| `notification_unmatched` | paperwork | 0.55 |
| `anchored_outside_limits` | motion | 0.50 |
| `notable_activity` | motion | 0.50 |
| `arrival_without_notification` | paperwork | 0.50 |
| `lane_deviation` | motion | 0.45 |
| `maiden_zone_visit` | motion | 0.40 |
| `identity_change` | identity | 0.35 |
| `flag_opacity` | identity | 0.30 |
| `port_risk_propagation` | network | 0.25 |

### (h) One operator surface (ADR-038)

The product had grown three top-level views a cold user described as *"seemingly
doing the same things"*: **Assistant** (every subject ranked, decomposed,
narrated), **Findings** (a narrower attribution-first table), and **Alerts** (the
raw detector queue, and the only place anything could be acted on).

They were not identical, but the overlap was most of each and the split cost more
than it bought: an officer investigating a hull had to visit two screens to see
her ranked score and her detections, and a third to act on either.

**`Watch` replaces all three, with two lenses over the same facts.** *By vessel* is
one row per hull, ranked, with every detection about her gathered underneath. *By
event* is a chronological queue, newest first, one card per detection. Neither is
a subset or a filter of the other: they are **two orderings of one dataset**,
because an officer investigates a *ship* and works a *watch*, and those are
different verbs. Disposition controls live on the alert in **both** lenses, so
recording a decision never requires changing screen.

**The real-versus-synthetic boundary goes; the label stays.** By operator
instruction there is no filter and no gate — every row still carries its
`SCENARIO` tag. A boundary makes a person navigate to see everything; a label makes
them able to tell what they are looking at.

---

# PART 8 — THE DETECTOR LIBRARY AND THE DARK-VESSEL CASCADE

## 8.1 The 14 detectors in `anomaly/library.py`

`detect_dark_vessels` · `detect_spoofing` · `detect_dark_rendezvous` ·
`detect_sensitive_loitering` · `detect_identity_then_anomaly` ·
`detect_port_risk` · `detect_identity_contradiction` · `detect_notable_activity` ·
`detect_vessel_interactions` · `detect_voyage_contradiction` ·
`detect_paperwork_contradiction` · `detect_unmatched_notification` ·
`detect_arrival_without_notification` · `detect_imagery_mismatch`

## 8.2 The dark-vessel filter cascade

An unmatched contact must survive four stages before it earns the name. **Every
suppression is a recorded verdict, not a deletion** — the analyst question *"why is
this NOT dark?"* must be answerable from the store.

1. **COVERAGE** — the AIS absence must not be explainable. Had a vessel been
   transmitting here, we would have heard it. A contact in a receiver shadow is
   *unexplained*, not dark.
2. **STATIC** — not a known fixed installation. The static-object layer
   **self-builds**: unmatched detections recurring at the same spot across
   ≥ `STATIC_MIN_SCENES` scenes spanning ≥ `STATIC_MIN_SPAN_DAYS` accumulate into
   objects. **Matched detections never accumulate** — a berthed ship is not a rig.
   Known accepted gap: a never-transmitting vessel loitering one spot for weeks
   would eventually staticize.
3. **SIZE** — length above `DARK_MIN_LENGTH_M` (20 m), a margin over the 15–25 m
   Sentinel-1 physics floor. Below it we cannot distinguish small craft from
   clutter, and we say so rather than alert.
4. **SCORE** — survivors get `dark_score = detection quality × hearability × size
   margin × isolation`; only scores above `DARK_SCORE_THRESHOLD` (0.5) alert.

## 8.3 The association engine

- **GATE** — a track is a candidate for a contact iff the contact lies inside the
  track's **Kalman uncertainty cone** at scene time plus a measurement buffer, and
  the track reported recently enough (`ASSOC_MAX_TRACK_AGE_H = 12`,
  `ASSOC_GATE_BUFFER_M = 500 m`).
- **SCORE** — log-likelihood: position against the cone, length against the
  registry, historical-presence bonus. Heading consistency is deferred because
  Sentinel-1-class detections do not carry reliable heading at this fidelity.
- **ASSIGN** — global optimum over the whole scene via Hungarian/JV, with a
  per-contact "no match" dummy at the score floor. **Never greedy.**
- **GRADE** — assigned pairs with a thin top-2 margin are **AMBIGUOUS** (top-k
  reported, confidence discounted); contacts whose best option is the floor are
  **UNMATCHED** — the dark-vessel candidates.

## 8.4 Risk score

Four weighted components: `anomaly 0.45`, `sanction 0.30`, `flag 0.15`,
`fingerprint 0.10`. Anomaly contributions decay on a **30-day half-life**.
Confirmed alerts count in full; open (unreviewed) alerts are discounted to 0.6.
A pile of weak alerts is squashed so it cannot exceed a single strong signal.
**Every risk score equals the weighted sum of its named components**, asserted by
the evaluation harness.

---

# PART 9 — MEASURED RESULTS (ALL SYNTHETIC UNLESS MARKED LIVE)

## 9.1 The synthetic corpus

| | |
|---|---|
| Vessels | **674 hulls** across twelve archetypes |
| AIS position reports | **816,356** |
| Voyage declarations (AIS msg 5) | 3,091 over 131 hulls |
| Port documents | 381 across 5 formats, 6 kinds, 6 houses |
| Graph | 674 vessel nodes, 421 `operated-by` edges, all carrying confidence, `t_start`, source, source_ref, pipeline_version |
| Ownership rows | 628 |
| Radar dark truth episodes | 87 |
| Generation | exits 0 in **5 m 10 s**, 14/14 validators pass |

**Archetypes:** container and general cargo on liner routes, product tankers,
Aframax, VLCC, bulkers waiting off Kandla and Mundra, reefers, loitering trawlers,
tugs, offshore supply, ferries, coastal dhows.

**Archetype motion is a load-bearing constraint, not decoration.** Since
`vessel_type.py` infers type from motion alone, a hull labelled trawler that
steams a straight line at 13 knots is a **corrupt training example**. An
`archetype_motion` validator checks all 394 bulk hulls and reports 0 violations.

**The anomaly base rate went DOWN as the corpus grew — deliberately.** 60 of 674
hulls (8.9%) carry an anomaly, down from 50 of 253 (19.8%). All 394 new bulk hulls
are boring; every new anomaly sits in a hand-placed group where 7 true anomalies
are outnumbered by 9 purpose-built decoys. **The expected and correct consequence
is that measured precision falls** — the old corpus, with one hull in five guilty,
flattered every number measured against it.

## 9.2 Detection performance

| Metric | Value | Note |
|---|---|---|
| Dark-contact **precision** | **100%** in every draw | the number policy constrains |
| Dark-contact recall | **43% – 62%**, varying by corpus draw | denominator is a handful of episodes |
| Radar→AIS correlation | **98.2%** of resolvable tracks to the right hull | |
| Radar dark detection | precision 100%, recall 43% on the simulated picture | |

**On the recall figure — important framing.** Recall moved 43% → 75% → 62% across
one session, and **every move was a fresh RNG draw rather than a change in
capability**. A single-variable A/B (the whole pipeline twice on one corpus) gave
identical cascade verdicts. Adding cast members shifts the generator's stream and
every scenario's noise is a fresh sample. **A recall figure with a denominator of
seven episodes was never a capability measurement.** Precision is what holds.

## 9.3 Alert queue on the corpus

27 alerts across 10 detectors on one seed: `dark_vessel` 7,
`vessel_interaction` 6, `identity_contradiction` 5, `notable_activity` 4,
`voyage_contradiction` 2. 39 ranked subjects.

All five `identity_contradiction` alerts were true positives: a broken IMO check
digit, a wrong call sign, a wrong name, and two hulls whose broadcast names
genuinely no longer match their registry entries.

## 9.4 Live data landed (2026-07-29, on a Windows laptop in download-only mode)

**This is the only genuinely live column in this pack.**

| Source | Landed |
|---|---|
| GFW loitering events | 24,153 |
| GFW port visits | 3,000 |
| GFW encounters | 14 |
| GFW AIS gaps | 5 |
| GFW vessel identity | 9,184 of 9,185 vessels (1 lookup failure); 9,648 identity intervals |
| OFAC SDN | 19,157 rows (1,516 vessels) |
| UN consolidated | 1,011 |
| EU consolidated | 6,017 |
| Sentinel-1 scene catalog | **636 scenes** (metadata only, no imagery) |
| Total | 27,791 GFW rows + 26,824 sanctions/catalog rows |
| Disk | 73.9 MB of a 1 GB budget; every positioned table passed the AOI bounds check |

**What that data is, precisely:** GFW's *derived* behaviour events, vessel
identity, three sanctions lists, and scene metadata. **No SAR imagery, no AIS
position tracks, and no dark vessel detected by us.**

All five GFW gap events are flagged by GFW as intentional AIS disabling. **That is
GFW's assertion, not our detection.** Our own dark-vessel detection needs SAR
contacts matched against AIS tracks, and neither is obtainable free for this area
of interest.

**One thing in the live path is genuinely ours:** the system computes **satellite
imaging opportunities** over flagged AIS gaps — where a vessel could have been
during its silence, against where Sentinel-1 was actually pointed (ADR-026). That
is the first analytical claim here that is not a reproduction of someone else's
finding. It still claims nothing about any vessel: it says an image exists, or does
not, and nobody has looked at it.

---

# PART 10 — DATA SOURCES: WHAT IS OBTAINABLE

| Source | Wanted | Obtainable? |
|---|---|---|
| GFW SAR detections, **per detection** | position, time, length, AIS-match status | ⚠️ Manual browser download only, no API |
| GFW SAR presence, **gridded** | counts per cell per day | ✅ via API |
| GFW SAR, **currently** | anything | 🔴 Offline since 2026-07-03 (upstream pipeline migration) |
| GFW encounters / loitering / port visits / AIS gaps | events | ✅ |
| GFW vessel identity | name/flag/MMSI/owner history with date ranges | ✅ |
| **Raw historical AIS positions for this AOI** | per-ship tracks | 🔴 **No free source — this is the structural gap** |
| OFAC / UN / EU sanctions | designated entities and vessels | ✅ public download |
| WPI world port index | port locations and attributes | ✅ (NGA portal, was briefly down) |
| Sentinel-1 scene catalog | footprints + acquisition times | ✅ no login needed for search |
| Basemap tiles | coastline, bathymetry, imagery | ✅ keyless (Esri, OSM) |

**For an ICG deployment the structural gap closes by itself:** the ICG *has* the
radar feed, the AIS feed, the PANS mailbox, the EO cameras and the VHF recordings.
The gap in this prototype is an access problem, not a capability problem, and it is
the single strongest argument for the project — every connector exists and is
waiting for a feed that the customer already operates.

**A dependency lesson worth including:** the map originally pointed at a tile host
that used to serve without a key and stopped; every tile came back stamped "API KEY
REQUIRED" and the map looked broken. Nothing about the map was wrong and no mark
was ever positioned by a tile. Two things were designed in afterwards: **more than
one keyless provider**, selectable, because a single hardcoded one is exactly how
this failed; and the operational layers **do not wait on the basemap** — they are
gated on the map style rather than its sources, so an unreachable tile host
degrades the picture instead of deleting it, and the panel says the tiles failed
rather than leaving an empty sea to be misread as an empty ocean.

---

# PART 11 — THE OPERATOR SURFACE

## 11.1 Six tabs

| Tab | What it is |
|---|---|
| **Map** | The live picture. Opens on **one layer: where the ships are** — no trails, no projections, no event pins, no density, no footprints, no areas. Clicking a vessel draws her trail and her projection whatever the fleet-wide switches say, because the click *is* the request. Names beside marks past zoom 8.5, decluttered in screen space. Pink for travelled, yellow for predicted. |
| **Watch** | The one screen an officer works. Two lenses — *by vessel* and *by event* — over the same facts, with disposition controls on the alert in both. |
| **Radar** | The coastal-radar picture, station coverage, contacts, and per-contact profiles. |
| **Vessels** | Per-hull view with risk scoring, identity, history. |
| **Graph** | The object graph — vessels, organisations, designations — opening on the whole network with a time scrubber. |
| **Method** | Not a sixth queue and not a re-fragmentation of Watch: it carries how a determination was reached. |

## 11.2 API surface — ~40 endpoints

`/health` `/stats` `/corpus-window` `/tracks` `/vessels` `/vessels/{id}`
`/vessels/{id}/track` `/vessels/{id}/report` `/vessels/{id}/checks`
`/vessels/{id}/motion` `/vessels/{id}/neighbourhood` `/detections` `/scenes`
`/events` `/events/density` `/findings` `/alerts` `/alerts/{id}`
`/alerts/{id}/disposition` `/predictions` `/baselines` `/ports` `/zones`
`/zones/{id}/vessels` `/geofences` `/graph/all` `/graph/seeds`
`/radar/stations` `/radar/tracks` `/radar/contacts`
`/radar/contacts/{id}/profile` `/eo/captures` `/eo/summary`
`/analysis/vessel-type` `/analysis/interactions` `/checks/coverage`
`/voi` `/voi/catalog` `/voi/workload` `/voi/{id}` `/voi/{id}/ask`

## 11.3 The incident report

A **self-contained HTML file**, not a PDF. It opens in any browser, prints to PDF
from there in one keystroke, survives being emailed, and needs no renderer on the
server or the operator's laptop. Every style is inline and no asset is fetched, so
it says the same thing on a machine with no network — which for a document that
exists to be forwarded is the whole point.

**It is built to be read by someone who was not here**, so a synthetic vessel is
unmistakable: a banner at the top and a repeat at the bottom. A generated dossier
forwarded and mistaken for a real one is the exact failure the honesty rules exist
to prevent.

## 11.4 Accessibility and theming

Light and dark themes come off **one set of CSS custom properties**; nothing
hardcodes a colour, including the map marks. The three-valued rule outcomes are
rendered by **shape as well as hue** — a dashed rule and a "?" for
`not_checkable` — because colour alone cannot carry a three-way distinction for a
colour-blind reader.

## 11.5 CLI

Entry points are invoked as `maritime-isr <verb> <target> [opts]`. Verbs include
`ingest` (with per-source subcommands: `s1`, `ais`, `gfw`, `gfw-events`,
`gfw-vessels`, `registries`, `noaa`, `radar`, `zones`), `doctor`, `preprocess`,
`validate`, `build-tracks`, `dark-vessels`, `graph-populate`, `graph-query`,
`alerts`, `anomalies`, `risk`, `baselines`, `voi`, `feedback`, `radar`, `zones`,
`overpass`, `scenario`, `eval-report`, `status`.

---

# PART 12 — WHAT IS *NOT* BUILT, AND KNOWN DEFECTS

**Include a version of this in Annexure-2's "Challenges & Mitigation". An
evaluator who finds a gap you did not disclose discounts everything you did.**

## 12.1 Not built at all

| | |
|---|---|
| **Area 3.3, multilingual VHF ASR & NLP** | The entire area. Designed, not written. |
| **A generative layer on the RAG answer path** | Deliberate. Retrieval and citation exist; generation is refused in the answer path. |
| **Own SAR ship detection on real scenes** | The CFAR/CNN code exists and is exercised on synthetic chips; no real scene has been processed, because SNAP has never been run on a host. |
| **Any camera** | Every EO capture is simulated behind the `CaptureSource` seam and every row says so. |
| **Operating depth / bathymetry** | Absent and **not approximated**. `coastline.py` gives distance from land, which is a different quantity. |
| **The four statutory maritime limits** | Absent by decision; they arrive only through the connector. |
| **Live AIS capture** | Parked — needs an always-on host. The connector parses `ShipStaticData` but **has never seen a live message.** |
| **The deployment VM** | Not provisioned. Nothing has run on the target host. |

## 12.2 Built but unmeasurable on this corpus

| | |
|---|---|
| `check_mmsi_flag`, `check_mmsi_form` | The reserved 999 MMSI block that makes synthetic identity collisions impossible also makes a genuine flag contradiction unbuildable. Must be measured on real AIS. |
| `transfer_pattern` interaction | The scenario's counterparties are dark by design. |

## 12.3 Open defects and questions, verbatim from the project record

- **OQ-pans-1. The extractor is format-blind but not house-blind.** Cochin 43.1%,
  Mangalore 72.4%, everyone else 99%+. Is this one layout quirk, or does the
  passage model assume a label/value adjacency that some real forms will not have?
  **This decides whether "drops in without rework" survives contact with a real
  agency.**
- **OQ-pans-2. Three documents resolved to the WRONG hull.** 70 declines is the
  design working; 3 wrong is the design failing quietly. What did those three
  share?
- **OQ-pans-3. `false_last_port` caught 15 of 27 authored lies.** The check's own
  recall, separate from extraction. Is the gap in the rule or in the track it
  compares against?
- **OQ-pred-1. Off-lane prediction is still ~11% worse than dead reckoning.** Down
  from +57%, not to zero.
- **OQ-pred-2. The corpus flatters the route arm by construction** — traffic is
  generated through one deterministic coastal corridor, so a flow field fitted to
  it recovers the generator's own waypoints. **The measured gap is an upper bound.
  Re-measure on real AIS before stating any of it externally.**
- **SNAP on ARM (aarch64) is unvalidated.** The install script is memory-capped
  for 24 GB but has never run on a host.
- **Scenario generation fails at seed 9**, in port-visit scheduling, with an event
  landing outside the corpus window. Confirmed pre-existing. Seeds 7 and 8
  generate cleanly.
- **~132 ruff lint findings** across the package, pre-existing and untouched.

## 12.4 A defect pattern worth naming in the submission

Several of the most serious bugs found in this project were **silences, not
errors** — a class of relationship vanishing with no message, a rule going quiet
because two id spaces named the same hull, a scheduler issuing zero requests
because its cadence was expressed in the one unit that did not bound the work,
three tests skipping because a table name read as "no corpus" on a corpus that had
the answer key all along.

**Every one was found by counting, not by a test failing.** That is the argument
for the instrumentation posture: a system that reports what it could not check is
the only kind in which this class of defect is visible at all.

---

# PART 13 — DESIGN PRINCIPLES / INNOVATION ARGUMENTS

Ranked roughly by how defensible they are in a technical evaluation.

1. **An exactly decomposable suspicion score.** Most risk scores are an
   undecomposable weighted blend. This one allocates every point back to a named
   factor carrying its own evidence, with `Σ points == score` asserted in tests.
   An unexplainable score is unsellable to a navy and to an insurer alike.
2. **Three-valued rules everywhere.** *contradiction / ok / **not_checkable***.
   "We could not check" is a first-class answer, never folded into "fine". The
   difference between reporting a clean inbox and reporting an inbox nobody read.
3. **Sensor-blind features, asserted rather than claimed.** One model serves radar
   and AIS because the feature vector is provably byte-identical for the same track
   from either sensor.
4. **A class vocabulary derived from the measured confusion matrix.** The system
   publishes the classes it can genuinely separate and names the ones it cannot,
   and the list updates itself as the model improves.
5. **Global assignment wherever targets compete** — association and camera cueing
   alike, never greedy nearest-match.
6. **The connector claim demonstrated by shared code, not asserted.** The e-PANS
   electronic feed is another reader on the same extractor as the scanned fax.
7. **Provenance and reproducibility as invariants**, with origin separated from
   derivation, and this system's own storage never cited as a source.
8. **Refusal is a designed output.** The resolver declines rather than
   fuzzy-matching a transposed name. Identification declines on sister ships.
   Forward projection was measured and **not promoted**. Track-departure detection
   was measured and **not promoted**. Each refusal is a false positive that never
   reached an officer.
9. **Coverage honesty.** Silence where there is no receiver is `unknown`, never
   `dark`.
10. **Precision as stated policy**, not a tuning accident: ≥ 7 of 10 alerts must
    survive review before recall is allowed to rise.
11. **The ontology is data, not code** — adding an edge type is an insert and a
    version bump, not a deployment.
12. **The answer path cannot confabulate.** No generative step; no fact absent from
    a retrieved row can reach the text.

---

# PART 14 — VERIFICATION DISCIPLINE

A unit is **not done** until:

1. **The exit test passes on the real machine / target host** — not just in a
   development sandbox. Sandbox-green is necessary, never sufficient.
2. **The evaluation harness has been re-run** and its results logged per git SHA.
   No exceptions — silent regression here corrupts everything downstream and
   nobody notices until analysts stop trusting alerts.
3. **The build-state record is updated** — unit status, what is verified vs
   assumed, anything now broken, and the next unit.

Three states are tracked and distinguished:

- 🟢 **Built and verified in sandbox** — code exists, tests pass. *Not* verification
  on real data or the real host.
- 🟡 **Built, unverified on host** — code exists, has never run on the target
  infrastructure. **Most of this project.**
- ⬜ **Currently doing** — has run on real data on the real host, result measured.
  **Almost nothing.**

The evaluation harness itself checks: every detector fired at least once and its
alerts carry evidence and a score above its gate; dispositions retune at least one
detector with a measured non-negative precision delta; and **every risk score
equals the weighted sum of its named components**.

---

# PART 15 — SUGGESTED SUBMISSION CONTENT

## 15.1 Eight deliverables for Annexure-1 §3

| Sr | Deliverable | Description |
|---|---|---|
| 1 | Fused maritime picture and correlation core | Source-agnostic ingest of coastal radar, AIS (dynamic and static), SAR and registries into a canonical schema on a shared H3 index; radar↔AIS correlation by global assignment. Every new feed enters as a connector. |
| 2 | AI-based MDA Assistant (**3.6**) | Ranked Vessel-of-Interest list; score decomposing exactly into named factors with evidence; plain-language narration; grounded question answering that retrieves rather than generates. |
| 3 | Radar-only classification module (**3.1**) | Vessel type, activity and vessel-to-vessel interaction from kinematics alone, with an honestly derived class vocabulary and per-call confidence. Identical on radar and AIS tracks. |
| 4 | AIS integrity and predictive track module (**3.4**) | Static-data authenticity checks, activity classification, route-aware forward projection with uncertainty cone, per-area behavioural baselines from local history. |
| 5 | PANS ingestion and risk-fusion application (**3.5**) | Readers for PDF, scanned PDF, DOCX, XLSX and the e-PANS feed, all through one extractor; per-field provenance; resolution that refuses rather than guesses; rules fusing the paperwork to the track. |
| 6 | Automated EO capture and mismatch loop (**3.2**) | Camera cueing without operator intervention as a global assignment; image tagged to the originating track as evidence; classification against a maintained library behind a swappable interface; alert on contradiction. |
| 7 | Multilingual VHF ASR & NLP module (**3.3**) | Speech-to-text over recorded VHF for the principal coastal-state languages, normalised to searchable English, with entity extraction and linkage to a track. *The one area not yet prototyped.* |
| 8 | Operator surface, incident report and evaluation harness | Six-tab operator UI, one-click self-contained incident report, and a permanent harness re-measuring precision, recall and per-rule outcomes on every model change, logged per code version. |

## 15.2 Twelve-month phasing for Annexure-1 §4

| Phase | Months | Work | Exit criterion |
|---|---|---|---|
| **P1 — Deployment and data foundation** | 1–3 | Provision the host; land live AIS, the ICG radar feed and SAR through the existing connectors; stand up the three storage tiers with the provenance envelope; run the evaluation harness against ICG data for the first time. | The pipeline runs unattended on ICG infrastructure for 14 consecutive days; every landed row carries source, timestamp and code version. |
| **P2 — Radar and AIS analytics on real data (3.1, 3.4)** | 4–6 | Re-train and re-measure the motion-only models on real radar and AIS; re-derive the class vocabulary from the *measured* confusion matrix; measure the identity checks, which cannot be measured synthetically; fit baselines on real local history. | Coarse-class accuracy and per-rule precision reported on held-out real hulls, vocabulary derived rather than declared. |
| **P3 — PANS and EO on real feeds (3.5, 3.2)** | 7–9 | Readers for the actual agency letterheads and the e-PANS portal feed; measure field recall **per issuing house**; integrate a live EO head at one station behind the existing interface; build the image library from captured imagery. | ≥ 90% field recall across all participating agencies; end-to-end automatic capture, tagging and mismatch alert on a live camera at one station. |
| **P4 — VHF ASR/NLP, integration and trial (3.3, 3.6)** | 10–12 | Build and train the multilingual VHF module and wire it in as the sixth evidence family; integrate all six areas into the ranked assistant; watchkeeper trial; precision tuning against reviewed dispositions; documentation and handover. | Field trial at a ROC/ROS: ≥ 70% of alerts survive watchkeeper review, with measured reduction in time-to-decision per investigated track. |

## 15.3 The three strongest arguments to lead with

1. **Five of six areas arrive as already-built, already-measured code**, so the
   twelve months are spent on real-data measurement and the sixth area rather than
   on first construction. Most applicants will be proposing a specification.
2. **The architecture's whole thesis is that new feeds enter as connectors and the
   fusion core does not change** — demonstrated by shared code rather than asserted
   — which is what decides whether this is a system or a one-off, and which is
   exactly what the e-PANS compatibility clause in 3.5 is testing for.
3. **The evidence chain survives cross-examination.** Every assertion is traceable
   to a source and to the exact code version that produced it, and every rule can
   say *"we could not check"*. In a domain where the output authorises a boarding,
   that is not a weaker product than one that reports only findings — it is the
   only kind that survives contact with an operator.

---

# PART 16 — GLOSSARY

| Term | Plain English |
|---|---|
| **AIS** | Automatic Identification System — the position signal ships legally broadcast on VHF radio, announcing identity, speed and location. A ship can switch it off. |
| **MMSI** | A ship's nine-digit radio ID. The first three digits (the MID) are allocated by the ITU to a flag state. |
| **IMO number** | A permanent seven-digit hull identifier. The seventh digit is a check digit over the first six. |
| **SAR** | Synthetic Aperture Radar — satellite radar imagery. Sees ships through cloud, day or night, whether or not they are broadcasting. |
| **Sigma-nought (σ⁰)** | Calibrated radar backscatter. How bright a pixel is in physical units. |
| **CFAR** | Constant False Alarm Rate — a classical detector that finds pixels brighter than their local background. Finds ship candidates in SAR. |
| **Dark vessel** | A ship detected by radar that the AIS picture cannot explain — she is there and she is not broadcasting. |
| **PANS** | Pre-Arrival Notification of Ships — the form a vessel files before entering port. |
| **e-PANS** | The electronic PANS feed from the National Logistics Portal (Marine), Indian Port Association. |
| **CSN** | Coastal Surveillance Network — the ICG's chain of coastal radar stations. |
| **ROS / ROC** | Regional Operating Station / Regional Operating Centre. |
| **MDA** | Maritime Domain Awareness. |
| **EO** | Electro-optical — a camera, as opposed to radar. |
| **ASR** | Automatic Speech Recognition. |
| **H3** | Uber's hexagonal global grid. Resolution 7 ≈ 5 km across, resolution 9 ≈ 170 m. |
| **Kalman filter** | A tracker that maintains a best estimate of where a vessel is and how uncertain that estimate is. |
| **Uncertainty cone** | The region a vessel could plausibly be in, given her last fix and the time since. Grows with silence. |
| **Hungarian / Jonker-Volgenant assignment** | An algorithm that finds the best *overall* pairing across a whole set at once, rather than pairing each item with its own nearest neighbour. |
| **Noisy-OR** | A way of combining independent pieces of evidence so that each raises confidence without any single one saturating it. |
| **Provenance envelope** | The block of fields on every record saying where it came from, when, and which version of the code processed it. |
| **not_checkable** | A rule's third outcome: we looked and the data did not permit an answer. Distinct from "fine". |
| **Precision / recall** | Precision: of the alerts raised, how many were real. Recall: of the real events, how many were caught. This project constrains precision and lets recall follow. |

---

*Compiled from the Maritime ISR repository at commit `ce8069d`, 2026-09.
Every quantitative figure is measured on the deterministic synthetic corpus with
injected ground truth, except Part 9.4, which is the single live ingestion run.
No figure anywhere in this pack is a measurement on operational Indian Coast Guard
data.*
