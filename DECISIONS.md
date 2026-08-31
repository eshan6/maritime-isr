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

---

## ADR-020 — A port visit's span is not its dwell *(Accepted)*
**2026-07-31. Follows ADR-015 (H3 unification) in kind: a landed table that the
code has since outgrown.**

**Context.** Profiling the operator's corpus produced `port_call_dwell_hours`
with a **75th percentile of 1,263 hours (52 days) and a 95th of 20,253 hours —
2.3 years**. A generator sampling that tail put background vessels alongside for
months and overran the corpus window. The initial reading was that GFW's
port-visit events contain corrupt durations.

They do not. The number being profiled was `duration_hours`, which is GFW's
**event span**, and it was being read as time alongside.

A GFW port visit is stitched from up to four sub-events — `PORT_ENTRY`,
`PORT_STOP`, `PORT_GAP`, `PORT_EXIT` — and the event's `start`..`end` covers
whichever of them GFW managed to observe. That span is a dwell only when the
vessel **stopped** and the anchorage it **entered is the anchorage it left**.
Otherwise the same number measures a transit across a port polygon, or an entry
and an exit at two different anchorages with everything in between unobserved.

The mapper that wrote the 3,000 landed rows recorded none of the fields needed
to tell those cases apart. Measured on that corpus: `confidence` and
`gfw_confidence_raw` are null on **100%** of port visits, and `port_name` — read
from the stop anchorage and from nowhere else — on **45.6%**. The mapper was
fixed on 2026-07-29 (`_confidence_raw` looks under `port_visit.confidence`; the
three anchorage records are flattened onto the row). The landed rows were not,
and cannot fix themselves.

**Decision — four parts.**

**(a) `duration_hours` keeps meaning exactly what GFW said.** It is never
clamped and never corrected. A cap in the ingest layer would destroy the
evidence that the source produces these spans and would put a magic number
where nothing downstream could see it.

**(b) `dwell_hours` is a new column, populated only where the structure supports
the claim** — an observed stop, and every anchorage id present in agreement.
Everywhere else it is NULL, which is the honest answer: we do not know how long
this vessel was alongside. `visit_anchorages_agree` is `None` rather than
`False` when fewer than two anchorage ids are present, because "they disagree"
and "we cannot tell" are different facts and collapsing them lets an unknown
read as a finding.

**(c) The landed table is re-derived from raw, not patched.** This is what
CLAUDE.md §4.2 exists for: raw is immutable and on disk, so
`tools/rebuild_conformed.py` re-runs the current `map_event` over the same bytes
GFW returned. No network — ADR-013 puts the machine in download-only mode, and
re-running the connectors would return a *different* window of events, changing
the corpus in the course of repairing a column. Synthetic rows are carried
through untouched (they share partitions by ADR-019), real rows with no raw
record are preserved and reported loudly, and `ingested_at` is preserved while
`pipeline_version` is restamped: a re-derivation is not a new ingest, but it is
new code.

**(d) The generator emits the same mix of visit structures.** If every synthetic
port visit were a clean dwell, `WHERE dwell_hours IS NULL` would be a perfect
real-row detector — the null-rate failure family from `nulls.py` reached through
a third door. The class is allocated **stratified, not sampled**: 45 port visits
cannot hit a 40% target on independent draws (the first attempt landed at 64%),
so rows are ordered by a hash of their event id and sliced at the cumulative
fractions. The fields are then derived by the same rule the real mapper applies,
because independent per-column draws would hit every marginal and still produce
rows that are jointly impossible — a dwell with no stop.

**Consequences.**

- **GFW's own port-visit confidence was being dropped entirely and is now
  recovered.** Measured on host: `confidence`, `gfw_confidence_raw` and
  `visit_confidence` all go from **0% to 100%** coverage across the 3,000 rows.
  That is the single largest gain here — it is GFW's own judgement about how
  much of the visit they actually saw, and it was on the floor.
- `visit_port_id` resolves a port across entry, stop and exit and records which
  one it used, because a port attributed from the exit is a weaker claim than
  one attributed from the stop. **On this corpus it changes nothing today** —
  see the correction below — and it is kept as a guard for corpora where the
  stop anchorage is absent.
- **A latent landmine in the landing layer surfaced and is fixed.** Arrow types
  a column from the values present, so a day partition where `dwell_hours`
  happened to be null in every row was typed `null` while its sibling was
  `double`; read together, DuckDB fails outright and **one sparse column makes
  the whole table unqueryable**. `landing.reconcile_null_columns` retypes those
  partitions after every land. The bug does not fire when a column is added — it
  fires the first time a partition comes out empty, which is why it needed a
  test rather than vigilance.
- **What this deliberately does not fix: the tail on synthetic
  `duration_hours`.** Real spans reach 2.3 years because GFW stitched across an
  interval longer than the entire eight-week scenario window; manufacturing that
  would mean emitting an event that never happened. Port-visit `duration_hours`
  therefore remains a channel on which the two populations differ. It is
  recorded here rather than papered over, and `dwell_hours` — the field anything
  reasoning about time alongside should use — is the matched one.
- `PLAUSIBLE_MAX_HOURS` stays as a floor under the generator even though its
  input is now sane, so an unnoticed change in the source's semantics cannot put
  vessels alongside for months again.

### ADR-020 — CORRECTION, 2026-07-31, after the first host run

**The explanation above is wrong, and the rebuild's own audit is what proved
it.** Recorded rather than edited away, because the reasoning error is the
instructive part.

Measured on the host, over all 3,000 real port visits:

```
observed a stop              3,000 (100.0%)
entry and exit agree         2,611 (87.0%)
entry and exit differ          389 (13.0%)
dwell_hours populated        2,611 (87.0%)

                     p05     p25     p50        p75            p95
duration_hours      4.0h   21.0h   107h   1,261h (52d)   20,242h (843d)
dwell_hours         3.7h   18.8h    92h   1,184h (49d)   19,862h (828d)
```

**Structure does not explain the tail.** Every visit has an observed stop, 87%
are structurally clean dwells, and the clean-dwell p95 is still 828 days. Two of
the five longest spans — over 5,000 days each — classify as `dwell`.

Two specific claims were false:

1. **"~46% of visits have no observed stop."** `port_name` is null on 45.6% of
   rows, and I read that as a missing stop. It is not: the intermediate
   anchorage is present on **100%** of visits, it simply has no `name` on 46% of
   them. `port_id` — which comes from the same anchorage's `id` — was **100%
   populated before the change**.
2. **"~46% of port visits were being dropped by the graph."** It follows from
   (1) and is equally false. `add_port_visits` keyed on `port_id or port_name`,
   `port_id` was always there, and nothing was being skipped. The number was
   never measured; it was inferred from a null rate on a different column.

**What survives, and it is not nothing:** GFW's port-visit confidence recovered
from 0% to 100%; `orphans: 0`, so raw really is sufficient to regenerate the
conformed layer and CLAUDE.md §4.2 holds on this corpus; `duration_hours`
quantiles identical before and after, confirming nothing clamps; the 13%
entry≠exit split is real structure now recorded; and the all-null-column landing
bug is real and fixed.

**What `dwell_hours` is now understood to be:** a narrower, better-defined field
than `duration_hours`, and **not** a solution to the long-duration problem. Any
claim that it is the "distributionally matched" field is withdrawn.

**The live hypothesis, and why it needs no bug anywhere.** The connector asks GFW
for events *overlapping* an eight-week window. A visit lasting fourteen years
overlaps every possible window; a visit lasting twelve hours only overlaps if it
falls inside one. An overlap query therefore over-samples long events in direct
proportion to their length, so the observed duration distribution is not the
distribution of port calls — it is that distribution multiplied by duration.
Separately, the pull returned **exactly 3,000** events, which is a result cap
rather than a count, making the sample biased in a second, unknown way.

If that is right, the fix belongs in **the profiler, not ingest**: measure only
visits fully contained in the query window, and state the cap. Ingest should
keep landing exactly what GFW returned. `tools/port_visit_forensics.py` reads
the raw payloads and answers it — pull size, GFW's `durationHours` versus
`end - start`, contained versus straddling durations, and the extreme records
printed verbatim rather than reasoned about.

**The process lesson, which is the reason this correction is in the file at
all.** The first explanation was built by reasoning about what a field *means*
from a null rate on a neighbouring column, and it survived writing an ADR, a
tool, a validator, sixteen tests and a PR description before any of it met the
data. Every one of those artefacts was internally consistent and jointly wrong.
The audit output in `rebuild_conformed.py` is what caught it, which is an
argument for tools that print what changed rather than asserting that it worked.

### ADR-020 — RESOLVED, 2026-07-31, from the raw payloads

`tools/port_visit_forensics.py` on the operator's corpus. **Nothing is wrong
with the data. The measurement was wrong.**

**1. Length-biased sampling, confirmed, and it is the whole effect.**

```
query window: 2026-06-04 .. 2026-07-30 (56 days)
fully inside the window : 1,278 (42.6%)
crossing an edge        : 1,722 (57.4%)   <- all start BEFORE the window opened

                        p05     p25     p50        p75          p95           max
all visits             4.0h   21.0h    107h  1,261h(53d)  20,242h(843d) 126,414h(5,267d)
contained (unbiased)   2.2h    7.6h   18.7h     46.5h       262h(11d)     1,224h(51d)
crossing an edge      24.2h   133h    855h   2,556h(107d) 35,545h(1,481d) 126,414h
```

Median contained **18.7 h**, median straddling **856 h** — a ratio of **46x**.
The contained distribution is an entirely ordinary population of port calls,
topping out at 51 days, which is the window. The tail lives entirely in events
that were already in progress when we started looking.

**2. The extreme records are correct.** Printed verbatim rather than reasoned
about, and they are exactly what they should be:

| span | anchorage | `atDock` | what it is |
|---|---|---|---|
| 5,022 d | `ind-ind-76`, `topDestination: ALANG` | true | **Alang is the world's largest shipbreaking yard.** Arrived 2012 to be scrapped. |
| 5,054 d | `ind-pipavav`, PIPAVAV | true | laid up alongside since 2012 |
| 4,904 d | GHOGHA ANCHORAGE | false | laid up at anchor since 2013 |

A ship that went to Alang in 2012 really has been there ever since. Calling
that a degenerate duration was our error, not GFW's.

**3. GFW's duration agrees with ours to the second** — `port_visit.durationHrs`
= 126,413.72 against `end - start` = 126,413.72. There is no discrepancy to
explain, because there was never a discrepancy.

**Decision.** The fix is in the profiler and **nowhere else**. Ingest keeps
landing exactly what GFW returns, unclamped. `tools/corpus_profile.py` measures
`port_call_dwell_hours` from window-contained visits and writes the unfiltered
figure separately as `port_visit_span_hours`. The `PLAUSIBLE_MAX_HOURS` cap now
never fires on the de-biased figure and stays only as a floor under the
generator.

**Three things the raw payloads corrected that had nothing to do with the
original question**, each one a field being read from the wrong place:

1. **`durationHrs`, not `durationHours`, and nested inside the sub-object.**
   Every duration in the corpus was ours, computed from `end - start`, while
   GFW's own number sat unread. They agree here — but a value we compute and a
   value the source asserts are different kinds of fact, and substituting one
   for the other is how a future discrepancy would stay invisible. Both
   spellings are now accepted at both levels. **`gap.durationHrs` was affected
   the same way**, which means `gap_duration_hours` was null on every gap.
2. **`topDestination` is present on 100% of anchorages; `name` on 54.4%.** The
   readable place — VADINAR, MUNDRA, ALANG — was there all along and was not
   being landed. An anchorage that renders as `ind-ind-76` in front of an
   operator is a worse answer than one that renders as ALANG.
3. **`anchorageId`** is a distinct stable key from `id` and is now landed.

**The 3,000 cap stands as a separate, unresolved finding.** The pull returned
exactly 3,000 port visits in one file. That is a page limit, so the corpus is a
sample of unknown size and unknown selection, and no count taken from it
describes the Arabian Sea. `data_health.py` flags any round row count for this
reason. Paginating the events connector is the fix and is not done.

**The process lesson, restated because it cost three passes.** Every wrong
answer in this ADR came from reasoning about what a field *means* from summary
statistics — a null rate, a quantile — rather than reading one record. The
corpus was never in the sandbox, and the correct first move was a ten-line
diagnostic that printed a payload, not an explanation built on a neighbouring
column's null rate. Both the structural theory and the "46% dropped by the
graph" claim would have died in the first minute against a single raw record.

---

## ADR-021 — A check that cannot tell absence from breakage is not a check *(Accepted)*
**2026-07-31. Generalises the pattern in STATE.md's "host-only bugs" list.**

**Context.** Three separate faults in one day, all the same shape:

1. **`gfw_intentional_disabling` was null on every gap row**, because the mapper
   that landed them never read the field. `tools/analytic_rename_gap.py` and
   `graph_report.py` both consumed it, found nothing, and STATE.md recorded
   *"ZERO GFW-flagged intentionalDisabling gaps in the whole corpus"* as a
   **finding about the world**. It was a finding about our mapper. Re-derived
   from raw, **5 of 5 gaps are flagged** — and that number was one of the two
   inputs to the decision not to build a graph UI.
2. **`ofac_imos_from_duckdb` returned a bare `set()` on every failure path**, so
   the profiler printed `0 vessel IMO(s)` on a machine holding 1,516 designated
   vessels, and the identifier collision guard ran against a denominator of 121
   while reporting success.
3. **`connect(read_only=True)` raised on every call** — `_register_views` used
   `CREATE OR REPLACE VIEW`, which writes to the database file and is refused in
   read-only mode. This is *why* (2) failed, and nobody knew because (2)
   swallowed the exception.

Each of these produced a **zero that looked like a measurement**. None produced
an error. The earlier examples in STATE.md — the green `doctor` that masked
three faults — are the same family, and the lesson had been written down and
then not applied.

**Decision.** Any code path that answers "how many X are there" must be able to
distinguish **"the answer is zero"** from **"the question could not be asked"**,
and must say which.

Concretely:

- **No bare empty return on a failure path.** Return a result carrying `ok` and
  a `reason`, or raise. `ofac_lookup.ofac_snapshot()` is the reference shape:
  it names the table it looked in, the tables it tried, the row count it
  scanned, and what went wrong.
- **A reason must be actionable.** "unavailable" is not a reason; "no OFAC table
  found, tried ofac_sdn/sdn/ofac, the database holds 7 tables including
  ofac_entries" is.
- **Report both directions.** `data_health.py`'s dark-vessel-gap check printed a
  warning only when the count was zero, so the count silently disappeared from
  the report the moment it went non-zero — hiding the single most demo-relevant
  number in the corpus. Checks now print the measured value whichever way it
  falls.
- **A null column and an absent finding are different.** Before concluding
  anything from an empty result, check whether the input column is populated at
  all. `analytic_rename_gap.py` already distinguishes three cases (empty table /
  all-null verdict / genuine negative) — that pattern is the requirement, not a
  nicety.

**Alternatives rejected.** Exceptions everywhere — rejected: a profiler that
dies because one optional lookup is unavailable is worse than one that reports
the gap and continues, and the caller is the right place to decide. Logging the
reason instead of returning it — rejected: the number ends up in a report, a
commit message and a PR description, and the caveat has to travel with it.

**Consequences.** Slightly more ceremony at each lookup. In exchange, a zero in
any report is now either a measurement or a labelled failure, and the specific
mistake of publishing "zero flagged dark-vessel gaps" as a finding about the
Arabian Sea cannot recur silently. **Every prior conclusion drawn from a zero
should be re-checked against this bar** — the two still outstanding are the
`0 of 98 OFAC-matched vessels with an encounter edge` figure and the WPI port
coverage.

---

## ADR-022 — One canonical vessel key, published by the side that owns it *(Accepted)*
**2026-08-01. Same failure family as ADR-015 and ADR-021.**

**Context.** Two modules built vessel node ids with their own f-strings and had
never met. Measured on the synthetic corpus before the fix:

| Keyspace | Minted by | Nodes | Out-edges | Edges/node |
|---|---|---|---|---|
| `vessel:gfw:<vessel_id>` | `from_landed.vessel_node_id` | **114** | **616** | **5.4** |
| `vessel:mmsi:<mmsi>` | `identity.resolve_mmsi` | **8** | **8** | **1.0** |

**8 of 8** of the second kind had a fully-populated twin under the first. Every
alert the anomaly library raised landed on the second kind.

**The symptom was not the one recorded in STATE.md.** That said alerts "land on
nodes the landed graph never sees". They resolved — **4 of 4, 100%** — so a
presence check passed. They resolved to a node whose entire content was
`{"mmsi": 999000012, "provisional": true}` and one self-referential edge, while
the hull with flag, owner, sanctions, port calls and encounters sat one keyspace
away. **A shadow stub, not a severed join.** An analyst clicking that alert
reaches nothing, which is a demo blocker regardless of any recall number.

**The root cause was narrower than either description.** `resolve_mmsi` works by
walking `identified-as` edges into an `id:mmsi:<mmsi>` node — the right design.
It fell through to minting a provisional hull because
`from_landed.add_identities` emitted **`id:name:*` nodes only**: 115 name nodes,
**zero** `id:mmsi:*` or `id:imo:*`. The lookup had nothing to find. The two sides
did not merely disagree on a format; **one side never published the key the
other side reads.**

**Decision — three parts.**

**(a) One constructor per key, in `schemas/keys.py`**, imported by both the
populator and the resolver. Hull ids come from `vessel_node_id`, identity ids
from `identity_node_id`. `identity_node_id` refuses an unregistered kind, so
adding one is a deliberate edit in the shared file rather than a new f-string in
whichever module needed it.

**(b) The populator publishes `id:mmsi:*` and `id:imo:*` nodes** with time-scoped
`identified-as` edges. This is what makes the fix structural rather than a
translation shim: after it, the resolver finds the hull on its own and no second
node is created. **A shim would have been the wrong fix** — it would have to be
consulted by every present and future consumer, and the first one that forgot
would silently see half a graph, which is precisely the failure being repaired.

**(c) Identifier values are normalised through one function.**
`gfw_vessel_identity.mmsi` lands as a string, `ais_position.mmsi` as an int, and
a Parquet round-trip can produce a float. `str(999000012)` and
`str(999000012.0)` are different node ids and the join would miss for a reason
no row count reveals — the ADR-015 shape again.

**Measured after the fix:** **102 of 103** distinct MMSIs resolve to a populated
hull (median 4 out-edges), up from **0 of 103**. The one that does not is a
vessel's *second* MMSI probed before its swap — the identity model working, and
a "fix" that forced 103/103 would have broken B1's phoenix and B4's zombie.

**Two defects in the same area, fixed with it.**

**`is_synthetic` was omitted on every vessel node.** `add_vessels` never passed
the argument and the column defaults to 0, so **all 114 scenario hulls landed
flagged as real** while the identity and gap nodes beside them were flagged
correctly. ADR-019 makes that flag the only thing separating the two
populations. **Every real-vs-synthetic vessel count taken before 2026-08-01 is
void**, and wrong in the direction that inflates the real side. `GraphStore.node`
also did not return the column, so the one accessor most callers use could not
see it.

**The double prefix `vessel:gfw:vessel:spine`.** The scenario generator writes
its own entity id into the `vessel_id` column GFW fills with a bare id, so the
namespace was applied twice on synthetic rows and once on real ones — the two
corpora did not share a node-id *shape*. `native_vessel_id` strips it, and is
**idempotent**, which the first version was not: it turned an already-canonical
id into `vessel:gfw:gfw:spine`. Caught by a round-trip assertion in the test,
not by inspection.

**Migration is zero-recompute on landed data.** Nothing under `data/conformed/`
is read. The graph's own node ids are rewritten in place, because the graph
accumulates edge history that cannot be regenerated (CLAUDE.md §6) — rebuilding
it is the destructive option here, not the safe one. Renaming a key changes no
fact. A graph with no double-prefixed node pays one indexed scan and no writes.
Where both spellings already exist the migration **declines to merge** and says
so, rather than picking a winner between two histories on a guess.

**Consequences — and the honest one first.**

- **Recall did not move: 3 of 22 before, 3 of 22 after, precision 100% both
  times, 0 false positives across 16 decoys both times.** This was predicted
  before the fix and is reported rather than dressed up. **The 14% was not
  primarily a join artifact**, so no prior tuning conclusion needs voiding on
  those grounds.
- **What it did fix is the click-through.** An alert now resolves to a hull with
  a median of 4 edges instead of a stub with 1. That was the second thing the
  session was called for and it is a demo blocker on its own.
- The measurement harness's `alias_map` stopped being a bridge over a defect and
  became what it should always have been: a translation between the *author's*
  names in `scenario_truth` and the *system's* node ids. Ground truth must not be
  renamed because the populator renames something.

---

## ADR-023 — One port gazetteer *(Accepted)*
**2026-08-01. Scope-cut demo session. Same family as ADR-015 and ADR-022.**

**Context.** Three port lists existed with different contents:

| List | Entries | Used for |
|---|---|---|
| `tracks.features.AOI_PORTS` | 8 | deriving `port_calls` from a track by proximity |
| `anomaly.library.HIGH_RISK_PORTS` | 2 | risk weight per port |
| `scenario.geography.PORTS` | 10 | placing the scenario fleet |

The feature extractor's list held **no Sikka and no Vadinar** — the two Gujarat
crude terminals most tanker traffic in this AOI calls at, and where the
generator was placing ships. A vessel could run a full laden voyage into Vadinar
and produce an **empty** `port_calls` list. Nothing errored; the call simply did
not exist as far as detection was concerned.

**Decision.** `maritime_isr/ports.py` holds one gazetteer. The scenario's
coordinates are authoritative where the lists overlapped — they were chosen
deliberately, including Gwadar's *approach anchorage* rather than its berth,
because the berth sits north of AOI v1's 25N edge. Five ports observed in the
real corpus were added using **GFW's own anchorage coordinates**, read verbatim
from the landed raw payloads: Pipavav, Alang, Hazira, Magdalla, Ghogha. Alang is
the world's largest shipbreaking yard and appears in the corpus as
`ind-ind-76`, which is why a readable name matters (ADR-020).

`HIGH_RISK_PORTS` **stays where it is**: a risk weight is a judgement about a
place, not a fact about it, and the two have different owners. It now validates
its keys against the gazetteer at import, because the previous arrangement made
a misspelled or absent port name produce silence rather than an error.

`SCENARIO_PORTS` is an explicit subset rather than "all of PORTS", so adding a
port for the extractor's benefit cannot silently move the fleet and break
determinism.

**Also fixed:** port matching took the **first** dictionary hit inside the
radius, so at Mumbai and JNPT — 11 km apart, both inside 15 km — the answer
depended on iteration order. `ports.port_at` returns the nearest.

**Consequences, measured.** Tracks producing at least one port call went from a
list that could not name Sikka or Vadinar to **81 of 104 tracks**, with Vadinar
(64) and Sikka (33) the two most-called ports — both previously invisible.
**8 calls now land at Kandla, a high-risk port**, giving
`port_risk_propagation` real input for the first time.

It still fires zero alerts, and that is a **threshold**, not a gazetteer
problem: Kandla's weight is 0.4 and the rule's threshold is 0.5, so a Kandla
call alone can never clear it. Karachi (0.7) would, and no track calls there.
**Not tuned — reported.** Scenario generation is byte-identical (determinism
test green).

---

## ADR-024 — The demo shows a ranked findings table, three sanctions registries, and the whole corpus *(Accepted)*
**2026-08-10. Demo data-coverage session.**

**Context.** The question that started this was "what data do we have in the
demo, is it only synthetic, and why aren't we showing more?" Measured in the
sandbox against a freshly generated corpus, the demo served: 210 vessels, 432
events, 19 sanctions matches, **1 alert**, 0 real rows of anything. On the
laptop it also serves the real corpus — 27,172 GFW events, 9,184 identities,
26,185 sanctions rows, 636 Sentinel-1 footprints — but four things were landed
and reaching no screen at all.

**Four decisions, all in the same direction: stop discarding data we already
have, and say what is missing rather than rendering absence as emptiness.**

### 1. A findings table, ranked by named signals and not by a score

`graph_report.py` had already settled this: on the real corpus the encounter
graph is star-shaped (14 encounters across 9,184 vessels; **0 of 126**
sanctions-matched hulls with an encounter neighbour), so a network view has
nothing to draw and *"what to build instead: a ranked table"* was the recorded
conclusion. It had not been built. `/api/findings` and the Findings screen are
it.

Two populations feed it and they are **not the same kind of claim**: GFW's
intentional-disabling gap assessments (theirs, carried through with
attribution), and sanctions identity matches (the registry decided who is
designated, GFW observed the vessel, *ours* is the match between them — ADR-018).

**There is deliberately no blended risk number.** Rank is a sum of named
signals, each returned with the row in plain English, and a test asserts the
priority equals the sum of the reasons shown. A number the listed reasons do
not add up to is not an explanation. Candidate-grade matches never rank a row:
they are leads for the vessels table, and promoting them here is the
alert-fatigue failure ADR-004 exists to prevent.

### 2. UN and EU sanctions are matched, on terms that fit what they actually are

`registries.py` has landed UN consolidated (1,011 rows) and EU consolidated
(6,017) since the first live run, and **nothing read either of them.** They are
matched now, but not as "another OFAC":

- **OFAC SDN has a vessel record type** — `sdn_type='vessel'` rows carry call
  sign, vessel type, tonnage and flag, so all four match tiers are reachable.
- **UN and EU have no vessel schema at all.** UN is individuals and entities;
  EU is `logical_id, name, programme, identifier`. Neither has a call-sign,
  flag or vessel-type column. A designated ship appears as an entity whose free
  text mentions it.

So UN/EU contribute through **IMO extracted from free text** — keyword-anchored
and check-digit validated, the same two independent checks `review_matches.py`
verified 98 of 98 against — and contribute a *name* match only when the
designation carries positive evidence it names a vessel. Matching vessel names
against arbitrary UN entity names would mostly hit trading companies and
people: a name collision dressed as a sanctions hit.

**Indexes are built per registry, not over the union.** Two reasons, the first
load-bearing: a shared name index would let a UN entity make an OFAC name key
"ambiguous" and drop it, silently moving a published number (126 matches, 98 by
IMO) for a reason unrelated to OFAC. And two lists naming one hull is
**corroboration, not ambiguity** — it lands as two rows that visibly agree, and
is ranked as the second-strongest signal available.

### 3. The map shows the whole corpus, and says what it is not showing

The map requested `limit: 4000`, applied **per kind** with `ORDER BY
start_time`. Against 24,153 real loitering events that drew the earliest 4,000
and stopped — roughly the last five weeks of the window absent from the screen,
with nothing saying so. It read as "no events after mid-July" rather than "you
asked for 4,000 of 24,153."

Raising the cap alone was not the fix; 27,000 undifferentiated dots is a smear.
`/api/events/density` aggregates per H3 cell **over every matching row**, and
the map draws graduated markers with area proportional to count. `/api/events`
now also returns `truncated` per kind with the true total, and the map surfaces
every such note on screen.

The Sentinel-1 footprint layer — 636 real scenes, the one unambiguously real
thing on that map — **defaulted to off**. It is on.

### 4. SAR contacts are drawn, and the word "dark" is withheld from them

`scenario_detections` was registered in the API reader and queried by no
endpoint, so the map had no way to draw a radar contact. `/api/detections`
serves them and the map draws them **hollow when no AIS track was associated**.
That is the *shape* of a dark vessel and the layer stops there: asserting
intentional silence requires demonstrated reception at the position (ADR-005,
CLAUDE.md §6). Everything this endpoint returns today is synthetic and the
response says so — an empty real split must not read as "the pipeline ran and
found nothing" when no SAR scene has been processed at all (ADR-017).

### Two real/synthetic divergences found while building this

**`ofac_name` meant two different things.** The real matcher only matches
`sdn_type='vessel'` rows, so its `ofac_name` is a listed *vessel* name; the
scenario generator reaches a hull through its owner, so its `ofac_name` is a
*company*. Comparing our vessel name to a company name always disagrees, so
"sails under a different name than the listing" — the identity-laundering
signal an IMO match exists to catch — fired on **19 of 19** scenario rows.
Fixed with an explicit `listed_entity_type` written by both sides; rows landed
before the column existed default to `vessel`, which is what the real matcher
has always produced.

**The matcher wrote `ship_name`/`flag`/`imo`; the API reads
`vessel_name`/`vessel_flag`/`vessel_imo`.** The scenario generator wrote the
latter, so the sanctions panel looked correct on the scenario corpus and
rendered blank vessel fields on the real one. The matcher now writes both.

**Consequences.** `sanctioned_vessel_matches` gains `registry`,
`listed_entity_type` and the `vessel_*` fields, and its natural key gains
`registry` — without it an OFAC row and a UN row for the same hull and tier
would collide on re-landing and one would silently overwrite the other, losing
exactly the corroboration this change is for. **The matcher must be re-run**;
rows landed earlier carry the pre-ADR-024 schema. `--registries OFAC`
reproduces the previous behaviour exactly.

**What this does not do.** It does not make a real alert possible. Every
detector reads the track engine, the real corpus has no AIS positions, and that
is ADR-005's unfunded feed, not a tuning problem.

---

## ADR-025 — The incident report, and the identity-change events that were never written *(Accepted)*
**2026-08-10. Same session as ADR-024, second pass.**

Two changes. One completes the M6 demo definition; the other fixes a piece of
plumbing that had been making a detector look like a tuning problem.

### 1. The one-click incident report

`CLAUDE.md` §0 defines done as: a non-engineer opens a map, finds last week's
dark vessels, clicks one, reads the plain-English reason, **and exports a
one-click incident report** — in under five minutes. Every clause but the last
was built. Searching the tree for it returned nothing: no endpoint, no button,
no renderer.

`GET /api/vessels/{id}/report` returns a **self-contained HTML file** by
default (`?format=json` gives the same payload as data). HTML rather than PDF
because it opens in any browser, prints to PDF from there in one keystroke,
survives being emailed, and needs no renderer on either end. Every style is
inline and no asset is fetched, so the document says the same thing on a
machine with no network — which for a file that exists to be forwarded is the
entire point.

**The report is written for someone who was not in the room**, so three things
are structural rather than a matter of care at authoring time:

- **A scenario vessel is unmistakable.** Banner at the top, banner at the
  bottom, and `SCENARIO-` on the filename itself. A generated dossier mistaken
  for a real one is exactly the failure §4.6 exists to prevent, and by the time
  it happens the label is out of our hands. A long document is also read from
  wherever it was scrolled to, which is why one banner is not enough.
- **Every determination names who made it.** GFW assessed the gaps; OFAC, the
  UN and the EU decided the designations; ours is the identity match between
  them. A test asserts no report can contain "we detected a dark vessel".
- **"What this report does not establish" is a required section.** An omission
  reads as *no concern here* unless it is named as *we cannot see this*. It
  always states that we detected no dark vessel, and — when no gap is flagged —
  that this is not evidence the vessel never went dark, because outside
  demonstrated coverage a silence cannot be attributed at all.

The export is available for **any** vessel, not only flagged ones: an analyst
establishing whether a hull is worth flagging needs to hand over what is known
about it, and refusing unless it is already flagged makes the export useless in
precisely that case.

### 2. `identity_changed` events — a rule that was unfed, not mistuned

`detect_identity_then_anomaly` has measured zero across every session, and was
recorded in STATE.md as blocked by "the `events` table is empty (0 rows)".

**The cause was a missing writer.** Those events had exactly one producer,
`identity.fold_registry_snapshot`, and the scenario pipeline never calls it —
it populates Phase 4 through `from_landed.populate()` instead. Nothing on that
path emitted an identity change, so the rule read an empty table and reported
silence. Five of six detectors were being described as silent; this one was
silent for a reason that had nothing to do with its logic.

`from_landed.add_identities` was **already computing genuine supersession** for
the rename analysis. It now emits the event alongside, for name, flag and MMSI.

Three decisions inside that, each of which could have been made wrongly:

- **Supersession, not closure.** A value counts as replaced only when a *later*
  interval carries a *different* value. GFW's `transmissionDateTo` is the end
  of our query window, so nearly every interval is closed — this is the same
  100%-closed trap that once labelled the entire fleet as having changed
  identity. Emitting on closure would fire the laundering rule on essentially
  every hull in the corpus: silent to worthless in one step.
- **IMO is excluded.** It is a permanent hull number, so a change in it is a
  far stronger and different claim than a change of label. Folding it in would
  let a transcription error read as a laundering step.
- **The timestamp is when the new identity starts**, not when the old one ends.
  The rule correlates a change against a later anomaly inside a window, and the
  two instants differ whenever intervals do not abut. Using the predecessor's
  end would date the change to the last time we heard the *old* identity.

`GraphStore.emit_once()` deduplicates on `(event_type, subject, ts, payload)`.
The events table is an append-only log with an autoincrement key, so a
populator re-deriving the same facts would append a duplicate on every run and
any rule counting changes would watch the corpus grow — the same defect class
as the stale alerts already recorded in STATE.md.

### Measured, including the part that did not move

```
identity_changed events    12, across 5 hulls  (name 2, flag 7, mmsi 3)
detectors firing            2 of 6   (was 1 of 6)
identity_then_anomaly       1 alert  (was 0)

true anomalies  22   DETECTED 1   MISSED 21     <- unchanged
decoys          16   FALSE POSITIVE 0           <- unchanged
precision 100%   recall 5%                      <- unchanged
```

**The rule fires and the scorecard does not move, and that is the finding.**
The alert lands on `clone_ghost`, which is B5 — already detected via
`ais_spoofing`. B5 now carries two alerts instead of one.

B1, B2, B3, B6 and D2 all declare `identity_then_anomaly` as their expected
type and all still miss, because **the rule is composite**: it needs an
identity change *and* a `dark_vessel` / `dark_rendezvous` / `ais_spoofing`
alert on the same hull within 14 days. Those hulls now have the identity change
and have no companion alert, because `dark_vessel` and `dark_rendezvous` are
structurally silent — every dark verdict is `suppressed_coverage` with
`hearable_conf = 0.0`, which is ADR-005's unfunded satellite feed arriving in
the detector.

So the plumbing fix was **necessary and not sufficient**, and the remaining
blocker on this family is the same funding decision as everywhere else. Zero
false positives were added, which is the other number that had to hold.

---

## ADR-026 — Imaging opportunity is a claim about geometry, never about a ship *(Accepted)*
**2026-08-13. First analytical assertion the system makes on its own behalf.**

**Context.** Since ADR-017 every dark-vessel and AIS-gap determination in this
system has been Global Fishing Watch's. We landed 636 Sentinel-1 catalogue
records on 2026-07-29 and nothing has ever read them; they carry a footprint
polygon and an acquisition time, and no pixels (ADR-013's 1 GB cap).

Reading `ingest/gfw_events.py` closely turned up something this file had not
recorded: **gap rows carry `gap_off_lat/lon` and `gap_on_lat/lon`** — the
positions where AIS stopped and resumed — alongside both timestamps. A known
start point, a known end point and an elapsed time is a solvable problem. A
vessel that went dark at A and reappeared at B was, at the moment of any
satellite pass in between, inside the intersection of a disc growing from A and
a disc shrinking toward B. Compare that region against the scene footprint and
the question *"was anyone watching?"* is answerable from data already on disk.

**Decision — four parts.**

**(a) The claim is about where a satellite was pointed, and nothing else.** A
`confirmed` row asserts that an image exists whose footprint necessarily
contained the vessel. It asserts nothing about what the image shows, because
nobody has looked — the pixels are not downloaded. The sentence "no image has
been examined and no vessel has been detected" is written into the module, the
CLI output and the incident report, and is covered by tests in all three. This
does not re-assess the gap: whether the silence was intentional remains GFW's
determination per ADR-017.

**(b) Three tiers, and the boundary is geometric.** `confirmed` = the reachable
region is entirely inside the footprint; `partial` = the footprint covers part
of it, reported as an **area fraction and never as a probability** (calling 40%
of an area a 40% chance would assume the vessel is uniformly distributed within
it, which nothing here establishes); `none` = evaluated, no pass. A fourth tier
`unknown` covers rows that could not be assessed at all — a gap missing both
positions, an unparseable footprint — because absence of a pass and inability
to look are different facts (ADR-021).

**(c) A gap nobody imaged still lands a row.** Tier `none`, empty scene id.
Without it, a gap that was evaluated and found unwatched is indistinguishable
from one the job never reached. Same reasoning as ADR-021, applied to output
rather than to a check.

**(d) It does not rank a vessel.** No entry is added to the findings `_RANK`
table. A satellite having flown overhead says nothing about whether a hull is
suspicious — it says the question is *resolvable*, which is a different axis.
Folding actionability into a suspicion score would build exactly the blended
number ADR-024 declined to build. The opportunity attaches as evidence on the
gap and travels into the report; it never moves a row up the list.

**The speed bound is an assumption and is labelled as one.** `v_max` defaults
to **20 knots** and is written onto every row. Generous is the safe direction:
a higher assumed speed enlarges the reachable region, which makes containment
*harder* to claim, so the error runs toward under-claiming (ADR-004).

**A gap that exceeds the speed bound is a spoofing tell, not a bad row.** Where
the endpoints are further apart than `v_max` explains, the assumed speed is
raised just above what the gap requires — geometry stays valid — and
`implied_speed_exceeds_vmax` is set so the fact surfaces. Discarding it would
be the anti-pattern CLAUDE.md §6 names outright.

**Alternatives rejected.** *Score it as a dark-vessel detection* — rejected
outright; it is the overclaim this whole file exists to prevent. *Use H3 cells
to pre-filter scene against region* — rejected: with 636 scenes exact geometry
costs nothing, and a resolution mismatch in the prefilter is precisely the bug
class ADR-015 was written about. Output rows are still H3-stamped at every
resolution so downstream joins work. *Skip gaps missing an endpoint* —
rejected: a single-sided cone is a weaker bound but an honest one, and it is
labelled `forward_cone` / `backward_cone` on the row.

**Consequences.**

- **The tier that fires depends on gap length, and that is physics.** A
  Sentinel-1 IW footprint is ~250 km square (~62,500 km²); at 20 kn the
  reachable region passes that size roughly six hours into a gap. So short gaps
  can be contained outright, long gaps only near their ends, and a mid-gap pass
  on a 24-hour silence lands `partial` at around 10% of area. Pinned by a
  characterisation test so a future threshold change cannot quietly turn thin
  coverage into confident claims.
- **It produces a shopping list.** Every confirmed opportunity names a scene id
  whose download would resolve a concrete question about a specific hull. That
  is a far better argument for un-parking Phase 1 than "SAR would be nice," and
  it is printed by the CLI.
- **It is not fusion-core work.** Two named sources joined into their own table;
  `fusion/` is untouched (CLAUDE.md §4.5), the same posture
  `ingest/sanctions_match.py` states for itself. It lives at package root
  because it belongs to neither ingest (it fetches nothing) nor the core.
- **Unverified on the real corpus.** Every number in this ADR is geometry or
  sandbox fixture. Nothing here has run against the 636 landed scenes or the 5
  flagged gaps, which live on Eshan's laptop.

---

## ADR-027 — What the operator sees first, and what it is allowed to imply *(Accepted)*
**2026-08-13. Two demo defects and one operator request, taken together.**

**Context.** Three reports arrived from the laptop demo in one session: the
timeline player was invisible and came back only intermittently, the Graph view
opened empty, and the Graph should default to showing the whole network. All
three are about **what is on screen before the operator does anything**, and
each was resolved by measurement rather than preference.

**Decision — five parts.**

**(a) A view-critical field gets its own cheap endpoint, requested first.** The
map's time window came from `/stats`, which scans every event table, groups the
sanctions matches, counts scenes, measures length coverage and walks the graph.
It was the **eighth** of eight requests fired on mount, past the browser's ~6
connections per origin, so it queued behind `/tracks` — **measured at 3.06 s**,
40× the next slowest call. `/api/corpus-window` returns the same two aggregates
alone, is requested first, and is cached for the session because a corpus window
cannot change while the process is up.

*The ordering was the fix; the cheaper endpoint was the smallest part of it.*
Worth stating because the first diagnosis blamed the endpoint cost and the
measurement contradicted it — `/stats` was 0.194 s, not the seconds assumed.

**(b) A control that is loading is disabled, never absent.** The scrubber
rendered only once its window existed, so it vanished on every navigation and
reappeared seconds later. A control that comes and goes reads as a broken page;
a disabled one reads as a loading page. It now stays mounted and fades.

**(c) The Graph opens on the whole network, capped, with the cap stated.** The
real corpus graph is an estimated ~19,000 nodes / ~22,000 edges, which no
in-browser force layout will draw, so the view shows the most-connected core up
to **1,500 nodes** and reports `total_nodes`, `total_edges` and `truncated`.

Degree-ranked rather than an arbitrary slice: a random cut is both incomplete
**and** unrepresentative — scattered fragments implying the data is sparse when
it is not. 1,500 rather than 900 because most of the cost is fixed overhead
(measured: 900 → 5.8 s, 1,409 → 7.0 s), so 66% more graph costs about a second.

**A truncated web must never be described as the whole graph.** The panel names
both totals whenever it truncates.

**(d) The centred node is a camera position, not a verdict.** Criteria, in
order: most-connected **sanctioned** vessel → most-connected vessel →
most-connected node. Sanctions designation leads because it is the only
finding-grade signal available at node level — `_RANK` already treats it as
evidence rather than something this view invented; degree breaks the tie because
the point of a web is structure.

The payload carries a `focus_basis` sentence and the UI states it: *"that is
where the camera starts, not a finding: it is the best-connected node, not the
most suspicious one."* A test asserts the string never contains "suspicious",
"risky", "dangerous" or "dark". **Degree is connectedness, not risk** — a vessel
with no edges is less connected, not less suspicious, and on this corpus that
distinction is large: GFW ownership covers ~1.3% of hulls.

**(e) An ended relationship is drawn, and drawn as ended.** The first
implementation filtered closed edges out, which **dropped 191 of 344** on the
fixture graph — because `neighbourhood()` and
`counts_by_synthetic()['edges_current']` both resolve latest-wins *without* that
filter. The web would have silently disagreed with every other edge count in the
product. Ended edges are kept, carry `is_current`, and render **dashed and
dimmed**: that satisfies invariant 3 without hiding evidence, because dashed
reads as "was true", which is what it is.

**The layout engine changed, and that is the load-bearing part.** Cytoscape's
built-in `cose` compares every pair of nodes on every iteration. Measured
end-to-end in Chromium:

| nodes | `cose` | `fcose` |
|---|---|---|
| 219 | 2.5 s | 1.9 s |
| 900 | — | 5.8 s |
| 1,409 | **115 s** | 6.5–7.0 s |

115 s is a hung tab, not a slow render. `cytoscape-fcose` seeds from a spectral
draft and approximates repulsion with a quadtree — O(n log n) per iteration
instead of O(n²) — for the same look and the same deterministic settling.
`cose` is retained below 250 nodes because the neighbourhood view is tuned to
it. **This adds a frontend dependency: `npm install` is required after pulling,
or the build fails.**

**Alternatives rejected.** *Raise the `cose` iteration budget instead of
changing engine* — rejected: the budget was already cut to 250 and it still took
115 s; the cost is the pairwise comparison, not the iteration count. *Use
fcose's `quality: "draft"` for speed* — rejected, and it does not work: draft
skips the spectral seeding that `randomize: false` expects and fcose throws
`Cannot read properties of undefined (reading 'nodeIndexes')`. Determinism is
worth more than the seconds — a web that reshuffles every visit cannot be
learned, and re-finding a cluster seen yesterday is most of the value. *Hide
ended edges to reduce clutter* — rejected, see (e). *Let the imaging or
connectivity signals rank a vessel on the Findings screen* — rejected: both
describe how **resolvable** or how **connected** a hull is, not how suspicious,
and blending those axes builds the number ADR-024 declined to build.

**Consequences.**

- On the real corpus the Graph will show a **stated subset**, not the whole
  graph. "Every relationship as one web" is literally true only on corpora below
  the cap.
- The frontend now has a layout dependency that a `git pull` does not install.
  A tab that locks for a minute means fcose did not load.
- `flushSync` is required around the pre-layout status message: React 18 batches,
  so neither `setTimeout(0)` nor a double `requestAnimationFrame` runs after the
  commit, and the message was set and never painted while the thread blocked for
  ~5 s. Verified in a browser, not reasoned about.

---

## ADR-028 — Coastal radar is a second sensor, and it found four places the core assumed AIS *(Accepted)*
**2026-08-16. The first source added since the connector claim was written down.**

**Context.** CLAUDE.md §4.5 makes an architectural promise: *"Every source is a
connector, never a core change. New data source = new module in `ingest/` that
maps into the canonical schema. The fusion core must never learn a
source-specific hack."* That promise had never been tested. Everything positional
in this system arrived as AIS, and the fusion core, the track engine and the
anomaly library were all written against it.

Separately, the demo could not tell its own story. Of six detectors, two fired,
producing one alert on the scenario corpus and zero on real data, because every
detector reads the track engine and the real corpus contains no AIS position
tracks. The headline claim — *a contact on radar with nothing broadcasting there
is a dark vessel* — needed SAR imagery we cannot obtain for this AOI (ADR-017)
and had never once fired.

The Indian Coast Guard's primary sensor is coastal radar: roughly a hundred
stations producing tracks with position, course and speed and **no identity**.
No such feed is available to us. But a radar track is structurally an AIS
position report minus the identity fields, which makes it the ideal test of the
connector claim and, simultaneously, the only route to a dark-vessel finding
that needs no imagery.

**Decision — five parts.**

**(a) Radar is a connector, and the simulated picture lands through it.**
`ingest/radar.py` maps a station feed into a canonical `radar_track_report`
table, validated by a pydantic `RadarTrackReport`, stamped with the provenance
envelope and all five H3 resolutions by the shared helper. `scenario/radar.py`
produces *feed records* and hands them to that connector, flagged synthetic, so
the connector is exercised by every scenario run rather than bypassed. A
parallel path for simulated radar would have proved only that the parallel path
works (ADR-019's reasoning, applied to a second sensor).

**(b) The picture is derived from the same vessel truth as the AIS.** The
simulator invents no vessel and no position. It walks `world.tracks` — the
integrated motion the scenarios already authored — and asks every five minutes
which coastal station could see that hull and what it would report. Three
populations fall out with no extra machinery: vessels on both sensors, vessels
on AIS only (offshore, in shadow, below the horizon, too small), and vessels on
**radar only** — the finding. Detection is decided by signal-to-noise against
the radar horizon, so a 12 m dhow disappears at 9 km and a laden VLCC is held to
48; nobody set those numbers. Sea clutter, terrain shadow sectors, a maintenance
outage and four real fixed installations are generated too, because a picture
with uniform circular coverage would make everything unexplained look dark and
the hardest question in the product — *unexplained, or merely unobserved* —
would never be asked.

**(c) What the core assumed, stated as a descriptor rather than a branch.**
`schemas/sources.py` holds a `TrackSource`: the grouping key, whether that key
is an *identity*, whether the sensor observes *transmission*, its position
accuracy, its reuse-guard interval. Nothing downstream may say
`if source == "radar"`; it may ask `if not source.key_is_identity`, which is a
question about meaning and stays correct when the third sensor arrives.

**(d) Correlation reuses the association engine, epoch by epoch.** The radar
picture is sliced into 15-minute epochs and each is handed to `associate_scene`
unmodified — same Hungarian global assignment, same uncertainty-cone gate, same
score floor. Per-epoch verdicts aggregate onto whole radar tracks, so the answer
is a *time series* and the transition in it is the product: a track that
correlates and then stops is a transponder being switched off with a witness.
Survivors go through the existing `dark_cascade`.

**(e) The claim is enforced by counting, not by association.** "Nothing is
broadcasting there" is checked as an H3 census: in the contact's own res-7 cell
and ring, how many radar contacts were seen against how many distinct AIS
identities were *heard*. More contacts than broadcasters means at least one is
unexplained whichever way the assignment ran; otherwise none is. This is the
join CLAUDE.md §3 says the architecture exists to make cheap.

**The findings about the architecture — the point of the exercise.** Four places
in the core assumed AIS. All four are fixed source-agnostically, and all four
would have failed *silently*:

1. **`detect_encounters` compared `mmsi_a == mmsi_b` to reject self-pairs.**
   On radar both are `None`, `None == None` is `True`, so **every radar-to-radar
   pair was discarded** and the encounter detector could not fire on radar at
   all. It would have read as "radar sees no rendezvous" — a conclusion, not a
   bug report. Now keyed on `track_key`, which is always populated; AIS
   behaviour is unchanged because for AIS the key *is* the MMSI.

2. **The anomaly library resolved every track's subject with
   `resolve_mmsi(store, tr.mmsi)`.** With `None` that mints `vessel:mmsi:None` —
   a node that resolves, passes a presence check, and is a different fiction for
   every radar track. Now `graph.identity.track_subject_id` returns the hull for
   an identity-bearing track and a `contact:radar:<station>:<n>` node otherwise.
   A contact is deliberately **not** a vessel node: it is almost certainly one,
   but what is *known* is that something of roughly this size was at these
   positions.

3. **`classify_gaps` would have labelled radar dropouts `INTENTIONAL_SILENCE`.**
   Every label in that taxonomy is a statement about a *broadcast*; a radar gap
   is the sensor losing contact. It now refuses a non-transmitting source
   outright, because the failure would have been quiet — the rows would look
   perfectly ordinary.

4. **The reuse guard was seven days.** Right for an MMSI welded to a hull;
   catastrophic for a track number a station recycles in minutes. Measured:
   single "tracks" of **11,829 plots spanning six weeks**, built by merging
   every target issued the same recycled number, with two vessels 20 km apart
   fifteen minutes apart implying 52 knots and slipping under the 60-knot
   hypothesis gate. Now per-source.

**And one defect that was not about radar at all.** The association score was
`−½(d/σ)²` with `σ` taken from the **gate radius**. A track whose last AIS
report is twelve hours old has a cone ~900 km wide, so σ came out at ~360 km and
a contact **186 km away** scored −0.13 — nowhere near the −8 floor. The score was
permissive in exact proportion to how little was known. Measured on the radar
picture before the fix: matches at 36, 61, 77, 131 and 187 km, every one of them
a real dark vessel explained away by a transmitting ship on the other side of
the Gulf of Kachchh. A 2-D Gaussian log-density is `−ln(2πσ²) − d²/2σ²`; the
first term is the volume normalisation, dropping it makes a large search free,
and restoring it (rescaled against `ASSOC_SIGMA_REF_M`, so the well-constrained
case is numerically unchanged) makes the floor mean something again. **This was
latent on the SAR path and invisible there**, because the entire synthetic SAR
corpus is six contacts placed beside fresh AIS tracks.

**Three constants were tuned to SAR and are now parameters**, all defaulting to
the SAR value so no existing call site changes: the static-object clustering
radius and resolution (200 m / res 8 is narrower than a coastal radar's own
error at range, so every fixed installation would have been promoted to a dark
vessel); the recurrence threshold (3 scenes is right for a six-day revisit and
produced **58** "installations" on radar, of which 54 were shipping lanes); and
detection persistence (`min_looks`, a no-op for a single-look sensor).

**Alternatives rejected.** *A `radar/` package parallel to `fusion/`* — rejected:
it would have made the connector claim unfalsifiable, which is the opposite of
the point. *Branching on the source name inside the core* — rejected; a `==` on a
name is a source-specific hack wearing a parameter's clothes. *Moving the
station network offshore so the existing deep-basin dark scenarios would be
seen* — rejected: coastal radar is coastal, the offshore transfer basins are 350
to 450 km out, and generating that away would have measured an easier problem
than the one claimed. Group R exists instead, inside the belt where a shore
station can actually see. *Treating a missing AIS gap row as evidence of
coverage* — rejected: on this corpus the gap classifier emits 26,778 gaps and
**every one is `COVERAGE_GAP`**, because `INTENTIONAL_SILENCE` needs two
completed satellite passes and there is no satellite-AIS schedule at all (a
defect predating this work, recorded in STATE.md). Treating `None` as
suppressing would have made the check a pure suppressor, able to hide a real
shutdown and unable ever to confirm one.

**Consequences.**

> **Two figures below were superseded within the day by ADR-029, and one of them
> was simply wrong.** Recall was re-measured at **43% (3 of 7)** after the corpus
> was regenerated, and the "one radar track in nine" correlation figure is
> **withdrawn** — it came from a probe that gated contacts at the wrong timestamp
> and counted genuinely dark vessels as correlation failures. The corrected
> figure is 98.2% of resolvable tracks. The original text is left intact below
> because a decision record that quietly edits its own numbers is worth less than
> one that shows them being corrected.

- **Measured through the landed pipeline: precision 100%, recall 50%** — four
  of eight findable dark episodes, zero false positives. Above the ADR-004
  gate. Run in memory against the full vessel registry it is five of eight; the
  landed figure is lower because `gfw_vessel_identity` carries a usable
  `length_m` on only 11 of 224 rows after null-masking, so the association's
  length term is mostly unavailable. **The landed number is the one quoted**,
  because it is the one the pipeline produces. Every number is synthetic and
  says nothing about a real CSN feed, which this system has never seen.
- **Radar↔AIS correlation resolves about one radar track in nine** (108 of 938
  tracks whose vessel was demonstrably on AIS). This is the weakest number in
  the build and the honest cause is AIS *receipt* sparsity rather than the
  algorithm: `primitives/ais.py` thins an anchored vessel's 3-minute transmit
  interval to roughly one landed report every fifty minutes, and the prediction
  cone between receipts opens to kilometres. Whether that thinning model is
  right for a shore receiver is an OPEN QUESTION, not something to retune
  quietly.
- Four of the eight findable episodes are missed, and each miss has a name:
  one to `suppressed_coverage` (the empirical AIS coverage model has no evidence
  of reception off Diu), three to `suppressed_not_isolated` (the Gulf of
  Kachchh has as many broadcasters as contacts). Two of the four are second and
  third episodes of vessels detected on another station's track, so three of
  the five dark *vessels* in group R are found; `coast_dark_party` is the one
  missed outright.
- **A scenario whose finding is a dark contact reads as MISSED in the
  scenario-level table**, because that table attributes alerts by vessel and a
  dark contact has no identity to attribute — which is the whole point of it.
  R1, R2 and R3 are scored in the dark-contact results instead, by position and
  time. Both tables now say so rather than leaving a reader to reconcile them.
- **DX7 fires on radar and that is correct.** The naval decoy transits into
  Mumbai's cover with AIS off; radar sees an unexplained contact and cannot know
  she is a warship. The radar answer key inherits the product policy that she
  must never be flagged, marks her `unavoidable_false_positive`, and the
  measurement reports the class by name — which is the argument for a
  known-units layer, with a number attached.
- **The build earns one of its two sentences, not both.** *"That contact is on
  radar and nothing is broadcasting there"* is delivered: four contacts, all
  correct. *"Here is where its transponder went quiet"* is **computed and
  stored and does not reach the queue.** 77 radar tracks come back
  `correlated_then_dark`, 48 of them carry a `went_dark_at` position and time —
  and **all 77 are suppressed by the census**, because a track that correlated
  for most of its life is by construction in water where AIS is heard, so
  something is broadcasting in its neighbourhood. The two checks are in tension
  and the census currently wins every time. The obvious next move is to stop
  counting a track's *own* prior identity as a broadcaster once it has stopped
  transmitting; that is a change with its own failure modes and it is not being
  made in passing at the end of a session.
- The graph gains a `contact` node type and a `correlates-with` edge. A contact
  may dock, meet and be detected; it may not own, be flagged or be sanctioned,
  because all three are assertions about an identity it does not have.
- Two boundaries are now stated with numbers rather than shrugged at: a 12 m
  hull is detected to about 9 km and works at 25 (R4), and 100 km offshore of
  the 215 km Mumbai–Ratnagiri stretch nothing has cover at all (R5). The second
  is an argument for a station, and the system can say where.

---

## ADR-029 — Finishing Build 1: the shutdown story, a serving layer, and two rules that were asking the wrong question *(Accepted)*
**2026-08-16. Closes the four items left open when ADR-028 merged.**

**Context.** ADR-028 landed coastal radar as a second sensor and was merged with
four things explicitly unfinished, listed at the merge rather than buried:

1. *"Here is where its transponder went quiet"* was computed for 77 tracks, 48
   of them with a position, and **reached the alert queue for none**.
2. Radar↔AIS correlation was reported as resolving **about one track in nine**,
   logged as OQ-radar-1 and deliberately not retuned.
3. **No API, no UI.** A watchkeeper could not see a single radar output; the
   whole path was reachable only from a terminal.
4. **Nothing had run on Eshan's machine.** Sandbox-green only.

This ADR records what closing the first three took, and what the fourth
actually is.

### (a) The one-in-nine figure was wrong, and is withdrawn

**Two faults in the measurement, one of them also a fault in the code.**

The correlation slices the radar picture into 15-minute epochs and hands each to
`associate_scene`. That function gated **every contact in the epoch at the
epoch's own timestamp** — so a contact observed at minute 14 was compared
against every AIS track's position at minute 0. A merchant at 13 knots covers
5.5 km in that time, and the gate was being asked to absorb it as error. The
repair is one function, `_t_of(c)`: each contact gates at the instant it was
actually observed. Nothing about the assignment, the score or the floor changed.

The second fault was in the probe, not the product: it counted every radar track
that failed to resolve, **including the tracks of vessels that really were
dark**. Those are the finding. Counting them as correlation failures measured
the system's success as its failure.

Corrected, on the same denominator the old figure used — radar tracks whose
vessel was demonstrably on AIS during that track:

| | |
|---|---|
| resolvable tracks | 996 |
| **resolved to the right hull** | **978 (98.2%)** |
| resolved to the wrong hull | 11 (1.1%) — 10 of them reported `ambiguous`, i.e. not claimed |
| left unresolved | 7 (0.7%) |
| tracks of a genuinely silent vessel | 241 — **224 correctly left dark, 14 followed across a short gap onto her own MMSI, 0 confidently explained away by another hull** |

That last row is the one that matters for the product: a false correlation onto
somebody else's hull is how a dark vessel disappears, and it happened zero times.

**OQ-radar-1 is answered, and answered differently than expected.** The open
question asked whether the AIS thinning model for anchored vessels was making
correlation look bad. It was not the cause. A second fix — teaching
`BuiltTrack.state_at` to **interpolate between two known fixes (a Brownian
bridge) instead of extrapolating forward from the earlier one** — cut the
95th-percentile position error at the midpoint of a gap from **4,120 m to
1,450 m**, which is exactly the sparsity problem the open question named, and it
is a filtering fix rather than a generator retune. The thinning model may still
be unrealistic and that remains worth checking against a real capture; it is no
longer load-bearing for any number this project quotes.

### (b) The shutdown story reaches the queue

Three things had to be true at once, and the first two were already built:

* the track has to be **held across the transition** — radar must see her both
  while she is transmitting and after she stops;
* the AIS side has to agree she went quiet. `_receipts_between` counts her
  actual receipts during the dark run, which needs no threshold: the vessel that
  survives this test was heard **0 times in five hours**, and the one that does
  not was heard 66 times. Two cleverer tests were tried first — her median
  reporting interval, then her 90th percentile — and both let an anchored ship's
  routine hole through as a shutdown;
* and the cascade has to stop suppressing her. It was suppressing all 77,
  correctly by its own logic: the neighbourhood census asks whether unexplained
  contacts outnumber unspent broadcasters, and a vessel who has been
  broadcasting all week is by construction in water where AIS is heard. The fix
  is `identity_known`: when we watched a *named* hull stop transmitting, that
  evidence stands on its own and does not depend on her neighbours, so the
  census is skipped for that row alone.

Landed result on seed 7: one `correlated_then_dark` track, carrying a shutdown
position, reaching the queue and rendering in the UI as *"Transponder went quiet
here. Last explained by MMSI 999000211 at 2026-06-25 12:36Z, position 18.8218,
72.5868."* One is not many. It is the difference between a sentence the system
can say and one it cannot.

### (c) A serving layer, and the synthetic flag stays on the screen

Three endpoints — `/api/radar/stations`, `/api/radar/contacts`,
`/api/radar/tracks` — plus a Radar view and three map layers.

They read **landed** tables. A full correlation is tens of minutes over ~5,000
epochs, so no request can compute one; `maritime-isr radar correlate --write`
lands `radar_correlation` and `radar_dark_contact` through the same landing
layer as every connector, provenance envelope and H3 cells included.

Two things the view is required to do:

* **Show the suppressions.** `?status=all` returns the rejected verdicts with
  their reasons. "Why is this NOT dark" has to be answerable from the product; a
  cascade whose rejections are invisible is a black box an operator has to take
  on faith.
* **Keep the synthetic flag visible in the interface, not only in the
  database.** There is no radar feed and a coverage map is the most persuasive
  picture this system can draw. The Radar tab carries a non-dismissible banner,
  every row carries a mark, and the three map layers say "(synthetic)" in their
  own names. Note this deliberately **contradicts `components/bits.jsx`**, where
  `SyntheticBadge` is a no-op on the reasoning that provenance is communicated
  outside the product. That reasoning may be right for a corpus that is mostly
  real; it is not right for a sensor that is entirely invented, and the build
  brief for this work makes the visible flag a requirement. The inconsistency is
  recorded here rather than resolved silently.

The coverage ring is drawn as **two** rings, not one: the radar horizon depends
on the target's height, so a station holds a 250 m tanker roughly twice as far
out as a 15 m skiff. A single circle would either promise skiff coverage the
station does not have or hide tanker coverage it does, and the band between the
two rings — "big ships only" — is exactly what an operator needs to see before
believing a silence.

### (d) Two rules that were asking the wrong question

Radar did not break these. It made them visible, which is the same thing ADR-028
found four times and is becoming the pattern: **a rule tuned against six SAR
contacts is not tested, it is merely quiet.**

**`detect_dark_rendezvous` was asking a global question.** Its `gap_party`
branch scanned every unmatched association *anywhere in the AOI* within twelve
hours, with no distance test at all — effectively "was anything, anywhere in the
Arabian Sea, dark today?" With six SAR contacts in the whole corpus that was
usually false by accident. With 270,000 radar plots it was true almost always:
**667 alerts on seed 7, 76 of them on background traffic with no truth row
behind them.** The rule now asks about the encounter's own parties: a sensor
that tracks its targets stamps the sensing track into the detection id, so
"a party to this meeting was unexplained while it was happening" is a lookup
rather than a proximity guess. The positional test survives unchanged for
detections with no track of their own, which is the SAR case it was written for.

Its **score could not fall below its own threshold** — `0.5 + confidence × 0.4`
against a gate of `0.50`, so `ANOMALY_THRESHOLDS["dark_rendezvous"]` had never
excluded anything in the project's history. It now starts from the evidence
(`0.35 + 0.35 × confidence`, plus 0.25 for a demonstrably silent party), so the
gate does the job it was configured to do.

**The cascade gate dropped `ambiguous` tracks before anything could judge
them.** A track whose support fell between the two thresholds — partly
explained, mostly not — never reached the filters. Measured: two of the seven
findable episodes on seed 7 had **no verdict row anywhere in the store**,
indistinguishable from episodes that were never in the picture. `ambiguous` is a
statement about *identity* ("we cannot name this target"), not about explanation,
and the cascade already owns the doubt it expresses — `require_excess_contacts`
is literally the test for "is this a mis-association rather than a dark vessel".
Admitting them moved nothing in the queue (precision 100%, recall 43% both ways)
and moved ten rows from silence into the record, two of them the missing
episodes. That is the whole benefit and it is claimed as nothing more.

### (e) The fourth item cannot be closed from here, and saying so is the point

**"Nothing has run on your machine" is not a defect I can fix.** Claude has no
access to Eshan's laptop; the only person who can convert *built, sandbox-green*
into *verified on the host* is Eshan, by running three commands and pasting back
what they print. They are listed with their success and failure signatures in
`STATE.md`. Until that happens the honest status of every number in this ADR is
**measured in the sandbox, on synthetic data** — which is also the status of
every number in ADR-028, and will not change by being written more confidently.

### Consequences

- **Precision 100%, recall 43%** (3 of 7 findable episodes) through the landed
  pipeline. Recall fell from the 4-of-8 quoted in ADR-028 because the corpus was
  regenerated and the episode set changed, not because detection got worse; the
  four misses are named individually in `STATE.md` and three of them are dark
  runs below the deliberate two-hour persistence floor.
- The two headline sentences are both earned now. That is one sentence more than
  ADR-028 could claim.
- **10,920 radar-only encounters** exist on this corpus, which is what a
  continuous sensor over eight weeks of coastal fishing traffic looks like. The
  rendezvous rule is now correct about *which* of them carry evidence — 81 of
  them, and **zero** on a named vessel with no truth row behind it, down from 76.
  68 of the 81 land on contacts nobody can name, which is the shape of the
  finding rather than a defect. The count is still long for a queue, and the
  principled discriminator — anchorage and port-limit geometry, so a raft-up
  inside a designated anchorage is not a rendezvous — is Build 2's zone layer.
  Recorded as an open question rather than tuned around.
- **A measurement hazard, now guarded.** The graph accumulates alerts across
  runs, which is correct for the graph and a trap for measuring a rule change:
  re-running the pipeline after tightening `dark_rendezvous` reported 81 new
  alerts against a store still holding 667 from the previous run's looser rule,
  and the final table scored both. `run_scenario_pipeline.py` now prints a NOTE
  when it finds alerts already there. **Delete `data/graph.sqlite` before
  measuring a detector change.** This was found by disbelieving a number that
  had failed to move when it should have.
- `tests/test_radar.py` gains six tests, every one driving real code: the
  locality repair (a silent contact 300 km away must not fire the rule), the
  ambiguous admission, and the three endpoints through the real FastAPI app.
  A seventh covers `_epoch` reading a tz-naive Parquet timestamp as UTC — the
  host's local zone would otherwise slide every radar track on the map, silently,
  and differently on Eshan's laptop than in the sandbox.

---

## ADR-030 — The maritime zone layer, and the boundaries this project refuses to invent *(Accepted)*
**2026-08-16. Build 2 of the Tier-1 brief.**

**Context.** The system understood four hardcoded circles. The requirement asks
for maritime geography: exclusive economic zone, contiguous zone, territorial
sea, the international maritime boundary line, port limits, anchorages, oil
terminals and single point moorings, and established shipping lanes. Five named
analyses are unbuildable without it and straightforward once it exists. It is
the highest coverage-per-effort item in the whole requirement.

**Decision — six parts.**

**(a) A zone is a queryable object, not a picture.** The requirement is not
"draw the EEZ", it is "tell me who was inside it, when, where they came in and
where they went out". So a zone carries geometry *and* a res-6 H3 covering, and
membership is a two-stage test: a cell lookup that over-selects, then an exact
containment test on the handful of candidates. That is the CLAUDE.md §3 hash
join applied to geography, and it is what makes the layer affordable over a
corpus of two hundred thousand positions.

The covering is **dilated by one ring on purpose**. A res-6 cell is ~7 km
across and a working port area is 8-15 km, so a 2 km single point mooring
contains no cell centre at all and `geo_to_cells` returns the empty set. A zone
indexed by the empty set is a zone no vessel is ever inside — the silent-failure
shape this project keeps rediscovering. `test_a_zone_smaller_than_a_cell_is_
still_indexed` fails if the dilation is removed, and asserts the fixture still
exercises the case.

**(b) The statutory limits are NOT derived, and that was a deliberate choice
against a working implementation.** Deriving the 12/24/200 nm limits from a
public coastline mask was built, measured and **discarded**. It works
arithmetically and is wrong in ways a reader cannot see:

* UNCLOS measures from **declared straight baselines**, not from the low-water
  line, and India has declared them across the Gulf of Kachchh and the Gulf of
  Khambhat — so a coastline-derived territorial sea sits inside the real one
  exactly where the traffic is densest;
* there is **no median line** with Pakistan, Oman, the Maldives or Sri Lanka, so
  a 200 nm envelope runs straight through four other states' waters;
* the resolution is ~7 km.

Transcribing them from memory is worse: the India–Pakistan IMBL is *disputed* —
the Sir Creek terminus is unresolved and the maritime boundary seaward of it has
never been delimited by agreement — and a plausible-looking polyline for a
contested boundary is the exact overclaim this project exists to engineer
against. Natural Earth's "maritime boundary indicator" lines are reachable and
are cartographic indicators at 1:10,000,000, which would look official and would
not be.

So EEZ, contiguous zone, territorial sea and IMBL arrive through
`ingest/zones.py` from a published file, or they do not arrive. **The kinds
exist, the analyses that need them are built and tested, and the pipeline names
the gap out loud.** Marine Regions (VLIZ) publishes all of them as GeoJSON;
`maritime-isr ingest zones --path <file> --kind territorial_sea` lands them with
no code change.

**(c) An analysis that cannot run says so, by name.** "Anchored outside port
limits" asks whether a vessel is stopped *inside* territorial waters, so with no
territorial sea loaded it is idle. `anchoring_analysis_status` returns
`(False, "IDLE — no territorial_sea zone is loaded … load a real one with …")`,
the pipeline prints it, the API returns it and the UI greys the layer with the
reason attached. Returning an empty list would be indistinguishable from having
looked and found nothing, which is the same defect this codebase has now found
under six different names.

**(d) A drawn area and a statutory boundary are the same object.** An operator
geofence lands in the same table, with the same schema, and is answered by the
same query route. The only things that distinguish it are `authority: operator`
and `confidence: 1.0` — and the second is not flattery: the operator's box is
exactly where the operator says it is, which is more than can be said for
anything else in the layer.

**"Draw a box anywhere. I'll tell you who was in it" is answered in about eight
seconds, on demand.** A box drawn ninety seconds ago has no precomputed
transitions, and `nobody was here` would be a lie, so the query falls back to
computing from landed positions — the zone's cell covering hash-joins against
the `h3_r6` already stamped on every position at ingest, reducing 209,000 rows
to the vessels that were anywhere near, and only those are walked. Through the
**same** `transitions_for_track` the pipeline uses, because a drawn box answered
by a second implementation is not the same kind of object.

**(e) Zone entry and exit are first-class events.** `zone_transition` is a
landed table on the same footing as encounters and gaps, and the crossings carry
bearings so "entering from where, leaving to where" survives into the rules and
the graph. Two things are handled explicitly because getting them wrong is
invisible:

* **the crossing is interpolated onto the boundary** between the last outside
  fix and the first inside one. A vessel at 15 knots covers 4.6 km between
  ten-minute fixes, so snapping to a fix puts the crossing kilometres inside the
  zone and every bearing computed from it is a bearing from the wrong place;
* **a track that starts inside was not seen entering.** `entry_censored` says
  so, and the bearing is `None` rather than a fabricated direction. On the
  corpus, **1,543 of 5,518 transitions are censored** — 28% — which is far too
  many to leave implicit.

**(f) The four hardcoded circles became data.** `SENSITIVE_ZONES` is now a view
over `zones.derive.SENSITIVE_AREAS`, the geometry is a landed row, and
`detect_sensitive_loitering` takes an optional `ZoneIndex`. With one it watches
every `sensitive_area` **and every operator `geofence`** — which is what turns
geofencing from a stub into a feature, at the one place it is easiest to fake.
Without one it tests the same four circles it always did, so the migration
cannot be blamed for a behaviour change.

**The port gazetteer gap, closed and measured.** Twenty-five west-coast
facilities the gazetteer did not know — Mormugao, Okha, Dwarka, Ratnagiri, New
Mangalore, Dahej, Veraval, Diu, Karwar, Beypore, Vizhinjam and fifteen more. A
stop at any of them produced no port call, no `docked-at` edge and no port-risk
signal: the vessel simply appeared to stop in empty water. `ports.gazetteer_
recall()` measures the effect on the same corpus with the same code path by
re-deriving with the old sixteen-name list and the new one, and
`GAZETTEER_V1_NAMES` is recorded in the source so the before/after figure does
not depend on which commit is checked out.

**Alternatives rejected.** *Deriving the limits and labelling them* — rejected;
see (b), and note that a label on a map layer is read by nobody at 0300. *A
`geofence/` module separate from the standing zones* — rejected: it would have
made "the same kind of object" false in the one place it is easiest to check.
*Storing zones as H3 cell sets alone* — rejected: the cells are an index, and a
7 km-resolution answer to "which side of the line" is not an answer. *Deriving
shipping lanes from the generated corpus* — rejected outright; that is fitting
the detector to the test set. The lanes are customary centrelines and say so.

**Consequences.**

- **Measured, synthetic, through the landed pipeline:** 63 zones, 4,720 zone
  transitions over 1,517 tracks (26% with a censored entry), 77
  `maiden_zone_visit` alerts, 36 `lane_deviation` alerts,
  `anchored_outside_limits` idle. Z1 and Z2 detected; all three new decoys
  correctly quiet; Z3 unfindable without a territorial sea.
- **These two rules broke the ADR-004 precision gate and it is not yet fixed.**
  Scenario precision fell 100% → 50%: eight decoys fired, five on
  `lane_deviation` and three on `maiden_zone_visit`, plus 72 alerts on
  background traffic. Every threshold that would restore it is one I cannot
  defend — a 21-day history requirement cuts maiden visit to exactly one alert,
  which is Z1, and that is fitting to the answer key rather than choosing a
  threshold. The distribution offers no break to anchor on. Recorded as an open
  item with the numbers rather than tuned away; the likely resolution is that
  "first visit to a zone" is a query at this history length and not an anomaly.
  See STATE.md.
- **The port circles overlapped on the first pass, and that was the real cause
  of the 643-alert maiden-visit figure** — not the rule. Sikka and Vadinar are
  11 km apart and both had a 10 km radius, so a vessel alongside at one was
  inside five zones and the "three distinct zones already visited" qualifier was
  met within an hour of arriving anywhere in the Gulf of Kachchh. Radii are now
  capped at half the distance to the nearest neighbouring port. Overlapping
  zones are not zones; they are one zone with several names.
- **Lane deviation measured on synthetic data is optimistic by construction, and
  the scenario says so in its own truth row.** The generator routes its vessels
  with the same land-avoiding router the lane centrelines were drawn from, so
  generated traffic sits on the lanes almost by definition and only a vessel a
  scenario deliberately sends off-route deviates. This number says very little
  about real traffic.
- **Maiden visit needs a history qualifier or it is a list of the fleet.** Over
  eight weeks, a vessel's first appearance anywhere is her first appearance in
  every zone she passes through. Unqualified, the rule fires once per hull.
  `MAIDEN_MIN_PRIOR_ZONES = 3` means the subject is a hull we have watched work
  this coast, now somewhere she has never been.
- The three new detectors carry **higher thresholds** (0.60-0.65) than the older
  rules, because they run over geometry whose own confidence is 0.35-0.55 —
  working circles standing in for declared limits, customary routes standing in
  for adopted ones. A finding built on weaker ground clears a higher bar.
- The graph gains a `zone` node type and four edges. One of them,
  `loiter-in-zone`, has been emitted since Phase 5 against a `zone:<name>`
  destination **that was never a registered node type**; it validated only
  because nothing checked. Both halves now exist.
- Group Z adds six scenarios: three true anomalies and three decoys, one decoy
  per condition of the anchoring rule. There is deliberately **no scenario
  asserting that crossing the IMBL is an offence** — the line is disputed, and a
  generated corpus that treated a crossing as ground-truth wrongdoing would bake
  a contested legal claim into the answer key, where the measurement would then
  reward a detector for making it.

---

## ADR-031 — One ranked object at the centre, and three defects the ranking exposed *(Accepted)*
**2026-08-22. Section 3, Area 1 of the IDEX Challenge 82 brief.**

**Context.** The Indian Coast Guard's Coastal Surveillance Network complaint is
precise: the existing software presents position and kinematics and has no
analytical tool to classify a track as a Vessel of Interest, so classification
falls to a human whose decision quality degrades with fatigue. The challenge
names six capability areas. The stated outcome, repeated twice in near-identical
words, is **one** thing — rank Vessels of Interest with supporting evidence and
reduce operator workload.

Five of the six areas are feeders. One of them *is* the object. This project
already had the feeders — an anomaly library, a radar dark cascade, a sanctions
matcher, an object graph — and no object. Alerts landed in a flat queue, risk
was a four-component index on a separate screen, and nothing tied a reason to a
next action.

**Decision — six parts.**

**(a) The subject of a Vessel of Interest need not be a vessel.** Measured on
seed 7, **52 of 55 alerts land on a `contact:` or `detection:` node**, not a
hull. A target nobody can name is precisely what makes a finding, so the object
ranks *subjects* and says which kind each is. Requiring a named hull would have
discarded the dark-vessel path — the one capability the requirement most needs —
from its own queue.

**(b) The score decomposes exactly, or it is not a score.** Factors combine as a
noisy-OR over independent evidence, `1 - PROD(1 - weight x confidence)`. That
does not decompose additively, but its logarithm does, exactly, so the result is
allocated back in log space: `points_k = score x ln(1-s_k) / SUM ln(1-s_j)`,
and `SUM(points) == score` to floating point. The identity is asserted in the
tests because it is the whole claim. An operator who reads "0.81, of which 0.42
is the sanctions designation and 0.39 the dark contact" can argue with the
system; one who reads "0.81, driven mainly by sanctions" cannot.

This does **not** overturn ADR-024's refusal of a blended number on the findings
table. What ADR-024 refused was an *undecomposable* float standing in for stated
facts. A number whose every point traces to a named factor carrying its own
evidence is a different object, and the findings table keeps its ordered-tuple
ranking unchanged.

**(c) Repeats combine two different ways, and conflating them overstates
confidence.** Four loitering episodes are four things that happened and each
raises the claim (noisy-OR). A designation arriving from the landed match table
*and* from walking the graph's ownership chain is **one fact seen twice**, and
combining it as two independent observations took 19 hulls to 0.97 confidence on
the first build — a system that sounds more certain the more places it looks.
`FactorSpec.repeats` distinguishes `occurrences` from `restatement`; the second
takes the maximum and keeps the corroboration as evidence.

**(d) Recommendations state capability and compute feasibility.** "Call her on
VHF" is not advice if she is beyond the nearest station, so range is worked out
from the station network's geometry using the same horizon function the radar
model uses, and an infeasible action is returned *with its reason* rather than
hidden. Every recommendation carries `performed_by` and `system_capability`, and
for most of them the honest answer today is "this instructs a human; the system
cannot do it" — the EO loop is Area 5, arrival notifications Area 4, radio
Area 6. Nothing here is autonomous: there is no path from this module to an
action.

**(e) The question answerer has no generative step, and that is the design.**
A question matches one of a closed set of intents; the intent retrieves; the
answer is assembled from the rows that came back. No fact that is not in a
retrieved row can reach the text. It is duller than a language model and it
cannot confabulate, which is the right trade for the surface an operator
calibrates their trust against. `QuestionAnswerer` is an interface so a model
can be substituted later under the same contract.

Three outcomes are kept distinct, and the middle one is most of the value:
`answered`; `no_data` — understood, and the system holds nothing, phrased as a
statement about the *record* rather than about the vessel; `unsupported` — about
something this system does not carry, naming which area of the build would carry
it. Asked "what cargo is she carrying?", it says cargo arrives on the arrival
notification, which is Area 4 and is not ingested.

**(f) Suppression is part of the product.** A subject that carried a signal and
stayed off the list is returned with its reason, the discipline ADR-028
established for the radar cascade. It matters more here: this list is where an
operator forms their model of what the system does, and a queue that silently
drops things cannot be calibrated against.

**Three defects the ranked list exposed**, none of which was visible while the
output was a flat alert queue:

1. **42 of 43 `dark_rendezvous` alerts fired inside a berth or a designated
   anchorage** — 32 of them 470 m from the Mangalore port coordinate, 8 at
   Mundra, 2 at Kochi. One alert in forty-three was in open water. Two hulls
   within 500 m at under 2 knots is the encounter primitive's definition, and
   alongside a terminal that describes every ship in the port. The loitering
   rule had suppressed waiting areas since the Kandla finding; the rendezvous
   rule had never been taught to ask. It stayed invisible while the corpus was
   SAR-only — six unmatched contacts can only meet by accident — and coastal
   radar (ADR-028) lit up the anchorages. Fixed by reusing the shared
   `at_waiting_area` helper. **43 alerts to 1; dark-contact precision and recall
   unchanged at 100% / 86%.** The cost is stated: a genuine transfer at anchor
   is now invisible to this rule, and separating the two needs activity
   classification, which is Area 2.

2. **All 9 `dark_vessel` alerts were recorded as real data.** `add_alert`
   derives `is_synthetic` by looking the subject up in `nodes`; `detect_dark_
   vessels` pointed its alerts at `detection:<id>`, a string nothing had ever
   created, so the lookup returned no row and the flag defaulted to 0. ADR-019
   rests the entire real-versus-synthetic split on that column. Same shape as
   ADR-022's shadow stub and ADR-028's second finding, for the third time:
   *minting an id is not creating the node*. Fixed twice over — the detector now
   publishes its subject (`ensure_detection_node`, inheriting the flag from the
   sensor by the same reserved-token rule contacts already use), and `add_alert`
   now **refuses** an unknown subject rather than guessing, because an
   unanswerable question is not evidence of realness.

3. **`reported-gap` edges were reachable from no graph view at all.** Fourteen
   edges in a family that is neither structural nor in any context group, so
   switching every layer on still could not draw them. An edge family the
   product holds and cannot show is worse than one it does not hold: the graph
   looks complete and is not. Added as a `gap` context layer.

**One inconsistency corrected while wiring the score.** `anomaly.risk` discounts
unreviewed alerts to 0.6 of their stated score, and copying that into the
assistant applied a 40% haircut to alert-derived factors while registry-derived
ones kept full value — pushing all nine dark-contact subjects to the bottom of
their own queue. In a risk *index* spanning months the discount is right; in a
*queue* whose entire content is unreviewed it cancels within one source and
distorts across sources. Open alerts now stand as stated and analyst
confirmation raises confidence, the only thing in the scoring that moves a
number up.

**Consequences.**

* The ranked list is the frame every later area plugs into. Three of six
  evidence families — paperwork, imagery, radio — are declared and empty, and
  the surface says so with the area that would fill each. "If adding an area
  does not change what appears on that list, it was built in isolation."
* The workload claim is measured rather than asserted: **2,713 tracked targets
  and 13 raw detector alerts resolve to 32 subjects, about 1 in 85 of the
  targets in the picture** — on the synthetic scenario corpus, in this sandbox,
  and that label travels with the number. It measures how much the system
  shortens a queue, not whether the right things are in it; precision and recall
  are measured separately against scenario truth and neither has been measured
  on an operational feed.
* `MIN_SCORE = 0.25` is the attention bar, set as arithmetic rather than tuned:
  it is a single factor at the catalog's weakest weight and full confidence, so
  the rule reads "one weak fact is not a reason".
* The assistant is enrolled in the existing ground-truth isolation guard
  (`DETECTION_PATHS` in `test_scenario.py`). It is the last place that may see
  the answer key.
* `maritime_isr/api/__init__.py` no longer builds the application at import
  time. Importing `api.reader` — a DuckDB read layer with no web dependency —
  used to construct the whole FastAPI app as a side effect, which became a real
  cycle the moment a non-API module needed the reader.

---

## ADR-032 — Predictive analysis of AIS tracks, and one capability measured out of the product *(Accepted)*
**2026-08-22. Section 3, Area 2 of the IDEX Challenge 82 brief.**

**Context.** AIS carries kinematics, position and self-declared static
information. The requirement is blunt about the gap: the existing software
"is not able to classify authenticity of static information transmitted on AIS
and activities of the vessels". The brief adds a word that decides the shape of
the work — *predictive* — and names four deliverables: identity authenticity,
forward projection, activity classification, and per-area baselines learned from
local history.

**Decision — five parts.**

**(a) Identity authenticity is arithmetic before it is inference.** Two checks
carry most of the value and neither needs a model. The IMO check digit is a
checksum over the first six digits and rejects 90.3% of random seven-digit
strings — verified in the tests rather than quoted, because the rule's whole
justification is that number. The MMSI's Maritime Identification Digits are
allocated by the ITU to a flag administration, so a hull broadcasting a
Panamanian prefix while declaring an Indian flag states two incompatible things
about itself in one message stream. Registry consistency (name, call sign,
vessel type) is promoted from "a component buried in a score" to a rule of its
own, with confidences that differ by field and say why: a call sign is issued
with the flag and changes only when the flag does; a name changes on sale and
registries lag.

`MID_TO_FLAG` is **deliberately partial**. This rule's entire value is that it
almost never produces a false positive, and the fastest way to destroy that is a
wrong row. An unallocated MID produces `not_checkable` — no claim at all —
rather than a guess.

**(b) Every check has three outcomes, not two.** `contradiction`, `ok` and
`not_checkable`. An absent IMO is a gap in the record and reporting it as a
contradiction would fire on most of an honest corpus; a surface also has to be
able to say "we looked and it was fine" as distinct from "we could not look".
`summarise()` reports all three, because a check that is `not_checkable` on 95%
of a corpus has told you almost nothing and a report of contradictions alone
would present that silence as a clean bill of health.

**(c) Activity classification lives in `tracks/` and reads motion only.** Area 3
requires that the same behaviours be recognisable "whether the track came from
radar or AIS", and says outright that if they are not, "that is a defect in the
fusion core". So the classifier takes a built track and never asks which sensor
produced it — satisfied by *placement*, not by a compatibility shim, and
enforced by `test_activity_is_identical_on_radar_and_ais`. Rules rather than a
learned model, because there is no labelled corpus of vessel activity for this
area and generating one from the scenario would be training a classifier on its
own answer key. `unclassified` is a first-class output: a confident wrong
activity costs more than an admitted gap.

**(d) Baselines are a landed artifact, not a constant.** The brief's charge that
this was "currently absent from the system entirely" was accurate — every
threshold here is global, which is why the loitering rule was once a Kandla
anchorage detector and the rendezvous rule a Mangalore berth detector (ADR-031).
Baselines are derived per H3 cell at **res 5** (~8 km across: an approach
channel, an anchorage and the open water outside them fall in different cells,
while a cell still accumulates enough observations to say anything), landed with
the full provenance envelope, and queryable. R5 is *named* in `h3util` but
deliberately **not** added to `RESOLUTIONS`: stamping it on every positioned row
would be a schema change across the whole corpus to serve one derived artifact
that computes its own cell.

`is_unusual` is **three-valued on purpose**. True, False and None are genuinely
different answers — "unusual here", "ordinary here", and "we have not watched
here enough to have an opinion" — and a boolean collapses the third into the
second, which reports every unmonitored patch of ocean as clean.
`MIN_OBSERVATIONS = 100` is the floor; below it a 95th percentile is an order
statistic over a handful of points that moves by knots when one vessel passes.

Measured on the corpus: **770 cells, 212 usable (27.5%)**, and the local normals
are genuinely different — the Kandla approach carries a 95th-percentile speed of
12.0 knots over 19,768 observations of 128 vessels, while the Mumbai anchorage
carries 6.5 knots with a median of 0.0.

**(e) Forward projection is built as an assertion and is NOT promoted to a
suspicion factor.** This is the part worth reading twice.

The brief asks that "a vessel which departs from its own predicted path is
detectable as such". It is detectable. It is also ubiquitous. Swept over 209 AIS
tracks across every combination of lead time, persistence gate and severity
threshold:

| lead (h) | min run | radii ≥ | departures | % of fleet |
|---|---|---|---|---|
| 0.5 | 2 | 1 | 1,770 | 98% |
| 0.5 | 3 | 5 | 377 | 73% |
| 1.0 | 3 | 5 | 775 | 92% |
| 3.0 | 2 | 5 | 1,177 | 97% |
| 3.0 | 2 | 20 | 22 | 10% |

There is no plateau — the rule flags almost every hull in the picture, or past a
cliff almost none, and a threshold sitting on a cliff is fitted to this corpus
rather than to the phenomenon. The cause is physical and not fixable by tuning:
at three hours a merchant runs 45 nm, a cone tight enough to notice an
alteration is about ±4° of heading, and **every vessel alters course at every
waypoint**. Dead reckoning is a good predictor along a leg and a useless one
across a turn, and a coastal voyage is mostly turns.

Under ADR-004 — precision before recall for anything analyst-facing — it is
therefore **not** a factor on the Vessel of Interest list, `assistant/catalog.py`
has no entry for it, and `test_projection_is_not_a_registered_suspicion_factor`
stops it being quietly added later without a fresh measurement. What the
projection *is* good for is kept: an assertion an operator can see ("where did
you expect her, how sure were you"), bridging a gap, and a comparison callable
on a subject already suspicious for another reason. What would make it
discriminate is stated so it is not rediscovered: prediction has to be
**route-aware**, and the zone layer's customary lanes (ADR-030) are what a
corridor model would be fitted to.

**Two defects the build found in itself.**

1. **`survey_pattern` was claimed on 151 of 209 tracks.** The rule asked for
   four long legs and three near-reciprocal turns, and any multi-week coastal
   rotation supplies both — long legs on passage, reciprocals at each end. A
   survey is not "has some reciprocal turns"; it is *made of* them and goes
   nowhere. Requiring the reciprocals to be a quarter of all course changes and
   straightness to be under 0.35 takes it to 0 false claims on whole tracks,
   while windowed classification still finds genuine patterns.

2. **The scenario's reserved MMSI block collides with an ITU reserved form, and
   the identity rule fired on the entire synthetic fleet.**
   `scenario.identifiers` mints into 999000000-999999999 precisely because 999
   is not an assignable MID; ITU-R M.585 separately reserves the two-digit
   prefix `99` for aids to navigation. Both true, and together they made
   `check_mmsi_form` report all 222 scenario vessels as "misrepresenting what
   kind of station it is" — at 0.8 confidence, through the one detector built to
   have no false positives. `_is_project_reserved` exempts the block, as a
   statement about *this project's identifier space* and never about ground
   truth — the same line `graph.identity.sensor_ref_is_synthetic` already walks
   — and the exemption is narrow enough that a genuine AtoN MMSI is still
   caught.

**What is NOT built, and why it is being said rather than implied.**

The brief's *declared destination against implied and historical destination*,
and *declared arrival time against plausible arrival time*, are **not
implemented**. The reason is data, not effort: `ais_position` carries no
declared destination or ETA, because the scenario generator emits AIS position
reports and no message-5 voyage data. Building the comparison against a column
that does not exist would produce a detector that can never fire — the dead-rule
failure this project has now hit twice (`dark_contact` versus `dark_vessel` in
ADR-031, and the whole family of never-firing detectors STATE.md tracks). The
honest sequence is: extend the scenario AIS emitter with declared destination
and ETA, regenerate, then build the comparison against real (synthetic) inputs.
That is the first item of Area 2's remainder.

**Consequences.**

* Two new detectors, both gated and both for opposite reasons.
  `identity_contradiction` is gated **low** (0.40) because its checks are
  arithmetic rather than inferential — there is no marginal, threshold-sensitive
  finding for a gate to hold back. `notable_activity` is gated **high** (0.55)
  because an activity is a description, not a finding: only three activities are
  eligible at all, and most vessels are transiting or anchored.
* The ranked list gains its first `identity` and `motion` factors sourced from
  Area 2, which is the brief's own test that an area was wired in rather than
  built in isolation.
* The arithmetic identity checks **cannot fire on scenario data by
  construction** and are built for the landed real GFW corpus. Their precision
  must be measured there, on the laptop; until then they are "built, unverified
  on host" (CLAUDE.md §5).
* `run_anomaly_library` takes `identities` and `baselines` as optional inputs,
  so a caller written against the original six detectors keeps working and the
  new ones stay quiet rather than raising.

---

## ADR-033 — Classification of radar data, and a vocabulary derived from the confusion matrix *(Accepted)*
**2026-08-22. Section 3, Area 3 of the IDEX Challenge 82 brief.**

**Context.** Radar gives kinematics and position and nothing else. The
requirement asks for vessel type, activity and interactions, all from motion,
and the brief is clear about why it matters: *"radar is their primary sensor and
it is the only sensor that sees a vessel which does not want to be seen."*

**Decision — five parts.**

**(a) The output vocabulary is derived from the confusion matrix, not
declared.** The brief's instruction is explicit — *"a small set of classes it
can genuinely separate, with honest confidence, is worth far more than a long
list of classes it guesses at"* — and the tempting implementation is a
hand-written list of coarse classes. That is a claim about the world. What this
system is entitled to make is a claim about its model, so `confusable_groups`
reads the measured confusion matrix and merges any pair mistaken for each other
more than 25% of the time. If a later feature genuinely separates a bulker from
a tanker, the groups shrink on their own and nothing needs editing.

Measured over 209 AIS tracks, split by hull:

| | |
|---|---|
| fine accuracy | 65% |
| **coarse accuracy** | **90%** |
| cannot separate | `[Aframax, bulker, product_tanker]`, `[Suezmax, VLCC]` |
| derived vocabulary | fishing, general_cargo, merchant, reefer |

Fishing is unmistakable at 14/16 — a third of the speed and three times the turn
rate. Reefer is 5/5. **The tanker/bulker/cargo cluster is not separable from
motion and never will be**, because a laden bulker and a laden product tanker at
13 knots on a great-circle course are doing the same thing. Saying so is the
product.

**(b) Split by hull, never by track.** CLAUDE.md's anti-pattern list bans
splitting an ML dataset by chip rather than by scene. Tracks from one vessel are
the same hazard one domain along: a hull on both sides lets the model memorise
her rather than her class. `train()` groups on the hull key and the accuracy
above is measured on held-out hulls.

**(c) Trained on AIS, applied to radar — which is only sound because the
features are motion and nothing else.** No feature reads an identifier, a
message rate, or a sensor name, and `test_type_features_are_sensor_blind`
asserts the feature vector is byte-identical for the same track presented as AIS
and as radar. That is Area 3's requirement — *"the same behaviours should be
recognisable whether the track came from radar or AIS. If they are not, that is
a defect in the fusion core"* — satisfied by construction rather than by a
compatibility shim.

**(d) Interactions are a new module, and the anchorage exclusion has to happen
before the pair search.** `detect_encounters` finds sustained proximity at low
speed and cannot describe two ships steaming in company two miles apart.
`tracks/interactions.py` classifies *relative* motion instead: how the
separation behaves, whether the courses agree, whether one holds a constant
bearing astern.

Excluding anchorages at classification time is correct and far too late — every
hull in an anchorage is near every other hull for days, so the candidate set is
dominated by pairs that will be discarded. The pair-sample guard tripped at
200,000 before the filter existed. Testing `at_waiting_area` per resampled point
is the obvious fix and is also too slow (3.1 million points against 35 gazetteer
entries). Precomputing the *cells* turns it into a set lookup — the same
hash-join move the architecture already runs on — and the search drops to two
seconds.

**(e) The contact profile is where Area 3 pays off.** *"'Unidentified contact'
is a position. 'Probable fishing vessel, loitering, no transponder, inside
territorial waters' is intelligence."* Everything needed already existed and had
never been assembled. `fusion/contact_profile.py` joins the cascade's verdict,
the inferred type, the inferred activity and the zone layer onto one object and
produces exactly that sentence. Measured on the eight dark contacts in the
corpus, it produces lines like *"Likely merchant, transiting, no transponder,
about 175 m"* — and, where it cannot, it says which part is missing and why.

It **profiles, it does not detect**: darkness was decided by the cascade and is
not revisited, and `test_the_profile_does_not_re_decide_darkness` greps for the
cascade's own variables to keep it that way.

**What the interaction detector measured, and what that means.**

A sweep over the combined 1,517-track picture found another cliff rather than a
plateau:

| min_minutes | interactions | of which real |
|---|---|---|
| 60 | 8 | **0** — all background fleet traffic sharing a lane |
| 120 | 0 | — |
| 180 | 0 | — |

So the gate is set at 120 minutes and the honest consequence is stated rather
than tuned away: **this detector produces zero findings on the current corpus**,
because the corpus contains no formation that persists past an hour. That is a
fact about the corpus and not a capability claim; all four behaviours are driven
end to end by fixtures.

The transfer case could not be validated at all, and the reason is worth
recording: **the scenario's transfer counterparties are dark by design.**
`chain_a`, `chain_b`, `coast_dark_party` and `spoof_partner` have no AIS track,
which is the point of those scenarios. Checked across all 18 x 4 track pairings,
`vessel:spine` and `vessel:receiver_alpha` never come within 500 m in a shared
epoch on any sensor. The transfers are therefore not observable as sustained
close co-location in the landed data, and a detector that claimed them would be
claiming something the corpus does not contain.

**Consequences.**

* The ranked list gains a `vessel_interaction` factor kind and dark-contact
  factors gain an inferred profile in their narration — the brief's own test
  that an area was wired in rather than built beside.
* A caller that already ran the pair search hands the result to
  `run_anomaly_library` rather than paying for it twice.
* Every figure here is measured on the synthetic corpus, trained on tracks whose
  class the generator also chose. Real performance will be lower and must be
  re-measured on the deploy host (CLAUDE.md §4.6).

---

## ADR-034 — The factors that never fired, and the four defects that kept them quiet *(Accepted)*

**Context.** Areas 2 and 3 of the IDEX Challenge 82 brief added three classes of
factor to the Vessel of Interest object: a contradicted identity, a notable
activity, and a relationship between two hulls. Each was built, gated, narrated
and wired into the anomaly library. Each fired **zero times** on the corpus.

Each zero had a stated cause, and every cause was defensible on its own. Taken
together they meant the ranked list never gained the factor classes those areas
existed to supply — and the brief sets exactly one test on that:

> After each area lands, the ranked Vessel of Interest list should visibly gain
> a new class of factor. If adding an area does not change what appears on that
> list, the area was built in isolation and needs wiring in before moving on.

Areas 2 and 3 failed it.

**Decision.** Write the situations into the corpus, and fix what writing them
exposes. **No threshold is loosened to make a rule fire.** A rule loosened until
it fires has been fitted to the absence of evidence, and afterwards it is
indistinguishable from a working one.

Group F adds sixteen hulls: three identity contradictions with two decoys that
share their surface, two notable activities with the false positive that used to
be mistaken for one, three relationships between AIS-visible hulls with a lane
decoy, and truth rows for all of it.

**What building the positive cases found. Four defects, all silent, all of the
same shape — a rule that could not fire and did not say so.**

**1. A reversal is not an event between two fixes** (`tracks/activity.py`).
The survey rule counted near-180-degree course changes fix to fix. A hull
limited to a quarter-degree per second takes twelve minutes to come about and
AIS in this corpus arrives every four, so a real reversal appears as three
sixty-degree steps and the count comes out zero. F6 was written as a genuine
ten-leg lawnmower and was classified `manoeuvring_erratically` with
`reciprocal_turns=0`. **The survey branch could not have fired on any real
survey vessel sampled at a realistic rate, which is every survey vessel.**
Reciprocals are now counted between the mean headings of consecutive straight
legs, which is what "she came about" means and is invariant to how often the
vessel was heard.

**2. Half of every cross-cell pair was discarded** (`tracks/interactions.py`).
Two defects in one loop. The candidate search took a one-ring H3 neighbourhood
at res 6 and a comment asserted that reached "roughly 9 km"; a res-6 edge is
3.7 km, so pairs in the 6-9 km band — inside the module's own declared 5 nm
reach — were dropped before any test ran. And the `a[0] >= b[0]` guard, correct
for deduplicating two hulls in the same cell, was *discarding* rather than
deduplicating across cells, because only one ordering is ever generated there.
The ring is now computed from the geometry and the pair key is ordered rather
than dropped. Both failures produce fewer findings and never wrong ones, which
is why "no interactions in this corpus" was reported with total confidence.

**3. The resampler deleted every stopped vessel** (`tracks/features.py`).
AIS cadence is state-dependent by design: ITU-R M.1371 has a Class A set report
every 10 seconds under way and every 3 minutes at anchor, and reception thinning
multiplies both — a moving hull here lands a fix every ~4 minutes, a stopped one
every ~65. The resampler's gap allowance was built from the track's *median*
interval, one number for a track whose cadence varies eighteen fold within
itself, so the stopped half of a track was dropped from every resample-based
analysis. F11 was written as a nine-hour ship-to-ship transfer with both parties
transmitting and produced **seven** usable pair samples. Everything the product
cares about at low speed was going with it: loitering, anchoring, transfer. A
gap is now interpolated across when the vessel demonstrably did not move —
1,000 m of travel between bracketing fixes, whatever the elapsed time — because
interpolating across a gap is only a fabrication if the vessel could have gone
somewhere.

**4. The pipeline query threw the second attestation away** (`tools/`).
`check_registry_consistency` needs two records of one hull. The real GFW
connector has landed both kinds from the beginning, with a comment saying why:
*"disagreement with the registry is a signal in its own right, so we keep
both."* The pipeline then selected one current row per `vessel_id`, collapsing
them, and the check answered "cannot check" for all 230 hulls. It now pairs the
self-reported row with the registry row. The generator was writing every
synthetic identity as `record_kind="registry"` — a corpus in which no vessel had
ever said anything about itself — and now writes what she broadcasts as
`self_reported` and the registry's own attestation beside it.

**And one gate that was re-derived twice, because the first re-derivation was
also wrong.** With the pair search fixed, the 120-minute persistence floor had
been measured through a broken candidate set and had to be swept again. That
sweep put the longest coincidental formation at 4.7 hours and the floor moved to
360 minutes, which looked decisive on one sample.

A second sample falsified it, and the correction is worth more than the number.
Changing the cast shifts the generator's RNG stream, so the background pairs are
a fresh draw each time; in the next corpus a pair of fishing-fleet hulls steaming
to the same ground held course and station for **11.7 hours** — longer than two
of the three authored relationships. **A fishing fleet transiting together is a
formation by every geometric test and by no useful one, and no persistence floor
will ever exclude it.** The corpus already contains a forty-vessel aggregation
decoy built to catch exactly this class of false positive, and the interaction
rule walked straight into it.

What separates the populations is how close they hold. Mean separation, over
both samples:

| | mean separation (m) |
|---|---|
| coincidental pairs, sample 1 | 6555, 8227, 7397, 6409, 8052 |
| coincidental pairs, sample 2 | 5745, 5337, 6100, 6651, 7764, 7814 |
| authored relationships | 589, 1191, 4245 |

Eleven coincidental pairs across two independent draws, none closer than
5,337 m; three authored relationships, all inside 4,245 m. So company and
shadowing are claimed only inside **2.5 nautical miles** — visual range is not
station-keeping range, and vessels deliberately working together stay close
enough to manoeuvre and talk. The 9% margin above the closest measured
coincidence is thin and is stated rather than rounded away. The 360-minute floor
is kept, and is no longer described as though it were doing the work.

**A survey pattern and a trawler are the same geometry.** Once reciprocals were
counted correctly, six-hour windows of the background fishing fleet produced
**36** survey claims and the queue went from 16 alerts to 53. Nothing in the
shape separates a vessel mowing a survey lawn from a vessel working a ground —
parallel legs, reciprocal turns, covering a box — and no tuning of the shape
gates will. Speed does: a survey vessel runs her lines at six to eight knots, a
trawler works at two to four with gear in the water. The survey branch now
declines the trawling band outright. The cost is stated rather than hidden: **a
genuine survey conducted at trawling speed is not findable by this rule** and
will be reported as fishing.

**Two headline numbers moved, and both were less solid than they looked.**

*Dark-contact recall.* Reported at 86% (6 of 7 findable episodes) before this
group existed; it reads **43% on seed 7 and 62% on seed 8**, with **precision at
100% in every draw**. A single-variable A/B — the whole pipeline run twice on
one corpus with `RESAMPLE_MAX_INTERP_M` forced to zero — produced *identical*
cascade verdicts, so none of the detector changes here is responsible. Adding
sixteen hulls to the cast shifts the generator's RNG stream, so every scenario's
noise, every coverage draw and every radar plot is a fresh sample. **A recall
figure with a denominator of seven episodes was never a capability measurement**,
and swings of tens of points between draws are what it has always been capable
of; the 86% was one draw quoted as though it were a property of the system. It
is now reported with its seed and its denominator, and precision — the number
ADR-004 actually constrains — is what holds steady.

*Generation at seed 9 fails outright*, in `background.py`'s port-visit
scheduling, with an event landing outside the corpus window. Confirmed
pre-existing: it fails identically with group F removed. Recorded in STATE.md as
an open item rather than fixed here.

**Consequences.**

* All three factor classes now reach the ranked list, and `tests/test_factor_coverage.py`
  is the brief's own test made executable — it fails if any of them goes quiet again.
* Every positive is paired with a decoy that shares its surface: punctuation in a
  registry name, a vessel-class quibble, a coastal rotation, a lane overtake.
  A group of positives measures recall and says nothing about precision.
* F1 breaks a corpus invariant on purpose — a synthetic IMO that fails its own
  checksum — and does it through the same narrow gate C3 uses for its impossible
  speed: declared in its truth row, whitelisted by scenario id *and* rule name.
  An undeclared broken IMO is still a generation failure.
* `check_mmsi_flag` and `check_mmsi_form` remain **unmeasurable on synthetic
  data and that is not fixed here.** Scenario MMSIs live in a reserved 999 block
  precisely so no synthetic hull can ever wear a real vessel's identity, and
  that reservation makes a flag contradiction unconstructible. The safety
  property is worth more than the test coverage; the two checks are exercised by
  unit fixtures and must be measured on the landed real GFW corpus.
* Every figure here is measured on the **synthetic suite**. Real-feed numbers
  will be lower and must be re-measured on the deploy host (CLAUDE.md §4.6).

---

## ADR-035 — The voyage she declares, and the four defects between the message and the rule *(Accepted)*

**Context.** Area 2 of the IDEX Challenge 82 brief asks for something the system
did not have, in the plainest terms it uses anywhere:

> Compare the destination the vessel declares against the destination its
> behaviour implies, and against where it has historically gone — a declared
> destination that the track has never been consistent with is **one of the
> strongest and simplest suspicion factors available**. Do the same with
> declared arrival time against plausible arrival time given current position
> and speed.

It was not built, and the reason was upstream of any rule: **nothing landed a
declared destination.** AIS message 5 carries destination, ETA and draught; the
generator emitted no message 5; and the live connector filtered to
`PositionReport` and returned `None` for everything else. A comparison against a
column that does not exist is a detector that can never fire.

**Decision.** Land the message, then write the rules.

* `schemas.records.VoyageDeclaration` — a canonical record, separate from
  `PositionReport` because message 5 is a separate message with its own cadence
  and its own nullity. A vessel transmits a hundred positions per declaration;
  folding them together would either repeat the declaration a hundred times or
  leave 99% of position rows carrying a null nobody could interpret.
* `ingest.aisstream._parse_static` — the live connector now subscribes to
  `ShipStaticData` and lands it. Message 5's ETA has month, day, hour and minute
  and **no year**, so the year is inferred against the moment it was heard;
  0/24/60 are the not-available codes and return `None`, because a vessel that
  declined to state an ETA has said something different from one that stated a
  wrong one.
* `anomaly.voyage` — two checks, split the way `anomaly.identity` splits its
  own. **The arithmetic one**: could any hull get from here to there by then.
  **The behavioural one**: was she ever heading towards the port she named.
* The generator declares a destination on **every ordinary port call**, honestly.
  A rule tested only against liars measures recall and says nothing about
  precision. 3,091 declarations over 131 hulls is the denominator; group F adds
  three hulls, two of which lie and one of which is diverted honestly.

**`destination` is landed as free text and deliberately not normalised.**
"JNPT", "NHAVA SHEVA", "INNSA" and "JNPT>>SIKKA" are all things real
transmitters send. Resolving them is a judgement with a confidence and belongs
downstream where a confidence can be attached; landing a cleaned value throws
away the evidence an analyst has to see. `resolve_destination` matches exactly —
name, alias, or the leading token of a route string — and returns `None` for
everything else. **No fuzzy matching:** a missed resolution costs a finding, a
wrong one tells a watchkeeper a ship is lying about a port we picked for her.

**Declared against *historical* destination is not built, and that is a
decision.** An unqualified "she has never been there before" fires on every
vessel's first call at every port — the Z1 lesson from ADR-030, which cost 168
hulls. A first-ever destination is a fact about our observation window, not
about the ship. History is available to the assistant as context, not as a rule.

**Four defects, and every one of them made the rule quieter or louder without
saying so.** The pattern is now familiar enough to be worth naming: each was
found by watching the alert count rather than by reading the code.

1. **The rule fired on the whole honest fleet: 43 alerts, 41 innocent hulls.**
   Required speed is distance over *remaining* time, so as the remaining time
   goes to zero the required speed goes to infinity. A vessel 60 km from her
   berth with half an hour left on a two-day-old ETA "needs 200 knots". She is
   late, which is the commonest thing at sea, and nobody retypes an ETA once it
   slips. The test is now "is she short by an amount no schedule slips by" —
   six hours — and an **expired** ETA is not checked at all, because the
   question the brief asks is forward-looking and a passed ETA has stopped
   asking it.

2. **The heading check read every fix after the declaration, with no end.** So
   it scored her arrival, her berth and her *next* voyage against a destination
   she had reached and left days earlier. Bounded at the stated ETA.

3. **A `timestamp[us]` column divided as if it were nanoseconds** — 234 fixes
   spanning nineteen hours came out as a span of 68 seconds, so the heading
   check answered "not enough track to say which way she went" about the one
   hull in the corpus written to steam the wrong way. `tracks.kalman.epoch_s`
   exists precisely because this bug atomised every track once before; the fix
   is to use it, and the test is there so a third occurrence is caught by a
   machine.

4. **Eleven honest hulls were called liars for swinging on their cables.** A
   ship at anchor yaws through most of the compass over a tide, so *every* step
   is more than 100 degrees off the bearing to the port she is waiting to enter
   and the away fraction comes out at a perfect 1.0. The heading check now
   requires her to be making way at all — three knots, the same line
   `tracks.interactions` draws, and for the same reason: a rule about direction
   needs a vessel that has one.

**Result on the corpus:** 43 → 13 → **2**, and the two are the two hulls written
to lie. Zero false positives across 3,091 declarations from 131 hulls.
`voyage_contradiction` reaches the ranked list.

**Consequences.**

* `ais_voyage` is a new conformed table and is in `ALL_TABLES`, so
  `scenario clear` removes it. It was also missing from
  `api.reader.CONFORMED_TABLES` for one run, with the silent effect that tuple
  always risks: the generator wrote it, `has()` answered False, and the rule
  reported no findings over a corpus that contained three.
* The connector change is on the **PARKED** live path (ADR-013): it is built and
  correct and has never consumed a live message, because there is no always-on
  host. Its parser is exercised by fixtures.
* Every figure here is measured on the **synthetic suite** (CLAUDE.md §4.6).

---

## ADR-036 — Pre-arrival notifications: unstructured in, evidence out *(Accepted)*

**Context.** Area 4 of the IDEX Challenge 82 brief names a problem the rest of
the system does not have:

> Pre-Arrival Notification of Ships data reaches the Coast Guard as PDF, Word
> and spreadsheet attachments by email. It contains vital information but cannot
> be stored in a structured database or fused with AIS because of its format.

Everything upstream of this area consumes feeds that are already records. This
one consumes a mailbox. The brief is explicit that a clean document set proves
nothing, because *the entire difficulty here is that the input is
unstructured* — so the corpus generates the mess as well as the truth: text
PDFs, scanned faxes with no text layer, Word forms that are sometimes tables,
spreadsheets whose form starts three rows down, and the structured portal feed
the requirement asks the system to stay compatible with.

**Decisions.**

1. **One record shape for every format, including the electronic one.** The
   portal feed is *another reader producing the same passages*, not a second
   pipeline. That is what makes "the electronic feed drops in without rework" a
   thing demonstrated by shared code rather than asserted in a design note: one
   extractor serves the fax and the portal, so a change to date parsing cannot
   fix one and break the other.

2. **Per-field provenance, and a locator an analyst can put a finger on.**
   Every value carries the passage it was read from, where in the document that
   was ("page 1 (scanned)", "PANS!A5", "table 2 row 3"), the method, and a
   confidence *earned* by that method — 1.0 for a spreadsheet cell, 0.97 for a
   PDF text layer, whatever tesseract reports (floored and capped) for OCR. A
   character offset satisfies a schema and helps nobody.

3. **Non-resolution is a finding, not a failure**, in both directions: a form
   naming a hull nothing holds, and a hull berthing with no form. There is no
   fuzzy name matching — normalisation recovers what is lossless (prefixes,
   punctuation, a dropped space) and a transposition stays unresolved, because
   edit distance would resolve "GRANITE TRUIMPH" and would equally resolve
   "GRANITE TRIUMPH II", a different ship.

4. **Three-valued rules.** `contradiction` / `ok` / `not_checkable`, as in
   ADR-035. A fifth of real forms omit a field and a fax loses two more to OCR;
   folding "the form did not say" into "the form was fine" reports a clean inbox
   nobody read.

5. **On declared cargo, the general case is not built.** A bulker declaring
   cement and riding high could be in ballast, part-laden, or lying, and motion
   alone does not separate those. What *is* checked is the one case that is
   arithmetic rather than inference: a hull declaring "no cargo" while
   broadcasting a laden draught. Approximating the rest would fire on honest
   ballast voyages, which is the alert-fatigue failure ADR-004 exists to
   prevent.

**Eleven defects, all of which presented as silence.** The unit suite was green
and the extraction stage reported 3,107 fields read from 292 documents at 10.6
of 11 per document. The alert counts were what exposed the area: 24
`notification_unmatched` against 1 authored, 10 `arrival_without_notification`
against 1, and 8 `paperwork_contradiction` against 2.

* **The filing time came from the filesystem.** `received_at` was the
  attachment's mtime — when the generator wrote the file, and in the real case
  when somebody scanned or forwarded it. Every paperwork rule measures a
  declaration against the track *as at the filing time*, so with a timestamp a
  month after the corpus window, `check_last_port` looked before a window that
  had not happened and `check_arrival_window` found no arrival after filing.
  Both returned `not_checkable` for all 292 documents. **Two of the three checks
  were dead corpus-wide and nothing said so** — the eight contradictions that
  did fire were all the third check. The filing date is now written into the
  document and *read like every other field*, with the mtime kept as a labelled
  fallback (`received_at_source`) because a value inferred from the filesystem
  and one read off the page are not the same evidence.

* **The resolver read one identity table.** `gfw_vessel_identity` carries an IMO
  for 135 of 502 hulls, while `ais_voyage` carries one for every hull that
  broadcast a message 5. A notification declaring IMO 1001661 for SOUTHERN
  TRADER matched nothing, dropped to the name rung, met a transposed name, and
  was reported as a form naming a ship we cannot identify — when the hull had
  been broadcasting that exact IMO all month. A gap in one table was being
  reported as a gap in somebody's paperwork. The registry is now the union of
  every identity source, merged most-trusted-first and fill-only.

* **The background corpus contradicted itself by accident.** Cargo was drawn
  uniformly from eleven values, one of which is "Ballast — no cargo", against a
  draught drawn independently — so roughly one form in thirty declared no cargo
  on a hull broadcasting a laden draught. The rule reported the contradiction it
  was handed, correctly, eight times, about vessels nobody wrote and no analyst
  could be told anything true about. The background now declares only what its
  draught supports, and the contradiction is authored as **P8** so that it can
  be counted.

* **Arrivals were judged against a window predating the record.** A vessel
  berthing on day two of the corpus was due to file on day minus two. Six of
  the ten `arrival_without_notification` alerts were hulls that berthed in the
  opening seventy-two hours, and this edge exists on every live feed's first
  days too. An arrival whose filing window falls before the record begins is now
  `not_checkable`, as is a stop the port-call detector recorded with no port
  name — pre-arrival notification is a duty owed on arrival at a *port*, and
  demanding paperwork for an unnamed offshore anchorage turned storage tankers
  into paperwork alerts.

Fixing the filing time brought the two strongest checks back to life, and they
promptly produced **33** contradictions where 3 were authored. Both were the
same mistake in mirror image: **the rules joined a declaration to the wrong
event.**

* **The arrival window matched by time, not by port.** `observed_arrival` was
  the first port call at or after filing. But a notification is filed 24-96
  hours ahead and a coastal vessel frequently makes another call inside that
  window, so the rule compared one voyage's estimate against a different
  voyage's berthing. It produced 30 contradictions, every single one of the form
  "berthed 31-65 hours *early*" — a distribution with no honest reading. A
  notification is now matched to the first recorded call **at the port the form
  names**; a port she never reaches yields no arrival to measure against, which
  is `not_checkable`, because a diverted voyage is not a false declaration about
  its ETA.

* **The last port compared against a gazetteer pin, then against the latest
  call.** The gazetteer holds one coordinate for a port area tens of kilometres
  across while the port-call detector records a berthing wherever she actually
  stopped — up to 150 km away in this corpus — so a hull was accused of lying
  about Karachi when her recorded call *was* Karachi, 144 km from the pin.
  Comparing against her most recent prior call instead was the same error again:
  a vessel that sailed from Mundra and then made an unnamed offshore stop has
  still sailed from Mundra. The question is whether **any** call before filing
  is at the port she names; the nearest-approach test remains as the fallback
  for a hull with no recorded calls at all.

* **The two halves of one gap both fired.** A form that resolves to no hull is
  reported as `notification_unmatched`; the arrival it names was then *also*
  reported as `arrival_without_notification`. That put a second alert on P6 —
  the decoy whose entire point is that the finding is "we cannot identify this
  form" and never a suspicion about the hull. A form nobody could match is still
  a form somebody filed, so an arrival an unresolved notification already names
  is no longer counted again.

* **Three decoys declared last ports their own voyages contradicted.** P5, P6
  and P7 were written with plausible-sounding origins — Mormugao, Sikka, Kandla
  — that sit 113, 170 and 440 km from where the corpus actually sails them. A
  decoy exists to prove the rule stays quiet on an honest form, so a decoy that
  fires is worse than no decoy at all. P2's declared Kochi was 500 km from her
  track too, which meant the authored arrival-window scenario was firing on the
  last-port check and naming the wrong finding.

A further defect fell out of the tests rather than the corpus: the OCR confusion
table folded `|` to `I` but left `L` alone, so "Vesse| Name" squashed to
VESSEINAME while "Vessel Name" squashed to VESSELNAME. `I`, `l`, `1` and `|` are
one shape to a scanner and the fold has to reach a single representative, or it
fails on the most common label in the corpus on exactly the format it exists
for. Folding `L` is aggressive, so `tests/test_pans.py` asserts no two fields'
label sets collide under the table — attaching a value to the wrong field is
worse than failing to read it.

Two more surfaced only when the full suite ran, and both are about the corpus
rather than the rules:

* **Adding one hull to the cast re-rolled the whole background fleet.** The cast
  tuples are ordered so that an addition never renumbers an existing hull, and
  the comments in `cast.py` say so. But minting still *draws* from `world.rng`,
  and every scenario drawing after `build_vessels` then gets different numbers.
  One extra Suezmax moved the vessel-type model's coarse accuracy from above its
  75% floor to 65% — not because the hull taught the model anything, but because
  its training data had been re-rolled behind it. Late cast additions now mint
  from a derived stream (`cast.LATE_ADDITIONS`), which is what the existing
  ordering discipline was already reaching for.

* **A synthetic document read by a real connector has to say both things.**
  `arrival_notification` is the first table landed by a connector rather than by
  the scenario writer, and the two facts collide: `pans-inbox` is honestly which
  connector produced the row — the whole design is that the connector really
  runs — while the corpus invariant is that no synthetic row may be mistaken for
  real. Forcing `source_id='synthetic-scenario'` would have made the connector
  lie about its own source. The row now carries
  `synthetic-scenario:pans-inbox`, the prefix convention `graph.store` already
  used for exactly this case, and the validator matches on the prefix rather
  than on equality.

**Consequences.**

* `arrival_notification` is a new conformed table, in `ALL_TABLES` and in
  `api.reader.CONFORMED_TABLES` (ADR-035's lesson: a table missing from that
  tuple makes `has()` answer False over a corpus that contains rows).
* `pans_inbox/` sits beside `conformed/`, not inside it: the documents are
  *inputs* with the standing of an unread attachment, not landed records.
* The generator holds `LADEN_DRAUGHT_M` and `BALLAST_PHRASES` independently of
  `anomaly.paperwork` rather than importing them. A corpus built from a rule's
  own thresholds cannot falsify that rule. Drift between the two is caught by a
  test, which is what makes independence safe rather than merely separate.
* OCR needs a binary pip cannot install. `reader_availability()` is printed
  before every run, because a pipeline that silently reads four formats of five
  reports a smaller inbox than arrived, and the missing fifth looks like nobody
  submitted rather than like nobody could read.
* No module under `ingest/pans/` or `anomaly/paperwork.py` reads
  `scenario_truth` (ADR-019); a test asserts it.
* Every figure here is measured on the **synthetic suite** (CLAUDE.md §4.6).
  Real-feed precision will be lower and must be re-measured on the deploy host,
  which does not exist yet.

---

## ADR-037 — Automating the electro-optical loop, and the flattery in a simulated sensor *(Accepted)*
**Section 3, Area 5 of the IDEX Challenge 82 brief.**

**Context.** The requirement is precise about what is wrong:

> The cameras at the radar stations are operated manually by watchkeepers.
> There is no automatic capture of an image against a radar or AIS track, no
> classification of type and identity against a library, no tagging of the
> image to the track, and no alert when what the camera sees disagrees with
> what the track claims.

Four things are asked for and **only one of them needs pictures**. Capture
without operator intervention, bind the image to a track, classify against a
library, alert on the disagreement: three are fusion and control logic. The
brief says so outright and adds the instruction that decides the shape of the
work — *"image classification is the commodity part of this problem, the part
any competitor can also do. So build the loop, and treat the classifier as a
replaceable component behind an interface."*

**Decisions — six parts.**

**(a) Cueing is a global assignment per slot, not a ranked list.** The obvious
implementation sorts tracks by suspicion and gives each its best camera. That is
**greedy per-target matching**, which CLAUDE.md §6 bans one domain along in the
association core, and it fails here for the identical reason: the three most
suspicious contacts are frequently inside one station's arc, so a greedy pass
hands that station's camera to all three, breaks the tie arbitrarily, and leaves
fifteen cameras idle. Each slot is solved with `linear_sum_assignment` over
cameras × candidates, the same tool `fusion/associate.py` uses.

**(b) The priority is three terms with stated weights, and the second is what
makes it work.** `0.55 x suspicion + 0.30 x information gain + 0.15 x staleness`,
multiplied by the expected image quality.

*Suspicion* dominates because that is the requirement's own framing.
*Information gain* — what a photograph would actually resolve — is a close second
and is the term a naive build omits: without it the network spends every slot
re-photographing the top of the ranked list and never looks at the other fifty
tracks. A contact nobody can name scores 1.00; a hull whose declared identity no
image has checked, 0.55; a hull an image already agreed with, 0.10; and a hull an
image already *disagreed* with, 0.85 — which is what sends the camera back for
the corroborating second look part (f) requires. *Staleness* is smallest because
"we have not looked at her lately" is not by itself a reason to look.

`PRIORITY_FLOOR = 0.30` is arithmetic rather than tuning: it is the priority of
an ordinary unsuspected hull whose identity no image has ever checked
(`0.30 x 0.55 + 0.15 x 1.0 = 0.315`). The rule reads *"a hull nobody suspects is
worth exactly one look, and after that she has to earn the next one."*

**(c) A closing observation window multiplies the cost, never the priority.**
Being about to leave does not make a ship more suspicious; it makes deferring her
more expensive. That distinction is the whole difference between a schedule and a
sorted list, and it is where ADR-032's forward projection finally earns its keep.
That ADR measured dead reckoning and found it *useless* as a suspicion signal —
every vessel departs from its own projection, because a coastal voyage is mostly
turns — while recording that the projection is good for stating an expectation.
"Will she still be inside Mumbai's arc in twenty minutes" tolerates the error
that "did she deviate from her predicted track" does not.

**(d) The deferral ledger is half the deliverable.** Every candidate that was
worth a camera and did not get one carries the reason: `outranked`, naming the
camera and the target that took it, or `no_camera_in_reach`, naming the nearest
station and the range. That is the suppression discipline ADR-028 set for the
radar cascade and ADR-031 extended to the ranked list, and it matters more for an
automation than for a queue — an operator who cannot find out why the system did
*not* look at something goes back to slewing the camera by hand. The ledger is
bounded per slot and the totals travel as counters, because an unbounded ledger
over a corpus-length campaign is larger than the corpus.

**(e) The classifier sits behind `ImageClassifier`, and the swap is
demonstrated rather than asserted.** Two implementations ship —
`PrototypeClassifier`, which uses every feature an image carries, and
`SilhouetteClassifier`, restricted to what an outline gives — and
`tests/test_area5.py` defines a **third inside the test file** and substitutes it
into the running loop. All three produce bound, landed captures; the verdicts
differ; nothing in the cueing, the tagging or the mismatch rule changes between
the runs. The pipeline prints the same comparison over the real captures.

There are no pixels. `eo/appearance.py` defines the six measurements a vision
model would extract from a photograph — length, slenderness, where the
superstructure sits, freeboard ratio, deck clutter, mast count — and the loop
consumes those rather than images. That is what makes the interface concrete: a
customer's model drops in by producing the same six numbers from real imagery.
Every capture row carries `capture_mode='simulated'`, an empty `image_ref`, and
the model's own `provenance` string, which says it has never seen an image.

**(f) The mismatch rule compares at the AIS ship-type family, and requires
corroboration.** Both halves were forced by measurement and neither was in the
first build. See the defects below.

**Six defects, four of which were visible only as numbers.**

* **The confidence did not track accuracy, so the model refused 84% of good
  images.** A hand-set softmax temperature had `PrototypeClassifier` picking the
  right fine class **96%** of the time while reporting a mean confidence of
  **0.35** — below its own 0.50 bar. The claim rate was 15-35%. A confidence that
  does not track the hit rate is decoration, and this project's entire thesis is
  that an operator can calibrate their trust against it. The temperature is now
  **fitted** so mean reported confidence equals measured accuracy under the
  capture's own conditions (ordinary temperature scaling), and the quality
  multiplier that used to be applied on top was removed as double-counting: the
  calibration samples are generated *at* that quality, so a poor image already
  produces a low number because the model is genuinely less often right on poor
  images.

* **The observation noise had no floors, so the model could tell a Suezmax from
  an Aframax.** Error falling to nearly nothing in a perfect image says a
  photograph measures "how cluttered is her deck" to three decimal places. It
  does not: these are soft perceptual quantities that a human analyst and a
  vision model both estimate to a broad band however good the picture is. With
  near-zero floors every prototype separated from every other and
  `measure_separability` reported an eleven-class vocabulary — exactly the *"long
  list of classes it guesses at"* the brief warns against, reached by flattering
  the sensor rather than by measuring it. `FEATURE_NOISE` now carries an
  irreducible floor per feature, tightest for length (a geometric measurement
  against a known range and a known aspect) and loosest for mast count (counting
  thin vertical structures against a bright sky).

* **The type-level merge was the wrong question, and 22 honest hulls in 1,500
  were accused.** `confusable_groups`, inherited from `tracks.vessel_type`, asks
  "can the model tell A from B" at 25%. That is the right question for
  *describing* a contact and the wrong one for *accusing* a named hull, and it
  does not even weight the errors the same way: calling a bulker a general cargo
  ship is a harmless slip inside one family, while calling her a product tanker
  is the difference between silence and an alert. Measured at a good daylight
  look, `product_tanker` was called `bulker` on 15% of samples and `bulker` was
  called `product_tanker` on 12% — both comfortably under the 25% bar, so both
  stayed separate labels, and the rule produced a ten-to-one false-positive rate
  against the two authored lies. A second merge pass now unions any pair mistaken
  across an **AIS ship-type family** boundary more than 5% of the time
  (`FAMILY_SEPARATION_THRESHOLD`), and a merged group that spans two families is
  published as `merchant`. False accusations fell to **0-0.36%** per look.

* **A merged label was read as bounding nothing, which silently discarded the
  brief's own headline example.** Under most conditions the model publishes
  `merchant` rather than `tanker`, and the first version returned "cannot check"
  whenever a label spanned two families. But `merchant` still rules out every
  family it does *not* contain, and a hull broadcasting that she is a fishing
  vessel while imaging as a merchant has plainly been contradicted — which is
  precisely *"a vessel declaring itself a fishing vessel that images as a
  tanker"*, the case the brief names. The comparison is now against the **set** of
  families a label leaves open, and a contradiction is a declared family that is
  not in it.

* **The rule read another model's label in the default model's vocabulary, and
  accused 36% of an honest fleet.** Swap in `SilhouetteClassifier`, whose
  vocabulary at a marginal look collapses to `small_craft` and `vessel`; the
  default model holds no such labels, fell through to a fixed table, read
  `small_craft` as meaning fishing and nothing else, and contradicted every
  merchant it saw. A label means what the model that emitted it meant by it, so
  the classifier now publishes its own family set on the verdict, it is landed on
  the capture row, and the rule never re-derives it. **This defect is why the
  swap test earns its place**: it was invisible with one classifier and immediate
  with two.

* **Sister ships are not separable in six numbers, and the identity radius
  pretended otherwise.** Two observations of the same hull sit 0.12 apart at the
  median and 0.18 at the ninetieth percentile; the *closest pair of different
  hulls* sits 0.11 apart. The distributions overlap and no radius separates them,
  because two Suezmaxes of the same dimensions genuinely do look the same. The
  radius is now the same-hull p90 and the work is done by the **margin**: an
  identification is offered when a hull is distinctive against what the library
  holds and refused when she is one of a class — the same refusal
  `ingest/pans/resolve.py` makes rather than fuzzy-matching a transposed name
  onto a different ship.

**Corroboration, and why it is the fix rather than a higher bar.** Even at 0.3%
per look, five hundred hulls photographed several times each is a steady trickle
of alerts about ships that have done nothing. Raising the confidence bar does not
help, because a wrong label and a right one look the same from inside and the bar
suppresses true positives just as fast. Two looks taken at different ranges,
aspects, light and visibility are close to independent, so agreeing errors are
rare in a way that agreeing truths are not — and the loop supplies the second
look for free through the `contradicted` information-gain term. They must agree
on the **family**: a hull called a tanker once and a trawler once has been
photographed badly twice, and counting that as corroboration would be counting
confusion as evidence.

**And a seventh defect, which was corroboration itself, stated as an absolute.**
"Two agreeing looks" is a claim about a *rate* — agreeing errors are one in a
hundred thousand **per pair of looks** — and it was enforced as a count. A
stationary background hull sat inside one station's arc for five days, collected
**130 classifiable looks** at a constant 6.8 km, and two of them landed on the
same wrong family. Two out of 130 is not one pair; it is 8,385 pairs, and at that
many draws the improbable is arithmetic. The rule accused a hull nothing had
authored as a liar, which voids the precision claim ADR-004 exists to protect.

It is fixed at both ends, because the two halves fail differently:

* **`anomaly/library.MIN_CONTRADICTED_SHARE = 0.5`.** The agreeing looks must
  also be a majority of the looks that decided anything either way
  (`not_checkable` excluded, so worse imaging cannot make the bar easier). The
  plain reading: if she images as what she declares more often than not, the
  camera has not contradicted her — it agreed 128 times and disagreed twice.
  0.5 sits far above the noise (2/130 = 0.015) and far below the authored liars,
  contradicted on two thirds to nine tenths of their decisive looks; anywhere in
  roughly 0.2–0.6 separates the two populations, so this is a boundary between
  measured distributions rather than a number tuned to a target count. This half
  holds however the images arrived.
* **`eo/cue.MAX_LOOKS_PER_VERDICT = 3`, made to bind.** Once three classifiable
  looks have settled a hull's declared type, her information-gain term drops to
  `INFO_SETTLED` (0.05) — a fourth photograph resolves nothing the first three
  did not. A hull nothing else has flagged then falls below `PRIORITY_FLOOR` and
  the camera goes elsewhere, which returns the wasted slots; a hull with real
  suspicion behind her stays in the order **on that suspicion**, which is the
  right reason to keep watching a ship. This half depends on the scheduler being
  the only source of captures, which is why it is not the only half.

The bound also had to be made to *work*: it lived in a dict local to each
`plan_cueing` call while the pipeline schedules a long window as consecutive
three-slot calls, so the counter reset at every boundary, never reached three,
and had **no effect at all** on a run of a thousand taskings. `CuePlan` now
carries the counts out and the caller feeds them back — and the caller counts on
what the image *returned*, not on the quality the scheduler expected of it,
because a look that came back too dim to read did not answer the question. A
memory that resets between calls is not memory, and a bound that cannot be
reached is not a bound; both were green in the unit suite.

**And five defects that presented as silence — the area's own headline finding
never fired on the corpus until all five were closed.** Removing the false
positive above left `imagery_type_mismatch` at **zero**, which is the worse bug.
Each of these hid the one behind it, and none was visible in a green unit suite:

* **The corpus held no lie.** `DECLARED_CLASS_OVERRIDES` is keyed by cast key
  (`eo_false_class`) so it reads beside `LATE_ADDITIONS`; both readers ask
  `declared_class_overrides.get(v.entity_id)`, which is `vessel:eo_false_class`.
  Merged un-keyed, every lookup missed, every authored liar broadcast the truth
  about herself, and the rule was **correct** to stay silent. This is the third
  time this area has been silenced by two id spaces for one hull — the identity
  index and the canonical node id above are the other two — which is why
  `build_vessels` now raises rather than skipping an override that names nobody,
  and why the property is checked at the cast in a unit test rather than after a
  twenty-minute pipeline run.
* **The camera cannot size a hull that only AIS reported.** Pixels-on-target is
  length over range, so a candidate with no length is refused as unobservable —
  the right refusal, since inventing a length invents the quality the whole
  priority model multiplies by. But only radar measures length, and 44% of
  candidate positions came from AIS tracks that carry none. O1 was a candidate
  **above the floor in ten consecutive slots with a camera free**, and was imaged
  in the two where the nearest fix happened to be the radar one; in the rest the
  camera declined to look at a 270 m tanker 5.5 km off Porbandar in clear
  daylight. Length is a property of the hull, and the two tracks are collapsed
  onto one subject precisely because they are one ship, so it is merged across
  them — measured, never read off her AIS static message, which would be taking
  the word of a hull we are in the middle of accusing of lying about herself.
* **The loop closed at the stage boundary rather than at the classifier's
  latency.** O1's first look contradicted her at 08:15; `verdict_state` advanced
  only when the stage ended, so for the rest of it the scheduler still believed
  her unverified, reset her staleness clock on the look it had just taken, and
  dropped her to 0.165 against a 0.30 floor. A stage is a planning convenience;
  a batch interval is a claim about how long it takes to read an image. Making
  them one number meant the second claim was never examined. Stages are now one
  slot, which is what a deployment does.
* **The urgency lookahead stopped at the plan's last slot**, so at one slot a
  stage every candidate was maximally urgent and the term stopped discriminating
  entirely — the faster loop would have silently traded it away. Whether anything
  can see her later is a fact about the sea, not about where this call stops.
  Fixed, and while fixing it the term was found to be charging the same premium
  whether or not anything was left to learn: it kept buying the camera for a hull
  leaving cover whom this pass had already photographed, while a hull nobody had
  ever imaged waited. It is now scaled by information gain. Urgency measures what
  is about to be lost, and an answer already in hand is not lost.
* **The pipeline reset the staleness clock at the stage boundary even for an
  unsettled contradiction**, reinstating at the seam the exact deadlock the
  withholding inside `plan_cueing` exists to break. The two rules have to state
  the same thing at both scales or the boundary quietly undoes the fix — the same
  shape of defect as the look counter resetting between calls.

**Capacity: the floor is an opportunity cost, not a quality bar.** 115,028
camera-slots were spent on nothing while 46,527 reachable targets were refused
for scoring under 0.30, and the marginal images are exactly where this area's
hulls live. Below-floor targets now stay in the same global assignment charged
`BELOW_FLOOR_PENALTY`, larger than any value spread and therefore lexicographic:
they take only a camera **no** above-floor target could have used. Still one
assignment, not a greedy second pass over the leftovers — CLAUDE.md §6 does not
stop applying because the targets are cheap. They are marked `opportunistic` on
the tasking and counted separately, so a fill cannot inflate the utilisation
figure that is meant to measure demand. This is safe only because of the share
rule: under a bare count of two, more looks meant more chances to accuse an
honest hull; under a share, the denominator grows with them and an extra look
makes an accusation *harder*.

**Utilisation is 4.5% and that is honest.** 89,480 deferrals are
`no_camera_in_reach` — a 20 km lens against the Arabian Sea. The idle slots are a
statement about coverage, not a scheduling defect, and the fix for them is a
different sensor, which is the same answer O4 already gives.

**No threshold was moved to make a count come out right.**
`MIN_MISMATCH_QUALITY` (0.45), `MIN_MISMATCH_CONFIDENCE` (0.62),
`PRIORITY_FLOOR` (0.30), `MIN_CLASSIFY_QUALITY` (0.35) and
`MIN_CORROBORATING_CAPTURES` (2) are unchanged from the build that reported zero.

**Where the corpus now stands (synthetic suite, seed 7).** O1 and O2 fire, each
on two decisive looks that agree, `contradicted_share` 1.0. O3 — the decoy that
declares bulker and is general cargo, both dry cargo under the AIS standard — is
photographed clearly and reports `ok`. O4 is never captured at all: 150 km
offshore, every slot defers her with the range to the nearest station attached.
O5 collects sixteen thermal looks that all decline, which is the capability
boundary she was authored to state — a silhouette carries no deck. **Two alerts,
both authored, nothing else.**

**What the simulation makes easier than reality, stated rather than buried.**
The simulated camera has a 100% presence-detection rate: slewed onto a
sea-clutter track it reports empty water, every time. Resolving a false radar
track is a genuine and valuable thing a camera does — clutter is the dominant
false-positive source in the whole radar picture — but a real head in a real
swell misses targets, and a rule built on "the camera saw nothing" would be
calibrated against a false-negative rate this project does not have. The empty
frame is therefore recorded on the capture, counted, and **deliberately not
promoted to an alert**. Similarly, a bearing the simulator holds no model for
returns *no capture at all* rather than an empty frame: a gap in the simulation
is not an observation about the sea (ADR-021).

**Consequences.**

* `eo_capture` is a new conformed table, in `ALL_TABLES` and in
  `api.reader.CONFORMED_TABLES` — the lesson ADR-035 and ADR-036 both paid for.
  `scenario_eo_appearance` is deliberately **absent** from the reader: it is the
  camera simulator's model of what is physically out there, the stand-in for the
  photons a lens would collect, and only the world generator may read it. A test
  asserts that no module under `eo/` or `anomaly/imagery.py` names it.
* `eo_capture` is a new node type, with `depicts` and `captured-by` edges. Its
  own type for the same reason `notification` has one (ADR-036): a photograph is
  an artifact, not a ship. `depicts` reaches a `contact` as well as a `vessel`,
  because a camera slewed onto a track nobody can name still photographed
  something, and that capture is the strongest single thing the system can offer
  about her.
* **`fusion/` is untouched.** The brief's standing caution is that an area
  needing a change to the fusion core has found a defect in the core. Area 5 did
  not: the loop reads built tracks and graph alerts and writes its own table. One
  thing was *wanted* and not taken — `fusion/contact_profile.py` is the natural
  home for an imaged type on a dark contact, and it sits inside `fusion/` despite
  profiling rather than fusing. Recorded as an OPEN QUESTION in STATE.md rather
  than patched around.
* The ranked list gains its first `imagery` factor, so five of the six evidence
  families in `assistant/catalog.py` are now filled and only `radio` (Area 6) is
  declared and empty. `cue_eo_camera` moves from "not built" to **"partly
  built"**, with the halves named separately: the decision, the tagging, the
  library and the rule run; the camera does not exist.
* Only **one** imagery factor kind is registered, and two were considered and
  rejected. A camera that *recognises* a previously-imaged hull is an
  identification, not a suspicion — it raises the value of a finding that already
  exists and asserts nothing on its own, so it travels as evidence, the posture
  ADR-026 took for imaging opportunity and ADR-032 for forward projection. A
  camera that finds empty water is the other, for the reason above.
* **A mismatch is only provable close in.** The head is useful against a merchant
  to about 20 km; the image quality needed to contradict a declared identity is
  only reached inside about 8 km in this coast's monsoon visibility. The gap
  between "can see her" and "can prove something about her" is recorded rather
  than tuned away, and it is why the Area 5 hulls are authored on close coastal
  passes and why O4 — a genuine liar 150 km offshore — is a stated capability
  boundary rather than a miss.
* The corpus now contains hulls whose AIS-declared type is a lie, which means the
  vessel-type model trains on mislabelled examples. That is realistic — any
  training set built from AIS labels contains liars — and at four hulls in two
  hundred it is inside the noise; it is named here so a future accuracy movement
  is not attributed to the wrong cause.
* Every figure in this ADR is measured on the **synthetic suite**, and most of it
  on prototype descriptors rather than on the corpus. There is no camera, no
  image has ever been examined, and real-feed precision must be re-measured on a
  deploy host that does not exist (CLAUDE.md §4.6, §5).

---

## ADR-038 — One operator surface, and naming who says so *(Accepted)*

**Context.** The product had grown three top-level views that a user, opening it
cold, described as "seemingly doing the same things": **Assistant** (every
subject ranked, with the score decomposed, narration and recommended actions),
**Findings** (a narrower table restricted to what the real landed corpus
supports, attribution-first), and **Alerts** (the raw detector queue, and the
only place any of it could be acted on).

They were not identical. But the overlap was most of each, and the split cost
more than it bought:

* The assistant was already a superset by coverage. Nearly anything in the other
  two also appeared there.
* Two capabilities were unique and both were buried. Only Alerts could record a
  disposition. Only Findings held the real-versus-synthetic line.
* An officer investigating a hull had to visit two screens to see her ranked
  score and her detections, and a third to act on either.

**Decisions.**

**(a) One tab, two lenses over the same facts.** `Watch` replaces all three.
*By vessel* is one row per hull, ranked, with every detection about her gathered
underneath. *By event* is a chronological queue, newest first, one card per
detection. Neither is a subset of the other and neither is a filter of the
other: they are two orderings of one dataset, because an officer investigates a
*ship* and works a *watch*, and those are different verbs. Four alerts on one
hull are one investigation and four items on a queue at the same time.

**(b) The disposition controls live on the alert, in both lenses.** The
capability that was unique to the old Alerts tab is now available wherever an
alert is shown. Recording a decision never requires changing screen, which was
the single strongest argument the old split had against it.

**(c) The real-versus-synthetic *boundary* goes; the real-versus-synthetic
*label* stays.** By operator instruction there is no filter, no separate
section and no gate. Every row still carries its `SCENARIO` tag. These are not
the same thing: a boundary makes a person navigate to see everything, a label
makes them able to tell what they are looking at. Dropping the tag as well would
put a generated figure in front of somebody with nothing to say it was
generated, which is the one error CLAUDE.md §4.6 and ADR-019 treat as
unrecoverable.

**(d) Provenance gets a reader-facing half: `origin` and `derivation`.** The
envelope on every record (CLAUDE.md §4.1) carries `source_id` and `source_ref`,
and those are correct as identifiers. What was wrong was rendering them raw. The
assistant printed `source graph / events` beneath an accusation, which names a
SQLite table and a column *inside this repository*. An operator asked to trust a
flag cannot audit that: there is nobody to ring and no record to pull. Against a
product whose entire proposition is traceable trust, citing a folder on our own
laptop is worse than saying nothing.

Two fields, deliberately separate because they answer different questions:

* **`origin`** is the body, register or feed the underlying facts came from. The
  thing an operator could go and check independently.
* **`derivation`** is what this system then did to those facts. Empty when a
  record was landed as-is; populated whenever we computed, compared, joined or
  inferred.

The split is the same one ADR-017 and ADR-018 already draw: a dark-vessel
assessment is GFW's finding carried through, a designation is OFAC's decision,
and the identity match between their record and our hull is **ours**.
`derivation` is where that last sentence lives. A derived claim carried under
its source's name is the source asserting something it never said.

Attribution is attached at **serialisation**, in `Evidence.as_dict`, not at the
dozen construction sites. "Every evidence item names a source somebody could
check" is then a guarantee rather than a convention twelve callers must
remember. Our own rule modules are named as ours (`identity_rules` reads
"Maritime ISR identity rules") rather than hidden behind a module path, and a
source id with no entry is humanised rather than dressed up: an unattributed
record says "not attributed", because a plausible-looking source name for a
record we cannot attribute would be a fabrication.

**Consequences and the defects this surfaced.**

* **Three tests asserted on the exact casing of a note** rather than on the fact
  that a note is made, so a copy edit failed them for a reason unrelated to the
  behaviour under test. They compare case-insensitively now.
* **`EvidenceHop` had no `origin` field**, so Pydantic silently dropped the
  attribution and alert cards kept printing the raw module id it was written to
  replace. A response model that omits a field does not error, it deletes: the
  same class of silent loss as an unregistered conformed table.
* **A failed `/tracks` request was swallowed**, and the map then printed "no AIS
  tracks in this window" — a claim about the sea made on the strength of a
  request that never returned. Loading, failed and genuinely empty are three
  different sentences now.
* **Every operational layer on the map was gated on MapLibre's `load`**, which
  does not fire until the map has rendered its sources. An unreachable tile host
  therefore did not degrade the picture, it deleted it: no vessels, no alerts,
  no area of interest, on a screen whose whole job is showing where things are.
  None of those marks need a basemap. They are gated on `style.load`.
* The old paths (`/assistant`, `/findings`, `/alerts`) redirect rather than 404.
  Somebody has a bookmark or a link in a handover note, and a dead URL in an
  operations tool reads as the tool being broken.

**What this ADR does not decide.** The interface's *wording* was rewritten at
the same time and is not a decision worth an ADR: it is house style, recorded in
the header comment of `WatchView.jsx`. One rule from that pass was reverted after
review and is worth stating so it is not reintroduced: **casing is left as the
data and the existing screens had it.** Forcing a capital on every enum was a
change nobody asked for.
