# Phase 0 — per-unit commit plan

Commit in this order (each maps to one execution-spec unit):

## 0.0 — repo skeleton + schemas
```
git add pyproject.toml .gitignore .env.example README.md \
        maritime_isr/__init__.py maritime_isr/config.py maritime_isr/h3util.py \
        maritime_isr/store.py maritime_isr/db.py maritime_isr/writer.py \
        maritime_isr/writer_detections.py maritime_isr/cli.py \
        maritime_isr/schemas/ tests/
git commit -m "0.0: repo skeleton, canonical schemas, provenance envelope, H3 helper, config, storage layer"
```

## 0.1 — Copernicus S1 connector
```
git add maritime_isr/ingest/__init__.py maritime_isr/ingest/copernicus.py
git commit -m "0.1: Copernicus Sentinel-1 GRD connector (STAC/OData + resumable idempotent R2 downloader)"
```

## 0.2 — SNAP preprocessing (install stub only in Phase 0)
```
git add maritime_isr/infra/install_snap.sh
git commit -m "0.2: SNAP install script stub (chain implemented in 0.2 session)"
```

## 0.3 — aisstream live connector + service
```
git add maritime_isr/ingest/aisstream.py maritime_isr/infra/aisstream.service
git commit -m "0.3: aisstream.io live AIS consumer + systemd service"
```

## 0.4 — historical AIS + GFW + registries
```
git add maritime_isr/ingest/noaa_ais.py maritime_isr/ingest/gfw.py \
        maritime_isr/ingest/registries.py maritime_isr/infra/mirror_cron.py \
        maritime_isr/infra/crontab.example
git commit -m "0.4: NOAA historical AIS, GFW SAR detections, versioned registries, R2 mirror cron"
```

## 0.5 — inspection dashboard
```
git add maritime_isr/inspect/
git commit -m "0.5: inspection dashboard v0 (AOI frame, AIS tracks, S1 footprints)"
```

---

# Unit 0.2 — SNAP preprocessing chain (implemented)

```
git add maritime_isr/infra/install_snap.sh \
        maritime_isr/process/s1_preprocess.py \
        maritime_isr/process/validate_sigma0.py \
        maritime_isr/process/snap_doctor.py \
        maritime_isr/cli.py tests/test_preprocess.py
git commit -m "0.2: SNAP preprocessing chain (pyroSAR gpt), sigma0 validator, doctor, memory-capped install"
```

---

# Scenario corpus (ADR-019)

```
git add maritime_isr/scenario/ tools/corpus_profile.py \
        tools/run_scenario_pipeline.py tests/test_scenario.py \
        maritime_isr/ingest/landing.py maritime_isr/graph/store.py \
        maritime_isr/graph/from_landed.py maritime_isr/graph/identity.py \
        maritime_isr/cli.py DECISIONS.md STATE.md COMMITS.md
git commit -m "scenario: synthetic corpus in the real tables, flagged is_synthetic (ADR-019)"
```

Run order on a machine that holds the real data:

```
python tools/corpus_profile.py                      # measure the real corpus
python -m maritime_isr.cli scenario generate --seed 7
python -m maritime_isr.cli scenario status
python tools/run_scenario_pipeline.py               # pipeline + measurement
python -m maritime_isr.cli scenario clear           # remove every synthetic row
```

---

# Real-corpus repairs (ADR-020, ADR-021)

Four units, landed after the corpus profile came back from the laptop and the
generator was aligned to it. Each was found by measuring, not by reading code.

```
git commit -m "ingest: a port visit's span is not its dwell (ADR-020)"
git commit -m "data: correct the ADR-020 diagnosis, de-bias the duration, gate the demo"
git commit -m "ingest: ADR-020 resolved from the raw payloads — the data was fine"
git commit -m "fix: three zeros that were breakage, not measurement (ADR-021)"
git commit -m "ingest: render the port name an operator can read"
```

Run order on a machine that holds the real data. Everything here is offline —
`rebuild_conformed` reads `data/raw/`, so ADR-013 holds and the corpus window
does not move.

```
python tools/data_health.py                     # the demo gate; exits 1 on a blocker
python tools/restamp_h3.py --dry-run            # must report 0 cells added
python tools/rebuild_conformed.py --dry-run     # audit; watch `orphans`, must be 0
python tools/rebuild_conformed.py               # back up data/conformed first
python tools/corpus_profile.py                  # refresh the profile, now de-biased
python tools/port_visit_forensics.py            # raw only; the ADR-020 investigation
```

**Host-verified 2026-07-31**, all of the above run on the operator's laptop
against the real corpus: 81,516 H3 cells added with 0 corrected; `orphans: 0`
across all four event kinds; GFW confidence recovered from 0% to 100% on 3,000
port visits; `visit_port_name` from 54.4% to 100%; 5 of 5 AIS gaps flagged by
GFW as intentional disabling, where this repo had recorded zero.

---

# One canonical vessel key (ADR-022)

Single-focus session. Diagnose, fix structurally, guard with an exercise test,
re-measure without tuning.

```
git add maritime_isr/schemas/keys.py maritime_isr/schemas/__init__.py \
        maritime_isr/graph/from_landed.py maritime_isr/graph/identity.py \
        maritime_isr/graph/store.py maritime_isr/scenario/measure.py \
        tests/test_vessel_keyspace.py tests/test_graph_from_landed.py \
        DECISIONS.md STATE.md COMMITS.md
git commit -m "graph: one canonical vessel key, published by the side that owns it (ADR-022)"
```

Verification, in this order:

```
python -m pytest tests/test_vessel_keyspace.py -q   # the exercise test
python -m pytest tests/ -q                          # 446 green
python -m maritime_isr.cli scenario generate --seed 7
python tools/run_scenario_pipeline.py               # recall must be UNCHANGED
```

**Recall is expected to stay at 14%.** It did — 3 of 22 before and after,
precision 100% both times, 0 false positives across 16 decoys both times. The
keyspace defect governed what an alert *connected to*, not whether it was
raised, and reporting that plainly was the point of the session. What changed:
MMSI-to-hull resolution went from **0 of 103** to **102 of 103**, and an alert
now reaches a hull with a median of 4 edges instead of a provisional stub with 1.

---

## demo: show the data we already have — findings table, UN+EU, density, SAR contacts (ADR-024)

One logical unit: stop discarding landed data at the serving layer.

```
git add maritime_isr/api/{app,models,service}.py \
        maritime_isr/ingest/sanctions_match.py maritime_isr/cli.py \
        maritime_isr/scenario/land.py maritime_isr/scenario/scenarios/common.py \
        frontend/src/views/FindingsView.jsx frontend/src/views/MapView.jsx \
        frontend/src/App.jsx frontend/src/api.js frontend/dist \
        tests/test_sanctions_match.py tests/test_api_exercise.py \
        DECISIONS.md STATE.md COMMITS.md
git commit -m "demo: a ranked findings table, three sanctions registries, and the whole corpus on the map"
```

Verification, in this order:

```
python -m pytest tests/test_sanctions_match.py -q    # 47 green (16 new)
python -m pytest tests/test_api_exercise.py -q       # 28 green (15 new)
python -m pytest tests/ -q                           # 480 green
cd frontend && npm run build                         # dist/ rebuilt
python -m maritime_isr.api                           # open /findings and /
```

**On the laptop, additionally — the matcher must be re-run.**
`sanctioned_vessel_matches` gains `registry`, `listed_entity_type` and the
`vessel_*` fields, and `registry` joins the natural key:

```
python -m maritime_isr.cli ingest registries          # refresh UN + EU
python -m maritime_isr.cli ingest sanctions-match     # all three registries
```

*The number to look for:* how many findings UN and EU add beyond OFAC's 126.
**Zero is a reportable result** — those lists name far fewer vessels than OFAC.
`--registries OFAC` reproduces the pre-ADR-024 behaviour for comparison.

Scenario generation is unchanged in geometry — the only new column on a
scenario row is `listed_entity_type`, so the corpus is otherwise byte-identical
and the determinism test stays green.

---

## demo: the one-click incident report, and the identity_changed events (ADR-025)

Two units, committed together because the second is small and both close items
this file already listed as open.

```
git add maritime_isr/api/{app,report,service}.py \
        maritime_isr/graph/{store,from_landed}.py \
        frontend/src/api.js frontend/src/components/{bits,VesselPanel}.jsx \
        frontend/src/views/FindingsView.jsx frontend/dist \
        tests/test_api_exercise.py tests/test_graph_from_landed.py \
        DECISIONS.md STATE.md COMMITS.md
git commit -m "demo: the one-click incident report, and the identity-change events nothing was writing"
```

Verification, in this order:

```
python -m pytest tests/test_graph_from_landed.py -q   # 28 green (11 new)
python -m pytest tests/test_api_exercise.py -q        # 43 green (12 new)
python -m pytest tests/ -q                            # 525 green
cd frontend && npm run build
rm -f data/graph.sqlite && python tools/run_scenario_pipeline.py
```

**The numbers to check after the pipeline run:**

```
identity_then_anomaly   1 alert   (was 0)
recall                  5%        (UNCHANGED — and that is the finding)
false positives         0         (must stay 0)
```

Recall not moving is expected and is recorded in ADR-025: the new alert lands
on B5, which was already detected. The rule is composite and the five scenarios
that declare it still miss because they have no companion dark alert — both
dark rules are structurally silent under ADR-005.

**A number worth eyeballing on the laptop:** the count of `identity_changed`
events. 12 across 5 hulls here; on 9,184 real vessels it will be far larger,
but a figure near the fleet size would mean the supersession rule has regressed
to counting interval *closure* — the 100%-closed trap, and the one failure mode
this change had to avoid.

---

## docs: bring the documents up to what was actually built

No code. Four documents had drifted from the tree, and one of them was asserting
a UI feature that does not exist.

```
git add README.md STATE.md ARCHITECTURE.md GLOSSARY.md COMMITS.md
git commit -m "docs: bring README, STATE, ARCHITECTURE and GLOSSARY up to ADR-024/025"
```

**The one worth knowing about:** README claimed scenario rows "carry a
`SCENARIO` badge and a distinct violet treatment everywhere they appear."
`SyntheticBadge` returns `null` and has done for some time, so on screen a
generated hull renders exactly like a real one. Corrected to describe what is
rendered, and raised as **OPEN QUESTION #10** rather than fixed — the no-op is
deliberate and reversing it is Eshan's call, made sharper by the fact that the
new incident report *does* label a scenario vessel.

Nothing to verify beyond reading, but the claims were checked against the tree
rather than against memory:

```
grep -n "SyntheticBadge" -A 4 frontend/src/components/bits.jsx   # returns null
grep -rn "<SyntheticBadge" frontend/src/                          # one call site
```

---

## feat: was anyone watching? — imaging opportunities over AIS gaps (ADR-026)

The first analytical claim this system makes on its own behalf. For every AIS
gap GFW flagged as intentional disabling, work out where the vessel could
physically have been at the moment of each Sentinel-1 pass during the silence,
and compare that area against the scene footprint.

```
git add maritime_isr/overpass.py maritime_isr/cli.py maritime_isr/api/ \
        tests/test_overpass.py tests/test_overpass_e2e.py \
        DECISIONS.md STATE.md README.md COMMITS.md
git commit -m "feat: satellite imaging opportunities over AIS gaps (ADR-026)"
```

**Needs no pixels and downloads nothing.** Both inputs were already on disk: the
636 Sentinel-1 catalogue records landed 2026-07-29 (footprint polygon +
acquisition time) and the gap events. ADR-013's 1 GB cap is untouched.

**What made it possible** was in `ingest/gfw_events.py` and not in any document:
gap rows carry `gap_off_lat/lon` and `gap_on_lat/lon` alongside both timestamps.
A known start, a known end and an elapsed time is a solvable problem.

**The finding that reframed the build.** Run against a realistic fixture — three
gaps of 3 h / 9 h / 28 h, fourteen scenes on a ~11.7 h repeat with ~250 km
footprints — it returned **0 confirmed, 3 partial, 1 unwatched**. At 20 kn the
area a vessel could occupy passes one Sentinel-1 footprint about **four hours**
into a gap, so `partial` is the ordinary outcome and `confirmed` needs a short
gap or a pass near one of its ends. The scene shopping list was rewritten
mid-build to draw from partial rows too; printing only confirmed ones would
report an empty list on most real corpora while useful scenes sat in the table.

Verify:

```
python -m pytest tests/test_overpass.py tests/test_overpass_e2e.py -q   # 49 passed
python -m pytest -q                                                     # 573 passed, 4 skipped, 1 pre-existing failure
maritime-isr overpass                                                   # on the laptop
```

*Success on the laptop:* a tier breakdown and, per opportunity, a scene id with
a coverage percentage. **Zero confirmed is a normal result** — read the geometry
note the command prints before treating it as a fault. A run that prints "no
Sentinel-1 scenes in the catalog" means the scene catalogue is not landed where
the command is looking, not that no satellite passed.

**One pre-existing failure surfaced while verifying this**, and it is not from
this change — `test_alerts_carry_evidence_chains` fails identically on a clean
tree with these changes stashed. Phase 3 `dark_vessel` alerts carry a
`detection:` subject where every consumer expects `vessel:`. It only appears
once both the scenario pipeline and the Phase 1–6 fixture chain have populated
the graph, which needs three undocumented generator steps. Recorded in STATE.md
and left for a decision — the fix touches ADR-022's canonical-key rule and does
not belong in an unrelated commit.

---

## fix: the timeline player vanished, and the graph opened empty

Two loading defects reported from the laptop demo. Both about *when* data
arrives rather than whether it exists.

```
git add maritime_isr/api/ maritime_isr/graph/store.py frontend/src frontend/dist \
        tests/test_map_graph_loading.py STATE.md COMMITS.md
git commit -m "fix: the timeline player vanished, and the graph opened empty"
```

**The scrubber** took its window from `/stats`, requested eighth of eight on
mount. Browsers open ~6 connections per origin, so it queued behind `/tracks` —
measured 3.06s, 40x the next slowest call — and the control rendered only once
its window existed, so it was *absent* for those seconds and absent again after
every navigation (React Router unmounts the view). Fixed by requesting the
window first, session-caching it, keeping the scrubber mounted-but-disabled
while waiting, and adding a cheap `/api/corpus-window`. The ordering was the
fix; the cheaper endpoint was the smallest part, contrary to first diagnosis.

**The graph** required picking from a dropdown of thousands where most picks
give a lone node (GFW ownership covers ~1.3% of hulls here). New
`/api/graph/seeds` ranks by edge degree and the view auto-seeds on the best.
The panel states what that claim is: best-connected, not most suspicious.

Verify — the browser half is the half that matters:

```
python -m pytest tests/test_map_graph_loading.py -q     # 10 passed
python -m pytest -q          # 583 passed, 4 skipped, 1 pre-existing failure
python -m maritime_isr.api   # open /, then switch to Findings and back
```

*Success:* the player is on screen with a live clock immediately, and stays
there when you leave the Map and return. Graph opens with a network drawn and a
line naming the vessel it chose. *Failure:* a blank white page means a stale
`dist/` — rebuild with `npm run build` in `frontend/`.

---

## feat: the Graph opens on the whole network, centred on one node

```
git add maritime_isr/api/ maritime_isr/graph/store.py frontend/ \
        tests/test_map_graph_loading.py STATE.md COMMITS.md
git commit -m "feat: the Graph opens on the whole network, centred on one node"
```

`/api/graph/all` returns every current relationship as one web, most-connected
core first, with the totals it was drawn from. The view opens on it, centred on
the most-connected sanctioned vessel.

**The layout was the whole problem.** Cytoscape's built-in `cose` compares every
pair of nodes each iteration: measured at **115s** on 1,409 nodes — a hung tab.
`fcose` (new dependency) does the same graph in ~6.5s via spectral seeding plus
quadtree repulsion. `cose` is kept below 250 nodes.

**New dependency — `npm install` is required**, a `git pull` alone will not
install `cytoscape-fcose` and the build will fail without it.

Verify:

```
cd frontend && npm install && npm run build
python -m pytest tests/test_map_graph_loading.py -q   # 21 passed
python -m pytest -q       # 594 passed, 4 skipped, 1 pre-existing failure
python -m maritime_isr.api                            # open /graph
```

*Success:* a web of the whole graph, framed on one vessel, with a panel stating
how much of the graph is on screen and on what basis the centre was chosen.
*Failure:* a tab that locks for a minute means `fcose` did not load and it fell
back to `cose` — check `npm install` ran.

---

## docs: bring every document up to ADR-026/027, and fix a path CLAUDE.md got wrong

No code. An audit of all twelve `.md` files against the tree.

```
git add *.md data/README.md COMMITS.md
git commit -m "docs: bring every document up to ADR-026/027"
```

**The one that mattered most:** CLAUDE.md named the fusion core `fuse/` in three
places — twice in invariant 5 and once in the layout. The code is in `fusion/`;
`maritime_isr/fuse/` is an **empty package with a 0-byte `__init__.py` that
nothing imports**. CLAUDE.md is read on every invocation, so this was the
highest-leverage inaccuracy in the repo. Corrected, with the dead package named
so nobody puts code in it.

Also landed: ADR-027 (what the operator sees first, and what it may imply);
GLOSSARY entries for imaging opportunity, reachable region, scene footprint,
speed bound, degree and ended relationship; ARCHITECTURE §5 derived-table list,
§6 overpass in the layer chain, §6a the cheap-endpoint and two-graph-read-shapes
rules; the stale test tally (525 → 594) plus the undocumented fixture chain the
tally depends on; and a pointer in the execution spec to capability built
outside its 0.0–6.3 numbering.

Verify by reading, but the claims were checked against the tree:

```
wc -c maritime_isr/fuse/__init__.py                 # 0
grep -rn "maritime_isr.fuse\b" maritime_isr/ tests/ # no importers
```

---

## feat: coastal radar as a second sensor, and the four places the core assumed AIS (ADR-028)

**2026-08-16.** The first source added since CLAUDE.md §4.5 wrote down the
connector claim — *"every source is a connector, never a core change"* — and the
first test of whether it was true. It was not, in four places, all of which
failed silently and all of which are now fixed source-agnostically.

**Why radar.** The demo could not tell its own story: of six detectors two fired,
because every detector reads the track engine and the real corpus has no AIS
position tracks. The headline claim — a contact on radar with nothing
broadcasting there — needed SAR imagery this AOI cannot get (ADR-017). Coastal
radar is the Coast Guard's primary sensor, is structurally an AIS position report
minus the identity fields, and needs no imagery. **The dark-vessel path fires for
the first time in this project's history.**

**What landed.**

- `schemas/sources.py` — a `TrackSource` descriptor. The grouping key, whether it
  is an identity, whether the sensor observes transmission, its accuracy, its
  reuse-guard. Nothing downstream branches on a source *name*.
- `schemas/records.py` + `schemas/__init__.py` — `RadarTrackReport`,
  `RADAR_TRACK_REPORT`, `RADAR_CORRELATION`; `track_source`/`track_key` on
  `TRACK`, `TRACK_POINT`, `ENCOUNTER`.
- `ingest/radar.py` — the connector, with the sensor physics (RCS↔length,
  range-dependent position accuracy) shared with the simulator so a bug in one
  is a bug in both.
- `scenario/radar_network.py`, `scenario/radar.py`, `scenario/radar_truth.py`,
  `scenario/scenarios/group_r.py` — 16 stations on real coastline, detection by
  signal-to-noise against the radar horizon, shadow sectors, a maintenance
  outage, sea clutter, four fixed installations, and six scenarios inside the
  coastal belt. The picture is derived from `world.tracks`, so a radar-only
  contact and an AIS-only track are two views of **one ship**.
- `fusion/radar_ais.py` — correlation by handing 15-minute epochs to
  `associate_scene` unmodified, aggregated into a time series so "she was
  explained, then she was not" is expressible.
- `scenario/measure_radar.py` — precision and recall against a second quarantined
  truth table, with the false positives broken down by cause.
- `tests/test_radar.py` — 27 tests, every one of which **runs** the thing.

**The findings, which are worth more than the feature.**

1. `detect_encounters` rejected self-pairs on `mmsi_a == mmsi_b`; both are `None`
   on radar, so every radar-to-radar pair was discarded and the detector could
   not fire at all.
2. The anomaly library resolved subjects with `resolve_mmsi(store, tr.mmsi)`,
   minting `vessel:mmsi:None` — a node that resolves and says nothing.
3. `classify_gaps` would have called radar dropouts `INTENTIONAL_SILENCE`.
4. The reuse guard was seven days, which on recycled station track numbers built
   single 11,829-plot "tracks" out of hundreds of unrelated targets.

**And one that was not about radar:** the association score normalised by the
gate radius, so it grew *more* confident the less was known — a 12-hour-stale
track matched a contact 186 km away. Restoring the volume-normalisation term of
a 2-D Gaussian log-density fixes it; the well-constrained case is numerically
unchanged, which is why the SAR regression suite still passes untouched.

**Measured, synthetic, through the landed pipeline: precision 100%, recall 50%.**
*(Superseded by ADR-029: re-measured at precision 100%, recall 43% — 3 of 7 —
after the corpus was regenerated. Left as written; this file is a log.)*
Zero false positives; zero fires on the three out-of-coverage episodes or on the
naval decoy. The weakest number is stated too: correlation resolves about one
radar track in nine, and the honest cause is AIS receipt sparsity in the
generator rather than the algorithm — logged as an OPEN QUESTION rather than
retuned.

Verify:

```
python -m maritime_isr.cli scenario generate     # ~272,000 radar plots landed
python -m pytest -q tests/test_radar.py          # 27 passed
python tools/run_scenario_pipeline.py            # stages 3b and 4b, then the
                                                 # dark-contact results block
python -m maritime_isr.cli radar correlate
```

---

## feat: close Build 1 — the shutdown story, a serving layer, and two rules that were asking the wrong question (ADR-029)

**Why.** ADR-028 merged with four things listed as unfinished. Three of them are
closed here. The fourth — *nothing has run on Eshan's machine* — cannot be closed
from a sandbox and is answered rather than fixed.

**The number that was wrong.** The merged PR, ADR-028, STATE.md and the README
all said radar↔AIS correlation "resolves about one radar track in nine". It does
not. Two faults: the probe counted **genuinely dark vessels** as correlation
failures (they are the finding), and `associate_scene` gated every contact in a
15-minute epoch at the **epoch's** timestamp rather than the contact's own — 5.5
km of manufactured error at 13 knots. Fixed with one function, `_t_of(c)`; the
assignment, the score and the floor are untouched. A second fix has `state_at`
**bridge between two known fixes** instead of extrapolating from the earlier one,
cutting r95 error at the midpoint of a gap from **4,120 m to 1,450 m**.

Corrected, on the same denominator: **978 of 996 resolvable tracks (98.2%)
resolve to the right hull**, 11 to the wrong one (10 of them not claimed), 7 to
none — and of 241 tracks belonging to a genuinely silent vessel, **zero were
confidently explained away by another hull**. OQ-radar-1 is closed by this, not
by retuning the generator.

**What landed.**

- `fusion/radar_ais.py` — `identity_known` on a dark row when a *named* hull was
  watched stopping, so the neighbourhood census is skipped for that row alone;
  `_receipts_between` to decide she really went quiet by counting her receipts
  during the dark run (0 in five hours vs 66); `land_correlation` so the result
  reaches disk; `ambiguous` admitted into the cascade.
- `api/service.py`, `api/models.py`, `api/app.py` — `/api/radar/stations`,
  `/api/radar/contacts`, `/api/radar/tracks`, reading landed tables.
- `frontend/src/views/RadarView.jsx` + three MapView layers — the Radar tab, the
  suppressed verdicts behind a checkbox, two coverage rings per station, and a
  dashed segment from a vessel's last AIS fix to where radar still had her.
- `anomaly/library.py` — `detect_dark_rendezvous` now asks whether a party to
  **this** meeting was unexplained, by track id, instead of scanning the whole
  AOI; and scores from the evidence so its own threshold can exclude something.
  **667 alerts → 81 on the same corpus, and 76 false alarms on named background
  traffic → 0.**
- `tools/run_scenario_pipeline.py` — a NOTE when the graph already holds alerts,
  because the measurement reads the whole store and a stale one scores two rule
  sets at once. That mis-measurement happened here and is written down.

**Measured, synthetic, through the landed pipeline: precision 100%, recall 43%**
(3 of 7 findable episodes), zero false positives, zero fires on the four
out-of-coverage episodes or the naval decoy. Recall is below ADR-028's figure
because the corpus was regenerated and the episode set changed; three of the four
misses are dark runs of 60–95 minutes against a deliberate 120-minute floor.

**Not fixed, and named:** `test_generation_is_robust_across_seeds` fails on seed
8 (`afloat`, `vessel:fleet_16`). Verified against a clean worktree at the parent
commit — it fails there too, so it is pre-existing land-routing, not a regression.

Verify:

```
rm -f data/graph.sqlite
python -m maritime_isr.cli scenario generate     # ~272,000 radar plots landed
python -m maritime_isr.cli radar correlate --write
python tools/run_scenario_pipeline.py            # stages 3b and 4b, then the
                                                 # dark-contact results block
python -m pytest -q tests/test_radar.py          # 34 passed
python -m uvicorn maritime_isr.api.app:app --port 8000   # then /radar
```

---

## feat: the maritime zone layer, and the boundaries this project refuses to invent (ADR-030)

**Why.** The system understood four hardcoded circles. Five named analyses in
the requirement are unbuildable without real maritime geography and
straightforward once it exists — the highest coverage-per-effort item in the
brief.

**The decision that shaped everything else: the statutory limits are not built.**
Deriving the 12/24/200 nm limits from a public coastline mask was implemented,
measured and **discarded**. UNCLOS measures from *declared straight baselines*,
not from the coast, and India has declared them across the Gulf of Kachchh and
the Gulf of Khambhat — so a coastline-derived territorial sea sits inside the
real one exactly where the traffic is densest, with no median line against any
neighbour. The India–Pakistan IMBL is *disputed* and undelimited seaward of Sir
Creek. A boundary that looks surveyed and is not is worse than no boundary.

So EEZ, contiguous zone, territorial sea and IMBL arrive through
`ingest/zones.py` from a published file or they do not arrive — and everything
downstream is built and tested against them anyway, with the gap named out loud
rather than showing as an empty layer.

**What landed.**

- `maritime_isr/zones/` — `model` (schemas + the closed kind vocabulary),
  `geometry` (one containment test, one cell-index rule, true-metre circles and
  corridors), `derive` (the operational set and the argument against the rest),
  `store` (landing + the two-stage `ZoneIndex`), `transitions`, `query`,
  `analyses`.
- `ingest/zones.py` — the connector. GeoJSON, Marine Regions' `POL_TYPE`
  vocabulary mapped explicitly, AOI clipping, and a feature it cannot classify
  is **skipped and counted** rather than guessed into the wrong kind.
- `zone_transition` — entry/exit as a landed event table with bearings. The
  crossing is interpolated onto the boundary (a vessel at 15 kn covers 4.6 km
  between fixes) and a track that began inside reports `entry_censored` with a
  null bearing rather than a fabricated direction.
- Four analyses: area visit (a query, no alerts by design), maiden visit, lane
  deviation, anchored outside port limits — the last idle until a territorial
  sea is loaded, and saying so by name.
- API: `/api/zones`, `/api/zones/{id}/vessels`, `POST`/`DELETE /api/geofences`.
  A drawn box is answered **on demand in ~8 s** via the H3 hash join, through
  the same `transitions_for_track` the pipeline uses.
- UI: ten independently-toggleable geography layers with a back-to-front visual
  hierarchy, a polygon draw tool, and a panel that shows each zone's authority,
  method and caveat before it shows the answer.
- `ports.py` — **25 west-coast facilities added**, `GAZETTEER_V1_NAMES` recorded
  so the before/after figure is reproducible, and `gazetteer_recall()` to
  measure it on the same corpus by the same code path.
- The four hardcoded circles became landed rows; `detect_sensitive_loitering`
  now watches operator geofences too when given a `ZoneIndex`, and behaves
  identically without one.
- Group Z: six scenarios — three true anomalies, three decoys, one decoy per
  condition of the anchoring rule. **No scenario asserts that crossing the IMBL
  is an offence**; that would bake a contested legal claim into the answer key.
- `tests/test_zones.py` — 35 tests, every one driving code: geometry accuracy,
  the sub-cell indexing case, crossing interpolation, censoring, the refusal to
  derive statutory limits, each analysis with its own decoy, the connector's
  skip-rather-than-guess behaviour, and the API round trip including the refusal
  to delete a standing zone.

Verify:

```
maritime-isr zones build          # 64 zones; names the four kinds it lacks
maritime-isr zones status
python -m pytest -q tests/test_zones.py       # 35 passed
python tools/run_scenario_pipeline.py         # stages 3c and 7b
```

---

## fix: the timeline player played 14 years of a window in which nothing could move

Reported from the laptop: *"when i run the timeline player, nothing happens.
nothing moves on the map."* The play button was innocent — the clock really was
advancing. The scrubber was scrubbing the wrong window.

```
git add maritime_isr/api/ frontend/src frontend/dist \
        tests/test_map_graph_loading.py STATE.md COMMITS.md
git commit -m "fix: the timeline player played 14 years of a window in which nothing could move"
```

**Two windows, treated as one.** `/corpus-window` returned the union of the
event tables and `ais_position`. The scrubber's only job is to interpolate AIS
tracks to a clock, so the only days it can move anything on are days with
positions — and on the laptop corpus those are 52 of 5,317. The 2012 start is a
thin tail of real GFW identity and loitering records; the eight-week narrative
sits at the dense end. So 99.04% of the bar covered years with nothing in them,
one playthrough at 7 s/day took **10.3 hours**, the whole motion band was 9.6 of
the slider's 1,000 steps, and the playhead defaulted to `t = 1` — 53 minutes
past the last position, so the map opened with **zero** vessels while the status
line read "200 vessels on AIS".

A scenario-only corpus has no 2012 tail, so both windows coincide and the player
worked in every sandbox it had ever run in. That is why it survived to the demo.

`/corpus-window` now returns `motion_start`/`motion_end` alongside `start`/`end`,
plus a `note` naming both spans when they differ. `/stats` and the incident
report still get the corpus window, which is what they mean. The client plays
the motion window, falls back to the corpus window only when no positions are
landed (ADR-005), and discloses the difference in the notes bar and in the
status line. The playhead parks on the busiest instant instead of the end;
playback is capped at 120 s end to end; the status line counts vessels **on
screen**, not in the corpus.

Verify — the browser half is the half that matters:

```
python -m pytest tests/test_map_graph_loading.py -q     # 17 passed
python -m pytest -q       # 662 passed, 37 skipped, 2 pre-existing failures
python -m maritime_isr.api      # open /, wait for the map, press play
```

Both failures reproduce on the base commit and belong to this sandbox, not to
the change: no ports landed (`scenario generate` was run without
`run_scenario_pipeline.py`), and a sanctions-hint assertion that hard-codes one
branch of `config.CLI` and so fails wherever the console script is on PATH.

*Success:* the map opens with the fleet already drawn and a mid-window clock,
and pressing play slides ships across the Arabian Sea while the counter moves.
The notes bar admits it when the corpus reaches back further than the AIS does.
*Failure:* a blank white page means a stale `dist/` — rebuild with
`npm run build` in `frontend/`.
