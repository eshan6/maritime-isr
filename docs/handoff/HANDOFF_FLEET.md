# HANDOFF — the wider fleet

**Status: complete in the sandbox; unverified on host.** Generation runs green
(exit 0, all fourteen validators pass), the graph populates, and 72 of 72
selected tests pass. Nothing here has run on the Oracle VM, because there is no
Oracle VM (STATE.md). Under CLAUDE.md §5 that makes every claim below **"built,
unverified on host"** — never "currently doing".

Every number in this document is **on the synthetic corpus**. "The corpus has
674 hulls" means this repository generated 674 hulls; it says nothing about the
Arabian Sea, and no figure here may be quoted externally without that label.

---

## What this work is

The corpus had **253 hulls** and read, on a map, as sparse — and worse, as a
picture in which most of the ships are interesting. That is exactly backwards
from an operator's problem, and it quietly made the precision policy (ADR-004,
"of every 10 alerts, 7 must survive review") untestable: you cannot measure
precision against a haystack that is mostly needles.

This widens the corpus to **674 hulls** by adding variety, not volume, and adds
one new scenario group (**group W**) that hides seven true anomalies among nine
near-identical decoys inside that new traffic.

**Volume alone would have been one line** — raise the fleet size. That would
have produced four hundred more hulls all running the same three-port coastal
rotation, and it would have made the corpus measurably *worse*, because
`tracks/vessel_type.py` infers vessel class from motion and trains on this
corpus. Four hundred identically-moving hulls wearing twelve different labels is
four hundred corrupt training examples. So every archetype pairs a class with a
behaviour, and the build now fails if the two drift apart.

---

## For Eshan — how to run this, and how to tell whether it worked

Three commands, in this order. The whole thing takes **about eight minutes**.
Your folder is `maritime-isr-live`, so the first line is exactly:

```
cd maritime-isr-live
maritime-isr scenario generate
maritime-isr graph-populate
maritime-isr scenario status
```

**What success looks like.** `scenario generate` runs for roughly five minutes
and ends with a block headed `validation` listing fourteen checks. Every line
must read `ok`, including one that says:

```
  archetype_motion              ok   394 checked, 0 violation(s)
```

Just above it, a block headed `cast` must end with these two lines together:

```
  = total                            674
```

and **no** line beginning `MISMATCH:`. Two `WARNING:` lines at the very end are
expected and fine — one about the collision guard running against the corpus
profile, one about 10 vessels that moved without landing AIS.

`graph-populate` then runs about two and a half minutes and should print
`vessel_nodes_total 674` and `ownership_edges 628`.

`scenario status` should show `ais_position 816,356` and `scenario_truth 96`,
with **0** in the `real` column of every row. Zero real rows is correct: there
is no live feed yet. Its `graph` block should read roughly `nodes 2,534
synthetic`, `edges 5,790`, `edges_current 3,964`.

**Order matters, and this one caught me out.** If you also run the test suite,
run it **before** `graph-populate`, not after. Several test modules open the
default-path graph (`data/graph.sqlite`) and clear or rebuild it — `conftest.py`
says so at the top — so a test run after `graph-populate` leaves the graph
empty. It looks alarming in `scenario status`: `nodes 43 real, 0 synthetic,
edges 0`. Nothing is corrupted; just re-run `graph-populate` and it comes back.

**What failure looks like.** Any line in the `validation` block reading `FAIL`
instead of `ok`, or a `MISMATCH:` line under `cast`, or the command stopping
early with a `Traceback`. If any of those happen, copy the last forty lines of
the output and send them back — the `FAIL` line names the rule and the count,
which is what identifies the problem.

One thing that is *not* failure: if the numbers in `scenario status` are
slightly different from the table below, that is fine as long as validation
passed. The exact counts depend on the corpus profile on your machine.

**Caveat that matters.** All of this is measured in Claude's sandbox. Under
CLAUDE.md §5 this work is **"built, unverified on host"** — it has never run on
the Oracle VM, because that VM does not exist yet. Your run is what turns it
into a verified unit.

---

## Before — counts as landed, prior to this work

`maritime-isr scenario status`, run 2026-09-02 against the store as it stood at
commit `0b125d3`:

| table | synthetic rows |
|---|---:|
| ais_position | 232,581 |
| ais_voyage | 3,295 |
| arrival_notification | 161 |
| gfw_ais_gaps | 13 |
| gfw_encounters | 6 |
| gfw_loitering | 94 |
| gfw_port_visits | 316 |
| gfw_vessel_identity | 514 |
| radar_dark_truth | 16 |
| radar_track_report | 285,673 |
| sanctioned_vessel_matches | 19 |
| scenario_detections | 6 |
| scenario_eo_appearance | 2,849 |
| scenario_organizations | 29 |
| scenario_ownership | 133 |
| scenario_sanctions | 6 |
| scenario_truth | 80 |

Scenarios: **80**. (Group W is not in this run — this store predates it.)
Real rows in every table: **0**. There is no real feed yet.

---

## After — the same command, after this work

`maritime-isr scenario generate` at seed 7, run 2026-09-02. **Exit 0, all 14
validators pass.** Wall-clock **5 min 10 s** (`real 5m9.997s`, user 4m23s) on
this sandbox — a 4-core container, not the Oracle VM, which is still not
provisioned. Treat that as an order of magnitude, not a spec.

| table | before | after | change |
|---|---:|---:|---:|
| ais_position | 232,581 | **816,356** | ×3.5 |
| ais_voyage | 3,295 | **7,114** | ×2.2 |
| radar_track_report | 285,673 | **480,010** | ×1.7 |
| scenario_eo_appearance | 2,849 | **7,183** | ×2.5 |
| gfw_vessel_identity | 514 | **1,356** | ×2.6 |
| gfw_port_visits | 316 | **749** | ×2.4 |
| gfw_loitering | 94 | **366** | ×3.9 |
| scenario_ownership | 133 | **628** | ×4.7 |
| radar_dark_truth | 16 | **87** | ×5.4 |
| sanctioned_vessel_matches | 19 | **72** | ×3.8 |
| scenario_organizations | 29 | **47** | +18 |
| gfw_ais_gaps | 13 | **24** | +11 |
| gfw_encounters | 6 | **10** | +4 |
| scenario_sanctions | 6 | **9** | +3 |
| scenario_truth | 80 | **96** | +16 |
| scenario_detections | 6 | **6** | — |
| arrival_notification | 161 | *(not landed by this command)* | — |

`arrival_notification` is landed by the port-documents connector
(`maritime-isr pans ingest`), a different workstream, not by `scenario
generate`. Its 161 rows are untouched by this work; the generator did build
**692 `pans_documents` in memory** (up from ~330), so re-running the document
corpus will pick the new port calls up.

Real rows in every table after the run: **0**. There is still no real feed.

### Hull count reconciles

The generation report now prints every cohort and sums it, and the sum is
checked against the hulls actually minted:

```
principal cast                      74
fishing-fleet decoy                 40
commercial fleet                    96
coastal-radar cast                   6
zone cast                            6
factor cast                         19
paperwork cast                       6
late additions                       6
wider fleet (archetypes)           394
wider fleet (group W)               27
= total                            674
```

**253 → 674 hulls (×2.66).** The report used to name three of the ten cohorts
and then print "= 253 total", which had not been arithmetically true since the
commercial fleet was added; it is now reconciled, and prints a MISMATCH line if
a hull is ever minted outside a cohort. (Fixing that reconciliation is what
exposed the one bug in this session: `run.format_generation` called
`cohorts()`/`expected_vessel_count()` without importing them, so the whole
command died with a `NameError` *after* generating and landing. Fixed in
`maritime_isr/scenario/run.py`.)

---

## The fleet, by archetype

394 bulk hulls in twelve archetypes, plus 27 hulls that group W places by hand.
The counts are the mix of the Arabian Sea, not an even split: the Indian west
coast carries far more small fishing craft than anything else, then coastal
cargo and product tankers, then a thin tail of crude and container tonnage.

| archetype | class | n | motion it must actually show |
|---|---|---:|---|
| trawler | `fishing` | 90 | out at 8-10 kn, then 30-60 h working the ground at 2.5-4 kn, course change every half hour |
| product | `product_tanker` | 50 | 12-15 kn into the Gulf of Kutch, short anchorage wait, long discharge berth |
| feeder | `general_cargo` | 46 | 11-14 kn coastal passages that follow the coast, so more turns than a deep-sea leg |
| bulker | `bulker` | 46 | passage at 12-14 kn then a day-plus at anchor — the wait is most of her track |
| box | `container` | 40 | long straight 17-21 kn legs, priority berths, almost no anchorage wait |
| tug | `tug` | 24 | darting jobs inside one harbour; **never leaves it** |
| dhow | `dhow` | 20 | 6-8 kn inshore hops, long anchor spells, never far from the beach |
| aframax | `Aframax` | 20 | one long 13-15 kn entry leg, very few turns, multi-day discharge |
| reefer | `reefer` | 16 | fastest merchant here, 17-20 kn, short turnarounds |
| osv | `osv` | 16 | 10-13 kn out to Bombay High, 12-30 h holding station on a platform |
| ferry | `ferry` | 14 | the same 20-mile leg repeatedly at 14-17 kn |
| vlcc | `VLCC` | 12 | slowest and straightest thing in the picture; turn rate limited by inertia |
| *(group W)* | mixed | 27 | placed by hand — see below |

**Why the motion matters and is enforced.** `tracks/vessel_type.py` infers what
kind of ship a radar contact is *from motion alone*, and it trains on this
corpus using the declared class as the label. A hull labelled `fishing` that
steams a straight line at 13 kn is therefore not harmless filler — it is a
corrupt training example that moves the confusion matrix the product quotes.
So each archetype states a kinematic envelope (median speed, 90th-percentile
speed, mean turn rate) beside its trade, and a new validator,
`archetype_motion`, walks every bulk hull's integrated track and fails the build
if she falls outside her own label's band.

On this run: **`archetype_motion  ok  394 checked, 0 violation(s)`.**

Four classes had to be added to the generator to do this honestly — `container`,
`tug`, `osv`, `ferry`. Three of them are why the existing type classifier looked
better than it was: it had never been shown a hull that works entirely inside a
harbour, or one that spends a day stationary beside an oil platform, so it never
had the chance to be wrong about one.

---

## Base rate — and why it is this low

Counted at the hull, not the scenario — "what fraction of the ships on this map
is one of the bad ones" is the number that decides whether precision is
measurable. Read off the landed `scenario_truth` table:

| | anomalous hulls | total hulls | base rate |
|---|---:|---:|---:|
| before this work | 50 | 253 | **19.8%** |
| after | 60 | 674 | **8.9%** |
| the 394 new bulk hulls alone | **0** | 394 | **0.0%** |
| the 27 group-W hulls | 10 | 27 | 37% |

**All 394 bulk fleet hulls are boring. Every single one.** Not one of them is
authored into an anomaly; the ten new anomalous hulls are all in group W, placed
by hand, and even there they are outnumbered by their own decoys. The corpus-wide
base rate roughly halves, 19.8% → 8.9%.

That drop is the point of the exercise, not a side effect.

ADR-004 is a product policy: of every 10 alerts, at least 7 must survive human
review, even at the cost of missing half the real dark vessels — because an
analyst who sees three false alarms stops opening the fourth. **You cannot
measure precision against a haystack that is mostly needles.** At one hull in
five a detector could fire on nearly anything and still look respectable; the
old corpus was quietly flattering every rule in this repository. At one in
eleven — and zero in the ordinary traffic — a false positive costs something,
which is the only condition under which a precision floor means anything at all.

**Expect measured precision to fall on this corpus, and expect that to be
correct.** Any rule whose numbers hold up here is the first one in this project
that has been asked a fair question. Any rule whose numbers drop was reading the
old base rate, not the vessels.

It is also what the Arabian Sea looks like. The overwhelming majority of ships
off Gujarat are doing their job.

The other half of the design is the **decoys**: group W adds 7 true anomalies
and 9 decoys, and each decoy is built to look like the anomaly directly above
it and be innocent.

| # | true anomaly | its decoy — same shape, lawful |
|---|---|---|
| W1 / WD1 | unlicensed pair trawling in the EEZ | a licensed pair, identical pattern |
| W2 / WD2 | catch transferred to a reefer at sea | transhipment, but alongside a berth |
| W3 / WD3 | a tug switches off mid-tow inside cover | a genuine transponder failure |
| W4 / WD4 | a ferry leaves her fixed run and stops at sea | a ferry held off the terminal, berth busy |
| W5 / WD5 | an OSV meets a dhow offshore | contracted platform standby |
| W6 / WD6 | goes dark *inside* demonstrated radar cover | silent *in the coverage hole* — not dark, unhearable |
| W7 / WD7 | two dhows meet after dark | three dhows fishing together inshore |
| — / WD8 | | two liners keeping an exact shared schedule |
| — / WD9 | | a lawful anchorage queue at Kandla |

WD6 is the one to keep: CLAUDE.md forbids asserting intentional silence outside
demonstrated coverage, and WD6 is that rule made into a test case a detector can
fail. Corpus-wide the split is now **52 true anomalies, 38 decoys, 6 deliberate
misses** across 96 truth rows.

---

## Where the new hulls show up

- **Graph.** 18 new operating companies (`scenario_organizations` 29 → 47) and
  `scenario_ownership` 133 → 628 edges. Every edge goes through
  `CorporateWorld.link`, which requires provenance, a confidence and
  `valid_from`/`valid_to` — invariant 3, enforced by construction rather than by
  memory. Operators are asserted at **0.8** (a commercial fact a port agent, a
  charter fixture and a class record all corroborate); a `managed-by` overlay on
  about one hull in six is asserted at **0.55** and its note says it is inferred
  from a shared correspondence address — "a candidate, never a finding".
  The operators are a *separate* set from `commercial.COMMERCIAL_ORGS` on
  purpose: hanging 400 hulls off the existing 18 nodes would have made every
  company a hub, and a hub makes every graph traversal through it useless.
- **Sanctions / OFAC.** 3 of the 18 new operators are designated
  (`scenario_sanctions` 6 → 9; `sanctioned_vessel_matches` 19 → 72). They sit on
  three *different* archetypes — a fishing cooperative, a small-craft trader, an
  open-registry reefer operator — so "is sanctioned" does not become a proxy for
  "is a tanker", which would leave the risk score measuring the generator. All
  entries terminate on the fictional `authority:SCENARIO-SDN` node and use
  `mint_sanctions_ref` (ADR-019): **no real OFAC entry number is ever emitted.**
- **Radar.** `radar_vessels_seen` 624, `radar_vessels_unseen` 50,
  `radar_track_report` 480,010, `radar_dark_truth` 16 → 87 episodes. Of those 87,
  **79 are explainable by AIS coverage and must NOT fire** — they are hulls we
  simply cannot hear, and calling them dark is a false positive by construction.
  7 are expected to fire; 1 is legitimately dark (naval) and will fire anyway.
- **Watch / Vessels / Map.** Every fleet hull lands ordinary `ais_position` and
  `radar_track_report` rows through the same path as any other hull, so the
  existing tabs pick them up with no UI change required. See the UI note below.

### Additivity — the reason this is safe to compare against old numbers

Every fleet hull is minted **after** every pre-existing hull and drawn from a
**derived** RNG (`world.seed ^ 0x5EA1`), never `world.rng`. That is not
tidiness. `cast.LATE_ADDITIONS` records what happened the last time someone
added a single Suezmax through the shared stream: it re-rolled the entire
background fleet behind it and moved the vessel-type model's measured coarse
accuracy from above its 75% floor to 65% — a number that looked exactly like a
regression and was not one. Four hundred hulls added that way would have made
every previously measured figure in this project incomparable in one commit.

So the corpus generated at seed 7 *before* this module existed is still, hull
for hull and fix for fix, contained in the corpus generated at seed 7 after it.
Serials still come from the shared counter (a duplicate identifier is a real
collision); only the random draws are isolated.

One detail worth knowing, because it is what surfaced the test bug above: the
minting now skips reserved IMO 1005253, which corresponds to serial 525. The old
cast used serials 0-252, so the skip falls entirely inside the new fleet's range
and no pre-existing hull's identifiers move. That is luck rather than design —
had a reserved identifier landed inside the old range, every hull after it would
have shifted and the additivity guarantee would have been broken by the
reservation rather than by the fleet. Worth remembering before the cast grows
again.

Group W is also appended last among the scenario groups and, critically,
**before `group_p`** — the paperwork group files one pre-arrival notification
per port call, so a fleet that arrived after it ran would land 400 berthings
with no notification on file. That is precisely the P3 contradiction, fired 400
times by our own ordering rather than by anything a vessel did.

---

## Verified in the graph, after `maritime-isr graph-populate`

`scenario generate` lands the ownership *table*; the graph is built from it by a
second command. Run it too, or the Graph tab and every risk score stay empty —
this is the step that is easy to forget:

```
cd maritime-isr-live
maritime-isr scenario generate      # ~5 min
maritime-isr graph-populate         # ~2.5 min
```

That run wrote, and I confirmed by querying `data/graph.sqlite` directly:

- **674 vessel nodes** (was 253), 47 organization nodes, 2,577 nodes total.
- **421 `operated-by` edges pointing at the new `org:flt-*` operators** —
  exactly the 394 bulk + 27 group-W hulls. Every one carries
  `base_confidence 0.8`, a `t_start`, a `source`
  (`synthetic-scenario:scenario-ownership`), a `source_ref`, a
  `pipeline_version` and `is_synthetic=1`. **421 of 421 non-null on all of
  them** — invariant 1 and invariant 3 hold on the new edges.
- 74 management edges at confidence 0.55, also fully stamped.
- 72 `sanctioned_findings`, all terminating on the fictional
  `authority:SCENARIO-SDN`, never OFAC.

---

## Two defects found while verifying — neither is in a file I own

### 1. The graph silently turns "managed-by" into "owned-by" — REAL, worth fixing

`maritime_isr/graph/from_landed.py`, in the ownership loader:

```python
kind = r.get("edge_kind") or "owned-by"
if kind not in ("owned-by", "operated-by"):
    kind = "owned-by"
...
props=dict(edge_kind=kind, share=r.get("share")),
```

The landed `scenario_ownership` table distinguishes three kinds correctly —
`operated-by` 549, `managed-by` 74, `owned-by` 5. The graph knows only two, so
all 74 `managed-by` rows are coerced to `owned-by`; and because `props` is built
from the *already-coerced* `kind`, the original relationship is not preserved
anywhere. Queried on disk, every one of them now reads
`{"edge_kind": "owned-by"}`.

Why this matters more than a naming quibble: the corpus asserts those 74 edges
as *technical management, inferred from a shared correspondence address — a
candidate, never a finding*, at confidence 0.55. The graph restates that as
**ownership**. Confidence is preserved, so nothing is asserted loudly, but the
relationship type is simply wrong, and "who owns this ship" is a question an
analyst would act on. That is the quiet overclaim CLAUDE.md §1 and invariant 3
exist to prevent, and it is the kind that survives review because every
individual number looks right.

It predates this work — the corpus had 5 such edges before and has 74 now, so
the fleet made an existing bug 15× louder rather than causing it. Fix belongs to
whoever owns `graph/`: either add `managed-by` to the edge spec, or at minimum
keep the original in `props` (`props=dict(edge_kind=r.get("edge_kind"), ...)`)
so the coercion is recoverable.

### 2. The Vessels tab will stop showing risk scores — BY DESIGN, but the threshold is now wrong

`maritime_isr/api/service.py`, `list_vessels`:

```python
if len(current) <= 500:
    risk = gsvc.risk_index()          # full index, every vessel gets a number
else:
    interesting = set(sanctions.keys()) | gsvc.alert_subjects()
    risk = gsvc.risk_index(only=interesting)   # the rest render "—"
```

The corpus is now **674 vessels and crosses that 500 threshold**, so the Vessels
tab flips to the large-corpus path: only sanctioned or alerted hulls get a risk
score and roughly 600 render "—". The comment says the branch exists because
scoring the 9,184-vessel real corpus takes minutes; 674 is nowhere near that,
and the number was chosen when the synthetic corpus was 253.

This is a one-line judgement call and it is in `api/`, not mine. Someone should
either raise the threshold (1,000 would restore the old behaviour with room to
spare) or make it a config value. **Nothing is broken — the tab renders — but an
operator comparing against last week's screenshot will see risk scores vanish
and reasonably conclude something regressed.** Worth deciding before Eshan sees
it, not after.

Also note `list_vessels` defaults to `limit=500`, so the default page now
returns 500 of 674 with `total_matched: 674`. The frontend needs pagination, a
raised limit, or a visible "showing 500 of 674".

---

## What the UI needs

Nothing structural — every fleet hull lands ordinary `ais_position`,
`radar_track_report` and `gfw_vessel_identity` rows through the same path as any
other hull, so Map, Watch, Radar, Vessels and Graph pick them up with no schema
change. Four practical points:

1. **Four new `vessel_class` values** the UI has never rendered: `container`,
   `tug`, `osv`, `ferry`. As landed: fishing 142, product_tanker 108,
   general_cargo 100, bulker 95, container 42, Aframax 40, reefer 28, dhow 28,
   tug 26, osv 18, VLCC 17, ferry 16, Suezmax 14, naval 1. If the Map assigns
   marker shapes or colours per class from a lookup, four keys will miss it and
   fall to whatever the default is. Per CLAUDE.md the map marks take their
   colours from CSS custom properties — the new classes must be added there, not
   hardcoded.
2. **Pagination**, per the `limit=500` note above.
3. **Risk-score coverage**, per the threshold note above.
4. **Map density.** 816k AIS positions against 232k before. Whatever the Map
   does today at 253 hulls it will do 2.7× more of; if it draws every position
   client-side this is where that stops being free.

---

## Tests

```
python -m pytest tests/ -q -k "scenario or cast or world or fleet"
72 passed, 1048 deselected, 1 warning in 896.50s (14:56)
```

All green, including the nine in `tests/test_fleet.py`, which are the ones that
hold this work in place:

| test | what it stops |
|---|---|
| `test_the_cast_report_reconciles` | the generation report's own arithmetic silently going wrong again |
| `test_every_fleet_hull_is_on_the_map` | a hull minted into the registry and never moved — invisible everywhere, still counted in "674 vessels" |
| `test_every_fleet_hull_broadcasts` | a hull with a track and no AIS, i.e. a permanent dark vessel created by accident |
| `test_motion_matches_the_declared_vessel_class` | mislabelled training examples for `tracks/vessel_type.py` |
| `test_each_archetype_is_distinguishable_from_the_others` | two archetypes with identical envelopes — hulls gained, information not |
| `test_every_fleet_hull_has_a_time_scoped_graded_operator_edge` | a naked graph fact (invariant 3) |
| `test_the_fleet_is_overwhelmingly_boring` | base-rate drift back toward a flattering denominator |
| `test_no_fleet_code_draws_from_the_shared_random_stream` | the additivity guarantee being broken silently |
| `test_the_wider_fleet_adds_far_more_boring_hulls_than_interesting_ones` | the fleet concentrating rather than diluting |

The motion check is deliberately enforced **twice** — by `validate.py` on every
generate, and here — because the validator can be quietly weakened by widening a
band, and a band widened to make the build pass is exactly the failure it exists
to catch.

The 14:56 runtime is mostly world construction: several modules each build a
674-hull corpus, and `test_generation_is_robust_across_seeds` builds four. That
is roughly 2.5× slower than before this work and will keep growing with the
cast; if it becomes a problem, the fix is a session-scoped cached world per
seed, not a smaller corpus.

---

## What changed in this session specifically

The fleet itself (`scenario/fleet.py`, `scenario/scenarios/fleet_traffic.py`,
`scenario/scenarios/group_w.py` and the `cast.py` / `profile.py` /
`primitives/vessel.py` edits) was built in the two earlier, rate-limited
sessions and was already committed at `0b125d3`. This session finished it,
measured it, and fixed three things that only showed up when it was actually
run:

1. **`maritime_isr/scenario/run.py`** — `format_generation` called `cohorts()`
   and `expected_vessel_count()` without importing them, so
   `maritime-isr scenario generate` died with `NameError: name 'cohorts' is not
   defined` *after* generating and landing the whole corpus. The command looked
   catastrophically broken while the data on disk was fine. Replaced the stale
   `from .cast import (FISHING_FLEET_SIZE, PRINCIPALS, RADAR_PRINCIPALS,
   build_cast)` — whose first three names were left over from the old
   three-cohort report line and were no longer used — with the two the new
   report actually needs.

2. **`tests/conftest.py`, `tests/test_scenario.py`, `tests/test_fleet.py`** —
   the test modules each built their own world straight from
   `ScenarioWorld.new`, skipping the `reserve_against_corpus` call that
   `run.generate` makes before naming a single hull. That was harmless only
   while the cast was small enough never to reach a reserved identifier. At 674
   hulls it walked into reserved IMO 1005253 and the collision guard failed —
   correctly, on a world production never builds. Consolidated into one
   `build_world` fixture in `conftest.py` that mirrors production, and pointed
   both modules at it. (A fixture rather than a shared import because `tests/`
   is not on `sys.path`.)

3. **`tests/test_fleet.py`** — the check that no fleet code draws from the
   shared RNG was a regex over source lines, so it matched the *prose* in
   `fleet.py` that explains why nobody may use `world.rng`, and failed the file
   that documents the rule best. Rewritten to walk the AST, which cannot
   misread a docstring. The failure mode this avoids is not the noisy one: it
   is a maintainer who has seen the test cry wolf once deleting the comment to
   make it green, after which a real violation lands unremarked.

Also corrected the base-rate figures in `test_the_fleet_is_overwhelmingly_boring`
to the measured 8.9% / 19.8% (they had been estimated at 9.5% / 21.3%).

---

## Unfinished

- **Nothing has run on the Oracle VM**, because there is no Oracle VM. Every
  figure here is sandbox-measured on the synthetic corpus. Under CLAUDE.md §5
  this whole workstream is **"built, unverified on host"** — not "currently
  doing". The exit test is Eshan pasting back a passing run from real
  infrastructure, and that has not happened.
- **The two defects above are reported, not fixed** — both are in files this
  workstream does not own (`graph/`, `api/`).
- **The port-document corpus has not been regenerated** since the fleet landed.
  The generator built 692 `pans_documents` in memory (up from ~330), but
  `data/port_documents/` still holds the 161 documents from the earlier run.
  Re-run the document corpus to pick up the new port calls.
- **No detector has been re-measured against the new base rate.** This is the
  important follow-up and it is deliberately out of scope here: every precision
  figure in this project was measured at a 19.8% base rate and is now measured
  against 8.9%. Those numbers are not comparable and **the old ones should not
  be quoted alongside the new ones.** Expect them to fall; that fall is the
  corpus getting honest, not the detectors getting worse.
- The `eval` harness has not been run on this corpus yet (CLAUDE.md §8.2 wants
  it on every model change; this is a corpus change, but it moves every measured
  number, so it counts).
- **The test suite destroys the populated graph** (see the ordering note above).
  That is pre-existing behaviour, not something this work introduced, but it now
  matters more because the graph takes 2.5 minutes to rebuild rather than
  seconds. Worth giving the tests their own graph path rather than letting them
  mutate `data/graph.sqlite`; that is a `tests/`-and-`config` change nobody has
  scoped yet.

---

## The one thing not to take away from this

That the corpus is now "3× more realistic". It is 2.66× larger and considerably
more varied, and the variety was chosen to match how ships actually behave off
the Indian west coast — but every hull in it was written by this repository. The
motion is generated, the companies are invented, the sanctions list is
fictional, and the radar is a simulation of a network that does not exist.

What genuinely improved is narrower and more useful than realism: the corpus can
now *fail* a detector. At a 19.8% base rate with twelve near-identical merchant
behaviours, most rules in this repository could not have been shown to be wrong.
At 8.9%, with nine decoys built to impersonate seven anomalies and four vessel
classes the type model has never seen, they can. That is the whole value here,
and it will show up as numbers going **down**.

