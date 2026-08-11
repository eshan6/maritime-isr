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
