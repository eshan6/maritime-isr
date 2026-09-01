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

---

## 2026-08-22 — the map said everything and distinguished nothing

*Reported: "the map key and the map have become too cluttered and crowded" —
and, underneath it, "which ones are the actual vessels?"*

```
git add maritime_isr/api/ frontend/src frontend/dist \
        tests/test_map_legibility.py STATE.md COMMITS.md
git commit -m "fix: the map drew a two-year-old pin and a live ship as the same mark"
```

**The question was the finding.** Exactly one layer on that map moves. Every
other coloured dot is an event pin, deliberately not filtered by the clock so
nothing blinks during playback — a good decision the map never disclosed and
then undermined by drawing both as a small coloured circle. So a pin that has
never moved read as a ship that had stopped, which is what "they stay stationary
for days" was describing.

Eighteen of twenty-four layers on at first paint. Eleven layers drawn as
circles between r4 and r8. Three unrelated meanings sharing `#b0221b`. Every one
of those defaults had been set for a real reason in its own past session; nobody
ever added them up.

Now: four collapsible key groups that say what they hold (a collapsed group
still shows `n/m` — collapsing reduces reading, it does not hide state); an
opening set of five layers; mark weight separating live from historical from
sensor, with alerts drawn as a ring rather than a disc because an alert is an
annotation *about* a position; AIS gaps moved off the encounter red to
near-black, which says "silence" and deliberately not "intentional silence"
(ADR-005); and the notes bar moved clear of the key it had been painting over.

Three defects found while in there, all of the same kind — **a view asserting
what it had not measured, or withholding what it had:**

* `/tracks` capped at 200 and returned `note: None` regardless. Measured: **200
  of 208** vessels drawn, in silence. `/events` has reported its cap since
  ADR-024; this was the one endpoint still breaking the rule.
* Alert markers were drawn at the subject's *earliest* located event, because
  events arrive ordered by `start_time`. Now: her track interpolated to the
  alert's own timestamp, else her nearest event **in time** with the label
  saying so, else no marker at all.
* 607 graph identity edges all read "identified as" — the one phrase carrying no
  information. `identity_kind` now rides the payload, gated on the closed
  `IDENTITY_KINDS` vocabulary. Measured: 225 MMSI, 224 name, 99 call sign, 59
  IMO, 0 unlabelled. **`GraphEdge` had to be declared too** — pydantic drops
  undeclared keys, so the field would have reached `/graph/all` and silently not
  `/vessels/{id}/neighbourhood`.

And the reported one: hovering a graph edge did nothing, because `mouseover` was
bound to `node` only.

Verify:

```
python -m pytest tests/test_map_legibility.py -q      # 12 passed
python -m pytest -q     # 694 passed, 18 skipped, 1 pre-existing failure
cd frontend && npm run build && cd .. && python -m maritime_isr.api
```

The pre-existing failure is the `config.CLI` assertion in
`test_sanctions_match.py`, which fails wherever the console script is on PATH.
Second session running; it should be fixed.

*Success:* the map opens calm, and the four key groups tell you which marks move
and which are pins. Hovering a graph edge lights it up and names it — "MMSI",
"IMO number", "sanctioned under" — instead of doing nothing.
*Failure:* a blank white page means a stale `dist/` — rebuild with
`npm run build` in `frontend/`.

---

## IDEX Challenge 82 — Area 1: the ranked Vessel of Interest (ADR-031)

The requirement names six capability areas and states one outcome twice: rank
Vessels of Interest with supporting evidence and cut operator workload. Five
areas are feeders; one **is** the object. This project had the feeders — an
anomaly library, a radar dark cascade, a sanctions matcher, an object graph —
and no object. Alerts sat in a flat queue, risk was a four-component index on a
different screen, and nothing tied a reason to a next action.

`maritime_isr/assistant/` is that object. Served at `/api/voi`, driven from
`maritime-isr voi`, shown on a new **Assistant** tab.

* **The score decomposes exactly.** Noisy-OR over independent factors, allocated
  back in log space, so the parts sum to the whole to floating point — asserted
  in the tests, because that identity is the entire claim. A composite an
  operator cannot take apart is worthless to them.
* **A "Vessel" of Interest need not be a vessel.** 52 of 55 alerts land on a
  contact or a detection, not a hull. A target nobody can name is what makes a
  finding.
* **Recommendations compute feasibility and state capability.** "Call her on
  VHF" is worked out against the station network's real geometry, and an
  unavailable action is shown with its reason. Most actions say plainly that
  the system cannot perform them — the EO loop is Area 5, paperwork Area 4,
  radio Area 6.
* **The question answerer has no generative step.** Retrieval against a closed
  intent set, three outcomes, and the middle one — "the system holds no record
  of that", phrased about the record rather than the vessel — is most of the
  value.

**Three defects the ranked list exposed**, all invisible in a flat queue:

1. 42 of 43 `dark_rendezvous` alerts fired inside a berth or designated
   anchorage — 32 of them 470 m from the Mangalore port coordinate. **43 → 1**;
   dark-contact precision and recall unchanged at 100% / 86%.
2. All 9 `dark_vessel` alerts were filed as **real** data. `add_alert` derives
   `is_synthetic` from the subject node; the detector pointed at a string
   nothing had created, so the flag defaulted to 0.
3. `reported-gap` edges were reachable from no graph view at all.

---

## IDEX Challenge 82 — Area 2: predictive analysis of AIS tracks (ADR-032)

Four capabilities, and one measured *out* of the product.

* **Declared identity authenticity** (`anomaly/identity.py`) — IMO check digit,
  MMSI country prefix against declared flag, registry consistency, MMSI
  structural form. Three outcomes each: `contradiction`, `ok`, `not_checkable`.
  The MID table is deliberately partial; an unallocated prefix makes no claim.
* **Activity classification** (`tracks/activity.py`) — in `tracks/`, reading
  motion only, so radar and AIS get the identical answer with no
  source-specific branch. That is Area 3's requirement satisfied by placement,
  enforced by a test.
* **Forward projection** (`tracks/projection.py`) — a first-class assertion with
  a cone that grows with lead time and is capped by physics.
* **Per-area baselines** (`baselines.py`) — the requirement's "maintained,
  inspectable artifact", derived per H3 res-5 cell and landed. Measured: 770
  cells, 212 usable. Kandla approach p95 = 12.0 kn over 19,768 observations;
  Mumbai anchorage p95 = 6.5 kn, median 0.0. `is_unusual` is three-valued —
  "cannot say" is not "normal".

**Track-departure detection is built and deliberately not a suspicion factor.**
Swept across every operating point it flagged **87-98% of the fleet**, with no
plateau. Every vessel alters course at every waypoint. Kept as an inspectable
assertion, with a test stopping it being re-added without a fresh measurement.

**Four defects the build found in itself**, each one a detector that could not
have worked:

1. `survey_pattern` claimed on **151 of 209 tracks** — a coastal rotation
   supplies long legs and reciprocal turns. Fixed by requiring reciprocals to be
   a majority of *alterations* (not of every fix-to-fix step) and a turn rate a
   voyage does not reach. Now 3 of 209.
2. The scenario's reserved MMSI block collides with ITU's aid-to-navigation
   prefix, so the identity rule reported **all 222 synthetic vessels** as
   contradictions — through the one detector built to have no false positives.
3. The `notable_activity` gate was set at 0.55 **above the 0.496 maximum the
   detector could produce**. Zero alerts, and not because the picture was clean.
4. The local baseline halved a survey pattern's score for having an ordinary
   *speed* — a category error that silenced the three genuine patterns it found.
   A baseline now scales a score only where the activity's signature is that
   metric.

**What is not built, and is said rather than implied:** declared destination
against implied and historical destination, and declared ETA against plausible
arrival. `ais_position` carries no declared destination or ETA — the generator
emits no AIS message-5 voyage data — and building a comparison against a column
that does not exist produces a detector that can never fire. The sequence is:
extend the scenario AIS emitter, regenerate, then build the comparison.

Verify:

```
python -m maritime_isr.cli scenario generate --seed 7
rm -f data/graph.sqlite && python tools/run_scenario_pipeline.py
python -m pytest tests/test_assistant.py tests/test_predictive.py -q
python -m maritime_isr.cli voi list --top 10
python -m maritime_isr.cli voi show <subject-id>
python -m maritime_isr.cli baselines show
cd frontend && npm run build && cd .. && python -m maritime_isr.api   # /assistant
```

*Success:* the Assistant tab opens on a ranked list whose scores add up on
screen, each row explains itself in sentences, and the follow-up box refuses
questions the system cannot ground.
*Failure:* a blank page means a stale `dist/` — rebuild in `frontend/`.

---

## IDEX Challenge 82 — Area 3: what the radar picture is *of* (ADR-033)

Areas 1 and 2 gave the system a subject and a set of reasons. Area 3 asks the
question underneath a radar plot that AIS normally answers for free: *what kind
of ship is that, what is it doing with the ship next to it, and what do I say
about it in one line?* Three capabilities, three different honest answers.

**1. Vessel type from motion alone** — `maritime_isr/tracks/vessel_type.py`.

Thirteen features, all motion: speed percentiles and spread, time stopped, time
at manoeuvring speed, turn rate, course spread, straight-leg geometry, report
cadence. No length, no identity, no declared type — because a radar contact has
none of those, and a feature the sensor cannot supply is a feature that makes
the model useless exactly when it is needed.

The split is by **hull**, never by track and never by chip. A vessel that
appears in training may not appear in test in any form. On held-out hulls:
**65% accurate over the eight fine classes, 90% over the coarse vocabulary.**

The coarse vocabulary is not hand-written. The confusion matrix is read back
after training and classes that trade places above a threshold are merged by
union-find, which produced `[Aframax, bulker, product_tanker]` and
`[Suezmax, VLCC]` as single answers and left `fishing`, `general_cargo`,
`reefer` standing alone. When a track lands in a merged group the classifier
returns `cannot_separate` with the group named, and below 0.45 confidence it
returns `unknown`. **The 90% figure is only honest because the vocabulary was
cut to fit what the data supports** — a tanker sub-class from motion is a claim
the requirement invites and this data refuses.

**2. Vessel-to-vessel interaction** — `maritime_isr/tracks/interactions.py`.

Four patterns: moving in company, shadowing, converging and holding, and the
transfer pattern (two hulls close, slow, and stably separated long enough to
pass something between them). Pair search is pre-filtered through H3 res 6 so
the candidate set is a hash join and not a cross product — the first version
tripped a 200,000-pair guard; the filtered search runs in about 2 seconds.

**It finds nothing on this corpus, and that is the reported result.** The
min-duration sweep found a cliff rather than a plateau: 8 findings at 60
minutes, every one of them ordinary fleet traffic sharing a coastal route, and
0 at 120. The gate is set at 120 and the zero stands. Separately,
`transfer_pattern` **cannot be validated here at all**: the scenario's transfer
counterparties (`chain_a`, `chain_b`, `coast_dark_party`, `spoof_partner`) are
dark by design, and `spine`/`receiver_alpha` never come within 500 m in a shared
epoch across any of the 72 pairings. Built, untested against a positive, said
out loud rather than implied by a green test.

**3. The contact profile** — `maritime_isr/fusion/contact_profile.py`.

One sentence per radar contact: *"Likely merchant, transiting, no transponder,
about 175 m."* Type from (1), activity from `tracks/activity` (Area 2), length
from the detection, transponder state from the dark cascade that already
decided it. It **assembles and never re-decides** — a collector that started
deciding darkness would be a second, uncalibrated copy of a rule that already
exists — and a test fails if it ever does. Produced on all 8 dark contacts.

**Wired into the queue.** `detect_vessel_interactions` joins the anomaly
library behind a 0.35 gate, and `detect_dark_vessels` now carries profiles onto
its alerts. On the corpus all three new detectors sit at 0 (`vessel_interaction`
per the sweep above; `identity_contradiction` and `notable_activity` from Area 2
for causes already recorded), and the queue is unchanged at 16 alerts.

All figures here are **synthetic-suite** figures from a simulated radar network
over simulated traffic. No real Coastal Surveillance Network feed has ever been
seen by this system, and real-feed numbers will be lower (CLAUDE.md §4.6).

Verify:

```
python -m pytest tests/test_radar_classification.py -q
rm -f data/graph.sqlite && python tools/run_scenario_pipeline.py
```

*Success:* the test file reports 25 passed / 2 skipped (the two skips are the
positive-case transfer tests this corpus cannot supply), and the pipeline prints
`interactions : 0 (none)` with the alert tally unchanged at 16.
*Failure:* an accuracy floor assertion firing means the classifier regressed —
read the printed confusion matrix before touching a threshold.

---

## IDEX Challenge 82 — group F: the factors that never fired (ADR-034)

Areas 2 and 3 added three classes of factor to the ranked Vessel of Interest —
a contradicted identity, a notable activity, a relationship between two hulls.
All three were built, gated, narrated and wired in. All three fired **zero
times**. Each zero had a defensible cause and together they meant the list never
gained what those areas existed to supply, which is the one test the brief sets:

> After each area lands, the ranked Vessel of Interest list should visibly gain
> a new class of factor. If adding an area does not change what appears on that
> list, the area was built in isolation.

**The fix is in the corpus and not in the thresholds.** A rule loosened until it
fires has been fitted to the absence of evidence, and afterwards it is
indistinguishable from a working one. Group F writes sixteen hulls: an IMO that
fails its own check digit, a call sign the registry does not hold, a name that
is not the registered one, a genuine ten-leg lawnmower, fourteen hours of
aperiodic manoeuvring, two hulls in company, one shadowing another, and a
ship-to-ship transfer with **both** parties transmitting — the case every other
transfer in this corpus cannot supply, because their counterparties are dark by
design. Each is paired with a decoy that shares its surface: a registry that
spells her name "M.V. X.", a vessel-class quibble, a coastal rotation, a lane
overtake.

**Four defects, found by writing the positive cases. Every one silent, every one
making a rule quieter without saying so.**

1. **A reversal is not an event between two fixes.** The survey rule counted
   near-180° course changes fix to fix. A hull limited to a quarter-degree per
   second takes twelve minutes to come about and AIS here arrives every four, so
   a real reversal is three sixty-degree steps and the count is zero. The
   lawnmower was classified `manoeuvring_erratically` with `reciprocal_turns=0`.
   Reciprocals are now counted between the mean headings of consecutive straight
   legs, which is what "she came about" means and does not depend on how often
   she was heard.

2. **Half of every cross-cell pair was discarded.** The interaction search used a
   one-ring H3 neighbourhood at res 6 under a comment asserting that reached
   "roughly 9 km"; a res-6 edge is 3.7 km. And the `a[0] >= b[0]` guard, correct
   for two hulls in one cell, was *dropping* cross-cell pairs rather than
   deduplicating them, because only one ordering is ever generated there. The
   ring is computed from the geometry now and the pair key is ordered.

3. **The resampler deleted every stopped vessel.** ITU-R M.1371 has a Class A set
   report every 10 s under way and every 3 min at anchor; the gap allowance was
   built from the track's *median* interval. A nine-hour transfer with both
   parties transmitting produced seven usable samples. A gap is now interpolated
   across when the vessel demonstrably did not move — 1,000 m between bracketing
   fixes, whatever the elapsed time.

4. **The pipeline query threw away the second attestation.** The real GFW
   connector lands `registry` and `self_reported` and says why: *"disagreement
   with the registry is a signal in its own right, so we keep both."* The query
   took one current row per vessel and collapsed them, so the consistency check
   answered "cannot check" 230 times out of 230.

**Two numbers moved, and both were less solid than they looked.**

The interaction persistence floor was re-derived on one corpus draw and
**falsified by the next**: a pair of fishing-fleet hulls steaming to the same
ground held station for 11.7 hours, longer than two of the three authored
relationships. Duration does not separate the populations and never did.
Separation does — eleven coincidental pairs across two draws, none closer than
5,337 m; three authored relationships all inside 4,245 m — so company and
shadowing are claimed only inside 2.5 nm.

Dark-contact recall, previously reported at 86%, reads **43% on seed 7 and 62%
on seed 8, with precision 100% in every draw**. A single-variable A/B — the whole
pipeline run twice on one corpus with the resampler change forced off — gave
identical cascade verdicts, so no detector change here is responsible. Adding
sixteen hulls shifts the generator's RNG stream and every scenario's noise is a
fresh sample. A recall figure with a denominator of seven episodes was never a
capability measurement, and is no longer quoted as one.

**And a false-positive flood, caught by the corpus's own decoy.** Once
reciprocals were counted correctly, six-hour windows of the background fishing
fleet produced 36 survey claims and the queue went from 16 alerts to 53. A
trawler working a ground and a survey vessel mowing a lawn make the *same*
geometry; only speed separates them. The survey branch now declines the trawling
band, and the cost is stated: a genuine survey conducted at trawling speed is not
findable by this rule.

All figures are **synthetic-suite** figures. No real feed has been seen.

Verify:

```
python -m maritime_isr.cli scenario generate --seed 7
rm -f data/graph.sqlite && python tools/run_scenario_pipeline.py
python -m pytest tests/test_factor_coverage.py -q
python -m maritime_isr.cli voi list --top 10
```

*Success:* the pipeline prints `interactions : 3` with one of each kind, and
`identity_contradiction 5 / notable_activity 3 / vessel_interaction 6`; the
ranked list carries all three factor kinds.
*Failure:* an empty `interactions` line means the candidate search regressed —
read `_ring_for` before touching a threshold.

---

## The voyage she declares, and the four defects between the message and the rule (ADR-035)

Everything still open from the last session, closed. The largest was a capability
the brief calls out in its own words as *"one of the strongest and simplest
suspicion factors available"* — and it was not built because **nothing landed a
declared destination.** AIS message 5 carries destination, ETA and draught; the
generator emitted no message 5; the live connector filtered to `PositionReport`
and dropped the rest. A comparison against a column that does not exist is a
detector that can never fire, so the column came first.

**What landed.** A canonical `VoyageDeclaration`, separate from `PositionReport`
because message 5 is a separate message with its own cadence and nullity. A
connector that parses `ShipStaticData` and resolves an ETA that has no year in
it. A generator that declares a destination on **every ordinary port call,
honestly** — 3,091 declarations over 131 hulls, because a rule tested only
against liars measures recall and says nothing about precision. Three new hulls:
two that lie and one diverted honestly mid-passage, which is the decoy that
forces the rule to ask "was she *ever* heading there" rather than "did she
arrive".

`destination` is landed as free text and deliberately not normalised. "JNPT",
"NHAVA SHEVA", "INNSA" and "JNPT>>SIKKA" are all things real transmitters send;
resolving them is a judgement with a confidence and belongs downstream.
`resolve_destination` matches exactly and returns None otherwise — no fuzzy
matching, because a missed resolution costs a finding and a wrong one tells a
watchkeeper a ship is lying about a port we picked for her.

**Four defects, found by watching the alert count.**

*43 alerts on 41 innocent hulls.* Required speed is distance over *remaining*
time, so near arrival it diverges: a vessel an hour from her berth on a stale
ETA "needs 200 knots". She is late. The test is now a six-hour shortfall, and an
expired ETA is not checked at all — the brief's question is forward-looking.

*The heading check read every fix after the declaration, with no end*, scoring
her arrival, her berth and her next voyage against a port she left days earlier.

*A `timestamp[us]` column divided as if nanoseconds.* Nineteen hours came out as
68 seconds, so the check answered "not enough track to say which way she went"
about the one hull written to steam the wrong way. `tracks.kalman.epoch_s`
exists because this atomised every track once before.

*Eleven honest hulls called liars for swinging on their cables.* A ship at
anchor yaws through the compass, so every step is "away" and the fraction is a
perfect 1.0 — on a question that should never have been asked.

43 → 13 → **2**, and the two are the two hulls written to lie.

**Also closed.**

*Seed 9 would not generate.* `background.py` budgeted a port call from the
moment she sails and never subtracted the passage; bg_8 leaves Kochi for Mumbai,
two and a half days the arithmetic never counted, and her departure landed 11.3
hours past the corpus window. Seeds 7-10 now all generate.

*Distance from shore was a proxy.* The vessel-type classifier used distance to
the nearest gazetteer port, so a hull five miles off an empty beach scored as
120 km from shore. Now computed from the same 1 km land mask the SAR detector
and the validator use. **Operating depth stays absent and is not faked.**

*`rendezvousing`* is named by the brief and is not a property of one track; it
lives in `tracks.interactions` as `converging_and_holding` and
`transfer_pattern`, and the activity module now says so.

*The MMSI checks* stay unmeasurable on synthetic data, recorded as a decision:
constructing a flag contradiction needs a valid country prefix and could collide
with a real hull, and a safety invariant with an exception is one somebody
widens later.

*A re-export removed as "unused"* broke three modules on import — restored with
the marker that says why it is there.

*The fishing-fleet decoy could start on land, and fixing it re-rolled the whole
fleet.* Forty hulls scatter on a uniform bearing 40-95 nm from a ground off
Gujarat, and a third of that circle is the Saurashtra peninsula.
`generate_track` routes transit legs around land but cannot route a vessel that
begins on it. The correction is the shared `nearest_water` — and the first
version of it moved one `r.uniform` call past another, which re-rolled every
parameter of all forty hulls and dropped fishing recall from 86% to 73%. A
corpus resample reads exactly like a classifier regression. Draw order is now
preserved deliberately, with a comment saying why, so the fix moves the hulls
that were on land and nothing else.

*The original note:* Forty hulls are scattered on a
uniform bearing 40-95 nm from a ground off Gujarat, and a third of that circle
is the Saurashtra peninsula. `generate_track` routes transit legs around land
but cannot route a vessel that begins on it. Latent until an unrelated change
shifted the RNG stream — which is how every seed-dependent placement bug in this
generator has surfaced. Now corrected through the shared `nearest_water`.

**Corpus, seed 7:** 27 alerts across 10 detectors. Dark-contact precision 100%,
recall 62% (5 of 8). Recall read 43%, then 75%, then 62% across this session's
runs, every move a fresh RNG draw rather than a change in capability; precision
held at 100% throughout, and it is the number ADR-004 constrains.
Synthetic-suite figures.

Verify:

```
python -m maritime_isr.cli scenario generate --seed 7
rm -f data/graph.sqlite && python tools/run_scenario_pipeline.py
python -m pytest tests/test_voyage.py -q
```

*Success:* the pipeline prints `declarations : 3,091 row(s) over 131 hull(s)`
and `voyage_contradiction 2 alert(s)`.
*Failure:* a voyage count in the dozens means a gate regressed — read
`MIN_SHORTFALL_H` and `UNDERWAY_MIN_KN` before touching anything else.

---

## Area 4 — pre-arrival notifications, and nine defects that presented as silence

**What this unit is.** The IDEX Challenge 82 brief's Area 4: arrival
notifications reach the Coast Guard as PDF, Word and spreadsheet attachments by
email, carrying vital information that cannot be stored or fused because of its
format. This lands them — five formats into one record shape, every value
carrying the passage it was read from and a locator an analyst can point at —
and then compares what the paperwork declares against what the track shows.

**New modules**

* `maritime_isr/ingest/pans/` — the connector. `readers.py` (one reader per
  format, all emitting the same `Label: value` passages, each with an earned
  confidence), `extract.py` (format-blind; synonym table, OCR confusion fold,
  day-first date parsing), `resolve.py` (IMO → call sign → normalised name, no
  fuzzy matching), `land.py`.
* `maritime_isr/anomaly/paperwork.py` — three three-valued checks: declared last
  port, declared arrival window, declared ballast against a laden draught.
* `maritime_isr/scenario/pans.py` + `scenarios/group_p.py` — the document
  generator and scenarios P1-P8.
* `tests/test_pans.py` — 27 tests.

**Three new detectors**, `paperwork_contradiction`, `notification_unmatched` and
`arrival_without_notification`, with factor kinds, narration and gates.

**The nine defects are the point of this entry.** The unit suite was green and
the extraction stage reported 3,107 fields from 292 documents. The alert counts
were what exposed the area: 24 unmatched against 1 authored, 10 unnotified
arrivals against 1, 8 paperwork contradictions against 2. Full account in
ADR-036. No threshold was moved to fix any of them — every fix is to a join, a
corpus inconsistency, or a value read from the wrong place.

Verify:

```
python -m maritime_isr.cli scenario generate --seed 7
rm -f data/graph.sqlite && python tools/run_scenario_pipeline.py
python -m pytest tests/test_pans.py -q
```

*Success:* the stage prints `5/5 available` readers and ~295 documents with
0 unreadable, and the tallies read `paperwork_contradiction 3`,
`notification_unmatched 2-3`, `arrival_without_notification 1`.
*Failure:* a paperwork count in the dozens means a rule is joining a
declaration to the wrong event again — read `match_arrival` and the
`prior_calls` branch of `check_last_port` before touching any threshold. A
count of 0 is worse: it means a check is returning "not checkable" for the whole
corpus, which is what the filing-time defect looked like.

---

## Area 5 — automating the electro-optical loop (ADR-037)

Four things the requirement asks for, and only one of them needs pictures:
capture without operator intervention, bind the image to a track, classify
against a library, alert on the disagreement. Three are control and fusion
logic. So this builds the loop and puts the classifier behind an interface, as
the brief instructs — *"image classification is the commodity part of this
problem, the part any competitor can also do."*

**New package `maritime_isr/eo/`.** `cue.py` is the scheduler and the centre of
the area: given far more tracks than cameras, which track a camera is pointed at
and when, as a **global assignment per slot** over cameras × candidates —
because greedy per-target matching hands one station's camera to the three most
suspicious contacts in its arc and leaves the rest idle, the failure CLAUDE.md §6
bans one domain along. Priority is `0.55 x suspicion + 0.30 x information gain +
0.15 x staleness`, multiplied by expected image quality; a closing observation
window multiplies the *cost*, not the priority. `camera.py` is the geometry and
quality model, `conditions.py` the light and weather, `capture.py` the image
bound to a track and landed as evidence, `classify.py` the swappable classifier
and its reference library, `appearance.py` the numeric stand-in for pixels.

**There is no camera.** Every capture is simulated through the `CaptureSource`
seam — `scenario/eo.py` in this build, a hardware driver in a deployment — and
every row carries `capture_mode='simulated'`, an empty `image_ref`, and the
model's own provenance string saying it has never seen an image.

**Swap-ability is demonstrated, not asserted.** Two implementations ship and
`tests/test_area5.py` defines a **third inside the test file** and substitutes it
into the running loop; all three produce bound, landed captures and nothing else
changes. The pipeline prints the same comparison over the real captures.

**Group O** authors two lies (a tanker broadcasting fishing; a crane-equipped
cargo ship broadcasting product tanker — the distinction motion can never make),
one decoy that must stay silent (a cargo ship broadcasting bulker: both are
*cargo* under the AIS ship-type standard, and a rule that fires here fires on the
merchant fleet), and two capability boundaries stated with numbers (a genuine
liar 150 km offshore that no camera can reach; a genuine liar whose only camera
windows fall at night, when a thermal silhouette cannot carry a deck).

**Eight defects, six of which were visible only as numbers.** A confidence that
did not track accuracy and refused 84% of good images; an observation-noise model
with no floors that published an eleven-class vocabulary; a merge threshold that
asked "can the model tell A from B" when the question was "can it place her in an
AIS ship-type family", producing 22 false accusations in 1,500 honest looks; a
merged label read as bounding nothing, which silently discarded the brief's own
headline example; a rule that read one model's label in another model's
vocabulary and accused 36% of an honest fleet when a second classifier was
swapped in; an identity radius that pretended sister ships are separable. Then
two join defects that made the whole area silent: radar tracks the correlation
cascade had already matched to hulls entering the cueing candidate set as
anonymous, and a declared-class index keyed on the identity table's vessel id
while the lookup used the canonical graph node id. Full account in ADR-037. **No
threshold was moved to fix any of them.**

Verify:

```
python -m maritime_isr.cli scenario generate --seed 7
rm -f data/graph.sqlite && python tools/run_scenario_pipeline.py
python -m pytest tests/test_area5.py -q
```

*Success:* stage 7c reports 16 cameras, a picture split into named and
unidentified targets, a tasking count with its utilisation, and a deferral
ledger whose largest bucket is `no_camera_in_reach`; stage 7d reports
`imagery_type_mismatch` firing on the two authored hulls and no others.
*Failure:* a count of 0 means the join broke again — check that captures carry
`vessel:` subjects and not only `contact:` ones before touching any threshold,
because the rule needs a *declared* identity to contradict. A count in the dozens
means the vocabulary is being compared at a finer resolution than the AIS
ship-type family, and the merchant fleet is about to arrive on the queue.

---

## Area 5 — five defects that presented as silence, and one that presented as an alert

`maritime_isr/scenario/cast.py`, `maritime_isr/eo/cue.py`,
`maritime_isr/anomaly/library.py`, `maritime_isr/anomaly/imagery.py`,
`tools/run_scenario_pipeline.py`, `tests/test_area5.py`

Area 5 shipped with a green unit suite, ran end to end, and produced exactly one
`imagery_type_mismatch` on the corpus. That alert was a **false positive**, and
the two hulls the area was authored to catch had never fired. Both halves are
now fixed and the corpus reports **2 alerts, both authored, none else**.

**The silence had five causes, each hiding the one behind it.**

1. **The corpus held no lie.** `DECLARED_CLASS_OVERRIDES` is keyed by cast key
   (`eo_false_class`); both readers look it up by entity id
   (`vessel:eo_false_class`). Merged un-keyed, every lookup missed and every
   authored liar broadcast the truth about herself — the rule was correct to say
   nothing. The third time this area has been silenced by two id spaces for one
   hull. `build_vessels` re-keys and now **raises** on an override that names
   nobody, because an override that lands on no one fails silently.
2. **The camera cannot size a hull an AIS track reported.** Pixels-on-target is
   length over range, and only radar measures length, so 44% of candidate
   positions carried none and were refused as unobservable. O1 was a candidate
   above the floor in ten consecutive slots with a camera free, and was imaged in
   the two where the nearest fix happened to be the radar one. Length is a
   property of the hull, so it is merged across the tracks that collapse onto her.
3. **The loop closed at the stage boundary, not at the classifier's latency.**
   O1's first look contradicted her at 08:15; the scheduler did not learn it
   until the stage ended, reset her staleness clock on the look it had just
   taken, and dropped her to 0.165 against a 0.30 floor for six slots in which
   she was in clear view. Stages are one slot now — in a deployment the loop's
   latency is the time to read an image, which is seconds.
4. **The urgency lookahead stopped at the plan's last slot**, which at one slot a
   stage makes every candidate maximally urgent. It now looks ahead in time, and
   is scaled by information gain: urgency measures what is about to be lost, and
   nothing is lost by missing a question already answered.
5. **The pipeline reset the staleness clock across the boundary even for an
   unsettled contradiction**, undoing at the seam the withholding that exists
   inside `plan_cueing` to supply the corroborating second look.

**The false positive was corroboration stated as an absolute.** An honest,
stationary hull sat in one station's arc for five days and collected **130
classifiable looks**; the classifier is noisy by design, two of them agreed on
`container`, and the rule accused her. "Two agreeing looks" is a claim about a
rate — one in a hundred thousand *per pair* — and 130 looks hold 8,385 pairs.
Fixed at both ends because they fail differently: `MIN_CONTRADICTED_SHARE = 0.5`
requires the agreeing looks to be a majority of the looks that decided anything
(this holds however the images arrived), and `MAX_LOOKS_PER_VERDICT = 3` in
`eo.cue` stops re-imaging a settled hull (this returns the wasted slots). The
bound also had to be made reachable: it lived in a dict local to each
`plan_cueing` call while stages were three slots long, so it never reached three
and had **no effect at all** on a run of a thousand taskings.

**Capacity: the floor is an opportunity cost, not a quality bar.** 115,028
camera-slots were spent on nothing while 46,527 reachable targets were refused
for scoring under 0.30. Below-floor targets now stay in the same global
assignment charged a penalty larger than any value spread, so they take only a
camera nothing else could use; they are marked `opportunistic` on the tasking and
counted separately, so a fill cannot inflate the utilisation figure that is meant
to measure demand. This is safe only because of the share rule above: under a
bare count, more looks meant more chances to accuse an honest hull; under a
share, an extra look makes an accusation harder.

**No threshold was moved to make a count come out right.**
`MIN_MISMATCH_QUALITY` (0.45), `MIN_MISMATCH_CONFIDENCE` (0.62),
`PRIORITY_FLOOR` (0.30), `MIN_CLASSIFY_QUALITY` (0.35) and
`MIN_CORROBORATING_CAPTURES` (2) are all unchanged.

Also: three corpus-accounting tests had been **skipping silently** because they
asked `api.reader` for `scenario_truth`, which ADR-019 deliberately does not
register there — "no such table" read as "no corpus". They read the partitions
directly now. And the headline sentence an operator reads said "she is a fishing,
which is a fishing under the AIS ship-type standard"; the family is now named as
a category rather than with an article.

Verify:

```
python -m maritime_isr.cli scenario generate --seed 7
rm -f data/graph.sqlite && python tools/run_scenario_pipeline.py
python -m pytest tests/test_area5.py -q
```

*Success:* stage 7c reports candidate positions taking their length from another
track of the same hull, a utilisation split into "earned their slot" and "filled
a camera nothing else could use", and a deferral ledger whose largest bucket is
`no_camera_in_reach`; stage 7d reports `imagery_type_mismatch` **2 alert(s)**, on
`eo_false_class` (O1) and `eo_crane_ship` (O2) and no others; the Area 4 rules
report `paperwork_contradiction` 3, `notification_unmatched` 2,
`arrival_without_notification` 1. `tests/test_area5.py` runs 62 tests with **no
skips** — a skip here means the corpus or the graph is missing and the accounting
did not happen.
*Failure:* a count of 0 with the decoys also silent means the overrides did not
land — check `world.declared_class_overrides` is keyed by entity id before
touching any threshold. A count above 2, or any alert on a hull outside group O,
means the share rule is not holding and the merchant fleet is next.

---

## The operator surface: one screen, named sources, and a dark theme (ADR-038)

`frontend/src/views/WatchView.jsx` (new), `frontend/src/lib/theme.js` (new),
`maritime_isr/assistant/attribution.py` (new), `frontend/src/App.jsx`,
`frontend/src/theme.css`, `frontend/src/views/MapView.jsx`,
`frontend/src/components/bits.jsx`, `frontend/src/lib/format.js`,
`maritime_isr/api/{models,graph_service,report,service}.py`,
`maritime_isr/assistant/{collect,model}.py`

Four merges, from a review of the running app.

**One tab where three were.** Assistant, Findings and Alerts showed
substantially the same facts three times over, with the only irreplaceable
capability (recording a disposition) buried in the last. `Watch` reads by vessel
or by event; the disposition controls are on the alert in both. No
real-versus-synthetic filter or section, by instruction; the per-row SCENARIO
tag stays. The three old paths redirect rather than 404.

**Attribution.** `source graph / events` named a SQLite table in this
repository. `origin` is now the outside body a fact came from and `derivation`
what this system then did to it, attached in `Evidence.as_dict` so it holds for
every collector. Identity-change evidence says what changed rather than
repeating one sentence per change.

**A working basemap.** The CARTO endpoint stopped being keyless and returned its
watermark on every tile. Three keyless sources, Ocean by default for bathymetry.
Nothing about the map was wrong: the marks were always placed from a lat/lon.

**Dark theme**, three states off one set of CSS custom properties, with the six
family colours re-stepped for the dark surface and revalidated.

**Selection is visible on the map**, as a ring above the marks rather than a
recolour, because colour there is meaning and a selected vessel is still a
vessel.

**Dates are DD/MM/YYYY everywhere**, including the exported report.

Defects surfaced along the way, three of them worse than the thing being fixed:
a misplaced brace that emptied the light theme of `--font` and the whole type
scale; every map layer gated on MapLibre's `load` so an unreachable tile host
deleted the picture instead of degrading it; `EvidenceHop` missing its `origin`
field so Pydantic silently dropped the attribution; and a swallowed `/tracks`
failure that printed "no AIS tracks in this window" whether the request failed
or the sea was empty.

**Reverted after review:** forcing a capital on every enum. Casing is left as
the data and the existing screens had it.

Verify:

```
python -m maritime_isr.api
```

*Success:* five tabs (Map, Watch, Radar, Vessels, Graph); the theme button at
the end of the nav cycles System, Light and Dark and the type stays sans-serif
in all three; `/watch` shows a By vessel / By event switch and the disposition
buttons mark the choice they recorded; evidence names a body rather than a
module path; the map draws vessels and the AOI with the basemap picker set to
any of the three.
*Failure:* serif type anywhere means a CSS block boundary moved, and the first
thing to check is whether `--font` still resolves on `:root` rather than inside
a theme block. An empty map with "no AIS tracks in this window" means the tracks
request returned nothing; "could not load tracks" means it failed and the
message names why.

---

## Only the vessels that are broadcasting: reporting sessions, a drawn projection, and a clock at 20 s per hour (ADR-039)

Two asks, and a defect neither of them mentions.

**"No point showing historical vessel tracks on our Map."** The `tracks` layer
drew every hull's entire eight-week polyline at once: two hundred static lines
that said nothing about *when*, did not change as the clock ran, and crossed
everything else on screen. Deleted. In its place, for the vessels broadcasting
at the instant on the clock: the vessel, the **trail** she has travelled in this
reporting session (growing behind her as it plays), and the **projection**
reaching ahead of her.

**"AIS track predictions are not visible."** They were not — in the strongest
sense. `tracks/projection.py` has existed since ADR-032 and **nothing served
it**. New `GET /predictions?at=`, and the map draws it: a dashed line to where
dead reckoning puts her three hours on, a hollow ring at the predicted position,
and the uncertainty cone on the selected vessel. The model stays in Python; a
JavaScript copy of it would be a second, uncalibrated predictor.

**The defect underneath.** The animation interpolated between whichever two
fixes bracketed the clock, anywhere in a vessel's series — so across the 206
inter-fix gaps in this corpus that run over six hours (longest 5.4 days) it drew
a vessel steaming steadily along a line **nobody observed**. It went unnoticed
because a smoothly gliding dot looks exactly like a correct one. `/tracks` now
segments each series into reporting sessions (`AIS_SESSION_BREAK_HOURS`, six
hours) on the full data before decimation, and during a silence she is simply
not drawn.

**The clock runs at 20 wall-clock seconds per hour**, up from 0.29 (7 s/day),
with a 1x/2x/5x/15x/60x control and the rate stated in the readout. It advances
by measured elapsed time each frame rather than a fixed step per timer tick.

**Four more defects found doing it:**

* **The playhead parking never worked** — it called `busiestInstant` with rows
  carrying no sessions, found nothing and gave up. It looked fine because play
  wraps to the start. Three of two hundred vessels on screen, in the run that
  caught it; ninety-one after the fix.
* **Every map note positioned itself at the same corner**, so two on screen
  together sat exactly on top of each other — a disclosure hidden by another
  disclosure. They stack in one column now.
* A fixed step per `setInterval` tick tied the pace to timer scheduling: a
  backgrounded tab made every vessel jump ten minutes at a time.
* A ring mark was selected by comparing its stroke colour to the alert red,
  which gave the second ring layer a hardcoded, un-themed stroke.

Verify:

```
python -m maritime_isr.api
```

*Success:* the Map opens with the fleet on screen (not one or two dots) and the
readout reads "N of M vessels broadcasting · 20 s per hour". Press play: the
clock advances about three minutes of corpus time per wall-clock second, and
each vessel slides along a solid trail that grows behind her with a dashed line
reaching ahead. Click one: her uncertainty cone appears as faint circles
widening along the dashed line. A note above the scrubber states the model and
that leaving the cone is ordinary navigation, not a finding.
*Failure:* "3 of 200 vessels broadcasting" at first paint means the playhead did
not park on the busiest instant — check that `busiestInstant` is being handed
`trackList` and not `tracks`. No dashed lines at all means `/predictions`
failed; the note says so and names the reason. Trails that jump across open
water mean `breaks` is not reaching the client — `tr.breaks || []` silently
treats a missing field as "she never stopped talking".

---

## The map opens on the sea: a dark canvas, one layer, and names (ADR-040)

Four asks, three defects underneath them.

**Dark map when the app is dark.** The basemap follows the theme. `canvas`
(Esri Light Gray / Dark Gray) is the new default and the one entry with both
variants; dimming is per-source now, because turning a light raster down to 62%
is a light map wearing a filter, not a dark map. `--map-ground` is what shows
wherever a tile has not arrived, which on a blocked link is everywhere.

**The two text panels are folded into a chip.** They covered a third of the
sea. Nothing is deleted: the chip names a count and opens to the full text, and
the count is derived from the list it renders.

**The map opens on one layer — where the ships are.** No trails, no
projections, no event pins, no density, no footprints, no areas. **Clicking a
vessel draws her trail and her projection regardless of the fleet-wide
switches**, because the click *is* the request.

**Names beside the marks past zoom 8.5**, decluttered in screen space, showing
her declared name or her MMSI labelled as one — never this system's own row id.
DOM markers rather than a symbol layer, so the names do not sit behind a third
external host on a screen already watching tiles fail.

**Pink for travelled, yellow for predicted.**

**Three defects:**

* **Picking a basemap did nothing until you picked it twice.** A "this is the
  mount run, record and return" branch that the mount run never reached, so it
  ate the operator's first click instead.
* **Switching basemap emptied the map, permanently, with no error.** MapLibre
  diffs an incoming style by default — a diff strips every source this app
  added and never fires `style.load`. And with `diff: false` forcing a reload,
  the gate still failed: `ready` went true → false → true inside one React
  batch, so React compared the ends, saw no change and re-ran nothing. A
  boolean cannot express "this happened again"; it is a monotonic `styleEpoch`
  now. Both were hidden behind the swallowed click.
* A first pass at label decluttering estimated the text box at 6.1 px per
  character against a real 6.6-7.55, so two boxes each decided they fit.

Verify:

```
python -m maritime_isr.api
```

*Success:* the Map opens dark on a dark system, showing vessel marks and
nothing else; one collapsed chip sits above the scrubber where two panels used
to be. Zoom past 8.5 and names appear beside the marks, none overlapping.
Click a vessel: a pink line appears behind her and a dashed yellow one ahead,
with faint cone rings widening along it. Switch basemap (Canvas / Ocean /
Streets / Satellite) — the vessels must still be there after every switch.
*Failure:* vessels disappearing after a basemap or theme switch means the style
gate has regressed to a boolean. Names overlapping means `LABEL_CHAR_PX` is
under-estimating the box again. A basemap button that highlights without the
map changing means the swap effect is swallowing the first click.

---

## The projection stops falling behind the ship, and dark actually goes dark (ADR-041)

Three reports against the shipped map.

**The projection went stale and the vessel outran it.** The refresh was keyed to
a bucket of *simulation* time with a 180 ms debounce — and simulation time runs
at the playback speed, so the bucket is 10 s wide at 1x, 0.67 s at 15x and
**0.17 s at 60x**, shorter than the debounce. The timer was cleared before it
could fire and past a certain speed the map issued **no requests at all**.
Measured: twelve seconds at 60x, **zero requests fired**, drawn path 8.7 nm
*behind* the vessel with her past the end of it. The same starvation appears at
much lower speeds on a slower machine, which is what the report showed at 15x.

Now: one poll on a wall clock, one request in flight at a time, nothing
aborted, and the trigger is staleness as a fraction of the projection's own lead
rather than a span of corpus time. After: 60x fires 18 and lands 18, head
0.02 nm from the vessel. **And only the part of the path still ahead of the
clock is drawn** — trimmed and interpolated so the line starts under her, and
not drawn at all once the clock has run off the end of it.

**A dark app was still drawing a bright sea, for two independent reasons.** The
remembered basemap (`misr.basemap` = `"ocean"`, the old default) beat the new
theme-following default, so changing the default changed nothing for anyone who
had used the map before; the key is versioned now. And opacity does not make a
light basemap dark — it makes it pale. Dark mode now pulls brightness down to
0.38, drains saturation to -0.45 and puts 0.15 of contrast back, as theme
tokens, applied only to sources that need it.

**"Map data not yet available" across the water at high zoom.** Past its
published levels the Esri ocean service returns *a picture saying the data is
missing* rather than a 404, so there is no error to handle — the map has to stop
asking. Ocean is capped at `maxzoom` 9 and upsamples instead. That number is
conservative and unmeasured: no outbound network here to check where coverage
actually stops.

Verify:

```
python -m maritime_isr.api
```

*Success:* the Map opens dark on a dark system **even though you have used it
before** — the basemap button reads Canvas, not Ocean. Select a vessel, press ▶
at 1x and then 15x: the dashed yellow line stays ahead of her the whole time and
never falls behind. At 60x it shortens and the note chip gains a "playback
speed" row saying why. Switch to Ocean and zoom in: it goes blurry rather than
showing "Map data not yet available".
*Failure:* a yellow line sitting behind the ship along her pink trail means the
refresh has gone back to simulation-time pacing. A bright sea under a dark UI
means either the stored basemap key is being read unversioned again, or the
raster brightness tokens are not reaching the paint.
