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

### ADR-015 — implemented 2026-07-29

Two corrections to the decision as originally written:

**There are FIVE resolutions in use, not four.** The audit missed
`COVER_RES = 4` in `tracks/coverage.py` (coverage is a regional property) and
`ENC_RES = 7` in `tracks/features.py`. The full set is **4, 6, 7, 8, 9**, and
`h3util.RESOLUTIONS` now declares all of them.

**`DEFAULT_RES` was deliberately kept at 6**, matching the old `tiling.py`
default. The decision said to fix the structure, not pick a winner, so fusion's
join resolution is unchanged and its behaviour is untouched by the refactor.

**What actually makes ingest and fusion joinable** is not the single helper on
its own — it is that `landing.stamp_h3` now stamps a cell at **every** project
resolution onto each located row. Any consumer joins at the resolution it needs
and nobody is tempted to derive one. Cost is a few short strings per row.

**Baselines re-measured on the synthetic suite, 2026-07-29, after the change:**

```
association accuracy   96.9%  (65 non-ambiguous contacts, target >=85%)
dark-vessel precision  100.0% (6 flagged, exit >=70%)
dark-vessel recall      75.0% (of 8 ghosts above the size floor)
```

**Identical to the pre-change figures** — which is the expected result of
preserving res 6, and is reported here as a re-measurement rather than a
carry-forward. Had they shifted, that would have signalled an accidental
behaviour change rather than a clean refactor.

Guarded by tests that assert: `tiling.py` stays deleted; no module outside
`h3util` computes cells (the one exception, `laptop_doctor`, verifies the h3 v4
API name exists and is documented as such); `cell_to_parent` appears nowhere;
and — protecting the *premise* rather than just the conclusion — that
parent-vs-direct disagreement is still measurably non-zero, so a future h3
release that made the hierarchy nested would fail the test and prompt revisiting
this ADR instead of obeying it blindly.

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

---

## ADR-017 — Phase 1 (own SAR) and xView3 are deferred, not scheduled *(Accepted)*
**Amends ADR-014's SAR clause. Operator decision, 2026-07-30.**

**Context.** Two things were on the near-term list: acquiring our own Sentinel-1
scenes and detecting vessels in them (Phase 1), and pulling the xView3 dataset to
train and measure a detector. Three facts landed against both:

1. **GFW's SAR datasets are offline**, so the free path to SAR *detections*
   (someone else's, already matched to AIS) is closed for now — see
   `DATA_SOURCES.md`.
2. **Per-detection AOI SAR has no API** we can drive from a laptop. Scene
   discovery works — 636 Sentinel-1 catalogue records are landed — but the
   pixels, and SNAP to process them, do not fit the download-only mode of
   ADR-013.
3. **The capability that actually works today is enrichment on landed data**:
   27,172 GFW events, 9,648 identity intervals and a versioned OFAC snapshot,
   all already on disk.

**Decision.** Phase 1 and xView3 are **deferred, with no scheduled date**. The
1 GB disk cap of ADR-013 stands and **nothing new is downloaded**. Work moves to:
populate the graph with real edges, measure whether the result is worth looking
at, and only then build something viewable — **in that order**.

The reasoning, in the operator's words: *"proving a capability nobody is asking
to see is the wrong spend."* Own-SAR is not on the M6 demo path. The demo path is
a map, a ranked list, a plain-English reason and an export — none of which needs
a pixel we processed ourselves.

**Deferred is not dropped.** Nothing here is deleted or rewritten; the Phase 1
modules stay in the tree under their `PARKED: awaiting deploy host` markers per
ADR-013. When GFW's SAR datasets return, or a deploy host with SNAP exists, this
reverts by un-parking, not by rebuilding.

**Alternatives rejected.** *Schedule Phase 1 anyway to keep the roadmap intact* —
rejected: a roadmap is a plan, not a debt. *Download xView3 now while the cap has
room* — rejected: the cap has room because we have not spent it, and spending it
on a dataset with no consumer is how prototypes end up with 900 MB of data and no
product. *Drop Phase 1 permanently* — rejected: the SAR path is the eventual
differentiator, and the code already exists.

**Consequences.** Until this reverts, **every dark-vessel and AIS-gap
determination in this system is Global Fishing Watch's, not ours.** We have run
no SAR, detected no vessel, and observed nothing going dark. That attribution
must travel with the number all the way to any UI — see ADR-018.

---

## ADR-018 — Match-tier corrections: IMO checksum, and call sign alone is a candidate *(Accepted)*
**Refines ADR-016(a). Three corrections, one framing rule.**

**Context.** The direct matcher landed 126 distinct vessels against the OFAC
snapshot — 98 by IMO, 29 by name, 0 by call sign. Reviewing what would happen if
those numbers were wrong surfaced three gaps.

**(a) IMO numbers are now check-digit validated.** OFAC has no IMO column; we
regex the number out of free-text remarks. Extraction review confirmed all 98
were keyword-anchored with a single 7-digit value — but that only proves we read
the right characters. It says nothing about whether the number *is* an IMO. The
seventh digit is a checksum (first six digits × 7,6,5,4,3,2, summed, last digit
of the sum), and it **rejects 90.3% of random 7-digit strings** — measured, not
quoted. `normalise_imo` now requires it, so a failing number cannot reach the
0.95 tier at all. This is independent evidence from the extraction check, which
is why both are kept.

**(b) A call sign alone is a candidate, not a finding.** Call signs are assigned
by the flag state, **reused after reassignment**, and short ones collide
internationally — a four-character call sign was never a globally unique key.
The tier splits: `call_sign_name` (call sign **and** name agree, 0.80, a finding)
above the 0.50 threshold, `call_sign` alone (0.40, a candidate) below it. Zero
call-sign matches exist today, so this is **policy set before it can fire**
rather than cleanup after it has. A missing name on either side does not demote —
absence of a name is not evidence against — it simply fails to promote.

**(c) The H3 regression guard now runs the join.** ADR-015's guard asserted that
ingest rows carry an `h3_r6` column. That is an existence check, and the defect
it guards is a *join* failure: both tables have healthy row counts, both have an
H3 column, and the query returns nothing. The test now lands a real ingest table,
runs the real `fusion.dark.dark_cascade`, joins them in DuckDB on the cell, and
asserts non-zero rows — plus a negative control at the wrong resolution, so a
test joining nulls to nulls cannot pass.

**The framing rule, to be held exactly as written.** *98 vessels matched by us
against GFW's event data* — **not** *98 sanctioned vessels detected by us*. No
SAR, no dark-vessel detection, nothing observed going dark by us. **The dark
determination is GFW's and must be attributed to them all the way to any UI we
eventually build.**

**Consequences.** Rows landed before this ADR carry the retired flat `call_sign`
tier semantics and any unvalidated IMOs; re-running the matcher is required
before its output is quoted. The review tool flags unrecognised tiers for exactly
this reason.

---

## ADR-019 — Scenario data lives in the real tables, distinguished by `is_synthetic` *(Accepted)*
**Operator decision, 2026-07-31. Companion to ADR-011 and ADR-018.**

**Context.** The graph populated from real landed data is real but **star-shaped**
(STATE.md, 2026-07-30): 17,562 nodes and 20,026 current edges, and **0 of 98
OFAC-matched vessels have a single encounter edge**. There are **zero** GFW-flagged
`intentionalDisabling` gaps in the whole corpus. Fourteen encounters landed for the
entire AOI over eight weeks. Every path between two vessels runs through a shared
port or flag, which thousands of unrelated ships also share.

That is a true finding about free data, not a bug, and it stands. But it leaves the
fusion core, the traversal budget, the decay policy, the anomaly library and the
risk scorer with **almost nothing to exercise** — code that has never met a
multi-hop chain, an identity break, or a decoy that should not fire.

**Decision — four parts.**

**(a) Synthetic scenario data lands in the SAME tables as real data**, flagged
`is_synthetic BOOLEAN NOT NULL DEFAULT FALSE`, and carrying `source_id
"synthetic-scenario"` in the existing provenance envelope. Not separate tables, not
views, not a parallel schema.

*Reason:* scenario data must exercise the **identical code path** as real data —
`from_landed.populate`, the track engine, decay, the anomaly library, risk scoring —
or it proves nothing about the real system. A parallel schema would only prove the
parallel schema works. This is already vindicated: landing here surfaced four real
defects (see Consequences).

**(b) The flag and the source id can never disagree.** `stamp_envelope` and
`GraphStore.add_edge` both **refuse the write** when they do. Two markers for one
fact are safer than one only if they cannot drift; a row flagged synthetic with a
real source would make every split silently wrong in a way no row count reveals.

**(c) Reserved identifier ranges, recorded here so they are never re-litigated:**

| Space | Reservation | Why it is safe |
|---|---|---|
| MMSI | `999000000`–`999999999` | 999 is not an assignable Maritime Identification Digit. The block is **structurally unreachable** by a real transmitting vessel, and stays so as new ships register. |
| IMO | `1000000`–`1999999`, **checksum-valid** | IMO ship numbers run from 5000000 upward. Checksum-valid so they exercise `normalise_imo`'s check-digit path exactly as a real number would. |
| Sanctions | `SCENARIO-SDN-nnnn` | A fictional list. A synthetic designation **never** points at a real OFAC entry number, and terminates on its own `authority:SCENARIO-SDN` node rather than on `authority:OFAC`. |

The ranges are defence in depth. The actual guarantee is `assert_no_collisions`,
which checks every generated identifier against the landed corpus at generation
time, falls back to the corpus profile when the corpus is absent, and **reports
which of the two it used** — a check that silently degrades to checking nothing is
the failure this project has already hit four times.

**(d) `scenario_truth` is ground truth and no detection, fusion, graph, scoring or
alerting code may read it.** A test parses those packages with `ast` and fails the
build on any import or live reference (docstrings that *document* the rule are
excluded — a grep cannot tell a promise not to read something from a read, and the
test carries its own negative control).

**Alternatives rejected.** *Separate tables or a synthetic-only view* — rejected: it
tests the synthetic path, which is not the product. *Real identifiers so the data
"looks realistic"* — rejected outright: a synthetic hull wearing a real IMO is a
false accusation sitting in the same table as our findings, and no downstream filter
undoes it once quoted. *Generate decoys more cheaply than true positives* —
rejected: a detector would separate them on craftsmanship and the precision figure
would measure the generator. A statistical separability test enforces this.

**Consequences.**

**Framing, which travels with every number.** Real findings and scenario data are
reported as **separate lines, never blended**. `scenario status` and the pipeline
report print every count split, and there is no combined total anywhere in the
output by design. The demo is pitched openly as containing scenario data.

**The real-data findings are unchanged and still stand.** 98 IMO matches, 0 of 98
with an encounter edge, 0 flagged gaps, 14 real encounters. Nothing here improves
them and the split makes that checkable.

**Four defects found by landing scenario data through the real path**, none visible
before:
1. `GraphStore.__init__` would **crash on any existing graph** — the `is_synthetic`
   index was declared in `_SCHEMA`, which runs before the migration that adds the
   column. Every pre-migration `graph.sqlite` would have raised `no such column`.
2. **The pipeline uses two vessel keyspaces that do not join.** `from_landed` keys
   hulls `vessel:gfw:<vessel_id>`; `graph.identity.resolve_mmsi` mints
   `vessel:mmsi:<mmsi>`. Alerts land on nodes the landed graph has never heard of.
   This is the ADR-015 failure class again — two modules, two keys, joins silently
   empty. **Not fixed in this session**; the measurement bridges it explicitly and
   reports it. It is the highest-value next fix.
3. `VoyagePlan` had no initial speed, so every leg boundary restarted the vessel
   from a dead stop — a 165° course change inside one 60 s step on a hull limited
   to 0.25 °/s.
4. Landing merges on a natural key *within a day partition*, so a scenario whose
   timing shifted between runs landed twice. `generate` now clears first.

**Migration is zero-recompute.** SQLite `ADD COLUMN` with a constant default is
schema-only: no existing row is read or rewritten, and every pre-existing row is
real, for which `FALSE` is correct. This matters because the graph accumulates
history that cannot be backfilled (ADR-011). Parquet partitions written before the
column existed are **not rewritten**; `read_table` defaults the missing value to
`False` on read.

**Cast size deviates from the plan, deliberately.** The build plan asked for 45–60
vessels; the corpus has **74 principals plus a 40-vessel fishing fleet**. The
catalogue is 29 scenarios plus 12 decoy families, and a vessel cannot be in two
places at once — `world.add_track` now refuses overlapping segments and
implausible repositioning, which is what forced the honest count. The
fleet-aggregation decoy is sized to the phenomenon: eight vessels would not resemble
the mass rendezvous it exists to test.
