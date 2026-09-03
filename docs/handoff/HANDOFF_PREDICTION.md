# HANDOFF — route-aware forward projection

**Session:** route-aware prediction, following ADR-032 (e).
**Branch:** `claude/system-tech-overview-4w7amz`.
**Status:** built, measured on the synthetic corpus, **not promoted**.
**For the parent session.** Proposed ADR-042 text is at the bottom, ready to
paste into `DECISIONS.md`. I did not edit `DECISIONS.md`, `STATE.md`,
`COMMITS.md`, `ARCHITECTURE.md`, `CLAUDE.md` or `README.md`.

> **Every number in this document is on the synthetic corpus.** Its vessels are
> routed through one deterministic coastal corridor by `scenario/searoute.py`,
> so a flow field fitted to that traffic recovers the generator's own
> waypoints. The route-aware arm is flattered by construction and the gap
> between the two arms is an **upper bound** on the real one. Nothing here may
> be stated as a live figure (CLAUDE.md §4.6, §5). No dark vessel has been
> detected on real data; none of this changes that.

---

## 0. Plain-English glossary for this document

| Term | What it means here |
|---|---|
| **Dead reckoning** | "She keeps doing what she is doing." Take her last position, course and speed and run them forward in a straight line. |
| **Forward projection** | Where we predict a ship will be at some future moment, plus a circle of uncertainty around it. |
| **The cone** | That circle of uncertainty. It grows the further ahead you predict. |
| **Lead time** | How far ahead the prediction reaches — half an hour, three hours, six hours. |
| **Departure** | The ship turned out to be *outside* her own predicted circle. |
| **Route prior / flow field** | A learned map of "traffic arriving in this patch of sea on this heading tends to go **that** way next, at **that** speed". |
| **H3 cell** | One hexagon of the shared grid the whole system indexes positions on. The flow field is built at resolution 6, hexagons about 6 km across. |
| **Held-out hull** | A vessel the model was never allowed to learn from, so measuring on her measures prediction rather than memory. |
| **Precision** | Of the ships we flagged, what fraction really were doing something odd. ADR-004 requires ≥0.7 for anything an analyst sees. |
| **Plateau vs cliff** | A *plateau* is a range of thresholds where the flag rate changes gently — you can pick one and it means something. A *cliff* is a threshold where it jumps from "everything" to "nothing", and picking a point on it is fitting the corpus, not the world. |

---

## 1. What I found on arrival, and what I did about it

The previous agent's note read: *"The first route model barely beat dead
reckoning. Let me diagnose and rebuild it properly — the flow field needs to be
conditioned on heading, and carry the local speed profile."*

**The diagnosis was right, and the rebuild was already committed** in
`maritime_isr/tracks/route_prior.py`. What had not happened was (a) connecting
it, (b) proving the diagnosis, and (c) measuring it.

### 1.1 The route-aware path could not run at all

`projection.project_route_aware()` called
`route_prior.step_along_prior(distance_m=…)` and read `stepped.lat` /
`stepped.lon`. The rebuilt `step_along_prior` takes `lead_h=` and returns a
`SteppedPath` with no `.lat` at all. **Every route-aware projection raised
`TypeError`.** Fixed: the walk now runs the full lead and the calibrated
*advance factor* is applied as a fraction along the walked path — which is
exactly how `route_prior.calibrate()` fits it, so the number is applied the way
it was measured.

### 1.2 The heading-conditioning diagnosis, proved rather than asserted

I built the counterfactual: the same fitting code with every incoming course
pooled into one bin (the obvious way to build a flow field, and the way this
one was built first). Held-out hulls, median position error in nautical miles,
p90 in brackets:

| lead (h) | dead reckoning | unconditioned field | heading-conditioned field |
|---|---|---|---|
| 1.0 | 0.82 (5.86) | 1.45 (6.86) | 0.85 (5.84) |
| 3.0 | 4.70 (23.17) | 5.20 (24.07) | **2.91 (21.26)** |
| 6.0 | 16.56 (59.51) | 12.30 (57.64) | **9.92 (48.06)** |

The unconditioned field is **behind dead reckoning at one hour and at three**.
That is "barely beat dead reckoning", with a number on it, and it is the
mechanism the previous agent named: a cell holding a waypoint holds both the
inbound and the outbound course, and asked "which is hers" an unconditioned
field answers with the one nearest her current heading — the inbound one — and
steers her straight through the corner. The one place a route model must earn
its keep is the one place that field could not.

This is now pinned by `test_the_flow_field_answers_differently_by_heading` and
its counterfactual twin `test_an_unconditioned_field_cannot_do_that`, both
built on a synthetic two-way corridor where the correct answers are opposite.

### 1.3 The speed profile, ablated

Removing the lane speed profile, held-out hulls, median nm:

| lead (h) | with profile | without |
|---|---|---|
| 1.0 | 0.85 | 0.94 |
| 3.0 | **2.91** | 3.59 |
| 6.0 | 9.81 | 10.06 |

Real, worth keeping, and **second** to the heading conditioning rather than
equal to it. The module docstring said "half the win"; that overstated it and
is now corrected.

### 1.4 Two bugs I found and fixed while measuring

**`not_checkable` did not mean dead reckoning.** The contract says a walk with
under 50% prior coverage falls back to dead reckoning, and the basis string
said so — but the code still used the *partly bent* path. A hull crossing a
corridor was being swung onto it by the two cells she clipped. Measured
off-lane at three hours, the route arm was **worse than dead reckoning**:
median 4.72 nm against 3.01. Now `not_checkable` means dead reckoning all the
way down (position, path and the lane-spread term in the cone), and off-lane
harm at three hours drops from +57% to +11%. Pinned by
`test_a_not_checkable_projection_really_is_dead_reckoning`.

**`fit_own_history` merged re-entries and then emitted them twice.** A hull
leaving a cell and returning later had both visits averaged into one passage,
which was then emitted once per visit with the same `t_end` — which also
disarmed the causality cut in `lookup`, since both copies carried the *later*
end time. Now one passage per contiguous run. Pinned by an assertion in
`test_own_history_separates_the_two_directions_through_one_cell`.

### 1.5 Ablations that did **not** help, reported so they are not retried

* Gating the flow field to modes within 60° of her current course: **worse**
  (3h median 2.91 → 3.22). The big turns the field predicts are real.
* Finer walk step (3 min rather than 6): 2.91 → 2.79. Not worth doubling the
  cost.
* Faster turn-rate clamp (15°/min rather than 6): 2.91 → 2.88. Not the lever.
* **The hull's own history is the weakest of the four conditioners.** It was
  available for about one projection in sixteen on held-out hulls, and removing
  it moved the median by under 0.15 nm at every lead. Kept because it is right
  and because eight weeks is the wrong corpus to judge it on — a second call at
  a port visited once is the case it exists for — but it is **not** carrying the
  result and the module no longer implies it is.

---

## 2. What is now in the tree

| File | What |
|---|---|
| `maritime_isr/tracks/route_prior.py` | The model. Heading-conditioned flow field, own-history with a causality cut, the walk, per-motion-class calibration. *(Pre-existing from commit 7a0a3b6; I fixed two bugs, added `truncate_path` and `motion_class`, and corrected every unverified number in its docstring.)* |
| `maritime_isr/tracks/projection.py` | `project_route_aware()` now actually works; `project(model=…)` switches arms; dead-reckoned behaviour with `model=None` is **byte-identical** to before, which is what lets Phase 3 and the imaging layer keep calling it unchanged. |
| `maritime_isr/tracks/prediction_eval.py` | **New.** The permanent measurement harness — hull split, model fitting, the ADR-032 sweep, the position-error tables, the plateau test. Everything below is reproducible from it. |
| `tests/test_route_prior.py` | **New.** 29 tests: the conditioning and its counterfactual, causality, the turn clamp, the three-valued `route_support`, the physics cap on the route-aware arm, source-blindness, and the two bug fixes. |

`tracks/kalman.py` is **untouched**. `TrackState.uncertainty_radius_m` — the
Phase 3 association-gating contract — is unchanged, and the route-aware cone
uses the identical `MAX_FEASIBLE_SPEED_KN` cap through
`route_prior.physics_cap_m()`, pinned by
`test_a_route_aware_cone_is_still_capped_by_physics`.

### The model, as fitted

* Corpus: 232,581 landed AIS positions → **239 tracks, 238 hulls**.
* Split **by hull, never by track**, 60/40, seed 7: **142 fit hulls / 96 scored
  hulls, zero overlap.**
* Flow field: res 6, 8 heading octants, ≥20 observations **and ≥3 distinct
  hulls** per key → **602 cells, 1,196 modes** (median 2 modes per cell).
* Vessel type from `tracks/vessel_type.py` (motion only, hull-grouped split,
  coarse accuracy 0.70), folded to merchant / fishing / unclassified for
  calibration sample size. `unclassified` keeps its own calibration and is
  never given the merchant one.

Fitted advance factor — how far along the modelled path she is predicted to
have got:

| class | 0.5 h | 1 h | 3 h | 6 h |
|---|---|---|---|---|
| merchant | 1.00 | 1.00 | 1.00 | 1.00 |
| fishing | 0.95 | 0.90 | **0.10** | **0.10** |
| unclassified | 1.00 | 1.00 | 1.00 | 1.00 |

A trawler working a ground goes back over her own water: the distance she
travels is not the distance she makes good. Predicting her 45 nm along her
heading because she is doing 15 knots is not a cone problem, it is a wrong
prediction with a cone drawn round it.

Fitted cone growth (90th percentile of training error per hour of lead):
merchant **6.3 nm/h** route / 7.0 nm/h dead-reckoned; fishing 6.4 / 8.7;
unclassified 4.8 / 6.0. Note how far these are from
`CONE_GROWTH_M_PER_HOUR = 1852` (1.0 nm/h), the stated-but-never-fitted
constant ADR-032 swept against. That gap turns out to matter more than the
route model does — see §4.

---

## 3. Sweep 1 — ADR-032 reproduced

Whole corpus, plain `project_from`, the original constant 1 nm/h cone. Severity
is a post-filter on `radii_outside`, which is how ADR-032 swept it.

| lead (h) | min run | radii ≥ | departures | % of fleet | **ADR-032 said** |
|---|---|---|---|---|---|
| 0.5 | 2 | 1 | 1,892 | **98%** | 1,770 / 98% |
| 0.5 | 3 | 5 | 411 | **74%** | 377 / 73% |
| 1.0 | 3 | 5 | 803 | **89%** | 775 / 92% |
| 3.0 | 2 | 5 | 1,263 | **95%** | 1,177 / 97% |
| 3.0 | 2 | 20 | 27 | **9%** | 22 / 10% |

**Reproduced.** Counts differ slightly because the corpus now yields 239
eligible tracks rather than 209; every percentage is within 3 points. ADR-032's
finding stands exactly as written: 98% or, past a cliff, 9%, and nothing usable
between.

---

## 4. Sweep 2 — the two arms, side by side

Held-out hulls only (96), **identical code path**, identical gates
(`require_steady_leg`, `min_run`), identical stride. The only difference
between the arms is whether a route prior is passed. **Both arms now carry a
measured cone**, fitted on the fit hulls against the predictor that will run —
otherwise the route arm would be beating a predictor nobody calibrated.

| lead | min run | radii ≥ | **dead reckoning** departures / % fleet | **route-aware** departures / % fleet |
|---|---|---|---|---|
| 0.5 | 2 | 1 | 36 / 22% | 93 / 52% |
| 0.5 | 2 | 1.5 | 24 / 14% | 66 / 44% |
| 0.5 | 2 | 2 | 4 / 3% | 41 / 28% |
| 0.5 | 2 | 3 | 0 / 0% | 19 / 15% |
| 0.5 | 2 | 5 | 0 / 0% | 1 / 1% |
| 0.5 | 3 | 1 | 19 / 8% | 47 / 26% |
| 0.5 | 3 | 1.5 | 14 / 4% | 40 / 24% |
| 0.5 | 3 | 2 | 3 / 2% | 29 / 17% |
| 0.5 | 3 | 3 | 0 / 0% | 15 / 9% |
| 1.0 | 3 | 1 | 99 / 50% | 119 / 56% |
| 1.0 | 3 | 1.5 | 42 / 30% | 105 / 52% |
| 1.0 | 3 | 2 | 15 / 12% | 71 / 45% |
| 1.0 | 3 | 3 | 1 / 1% | 34 / 33% |
| 1.0 | 3 | 5 | 0 / 0% | 10 / 10% |
| 3.0 | 2 | 1 | 292 / 83% | 396 / 92% |
| 3.0 | 2 | 1.5 | 151 / 61% | 276 / 84% |
| 3.0 | 2 | 2 | 48 / 33% | 213 / 78% |
| 3.0 | 2 | 3 | 6 / 6% | 79 / 47% |
| 3.0 | 2 | 5 | 0 / 0% | 20 / 19% |
| 3.0 | 2 | 8 | 0 / 0% | 2 / 2% |

Everything at radii ≥ 8 and above is 0% in both arms and is omitted.

Two things to read here, and neither is the one you would hope for.

**The route arm flags *more*, not less.** Its cone is tighter (6.3 vs 7.0 nm/h
for a merchant) *and* its prediction is better centred, so more of the fleet
lands outside it. A better predictor with an honestly calibrated cone does not
produce fewer alerts; it produces the same alert rate at a smaller physical
distance. That is the point §5 turns on.

**Compare either arm here against §3 and it is obvious what actually moved.**
ADR-032's 98% was measured against a 1 nm/h cone. The measured rate is six to
seven. Most of the difference between "98% of the fleet" and "22% of the
fleet" is the cone being sized honestly — nothing to do with route-awareness.

---

## 5. Is there now a plateau?

**Yes, and it does not help.** This is the part where the honest answer and the
hoped-for answer part company.

A plateau, tested mechanically (two or more adjacent severity steps whose
flagged fraction stays between 5% and 60% and does not halve between steps):

| arm | lead | min run | plateau? | the run (radii → % fleet) |
|---|---|---|---|---|
| dead reckoning | 0.5 | 2 | yes | 1 → 22%, 1.5 → 14% |
| route-aware | 0.5 | 2 | yes | 1 → 52%, 1.5 → 44%, 2 → 28%, 3 → 15% |
| dead reckoning | 0.5 | 3 | no | 1 → 8% only |
| route-aware | 0.5 | 3 | yes | 1 → 26%, 1.5 → 24%, 2 → 17%, 3 → 9% |
| dead reckoning | 1.0 | 3 | yes | 1 → 50%, 1.5 → 30% |
| route-aware | 1.0 | 3 | yes | 1 → 56%, 1.5 → 52%, 2 → 45%, 3 → 33% |
| dead reckoning | 3.0 | 2 | no | 2 → 33% only (61% above it, 6% below) |
| route-aware | 3.0 | 2 | no | 3 → 47% only |

So the cliff ADR-032 found is genuinely gone at short and medium leads: the
severity axis now *grades*, and there are thresholds flagging 9–28% of the
fleet rather than 98% or 0%. **That is a real change, and it should be recorded
as one.**

But it is a change produced by **calibrating the cone**, not by
route-awareness — the dead-reckoning arm grades too, and grades *lower*. And a
graded response is a necessary condition for a useful threshold, not a
sufficient one. The sufficient condition is whether the ships on the flagged
side are the interesting ones. They are not: see §7.

At three hours — the lead an operator would actually want — there is still no
plateau in either arm.

---

## 6. Position error, new vs baseline, with spread

Held-out hulls, both arms scored on **exactly the same samples** (same origin
fixes, same target fixes, same silence rule). Nautical miles.

### All samples

| lead (h) | arm | n | p10 | p50 | p90 | mean |
|---|---|---|---|---|---|---|
| 0.5 | dead reckoning | 2,406 | 0.06 | 0.23 | 2.01 | 0.74 |
| 0.5 | route-aware | 2,406 | 0.05 | 0.24 | **1.89** | 0.71 |
| 1.0 | dead reckoning | 2,345 | 0.12 | 0.71 | 7.58 | 2.43 |
| 1.0 | route-aware | 2,345 | 0.09 | 0.79 | **5.37** | 1.95 |
| 3.0 | dead reckoning | 2,304 | 0.20 | 3.33 | 27.21 | 9.79 |
| 3.0 | route-aware | 2,304 | 0.15 | **2.46** | 25.70 | 8.89 |
| 6.0 | dead reckoning | 2,118 | 0.14 | 9.75 | 55.65 | 21.72 |
| 6.0 | route-aware | 2,118 | 0.14 | **5.59** | 54.98 | 19.86 |

### The same thing as a change, by slice

| slice | lead (h) | p50 route / DR | Δ p50 | p90 route / DR | Δ p90 |
|---|---|---|---|---|---|
| all | 0.5 | 0.24 / 0.23 | +7% | 1.89 / 2.01 | −6% |
| all | 1.0 | 0.79 / 0.71 | +12% | 5.37 / 7.58 | −29% |
| all | 3.0 | 2.46 / 3.33 | **−26%** | 25.70 / 27.21 | −6% |
| all | 6.0 | 5.59 / 9.75 | **−43%** | 54.98 / 55.65 | −1% |
| merchant | 0.5 | 0.16 / 0.15 | +8% | 1.51 / 1.70 | −11% |
| merchant | 1.0 | 0.40 / 0.36 | +9% | 5.27 / 5.88 | −10% |
| merchant | 3.0 | 1.83 / 2.91 | **−37%** | 24.67 / 26.89 | −8% |
| merchant | 6.0 | 4.80 / 9.79 | **−51%** | 58.82 / 58.81 | +0% |
| fishing | 0.5 | 0.82 / 0.81 | +2% | 2.19 / 2.31 | −5% |
| fishing | 1.0 | 2.04 / 2.17 | −6% | 4.10 / 8.67 | **−53%** |
| fishing | 3.0 | 3.67 / 3.72 | −1% | 27.75 / 27.76 | −0% |
| fishing | 6.0 | 5.63 / 5.63 | +0% | 51.53 / 51.84 | −1% |
| unclassified | 3.0 | 4.74 / 6.80 | −30% | 23.10 / 24.48 | −6% |
| unclassified | 6.0 | 16.25 / 24.05 | −32% | 52.15 / 57.91 | −10% |
| **on lane** | 1.0 | 0.89 / 0.88 | +1% | 4.97 / 7.85 | **−37%** |
| **on lane** | 3.0 | 2.30 / 3.47 | **−34%** | 25.58 / 27.46 | −7% |
| **on lane** | 6.0 | 4.95 / 9.27 | **−47%** | 54.32 / 55.93 | −3% |
| **off lane** | 1.0 | 0.45 / 0.42 | +6% | 6.10 / 6.99 | −13% |
| **off lane** | 3.0 | 3.35 / 3.01 | **+11%** | 26.62 / 26.23 | +1% |
| **off lane** | 6.0 | 9.92 / 10.63 | −7% | 56.27 / 54.17 | +4% |

*On lane* means the flow field had a supported mode for her cell and her
heading **at the moment the prediction was made** — a fact the predictor holds
before it predicts. Nothing here reads `scenario_truth`.

**What the breakdown shows that the aggregate hides:**

1. **The win is on-lane and at long lead, and it is large there.** Merchant on
   lane at six hours: median error halves. That is a genuinely better answer to
   "where do you think she is".
2. **It is a median win, not a tail win.** At three hours the median falls 26%
   and the p90 falls 6%; at six hours, 43% and 1%. **A detector lives on the
   tail**, because the cone is sized at the 90th percentile. A model that
   predicts the ordinary vessel much better and the awkward one no better is a
   better *assertion* and not a better *detector*. This single row is the
   quantitative core of the negative result.
3. **Off lane it is still slightly worse** (+11% median at three hours, down
   from +57% before the `not_checkable` fix). The residual is walks that *start*
   off-lane, pick up support part-way, and clear the 50% coverage floor. Worth
   another look; see §9.
4. **Fishing gets almost nothing from the road and a lot from the advance
   factor.** Her one-hour p90 halves (8.67 → 4.10) because the calibration
   stopped predicting her 15 nm along a heading she was never going to hold.
   Her three- and six-hour numbers are unchanged: a vessel working a ground is
   not on anybody's road.
5. **At 0.5 h the route arm is very slightly *worse* at the median** (+7%). Over
   half an hour a hull holds her course and dead reckoning is close to optimal;
   the flow field can only add noise. Reported rather than smoothed over.

---

## 7. The decisive measurement — precision against the answer key

Held-out hulls, scored against `scenario_truth`. **Generous by construction:** a
flagged hull counts as correct if she carries *any* injected anomaly, even one
that has nothing to do with her track (a bad IMO check digit, say). Base rate:
14 of 96 held-out hulls carry one, **0.15**.

| arm | lead | min run | radii ≥ | hulls flagged | of which anomalous | precision | lift |
|---|---|---|---|---|---|---|---|
| dead reckoning | 0.5 | 2 | 1 | 21 | 7 | 0.33 | 2.3× |
| dead reckoning | 0.5 | 2 | 2 | 3 | 2 | *0.67* | *4.6×* |
| dead reckoning | 1.0 | 3 | 1 | 48 | 7 | 0.15 | 1.0× |
| dead reckoning | 1.0 | 3 | 2 | 12 | 3 | 0.25 | 1.7× |
| dead reckoning | 1.0 | 3 | 3 | 1 | 0 | *0.00* | *0.0×* |
| dead reckoning | 3.0 | 2 | 1 | 80 | 10 | 0.12 | 0.9× |
| dead reckoning | 3.0 | 2 | 2 | 32 | 3 | 0.09 | 0.6× |
| dead reckoning | 3.0 | 2 | 3 | 6 | 1 | *0.17* | *1.1×* |
| route-aware | 0.5 | 2 | 1 | 49 | 9 | 0.18 | 1.3× |
| route-aware | 0.5 | 2 | 2 | 27 | 6 | 0.22 | 1.5× |
| route-aware | 0.5 | 2 | 3 | 14 | 4 | 0.29 | 2.0× |
| route-aware | 0.5 | 2 | 5 | 1 | 1 | *1.00* | *6.9×* |
| route-aware | 1.0 | 3 | 1 | 53 | 8 | 0.15 | 1.0× |
| route-aware | 3.0 | 2 | 1 | 88 | 11 | 0.12 | 0.9× |
| route-aware | 3.0 | 2 | 2 | 75 | 7 | 0.09 | 0.6× |
| route-aware | 3.0 | 2 | 3 | 43 | 5 | 0.12 | 0.8× |

*Italic rows flag fewer than seven hulls.* A precision computed on one to six
flagged hulls is noise — "1.00" on a single hull is one ship, not a capability.
Reading those rows as a result would be exactly the "threshold sitting on a
cliff" failure ADR-032 already caught once, and they are printed only so nobody
finds them later and thinks they were hidden.

**On every operating point that flags a meaningful number of hulls, precision
runs 0.09 to 0.33 against a base rate of 0.15.** At one hour and at three hours
the rule is **at or below chance in both arms**. ADR-004 requires **≥0.7**.

---

## 8. Verdict

**Do not promote forward projection to a suspicion factor.** Route-awareness
does not change the answer, and `test_projection_is_not_a_registered_suspicion_factor`
must stay exactly as it is. `assistant/catalog.py` is unchanged and has no
`departure` entry.

The reasoning, in the order it should be read:

1. **Precision is at chance.** 0.09–0.33 against a base rate of 0.15, under a
   deliberately generous definition of a hit, at every operating point with
   enough hulls to measure. ADR-004's bar is 0.7. This alone settles it.
2. **The improvement is in the median, not in the tail.** −26% to −43% at p50,
   −1% to −6% at p90. The cone is sized at p90. A predictor that improves where
   the cone is not, cannot tighten the cone, and therefore cannot discriminate
   better — which is the mechanism, not just the outcome.
3. **The route arm flags *more* of the fleet than dead reckoning**, at every
   operating point in §4 — because a better-centred prediction inside a tighter
   cone leaves the same fraction outside. The alert-fatigue problem ADR-004
   exists to prevent is not improved by this work; it is very slightly worse.
4. **The plateau that did appear is not evidence for promotion.** It comes from
   calibrating the cone honestly, it appears in the dead-reckoning arm too, and
   it grades a population whose flagged side is at chance. A graded axis over a
   useless signal is a smoother way of being wrong.

**And an argument against reading this as a failure.** ADR-032 named the fix,
the fix was built, and the fix works *as a predictor* — median error at six
hours nearly halves on lane. The refusal to promote is now supported by a
measurement of the built thing rather than by an argument about a thing not
built. That is a stronger position than ADR-032 held, and it closes the
"but you never tried route-awareness" objection permanently.

**What the projection is now good for** — the honest uses ADR-032 named, each
measurably better:

* **An assertion an operator can see.** `Projection` now carries `path` (the
  curve, truncated to end at the predicted position), `route_support`,
  `prior_coverage`, `cone_modulation` and a `basis` string naming all of it.
* **Bridging a gap along the road** rather than along a heading. This is the
  imaging-opportunity use (ADR-026) and it is where the −47% on-lane six-hour
  median actually pays.
* **Expected-versus-actual, called up on a subject already suspicious for
  another reason.** A comparison an analyst asks for is a different and far
  safer thing than a detector that asks on its own.

---

## 9. Open questions for `STATE.md`

* **OQ-pred-1. Off-lane is still slightly worse than dead reckoning** (+11%
  median at three hours). The cause is walks that start off-lane, acquire
  support part-way, and clear the 50% coverage floor. Candidate fix: require
  support at the *origin* cell, not just averaged over the walk. Not attempted
  here because it is a model change and I had already spent the measurement
  budget; it should be measured, not assumed.
* **OQ-pred-2. Is the plateau in §5 an artifact of the deterministic corridor?**
  The scenario routes every vessel down one lane. Real coastal traffic is far
  more dispersed, the flow field will be thinner, and coverage will be lower.
  My honest expectation is that the plateau narrows and the route/DR gap shrinks
  toward zero on real data. Must be re-measured on the deploy host.
* **OQ-pred-3. Own history is untested on the case it exists for.** Eight weeks
  holds too few repeat port calls. Worth revisiting once the corpus spans
  months, not before.
* **OQ-pred-4. Should the route prior be landed?** `RoutePrior.as_rows()`
  produces conformed rows with the full provenance envelope, but nothing writes
  them. Landing it would make the flow field inspectable in the UI the way
  `baselines` is. Not done: it is a pipeline change and outside this session's
  ownership.

---

## 10. For the UI agent

Nothing in the UI should imply projection is a detection. It is an assertion.

* **The predicted curve, not the endpoint.** `Projection.as_dict()["path"]` is
  a list of `[lat, lon]` that already ends at the predicted position. Draw it as
  a line with the cone at its end. A straight line where the model predicted a
  turn is the single most misleading thing this surface could show.
* **`route_support` is three-valued and must be visible as three states**, not
  two: `own_history` ("steered by her own previous passages"), `fleet_prior`
  ("steered by the learned lane"), `not_checkable` ("no lane here — this is
  dead reckoning"). Rendering `not_checkable` the same as the other two would
  make an operator trust the weakest prediction as much as the strongest, which
  is the whole reason the field exists. Use the existing light/dark CSS custom
  properties; do not hardcode a colour.
* **`prior_coverage`** (0–1) is how much of the walk had a road under it, and
  **`cone_modulation`** is how much wider or narrower than the calibrated cone
  this one was drawn, with `basis` carrying the sentence explaining why. Both
  belong in the detail panel, not on the map.
* **Expected vs actual** is the useful comparison: her predicted position and
  cone at time *t*, and where she actually was. Show it **on a vessel the
  operator already opened**, never as a queue or a list — there is no
  "departures" list to build, and building one would ship a rule measured at
  chance.
* **The caveat travels with the number.** Anything sourced from this model
  should carry `RoutePrior.caveat()` or the §0 warning: synthetic corpus,
  optimistic by construction, not measured on live data.

---

## 11. Proposed ADR-042 — paste into `DECISIONS.md`

> **Not applied by me.** Parent session: review, then paste. It follows the
> house format and refers only to things that exist in the tree.

---

## ADR-042 — Route-aware forward projection: built, measured, and still not a suspicion factor *(Accepted)*

**2026-09-02. Follows ADR-032 (e).**

**Context.** ADR-032 refused to promote forward projection and, unusually, named
the fix it was declining to build: *"prediction has to be route-aware, and the
zone layer's customary lanes (ADR-030) are what a corridor model would be fitted
to."* That work has now been done. This ADR records what was built, what it is
worth, and why the refusal stands anyway — because a refusal that survives the
fix it asked for is worth more than the original refusal, and because the next
person to have this idea is entitled to the measurement rather than the
argument.

**Decision — five parts.**

**(a) The flow field is a transition model keyed on heading, not a histogram of
courses.** The obvious construction — store, per H3 cell, the courses traffic
steers there — was built first and **measured worse than dead reckoning**
(held-out hulls, median error 1.45 nm against 0.82 at one hour, 5.20 against
4.70 at three). The cause is structural, not a tuning miss: a cell containing a
waypoint holds both the inbound and the outbound course, and asked "which of
these is hers" an unconditioned field returns the one nearest her present
heading — the inbound one — and steers her straight through the corner. The one
place a route model must earn its keep is the one place that construction
cannot.

So the key is **(cell, incoming course octant)** and the value is where traffic
*went next*: the bearing made good over the following six minutes and the speed
made good at it. Res 6 (~6 km), eight octants, and a key speaks only with ≥20
observations from **≥3 distinct hulls** — the observation floor alone is
clearable by one densely-reporting vessel turning her private routing into
"what traffic does here", which is the chip-versus-scene hazard one domain
along. On the corpus: **602 cells, 1,196 modes.** The conditioning and its
counterfactual are both pinned in `tests/test_route_prior.py`, on a synthetic
two-way corridor where the correct answers are opposite.

**(b) Four conditioners, and their contributions are unequal and measured.**
The flow field carries the result. The **speed profile** — her own speed scaled
by how the lane's speed here compares with the lane's speed where she started —
is second and real (3 h median 3.59 nm without it, 2.91 with). **Vessel type**
from `tracks/vessel_type.py`, motion-only so a radar contact gets the same
answer, matters most for vessels working a ground: the fitted advance factor is
1.0 for a merchant at every lead and **0.1 at three hours for a fishing
vessel**, and applying the merchant number to a trawler is not a cone error, it
is a wrong prediction with a cone drawn round it. The hull's **own history** is
the weakest: available for about one projection in sixteen and worth under
0.15 nm at every lead. It is kept because eight weeks is the wrong corpus to
judge it on and because its causality cut — only passages that *ended* before
the prediction was made — is the kind of guard that is far easier to build now
than to retrofit.

**(c) `not_checkable` means dead reckoning all the way down.** A walk with
under 50% prior coverage returns `not_checkable`, and the projection then uses
the straight line — position, drawn path, and the lane-tightness term in the
cone. This was a *bug* when found: the code returned the part-bent path while
the basis string claimed dead reckoning, and a hull merely crossing a corridor
was swung onto it by the two cells she clipped. Measured off-lane at three
hours, that made the route arm **worse than the dead reckoning it replaced**
(median 4.72 nm against 3.01). `route_support` is three-valued —
`own_history` / `fleet_prior` / `not_checkable` — for the same reason every
other check in this system is: a prediction that quietly fell back and
presented itself as route-aware is the worst of the three, because a consumer
would trust it more than it deserves.

**(d) The measurement is a module, not a script.**
`tracks/prediction_eval.py` fits the model on a **hull-grouped** split (142 fit
hulls, 96 scored, zero overlap), runs the ADR-032 sweep against either arm
through the identical code path, and produces the position-error and plateau
tables. Both arms carry a cone fitted against the predictor that will actually
run — the alternative is a tuned model beating an untuned one and calling the
difference route-awareness. On-lane and off-lane are decided by whether the
flow field had support **at the moment of prediction**, a fact the predictor
holds before it predicts; nothing in the harness reads `scenario_truth` except
the precision measurement, which is reported as such.

**(e) Forward projection is still NOT a suspicion factor, for three new
reasons.** ADR-032's reason was that a tight cone flags everybody. That reason
is now *obsolete* and should not be quoted: it was measured against a cone
growing at a stated-but-never-fitted 1 nm per hour, where the measured rate is
six to seven. Calibrate the cone honestly and the severity axis grades smoothly
instead of falling off a cliff — a genuine plateau, at short and medium leads,
in **both** arms. The reasons it still fails are different and worse:

1. **Precision against the scenario's own answer key is at chance.** On held-out
   hulls, counting a flag as correct if the hull carries *any* injected anomaly:
   **0.09 to 0.33 against a base rate of 0.15**, at every operating point that
   flags enough hulls to measure. ADR-004 requires ≥0.7.
2. **The improvement is in the median and not in the tail.** Three-hour median
   error falls 26% and the ninetieth percentile falls 6%; at six hours, 43% and
   1%. The cone is sized at the ninetieth percentile, so a model that improves
   where the cone is not cannot tighten the cone and cannot discriminate better.
   It is a better **assertion**, which is a smaller claim than a better
   detector and is the claim this ADR makes.
3. **The route arm flags *more* of the fleet than dead reckoning at every
   operating point**, because a better-centred prediction in a tighter cone
   leaves the same fraction outside. Alert fatigue is not improved by this work.

So `assistant/catalog.py` still has no `departure` entry and
`test_projection_is_not_a_registered_suspicion_factor` still stands. What the
projection is for is unchanged and now better: an assertion an operator can see
with the predicted **curve** attached, a gap bridged along the road rather than
along a heading (the on-lane six-hour median halves, which is where this pays),
and an expected-versus-actual comparison called up on a subject already
suspicious for another reason.

**Every figure here is on the synthetic corpus**, whose vessels are routed
through one deterministic coastal corridor by `scenario/searoute.py`. A flow
field fitted to that traffic recovers the generator's own waypoints, so the
route-aware arm is flattered by construction and the gap between the arms is an
upper bound on the real one. The honest expectation for live data is that the
flow field is thinner, coverage lower, and the gap smaller. Re-measure on the
deploy host before any of it is stated externally.
