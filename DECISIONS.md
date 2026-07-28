# DECISIONS.md — Architecture Decision Records

One entry per non-obvious choice. Format: **Context / Decision / Alternatives
rejected / Consequences.** These exist so a decision isn't silently reversed the
first time it's inconvenient. Several were made implicitly during planning and are
written down here for the first time — those are marked *(implicit)*.

Status legend: **Accepted** = in force. **Deferred** = decided but not built.

---

## ADR-001 — Free data first, paid feeds as connectors *(Accepted)*
**Context.** The commercial thesis is a sovereign maritime-fusion platform; the
prototype has to prove the fusion works before anyone pays for premium feeds.
**Decision.** Build the entire prototype on free/open data ($0 operating cost).
Every paid or classified feed is added later as a **connector** into the canonical
schema, never a change to the fusion core.
**Alternatives rejected.** Start with paid satellite AIS (Spire) for clean
offshore coverage — rejected: costs money before value is proven, and lets the
core grow source-specific dependencies that break the connector thesis.
**Consequences.** Offshore coverage is honestly incomplete until Spire is funded
(see ADR-005). The connector abstraction must be right from Phase 0, because it's
the thing being sold. Any connector that forces a core change is a design bug.

---

## ADR-002 — DuckDB over Parquet, not a database server *(Accepted)*
**Context.** Workload is analytical scans over AIS/detection/track/edge tables for
a solo prototype.
**Decision.** DuckDB reading Parquet files directly. No Postgres, no server.
**Alternatives rejected.** Postgres/PostGIS — rejected: a server to run, secure,
back up, and pay for, buying nothing until concurrent writers exist (none do).
SQLite — rejected: row-oriented, poor for the columnar analytical scans that
dominate here.
**Consequences.** Spatial work is done via H3 (see ADR-003) rather than PostGIS
geometry ops. If a future unit needs concurrent multi-writer transactions, revisit
— but only then.

---

## ADR-003 — H3 as the spatial substrate, res 7 for joins / res 9 for fine *(Accepted)*
**Context.** Phase 3 association is the product, and it hinges on cheaply answering
"which ships could be at this radar contact?" across growing data.
**Decision.** Stamp every located record with H3 cells at ingest. **Res 7 (~5 km)**
is the join key; **res 9 (~170 m)** is for fine matching. Common grid across AIS,
SAR, and footprints turns association into a hash join.
**Alternatives rejected.** Runtime geometry intersection (PostGIS / shapely) —
rejected: scales badly and couples association to a geometry engine. Slippy tiles
— rejected: square tiles have non-uniform neighbor distances; H3 hexagons have
uniform adjacency, which matters for ring/gating queries.
**Consequences.** All modules must use the **one shared H3 helper** and **h3 v4**
names (`latlng_to_cell`, `grid_disk`). Inconsistent cell computation is the first
thing to suspect when a join misses. Res choice is fixed project-wide — changing it
later means recomputing every located record.

---

## ADR-004 — High precision, low recall at launch — as policy *(Accepted)*
**Context.** Failure-mode analysis concluded that **alert fatigue destroys analyst
trust before accuracy problems do**.
**Decision.** Tune every analyst-facing detector so **≥7 of 10 alerts survive human
review**, even at the cost of missing half the true positives. Recall rises release
by release **only while measured precision holds**. This is a product policy, not a
tuning artifact. For dark-vessel conviction specifically: **≥2 missed satellite
passes** before asserting INTENTIONAL_SILENCE.
**Alternatives rejected.** Maximize recall / F1 — rejected: a high-recall system
that cries wolf gets ignored, and an ignored system has zero value regardless of
its F1.
**Consequences.** Early demos will visibly *miss* some real dark vessels. That is
acceptable and must be framed as deliberate, not as a bug. Precision is a product
feature with a number attached; the eval harness gates it.

---

## ADR-005 — Spire satellite AIS deferred, but interfaced now *(Deferred)*
**Context.** Terrestrial + free AIS has coverage holes offshore; Spire-class
satellite AIS would close them, at low-hundreds-USD/month.
**Decision.** Write the connector **interface** (`ingest/spire_stub.py`) against
the canonical schema now; leave the body unimplemented; Phase 3 codes against the
interface. Fund and implement only when a demo/pilot justifies the cost.
**Alternatives rejected.** Integrate Spire now — rejected: violates the $0 thesis
before value is proven. Ignore it entirely — rejected: retrofitting the interface
later risks a core change.
**Consequences.** Offshore gaps stay `unknown` (ADR-004 honesty rule) until funded.
The stub proves the connector abstraction accommodates a paid feed without a core
change — that proof is itself a selling point.

---

## ADR-006 — Terrain correction is geocoding-only over ocean *(Accepted)*
**Context.** SNAP's terrain correction can also do **radiometric flattening**,
which triggers DEM (elevation) tile downloads. Those downloads **hang** in the
pipeline.
**Decision.** Terrain-correct for **geocoding only** (pixel positioning). Do **not**
enable radiometric flattening.
**Alternatives rejected.** Full radiometric terrain flattening — rejected: causes
the DEM-download hang, and over open ocean there is no terrain to flatten, so it
buys nothing for our use case.
**Consequences.** Over-land backscatter would be less physically accurate, but the
AOI is ocean-dominant and detection runs over water. Documented so nobody "fixes"
the pipeline by turning flattening back on.

---

## ADR-007 — pyroSAR + SNAP `gpt`, not the `snappy` bridge *(Accepted)*
**Context.** SAR preprocessing needs ESA SNAP; SNAP offers a Python bridge
(`snappy`) or command-line `gpt` with XML workflow graphs.
**Decision.** Drive SNAP through pyroSAR, which calls `gpt` with workflow XMLs.
**Alternatives rejected.** `snappy` Python bridge — rejected: brittle to install,
painful to version-pin, a recurring source of environment breakage.
**Consequences.** Preprocessing is reproducible and scriptable. SNAP install is
still the fiddliest unit in the project (0.2) — budget a full debugging session,
and see OPEN QUESTIONS on ARM.

---

## ADR-008 — SNAP memory cap for the 24 GB VM *(Accepted)* *(implicit)*
**Context.** SNAP is a memory-hungry JVM app; the target VM has 24 GB total shared
with everything else.
**Decision.** Cap SNAP: **12 G JVM heap**, bounded tile cache, **4 threads**
(matching the 4-core VM). Encoded in the install script.
**Alternatives rejected.** SNAP defaults — rejected: they can consume all RAM and
OOM-kill the box mid-scene.
**Consequences.** Preprocessing is slower but stable. If the VM spec changes, these
caps must change with it.

---

## ADR-009 — Oracle Cloud always-free ARM VM as compute *(Accepted)* *(implicit)*
**Context.** Need standing compute for live AIS capture, preprocessing, nightly
fusion, and the API — at $0.
**Decision.** Oracle Cloud always-free **ARM (Ampere) VM, 4 cores / 24 GB RAM.**
**Alternatives rejected.** Paid cloud VM — rejected: violates $0 thesis. Eshan's
Windows laptop as host — rejected: can't run a 24/7 systemd service or always-on
capture. Free x86 tiers — rejected: Oracle's free x86 is far smaller than its ARM
allocation.
**Consequences.** **ARM (aarch64) is a real risk surface** for SNAP and ML tooling
— see OPEN QUESTIONS in STATE.md. This must be validated early; if SNAP won't run
on ARM, preprocessing needs a rethink (x86 burst instance for preprocessing only,
or a different SAR toolchain).

---

## ADR-010 — Cloudflare R2 for raw object storage *(Accepted)* *(implicit)*
**Context.** SAR scenes are large and re-read often (re-runs, reprocessing).
**Decision.** Cloudflare R2 free tier for raw scenes and chips.
**Alternatives rejected.** AWS S3 — rejected: egress fees, and we re-read a lot.
Local disk only — rejected: VM disk is finite and not durable across rebuilds.
**Consequences.** Zero egress cost is the reason this is affordable. Live AIS writes
land locally first; only **closed** partitions mirror to R2.

---

## ADR-011 — Graph turns on early, at prototype accuracy *(Accepted)*
**Context.** The object graph's value is accumulated edge history (identity changes,
encounters, port calls) — the moat. History **cannot be backfilled**.
**Decision.** Turn the graph on at Phase 4 and let it accumulate from day one, even
while accuracy is low.
**Alternatives rejected.** Wait until detection is accurate, then start the graph —
rejected: you permanently lose the intervening history; you can't record what you
weren't watching.
**Consequences.** Early graph data is noisy; confidence decay (unit 4.3) and
provenance keep noise from calcifying into false "facts."

---

## ADR-012 — Naming: Maritime ISR only; "Bastion" retired *(Accepted)* *(implicit)*
**Context.** An early planning doc (`bastion-product-roadmap.md`) uses the codename
"Bastion." The project is named Maritime ISR.
**Decision.** **Maritime ISR / `maritime_isr` exclusively.** No file, module, class,
or doc carries "Bastion." Read "Bastion" in the roadmap as "Maritime ISR."
**Alternatives rejected.** Keep Bastion as an internal codename — rejected: two
names for one thing invites drift and confusion in a solo build.
**Consequences.** The roadmap file's title is a known, tolerated artifact of
history; do not propagate it. Do not "restore" the Bastion name anywhere.
