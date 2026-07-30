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

---

## ADR-013 — Download-only laptop mode *(Accepted)*
**Context.** The Oracle ARM VM (ADR-009) is **not provisioned**, and the operator
works from a Windows laptop that cannot stay powered on. Several Phase 0 exit
criteria were written assuming an always-on host: 30+ days of continuous AIS
capture, R2 object storage, a systemd service, and SNAP installed. None of those
assumptions currently hold, and they may not hold for some time. Until this was
written down, the mode existed only in session prompts — so the criteria it
invalidates were failing against an unrecorded decision, which is exactly how a
plan quietly rots.
**Decision.** Until a deploy host exists, the project runs in **download-only
laptop mode**:
- Every data source must be a **finite download**. No streaming, no always-on
  consumers.
- Storage is **local Parquet + DuckDB**. `MISR_STORE_BACKEND=local` is the
  default (was `mirror`).
- **Total downloaded data stays under 1 GB.**
- No R2, no systemd, no SNAP, no live AIS capture.
- Code that requires any of the above is **PARKED, not deleted** — marked
  `PARKED: awaiting deploy host` in its module docstring, with the reason and
  what would un-park it.
**Alternatives rejected.** *Provision the VM first and change nothing* —
rejected: it blocks all progress on an infrastructure step that has been pending
for weeks, when a large amount of useful ingest work needs no server at all.
*Delete the parked code* — rejected: it is correct code whose only fault is
needing a host; deleting it means rewriting it later.
**Consequences.** This decision is what retires the VM-dependent Phase 0 exit
criteria — see ADR-014, which amends them explicitly rather than leaving them
permanently red. Currently parked: `ingest/aisstream.py`,
`infra/aisstream.service`, `infra/mirror_cron.py`, `process/s1_preprocess.py`,
`process/validate_sigma0.py`, and the imagery-download half of
`ingest/copernicus.py`. Separately, `ingest/noaa_ais.py` is parked for a
different and permanent reason: Marine Cadastre covers the **US EEZ only** and
can never serve AOI v1 (see DATA_SOURCES.md). When a deploy host appears, this
ADR is superseded, not merely ignored.

---

## ADR-014 — Phase 0 exit criteria amended for download-only mode *(Accepted)*
**Context.** Three exit criteria cannot be met under ADR-013, and two of those
could not be met on the free path *at all* for AOI v1. Left unamended, Phase 0
stays permanently red for reasons that have nothing to do with build quality,
and "Phase 0 incomplete" stops carrying information.
**Decision.** Amend the following on the record. Each amendment states what was
required, what is required instead, and why.

**Roadmap Phase 0.3 — "30+ days of continuous AIS over the AOI, <1% parser drop
rate."**
→ *Amended to:* **GFW derived AIS event tables** (encounters, loitering, port
visits, AIS gaps) landed over the AOI for a rolling 8-week window, with
provenance and idempotent re-runs.
*Reason:* raw historical AIS positions are **not freely obtainable for this
AOI** — Marine Cadastre is US-waters only, aisstream is live-only and needs an
always-on host, and satellite AIS is paid (ADR-005). This is a data-availability
fact, not a build gap. The drop-rate criterion is retained verbatim and deferred
to whenever live capture begins; it is meaningless without a parser running.

**Spec unit 0.1 — "`ingest s1 --days 90` completes; catalog shows expected scene
count; re-run downloads nothing new."**
→ *Amended to:* **`ingest s1 --days 56 --catalog-only` completes; the scene
catalog shows the expected count for the AOI window; a re-run adds no rows.**
*Reason:* the imagery itself is GB-scale and both the 1 GB budget and the
absence of R2 (ADR-013) exclude it. Catalog metadata is kilobytes, needs no
credentials, and delivers the actual near-term value — letting every detection
be joined to the satellite pass that produced it, which is what separates "no
ship was there" from "nothing looked there." The download half is deferred
verbatim, not dropped.

**Spec unit 0.4 — "GFW SAR detections for our AOI queryable; sanctions tables
carry as-of dates; a re-run produces a diff, not a duplicate."**
→ *Clauses two and three stand unamended and are met.* Clause one is
→ *Amended to:* **GFW SAR ingest paths exist and are exercised: gridded presence
via the API, and per-detection via the Data Download Portal CSV importer.
Non-zero SAR rows are NOT required for unit closure while the upstream dataset
is offline.**
*Reason:* two independent upstream facts, neither ours. GFW's per-detection SAR
has **no API** — it is a browser CSV export only. And GFW's SAR datasets have
been **offline since 2026-07-03** pending their Sentinel-1C/1D migration. Gating
our unit on another organisation's outage would misreport our own readiness.
When SAR returns, this clause reverts to its original form with no code change.

**Alternatives rejected.** *Leave the criteria as written and mark Phase 0
permanently incomplete* — rejected: a criterion that can never pass stops being
a test and becomes noise; worse, it hides the criteria that genuinely haven't
been met yet. *Silently reinterpret them* — rejected: that is the failure this
whole document exists to prevent.
**Consequences.** Phase 0 can close under download-only mode. Each amendment
carries its reason and its reversion condition, so when the VM, R2, or GFW's SAR
feed arrives, the original criterion is restored rather than forgotten. **No
amendment weakens a criterion about our own correctness** — provenance,
idempotency, AOI scoping and as-of dating are all untouched.

---

## ADR-015 — One H3 helper; every resolution computed directly from coordinates *(Accepted)*
**Supersedes the helper clause of ADR-003; reinforces its resolution policy.**

**Context.** ADR-003 mandated "the **one shared H3 helper**." Two exist, at three
different resolutions, and they are both live:

| Helper | Resolution | Used by |
|---|---|---|
| `tiling.py` | **6** (`H3_RESOLUTION`) | `detect/pipeline`, `fusion/associate`, `fusion/dark`, `tracks/{builder,coverage,features}`, `connectors/{ais,registries}` |
| `fusion/dark.py` | **8** (`STATIC_RES`) | static-object clustering |
| `h3util.py` | **7 and 9** | `ingest/landing` (all ingest connectors), `ingest/registries`, `writer` |

This is a live defect, not a cosmetic one. The ingest tables stamp res-7/res-9
cells; the fusion core joins on res-6 cells. **Different resolutions produce
different cell IDs, so those joins return nothing** — the exact silent-miss
failure ADR-003 was written to prevent. It has not yet caused damage only
because nothing consumes the ingest tables yet.

**Decision.**
1. **One helper module** computes **every** resolution the project uses — 6, 7, 8
   and 9 — from lat/lon. The duplicate is deleted. No module computes cells
   itself, and no module hard-codes a resolution outside the helper.
2. **Every resolution is computed directly from coordinates. Never derive a
   coarser cell as the parent of a finer one.** H3's hierarchy is not perfectly
   nested: a res-7 cell is not geometrically contained in its res-6 parent, so
   `cell_to_parent` and direct computation disagree for points near boundaries.
   **Measured on 200,000 random points inside AOI v1:**

   ```
   parent(res-7 cell) != direct res-6 cell : 14,398  (7.199%)
   parent(res-9 cell) != direct res-7 cell : 12,341  (6.170%)

   example  8.720358N, 65.061535E
     direct res-6        86604ba17ffffff
     parent(res-7 -> 6)  86604ba07ffffff
   ```

   Roughly **1 record in 14** would be filed under the wrong coarse cell —
   position-dependent, intermittent, invisible in row counts, and far harder to
   diagnose than the present uniform mismatch.
3. **Fusion baselines must be re-measured, not carried forward,** if fusion's
   join resolution changes. Current synthetic-suite baseline, measured
   2026-07-29 at res 6:

   ```
   association accuracy   96.9%  (65 non-ambiguous contacts, target >=85%)
   dark-vessel precision  100.0% (6 flagged, exit >=70%)
   dark-vessel recall     75.0%  (of 8 ghosts above the size floor)
   ```

   These are **synthetic-suite numbers** (CLAUDE.md §4.6). After the change, the
   new figures are stated as the baseline; the old ones are not reused.

**Alternatives rejected.** *Pick a winner and convert everything to it* —
rejected as the whole fix: it resolves today's mismatch but leaves the structural
cause, two modules free to compute cells independently, so the next resolution
choice reintroduces it. *Derive res 6 from res-7 parents* — rejected on the
measurement above. *Change ADR-003's res 7/9 policy* — rejected: res 7 for joins
and res 9 for fine matching remain correct and are reinforced here; res 6 and 8
are additional working resolutions, not replacements.
**Consequences.** This is **its own session**, not a patch on the ingest work —
it touches eight modules in the fusion core, requires re-running the evaluation
harness, and must restate the baselines. Until it lands, ingest tables and fusion
tables **cannot be joined**, and any attempt to do so silently returns nothing.

---

## ADR-016 — Direct sanctions matching replaces ownership traversal on free data *(Accepted)*
**Amends the Phase 4 exit criterion. Companion to ADR-014.**

**Context.** Measured on the first live run, 2026-07-29: GFW registry ownership
covers **61 intervals across 9,184 vessels — 0.66%**, and identity history is
**1.05 records per vessel**, meaning most hulls have a single identity record and
no recorded rename or reflagging.

The roadmap's canonical chain (4.4) assumes *vessel met vessel → traverse
owned-by → owner sanctioned-under OFAC → alert*. At 0.66% ownership coverage,
combined with 14 encounters in an 8-week AOI window, that chain has an owner to
traverse for roughly 1 vessel in 150. The Phase 4 exit criterion requiring it to
fire **"on at least one organic real-world case"** cannot be met on free data for
this AOI — not because the code is wrong, but because the edge does not exist.

**Decision — two parts.**

**(a) Invert the chain: match sanctions to hulls directly.** OFAC SDN names
**1,516 vessels outright**, each with call sign, vessel type, tonnage, flag and a
`vessel_owner` field. So instead of asking GFW who owns a hull, match our
identified vessels against OFAC by IMO, then call sign, then normalised name. A
hit is a **direct sanctioned-vessel finding that needs no ownership edge at all**,
and OFAC's own `vessel_owner` column supplies the organisation side — the org
graph is built from the sanctions list rather than from GFW.

Match precedence is deliberate and must be preserved: **IMO > call sign > name.**
IMO is a permanent hull number; name and call sign are changeable and collide.
A name-only match is a *candidate*, never a finding, and must carry lower
confidence per CLAUDE.md §4.3.

**(b) Ownership-based risk propagation is a paid-feed feature.** The free tier is
**behavioural** — loitering, encounters, AIS gaps, port patterns, all dense and
free — plus **direct** sanctions matching. Ownership graphs (Lloyd's List
Intelligence, S&P Global Maritime, Clarksons class) slot in later as a connector
per ADR-001, exactly as Spire does for satellite AIS per ADR-005.

**Phase 4 exit criterion amended to:** *"the sanctioned-entity chain fires
correctly on synthetic injects, **and** a direct OFAC vessel match is
demonstrated on real AOI data."* The original organic-ownership-chain clause is
**deferred, not dropped** — it reverts the moment an ownership feed is funded.

**Alternatives rejected.** *Equasis / IMO GISIS scraping* — both are free-
registration ship databases with real ownership data, but bulk programmatic
access is very likely against their terms of use; **read the ToS before writing a
connector**, do not assume. Logged as research, not a plan. *Leave the criterion
unamended* — rejected: a criterion that can never pass is not a test, and hiding
behind it would let us imply an ownership capability we do not have. *Abandon
Phase 4* — rejected: ADR-011 stands, the graph still accumulates behavioural and
identity edges from day one, and those cannot be backfilled.

**Consequences.** The near-term product is honestly *behavioural anomaly
detection with direct sanctions matching over the Arabian Sea*, not
ownership-network intelligence. That is a smaller claim and a true one. **Do not
describe ownership-chain capability in any external material until a paid feed is
integrated and measured.** Two things follow for implementation: the matcher lives
on the ingest/graph side and the fusion core learns nothing OFAC-specific
(CLAUDE.md §4.5); and match confidence must reflect which key matched.

**Also settled here:** WPI is downgraded from blocked-dependency to optional.
GFW events already carry `distances.startDistanceFromPortKm` /
`endDistanceFromShoreKm` on **every** event type, and port visits carry full
anchorage records with lat/lon, name and flag. That answers "anchorage queue or
open-water loiter?" from data already landed, with no external dependency. WPI
remains useful only for global coverage of ports we have no events at yet.
