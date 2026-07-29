# Maritime ISR — Product Roadmap (Prototype → Sellable Platform)

**Scope of this document:** the product only. What gets built, in what order, with what acceptance criteria. No procurement, no legal, no entity structuring, no hiring plans beyond the minimum engineering assumptions needed to make the timeline honest.

**Product thesis (one line):** A fusion intelligence platform for the Indian Ocean that ingests open and commercial sensor feeds, resolves detections into a persistent object graph of vessels and their relationships, and surfaces dark-vessel and anomalous-behavior alerts with full evidence chains — built entirely on free/open data first, designed so every paid or classified feed later is a connector, not a rewrite.

**Operating model:** Anduril-style. Build the working product on open data, then demo a live Indian Ocean picture — not slides. Claude builds the pipeline code, models, entity-resolution engine, and evaluation harness session by session; a small engineering team (assume 2–3 engineers by Phase 3) deploys, operates, and productionizes.

**Baseline assumptions the roadmap depends on:**
- Area of interest (AOI) v1: Arabian Sea + Indian west-coast EEZ, roughly 5°N–25°N, 60°E–78°E. Expand to Bay of Bengal in Phase 5.
- Sentinel-1 revisit over the AOI is every 2–4 days (worse since S1-B loss; S1-C improves this). The prototype is a *persistent picture with gaps*, not real-time tracking. Every design decision assumes sparse revisit — this is a feature of the architecture, because it forces the track-persistence and confidence-decay machinery that classified-tempo feeds will later exploit.
- Nothing in Phases 0–6 requires a rupee of data spend beyond a satellite-AIS subscription (Phase 2, low hundreds of USD/month at prototype scale) and cloud compute.

---

## Phase 0 — Data Foundations & Scene Pipeline (Weeks 1–4)

The unglamorous layer everything else stands on. Done wrong, every downstream phase inherits silent corruption.

### 0.1 Ingestion connectors (the "promiscuous inputs" principle starts here)
Every source gets the same treatment: a connector that lands raw data immutably, a normalizer that maps it into a canonical schema, and provenance metadata (source, acquisition time, ingest time, processing version) stamped on every record. This is the Foundry discipline — raw → clean → conformed layers, never mutate raw.

Connectors to build, in order:
1. **Copernicus Data Space (Sentinel-1 GRD)** — query by AOI + time window, download IW-mode VV/VH scenes, maintain a scene catalog (footprint, orbit, timestamp, processing status). Includes the ESA preprocessing chain: orbit-file application, thermal noise removal, calibration to sigma-nought, terrain correction.
2. **Terrestrial AIS** — free/near-free aggregator streams + NOAA-style historical archives for training data. NMEA/JSON parser, deduplication, canonical position-report schema (MMSI, IMO, lat/lon, SOG, COG, heading, timestamp, message type, receiver source).
3. **Global Fishing Watch open datasets** — vessel tracks, fishing effort, and critically their published SAR-detection outputs. This is ground truth, gifted. Land it early; it feeds every evaluation harness after this.
4. **Static registries** — IMO/ship registry snapshots, port databases (WPI), sanctions lists (OFAC SDN, UN, EU consolidated). Versioned snapshots, diffed on each refresh — sanctions edges must carry *as-of* dates.

### 0.2 Storage & catalog
- Object store for raw scenes and tiles; columnar store (Parquet + a query engine) for AIS and detections; a lightweight metadata catalog so every artifact is discoverable by AOI/time/source.
- **Tiling scheme decided now:** all detections, tracks, and scenes indexed on a common spatial grid (H3 or slippy tiles). Entity resolution in Phase 3 lives or dies on cheap spatial joins.

### 0.3 Acceptance criteria (Phase 0 exit)
- One command backfills 90 days of Sentinel-1 over the AOI and keeps it current automatically.
- 30+ days of continuous AIS over the AOI landing with <1% parser drop rate, deduplicated.
- Every record answers: where did you come from, when, and through which pipeline version.

**Anti-goal:** no ML, no UI, no cleverness. Plumbing only.

---

## Phase 1 — SAR Ship Detection (Weeks 4–10, overlaps Phase 0 tail)

### 1.1 Detector v1: CFAR baseline
Constant False Alarm Rate detection on calibrated sigma-nought, with land/coastline masking (this is where most naive pipelines die — a bad land mask floods you with false contacts from breakwaters, islets, and aquaculture). Output: contact candidates with position, backscatter statistics, estimated length/width from the pixel blob.

CFAR is deliberately first: it's transparent, tunable, and gives an honest floor. Ship-scale objects at Sentinel-1 resolution (~10–20m) are detectable down to roughly 15–25m vessel length in favorable sea states; below that is physics, not engineering.

### 1.2 Detector v2: learned classifier on top of CFAR
CNN classifier over chips around each CFAR candidate to kill false positives (sea clutter, azimuth ambiguities — the ghost detections that appear offset from strong reflectors, wind farms, fixed infrastructure). Train on open benchmarks: **xView3-SAR** (the canonical dark-vessel dataset, built for exactly this problem), SSDD/LS-SSDD, and GFW's published detections as weak labels over our own AOI.

### 1.3 Evaluation harness (permanent fixture, not a phase deliverable)
- Held-out xView3 scenes + a hand-labeled set of ~20 AOI scenes.
- Metrics tracked per release: precision, recall, F1 at matched-detection level; length-estimation error; false positives per 1,000 km² (the operationally meaningful number — an analyst feels FP density, not F1).
- Every model change runs the harness. No exceptions. Silent regression here corrupts everything downstream and you won't see it until analysts stop trusting alerts.

### 1.4 Acceptance criteria (Phase 1 exit)
- ≥0.75 F1 on xView3 vessel detection (competitive open baselines sit in this range; we don't need SOTA, we need *trustworthy and measured*).
- False positives per scene low enough that a human can review a full scene's contacts in under 10 minutes.
- Fully automatic: new scene lands → contacts published to the detection store within 2 hours, no human touch.

---

## Phase 2 — AIS Track Engine (Weeks 6–12, parallel to Phase 1)

Detections without tracks are photographs; tracks are the memory the graph is built from.

### 2.1 Track builder
- Segment raw position reports into per-MMSI tracks; handle the real-world filth: MMSI reuse, duplicate MMSIs broadcasting simultaneously (a spoofing tell — log it, don't discard it), impossible-speed jumps, position noise.
- Kalman-smoothed track state with interpolation/extrapolation and *explicit uncertainty growth* over time since last report. That uncertainty cone is the core input to Phase 3 matching.

### 2.2 Gap detection & dark-period inference
- Classify every AIS gap: coverage gap (terrestrial receiver shadow — expected), satellite-pass gap (expected, schedulable), or **intentional silence** (transponder off in an area with known coverage). Requires building a coverage model of our own receivers/feeds — an honest map of where silence is meaningful. This coverage model is itself a product asset nobody else will have for Indian waters specifically.
- Add satellite AIS (Spire-class subscription) here; it collapses the coverage-gap ambiguity offshore and is the only paid data in the prototype.

### 2.3 Behavioral feature extraction
Per track: speed/heading profiles, loitering episodes, drift signatures, port-call sequences, encounter candidates (two tracks converging to <500m at <2kn — the rendezvous primitive). These features are cheap now and become the vessel's *behavioral fingerprint* — the thing a one-time spoof can't fake — in Phase 4.

### 2.4 Acceptance criteria (Phase 2 exit)
- 30-day continuous track store for the AOI; track fragmentation rate measured and <10% on a hand-checked sample.
- Every gap in every track labeled with a gap-type and confidence.
- Rendezvous-candidate detector running with a reviewed sample precision >70%.

---

## Phase 3 — Entity Resolution: The Fusion Core (Weeks 10–18)

This is the product. Everything before feeds it; everything after consumes it.

### 3.1 SAR↔AIS association engine
Probabilistic matcher associating each SAR contact to the AIS track picture at scene-acquisition time:
- **Gating:** which tracks could physically be at this contact's position given last-known state + uncertainty cone + max feasible speed.
- **Scoring:** position likelihood, length compatibility (SAR-estimated vs. registry length), heading consistency, historical presence in this cell.
- **Assignment:** global optimization across the scene (Hungarian/JV algorithm on the score matrix), not greedy per-contact matching — greedy matching double-assigns and manufactures phantom dark vessels.
- Output per contact: matched (with confidence), ambiguous (top-k candidates), or **unmatched = dark-vessel candidate**.

### 3.2 The dark-vessel product logic
Unmatched SAR contact → filter cascade before it earns the name:
1. Not explainable by AIS coverage gap at that time/place (uses Phase 2 coverage model).
2. Not a known fixed installation (rigs, buoys, wrecks — maintain a static-object layer that accumulates from repeated same-position detections; self-building).
3. Size above the detectability floor with margin.
Survivors get a dark-vessel score and enter the alert pipeline.

### 3.3 Launch posture: high precision, low recall — deliberately
Per the failure-mode analysis: alert fatigue kills trust before accuracy does. v1 thresholds tuned so that of every 10 dark-vessel alerts, ≥7 survive human review, even if that means missing half the true darks. Recall expands release by release as measured precision holds. This is a stated product policy, not a tuning accident.

### 3.4 Evaluation
- xView3 includes matched AIS — it's an end-to-end benchmark for exactly this association task. Target: association accuracy ≥85% on non-ambiguous contacts.
- GFW's published dark-detection outputs over our AOI as cross-validation: where do we and GFW agree/disagree, and why — every disagreement is either our bug or our edge.

### 3.5 Acceptance criteria (Phase 3 exit)
- End-to-end nightly run: scene → contacts → association → dark-vessel candidates, fully automatic.
- Reviewed dark-vessel precision ≥70% over a two-week live sample.
- **The demo exists:** "dark vessels off Porbandar from last Tuesday" is a real, reproducible query.

---

## Phase 4 — Object Graph & Ontology (Weeks 16–26)

Where detections become intelligence. This phase converts a detection pipeline into a platform, and it's the moat: accumulated edges, not algorithms.

### 4.1 Ontology v1 (small, correct, extensible)
Object types: **Vessel, Organization, Person (minimal), Port, Voyage, Encounter, Detection, Track, Alert, Sensor/Source.**
Edge types: *owned-by, operated-by, flagged-to, docked-at, met-with, detected-by, resolved-from, sanctioned-under, formerly-identified-as* (MMSI/name/flag change history — the identity-laundering edge).
Design rules learned from the Palantir playbook:
- Every edge carries provenance and confidence. No naked facts in the graph.
- Every assertion is time-scoped (*owned-by* has a start/end; sanctions have as-of dates).
- The ontology is versioned and migration-tested — it *will* change when the first real operator looks at it, and that change must not require a rebuild. The schema is a hypothesis until a navy has argued with it.

### 4.2 Identity persistence & vessel fingerprinting
- Fold registry data, AIS static messages, and detection history into persistent Vessel entities; detect and record identity changes (MMSI swaps, renames, reflaggings) as first-class events rather than data errors.
- Behavioral fingerprint per vessel from Phase 2 features + long-run detection history. This is the "three years of history a one-time spoof can't erase" asset — it compounds from the day the graph turns on, which is the argument for turning it on early even at prototype accuracy.

### 4.3 Confidence decay (the anti-corruption mechanism)
Old, unrefreshed tracks and stale edges lose confidence on a defined schedule instead of persisting as false facts. Decay parameters are per-edge-type and tunable. This is the designed answer to silent track corruption — build it into the graph engine now, not as a patch after the first embarrassing stale alert.

### 4.4 Graph event engine
Every new detection/track update/registry diff emits a graph event; rules subscribe to events and traverse edges. The canonical chain must work end to end: *Ship A met-with Ship B → traverse Ship B's owned-by → owner sanctioned-under OFAC → Ship A risk score jumps → alert with the full evidence chain attached.* One hop, then two hops, with cycle protection and traversal budgets.

### 4.5 Acceptance criteria (Phase 4 exit)
- Graph populated for the full AOI: every AIS-active vessel is an entity with registry, track, and detection history attached.
- The sanctioned-owner rendezvous chain fires correctly on synthetic injects and on at least one organic real-world case.
- Ontology migration test passes: add a new edge type with zero downtime and zero recompute of existing data.

---

## Phase 5 — Analytics, Alerting & Anomaly Library (Weeks 24–34)

### 5.1 Alert framework
- Alert = rule + evidence chain + confidence + disposition workflow (confirm / dismiss / watch). Analyst dispositions are captured as labels — **the proprietary feedback loop starts here**, and it's the data asset that makes the 70%-accurate system improve in ways a competitor cloning the architecture can't replicate.
- Watchlists: any graph query becomes a standing subscription ("alert me on any vessel two hops from this org entering the AOI").

### 5.2 Anomaly library v1 (each is a rule/model over the graph, shipped one at a time, precision-gated like Phase 3)
1. Dark-vessel detection (from Phase 3, now graph-enriched with vessel history).
2. AIS spoofing: impossible kinematics, duplicate MMSI, position/SAR contradiction (AIS says here, physics says nothing is here).
3. Dark rendezvous: SAR-detected encounter where one or both parties are AIS-silent — the ship-to-ship transfer signature.
4. Loitering near sensitive geometry (cables, pipelines, exercise areas, ports — geofence layer).
5. Identity-change-then-anomaly sequences (rename/reflag followed within N days by dark behavior — the laundering pattern).
6. Port-call risk propagation: calls at high-risk ports raise vessel scores via graph edges.

### 5.3 Risk scoring
Composite per-vessel risk score aggregating anomaly history, graph proximity to sanctioned/flagged entities, flag/ownership opacity, and fingerprint deviations. Explainable by construction: the score decomposes into its evidence chains, because an unexplainable score is unsellable to both a navy and an insurer.

### 5.4 Acceptance criteria (Phase 5 exit)
- Six anomaly types live, each with measured precision on live data.
- Disposition feedback measurably improves at least one detector (retrain and show the delta on the harness).
- Weekly automated "Indian Ocean anomaly summary" generated with zero human effort.

---

## Phase 6 — Product Surface (Weeks 28–38, overlaps Phase 5)

The layer that turns the platform into the laptop-rotation demo and then a usable analyst tool.

### 6.1 Operational picture (map UI)
- Live AOI map: tracks, latest SAR contacts, dark-vessel candidates, alert markers; time-scrubber to replay any window (the replay is disproportionately persuasive in demos — "watch this ship go dark and meet that one").
- Entity page per vessel: identity history, fingerprint, track archive, detections, graph neighborhood, risk score with decomposition.

### 6.2 Analyst workflow
- Alert queue with triage states, evidence-chain view (every alert renders its graph traversal as a readable chain), one-click disposition feeding 5.1.
- Graph explorer: click-through neighborhood navigation, saved queries → watchlists.

### 6.3 Reporting
- One-click incident report (PDF/doc) from any alert: imagery chip, track plot, evidence chain, confidence statement. This artifact is what an IFC-IOR watch officer or an insurance underwriter actually forwards internally — it's the product's viral loop inside a customer org.

### 6.4 Acceptance criteria (Phase 6 exit)
- A non-builder (you) can, unassisted: open the picture, find last week's dark vessels, open one, read why it's flagged, and export the report — in under 5 minutes.
- The full demo runs on a laptop against the live backend with no rehearsed data.

---

## Phase 7 — Hardening & Expansion Tiers (Weeks 36–52)

### 7.1 Connector expansion (the "each tier is a connector, not a rewrite" proof)
In priority order, each validating the ingestion abstraction:
1. **VIIRS night-lights** — free, immediately catches light-luring fishing fleets; cheap win.
2. **Sentinel-2 / Landsat optical** — free daytime confirmation imagery for alert enrichment.
3. **Commercial SAR tasking (ICEYE/Umbra class)** — build the connector and tasking-request workflow now, exercise it with a handful of paid scenes only when a demo or pilot justifies it. Unpredictable tasking is also the designed mitigation for adversaries gaming Sentinel-1's predictable revisit.
4. **RF geolocation (HawkEye 360 class)** — connector spec'd against their published formats; integration deferred until a customer funds the feed.
The architecture test: each connector lands in the canonical detection schema and flows through Phases 3–5 *with zero changes to the fusion core*. If any connector forces a core change, that's a Phase 3 design bug to fix before it calcifies.

### 7.2 Bay of Bengal AOI + scale hardening
Second AOI proves multi-region operation; drives the batch→streaming refactor where needed, cost profiling, and pipeline SLAs (scene-to-alert latency target: <3h from scene availability).

### 7.3 Commercial-tier product seed (the cash-flow layer)
Not a second product — a second *lens on the same graph*:
- **Vessel risk-score API + report** for marine insurers/P&I clubs: the Phase 5.3 score, packaged. Zero new pipeline work; packaging and thresholds only.
- **Port/anchorage analytics** (congestion, dwell, dark activity near terminals) from existing track data.
The discipline: commercial features may only consume the graph, never fork it. One platform, two doors.

### 7.4 Deployment posture work
Containerized, infra-as-code, single-tenant deployable — because the sovereign customer will eventually demand on-prem/air-gapped, and retrofitting that into a SaaS-shaped system is a rewrite. Cheap to keep true from the start, brutal to bolt on later.

---

## Visibility track — inspection dashboards (in force from Week 4)

Two dashboards exist in this plan and they are not the same thing. The **product surface** (Phase 6, weeks 28–38) is the operational picture built to be used and demoed. The **inspection dashboard** is a cheap, throwaway visual harness that exists purely so the build is watchable from week 4 onward — diagnostic, deliberately ugly, never shown to a customer, discarded and rebuilt as the Phase 6 surface.

Each phase ships an inspection view as part of its exit criteria:

| Week | Inspection dashboard shows | Kind |
|---|---|---|
| ~4 (Phase 0) | AOI map with raw AIS tracks and SAR scene footprints painting in — proof the pipes flow | Inspection |
| ~10 (Phase 1) | SAR scenes with detected ships boxed; live F1 / false-positive scoreboard from the eval harness | Inspection |
| ~12 (Phase 2) | Vessel tracks with silences highlighted; coverage model showing where silence is meaningful | Inspection |
| ~18 (Phase 3) | **Dark-vessel candidates plotted on the map** — the first view of the actual product output | Inspection |
| ~26 (Phase 4) | Click a dark vessel → graph neighborhood: owner, flag history, sanctioned-entity chains | Inspection |
| ~34 (Phase 5) | Live alert feed + auto-generated weekly anomaly summary, readable without engineering help | Transitional |
| ~38 (Phase 6) | The product: polished operational picture, replay, entity pages, triage queue, reports | Product |

Rules for the visibility track:
1. Inspection views are built with minimum effort — no polish budget before Phase 6. Any hour spent making them pretty is an hour stolen from the fusion core.
2. Each phase's inspection view is added to that phase's acceptance criteria: the phase isn't done until its output is visible on the map.
3. In-session, the dashboard is generated as a self-contained interactive artifact against that session's data snapshot; the standing 24/7 version is the deployed copy operated by the engineering team.
4. The first inspection dashboard (AOI frame, AIS tracks, SAR footprints) is built at the start of Phase 0, before the full ingestion pipeline is wired, so there is a visual frame everything else fills into.

## Cross-cutting engineering principles (in force from Week 1)

1. **Raw is immutable; everything downstream is reproducible.** Any output regenerable from raw + code version.
2. **Provenance on every record, confidence on every assertion, time-scope on every edge.** Non-negotiable, because the product *is* trust.
3. **The evaluation harness gates every release.** Precision is a product feature with a number attached.
4. **High precision before high recall, always,** for anything analyst-facing.
5. **Connector-shaped everything.** New data tier = new connector. Fusion core never learns source-specific hacks.
6. **The graph starts accumulating on day one of Phase 4** even at prototype accuracy — edge history is the compounding asset and it cannot be backfilled later.

## Milestone summary

| Milestone | Target | The one-line proof |
|---|---|---|
| M0 — Pipes live | Week 4 | 90 days of SAR + AIS landing automatically |
| M1 — Eyes | Week 10 | Ships detected in SAR, F1 ≥0.75, measured |
| M2 — Memory | Week 12 | Continuous AIS tracks with classified gaps |
| M3 — **Fusion** | Week 18 | Real dark vessels off the Indian coast, ≥70% precision |
| M4 — Graph | Week 26 | Sanctioned-owner rendezvous chain fires organically |
| M5 — Judgment | Week 34 | Six anomaly types live, feedback loop closing |
| M6 — **Demo** | Week 38 | The laptop rotation: live picture, replay, evidence, report |
| M7 — Platform | Week 52 | Second AOI, third sensor modality, commercial API seeded |

## Top product risks & designed mitigations

| Risk | Mitigation (built into the plan, not hoped for) |
|---|---|
| Alert fatigue destroys analyst trust early | Precision-gated launches (3.3, 5.2); recall grows only as measured precision holds |
| Silent track/graph corruption | Confidence decay (4.3); immutable raw + reproducibility (principle 1) |
| Sentinel-1 revisit gaps gamed by adversaries | Architecture assumes sparsity; commercial tasking connector (7.1) adds unpredictability when funded |
| Detection floor: small craft invisible at 10–20m resolution | Stated capability boundary in the product, not a surprise; VIIRS + optical partially compensate; commercial SAR closes it later |
| Ontology wrong before operator contact | Small v1, versioned, migration-tested (4.1) — cheap to change until it's expensive not to |
| Entity resolution too fragile | It's fragile *at bootstrap* by design; fingerprint history (4.2) makes it harder to fool every month it runs — the fragility is the moat |
