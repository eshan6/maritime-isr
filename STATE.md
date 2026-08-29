# STATE.md — Living Build Status

**This is the memory between sessions.** It is the one file that changes every
session. `CLAUDE.md`, `ARCHITECTURE.md`, `DECISIONS.md` are stable contracts;
**this** file tracks reality.

> **Claude Code: at the end of every session, update this file** — move units
> between status buckets, record what was verified vs assumed, log anything now
> broken, and set "Next up." If you don't update it, the next session starts blind.

**Last updated:** 2026-08-29 (sixth session) — **Area 5 fires for the first
time: 2 alerts, both authored, no false positive. Area 4 unchanged at 3/2/1.**

**Area 5 was silent because the corpus held no lie.** `DECLARED_CLASS_OVERRIDES`
is written in cast keys (`eo_false_class`); both readers look it up by entity id
(`vessel:eo_false_class`). Merged un-keyed, every lookup missed, so every
authored liar broadcast the truth about herself and the rule was correct to say
nothing. **Third time this area has been silenced by two id spaces for one hull**
— ADR-037 records the other two. `build_vessels` now re-keys and *raises* on an
override that names nobody, and a unit test checks the property at the cast in a
second rather than after a twenty-minute pipeline run.

**Then four more, each hiding the next.** (1) The camera refuses a hull of
unknown size, and 44% of candidate positions carried no length — an AIS track
has none, only radar measures it — so the feed handed the scheduler an
unmeasurable hull in half of O1's slots and the camera declined to look at a
270 m tanker 5.5 km offshore in clear daylight. Length is a property of the
hull, so it is now merged across her tracks. (2) The loop closed at stage
boundaries, not at the classifier's latency: O1's first look contradicted her at
08:15 and the scheduler did not know until the stage ended, reset her staleness
clock on the look it had just taken, and dropped her below the floor for six
slots she was in clear view. Stages are now one slot. (3) The urgency lookahead
stopped at the plan's last slot, which at one slot a stage made every candidate
maximally urgent — fixed, and urgency is now scaled by information gain, because
nothing is lost by missing a question already answered. (4) The pipeline reset
the staleness clock at the boundary even for an unsettled contradiction, undoing
inside `plan_cueing` the fix that exists to supply the corroborating look.

**The one alert the area used to produce was a false positive**, on an honest
stationary hull that collected **130 looks** in one arc; two of them agreed on
`container` and the rule accused her. "Two agreeing looks" is a claim about a
rate (one in 100,000 **per pair**) enforced as an absolute, and 130 looks hold
8,385 pairs. Fixed at both ends: `MIN_CONTRADICTED_SHARE = 0.5` (the agreeing
looks must be a majority of the looks that decided anything) and a bound of 3
classifiable looks per settled verdict in `eo.cue`, which also returns the
wasted slots. **No threshold was moved to make a count come out right** —
`MIN_MISMATCH_QUALITY` (0.45), `MIN_MISMATCH_CONFIDENCE` (0.62),
`PRIORITY_FLOOR` (0.30) and `MIN_CORROBORATING_CAPTURES` (2) are all untouched.

**Capacity.** Cameras sat idle in 115,028 slots while 46,527 reachable targets
were refused for scoring under the floor. The floor is an opportunity cost, so
below-floor targets now stay in the same global assignment charged a penalty
larger than any value spread — they take only a camera nothing else could use,
they are marked `opportunistic` on the tasking, and utilisation is reported
split so a fill cannot inflate the number that measures demand. Utilisation is
still **4.5%**, and that is honest: 89,480 deferrals are `no_camera_in_reach`,
which is a 20 km lens against the Arabian Sea, not a scheduling defect.

**Three corpus-accounting tests had been skipping silently.** They asked
`api.reader` for `scenario_truth`, which ADR-019 deliberately does not register
there, so "no such table" read as "no corpus" on a corpus that had the answer
key all along. They read the partitions directly now and pass.

**All figures here are on the synthetic suite** (seed 7). No camera exists, no
image has ever been examined, and nothing in Area 5 has run on real data or on
the Oracle VM, which is still not provisioned (CLAUDE.md §5).

**Extractor hardening (Area 4).** Measured against labels and value formats the
generator never writes — the corpus shares one synonym table with the extractor,
so any accuracy figure taken on it is circular. On that independent fixture set:
**50.0% -> 99.1% correct, and misattributions 2 -> 0.** The two it was getting
wrong are the failure that matters, a value landing on the *wrong* field: it read
`crew_count` as "Owner: BLUEWATER SHIPPING LTD" off a label whose value was empty
followed by the next label. Corpus-side extraction is unchanged (293 of 295
resolved, 0 unreadable), which is the right outcome: robustness on unseen forms
bought without disturbing what already worked. Full suite green.

**Two landing defects, both latent since Area 4 shipped.** `stamp_envelope`
enforced bare equality on `synthetic-scenario` while `graph.store` and
`scenario.validate` matched a `synthetic-scenario:` prefix; the three disagreed.
Area 4 survived only because it set `is_synthetic` *after* stamping and so never
tripped the check — two errors cancelling, which is not agreement. Area 5 set it
before and the pipeline died with 487 captures in hand. Both fixed; the invariant
now actually guards the drift it exists for.

---

**Fourth session** — **Area 5 built: the electro-optical loop, and six defects
that only counting exposed.**

**0. Area 5 — automating the electro-optical loop (ADR-037).** The requirement
names four things and only one of them needs pictures: capture without operator
intervention, bind the image to a track, classify against a library, alert on
the disagreement. Three are control and fusion logic, so those are what got
built, and the classifier sits behind an interface as the brief instructs.

**The cueing scheduler is the centre of it.** Given a picture with far more
tracks than there are cameras, it decides which track each camera is pointed at
and when, as a **global assignment per slot** — `linear_sum_assignment` over
cameras × candidates, the same tool the association core uses, because greedy
per-target matching double-books one station's camera onto the three most
suspicious contacts in its arc and leaves the rest idle (CLAUDE.md §6, one
domain along). Priority is `0.55 x suspicion + 0.30 x information gain + 0.15 x
staleness`, multiplied by expected image quality from range, aspect, light and
visibility; a closing observation window multiplies the *cost* rather than the
priority, because being about to leave does not make a ship more suspicious, it
makes deferring her more expensive. Every tasking carries the arithmetic and a
sentence; every candidate that was worth a camera and did not get one carries
the reason — `outranked`, naming who took the camera, or `no_camera_in_reach`,
naming the nearest station and the range.

**There is no camera and no image exists.** Every capture is simulated through
the `CaptureSource` seam — in this build by `scenario/eo.py`, which is the world
generator and is entitled to know what is out there; in a deployment by a driver
talking to hardware. Every capture row carries `capture_mode='simulated'`, an
empty `image_ref` and the model's own provenance string saying it has never seen
an image. Nothing under `eo/` may read ground truth and a test asserts it.

**Six defects, four visible only as numbers** (full detail in ADR-037):

* **The confidence did not track accuracy**, so 84% of good images were refused
  — the model picked the right class 96% of the time while reporting a mean
  confidence of 0.35, below its own bar. The softmax temperature is now fitted
  to measured accuracy.
* **The observation noise had no floors**, so the model "could" tell a Suezmax
  from an Aframax and published an eleven-class vocabulary — the long list of
  classes it guesses at that the brief warns against, reached by flattering the
  sensor.
* **The vocabulary merge asked the wrong question.** The 25% type-level merge
  inherited from `tracks.vessel_type` is right for describing a contact and
  wrong for accusing a hull: `product_tanker`↔`bulker` confusion sat at 12-15%,
  under the bar, and the rule produced **22 false accusations in 1,500 honest
  looks**. A second merge pass unions any pair confused across an AIS ship-type
  family boundary above 5%; false accusations fell to **0-0.36%**.
* **A merged label was read as bounding nothing**, which silently discarded the
  brief's own headline example — `merchant` cannot say which family she is but
  it says which she is *not*, and a trawler-declaring hull imaging as a merchant
  is contradicted.
* **The rule read one model's label in another model's vocabulary** and accused
  **36% of an honest fleet** when a second classifier was swapped in. Invisible
  with one model, immediate with two — which is why the swap test earns its
  place.
* **Sister ships are not separable in six numbers.** Same-hull distances (median
  0.12) overlap the closest cross-hull pair (0.11), so re-identification works
  only where a hull is distinctive and the margin test refuses otherwise.

**WHAT IS NOT WORKING: `imagery_type_mismatch` fires 0 times on the corpus.**
Stated first because everything above describes a loop that runs end to end and
still produces no finding. The pipeline is green, 1,045 captures land, 450 carry
a type claim, and three of the four imageable O-hulls now get a usable
photograph — but the authored contradiction O1 does not, so the alert count is
zero where the answer key says one.

Traced, not guessed. O1 is imaged twice, at 08:05 and 08:15, at an *identical*
8.588 km and 0.29 quality against a 0.35 classify floor. Her closest approach is
08:32 at 5.46 km. The scheduler evaluates a frozen position for her and never
sees her close. It is not the cadence: the interval was cut from 30 to 10
minutes and she was evaluated at the same stale fix. It is not the priority
floor: she computes to 0.3150 against 0.30 and is eligible. It is not the camera
arc: SYN-POR masks 10-100°, she approaches on 206-245°. **It is the candidate
feed** — `run_eo_loop` samples one candidate per three track fixes from her
*correlated radar track*, and those positions stop before her close pass. That
is the next thing to fix and it is a join, like every other defect in this area.

**Nothing was tuned to move this number.** The 0.30 floor, the 0.55 suspicion
weight and the 0.35 quality floor are all untouched. Lowering any of them fires
O1 tonight and says nothing about hulls nobody authored.

**Corroboration is the fix for the residual error, not a higher bar.** A wrong
label and a right one look the same from inside, so raising the confidence gate
suppresses true positives just as fast. Two looks at different ranges, aspects
and light are close to independent, and the loop supplies the second for free
because a contradicted hull keeps a high information gain. They must agree on
the *family*: a hull called a tanker once and a trawler once was photographed
badly twice.

---

**Last updated:** 2026-08-25 (third session) — **Area 4 built: the arrival
notification inbox, and eleven defects that all presented as silence.**

**0. Area 4 — pre-arrival notifications (ADR-036).** The brief's Area 4 is the
only part of this system whose input is a *mailbox* rather than a feed: PDFs,
scanned faxes with no text layer, Word forms that are sometimes tables,
spreadsheets whose form starts three rows down, and the structured portal feed
the requirement asks the system to stay compatible with. All five now land as
one record shape, every value carrying the passage it was read from and a
locator an analyst can put a finger on (`page 1 (scanned)`, `PANS!A5`,
`table 2 row 3`), at a confidence that is *earned* — 1.0 for a spreadsheet
cell, 0.97 for a PDF text layer, whatever tesseract reports for OCR.

On the synthetic suite: **295 documents, 292 resolved to a hull, 0 unreadable,
3,406 fields extracted.** Three paperwork rules over that, three-valued as in
ADR-035.

**The counting is what found the bugs.** The unit suite was green and the stage
reported 3,107 fields read at 10.6 of 11 per document — and the alert queue held
24 `notification_unmatched` against 1 authored, 10 `arrival_without_notification`
against 1, and 8 `paperwork_contradiction` against 2. Nine defects came out, in
three waves, and **not one of them raised an error**:

* **The filing time came from the file's mtime.** Every rule measures a
  declaration against the track *as at filing*, so with a timestamp a month past
  the corpus the two strongest checks looked before a window that had not
  happened and returned "not checkable" for all 292 documents. Two of three
  checks were dead corpus-wide and nothing said so. The date is now written into
  the document and read like any other field.
* **The resolver read one identity table.** A form declaring IMO 1001661
  matched nothing, dropped to the name rung, met a transposed name, and was
  reported as naming an unidentifiable ship — while the hull had been
  broadcasting that exact IMO in AIS message 5 all month. A gap in one table,
  reported as a gap in somebody's paperwork.
* **The background corpus contradicted itself at random**, drawing "Ballast —
  no cargo" against an independently drawn draught.
* **Arrivals in the first 72 hours were judged** against a filing window
  predating the record, and unnamed offshore stops were charged with owing
  paperwork nobody owed.
* **Both rules joined a declaration to the wrong event** — the arrival window
  matched the next call by *time* rather than the call at the *declared port*
  (30 alerts, every one "berthed 31-65 hours early", a distribution with no
  honest reading); the last port compared against a gazetteer pin, then against
  the latest prior call rather than *any* prior call.
* **The two halves of one gap both fired**, putting a second alert on P6's
  decoy.
* **Three decoys declared last ports their own voyages contradicted**, and the
  authored scenarios timed their filings off a nominal `week()` value while the
  port-call builder berthed them up to two days elsewhere — producing
  *pre*-arrival notifications filed after the arrival.
* **The corpus filed forms declaring a last port the vessel had not yet
  reached**, because the 24-96 h lead was drawn without reference to her
  previous call.
* **The OCR confusion table folded `|` to `I` but left `L` alone**, so
  "Vesse| Name" and "Vessel Name" squashed differently and the most common label
  in the corpus failed to match on exactly the format the fold exists for.
* **Adding one hull to the cast re-rolled the whole background fleet.** The cast
  tuples are ordered so an addition never renumbers an existing hull — but
  minting still draws from `world.rng`, and every scenario drawing after
  `build_vessels` then got different numbers. The vessel-type model's coarse
  accuracy fell from above its 75% floor to 65%, not because the new hull taught
  it anything but because its training data had changed underneath it. Late
  cast additions now mint from a derived stream (`cast.LATE_ADDITIONS`), and
  accuracy is back above the floor.
* **The synthetic/connector provenance clash.** `arrival_notification` is the
  first table landed by a real connector rather than by the scenario writer, and
  the corpus invariant demanded every synthetic row carry
  `source_id='synthetic-scenario'` — which would have forced the connector to
  lie about which source read the row. It now carries
  `synthetic-scenario:pans-inbox`, the prefix convention `graph.store` already
  used for exactly this, and the validator matches on the prefix.

Result on the synthetic suite: `paperwork_contradiction` 8 → 33 → **2-3**,
`notification_unmatched` 24 → **3**, `arrival_without_notification` 10 → **1**.
No threshold was moved to get there; every fix is to a join, a corpus
inconsistency, or a value read from the wrong place.

---

**Previous session** — **every open item closed, and the biggest one was a
capability the brief called "the strongest and simplest suspicion factor
available".**

**1. Declared destination and ETA — built (ADR-035).** This was the clearest
thing the brief asked for that the system did not have, and the reason was
upstream of any rule: **nothing landed a declared destination.** AIS message 5
carries it, the generator emitted no message 5, and the live connector filtered
to position reports and dropped the rest. So the column came first — a canonical
`VoyageDeclaration`, a connector that parses `ShipStaticData`, and a generator
that declares a destination on **every ordinary port call, honestly**, because a
rule tested only against liars measures recall and says nothing about precision.
**3,091 declarations over 131 hulls** is the denominator. Two rules over it: an
arrival no hull could make (arithmetic, like the IMO check digit) and a
destination she never once steered towards (behavioural, gated lower). Result:
**2 alerts, both of them the two hulls written to lie, zero false positives.**

Getting there took four defects out, and every one made a rule quieter or
louder without saying so:

* **43 alerts on 41 innocent hulls.** Required speed is distance over
  *remaining* time, so near arrival it diverges: a vessel an hour from her berth
  on a stale ETA "needs 200 knots". She is late, not lying. The test is now a
  six-hour shortfall, and an expired ETA is not checked at all.
* **The heading check read every fix after the declaration, forever** — scoring
  her arrival, her berth and her next voyage against a port she reached and left
  days earlier. Bounded at the stated ETA.
* **A `timestamp[us]` column divided as if nanoseconds.** Nineteen hours came
  out as 68 seconds, so the check said "not enough track" about the one hull
  written to steam the wrong way. `tracks.kalman.epoch_s` exists because this
  atomised every track once before.
* **Eleven honest hulls called liars for swinging on their cables.** A ship at
  anchor yaws through the compass, so every step is "away" and the fraction is a
  perfect 1.0. The check now requires her to be making way.

**2. Seed 9 generation — fixed.** `background.py` budgeted a port call from the
moment she *sails* and never subtracted the passage; bg_8 leaves Kochi for
Mumbai, two and a half days of steaming the arithmetic never counted, and her
departure landed 11.3 hours past the window. The berth is now shortened to fit
after the call is built, since the routed passage length cannot be known before.
**Seeds 7, 8, 9 and 10 all generate cleanly.**

**3. Distance from shore — real, not a proxy** (`coastline.py`). The vessel-type
classifier had six of the brief's seven named inputs; the seventh was faked with
distance to the nearest gazetteer *port*, so a hull working five miles off an
empty beach scored as 120 km from shore. Now computed from the same 1 km land
mask the SAR detector and the corpus validator use. **Operating depth is still
absent and is not faked**: it needs bathymetry this project does not hold, and
the shelf off Gujarat and the shelf off Kerala are different shapes.

**4. `rendezvousing` — resolved as a placement, not a gap.** The brief names it
as an activity; it is not a property of one track. Two vessels closing and
holding station is a statement about a *pair*, and from a single track the
motion is indistinguishable from loitering. The capability lives where the
second track does — `tracks.interactions` produces `converging_and_holding` and
`transfer_pattern` — and the activity module now says so instead of silently
omitting the word.

**5. `check_mmsi_flag` and `check_mmsi_form` — still unmeasurable on synthetic
data, and that is now a recorded decision rather than an omission.** Building a
flag contradiction needs a *valid* MID, and the remaining six digits could then
belong to a real hull; the 999-block reservation exists to make that impossible.
A malformed-length MMSI would be safe in itself but could only be generated by
relaxing the collision guard, and **a safety invariant with an exception is one
somebody widens later.** Both checks have fixture coverage for agreement,
contradiction, an unknown MID, a reserved AtoN prefix, a wrong length and the
project's own block; their precision must be measured on the landed real GFW
corpus.

**Queue on the corpus (seed 7):** 27 alerts across 10 detectors.
`voyage_contradiction` 2, `vessel_interaction` 6, `identity_contradiction` 5,
`notable_activity` 4, `dark_vessel` 7. Dark-contact **precision 100%, recall
62%** (5 of 8).

**Recall moved three times in this session — 43%, 75%, 62% — and every move was
a fresh RNG draw rather than a change in capability.** Adding cast members and
changing a placement both shift the generator's stream, so each run is a new
sample of the same distribution. This is the instability recorded last session,
reproduced twice more. **Precision held at 100% in every single run**, which is
the number ADR-004 actually constrains and the only one of the two worth
quoting. SYNTHETIC-SUITE throughout (CLAUDE.md §4.6).

Prior entry: 2026-08-25 — **the factors that never fired.** Areas 2 and 3
added three classes of factor to the ranked list — a contradicted identity, a
notable activity, a relationship between two hulls — and all three fired **zero
times**. The brief sets one test on that: *"if adding an area does not change
what appears on that list, the area was built in isolation."* Areas 2 and 3
failed it.

**The fix was in the corpus, not the thresholds** (ADR-034). Group F adds
sixteen hulls: three identity contradictions with two decoys that share their
surface, two notable activities with the false positive that used to be mistaken
for one, three relationships between AIS-visible hulls with a lane decoy. The
ranked list now carries **`vessel_interaction` x6, `identity_contradiction` x5,
`notable_activity` x2** across 39 subjects.

**Writing the positive cases found four defects, every one of them silent, every
one making a rule quieter without saying so:**

1. **A reversal is not an event between two fixes.** The survey rule counted
   near-180° course changes fix to fix. A hull takes twelve minutes to come
   about and AIS arrives every four, so a real reversal is three 60° steps and
   the count is zero. **The survey branch could not have fired on any real
   survey vessel sampled at a realistic rate**, which is every survey vessel.
2. **Half of every cross-cell pair was discarded.** The interaction search took
   a one-ring H3 neighbourhood at res 6 and a comment asserted that reached
   "roughly 9 km" — a res-6 edge is 3.7 km. And its dedup guard was *dropping*
   cross-cell pairs rather than deduplicating them. Both produce fewer findings
   and never wrong ones, so "no interactions in this corpus" was reported with
   total confidence.
3. **The resampler deleted every stopped vessel.** AIS cadence is
   state-dependent by design (10 s under way, 180 s at anchor); the gap
   allowance used the track's *median*. A nine-hour ship-to-ship transfer with
   both parties transmitting produced **seven** usable samples. Loitering,
   anchoring and transfer were all going the same way.
4. **The pipeline query threw away the second identity attestation.** The real
   GFW connector lands `registry` and `self_reported` and says why —
   *"disagreement with the registry is a signal in its own right"* — and the
   query selected one row per vessel, collapsing them. The consistency check
   answered "cannot check" 230 times out of 230.

**And two numbers moved that were never as solid as they looked.**

*The interaction persistence floor* was re-derived on one corpus draw (longest
coincidence 4.7 h → gate at 6 h) and **falsified by the next**, where a pair of
fishing-fleet hulls steaming to the same ground held station for 11.7 hours. A
fishing fleet transiting together is a formation by every geometric test and by
no useful one. What separates the populations is **how close they hold**: eleven
coincidental pairs across two draws, none closer than 5,337 m; three authored
relationships all inside 4,245 m. Company and shadowing are now claimed only
inside 2.5 nm.

*Dark-contact recall*, reported at 86% before this group existed, reads **43% on
seed 7 and 62% on seed 8 — with precision 100% in every draw.** A
single-variable A/B (the whole pipeline twice on one corpus, resampler change
forced off) gave identical cascade verdicts, so no detector change here is
responsible: adding sixteen hulls shifts the generator's RNG stream and every
scenario's noise is a fresh sample. **A recall figure with a denominator of
seven episodes was never a capability measurement.** Precision — the number
ADR-004 constrains — is what holds.

**Queue on the corpus:** 22 alerts across 9 detectors, 39 ranked subjects.
`identity_contradiction` 5 (all true positives: F1's broken IMO check digit,
F2's call sign, F3's name, plus `spine` and `identity_break` whose broadcast
names genuinely no longer match their registry entries), `notable_activity` 3,
`vessel_interaction` 6, `dark_vessel` 4. SYNTHETIC-SUITE numbers throughout
(CLAUDE.md §4.6).

**OPEN — generation at seed 9 fails outright**, in `background.py`'s port-visit
scheduling, with an event landing outside the corpus window. Confirmed
pre-existing: it fails identically with group F removed. Seeds 7 and 8 generate
cleanly. Not fixed here.

Prior entry: 2026-08-24 — **IDEX Challenge 82, Area 3 built: what the
radar picture is *of*.** Three capabilities, and the honest result on each is
different, which is the point of measuring rather than asserting.

1. **Vessel type from motion alone** (`tracks/vessel_type.py`). A random forest
   over 13 motion-only features — speed distribution, turn rate, stop
   behaviour, leg geometry. Held-out **hulls**, never chips or tracks, so no
   vessel appears on both sides of the split. **65% accurate on the eight fine
   classes; 90% accurate on the coarse vocabulary the confusion matrix itself
   produced.** The model is not asked to make a distinction it cannot make: the
   confusion matrix is read back and classes that trade places are merged, so
   `Aframax / bulker / product_tanker` and `Suezmax / VLCC` are reported as one
   answer each and the classifier returns `cannot_separate` rather than a
   confident wrong hull type. Below 0.45 confidence it says `unknown`.
   **A tanker sub-class is NOT a claim this system can make** — it is a claim
   the requirement invites and the data refuses, and the 90% figure is only
   honest because the vocabulary was cut to fit it.
2. **Vessel-to-vessel interaction** (`tracks/interactions.py`) — moving in
   company, shadowing, converging and holding, transfer pattern. **Zero
   findings on the corpus at the 120-minute gate, and that zero is reported
   rather than tuned away.** The sweep found a cliff, not a plateau: 8 findings
   at 60 minutes, every one of them ordinary background fleet traffic on the
   same coastal route, 0 at 120. And `transfer_pattern` **cannot be validated
   on this corpus at all** — the scenario's transfer counterparties are dark by
   design, so the pattern's whole point is that only one side of it is ever
   visible. It is built, it is untested against a positive, and it says so.
3. **The contact profile** (`fusion/contact_profile.py`) — one sentence per
   radar contact: *"Likely merchant, transiting, no transponder, about 175 m."*
   Produced on all **8** dark contacts. It **assembles and never re-decides**:
   the darkness verdict comes from the cascade that already made it, with a test
   that fails if the profile ever starts deciding for itself.

The queue on the corpus is unchanged in shape — 16 alerts, `dark_vessel` 9,
`dark_rendezvous` 1 — and the three new detectors (`identity_contradiction`,
`notable_activity`, `vessel_interaction`) all sit at **0** on it. Each zero has
a stated cause: identity arithmetic cannot fire on scenario identifiers; the
re-derived activity thresholds retired the 151 false survey claims and left no
notable activity behind them; interactions found no pair above the gate. These
are SYNTHETIC-SUITE numbers (CLAUDE.md §4.6) and say nothing about a real
Coastal Surveillance Network feed, which this system has never seen. See
ADR-033.

Prior entry: 2026-08-22 (second session) — **IDEX Challenge 82, Areas 1
and 2 built.** The system now has one object at its centre: a ranked Vessel of
Interest carrying its reasons, its evidence, a score that decomposes exactly to
the factor, a proposed next action tied to the factor that motivated it, and a
question answerer that retrieves rather than generates (ADR-031). Area 2 fills
that frame with declared-identity authenticity, activity classification from
motion alone, forward projection as a first-class assertion, and the per-area
behavioural baselines the requirement asks for twice and the system did not have
(ADR-032).

**Five defects were found by building the frame, and every one of them was
invisible while the output was a flat alert queue:**

1. **42 of 43 `dark_rendezvous` alerts fired inside a berth or a designated
   anchorage** — 32 of them 470 m from the Mangalore port coordinate. Two hulls
   within 500 m at under 2 knots is the encounter definition, and alongside a
   terminal that describes every ship in the port. Now 43 → 1; dark-contact
   precision and recall unchanged at 100% / 86%.
2. **All 9 `dark_vessel` alerts were recorded as REAL data.** `add_alert`
   derives `is_synthetic` from the subject node; the detector pointed at a
   string nothing had created, so the lookup found nothing and the flag
   defaulted to 0. ADR-019 rests the whole real/synthetic split on that column.
3. **`reported-gap` edges were reachable from no graph view at all** — an edge
   family the product holds and cannot draw, so the graph looked complete and
   was not.
4. **`survey_pattern` was claimed on 151 of 209 tracks** — a coastal rotation
   supplies long legs and reciprocal turns, which was all the first rule asked
   for. Now 0 on whole tracks, with genuine patterns still found in windows.
5. **The scenario's reserved MMSI block collides with an ITU reserved form**, so
   the identity rule reported all 222 synthetic vessels as "misrepresenting what
   kind of station it is" — at 0.8 confidence, through the one detector built to
   have no false positives.

**Measured on the scenario corpus, seed 7, after all of it:** the alert queue is
**16 alerts** (was 55) across 9 detectors, and the ranked list is **35 subjects**
drawn from 2,713 tracked targets — about 1 in 78 of the picture. `dark_rendezvous`
43 → 1, `dark_vessel` 9 (all now correctly flagged synthetic), `notable_activity`
3, `identity_contradiction` **0**. That last zero is expected and is not a clean
picture: the arithmetic identity checks cannot fire on scenario identifiers by
construction, and the corpus carries one registry source so the consistency check
has nothing to compare against. They are built for the landed **real** GFW
identity corpus and must be measured there, on the laptop.

Area baselines: **770 cells, 212 usable (27.5%)** at H3 res 5. The local normals
are genuinely different — Kandla approach p95 = 12.0 kn over 19,768 observations
of 128 vessels; Mumbai anchorage p95 = 6.5 kn with a median of 0.0.

**And one capability was measured *out* of the product.** Departure from a
vessel's own dead-reckoned track flagged **87–98% of the fleet** at every
operating point that flagged anything at all, with no plateau anywhere in the
sweep. Every vessel alters course at every waypoint; a cone tight enough to
notice is tight enough to notice everything. Under ADR-004 it is built, kept as
an inspectable assertion, and **not** promoted to a suspicion factor — with a
test that stops it being quietly re-added without a fresh measurement. See
ADR-032(e).

Prior entry: 2026-08-22 — **"which ones are the actual vessels?"** Reported
as "the map key and the map have become too cluttered and crowded", and the
question underneath it was the real finding: an operator could not tell a ship
at her position this instant from a pin dropped two years ago. Eighteen of
twenty-four layers were on at first paint, eleven of them were circles of near
identical size, and three unrelated meanings shared one red. The key is now four
collapsible groups that say what they hold, the opening set is five layers, mark
weight separates live from historical from sensor, AIS gaps have their own
colour, `/tracks` reports the cap it had always applied in silence, alert markers
stopped claiming a position they never had, and the graph labels identity edges
by identifier rather than calling all 607 of them "identified as". See the
section at the end of this file.

Prior entry: 2026-08-21 — **the timeline player ran and nothing on the map
moved.** The scrubber was playing the *corpus* window (2012→2026, on a thin tail
of real GFW records) when only the *AIS* window (the eight-week narrative, 52
days) can move a vessel: 99% of the bar covered days with no positions, one
playthrough took ten hours, and the default playhead sat past the last position
so the map opened empty. Both spans are now served, the scrubber plays the one
that can move, and it says which.

Prior entry: 2026-08-16 (third session) — **Build 2: the maritime zone
layer** (ADR-030). The system understood four hardcoded circles; it now holds a
provenance-carrying geography layer that is queryable rather than merely
drawable, with zone entry/exit as first-class events, operator-drawn geofences
that are the same kind of object as a statutory boundary, four new analyses and
25 west-coast ports the gazetteer had never heard of. **The statutory limits —
EEZ, contiguous zone, territorial sea, IMBL — are deliberately NOT built**; see
the section at the end of this file.

Earlier the same day — **Build 1 closed** (ADR-029):
the transponder-shutdown position now reaches the queue, three API routes and a
Radar tab make it visible, the published "1 radar track in 9" correlation figure
was **wrong and is withdrawn** (the real number is 98.2% of resolvable tracks),
and two anomaly rules that had been asking global questions were made local.
Earlier the same day — **coastal radar became a second sensor**
(ADR-028), at the end of this file. The dark-vessel path fires for the first
time in this project's history, without SAR imagery. Testing the connector
claim found four places the core assumed AIS and one association defect that
was latent on the SAR path — all silent failures, all now fixed.

Prior entry: 2026-08-13 — three passes:

1. **"Was anyone watching?"** (ADR-026) — satellite imaging opportunities over
   AIS gaps. The first analytical claim this system makes on its own behalf
   rather than reproducing Global Fishing Watch's.
2. **The demo's two loading defects** — the timeline player was requested eighth
   of eight and hidden until it arrived; the Graph opened empty.
3. **The Graph opens on the whole network** — every relationship as one web,
   centred on the most-connected sanctioned vessel. Needed a new layout engine:
   `cose` took 115 s on 1,409 nodes, `fcose` takes ~6.5 s.

Prior entry: 2026-08-10 — demo data-coverage session, two passes.
ADR-025 (second): the one-click incident report, and the identity_changed
events that were never written. ADR-024 (first): findings
table, UN+EU sanctions matching, map density + truncation honesty, SAR contact
layer. See "Demo data coverage" at the end of this file. Prior entry:
2026-07-31 (second pass). The real corpus profile landed
from the laptop and **found six defects in the generator**, all now fixed —
most seriously, synthetic rows were separable from real ones by a single
`IS NOT NULL` filter. Seven of fifteen parameters are now MEASURED from 12,483
real rows. See "Real-corpus alignment" below.

Synthetic scenario corpus built and landed (ADR-019): 40 scenarios in the same tables as real data, flagged `is_synthetic`,
exercising the identical code path. Validators green, determinism and
truth-isolation tests passing, full suite 379 green. The pipeline ran over the
corpus and produced the first measured detection numbers — **precision 100%,
recall 18%** on the synthetic suite. Four real defects in the existing codebase
were found by landing scenario data through the real path; one of them is still
open and is the highest-value next fix. See "Scenario corpus" below.

---

## The status vocabulary (be strict about this — see CLAUDE.md §5)

- ✅ **Verified on host** — ran on Eshan's machine / the Oracle VM against real
  data, exit test passed, result pasted back. This is the only "really done."
- 🟡 **Built, sandbox-green** — code exists, tests pass in Claude's sandbox.
  **Not** verified on real infra or real data.
- 🟠 **Built, unverified on host** — code exists, has never run anywhere real
  (usually blocked on the VM not existing yet).
- ⬜ **Not started.**

---

## Current reality (the one-paragraph truth)

**The project holds real data for the first time.** On 2026-07-29 the ingest
connectors ran live on Eshan's Windows laptop and landed **27,791 GFW event and
vessel-identity rows** plus **26,824 sanctions and scene-catalog rows** over
AOI v1 — 73.9 MB of the 1 GB budget, every positioned table passing the AOI
bounds check. Running in download-only laptop mode (ADR-013): no Oracle VM, no
R2, no systemd, no SNAP, no live AIS capture.

**What that data is, precisely:** GFW's *derived* behaviour events (loitering,
port visits, encounters, AIS gaps), vessel identity, three sanctions lists, and
Sentinel-1 scene metadata. **No SAR imagery, no AIS position tracks, and no dark
vessel detected by us.** GFW's 5 gap events are GFW's finding, not ours — and as
of 2026-07-31 **all five are flagged by GFW as intentional AIS disabling**,
which the earlier mapper was not reading and which this file recorded as zero
for a day. That is five real vessels GFW assessed as deliberately going dark,
and it is still GFW's assertion rather than our detection. Our own
dark-vessel detection needs SAR contacts matched against AIS tracks, and neither
is obtainable free for this AOI (see DATA_SOURCES.md). The synthetic Phase 1–6
prototype remains green in-sandbox and every metric it produces is synthetic.

**What the demo is, as of 2026-08-10 (ADR-024/025).** Five screens — Map,
Findings, Alerts, Vessels, Graph — plus a one-click incident report, served by
one Python process. The M6 demo definition in CLAUDE.md §0 is now **built end
to end**: a map, a ranked list, a plain-English reason, and an export.

**One thing in it is now ours.** As of 2026-08-13 (ADR-026) the system computes
**satellite imaging opportunities** over flagged AIS gaps — where a vessel could
have been during its silence, against where Sentinel-1 was actually pointed.
That is the first analytical claim here that is not a reproduction of someone
else's finding. It still claims nothing about any vessel: it says an image
exists, or does not, and nobody has looked at it.

**And one sentence that has to travel with it:** of six detectors, **two fire**,
producing **one alert** on the scenario corpus and **zero on real data** — every
detector reads the track engine, and the real corpus has no AIS positions. The
demo's strength is the evidence chain, the provenance and the sanctions identity
matching; it is not detection performance. Say that before anyone asks.

**Amended 2026-08-16 (ADR-028, revised by ADR-029):** on the scenario corpus a
third detector now fires. `dark_vessel` produces **3 alerts at 100% precision**
over a simulated coastal-radar picture, which is the first time the headline
claim — a contact on radar with nothing broadcasting there — has fired at all.
Every word of the paragraph above still holds for **real data**: there is no
radar feed, there are still no real AIS positions, and nothing in this has run
on Eshan's machine.

---

## Unit status

| Unit | What it is | Status | Notes |
|---|---|---|---|
| 0.0 | Repo skeleton + canonical schemas + H3 helper + config loader | 🟡 | Schema round-trip tests green in sandbox. |
| 0.1 | Copernicus Sentinel-1 GRD connector | ✅ | **636 scenes landed live**, no credentials needed. Amended exit test met (ADR-014). Imagery download PARKED. |
| 0.2 | SNAP preprocessing chain (pyroSAR) + install script + `doctor` cmd | 🟡 | Install script memory-capped (12 G heap / tile cache / 4 threads). **Never run on a host** — and ARM is unvalidated (see OPEN QUESTIONS). Fiddliest unit; budget a full session. |
| 0.3 | aisstream live AIS consumer + systemd service | ⬜ | **PARKED** (ADR-013) — needs an always-on host. Exit test deferred verbatim, not amended. |
| 0.4 | GFW + versioned OFAC/UN/EU/WPI registries | ✅ | **Ran live.** GFW events 27,172 rows; vessel identity 9,648 intervals / 9,184 summaries; OFAC 19,157 (1,516 vessels); UN 1,011; EU 6,017. WPI blocked by an NGA outage — optional per ADR-016. SAR clause amended (ADR-014). NOAA PARKED. |
| 0.5 | Inspection dashboard v0 (AOI frame, AIS tracks, scene footprints) | 🟡 | Throwaway/ugly by design. Verifies once real AIS + scenes are landing. |
| 1.0 → 6.3 | Phases 1–6 (synthetic prototype) | 🟡 | Implemented and green on the synthetic suites. Every metric is synthetic-only. |
| 6.0 → 6.3 | Phase 6 product surface (API + UI + export) | 🟡 | **The M6 demo definition is now fully built** (ADR-024/025): map, ranked findings, plain-English reason, one-click incident report. Sandbox-green and browser-verified; **never run against the real corpus**. Graph opens on the whole network and the scrubber no longer disappears (ADR-027). |
| — | Coastal radar as a source (`ingest/radar`, `fusion/radar_ais`, `api/radar/*`, Radar tab) | 🟡 | **Not a spec unit** — ADR-028 + **ADR-029**. A simulated CSN picture over the same vessel truth as the AIS, landed through a real connector into `radar_track_report`, correlated by the existing association engine, filtered by the existing dark cascade, served over three API routes and drawn in the UI. **Precision 100%, recall 43% on the synthetic picture; correlation resolves 98.2% of resolvable tracks to the right hull.** Sandbox-green; **never run on the laptop**, and no real radar feed exists. |
| — | Maritime zone layer (`zones/`, `ingest/zones`, `/api/zones`, map geography + draw tool) | 🟡 | **Not a spec unit** — ADR-030. Port areas, anchorages, terminals/SPMs, customary lanes, the four migrated sensitive areas and operator geofences, as landed rows with provenance and an H3 cell index. Zone entry/exit are landed events. **The four statutory limits are absent by decision** and arrive only through the connector. Sandbox-green; **never run on the laptop**. |
| — | **IDEX Area 1 — the MDA assistant** (`assistant/`, `/api/voi`, `maritime-isr voi`, Assistant tab) | 🟡 | **Not a spec unit** — ADR-031. The ranked Vessel of Interest object: factor catalog, a score that decomposes exactly to the factor, plain-language narration, next actions with computed feasibility and stated capability, and a question answerer that retrieves rather than generates. Sandbox-green, browser-verified; **never run on the laptop**. |
| — | **IDEX Area 2 — predictive AIS analysis** (`anomaly/identity`, `tracks/activity`, `tracks/projection`, `baselines.py`) | 🟡 | **Not a spec unit** — ADR-032. Identity authenticity (IMO check digit, MMSI/flag, registry consistency), activity classification from motion alone, forward projection as an assertion, per-area baselines as a landed artifact. **The arithmetic identity checks cannot fire on scenario data by construction** and must be measured on the landed real GFW corpus. Track-departure detection is built and deliberately **not** a suspicion factor — measured at 87-98% of the fleet. |
| — | **IDEX Area 3 — radar picture classification** (`tracks/vessel_type`, `tracks/interactions`, `fusion/contact_profile`) | 🟡 | **Not a spec unit** — ADR-033. Vessel type from motion alone: **65% fine / 90% coarse on held-out hulls**, with the coarse vocabulary derived from the confusion matrix so the model is never asked to separate `Aframax/bulker/product_tanker` or `Suezmax/VLCC`. Interaction detection (company, shadowing, converging-and-holding, transfer): **0 findings on the corpus at the 120-minute gate**, reported not tuned; `transfer_pattern` is **unvalidatable here** because the scenario's counterparties are dark by design. Contact profiles produced on all 8 dark contacts. Sandbox-green; **never run on the laptop**, and no real CSN feed exists. |
| — | **IDEX Area 2 — declared voyage** (`schemas.VoyageDeclaration`, `ingest/aisstream`, `anomaly/voyage`, `coastline.py`) | 🟡 | **Not a spec unit** — ADR-035. AIS message 5 landed for the first time: 3,091 declarations over 131 hulls, honest by default. Two rules — an arrival no hull could make, and a destination she never steered towards. **2 alerts, both true positives, zero false positives.** The live connector now parses `ShipStaticData` but is on the PARKED path and has never seen a live message. Real distance-from-shore replaces the port-distance proxy; **operating depth is still absent and not faked**. Sandbox-green; **never run on the laptop**. |
| — | **IDEX factor coverage — group F** (`scenario/scenarios/group_f.py`, `tests/test_factor_coverage.py`) | 🟡 | **Not a spec unit** — ADR-034. Sixteen hulls writing the situations Areas 2 and 3 were built for and the corpus never contained, each paired with a decoy. All three factor classes now reach the ranked list (`vessel_interaction` x6, `identity_contradiction` x5, `notable_activity` x2). **No threshold was loosened to make a rule fire**; four silent defects were fixed instead. `check_mmsi_flag` and `check_mmsi_form` remain **unmeasurable on synthetic data by construction** — the reserved 999 MMSI block that stops a synthetic hull wearing a real identity also makes a flag contradiction unbuildable. Sandbox-green; **never run on the laptop**. |
| — | Imaging opportunities over AIS gaps (`overpass`) | 🟡 | **Not a spec unit** — ADR-026, outside the 0.0–6.3 numbering. The first determination that is ours rather than GFW's. Needs no pixels; joins the 636 landed scene footprints against flagged gaps. Sandbox-green; **never run against the real corpus**. |

**Ingest rework detail (units 0.1 / 0.3 / 0.4), 2026-07-29:**

| Work | Status | Notes |
|---|---|---|
| Repair of partial-upload breakage | ✅ | Import break was already fixed in `69b0e82`; residual was regenerating gitignored synthetic data + deleting a re-uploaded `RENAME_AFTER_UPLOAD.md`. |
| `DATA_SOURCES.md` reconnaissance | ✅ | Desk research, then replaced with measured numbers from the live run. |
| Laptop-mode hardening + `doctor` | ✅ | Host-verified on the Windows laptop. |
| GFW event connectors | ✅ | **Live:** 24,153 loitering / 3,000 port visits / 14 encounters / 5 gaps. |
| GFW vessel identity | ✅ | **Live:** 9,184 of 9,185 vessels, 1 lookup failure. |
| Sanctions (OFAC / UN / EU) | ✅ | **Live**, versioned with as-of dates. |
| S1 scene catalog | ✅ | **Live:** 636 scenes, metadata only. |
| WPI ports | ⬜ | Blocked by NGA portal outage. Optional per ADR-016; manual `--path` import available. |
| GFW SAR (gridded + portal CSV) | ⬜ | Upstream offline since 2026-07-03. Both paths degrade cleanly. |
| Ingest report | ✅ | Prints real counts, date ranges, AOI checks and disk usage. |

**Test tally: 594 passing / 4 skipped / 1 failing in-sandbox, with the scenario
corpus *and* the Phase 1–6 fixtures generated** (was 525 on 2026-08-10). The
one failure is **pre-existing and newly visible** — see "A latent defect the
fixtures exposed" below. Sandbox-green ≠
host-verified — do **not** report this as "594 tests prove it works on real
data." Every number the system has ever produced comes from synthetic fixtures
or fixture-driven tests.

**And the tally is only reproducible with the fixtures built.** A bare checkout
needs the scenario corpus *and* an undocumented three-step chain before the
Phase 1–6 tests will run at all:

```
python -m maritime_isr.cli scenario generate --seed 7
python tools/run_scenario_pipeline.py
python tools/make_synthetic_feed_phase2.py
python tools/make_synthetic_scenes_phase3.py
python tools/make_synthetic_orgworld_phase4.py
python tools/run_phase2_synthetic.py   # then 3, 4, 5, and run_phase6_product.py
```

Each missing prerequisite surfaces as a bare `FileNotFoundError` naming a file,
so the chain is discovered by walking it. Anyone quoting the tally should say
which fixtures were present.

*A caveat on that number that matters more than its size:* `test_api_exercise.py`
and parts of `test_phase6.py` **skip themselves** when no corpus is landed. A
bare checkout reports green with the most valuable ~30 tests never executed.
Generate the corpus and run the pipeline before quoting the tally:

```
python -m maritime_isr.cli scenario generate --seed 7
rm -f data/graph.sqlite && python tools/run_scenario_pipeline.py
python -m pytest -q -rs        # read the SKIPPED lines, do not skim them
```

**Host-only bugs found once real hardware was involved (6).** None were visible
in the sandbox; all sat in the seam between the code and the real world:
1. `pytz` missing — DuckDB cannot bind a tz-aware datetime to `TIMESTAMPTZ`.
2. `.env` was never read at all; every credential looked missing.
3. `doctor` passed a token that could not be sent — a credential check that
   tested existence rather than usability.
4. `data_root` resolved from the working directory while `.env` resolved from the
   package, so `doctor` went green against an empty data dir.
5. GFW vessel-detail params were wrong (`datasets[0]` vs `dataset`, wrong
   `includes`, and `registries-info-data` defaulting to NONE) — 422 on all 9,185.
6. EU sanctions needed a token in the URL; noted as a risk in DATA_SOURCES.md
   and then shipped without one.

**The pattern worth remembering:** items 2, 3 and 4 were all *masked by a green
doctor*. Checks that assert a thing exists are near-worthless; checks that
exercise it are what catch real faults. Fixture-shaped tests validated mapping
logic but could not catch item 5, because the fixtures encoded my own assumption
about the request shape.

---

## Blocking dependency chain (what unblocks what)

**Under ADR-013, the ingest chain needs no VM at all:**

```
Free GFW token ──► ingest gfw-events ──► ingest gfw-vessels ──┐
                                                              ├─► ingest report
(no credentials needed) ─► ingest registries ─────────────────┤   = first real data
(no credentials needed) ─► ingest s1 --catalog-only ──────────┘
```

The legacy chain below still governs SAR imagery, SNAP and live AIS:

```
Provision Oracle ARM VM ─┬─► run 0.1 backfill (also needs Copernicus acct + R2 token)
                         ├─► install SNAP + run 0.2 (VALIDATE ARM FIRST)
                         ├─► run 0.3 as systemd service (needs aisstream key)
                         └─► run 0.4 pulls (needs GFW key) + cron for registry refresh
                                        │
                                        └─► 0.5 dashboard shows real pipes flowing → Phase 0 truly closed
```

**Nothing downstream of "VM provisioned" can be host-verified until the VM exists**
— but under ADR-013 that no longer blocks the ingest units or Phase 1, so the VM
is **not** the highest-leverage next action any more. Landing real data is.

A handoff to Claude Cowork was prepared to drive the Oracle Cloud VM setup
end-to-end; Eshan handles account creation, card verification, and credential
steps personally.

---

## Next up

> **Read this box first — the rest of this section is from 2026-07-31 and some
> of it is done.** The current highest-leverage action, as of 2026-08-10, is
> **running the demo on the laptop against the real corpus.** Everything in
> ADR-024 and ADR-025 is sandbox-green and browser-verified and *none of it has
> touched real data*. In order:
>
> ```
> python -m maritime_isr.cli ingest registries        # refresh UN + EU
> python -m maritime_isr.cli ingest sanctions-match   # REQUIRED: schema + key changed
> python -m maritime_isr.cli overpass                 # NEW (ADR-026): who was watching
> python tools/data_health.py                         # the demo gate
> python -m maritime_isr.api                          # then open /findings
> ```
>
> **Three numbers to report back**, whatever they are:
> 1. How many findings **UN and EU add beyond OFAC's 126.** Zero is a result.
> 2. Whether `/findings` shows the **5 GFW-flagged intentional-disabling gaps**.
>    If that section is empty on real data, the flag did not survive
>    `rebuild_conformed.py`.
> 3. The count of **`identity_changed` events**. 12 across 5 hulls in the
>    sandbox; a figure near the 9,184-vessel fleet size would mean the
>    supersession rule regressed to counting interval *closure* — the
>    100%-closed trap.
>
> Then click **Export report** on a real finding and read the resulting file
> end to end. It is the artefact that leaves the building, and nobody has yet
> seen one built from real rows.

**Everything below is built and sandbox-green. None of it has run on the real
landed tables** — those live on Eshan's laptop, not in the sandbox. The numbers
these produce are the point; do not quote any of them until he pastes a run back.

### Run these four, in this order, and paste the output back

    1. python -m maritime_isr.cli ingest sanctions-match
    2. python tools/review_matches.py            # DONE - 98/98 pass
    3. python tools/analytic_rename_gap.py       # RE-RUN — it ran against nulls
    4. python tools/graph_report.py              # RE-RUN — same reason

**Both 3 and 4 must be re-run and their earlier results treated as void.** They
read `gfw_intentional_disabling`, which was null on every gap row until the
rebuild recovered it. The corpus holds **5 of 5 gaps flagged by GFW as
intentional disabling**, not zero — see the overturned finding below. Any
conclusion either tool reached about dark vessels was reached against an empty
column.

### Before those: the port-visit work (ADR-020)

    python tools/rebuild_conformed.py --dry-run     # DONE 2026-07-31, orphans 0
    python tools/data_health.py                     # NEXT — the demo gate
    python tools/port_visit_forensics.py            # raw only, writes nothing
    python tools/rebuild_conformed.py               # after backing up data/conformed
    python tools/corpus_profile.py                  # refresh the profile
    python tools/restamp_h3.py --dry-run            # must still report 0 added

**`data_health.py` is the demo gate.** Read-only, exits 1 on any BLOCKER. It
grades the data on disk rather than the code's intentions: H3 coverage at all
five resolutions, the provenance envelope on every row, `is_synthetic` and
`source_id` never disagreeing, whether the raw store is complete enough to
re-derive, whether a row count is really an API page limit, and whether there is
anything in the corpus to demo at all. Run it before every demo. A blocker means
the demo would fail outright or state something false on screen.

**The forensics run is about the open question**, not the demo. The dry run
refuted the structural explanation (see the correction above); this reads
`data/raw/` and writes nothing, so it is safe to run before deciding anything.

Re-derives the conformed GFW tables from the immutable raw JSON with the current
mapper. **No network** — it reads `data/raw/gfw-events/`, so ADR-013 holds and
the corpus window does not move.

*What the dry run showed (2026-07-31):* `confidence`, `gfw_confidence_raw` and
`visit_confidence` all 0% → **100%**; `visit_port_id` 0% → 100% but changing
nothing, because `port_id` was already 100%; `dwell_hours` 87%;
`duration_hours` quantiles **identical** before and after, confirming nothing
clamps; **`orphans: 0` on all four kinds.**

*`orphans: 0` is the load-bearing one.* It is the first direct check that raw is
sufficient to regenerate the conformed layer — "every derived output is
regenerable from raw + git SHA" (CLAUDE.md §4.2) is now measured on this corpus
rather than asserted. Anything other than 0 would have meant part of raw is
missing; the tool preserves those rows rather than deleting them, and says so.

*Failure mode:* `no raw payloads on disk` for every kind means `data/raw/` was
not kept. Nothing is written in that case and the conformed table is left as-is;
report it, because the fix then requires a re-download and that is an ADR-013
decision, not a tooling one.

**The `maritime-isr` console script is not installed on the laptop.** It is
declared in `pyproject.toml` but the package was never `pip install`ed, so the
short name does not resolve. `python -m maritime_isr.cli <verb>` runs the same
code with no install; `pip install -e .` from the repo root makes the short name
work if we want it.

**1 — re-run the matcher.** Required, not optional: ADR-018 changed what a match
means. IMO numbers are now check-digit validated and the call-sign tier split in
two, so rows landed before today carry retired semantics. *Success:* it prints
`landed N match(es)`, then a tier breakdown including `call_sign_name`.
*Failure:* `no vessel identity landed` — the connectors have not run.

**2 — review the matches.** Now also validates the check digit on every IMO
match. *Success:* `98 of 98 IMO matches pass their check digit`. **If any fail,
they leave the 0.95 tier and the headline number changes** — report the failures
verbatim.

**3 — the rename-then-gap analytic.** Cross-references IMO matches carrying a
different OFAC name against gaps GFW flagged `intentionalDisabling`, and splits
the name disagreements by which record is fresher. *Success is any number,
including zero* — `VESSELS THAT ARE BOTH ... : 0` is a finding about the free
data, not a failure. Report it as zero if it is zero.

**4 — populate the graph and measure it.** Writes real edges only (met-with,
docked-at, flagged-to, sanctioned-under, identified-as, reported-gap) and prints
node/edge counts, the confidence distribution after decay, and the connectivity
of the sanctions-matched population. **The number to look for:** how many of the
IMO-matched vessels have at least one encounter edge. If that is near zero, a
graph UI has nothing to draw and the next build should be a ranked table, not a
network view. The report also names the single densest sanctioned neighbourhood
— the candidate demo vessel.

*Failure mode to watch:* `skipped match rows with unrecognised tier(s)` means
step 1 was not re-run.

### Deferred, with the reasoning on the record

**Phase 1 (own SAR) and xView3 — deferred, no date (ADR-017).** The 1 GB cap
stands and nothing new is downloaded. Own-SAR is not on the M6 demo path; GFW's
SAR datasets are offline and per-detection AOI SAR has no API. Parked code stays
parked, not deleted. Reverts by un-parking when a deploy host or the GFW datasets
return.

### Ready to run whenever the publisher is back

`maritime-isr ingest registries --only wpi` — NGA's MSI portal is under
maintenance; every URL variant returns 503. `tools/probe_wpi.py` finds a working
URL when they recover, or import a browser download with `--path`. Optional per
ADR-016 — GFW's `distances` block already covers the immediate need.

`maritime-isr ingest gfw` — GFW SAR offline upstream since 2026-07-03.

## First analytical result (2026-07-29)

`maritime-isr ingest sanctions-match` on the landed data: **126 distinct vessels
match an OFAC-sanctioned hull, 98 of them by IMO** (permanent hull number, so
these are findings rather than candidates), 29 by name only (candidates), 0 by
call sign. IMO extraction verified on all 98 — keyword-anchored, single 7-digit
value, 0 questionable. 53 of the 98 carry a different name and often a different
flag from OFAC's, which is the identity-laundering signature and is exactly what
an IMO match is for.

**Say:** "98 vessels matched by us against GFW's event data." **Do not say:**
"98 sanctioned vessels detected by us." GFW detected the vessels and GFW assessed
every dark/gap determination; OFAC decided who is sanctioned; our contribution is
the identity match between the two. No SAR, no dark-vessel detection, nothing
observed going dark by us. **That attribution travels with the number all the way
to any UI we build** (ADR-018). Full caveats in DATA_SOURCES.md.

---

## Second analytical result (2026-07-30) — one confirmation, two negatives

Run live on the laptop against the landed tables. Three outcomes, and only the
first is good news.

### 1. CONFIRMED — every IMO survives its check digit

`python tools/review_matches.py`: **98 of 98 IMO matches pass their check
digit**, and 0 of 98 had questionable extraction. Two independent checks now
agree — extraction is keyword-anchored with a single 7-digit value, and the
arithmetic says each number is a real IMO. Since the checksum can only *remove*
rows from the 0.95 tier, **98 is confirmed rather than a ceiling**: re-running
the matcher under ADR-018 cannot reduce it. 45 names agree with OFAC, 53 differ.

This is the strongest corroboration the sanctions matching has. Record it.

### 2. NEGATIVE — the freshness split is degenerate, and it is our bug

`python tools/analytic_rename_gap.py` put **53 of 53 name mismatches in "OFAC
newer" and 0 in "GFW newer."** A split that lands 100% in one bucket is not a
finding about vessels, it is an artifact of the query.

**Cause:** `sanctions_as_of` is **the day we downloaded the SDN list**, not the
day OFAC designated the vessel — the SDN CSV carries no designation date. A GFW
identity interval's `valid_from` is when that identity started transmitting,
always before our download. So the comparison resolves one way by construction.

**Fix shipped:** `freshness_is_informative()` detects the single-snapshot
condition and the report now refuses to present the buckets, printing `THE SPLIT
ABOVE CARRIES NO SIGNAL` instead. It becomes usable when a **second OFAC
snapshot** is landed and a name change can be seen happening *between* them —
which is exactly what the versioned snapshots in `registries.py` were built for.
A matter of waiting for the next refresh, not of a better query.

**Honest statement today:** *53 vessels carry a name that differs from OFAC's,
and we cannot say which name came first.*

### 3. NEGATIVE — zero flagged gaps, so the cross-reference never ran

`gaps GFW flagged intentionalDisabling : 0`. Only 5 gap events are landed for
the whole AOI window, and none carries the flag. **The intersection was zero
before the name-mismatch side was consulted** — that is a weaker kind of null
than "both populations exist and do not overlap," and it was being reported as
though the two had been compared.

**Fix shipped:** the report now prints the denominator (total gap rows, flagged
true, explicitly false, null) and distinguishes three cases — an empty table
(missing input), an all-null verdict column (possible mapping bug), and GFW
having genuinely assessed gaps and flagged none (a real negative). **Run it again
after the fix to see which of the three we are in** — if the column is entirely
null, the mapping needs checking before any conclusion about vessels.

#### ⛔ OVERTURNED ON HOST 2026-07-31 — it was case two, the mapping bug

`tools/data_health.py`, after `rebuild_conformed.py` re-derived the gaps table
from raw with the current mapper:

```
[INFO] flagged dark-vessel gaps
       5 of 5 AIS gap(s) are flagged by GFW as intentional disabling
       vessels 324afd84e-ee, 854502228-8e, a76aaca78-8c, a7886313f-f8, e2a85d697-7a
```

**Not zero. All five.** The verdict column was entirely null because the mapper
that landed those rows never read `gap.intentionalDisabling` — it was added on
2026-07-29 and the rows predate it. The negative above was our bug reported as a
finding about the world, and it stood for a day.

**Everything that "zero flagged gaps" was used to justify has to be re-examined**,
because it was one of the two numbers behind the decision not to build a graph UI
and behind ADR-019's framing of the corpus as having nothing to exercise. The
other number — 0 of 98 OFAC-matched vessels with an encounter edge — is
independent and still stands until re-measured.

**What this is, stated precisely (CLAUDE.md §6).** These are **GFW's**
assessments that a vessel deliberately switched off AIS, carried through as
GFW's assertion. They are **not** our dark-vessel detections: we did not compute
them, we have no receiver-coverage model over those positions, and asserting
intentional silence outside demonstrated coverage is a false positive by
construction. The honest sentence is *"GFW assessed this gap as intentional
disabling"* — never *"we detected a dark vessel."*

**For the demo this is the single most valuable row in the corpus**: five real
vessels, in the Arabian Sea, in the last eight weeks, with a named source and a
traceable assertion. Re-run `tools/analytic_rename_gap.py` and
`tools/graph_report.py` — both consumed this column and both ran against nulls.

### 4. THE DECIDING NUMBER — a graph UI has nothing to draw

`python tools/graph_report.py`, 352s over the landed tables:

```
  IMO-matched (findings): 98 vessel(s)
      with >= 1 encounter edge :    0  (0%)
      with >= 3 encounter edges:    0  (0%)
  all matched (any tier): 126 vessel(s)
      with >= 1 encounter edge :    0  (0%)
  126 of 126 matched vessels have NO encounter neighbour at all (100%).
  DENSEST SANCTIONED NEIGHBOURHOOD: None.
```

**Answer to the question the exercise was run to settle: do not build a graph
UI.** Not "not yet" — on this data there is no network. The arithmetic behind it
is simple and was visible in the ingest counts all along: **14 encounters landed
for the entire AOI over 8 weeks**, collapsing to 8 distinct vessel pairs, drawn
from a population of 9,184 vessels. The chance that any of those 8 pairs touches
the 126 sanctions-matched hulls was always small, and it did not happen.

The graph itself is real and healthy — 17,562 nodes, 20,026 current edges, decay
running on real observation times. It is simply **star-shaped**: vessels connect
to flags, ports and identities, and not to each other. Every path between two
vessels runs through a shared port or flag, which is true of thousands of
unrelated ships and carries no signal.

**What to build instead: a ranked table.** The product that the landed data
actually supports is a sorted list of the 98 IMO-matched vessels with their OFAC
program, name disagreement, flag, and behavioural counts (loitering events, port
visits, distance from shore), each row expandable to its evidence and export.
That is the M6 demo path — map, ranked list, plain-English reason, export — with
the network view removed. Nothing else in the demo definition changes.

**Revisit the graph decision when** encounter volume rises by roughly two orders
of magnitude: a wider time window, a larger AOI, or a paid feed. The populator
and the report stay in the tree for exactly that — re-running them is the test.

### Three reporting bugs found in that output, all fixed

1. **Decay counted history as current.** `decay_summary` summed raw rows, so an
   interrupted run followed by a re-run showed 17,978 `flagged-to` edges over
   8,989 real ones and 10,220 "already decayed" against a much smaller true
   figure. Now resolves latest-per-triple, matching what `store.edges()` returns
   on read. `history=True` gives the raw view deliberately.

2. **"Closed interval = former identity" was wrong.** 8,724 of 8,724 intervals
   came back closed — 100%, which should have been read as a bug on sight. GFW's
   `transmissionDateTo` is the last transmission *inside the window we queried*,
   so every record ends because our query ended. Treating that as supersession
   labelled the entire fleet as having changed identity. `formerly-identified-as`
   now requires real evidence: the same vessel carrying a **different** name in a
   **later** interval. The count is renamed `identified_as_superseded`.

3. **The report did not say when history exceeded current.** It now does, so the
   append-only store's re-run behaviour is visible rather than confusing.

### Still outstanding

**A second OFAC snapshot.** It unblocks the freshness split (§2) and is the only
cheap thing on the list — one `registries --only ofac` run on a later date.

---

## Scenario corpus (2026-07-31) — ADR-019

**What was built.** `maritime_isr/scenario/`, a two-layer generator. Layer 1 is
primitives with no idea what a scenario means — vessel factory, great-circle
track integrator with per-class acceleration and turn-rate limits, an AIS
emitter following ITU-R M.1371 intervals with reception thinning and heavy-tailed
position noise, rendezvous, gaps, port calls, identity events, corporate
structure. Layer 2 composes them into 40 scenarios, each writing exactly one
`scenario_truth` row.

**Landed, read back from disk (not what was built — what exists):**

```
ais_position               97,314 synthetic        0 real
gfw_loitering                  51 synthetic        0 real
gfw_port_visits                45 synthetic        0 real
gfw_vessel_identity           122 synthetic        0 real
gfw_ais_gaps                   12 synthetic        0 real
gfw_encounters                  6 synthetic        0 real
scenario_truth                 40 synthetic        0 real
scenario_organizations         11 | scenario_ownership 37 | detections 6
```

The `0 real` column is not a claim that real data vanished — **the real corpus
is on Eshan's laptop and was never in this sandbox** (`data/` is gitignored).
Every figure below is scenario-only and must be re-run where the real data lives
to produce a genuinely combined view.

**Catalogue:** 22 true anomalies, 16 decoys, 2 deliberate misses. Cast is 74
principal vessels plus a 40-vessel fishing fleet — over the 45-60 the plan asked
for, deliberately, because a hull cannot be in two places at once and the
catalogue needs the hulls. See ADR-019.

### Measured detection results — the honest numbers

```
true anomalies    :  22   DETECTED 4   MISSED 18
decoys            :  16   FALSE POSITIVE 0   correctly quiet 16
deliberate misses :   2   correctly silent 2

precision 100%    recall 18%     (ADR-004 target: precision >= 70%)
of 4 detections, 2 came from the rule the scenario expected
alerts on entities with no truth row (background traffic): 1
```

By family: identity_manipulation P100/R33, spoofing P100/R33,
graph_ownership P100/R50, dark_transfer P n/a / **R 0%**,
behavioural_geographic P n/a / **R 0%**.

**Read this as tuning information, not as failure — and no thresholds were
touched this session** (measure first, tune as a separate decision).

**Why recall is 18% — four causes, all diagnosable:**

1. **The vessel-keyspace split (the big one).** `from_landed` keys hulls
   `vessel:gfw:<vessel_id>`; `graph.identity.resolve_mmsi` mints
   `vessel:mmsi:<mmsi>`. Alerts land on nodes the landed graph has never heard
   of. This is the **ADR-015 failure class again**. The measurement bridges it
   with an explicit alias map and says so; the defect itself is **not fixed**.
   **This is the highest-value next fix in the repo.**
2. **Every gap classified `COVERAGE_GAP`, none `INTENTIONAL_SILENCE`** (2,276 of
   2,276). The coverage model learns reception from our own emitted data, and
   our modelled reception is terrestrial-only (ADR-005), so no silence is
   attributable anywhere. That is the honesty rule working exactly as designed —
   and it means the whole `dark_transfer` family (A1-A5) cannot currently fire.
3. **6,438 encounters detected by the track engine, 6 `met-with` edges in the
   graph.** The graph populator reads landed `gfw_encounters` only; the track
   engine's own encounters are never fed to it. Another seam.
4. **The fusion path was not exercised** — `dark_vessel` needs association
   verdicts, which need SAR scenes we do not process (ADR-017). The 6 synthetic
   SAR contacts land but nothing consumes them yet.

**What did work.** Zero false positives across all 16 decoys, including the
clean-vessel-dirty-neighbour case (proximity is not association), the legitimate
bunkering built by the same primitive call as the illicit transfer, and the
40-vessel fishing aggregation. Both deliberate misses stayed correctly silent
with their capability boundary recorded and a number attached.

### Four defects found in the EXISTING codebase by landing through the real path

1. **`GraphStore.__init__` would crash on any existing graph.** The
   `is_synthetic` index was declared in `_SCHEMA`, which runs *before* the
   migration adding the column, so every pre-migration `graph.sqlite` would
   raise `no such column: is_synthetic`. **Fixed.**
2. **Two vessel keyspaces that do not join** (above). **Open.**
3. **`VoyagePlan` had no initial speed**, so every leg boundary restarted the
   vessel from rest — 165 degrees of course change in one 60 s step on a hull
   limited to 0.25 deg/s. **Fixed.**
4. **Landing merges within a day partition only**, so a re-run whose timing
   shifted landed duplicate truth rows. **Fixed** — `generate` clears first.

### FIRST RUN ON THE LAPTOP — do this before generating

**Back up `data/conformed/` first.** Scenario rows land into the *same day
partitions* as real rows, so `land_table` reads each partition, merges, and
rewrites it. Raw is immutable and conformed is re-derivable, but re-deriving is
a re-run of every connector — a copy is cheaper:

```
xcopy /E /I data\conformed data\conformed_backup     (Windows)
```

**Then, in this order:**

```
python tools/corpus_profile.py            # 1. read-only; writes the profile
python -m maritime_isr.cli scenario status  # 2. read-only; shows the split is 0
python -m maritime_isr.cli scenario generate --seed 7
python -m maritime_isr.cli scenario clear   # 4. proves the round trip is clean
```

Step 1 is read-only and touches nothing. **Run steps 1-2 and paste the output
back before running step 3** — the profile carries the real schemas and null
rates, which is what says whether the merge will work at all.

**The known risk, and why it is survivable.** If a column holds a string in the
real rows and a number in the synthetic ones, Arrow raises on conversion and
the write fails. `tests/test_scenario_mixed_corpus.py` pins that failure as
**loud** — it raises naming the column rather than silently coercing — and
`clear()` is tested to leave real-only partitions untouched and real rows
byte-identical. The fixtures in that test are *fabricated to the connector
shapes read off the source*, not real rows; the profile from step 1 is what
turns that guess into a check.

### Still to do here

- **Run `tools/corpus_profile.py` on the laptop.** Every generator parameter is
  currently an ASSUMED published prior, and the generation report says so in a
  measured-vs-assumed table with a row count behind each. The profiler reads the
  real tables and emits `data_profiles/real_corpus_profile.json` (~100-200 KB,
  committable), after which the same parameters are re-sampled from real
  distributions with no code change. **Until then, no distribution in this
  corpus is measured.**
- **Re-run the collision guard where the corpus lives.** In the sandbox it fell
  back to the profile and reported `0 real IMOs known` — it says so rather than
  claiming a clean check.

---

## Real-corpus alignment (2026-07-31, second pass)

`tools/corpus_profile.py` ran on the laptop and produced
`data_profiles/real_corpus_profile.json` (159 KB, committed). It carries
distributions, **table schemas with per-column types and null rates**, and the
identifier lists the collision guard needs. Running the generator against it
found six defects.

### Six defects in the generator, found by the real profile

1. **Synthetic rows were separable by a single `IS NOT NULL` filter.** The real
   corpus leaves `gfw_encounters.imo` **100% null**,
   `gfw_vessel_identity.length_m` and `tonnage_gt` **98.6%**, `imo` there
   **74.8%**, `call_sign` **55.3%**. The generator populated all of them. Any
   precision measured on a combined corpus would have been measuring that
   filter. **The track-level separability test passed the whole time** — it
   compares cadence, noise and speed, and the distinction leaked through the
   *columns*. Fixed: `scenario/nulls.py` masks each field at its measured rate,
   deterministically, with a declared exemption set for the handful of hulls
   whose scenarios need a field present. `verify()` checks the achieved rate
   against the real one and a test locks it in.
2. **`sanctions` was an invented table.** The generator wrote a conformed
   `sanctions` table. There is no such table — real OFAC lives in **DuckDB as
   `ofac_sdn`**. Renamed to `scenario_sanctions`.
3. **The collision guard had the wrong denominator.** It read
   `read_table("sanctions")`, found nothing, and reported clean. Now reads the
   real OFAC snapshot from DuckDB via `ingest/ofac_lookup.py`.
4. **The corpus window ran five days past the real data.** T1 was 2026-07-30;
   the last real event is **2026-07-25 22:53**. Pulled back; E4 and two truth
   windows rescheduled to fit.
5. **Generation was seed-dependent.** Seed 8 overran the window through
   background port calls, because the measured dwell distribution reaches 336 h.
   Seed 7 happened to fit. Background calls are now bounded by the remaining
   window and seeds 7/8/11/42 all validate.
6. **A 2.3-year "port visit" was being sampled as a duration.** See below.

### Two findings in the REAL data

**`gfw_port_visits` durations have a broken tail.** p50 = 107 h, p75 = **1,263 h
(52 days)**, p95 = **20,254 h — 2.3 years**. A port visit does not last 2.3
years. Something in the GFW port-visit ingest produces degenerate durations
across the 3,000-row table. The generator now truncates the tail at 14 days and
**says so in the provenance report** rather than capping silently; the body of
the distribution is still used because it is real. **Worth investigating on the
ingest side — this is a real-data defect, not a scenario one.**

**✅ RESOLVED ON HOST 2026-07-31 — ADR-020. Nothing was wrong with the data;
the measurement was wrong.** Two explanations were tried and refuted before the
raw payloads settled it. What follows is the original reasoning, kept because
the error is instructive; the resolution is at the end of this section.

The durations are not corrupt. The number being profiled was `duration_hours`,
GFW's **event span**, read as though it were time alongside.

A GFW port visit is stitched from up to four sub-events — entry, stop, gap,
exit — and its span covers whichever of them GFW observed. That span is a dwell
only when the vessel **stopped** and the anchorage it **entered is the anchorage
it left**. Otherwise the same number measures a transit across a port polygon,
or two observations at different anchorages with the middle unobserved.

The mapper that wrote those 3,000 rows recorded none of the fields that decide
it. Measured on the corpus: `confidence` and `gfw_confidence_raw` null on
**100%** of port visits, `port_name` — which comes from the stop anchorage and
nowhere else — null on **45.6%**. The mapper was fixed on 2026-07-29; the rows
were not.

What changed:

- `dwell_hours`, populated only where the structure supports the claim.
  `duration_hours` is untouched and unclamped — it still means what GFW said.
- `tools/rebuild_conformed.py` re-derives the conformed tables from the
  **immutable raw JSON** with the current mapper. No network (ADR-013), no new
  window of events, synthetic rows carried through untouched, orphaned real rows
  preserved and reported.
- **~46% of real port visits were being dropped by the graph.** `add_port_visits`
  keyed on `port_id`, which is read from the stop anchorage alone, so every
  stop-less visit was counted as `port_visits_skipped` and never became an edge.
  `visit_port_id` resolves across entry/stop/exit and records which it used.
- A latent landing-layer bug surfaced: a day partition whose optional column is
  all-null gets Arrow type `null` while its sibling gets `double`, and reading
  them together fails outright — **one sparse column makes a whole table
  unqueryable**. `landing.reconcile_null_columns` fixes it after every land.
- The generator emits the same structure mix (stratified, not sampled — 45
  visits cannot hit a 40% target on independent draws; the first attempt landed
  at 64%), so `WHERE dwell_hours IS NULL` is not a synthetic-row detector.

**Still separable, and recorded rather than hidden:** synthetic port-visit
`duration_hours` cannot reproduce the real multi-week tail.

#### CORRECTION — the host run refuted the above (2026-07-31)

`tools/rebuild_conformed.py --dry-run` on the real corpus, `orphans: 0`,
`duration_hours` quantiles identical before and after:

```
observed a stop              3,000 (100.0%)
entry and exit agree         2,611 (87.0%)
entry and exit differ          389 (13.0%)
dwell_hours populated        2,611 (87.0%)

                     p05     p25     p50        p75            p95
duration_hours      4.0h   21.0h   107h   1,261h (52d)   20,242h (843d)
dwell_hours         3.7h   18.8h    92h   1,184h (49d)   19,862h (828d)
```

**Structure does not explain the tail.** Every visit has a stop; 87% are clean
dwells; the clean-dwell p95 is still 828 days; two of the five longest spans
(>5,000 days each) classify as `dwell`.

Two claims were false and are withdrawn:

- **"~46% of visits have no observed stop."** `port_name` is null on 45.6% of
  rows and I read that as a missing stop. The intermediate anchorage is present
  on **100%** of visits — it just has no `name` on 46% of them.
- **"~46% of port visits were dropped by the graph."** Follows from the above
  and is equally false. `port_id` was **100% populated before the change**, and
  `add_port_visits` keyed on it. Nothing was being skipped. **The number was
  never measured — it was inferred from a null rate on a different column.**

**What did survive the host run, all measured:**

| Field | Before | After |
|---|---|---|
| `confidence` | 0% | **100%** |
| `gfw_confidence_raw` | 0% | **100%** |
| `visit_confidence` | 0% | **100%** |
| `visit_port_id` | 0% | 100% (changes nothing today) |
| `dwell_hours` | 0% | 87% |

Plus: `orphans: 0` across all four kinds — **raw really is sufficient to
regenerate the conformed layer, so CLAUDE.md §4.2 holds on this corpus.** That
is the first time it has been checked. And the all-null-column landing bug is
real and fixed.

`dwell_hours` is a narrower, better-defined field than `duration_hours` and is
**not** a fix for the long durations. Any claim that it is "distributionally
matched" is withdrawn.

#### The live hypothesis — needs no bug anywhere

The connector asks GFW for events **overlapping** an eight-week window. A visit
lasting fourteen years overlaps every possible window; one lasting twelve hours
only overlaps if it falls inside. An overlap query therefore over-samples long
events **in direct proportion to their length**, so the observed distribution is
not the distribution of port calls — it is that distribution multiplied by
duration. Separately, the pull returned **exactly 3,000** events, which is a
result cap, not a count.

If that holds, the fix is in **the profiler, not ingest**: measure only visits
fully contained in the query window, and state the cap. Ingest keeps landing
what GFW returned.

#### RESOLVED — the raw payloads, 2026-07-31

`tools/port_visit_forensics.py` on the real corpus. Length bias is the whole
effect and the long visits are genuine.

```
query window: 2026-06-04 .. 2026-07-30 (56 days)
fully inside : 1,278 (42.6%)      crossing an edge: 1,722 (57.4%)

                        p05     p25     p50        p75          p95           max
all visits             4.0h   21.0h    107h  1,261h(53d)  20,242h(843d) 126,414h(5,267d)
contained (unbiased)   2.2h    7.6h   18.7h     46.5h       262h(11d)     1,224h(51d)
crossing an edge      24.2h   133h    855h   2,556h(107d) 35,545h(1,481d) 126,414h
```

**Median contained 18.7 h. Median straddling 856 h. A 46x ratio.** The contained
distribution is an ordinary population of port calls topping out at the window
length. The tail lives entirely in visits already in progress when we started
looking.

**The extremes are correct.** The 5,022-day visit sits at `ind-ind-76`,
`topDestination: ALANG` — **the world's largest shipbreaking yard**, arrived
2012 to be scrapped. Two more are laid up at Pipavav and Ghogha since 2012.
Calling these degenerate was our error, not GFW's.

**GFW's own duration agrees with ours to the second** (`durationHrs`
126,413.72 vs `end - start` 126,413.72). There was never a discrepancy.

Fix is in the profiler and nowhere else — already landed.

#### Three unrelated fields were being read from the wrong place

Found by printing the payloads, none related to the original question:

1. **`durationHrs`, not `durationHours`, nested in the sub-object.** Every
   duration was ours, computed from `end - start`, while GFW's sat unread. They
   agree — but **`gap.durationHrs` was affected identically**, so
   `gap_duration_hours` was null on every gap. Both spellings now accepted at
   both levels.
2. **`topDestination` present on 100% of anchorages, `name` on 54.4%.** The
   readable place — VADINAR, MUNDRA, ALANG — was there all along and unlanded.
   An anchorage rendering as `ind-ind-76` in front of an operator is a worse
   answer than one rendering as ALANG. Now landed, and the generator matches
   the 45.6% unnamed rate so the corpus stays non-separable.
3. **`anchorageId`** is a distinct stable key from `id`. Now landed.

#### Still open: the 3,000 cap

The pull returned **exactly 3,000** port visits in one file — a page limit. The
corpus is a sample of unknown size and unknown selection, so **no count taken
from it describes the Arabian Sea**. `data_health.py` flags any round row count.
Paginating the events connector is the fix and is **not done**.

**Process note.** The first explanation was built by reasoning about what a
field means from a null rate on a *neighbouring* column, and it survived an ADR,
a tool, a validator, sixteen tests and a PR description before meeting the data.
All of it was internally consistent and jointly wrong. The audit output in
`rebuild_conformed.py` is what caught it — an argument for tools that print what
changed rather than assert that it worked.

**✅ RESOLVED ON HOST 2026-07-31 — the landed events carried only `h3_r7`
and `h3_r9`.**

```
table              parts     rows  positioned  cells added  corrected
gfw_encounters         6       14          14           42          0
gfw_loitering         86   24,153      24,153       72,459          0
gfw_port_visits      407    3,000       3,000        9,000          0
gfw_ais_gaps           4        5           5           15          0
                                        27,172       81,516          0
```

**`corrected: 0` is the number that matters.** Every H3 cell already present
was correct — nothing had been derived from a parent resolution, so the 7.2%
ADR-015 failure mode had never occurred in this data. Only the three missing
resolutions (r4, r6, r8) needed adding, and 27,172 × 3 = 81,516 confirms
exactly that with nothing unexplained.

Run on Eshan's laptop against the real corpus, `data/conformed` backed up
first (3,373 files). **This is host-verified, not sandbox-green.** Original
finding below.

 ADR-015 requires all five
resolutions, and the code does that now — but these rows were landed **before**
the fix, so the ingest↔fusion join at res 6 still returns nothing. New tool:
`python tools/restamp_h3.py --dry-run` reports, without `--dry-run` recomputes
the missing cells from lat/lon. This is legitimate rather than a patch of the
conformed layer: an H3 cell is a pure function of the row's own coordinates, so
recomputing produces exactly what re-running the connector would. It also flags
any cell that is present but *wrong*, which would mean something derived a cell
instead of computing it (the 7.2% ADR-015 measured).

### Parameters now measured (7 of 15, from 12,483 real rows)

| Parameter | Old prior | Measured | Rows |
|---|---|---|---|
| flag distribution | IND 8% | **IND 72.5%** | 9,315 |
| port-call dwell (median) | 26 h | **107 h** (tail truncated) | 3,000 |
| loiter duration (median) | 10 h | 5.8 h | 24,153 |
| encounter duration (median) | 6.5 h | 9.2 h | 14 |
| fishing length (median) | 27 m | 14.1 m | 60 |
| bulker / general cargo length | 225 / 120 m | 171.7 m | 46 |
| reefer length | 145 m | 141.1 m | 2 |

Still ASSUMED: all four tanker classes, dhow, naval, encounter separation,
anchorage wait. GFW's `vessel_type` taxonomy is too coarse to separate VLCC from
Suezmax, and 98.6% of identity rows have no length at all — so those stay
published priors and the report says so.

### Measured results after alignment

```
true anomalies:  22   DETECTED 3   MISSED 19
decoys:          16   FALSE POSITIVE 0
deliberate misses: 2   correctly silent 2
precision 100%   recall 14%
```

Recall moved 18% -> 14% between passes. That is **not** a regression to chase:
the null masking removed IMO and length from most rows, which is what the real
data looks like, so the identity-based detectors have less to work with. The
earlier 18% was measured against a corpus that was easier than reality.

---

## Known broken / rough / watch

- **Two of six detectors fire; five of six scenarios in the identity family are
  gated behind an unfunded feed.** `dark_vessel` and `dark_rendezvous` return
  `suppressed_coverage` at `hearable_conf = 0.0` on every verdict, and
  `identity_then_anomaly` is *composite* — it needs one of those to have fired
  first. So fixing its empty input (ADR-025) moved it from **unfed** to **fed
  and gated**, and recall did not move. Not a threshold anywhere in that chain.
- **`test_api_exercise.py` skips itself on a bare checkout.** Roughly 30 of the
  most valuable tests do not run unless the scenario corpus is generated and the
  pipeline has been run. A green tally on a fresh clone proves much less than it
  looks like it does — see the note under the test tally above.
- **No CI.** A workflow was written and then dropped on 2026-08-10 at Eshan's
  instruction. The design that had been verified locally: lint on a
  **real-bug-only** ruff subset (`E9,F63,F7,F82,F811` — the full default set
  reports 153 pre-existing findings, and a gate that fails on day one for
  unrelated reasons teaches everyone to ignore the gate); `shell: bash` on any
  step that pipes pytest, because GitHub's default `bash -e` has **no pipefail**
  and `pytest | tee` would report green on a failing suite; corpus generation
  before the test step, with a guard that **fails the build if the
  corpus-dependent tests skipped**; and a frontend job comparing the committed
  `dist/` bundle hash against a fresh build, since a stale `dist` makes the
  Python-only demo serve a blank white page with the server reporting nothing
  wrong.
- **GFW SAR is offline upstream since 2026-07-03**, pending their migration to
  Sentinel-1C/1D, with a ≥1 month gap announced. Both SAR paths degrade to a
  clear message. Re-check before assuming a SAR pull will return anything.
- **Per-detection SAR has no API.** Only gridded counts are automatable; vessel
  length and AIS-match status come from a manual portal CSV export. See
  DATA_SOURCES.md.
- **GFW ownership data is effectively empty for this AOI** — 4 ownership
  intervals across 300 vessels (<=1.3%), and identity history is 1.05 records
  per vessel. The Phase 4 canonical chain and the identity-change anomaly rule
  both depend on data that is largely absent. Measured, not assumed. See
  DATA_SOURCES.md. Not a reason to skip Phase 4 (ADR-011), but organic firing
  of the chain on real AOI data cannot currently be claimed.
- **`gfw_port_visits` returned exactly 3,000 rows** = exactly 3 pages of 1,000.
  Verify this is a real count and not silent pagination truncation.
- **The vessel-type model trains on declared class, including known lies.**
  `gfw_vessel_identity.vessel_class` carries what a hull *broadcasts*, which is
  right for a registry, and the type model's training fleet is built from it. So
  Area 5's O-hulls — authored to declare a class their motion contradicts — sit
  in the training set as a Suezmax labelled "fishing". In the real world every
  misdeclaring vessel does the same. **Unmeasured, and deliberately so:** the
  single-split score varies 0.700-0.986 across seeds on one corpus, which
  swamps any effect five hulls could have, and a change that cannot be measured
  should not be made and called a fix. Worth measuring properly with the
  multi-split harness now in `test_coarse_accuracy_clears_its_floor`. Not known
  to be hurting anything today.

- **WPI unavailable** — NGA returns 503 on every URL variant including files
  known to exist. Publisher-side outage; retry later. `tools/probe_wpi.py`
  finds a working URL when they are back.
- **No free raw historical AIS for this AOI.** Marine Cadastre is US EEZ only, so
  `ingest/noaa_ais.py` can never contribute a row here. This is a structural gap,
  not a bug — it constrains unit 3.2 and the M3 demo, though not unit 3.1's
  xView3-based exit test. See OPEN QUESTION #7 (mostly resolved).
- **The EU sanctions URL is unconfirmed** — it may require a token. The connector
  reports and skips rather than failing the other three.
- ~~Execution spec and roadmap missing from the repo.~~ **RESOLVED 2026-07-29** —
  both committed at `c59352b`, along with DECISIONS.md. Roadmap renamed
  `bastion-product-roadmap.md` -> `maritime-isr-product-roadmap.md`, which
  completes ADR-012. Reading them produced ADR-014 (Phase 0 criteria amended) and
  upgraded the H3 finding to ADR-015.
- ~~TWO H3 helpers at THREE resolutions.~~ **FIXED 2026-07-29 (ADR-015).**
  `tiling.py` deleted, `h3util` is the only helper, all five resolutions
  (4/6/7/8/9) declared there and computed directly from lat/lon.
  `landing.stamp_h3` now stamps every resolution, so ingest and fusion tables
  **can** join. Fusion baselines re-measured and unchanged (96.9% / 100% / 75%),
  as expected from preserving res 6. Guard tests keep it from regressing.
  Historical detail below.

  ```
  tiling.py  res 6 (H3_RESOLUTION)  -> detect/pipeline, fusion/associate,
                                       fusion/dark, tracks/{builder,coverage,
                                       features}, connectors/{ais,registries}
  dark.py    res 8 (STATIC_RES)     -> static-object clustering
  h3util.py  res 7 + 9              -> ingest/landing, ingest/registries, writer
  ```

  Ingest tables stamp res-7/9; the fusion core joins on res-6. **Different
  resolutions are different cell IDs, so those joins return nothing.** Harmless
  today only because nothing consumes the ingest tables yet; **blocking before
  Phase 3 touches this data.** Fix is structural (one helper, all resolutions
  computed directly from lat/lon, never via `cell_to_parent` — measured 7.2%
  disagreement, see ADR-015) and is **its own session**, not a patch. Fusion
  baselines must be re-measured afterwards, not carried forward.
- **SNAP + ARM is unproven** (OPEN QUESTION #1) — deferred; PARKED under
  ADR-013, so it blocks nothing currently in flight.
- Every accuracy or capability statement so far is **synthetic-suite only.** Real
  precision must be re-measured on the host and will be lower.
- GitHub web-UI uploads keep reintroducing `RENAME_AFTER_UPLOAD.md` after it is
  deleted. Dotfiles themselves are correct in git. Do not re-upload that file.

---

## OPEN QUESTIONS — ask Eshan, do NOT invent an answer

0. **`fusion/contact_profile.py` is in the wrong package, and Area 5 is the
   second area to want it there.** It joins the cascade's verdict, an inferred
   type, an inferred activity and the zone layer onto one description of a
   contact — it PROFILES and never fuses, and nothing in it is fusion-core
   logic. Area 5 wanted to add "and the camera at Mumbai imaged her at 14:20 as
   a tanker-shaped merchant" to that sentence, which is exactly the same kind of
   description, and **declined to**, because the brief's standing caution is
   that an area needing a change to the fusion core has found a defect in the
   core rather than a reason to patch one.

   So the imagery evidence reaches the operator through the assistant instead,
   which is where assembly belongs. That is a defensible home. But the profile
   is now the one place a dark contact is described end to end and it cannot
   carry the strongest evidence the system has about her, which is a real cost.

   The question is whether `contact_profile.py` should move out of `fusion/` —
   to `tracks/`, beside the type and activity inference it already calls, or to
   package root beside `coastline.py` and `baselines.py`. It is a rename plus
   an import change and it touches no logic. **Do not do it silently**: it moves
   a module the radar work and the assistant both read, and CLAUDE.md §7 names
   its current location.

1. **Does SNAP (and pyroSAR) actually run on the Oracle ARM (aarch64) VM?**
   SNAP is JVM-based so it *should*, but native processors and some operators can
   be architecture-sensitive, and this has never been tested on-host. If it does
   not: options are (a) an x86 burst instance for preprocessing only, (b) a
   different SAR toolchain, (c) accept a paid x86 VM for the preprocessing step.
   **This gates all of Phase 1.** Validate before deep SNAP debugging.

2. **Where does the CNN (unit 1.3) train — VM CPU overnight, or a free Colab GPU
   session?** The spec allows either. On ARM CPU, ResNet-18-class training is
   feasible but slow; Colab is x86+GPU but adds a manual step and data-shuttling.
   Decide before unit 1.3.

3. ~~**During bootstrap (no VM), what is `MISR_STORE_BACKEND`?**~~ **ANSWERED
   2026-07-28:** `local` is now the default in `config.py`. The env var still
   overrides, so the deploy host flips to `mirror` when R2 exists.

4. **Registry refresh cadence.** Sanctions/port lists refresh on cron with
   diff-on-refresh, but the interval isn't pinned numerically (daily? weekly?).
   Pick before wiring the 0.4 cron entries.

5. **The two-week live precision sample (unit 3.2)** requires the VM capturing live
   for a continuous fortnight. That's a wall-clock dependency, not a coding one —
   flag the calendar cost when Phase 3 approaches so it isn't a surprise.

6. **Roadmap doc naming.** `bastion-product-roadmap.md` keeps the retired "Bastion"
   codename in its title (see ADR-012). Leave as-is as a historical artifact, or
   rename the file? Cosmetic; confirm before touching, since Eshan tracks commits
   per-unit. **Note:** the file is not currently in the repo at all.

7. ~~**THE BIG ONE — what is the product, given there is no free raw AIS for this
   AOI?**~~ **MOSTLY RESOLVED 2026-07-29.** The question conflated two different
   things: how the association engine is *proven*, and what the product *does
   over our AOI*. Splitting them dissolves most of it.

   **Resolved — how association is proven.** Unit 3.1's exit test is
   *"association accuracy ≥85% on **xView3's** non-ambiguous matched-AIS
   contacts"* (spec line 126; roadmap 3.4 agrees). xView3 ships with matched AIS,
   so the association engine is benchmarked against a public dataset and **never
   needed AOI AIS to be validated.** Absence of free AOI AIS does not block
   Phase 3's association exit test, and does not stall Phases 1, 4, 5 or 6.

   **Still open — the product over AOI v1.** Unit 3.2's exit test still requires
   *"a two-week live sample"* of reviewed dark-vessel alerts at ≥70% precision.
   That needs our own SAR contacts plus the 2.2 coverage model over the AOI, and
   the coverage model needs AIS reception data we do not have. So: **the exit
   test can pass on xView3 while the AOI product remains unbuildable on free data
   alone.** Recording that distinction rather than letting a green benchmark
   imply a working product.

   The three options below therefore apply to unit 3.2 and the M3 demo, not to
   the engine's validation:

   **(a) Re-aim at enrichment.** Treat GFW's gap/encounter events as the
   dark-vessel signal and make our contribution the *fusion* — sanctions
   exposure, ownership chains, satellite-pass geometry, risk scoring, the
   evidence-chain product surface. Deliverable now, zero funding.

   **(b) Fund satellite AIS (Spire or similar).** Restores the original design
   end to end (ADR-005). Costs money and needs a deploy host.

   **(c) Long live capture on an always-on host.** aisstream.io is free and the
   connector is written but PARKED (ADR-013); coastal-only, no backfill, weeks of
   wall-clock before it is useful.

   Not mutually exclusive — (a) now, (b) or (c) later. Still **do not let this be
   decided by default**, but it no longer gates the build.

9. **Should the scenario corpus simulate GFW's `gfw_intentional_disabling`
   verdict?** Right now it does not — every synthetic gap lands the column
   `None`, by an explicit decision in `scenario/scenarios/common.py`, on the
   grounds that inventing another organisation's assessment puts words in their
   mouth and could hand the answer to any detector that read it.

   **Half of that decision's stated justification is now refuted.** The
   docstring also argued "the real corpus has exactly zero gaps flagged
   intentional, so the combined column stays honest." That was measured against
   a column that was null because of a mapper bug. The host run on 2026-07-31
   found **5 of 5 real gaps flagged `intentionalDisabling=true`**. So across a
   combined corpus the real gaps are 100% flagged and the synthetic ones 100%
   null, and `gfw_intentional_disabling IS NULL` is a **single-filter
   synthetic-row detector** — the exact defect class `scenario/nulls.py` exists
   to close (ADR-019).

   The practical cost: the Findings screen's dark-gap section — the most
   valuable rows in the real corpus — is **empty on any sandbox corpus** and
   populated only where the real rows live. Nothing can exercise it here.

   *Not fixed, deliberately.* Simulating a third party's assessment is a
   decision about what the scenario corpus may claim, not a bug fix. If it is
   taken, the defensible construction is to derive GFW's flag from the physical
   cause **and** the modelled reception at the off-position — true only where an
   intentional shutdown happened inside plausible coverage, null where it
   happened outside it, false for other causes in coverage — so the column is
   not a copy of the answer key, plus a guard test that no detection path reads
   it. **Ask Eshan before doing it.**

10. **Should an individual scenario vessel be marked ON SCREEN?** Right now it
    is not. `SyntheticBadge` renders `null` — a deliberate no-op with a comment
    saying the distinction "is preserved in the data layer and communicated
    outside the product." The data layer *is* honest: `is_synthetic` on every
    row, every count split, and the exported incident report labels a scenario
    vessel top, bottom and in its filename.

    But on the map, in the vessels table and in the vessel panel, **a generated
    hull renders exactly like a real one.** With a corpus that is deliberately
    both (ADR-019), an operator looking at a screen has no way to tell which
    they are pointing at, and "communicated outside the product" is a promise
    about a conversation rather than a property of the thing.

    The inconsistency got sharper on 2026-08-10: the report now shouts SCENARIO
    DATA while the screen it was exported from says nothing. Two defensible
    resolutions — mark it on screen too, or drop the label from the report for
    consistency — and they point in opposite directions, so **ask Eshan.**
    (README.md claimed a `SCENARIO` badge and a violet treatment "everywhere
    they appear" until this was found; that text has been corrected to describe
    what is actually rendered.)

8. **Terminology: "D1"/"D2" are retired.** They were never defined in the repo
   and collided with the `D-0x` decision-ID shorthand. Refer to work by execution
   spec unit numbers (0.0–6.3) or in plain language. What earlier sessions called
   "D1" is the download-only rework of ingest — **units 0.1, 0.3 and 0.4**.
   `tools/d1_report.py` keeps its filename for now; call it the ingest report.

   *Related:* `DECISIONS.md` uses **`ADR-0xx`** IDs, not `D-0x`. There is no
   `D-01` or `D-02` in the file. Session notes referring to "D-01" mean
   **ADR-001** (free data first). Flagging rather than renaming, since ADR IDs
   are cited across the docs.

---

### OQ-radar-1 — is the AIS emitter's thinning model right for a shore receiver?

**Asked 2026-08-16 (ADR-028). ANSWERED the same day (ADR-029) — and answered
differently than the question expected. The text below is kept verbatim because
the reasoning that framed it is still the right reasoning; the resolution
follows it.**

`primitives/ais.py` models reception as `thin = 12 / coverage^0.9` on top of the
M.1371 transmit interval. For a vessel **at anchor** the transmit interval is
180 s, so even at good coverage the landed interval comes out near **fifty
minutes**. The radar sees her every five.

That single number is the binding constraint on radar↔AIS correlation: between
receipts the prediction cone opens to kilometres, the association correctly
declines to claim a match, and only about one radar track in nine gets resolved
to a hull. It is also why twelve of fifteen early false positives were ordinary
anchored merchants.

A real terrestrial AIS receiver in line of sight of an anchorage hears a Class A
transmitter far more often than once an hour. If the thinning model is wrong,
the correlation number is an artefact of the generator and not a property of the
system — and quietly retuning it would turn a measured weakness into a
fabricated strength, which is the one thing this corpus exists to prevent.

**What would settle it:** any real terrestrial AIS capture, even an hour of it,
measuring the landed interval for a vessel at anchor within ~50 km of the
receiver.

**RESOLVED 2026-08-16 (ADR-029) — the thinning model was not the cause, and the
1-in-9 figure was wrong.**

Two things were wrong, and neither was the generator.

1. **The measurement counted genuinely dark vessels as correlation failures.**
   A radar track of a vessel with her transponder off *cannot* resolve to a
   hull; that is the finding, not a miss. It was in the denominator.
2. **`associate_scene` gated every contact in a 15-minute epoch at the epoch's
   timestamp**, not at the instant the contact was actually observed. A merchant
   at 13 knots covers 5.5 km in fourteen minutes, and the gate was being asked
   to absorb that as error. One function — each contact gates at its own time —
   fixed it.

A third fix addressed the sparsity this question was actually about, and did it
in the filter rather than the generator: `state_at` now **interpolates between
two known fixes (a Brownian bridge) instead of extrapolating forward from the
earlier one**, cutting the 95th-percentile position error at the midpoint of a
gap from **4,120 m to 1,450 m**.

Corrected figures, on the same denominator the old one used — radar tracks whose
vessel was demonstrably on AIS during the track:

| | |
|---|---|
| resolvable tracks | 996 |
| resolved to the **right** hull | **978 (98.2%)** |
| resolved to the wrong hull | 11 (1.1%) — 10 reported `ambiguous`, i.e. not claimed |
| left unresolved | 7 (0.7%) |
| tracks of a genuinely silent vessel | 241 — 224 correctly left dark, 14 followed across a short gap onto **her own** MMSI, **0 confidently explained away by another hull** |

**The thinning model may still be unrealistic** and is still worth checking
against a real capture — an anchored Class A transmitting every 180 s should not
land once every fifty minutes. But it is no longer load-bearing for any number
this project quotes, so it is a curiosity rather than an open question, and it
must not be quietly retuned to make a figure look better.

## Demo readiness (2026-07-31)

**`python tools/data_health.py` — run it before every demo.** Read-only, exits 1
on any BLOCKER. It grades the bytes on disk, not the code's intentions, because
every data defect this project has hit was found late and by accident while
looking at something else: the H3 resolutions (ADR-015), the two vessel
keyspaces, the port-visit durations (ADR-020). Each was invisible in a row count
and obvious the moment somebody printed the right number.

| Level | Meaning |
|---|---|
| **BLOCKER** | the demo would state something false, or a core query returns nothing |
| **WARN** | it works, but a number on screen is weaker than it looks |
| **INFO** | measured context |

Blockers: unreadable table (including the all-null-column type conflict that
takes down a whole table at once), missing H3 at any of the five resolutions, a
gap in the provenance envelope, `is_synthetic` disagreeing with `source_id`, and
a missing raw store.

Warnings currently expected on the real corpus:

- **No flagged dark-vessel gaps.** The demo cannot show a *real* dark vessel
  from this corpus. Run the scenario corpus alongside and label every figure as
  synthetic (ADR-019), or widen the pull.
- **Thin encounter graph.** 14 encounters across the whole AOI and window; a
  network view has nearly nothing to draw, so prefer a ranked table.
- **Multi-year port-visit spans.** Do not render these as "time alongside" —
  ADR-020 is open.
- **Possible result cap.** Exactly 3,000 port visits is an API page limit, not a
  count of what is in the Arabian Sea. Say "the first N returned"; an operator
  will otherwise reasonably hear the second thing.

### The duration measurement is now de-biased

`tools/corpus_profile.py` measures `port_call_dwell_hours` from **visits that
began and ended inside the query window**, and writes the unfiltered figure
separately as `port_visit_span_hours` so the difference is visible in the
profile rather than argued about.

The reason is arithmetic, not a theory about GFW: the connector asks for events
*overlapping* an eight-week window, and an overlap query over-samples long
events **in direct proportion to their length**. A fourteen-year visit overlaps
every possible window; a twelve-hour one only overlaps if it falls inside. Any
quantile over the whole table inherits that, which is how the figure reached a
p95 of 2.3 years while every visit in it was structurally sound.

The cost is stated rather than hidden: genuine long stays are excluded, so the
de-biased figure **understates** the real tail. For a number that feeds a
generator which must not put a background vessel alongside for two years, that
is the right way round. When `data/raw/` is absent the window cannot be
recovered and the profiler **says it cannot de-bias** instead of silently
falling back.

---

## Vessel keyspace — CLOSED 2026-08-01 (ADR-022)

### The framing in this file was wrong, and the correction matters

This file recorded: *"alerts land on nodes the landed graph never sees."*
**Measured: alerts resolve to a node 4 of 4 — 100%.** A presence check passed
throughout. The defect was an **empty provisional stub shadowing a populated
hull**, not a severed join:

| Keyspace | Minted by | Nodes | Out-edges | Edges/node |
|---|---|---|---|---|
| `vessel:gfw:<vessel_id>` | `from_landed` | **114** | **616** | **5.4** |
| `vessel:mmsi:<mmsi>` | `resolve_mmsi` | **8** | **8** | **1.0** |

8 of 8 stubs had a fully-populated twin. Every alert landed on the stub, whose
entire content was `{"mmsi": …, "provisional": true}`. Clicking it reached
nothing.

**Root cause, narrower than either description:** `from_landed.add_identities`
published **`id:name:*` nodes only** — 115 of them, **zero** `id:mmsi:*` or
`id:imo:*`. `resolve_mmsi` reads `id:mmsi:*`, found nothing, and minted a twin.
One side never published the key the other side reads.

**There were four keyspaces, not two:** `vessel:gfw:*`, `vessel:mmsi:*`,
`vessel:imo:*`, and the scenario's own `vessel:<key>` in the `vessel_id` column
and in `scenario_truth`.

### Measured after the fix

| | Before | After |
|---|---|---|
| MMSIs resolving to a populated hull | **0 / 103** | **102 / 103 (99.0%)** |
| Median out-edges on a resolved hull | 1 | **4** |
| Provisional stubs minted | 8 | **0** for known MMSIs |
| Node id shape | `vessel:gfw:vessel:spine` | `vessel:gfw:spine` |

The one MMSI that does not resolve is a vessel's **second** MMSI probed before
its swap — time scoping working as designed. Forcing 103/103 would break B1's
phoenix and B4's zombie.

### **The 14% was NOT primarily a join artifact**

```
                     BEFORE          AFTER
true anomalies       22  DET 3       22  DET 3
decoys               16  FP 0        16  FP 0
deliberate misses     2  silent 2     2  silent 2
precision           100%            100%
recall               14%             14%
```

**Unchanged, exactly as predicted before the fix.** No prior tuning conclusion
needs voiding on join grounds. What the fix bought is the click-through — an
alert now reaches a hull with a median of 4 edges instead of a stub with 1 —
which was a demo blocker on its own.

### ⛔ Every real-vs-synthetic VESSEL count before 2026-08-01 is void

`add_vessels` omitted `is_synthetic`, and the column defaults to 0, so **all 114
scenario hulls landed flagged as real** while identity and gap nodes beside them
were flagged correctly. ADR-019 makes that flag the only thing separating the
two populations. The error runs in the direction that **inflates the real side**.
`GraphStore.node` also did not return the column, so the accessor most callers
use could not see it. Both fixed; guarded by a test that checks the flag **per
node type**, because that is exactly how it hid — the totals looked plausible
while one type was entirely wrong.

*Provisional, not measured:* this sandbox holds synthetic rows only. The
real-corpus behaviour of all of the above needs a host run to confirm.

---

## Why 19 of 22 scenarios still miss — the miss-cause account

Attributed by the rule each scenario's `scenario_truth` row expects. **None of
these is a threshold. Nothing was tuned.**

| Blocking cause | Rules blocked | Scenarios | Evidence |
|---|---|---|---|
| **Fusion stage never runs** | `dark_vessel`, `dark_rendezvous` | A1, A2, A3, A4, B1, E6 | `tools/run_scenario_pipeline.py` passes `associations=[]`, `verdicts=[]` |
| **`events` table is empty (0 rows)** | `identity_then_anomaly` | B1, B2, B3, B6, D2 | rule reads `SELECT … FROM events WHERE event_type='identity_changed'` |
| **`INTENTIONAL_SILENCE` is unreachable** | everything gap-based | A-group, C-group | see below |
| **Spoof detector sensitivity** | `ais_spoofing` | A3, C1, C2, C3 | 1 spoof event from 103 tracks |
| **Geofence coverage** | `loitering_sensitive` | A4, E1, E2, E3, E7 | **4 of 50** synthetic loitering events fall inside any of the 4 `SENSITIVE_ZONES` |
| **High-risk port list** | `port_risk_propagation` | A5, E4, E6 | `HIGH_RISK_PORTS = {Karachi, Kandla}` only |

### The gap classifier is not conservative — it is unreachable

All 4,108 gaps classify `COVERAGE_GAP`. That is **structural, not a threshold**:

```
default SatPassSchedule windows : 0
passes_within(any interval)     : 0
nominal_period_s()              : 0.0
```

Walking `classify_gaps`'s decision tree with those values:

1. `covered and n_passes >= 2 and not in_spoof` — `n_passes` is always 0 → **never taken**
2. `n_passes == 0 and period and …` — `period` is `0.0`, falsy → **never taken**
3. `sat_cov >= 0.5` — needs a receiver whose name starts `sat`; the corpus is terrestrial-only → **never taken**
4. `else: COVERAGE_GAP` → **always**

`INTENTIONAL_SILENCE` requires ≥2 satellite passes (ADR-004's conviction rule),
and **no satellite AIS feed is configured** (ADR-005 — Spire unfunded). So
`dark_rendezvous` is silent because of an upstream classifier that cannot reach
its own positive branch, not because of its own logic. **Not fixed this
session.** The honest reading is that this is ADR-005's cost arriving in the
detector, and it is a funding decision rather than a code one.

### Architecture finding: the graph is populated and no detector reads it

`port_risk_propagation` looked like the one graph-traversal rule. It is not — it
reads `extract_features(track)["port_calls"]`, string-matches against a
two-entry dict, and writes a `docked-at` edge as **after-the-fact evidence**.
Checking the other five: `dark_vessel`, `dark_rendezvous`, `ais_spoofing` and
`loitering_sensitive` all take their inputs from the track engine and use the
graph only to resolve an id and record evidence. `identity_then_anomaly` reads
the `events` table, which is empty.

**No detector traverses the graph to reach a conclusion.** We have 624 edges of
ownership, flag, sanctions, port and encounter structure that nothing in the
detection path consults. That is the distance between *"we built a graph"* and
*"the graph does work"*, and it is the reason D1's ownership convergence can only
be detected by accident.

Recorded, not fixed. It is a design question, not a bug.

### Flagged only, no action taken

- **`events` table empty**, blocking `identity_then_anomaly` outright.
- **Layout drift from CLAUDE.md §7**: empty `fuse/` and `rules/` packages sit
  alongside the populated `fusion/` and `anomaly/`. Two of the four module
  boundaries named in the operating contract do not exist as described.
- **Three separate port gazetteers**: `tracks/features.AOI_PORTS` (8 ports, no
  Sikka or Vadinar — where most scenario tanker traffic goes),
  `anomaly/library.HIGH_RISK_PORTS` (2), and `scenario/geography.PORTS` (11).

---

## The corpus was drawn on land — 51.3% of it (2026-08-09)

**Found by looking at the map**, which is the point of Phase 6: vessels sitting
in the middle of Gujarat, and routes from sea to port that flew straight over the
Saurashtra peninsula. Every other validator was green — the points were inside
the AOI, inside the corpus window, at plausible speeds and plausible turn rates.

**Measured:** 51.3% of landed synthetic AIS positions on land at the start;
**0.039% now** (83 of 211,562), and those are moored hulls alongside the Mumbai
and JNPT quays, where a berthed ship legitimately is. Worst single track went
from 26% to 0.33% (one point). Status: **built + verified in sandbox** — this is
the synthetic corpus on this machine, not a claim about real feeds.

### Four distinct defects, not one

1. **No routing at all.** Every transit was a great-circle line. Fixed with
   `scenario/searoute.py`: a 17-waypoint coastal corridor, port approach
   fairways, and a local `_repair` pass that fixes any leg the corridor cannot
   describe — which is how enclosed water like the Gulf of Kutch is handled
   without enumerating it.
2. **Destinations on land.** Routing solves passages between two sea positions;
   it cannot rescue a target *in* Kachchh. Rendezvous approach starts and
   departure targets were computed as a bearing and a distance and never checked.
   Fixed with `nearest_water()` (snap, preferring water the origin can reach) and
   `seaward_point()` (steam out on a heading as far as there is sea).
3. **A departure bearing measured from the berth and applied from the
   anchorage** — two different origins, so the 80 nm ray was a line nothing had
   checked. It pointed north out of the Gulf of Kutch and sent a fifth of the
   commercial fleet 150 km into the Rann of Kutch for a day and back.
4. **`crosses_land` under-sampled.** At 0.5 km spacing against a ~1 km mask, a
   600 km Mumbai-to-Okha leg clipped the Gujarat coast and was reported clear.
   Now 0.25 km. **Caught only because the test samples finer than the code does**
   — a test that asks the module its own question can only confirm the module is
   self-consistent.

### Six ports were town centres

Sikka, Vadinar, Mundra, Kandla, JNPT and Mumbai reference points were on land.
Moved into the water beside the terminal. JNPT genuinely sits 0.5 km from land
inside Mumbai harbour and was **not** moved further — that is where the port is,
and the 1 km mask cannot resolve a harbour channel.

### One thing deliberately not done

The plan's **origin is never snapped**. Snapping it was the obvious symmetry and
it was wrong: scenarios continue a vessel from where her last segment ended, at
the same instant, so a start that jumped even 500 m made her cover it in zero
time — `add_track` correctly rejected it as a hull doing 29.8 kn. She gets an
extraction leg to the nearest water instead, preserving both her real position
and her continuity.

### New permanent checks

- `afloat` validator — per-track land rate, tolerance **3%** (measured: worst
  track 0.33%, corpus 0.006%, the defect it catches ran 8-26%).
- `test_every_port_pair_routes_clear_of_land` — all 1,260 ordered pairs of ports,
  anchorages and corridor waypoints, sampled at 0.2 km. This is the check that
  would have caught the original defect on day one.
- Tests for `nearest_water`, `seaward_point`, and the `afloat` rule itself
  (driven with a known-bad and a known-good track, not asserted on its constant).

**Unchanged by this work:** the `loitering_sensitive` false-positive defect
(29 alerts on ordinary commercial vessels at Kandla anchorage) is still open and
still waiting on a decision. Re-measured after the fix: identical.

---

## The loitering rule was a Kandla anchorage detector (2026-08-09)

`loitering_sensitive` fired **33 times; 29 were ordinary merchant vessels**
queueing at the Kandla anchorage, which sits inside the "Kandla pipeline
corridor" geofence. About **12% precision** against ADR-004's stated 70% floor.

**Fixed.** Background false positives: **29 → 0**. Status: built + verified in
sandbox, on the synthetic corpus.

### Why adding the missing ports did not fix it

The previous session added Sikka, Vadinar and Gwadar to `AOI_PORTS` expecting
that to suppress these, and it did nothing. The reason is a layer that did not
exist rather than an entry that was missing: **`PORT_RADIUS_KM` is drawn around
a terminal, and a ship waiting for that terminal is not at it.** She is at the
designated anchorage 15-30 km further out — that is what an anchorage is for.
Kandla's anchorage is 30 km from the Kandla berth coordinate, so an 8 km radius
could never have reached it however many ports were listed.

Added `AOI_ANCHORAGES` (9 charted waiting areas) and `ANCHORAGE_RADIUS_KM = 10`.
The radius is the size of a designated anchorage area, **not** a number tuned
until the alerts went away — sizing it on the observed false positives would be
fitting the detector to this corpus.

### The recall cost was illusory, and the earlier estimate was wrong

Reported before this fix: *"recall 14%, and suppressing the anchorage drops it
to ~5%."* The 14% was inflated and the trade never existed.

The two detections it removes are **B4** and **D1**, both credited via
`loitering_sensitive`. Neither scenario is about loitering:

| scenario | what it is | `expected_anomaly_types` |
|---|---|---|
| B4 | zombie IMO — hull recorded demolished 2019-11 | `identity_then_anomaly` |
| D1 | ownership convergence two hops up | `port_risk_propagation` |

Both fired loitering because their vessels made an ordinary port call at Kandla
and waited at the anchorage — **the same false positive as the other 29**. The
harness was already saying so and it was not read carefully enough: *"of 3
detections, 1 came from the rule the scenario expected."*

**Genuine recall was 5% before and is 5% after.** One real detection, B5 via
`ais_spoofing`, which is B5's declared type. What changed is that the number is
now honest.

### The rule now fires zero times, and that is the finding

`loitering_sensitive` contributes nothing to this corpus. **No scenario in the
catalogue loiters inside a sensitive geofence away from a waiting area**, so the
rule had nothing to find and its 32 alerts were all anchorage queueing. It was
not a detector that was mistuned; it was a detector pointed at the wrong thing.

Two tests now pin both halves, because a rule silenced everywhere would also
show as "no false positives":
- `test_queueing_at_an_anchorage_is_not_loitering` — stopped at Kandla anchorage,
  no episode.
- `test_the_loitering_rule_still_fires_away_from_a_waiting_area` — stopped in the
  Mumbai High field, 130 km from the nearest waiting area, still an episode.

### Open, not fixed

- **Scenario-level precision/recall still counts an alert of the wrong type as a
  detection.** `ScenarioOutcome.type_match` exists and the summary prints the
  count, but `outcome` and the family P/R table do not use it — which is how B4
  and D1 read as true positives for four sessions. Fixing this changes published
  numbers, so it is flagged rather than changed quietly.
- **Nothing exercises the geofence layer.** `SENSITIVE_ZONES` is a four-entry
  seed and no scenario is built against it. Either the catalogue needs a
  sensitive-zone loitering scenario, or the rule is not earning its place in the
  demo.

## Demo scope-cut, 2026-08-01 — fusion wired, gazetteer consolidated

Two plumbing fixes only. **No thresholds tuned. Coverage model, graph detector
and events table deliberately untouched.**

### 1. The fusion stage now runs (Phase 3)

`tools/run_scenario_pipeline.py` passed `associations=[]` and `verdicts=[]` as
**literals**, so `detect_dark_vessel` iterated an empty list and
`detect_dark_rendezvous` short-circuited on all 5,880 encounters. Two of six
detectors had never been asked a question, and were being reported as silent.

Now running, over the same corpus:

```
scenes           : 4 (6 contacts)
registry entries : 9 MMSI -> length
associations     : 6  (2 matched, 4 unmatched)
dark verdicts    : 4  {'suppressed_coverage': 4}
```

**Also fixed:** association rows carried no `lat`/`lon`, and
`detect_dark_rendezvous` read `a["props"]["lat"]` — a key nothing ever wrote.
Its footprint branch could never be taken; the rule could only fire through
`gap_party`. The contact's position now travels with the association.

**Both dark rules still fire zero, for reasons that are not thresholds:**

- `dark_vessel` — all 4 verdicts are `suppressed_coverage` with
  **`hearable_conf = 0.0`**. The contacts sit in the deep basin, outside
  terrestrial reception, and there is no satellite AIS. The cascade is
  **correct** to suppress: asserting darkness where we cannot hear is a false
  positive by construction (CLAUDE.md §6). This is ADR-005's unfunded feed, not
  a detector fault.
- `dark_rendezvous` — the nearest unmatched contact is **730 km** from any
  encounter; the threshold is 3 km. The SAR contacts and the encounters are in
  different parts of the AOI entirely. **Scenario-authoring gap**, flagged not
  fixed: A1–A3 place a rendezvous and a SAR contact but never co-locate them.

### 2. One port gazetteer (ADR-023)

`tracks.features.AOI_PORTS` held 8 ports with **no Sikka and no Vadinar** — the
two Gujarat crude terminals the generator places most tanker traffic at. A full
laden voyage into Vadinar produced an **empty** `port_calls` list, silently.

Consolidated into `maritime_isr/ports.py` (16 ports), plus five real-corpus
ports using GFW's own anchorage coordinates (Pipavav, Alang, Hazira, Magdalla,
Ghogha). Port matching now takes the **nearest** port rather than the first
dictionary hit — Mumbai and JNPT are 11 km apart and both inside the radius, so
the old answer depended on iteration order.

**Measured:**

```
tracks with >=1 port call : 81 / 104
ports called              : Vadinar 64, Mundra 48, Sikka 33, Mumbai 20,
                            Mangalore 14, Kochi 11, JNPT 9, Kandla 8, Pipavav 1
calls at a high-risk port : 8  (Kandla)
```

Vadinar and Sikka — the top two — were invisible before. Scenario generation is
byte-identical; determinism test green.

`port_risk_propagation` **still fires zero, and that one IS a threshold**:
Kandla's weight is 0.4, the rule's threshold is 0.5, so a Kandla call alone can
never clear it. Karachi (0.7) would, and no track calls there. **Not tuned —
reported**, per the standing instruction.

### Re-measured: unchanged

```
true anomalies    :  22   DETECTED 3   MISSED 19
decoys            :  16   FALSE POSITIVE 0   correctly quiet 16
deliberate misses :   2   correctly silent 2
precision 100%    recall 14%
```

Three sessions have now moved recall by zero. That is itself the finding: the
misses are **not** in the detectors' logic. They are upstream of it — an
unfunded satellite feed, an empty events table, a scenario whose SAR contacts
and encounters do not overlap, and one threshold set above the only weight any
track can reach.

446 tests green.

### Flagged this session, no action taken

- **Only 9 of 103 vessels have a length in the registry**, because `length_m` is
  null-masked to match the real corpus (98.6% null). Association therefore
  applies almost no length gate and is running on proximity alone. Real, and a
  direct consequence of the fidelity work — not a regression.
- **A1–A3 do not co-locate their SAR contact with their rendezvous.** 730 km
  apart. Fixing it means regenerating the corpus.

---

## Merging ADR-023 surfaced a second copy of the same defect (2026-08-09)

The `claude/maritime-isr-synthetic-scenarios-lk95t3` branch (ADR-023 — run the
fusion stage, one port gazetteer) had been sitting unmerged for eight days and
was 12 commits behind. Merged now. Two things came out of it.

### The gazetteers are one list, and it carries anchorages

`maritime_isr/ports.py` is the single gazetteer. The merge resolved three ways
at once and all three mattered:

- **ADR-023's structure wins** — one list, nearest-port matching instead of
  first-dict-hit (Mumbai and JNPT are 11 km apart and both inside the radius, so
  the old answer depended on iteration order), plus five real-corpus ports on
  GFW's own coordinates.
- **The water-verified coordinates win** — ADR-023 predates the routing fix and
  carried the old centroids, six of which are on land. Merging its values
  verbatim would have put the fleet back in Gujarat.
- **The anchorage layer moves into it** — `AOI_ANCHORAGES` was a second literal
  in `tracks/features.py`, which is the same duplication ADR-023 exists to end.
  `ports.at_waiting_area()` now answers "is a stopped vessel here just waiting
  for a berth" from both layers, and is the only place that question is asked.

`scenario.geography.PORTS` and `ANCHORAGES` are now views onto that list.
Generation is unchanged — same coordinates, so the corpus is byte-identical.

### `port_risk_propagation` was firing on "is in the Kandla trade"

**This defect did not exist on either branch alone.** ADR-023 measured it firing
zero and said so honestly; so did main. The merge made Kandla calls register
properly for the first time — their wider gazetteer plus our water-corrected
Kandla berth — and then it fired **8 alerts, every one background traffic, none
on the cast.**

The cause is one term. Score was `max_port_risk + 0.05 * (len(risky) - 1)`,
where `risky` is the call *sequence*. Kandla's weight is 0.4, three calls added
0.10, and eight ordinary merchants landed on exactly the 0.50 gate. But a liner
working a Kandla rotation calls there every circuit **because that is its
trade** — a repeat visit is not a fresh risk event.

Fixed by boosting on **breadth** (distinct high-risk ports) rather than on visit
count. One hull touching both Karachi and Kandla is genuinely unusual; one hull
calling at Kandla three times is a schedule. Visit counts stay in the evidence,
because "called here four times" is worth showing an analyst even when it is not
a reason to alert.

**Open:** whether repeat *intensity* deserves a signal of its own. It would need
a per-port baseline of normal call frequency, which this corpus cannot provide.

### Measured after the merge, on a rebuilt graph

```
alerts        ais_spoofing 1, everything else 0
background    0 alerts on entities with no truth row  (was 8)
scenarios     1 of 22 detected, 0 false positives across 16 decoys,
              both deliberate misses silent
              of 1 detections, 1 came from the rule the scenario expected
fusion        4 scenes, 6 contacts, 6 associations (1 matched),
              5 dark verdicts — all suppressed_coverage
```

Three sessions have now moved recall by zero. That remains the finding: the
misses are upstream of the detectors — an unfunded satellite feed (ADR-005), an
empty `events` table, and a scenario whose SAR contacts and encounters never
co-locate.

### ⚠ Stale alerts survive a re-run, and they corrupt the measurement

Found while verifying the above. `run_scenario_pipeline.py` **never clears the
alerts table**, and alert ids are deterministic, so a row emitted by an older
build stays in `data/graph.sqlite` forever. After the fix above, re-running the
pipeline still reported the 8 alerts a current build cannot emit — the number
only went to 0 after deleting `data/graph.sqlite` by hand.

This is the failure CLAUDE.md §8.2 exists to prevent: the harness reporting
something the code no longer does. **Until it is fixed, delete
`data/graph.sqlite` before re-running the pipeline after any detector change.**

Not fixed here because the honest fix is not "clear the table" — the graph is
deliberately append-only (§6, it cannot be backfilled). Alerts carry a
`source_ref`; the measurement should filter on the current pipeline version
rather than trusting the table. That is a harness design change and wants its
own decision.

---

## Demo data coverage — what the demo shows, and four things it was discarding (2026-08-10)

**The question:** *"What data do we have in our demo? Is it only synthetic? We
need to show more than we currently are."* Answered by measurement, not by
reading the docs — the corpus was regenerated in the sandbox and every number
below came off a running API.

### What the demo actually held, measured

Sandbox (a fresh clone with `scenario generate` + `run_scenario_pipeline`):

```
vessels     210 synthetic /   0 real     alerts        1 synthetic / 0 real
events      432 synthetic /   0 real     sanctions    19 synthetic / 0 real
ais_position 211,562 synthetic / 0 real  scenes        0 (catalog is on the laptop)
```

**So: in the sandbox, 100% synthetic.** On the laptop it is both — real and
scenario rows share every table, split by `is_synthetic` on every count.

**The sharper answer, and the one that matters for the demo:** *everything that
moves on the map is synthetic, always.* The time scrubber animates vessels
along AIS position tracks, and the real corpus has **zero AIS positions** —
there is no free raw AIS for this AOI. Real data can only ever appear there as
static event dots plus scene footprints. Equally, **real alerts will always be
0** on the current build: every detector reads the track engine, and there are
no real tracks to read (ADR-005).

### Four things landed and reaching no screen — all now fixed (ADR-024)

1. **UN + EU sanctions were matched against nothing.** 7,028 real designated-
   entity rows landed since the first live run, read by no code path outside a
   reporting script. `sanctions_match.py` now matches all three registries, on
   terms that fit what UN and EU actually are (no vessel record type, so IMO
   from free text plus a vessel-marker gate on name matching). Indexes are built
   **per registry** so OFAC's published 126/98 cannot move for a UN-shaped
   reason, and a hull designated by two lists lands two rows — corroboration,
   not ambiguity.
2. **The map silently truncated the real corpus.** `limit: 4000` applied per
   kind with `ORDER BY start_time` meant 24,153 real loitering events rendered
   as the earliest 4,000 and stopped, reading as "nothing happened after
   mid-July." Added `/api/events/density` (per-H3-cell counts over **every**
   row, drawn as graduated markers), and `/api/events` now returns `truncated`
   per kind with the true total, surfaced on screen.
3. **The 636 Sentinel-1 footprints defaulted to a layer that was off.** On.
4. **`scenario_detections` had no endpoint,** so the map could not draw a radar
   contact at all. Added `/api/detections` + a layer that draws an unmatched
   contact **hollow** — the shape of a dark vessel, with the word withheld.

Plus the screen the data was always asking for: **`/api/findings` and a Findings
tab**, leading with GFW-assessed intentional-disabling gaps and then the
IMO-matched sanctioned hulls, each row expandable to its evidence. Rank is a sum
of named signals, never a blended score, and a test asserts the priority equals
the sum of the reasons shown.

### Two real-vs-synthetic divergences found while building it

Both are the ADR-019 separability family, and both were invisible until the same
code read both corpora:

1. **`ofac_name` meant two different things** — a listed *vessel* name from the
   real matcher, a *company* name from the scenario generator (which reaches a
   hull through its owner). So "sails under a different name than the listing"
   fired on **19 of 19** scenario rows, turning the identity-laundering signal
   into noise. Fixed with an explicit `listed_entity_type` on both sides.
2. **The matcher wrote `ship_name`/`flag`/`imo`; the API reads `vessel_name`/
   `vessel_flag`/`vessel_imo`.** The scenario generator wrote the latter, so the
   sanctions panel looked right on scenario data and rendered **blank vessel
   fields on the real corpus**. The matcher now writes both.

### Status, stated precisely

**Built + verified in sandbox.** 480 tests green (28 of them new, covering the
findings contract, density-vs-page, truncation honesty and detection
labelling), frontend builds, and both screens were rendered in a headless
browser and read. **None of this has run against the real corpus** — that lives
on the laptop, and every number above the "measured" heading is from the
scenario corpus.

### What Eshan needs to run, in this order

```
python -m maritime_isr.cli ingest registries          # refresh UN + EU
python -m maritime_isr.cli ingest sanctions-match     # now all three registries
python tools/data_health.py                           # the demo gate
python -m maritime_isr.api                            # then open /findings
```

**The matcher must be re-run** — `sanctioned_vessel_matches` gains `registry`,
`listed_entity_type` and the `vessel_*` fields, and `registry` joins the natural
key, so rows landed earlier carry retired semantics. `--registries OFAC`
reproduces the previous behaviour exactly if the new number needs comparing
against the old one.

*Success:* it prints a per-registry breakdown, a tier breakdown, and — if any
hull is designated by two lists — a line naming how many. *The number to look
for:* how many findings UN and EU add beyond OFAC's 126. **Report it even if it
is zero** — zero is a finding about the free data (UN and EU list far fewer
vessels than OFAC, and mostly DPRK-related hulls that may not trade in this
AOI), not a failure.

Then open `/findings`. On the real corpus its top section should hold the **5
GFW-flagged intentional-disabling gaps**. If it is empty there, the flag did not
survive the rebuild and `tools/rebuild_conformed.py` needs re-running.

### Still open after this

- **The scenario corpus cannot exercise the dark-gap section** — see OPEN
  QUESTION #9. Synthetic gaps land `gfw_intentional_disabling = None`, so
  `IS NULL` is a synthetic-row detector across a combined corpus. Deliberately
  not fixed; it needs a decision, not a patch.
- **Real alerts remain structurally impossible** and no work here changes that.
- **The 3,000-row port-visit page cap is still unpaginated**, so no count taken
  from that table describes the Arabian Sea.

---

## The export, and the events table (2026-08-10, second pass) — ADR-025

### The M6 demo definition is now fully built

`CLAUDE.md` §0's last clause — *"export a one-click incident report"* — did not
exist anywhere: no endpoint, no button, no renderer. It does now.
`GET /api/vessels/{id}/report` returns a self-contained HTML document (or the
same payload as JSON), and there is an **Export report** button on every
findings row and on the vessel panel.

Verified end to end in a headless browser: clicking the button downloads
`SCENARIO-incident-report-desert-zenith-2026-08-10.html`, 6,554 bytes, no page
errors. The `SCENARIO-` prefix on the filename is not cosmetic — it is the
label that survives the file being forwarded.

The report is available for **any** vessel, not only flagged ones, because an
analyst deciding whether a hull is worth flagging is exactly who needs to hand
over what is known about it.

### `identity_then_anomaly` was unfed, not mistuned — and it still misses

This file recorded the blocker as *"the `events` table is empty (0 rows)"*.
**The cause was a missing writer**: `identity_changed` had exactly one
producer, `identity.fold_registry_snapshot`, which the scenario pipeline never
calls — it goes through `from_landed.populate()`. `add_identities` was already
computing genuine supersession for the rename analysis, so it now emits the
event alongside, for name / flag / MMSI (not IMO — a permanent hull number
changing is a different and stronger claim).

```
identity_changed events    12, across 5 hulls  (name 2, flag 7, mmsi 3)
detectors firing            2 of 6   (was 1 of 6)
identity_then_anomaly       1 alert  (was 0)

true anomalies  22   DETECTED 1   MISSED 21     <- UNCHANGED
decoys          16   FALSE POSITIVE 0           <- UNCHANGED
precision 100%   recall 5%                      <- UNCHANGED
```

**The rule fires and recall does not move.** The alert lands on `clone_ghost`
= B5, which `ais_spoofing` had already detected; B5 now carries two alerts.

**Why B1, B2, B3, B6 and D2 still miss, and it is not their thresholds:**
`identity_then_anomaly` is a **composite** rule — it needs an identity change
*and* a `dark_vessel` / `dark_rendezvous` / `ais_spoofing` alert on the same
hull inside 14 days. Those five hulls now have the identity change and no
companion alert, because both dark rules are structurally silent (every verdict
`suppressed_coverage`, `hearable_conf = 0.0`). **The rule is gated behind
ADR-005's unfunded satellite feed**, one layer further back than this file
previously recorded.

The honest correction to make here: "the events table is empty" was a true
observation and a misleading diagnosis. Fixing it moved the detector from
*unfed* to *fed and gated*, which is progress worth having and is not recall.

### Status

**Built + verified in sandbox.** 525 tests green (31 more than the previous
pass — 11 on the identity-change plumbing, 12 on the report, plus the ADR-024
set). Frontend builds; the download was exercised through a real browser, not
just the endpoint. **None of it has run against the real corpus.**

### What to look for on the laptop

The report is where the real corpus will differ most visibly: on your data the
top findings carry **GFW-flagged intentional-disabling gaps**, so their reports
gain the gap-assessment table and the GFW attribution paragraph that no sandbox
report can show. If those sections are missing there too, the flag did not
survive `rebuild_conformed.py`.

`identity_changed` will also be far denser on real data — 9,184 vessels with
identity history, against 5 hulls here — so it is worth printing the count and
checking it is not implausibly large. A number near the fleet size would mean
the supersession rule has regressed to counting interval closure, which is the
100%-closed trap and the one failure mode this change had to avoid.

---

## "Was anyone watching?" — imaging opportunities over AIS gaps (2026-08-13, ADR-026)

### What was built

`maritime_isr/overpass.py` + `maritime-isr overpass`. For each AIS gap GFW
flagged as intentional disabling, it works out where the vessel could
physically have been at the moment of each Sentinel-1 pass during the silence,
and compares that area against the scene footprint. Lands
`sar_imaging_opportunity`; surfaces on the findings gap rows and in the
exported incident report.

**It needs no pixels.** Both inputs are already on disk: 636 Sentinel-1
catalogue records (footprint polygon + acquisition time, landed 2026-07-29) and
the gap events. Nothing new is downloaded, so ADR-013's 1 GB cap is untouched.

### The thing that made it possible, which this file had not recorded

**Gap rows carry `gap_off_lat/lon` and `gap_on_lat/lon`** — the positions where
AIS stopped and where it resumed — alongside both timestamps. That is a known
start point, a known end point and an elapsed time, which is a solvable
geometry problem. It was found by reading `ingest/gfw_events.py`, not from any
document.

### The measured finding, and it reframes the capability

Built a realistic fixture (three gaps of 3 h / 9 h / 28 h; 14 scenes on a
~11.7 h repeat with ~250 km footprints) and ran the real CLI against it:

```
by tier: confirmed: 0   partial: 3   none: 1   unknown: 0
2 of 3 gap(s) had at least one imaging opportunity
  S1A_..._20260712T103000  partial, 10% of area, t+4.5h into the gap
  S1A_..._20260712T221200  partial,  8% of area, t+16.2h
  S1A_..._20260711T110600  partial,  7% of area, t+5.1h
```

**Zero confirmed, and that is geometry rather than a bug.** At 20 kn the area a
vessel could occupy passes the ~62,500 km² of one Sentinel-1 footprint about
**four hours** into a gap. So:

- a **short** gap can be bounded inside a single footprint outright;
- a **long** gap can only be bounded by a pass near one of its **ends**;
- a mid-gap pass on a 24-hour silence lands `partial` at roughly **10%**.

`partial` is therefore the ordinary outcome, not the exception. The tool was
rewritten mid-build to serve that regime: the scene shopping list draws from
partial rows too, ordered by coverage, because printing only confirmed rows
would report an empty list on most real corpora while useful scenes sat in the
table. Pinned by a characterisation test so a later threshold change cannot
quietly turn thin coverage into confident claims.

**What this means for the pitch.** "We can tell you which satellite images
would resolve this gap" survives. "We can tell you the vessel was definitely
photographed" survives only for short gaps or well-timed passes. Say the first.

### What it may never claim

A `confirmed` row means *an image exists whose footprint necessarily contained
the vessel* — nothing about what the image shows, because nobody has looked and
the pixels are not downloaded. The sentence "no image has been examined and no
vessel has been detected" is in the module, the CLI output and the report, with
tests on all three. Partial coverage is reported as an **area fraction, never
as a probability**. And this does not re-assess the gap: whether the silence
was intentional stays GFW's determination (ADR-017).

**It does not rank a vessel** (ADR-026d). A satellite having flown overhead
says nothing about whether a hull is suspicious — it says the question is
resolvable, which is a different axis. Folding actionability into a suspicion
score would build the blended number ADR-024 declined to build.

### Two things it does that are easy to get wrong

- **A gap nobody imaged lands an explicit row** (tier `none`), so
  evaluated-and-unwatched is distinguishable from never-run (ADR-021). "Nobody
  was watching" is itself a finding about coverage.
- **A gap whose endpoints are further apart than 20 kn explains is flagged, not
  dropped.** The assumed speed is raised just above what the gap requires so
  the geometry stays valid, and `implied_speed_exceeds_vmax` records it. Per
  CLAUDE.md §6 an apparent teleport is a spoofing tell, not a bad row.

### Status, stated precisely

**Built + verified in sandbox.** 49 new tests (26 geometry, 23 seam/landing/
report); full suite **567 passed, 10 skipped** with the scenario corpus
generated. The CLI was exercised end to end against a fixture through the real
landing path, and the report section was rendered and read.

**It has never run against the real corpus.** The 636 scenes and the 5 flagged
gaps live on Eshan's laptop. Every number above is fixture geometry.

### Two sandbox limits worth knowing before it runs for real

1. **The scenario corpus cannot exercise it.** Synthetic gaps land
   `gfw_intentional_disabling = None` (OPEN QUESTION #9), so the flagged path
   finds nothing here — `pytest -rs` says so directly: *"no GFW-flagged gaps in
   this corpus."* `--all-gaps` reaches them, but the sandbox has no scene
   catalog either. Both failure modes print the connector to run.
2. **Wall-clock overlap holds, and had to.** The scene catalogue was pulled 90
   days back and the gap events 8 weeks back, both ending on the same date, so
   GFW's window sits inside the Sentinel-1 one. If either is re-pulled alone
   that stops being true and the join goes quiet for a reason that has nothing
   to do with ships.

### What Eshan should run, and what to report back

```
python -m maritime_isr.cli overpass          # after the ADR-024/025 commands
```

*Success:* a tier breakdown, and for each opportunity a scene id with a
coverage percentage. **Three numbers to report back, whatever they are:**

1. **How many of the 5 flagged gaps got any pass at all.** Zero is a real
   result — it means Sentinel-1 was not overhead during those silences.
2. **The best coverage percentage across all of them**, and whether any gap
   reached `confirmed`. Given the geometry above, expect `partial` and expect
   the number to be low.
3. **Whether any row has `implied_speed_exceeds_vmax` set.** On real GFW data
   that would be a hull whose gap endpoints do not agree with 20 kn — worth
   looking at directly, and the one output here that could be a spoofing tell.

Then export an incident report for a vessel that got a pass, and read the
**Satellite imaging opportunities** section end to end. It is the first section
in that document describing something we computed rather than something we
were told.

### Flagged this session, no action taken

- **Union across passes is not computed, deliberately.** If five passes each
  cover 10% of the reachable area, the honest combined figure is *not* 50% —
  the reachable region differs at each pass time, so combining them is a
  statement about trajectories rather than points and needs a different
  construction. Each pass is reported on its own. Raising it rather than
  approximating it.
- **`v_max = 20 kn` is assumed, not measured.** The landed AIS positions in the
  scenario corpus could give a real speed distribution for the fleet; the real
  corpus has no AIS positions at all, so there is nothing to measure it against
  where it matters.

### A latent defect the fixtures exposed (2026-08-13) — not fixed, needs a decision

`tests/test_api_exercise.py::test_alerts_carry_evidence_chains` **fails**, and
it is nothing to do with ADR-026 — it fails identically on a clean tree with
this session's changes stashed. Verified that way rather than assumed.

```
assert al["subject"].startswith("vessel:")
AssertionError: 'detection:det_SYN3_022_00154'.startswith('vessel:')
```

**Why nobody saw it before.** It needs the graph populated by *both*
`tools/run_scenario_pipeline.py` **and** the Phase 1–6 runners
(`make_synthetic_feed_phase2.py` → `make_synthetic_scenes_phase3.py` →
`make_synthetic_orgworld_phase4.py` → `run_phase{2..5}_synthetic.py` →
`run_phase6_product.py`). That combination had apparently never been built in
one tree, because the phase runners need three generator steps that nothing
documents as prerequisites — each one fails with a bare `FileNotFoundError`
naming a file, and you discover the chain by walking it.

**What it means.** Phase 3's `dark_vessel` alerts are subjected to a
**detection id**, while the API contract and every consumer assume an alert
subject is a vessel. So a dark-vessel alert cannot be joined to the hull it
concerns. That is the same failure family as ADR-022 (one canonical vessel key,
published by the side that owns it) reappearing on the alert side.

**Not fixed, deliberately.** The fix is a choice — resolve the detection to its
associated vessel before emitting, or declare that alert subjects are
polymorphic and teach every consumer — and that is a design decision touching
ADR-022, not a patch to slip into an unrelated commit. **Ask Eshan.**

**Also worth recording:** the test tally in this file has never been reproduced
from a bare checkout, because the fixture chain above is undocumented. Anyone
quoting it should say which fixtures were present.

---

## The demo's two loading defects (2026-08-13, second pass)

Both reported from the laptop: *"data isn't loading in Graph, and the timeline
player isn't visible — it came on once, then disappeared when I switched menus
and came back."* Both were about **when** data arrives, not whether it exists.

### 1. The time scrubber was requested last, behind a 3-second call

`MapView` fires eight requests on mount. The scrubber's window came from
`/stats`, requested **eighth**. A browser opens ~6 connections per origin, so it
queued behind `/tracks` — **measured at 3.06 s** against the scenario corpus,
40× the next slowest call:

```
tracks 3.060s   events 0.262s   density 0.084s   detections 0.040s
scenes 0.038s   ports  0.039s   stats  0.194s    corpus-window 0.073s
```

And the scrubber rendered only once its window existed (`{window_ && …}`), so
for those seconds the demo's primary control was **absent**, not loading. React
Router unmounts the view on navigation, so returning to the map paid the whole
wait again — exactly the "it disappeared" report.

**Four changes, in order of how much each mattered:**

1. **Request the window first.** It no longer queues behind anything.
2. **Session-cache it** (`api.corpusWindow`) — a corpus window cannot change
   while the server is up, so a return visit does not reach the network at all.
3. **Keep the scrubber mounted always**, disabled and faded while waiting. A
   control that comes and goes reads as a broken page; a disabled one reads as
   a loading page.
4. **A cheap `/api/corpus-window`** — two aggregates per event table instead of
   `/stats`, which also groups the sanctions matches, counts scenes, measures
   length coverage and walks the graph. Worth 0.194 s → 0.073 s here, and more
   on the real corpus. This was the *smallest* of the four, contrary to my
   first diagnosis; the ordering was the fix.

*Measured after, in a real browser:* scrubber present and live **at 400 ms** on
first load and **at 350 ms** on return, against absent-for-3-seconds before.

### 2. The Graph view opened empty and stayed that way

It required choosing from a dropdown of thousands, and most choices produce a
single circle because **GFW registry ownership covers ~1.3% of hulls in this
AOI** — so exploring by hand mostly confirms an impression that the graph is
broken.

New `/api/graph/seeds` ranks vessel nodes by edge degree
(`GraphStore.top_connected_nodes`), and the view auto-seeds on the best one when
no `?seed=` is given.

**The honesty constraint on this:** it is a presentation choice and changes no
stored fact. A vessel missing from the seed list is less **connected**, not less
suspicious, so the panel says which claim it is making — *"Opened on MV SYN 001
— the most connected vessel in the graph (25 edges), chosen automatically. It is
the best-connected hull, not the most suspicious one."* The empty state now also
distinguishes "no ownership edges in the graph at all" from "you have not picked
a vessel", which look identical on screen and mean different things.

### Status

**Built + verified in sandbox and in a real browser** (Chromium, both views,
including the navigate-away-and-back case). 10 new tests; suite **583 passed / 4
skipped / 1 pre-existing failure**. The frontend bundle was rebuilt — `dist/` is
committed, and a stale one serves a blank page.

### Flagged, not chased

`/api/graph/seeds` reports `is_synthetic: false` for hulls named `MV SYN 001`.
Those come from the **Phase 1–6 fixture world**, which predates ADR-019 and is a
separate synthetic corpus from the scenario one — so this is probably correct
rather than a flag drift, but it has not been checked and ADR-019 puts the whole
real/synthetic split on that column.

---

## The Graph opens on the whole network (2026-08-13, third pass)

Operator request: make the default Graph state show **every relationship in the
dataset as one web**, focused on a particular node. Built, with two constraints
that had to be measured rather than assumed.

### The layout was the whole problem, and `cose` could not do it

The view used cytoscape's built-in `cose`, a force simulation that compares
every pair of nodes on every iteration. Measured end-to-end in Chromium on a
graph shaped like the real one:

| nodes | `cose` | `fcose` |
|---|---|---|
| 219 | 2.5 s | 1.9 s |
| 900 | — | 5.8 s |
| 1,409 | **115 s** | 6.5–7.0 s |

115 seconds is a hung tab, not a slow render. `fcose` (added as a dependency)
seeds from a spectral draft and approximates repulsion with a quadtree —
O(n log n) per iteration instead of O(n²) — for the same force-directed look and
the same deterministic settling. `cose` is kept below 250 nodes because the
neighbourhood view's appearance is tuned to it.

Two things found while tuning, both by measuring rather than reasoning:

- **`quality: "draft"` throws.** Draft skips the spectral seeding that
  `randomize: false` then expects, and fcose dies on `Cannot read properties of
  undefined (reading 'nodeIndexes')`. Determinism is worth more than the seconds
  draft would have saved — a web that reshuffles every visit cannot be learned.
- **The "laying out 1,409 nodes…" status never painted.** React 18 batches, so
  neither `setTimeout(0)` nor a double `requestAnimationFrame` ran after the
  commit. `flushSync` fixed it. Without it the operator stares at ~5 blocked
  seconds with the previous message on screen.

### The cap, and why it is 1,500

The real corpus graph is an estimated **~19,000 nodes / ~22,000 edges** (9,184
vessels plus identity intervals, flags, ports). No in-browser force layout will
draw that, so the view shows the **most-connected core** up to a cap.

1,500 rather than 900 because most of the cost is fixed overhead, not per-node:
900 → 5.8 s against 1,409 → 7.0 s, so 66% more graph costs about a second.

Degree-ranked rather than an arbitrary slice: a random cut is both incomplete
*and* unrepresentative — scattered fragments implying the data is sparse when it
is not.

**The truncation is stated on screen**, with both totals: *"Showing 900 nodes and
1,125 relationships of 1,409 and 1,634 in the graph — this is a partial picture,
the most-connected core."* A partial web that looks whole is exactly how a
viewer concludes the dataset is smaller than it is.

### The focus, and what it does not claim

Criteria, in order: **most-connected sanctioned vessel → most-connected vessel →
most-connected node**. Sanctions designation comes first because it is the only
finding-grade signal available at the node level — `_RANK` already treats it as
evidence rather than something this view invented. Degree breaks the tie because
the point of a web is structure.

The camera frames the focus **neighbourhood**, not the whole web: opening fully
zoomed out shows a grey mass and nothing legible; the web is still all there.
The panel states the basis and refuses the stronger reading — *"That is where
the camera starts, not a finding: it is the best-connected node, not the most
suspicious one."* A test asserts the basis string never contains
"suspicious"/"risky"/"dangerous"/"dark".

### One defect found by building it

`subgraph_by_degree` first filtered `t_end IS NOT NULL`, which **dropped 191 of
344 edges** on the fixture graph — because `neighbourhood()` and
`counts_by_synthetic()['edges_current']` both resolve latest-wins *without* that
filter. The web would have silently disagreed with every other edge count in the
product. Ended edges are now kept and **drawn dashed and dimmed**, carrying
`is_current`, which satisfies invariant 3 without hiding them: it reads as "was
true", which is what it is.

### Labels

The web holds up to 1,500 nodes and `syncLabelScale` pins label size to the
*screen*, so zooming out stacks text rather than shrinking it away. The web view
labels only what is worth naming at rest — the focus neighbourhood, every
organisation and sanctions authority, anything designated, and the 40 best-
connected hubs. Everything else reveals on hover, the interaction the view
already had. The neighbourhood view still labels everything.

### Status

**Built + verified in sandbox and in a real browser** at 219 and 1,409 nodes,
including the navigate-away-and-back case. 11 new tests (21 in that file); suite
**594 passed / 4 skipped / 1 pre-existing failure**. `dist/` rebuilt.

**New frontend dependency: `cytoscape-fcose`.** A `git pull` alone will not
install it — `npm install` in `frontend/` is required before `npm run build`.

---

## Coastal radar as a source (2026-08-16, ADR-028)

### What was built

The Indian Coast Guard's primary sensor is coastal radar: stations producing
tracks with position, course and speed and **no identity**. No such feed is
available to us, so the picture is simulated over the *same vessel truth* the
synthetic AIS is emitted from, and landed through a real connector
(`ingest/radar.py`) into a real table (`radar_track_report`), flagged synthetic,
travelling the identical code path.

Then the existing machinery, unmodified, is pointed at it: the track engine
builds radar tracks, `associate_scene` correlates them against AIS epoch by
epoch with the same Hungarian assignment, and the survivors go through the same
`dark_cascade`. **The dark-vessel path fires for the first time in this
project's history, on data that needs no SAR imagery.**

### The measured result — SYNTHETIC, through the landed pipeline

| | |
|---|---|
> **Superseded 2026-08-16 by ADR-029.** The corpus was regenerated and three
> defects were fixed after ADR-028 merged, so the figures below are the current
> ones and differ from that ADR's. In particular the **"one radar track in nine"
> correlation figure published in ADR-028 was wrong and is withdrawn** — see
> OQ-radar-1 above for what was actually wrong with it.

| | |
|---|---|
| radar plots landed | 271,684 |
| station track numbers issued | 2,408 → 1,277 tracks after the track engine |
| dark episodes in the picture | 12 — **7 a correct system should find**, 4 explainable by AIS coverage, 1 legitimately dark (naval) |
| dark contacts produced | **3** |
| **precision** | **100%** (0 false positives) |
| **recall** | **43%** (3 of 7 findable episodes) |
| fired on the 4 out-of-coverage episodes | **0** — the anti-pattern guard holds |
| fired on the naval decoy | **0** |
| correlation: resolvable tracks resolved to the right hull | **98.2%** (978 of 996) |
| correlation: genuinely silent tracks explained away by another hull | **0** |
| tracks reaching the queue with a transponder-shutdown position | **1** — the sentence ADR-028 could not say |

Recall is low on purpose. ADR-004 makes precision the binding constraint and
explicitly accepts missing half the real dark vessels rather than burying an
analyst; three of the four misses are dark runs of 60–95 minutes against a
deliberate 120-minute persistence floor, and that floor was set by measurement
(at 40 minutes: 20 contacts, precision 40%, nine of twelve false positives being
brief holes of exactly that kind).

### Four places the core assumed AIS — all silent failures

The connector claim in CLAUDE.md §4.5 had never been tested. It is now, and it
held only after four fixes. Every one would have failed quietly:

1. `detect_encounters` rejected self-pairs with `mmsi_a == mmsi_b`. Both are
   `None` on radar, so **every radar-to-radar pair was discarded** and the
   detector could not fire at all. It would have read as "radar sees no
   rendezvous".
2. The anomaly library resolved subjects with `resolve_mmsi(store, tr.mmsi)`,
   which on `None` mints `vessel:mmsi:None` — a node that resolves, passes a
   presence check, and is a different fiction per track.
3. `classify_gaps` would have labelled radar dropouts `INTENTIONAL_SILENCE` —
   the offshore-silence anti-pattern arriving through a new sensor.
4. The reuse guard was seven days. On a recycled station track number that
   merged hundreds of targets into single **11,829-plot** tracks.

### And one defect that was not about radar at all

The association score normalised by the **gate radius**, so it grew more
permissive the less was known. A twelve-hour-stale AIS track has a ~900 km cone;
a contact **186 km away** scored −0.13 against a −8 floor and matched. Measured
before the fix: matches at 36, 61, 77, 131 and 187 km. Restoring the missing
volume-normalisation term of a 2-D Gaussian log-density fixes it, and the
well-constrained case is numerically unchanged.

**This was latent on the SAR path and invisible there**, because the whole
synthetic SAR corpus is six contacts placed beside fresh AIS tracks.

### The static-object layer ran for the first time

It has shipped since Phase 3 with nothing to consume: the SAR corpus holds six
contacts in total. Radar reports a mooring buoy every quarter of an hour for
eight weeks. It absorbed the fixed installations and suppressed 212 contacts —
but only after its recurrence threshold became a parameter: at the SAR value of
3 scenes it produced **58 "installations", 54 of which were shipping lanes**.

### Status, stated precisely

- 🟡 **Built, sandbox-green.** The whole radar path runs end to end in Claude's
  sandbox over the generated corpus — connector, correlation, cascade, landing,
  three API routes and a Radar tab, the last of these driven in a real Chromium
  against a real uvicorn process and screenshotted. **None of it has ever run on
  Eshan's machine**, and that is the one status line in this file that no amount
  of further work in the sandbox can change.
- ⬜ **No real radar data exists and none is expected.** `ingest/radar.py` has a
  `run(path)` entrypoint for a CSV or newline-JSON station feed. It is
  **untested against any real system** and says so in its own docstring.
- Every figure above is synthetic. Nothing here says anything about performance
  on a real Coastal Surveillance Network feed.

### What Eshan needs to run, in this order

**This is the item nothing in Claude's sandbox can close.** Every figure above
is measured in the sandbox on generated data. Converting *built, sandbox-green*
into *verified on the host* needs someone with access to the laptop, and that is
only you — I cannot reach your machine. Four commands, in order:

```
rm -f data/graph.sqlite                        # start the alert store clean
python -m maritime_isr.cli scenario generate
python -m maritime_isr.cli radar correlate --write
python tools/run_scenario_pipeline.py
```

**1. `rm -f data/graph.sqlite`** — *success* is silence. This is not optional
when detectors have changed: the graph accumulates alerts across runs by design,
so a stale store makes the measurement at the end score two rule sets at once.
The pipeline now prints a NOTE if it finds alerts already there.

**2. `scenario generate`** — *success* is `validation ... 0 violation(s)` on
every check and a `radar_track_report` row count near **272,000** in the LANDED
block. Takes a few minutes. *Failure* is any line reading `N violation(s)` — copy
the whole validation block back.

**3. `radar correlate --write`** — *success* is a `radar ↔ AIS correlation
(SYNTHETIC)` block reporting **~1,277 radar tracks**, then `landed N row(s) into
radar_correlation`, then a short list of dark contacts each with a position, a
length and a station — and at least one of them followed by a line beginning
`last explained by MMSI`. Takes **20–40 minutes** and prints nothing in between;
if it looks hung, it is not. *Failure* looks like `no landed radar_track_report
data`, which means step 2 did not finish.

**The `--write` matters.** Without it the correlation prints and is discarded,
and the Radar tab in the UI stays empty — the API serves landed tables and
cannot redo a forty-minute correlation per request.

**4. `run_scenario_pipeline.py`** — *success* is a `3b. coastal radar` section,
a `4b. every behavioural detector, over radar-sourced tracks` section, and a
`dark-contact results` block at the very end with a precision and a recall. Also
**20–40 minutes**.

Then, to see it: `python -m uvicorn maritime_isr.api.app:app --port 8000` and
open `http://127.0.0.1:8000/radar`. *Success* is a page headed **COASTAL RADAR —
DARK CONTACTS** with an amber SYNTHETIC banner, three contact cards, and a
checkbox that reveals the suppressed verdicts. The Map tab gains three radar
layers in the layer box.

Any Python traceback should be pasted back **whole** — the last line alone is
usually the least informative part of it.

### Open after this

*(Revised 2026-08-16 by ADR-029 — three of the five items below were closed the
same day. They are struck through rather than deleted so the record of what was
open, and what closing it took, survives.)*

- ~~**Correlation resolves 1 track in 9.**~~ **CLOSED.** The figure was wrong;
  98.2% of resolvable tracks resolve to the right hull. See OQ-radar-1 above.
- `coast_dark_party` (R3) is missed outright: the Gulf of Kachchh has as many
  broadcasters as contacts in her neighbourhood, so the census cannot say
  anything is unexplained.
- The AIS gap classifier emits **26,778 gaps and zero `INTENTIONAL_SILENCE`**
  on this corpus, because that verdict needs two completed satellite passes and
  there is no satellite-AIS schedule at all. This predates the radar work and is
  already recorded above; the radar path now has a check that depends on it and
  can therefore only ever suppress, never confirm. It is written so that a
  *missing* gap row falls through rather than suppressing, which is what keeps
  a real shutdown findable.
- ~~**Only one of the two headline sentences is earned.**~~ **CLOSED.** Both are
  now. The census is skipped for a row where a *named* hull was watched stopping
  (`identity_known`), because that evidence stands without her neighbours, and
  `_receipts_between` decides whether she really went quiet by counting her
  actual receipts during the dark run rather than by any threshold. One contact
  reaches the queue carrying a shutdown position, and the UI draws the segment
  from her last AIS fix to where radar was still holding her.
- ~~Nothing in the UI shows radar. The API has no radar endpoints.~~ **CLOSED.**
  Three routes and a Radar tab — see ADR-029(c). The suppressed verdicts are one
  checkbox away, and the synthetic flag is on the screen and not only in the
  database.
- **10,920 radar-only encounters exist on this corpus.** That is what a
  continuous sensor over eight weeks of coastal fishing traffic looks like, and
  it is the reason `dark_rendezvous` is still a long queue after ADR-029
  tightened it from **667 alerts to 81** — 68 of them on contacts nobody can
  name, which is the shape of the finding, and **zero** on named background
  traffic, down from 76. The principled discriminator is
  geographic — a raft-up inside a designated anchorage is not a rendezvous — and
  that needs the zone layer, which is Build 2. **Do not tune a distance
  threshold to hide it.**
- **A red slow test that predates this work.** `test_generation_is_robust_across_
  seeds` fails on **seed 8**: `afloat: vessel:fleet_16 [DX10] — 3% of this track
  is on land, e.g. (21.7831, 69.9213)`. Verified against a clean worktree at the
  pre-ADR-029 commit — seeds 7, 11 and 42 pass there and seed 8 fails there too,
  so this is a pre-existing land-routing gap in the Gulf of Kutch and not a
  regression. It is marked `@pytest.mark.slow`, which is why it has been passing
  unnoticed in ordinary runs. Not fixed here: it is scenario-route generation,
  not Build 1, and fixing it blind would be guesswork.

---

## Closing Build 1 (2026-08-16, second session, ADR-029)

Four things were listed as unfinished when ADR-028 merged. Three are closed;
the fourth cannot be closed from here and that is the honest answer to it.

### 1. The published correlation figure was wrong

ADR-028, this file and the README all said radar↔AIS correlation "resolves about
one radar track in nine". **It does not, and never did.** The figure came from a
probe with two faults:

- it counted radar tracks belonging to **genuinely dark vessels** as correlation
  failures — those are the finding, not a miss;
- and `associate_scene` was gating every contact in a 15-minute epoch at the
  **epoch's** timestamp rather than the contact's own. A merchant at 13 knots
  covers 5.5 km in fourteen minutes; the gate was absorbing that as error.

The code fault is fixed (`_t_of(c)` — one function, no change to the assignment
or the score) and a second fix, `state_at` bridging between two known fixes
instead of extrapolating from the earlier one, cut r95 position error at the
midpoint of a gap from **4,120 m to 1,450 m**. The corrected figures are in
OQ-radar-1 above. The headline: **98.2% of resolvable tracks resolve to the
right hull, and zero genuinely-silent tracks were confidently explained away by
somebody else's.**

This is recorded loudly because the wrong number went out in a merged PR and in
three documents. Withdrawing it is the point of writing it down.

### 2. "Here is where its transponder went quiet" reaches the queue

Computed for 77 tracks and reaching the alert queue for none, before. It needed
three things and two of them already existed:

- the **radar track has to span the transition** — held while she transmits and
  after she stops. This is why only one track qualifies on seed 7: the coastal
  network's cover is patchy, and most vessels that go quiet do so out of sight
  of the station that was watching them;
- **`_receipts_between`** decides whether she went quiet by counting her actual
  receipts during the dark run. No threshold: the vessel that survives was heard
  **0 times in five hours**, the one that does not was heard **66**. Two
  cleverer tests were tried first (median reporting interval, then p90) and both
  let an anchored ship's routine hole through as a shutdown;
- **`identity_known`** — when we watched a *named* hull stop transmitting, that
  evidence stands on its own and the neighbourhood census is skipped for that
  row. Without this the cascade suppressed all 77, correctly by its own logic: a
  vessel who has been broadcasting all week is by construction in water where
  AIS is heard.

Landed: one contact, carrying `went_dark_at` / `went_dark_lat` / `went_dark_lon`
and the MMSI, rendering in the UI as a sentence and on the map as a dashed
segment from her last fix to where radar still had her.

### 3. A watchkeeper can see it now

`/api/radar/stations`, `/api/radar/contacts`, `/api/radar/tracks`, a **Radar**
tab, and three map layers. All read landed tables — a correlation is 20–40
minutes and no request can redo it, which is what `radar correlate --write` is
for.

Two design points worth keeping:

- **The suppressions are visible.** `?status=all` and a checkbox. "Why is this
  NOT dark" has to be answerable from the product.
- **The coverage ring is two rings.** The radar horizon depends on target
  height, so a station holds a 250 m tanker roughly twice as far as a 15 m
  skiff. One circle would either promise coverage that does not exist or hide
  coverage that does; the band between them is "big ships only", which is
  exactly what has to be visible before anyone believes a silence.

The synthetic flag is on the screen — a non-dismissible banner and a per-row
mark. Note this **contradicts `components/bits.jsx`**, where `SyntheticBadge` is
deliberately a no-op. That decision may be right for a corpus that is mostly
real; it is not right for a sensor that is entirely invented. Recorded rather
than silently reconciled.

### 4. Nothing has run on Eshan's machine — and I cannot change that

This is not a defect I can fix. I have no access to the laptop. The commands,
with their success and failure signatures, are in **"What Eshan needs to run"**
above. Until they are run and pasted back, every figure in this file is
*measured in the sandbox, on synthetic data*, and writing it more confidently
would not make it otherwise.

### Two rules that were asking the wrong question

Radar did not break these — it made them visible. This is the fourth and fifth
instance of the same pattern: **a rule tuned against six SAR contacts is not
tested, it is quiet.**

- **`detect_dark_rendezvous` was asking a global question.** Its `gap_party`
  branch scanned every unmatched association *anywhere in the AOI* within twelve
  hours with **no distance test at all**. With six SAR contacts that was usually
  false by accident; with 270,000 radar plots it was true almost always —
  **667 alerts on seed 7**, 76 of them on background traffic with no truth row.
  It now asks about the encounter's own parties, by track id, and keeps the
  positional test only for detections that have no track of their own (the SAR
  case it was written for). **Same corpus, after the repair: 81 alerts, and the
  76 that had landed on named background traffic with nothing to find become
  zero.**
- **Its score could not fall below its own threshold** — `0.5 + confidence×0.4`
  against a gate of `0.50`, so `ANOMALY_THRESHOLDS["dark_rendezvous"]` had never
  excluded anything in this project's history. Now scored from the evidence.
- **The cascade gate dropped `ambiguous` tracks** before any filter saw them.
  Two of the seven findable episodes had **no verdict row anywhere in the
  store** — indistinguishable from episodes that were never in the picture.
  `ambiguous` is a statement about identity, not about explanation. Admitting
  them changed the queue by nothing (precision 100%, recall 43% either way) and
  moved ten rows out of silence and onto the record.

### A measurement hazard, now guarded

The graph accumulates alerts across runs — correct for the graph, a trap for
measuring a rule change. Re-running the pipeline after tightening
`dark_rendezvous` reported 81 new alerts against a store still holding 667 from
the previous run's looser rule, and the final table scored **both rule sets**.
`run_scenario_pipeline.py` now prints a NOTE when the store already holds
alerts. **Delete `data/graph.sqlite` before measuring a detector change.**

---

## The maritime zone layer (2026-08-16, ADR-030)

### What was built

The system understood four circular areas, hardcoded in `anomaly/library.py`.
It now holds a geography layer: **port areas, anchorages, oil terminals and
single point moorings, customary shipping lanes, the four migrated sensitive
areas, and whatever the operator draws** — every one a landed row with the full
provenance envelope, an H3 res-6 cell index, and an explicit claim about what it
is worth.

The layer is **queryable, not merely drawable**. Any zone answers *who was
inside you, during which window, entering from where and leaving to where*, and
zone entry/exit is a first-class landed event on the same footing as an
encounter or a gap.

### What is deliberately NOT built, and why

**EEZ, contiguous zone, territorial sea and the India–Pakistan IMBL are
absent.** Deriving the first three from a public coastline mask and the UNCLOS
distances was implemented, measured and **discarded**:

- UNCLOS measures from **declared straight baselines**, not from the low-water
  line. India has declared them across the Gulf of Kachchh and the Gulf of
  Khambhat, so a coastline-derived territorial sea sits *inside* the real one
  exactly where the traffic is densest.
- There is **no median line** with Pakistan, Oman, the Maldives or Sri Lanka, so
  a 200 nm envelope from the Indian coast runs straight through four other
  states' waters.
- The IMBL is **disputed**: the Sir Creek terminus is unresolved and the
  maritime boundary seaward of it has never been delimited by agreement.

A boundary that looks surveyed and is not is worse than no boundary. So these
four arrive through `maritime-isr ingest zones` from a published file, or they
do not arrive — and **the system says which kinds are missing** in the pipeline
output, the `/api/zones` response and the map's layer box, because an empty EEZ
layer and an EEZ nobody loaded look identical otherwise.

Marine Regions (VLIZ) publishes all four as GeoJSON at
`marineregions.org/downloads.php`. That host was not reachable from the
environment this was built in; it should be reachable from the laptop.

### The measured result — SYNTHETIC, through the landed pipeline

| | |
|---|---|
| zones landed | **63** — 41 port areas, 9 anchorages, 5 terminals/SPMs, 4 lanes, 4 sensitive areas |
| statutory limits landed | **0** — by decision, see above |
| cell index rows | 13,997 (res 6) |
| zone transitions | **4,720** over 1,517 tracks, in 20 s |
| ... of which entry is censored | **1,208 (26%)** — the track was already inside when it began |
| by kind | shipping_lane 1,588 · anchorage 1,507 · port_limit 1,507 · sensitive_area 116 · oil_terminal 2 |
| a drawn box, answered on demand | **~8 s**, 93 vessels in a 0.7° × 0.6° box off Mumbai |
| area_visit | 4,720 presence rows (a query — emits no alerts by design) |
| maiden_zone_visit | **77 alerts** |
| lane_deviation | **36 alerts** |
| anchored_outside_limits | **IDLE** — no territorial sea loaded |

### Group Z, scored against scenario truth

| | |
|---|---|
| Z1 maiden visit | **DETECTED** — `maiden_zone_visit` |
| Z2 lane deviation | **DETECTED** — `lane_deviation` |
| Z3 anchored outside limits | **MISSED** — unfindable, no territorial sea loaded |
| Z4 settled liner (decoy) | correctly quiet |
| Z5 anchorage waiter (decoy) | correctly quiet |
| Z6 high-seas hold (decoy) | correctly quiet |

**All three new decoys held.** Both findable new anomalies were found. Z3 is a
miss the detector did not earn — the pipeline names the missing boundary.

### The precision regression these two rules caused — NOT resolved

**Scenario-level precision fell from 100% to 50%.** Eight decoys fired that
should not have: **five on `lane_deviation`** (C4, DX3, DX9, DX10 ×24, E5 ×2)
and **three on `maiden_zone_visit`** (D4, DX2, DX5 ×2). Separately, 72 alerts
landed on background traffic with no truth row — 71 of them maiden visits.

ADR-004 sets the gate at 70% and this is below it. **It is recorded rather than
tuned away, because every threshold that would fix it is one I cannot defend.**
The measurements:

* the novelty distance for maiden visit has a genuine structural break — 75% of
  candidate fires are within 28 km, the next is 558 km, and any threshold from
  100 to 300 km selects the same set. That one is defensible and is in;
* a *history* requirement would cut 77 → 17 at 14 days and 77 → **1** at 21
  days. The 1 is Z1. That is fitting to the answer key, not a threshold, and
  the distribution is smooth (77 → 69 → 48 → 35 → 17 → 1) with no break to
  anchor on. **I will not pick it.**

The honest reading is that **"first visit to a zone" is not an anomaly at an
eight-week history length** — it is a fact about our observation window. The
likely correct resolution is to demote `maiden_zone_visit` to a query like
`area_visit`, which would need Z1's truth row rewritten to expect a query
result rather than an alert. `lane_deviation`'s decoy fires need separate
diagnosis; DX10 firing 24 times suggests one long off-route passage being
re-reported rather than 24 findings.

**Until that is settled, Build 2 lands with a precision figure below the
ADR-004 gate, stated here rather than hidden.**

### Four analyses, and the honest state of each

| Analysis | State |
|---|---|
| **Area visit** | Runs. Deliberately emits no alerts — "which vessels visited this area" is a question an operator asks, not a judgement the system makes, and dressing it as an anomaly would put a hundred lawful port calls in a queue ADR-004 spends its whole budget keeping short. |
| **Maiden visit** | Runs. Needs a history qualifier or it is a list of the fleet: over eight weeks a vessel's first appearance anywhere is her first appearance in every zone she passes through. `MAIDEN_MIN_PRIOR_ZONES = 3`. Declines to claim anything about a radar contact — "she has never been here before" is a statement about a hull, and a station track number is recycled in minutes. |
| **Lane deviation** | Runs, and **the synthetic figure is optimistic by construction**. The generator routes its vessels with the same land-avoiding router the lane centrelines were drawn from, so generated traffic sits on the lanes almost by definition and only a vessel a scenario deliberately sends off-route deviates. This number says very little about real traffic. |
| **Anchored outside port limits** | **IDLE until a territorial sea is loaded**, and says so by name rather than returning an empty list. The code path is proven by `test_an_imported_territorial_sea_makes_the_idle_analysis_run`, which writes a GeoJSON, lands it through the real connector, and watches the analysis start firing with no code change. |

### The port gazetteer gap, closed

**25 west-coast facilities the gazetteer did not know** — Mormugao, Okha,
Dwarka, Ratnagiri, New Mangalore, Dahej, Veraval, Diu, Karwar, Beypore,
Vizhinjam, Jakhau, Navlakhi, Bedi, Mangrol, Dahanu, Murud, Dabhol, Vengurla,
Redi, Honnavar, Malpe, Kannur, Alappuzha, Kollam. A stop at any of them produced
no port call, no `docked-at` edge and no port-risk signal: the vessel simply
appeared to stop in empty water.

`GAZETTEER_V1_NAMES` records the old sixteen-name list **in the source**, so the
before/after figure does not depend on which commit is checked out, and
`gazetteer_recall()` measures it on the same corpus through the same
`port_at()` call the feature extractor uses. No anchorages were added for the
new ports: `ANCHORAGES` holds *charted waiting areas* and this project does not
have the charts.

### Things worth knowing before changing anything here

- **The cell covering is an index, not the geometry, and it is dilated on
  purpose.** A res-6 cell is ~7 km across and a 2 km single point mooring
  contains no cell centre, so an undilated covering is the empty set — and a
  zone indexed by the empty set is a zone no vessel is ever inside. Membership
  is always cell lookup *then* exact `contains`.
- **28% of transitions have a censored entry** (1,543 of 5,518 on the first
  run): the track was already inside when it began, so the entry position is
  where we picked her up rather than where she crossed. Anything reasoning about
  entry direction must respect `entry_censored` or it will report the middle of
  a zone as a boundary crossing.
- **A drawn box is answered on demand** in about eight seconds, because it has
  no precomputed transitions and "nobody was here" would be a lie. The answer
  covers **AIS only** and says so; radar tracks are built by the track engine
  from a different table and reconstructing them per request would take minutes.
- **`loiter-in-zone` was never a registered edge type.** It has been emitted
  since Phase 5 against a `zone:<name>` destination that was not a node type,
  and validated only because nothing checked. Both halves now exist.

### What Eshan needs to run

```
maritime-isr zones build
maritime-isr zones status
```

*Success* for the first is `landed N row(s) into maritime_zone` and
`maritime_zone_cell`. *Success* for the second is a table of kinds and counts
followed by a **NOT PRESENT** block naming the four statutory limits — that
block is correct output, not an error.

To load a real boundary, on a machine that can reach Marine Regions:

```
maritime-isr ingest zones --path territorial_seas_v4.geojson
maritime-isr ingest zones --path eez_v12.geojson --kind eez
```

*Success* is `[zones] landed N zone(s) into maritime_zone` with a skipped count
of zero. A non-zero skip count is printed with a reason per feature; paste it
back rather than ignoring it, because a connector that quietly drops a third of
its input leaves a hole nobody notices until an analysis is inexplicably quiet.

Then re-run `python tools/run_scenario_pipeline.py` and the
`anchored_outside_limits` analysis stops reporting itself idle.

### Status, stated precisely

- 🟡 **Built, sandbox-green.** The layer builds, lands, indexes, answers
  queries, renders and draws in Claude's sandbox — the draw-a-box flow was
  driven in a real Chromium against a real uvicorn process and screenshotted.
  **None of it has ever run on Eshan's machine.**
- ⬜ **No real boundary file has ever been loaded.** `ingest/zones.py` is tested
  against a GeoJSON this project wrote, not against a Marine Regions download.
  The `POL_TYPE` mapping is written from that publication's documented
  vocabulary and is **untested against the real file**.
- Every figure is synthetic.

---

## The interface was shouting at everyone (2026-08-18)

Three complaints from Eshan, one of them a hang, one of them a bug that had
been on screen since the risk panel was written, and one a house-style rule.

### 1. The whole-network graph froze the tab — the actual cause

**Symptom:** opening Graph, or coming back to the whole network, made the page
stop responding, every time.

**Cause, measured rather than guessed.** A throwaway harness laid out a graph
the shape and size of the server's cap (1,500 nodes / 1,934 edges) in a real
Chromium and timed each stage:

| stage | before |
|---|---|
| fcose layout | **15,653 ms** of blocked main thread |
| one hover-fade | 481 ms |
| clearing that hover | 410 ms |
| five zoom steps (label rescale) | 1,950 ms |

The layout was the freeze; the hover and zoom costs were why it stayed
unusable afterwards. Fifteen seconds of a blocked main thread is Chrome's
"page unresponsive" dialog, which is exactly what "it crashes" looks like.

**The layout cost was not the iterations.** Cutting the budget 800 → 250
iterations moved it 15.7s → 14.4s. Nearly all of it was fcose's *spectral*
seeding — the eigendecomposition it runs when `randomize: false` — which is
superlinear in node count (600 nodes: 2.3s; 1,500 nodes: 14.4s).

**The fix.** `quality: "draft"` skips the spectral step entirely and runs the
same graph in **0.3–0.65 s**, but it requires `randomize: true`, which would
reshuffle the web on every visit — and a picture you cannot re-find is a
picture you cannot learn. So the layout runs with `Math.random` temporarily
replaced by a **seeded xorshift32** (`withSeededRandom` in `GraphView.jsx`).
Verified by laying the same 1,500-node graph out twice and comparing every
node position: identical to the last decimal.

End to end, page load to a settled 1,500-node network, in Chromium:
**2.1–2.4 s**, down from a hang.

Three other things were making it stay slow, all fixed:

- **Function-valued cytoscape styles** (`"background-color": (n) => …`) are
  re-invoked per element on every restyle. Replaced with `data(...)` mappers;
  everything the stylesheet reads is precomputed onto the element.
- **Hover-fade** added a class to *every* element and restyled all of them.
  Above 600 elements the view now raises the hovered neighbourhood without
  pushing everything else down.
- **The zoom label-rescale** applied a style bypass to all 3,400 elements when
  at most a hundred draw text. Scoped to a `.labelled` class.

Also: the whole-network view now caps its opening zoom (`OPENING_MAX_ZOOM`), so
a focus node with three neighbours no longer fills the viewport with three
enormous circles; and a "laying out N nodes…" overlay states that the canvas is
about to hold the thread, instead of the page appearing to die silently.

**One bug introduced and caught in the browser, worth remembering:** the first
version of the in-flight guard was a boolean. React mounts an effect, tears it
down and mounts it again in development, so the first call cancelled itself and
the second refused to start — the view sat on "loading the whole network…"
forever. It is a monotonic run-token now, not a flag.

### 2. The four risk bars never worked

`.rbar-fill` was a `<span>`, so it stayed `display: inline`, and an inline box
ignores `height`. **Every bar rendered as an empty grey track**, whatever the
component scored — since the panel was written. Both track and fill are
block-level now.

The width was also wrong in a way that would have lied once the bars appeared:
it was `weighted / max(weighted)`, which normalises the largest component to a
*full* bar however small it is. EMPRESS, scoring 0.037 on flag opacity and zero
on everything else, would have drawn one **full** bar — a picture of maximum
flag risk for a hull barely above zero. Each bar is now the component's own
`value` on its own 0–1 scale, and the figure beside it is the weighted
contribution. One colour, not four; four hues implied four kinds of thing when
they are four terms of one sum.

### 3. Type: one family, six sizes, three weights, no italics

The UI had grown two typefaces, a dozen ad-hoc sizes (10.5, 11.5, 12.5, 13.5,
26…), four weights and italic "not available" text. `theme.css` now defines the
whole scale as tokens and every component uses it — no inline `fontSize`
survives anywhere in `src/`. Audited in a real browser across all seven views:

```
fams: ["Inter"]  sizes: [11,12,13,15,20,28]  weights: [400,500,600]  italics: 0
```

`.mono` is the same family with tabular figures rather than a second typeface,
so identifiers still line up.

### 4. "Synthetic" and "scenario" are out of the interface

Per Eshan's instruction, neither word (nor "simulated") appears anywhere a user
can read: not in copy, layer names, badges, map popups, tooltips or option
text. Audited in-browser on every view.

**This is in tension with CLAUDE.md invariant 6, and the tension is resolved by
wording, not by dropping the disclosure.** The Radar view still carries a
non-dismissible line — "No live coastal radar feed is connected to this system.
… every figure here describes the model rather than a sensor" — because there
is no radar behind it and the interface must not imply one. What went is the
jargon and the repetition (a `SYNTHETIC` badge on every row), not the honesty.
**If Eshan wants that line gone too, say so — it is his call, but the data-layer
guarantee and the external framing then carry the whole load.**

Nothing in the data layer changed: `is_synthetic` is still on every row and the
API still returns real/generated split counts. The UI simply stops printing it.

### Status

- 🟡 **Built, sandbox-green.** Every claim above was measured or audited in a
  real headless Chromium against a stub API that mimics the FastAPI shape —
  including the 1,500-node whole-network load. **None of it has run against the
  real backend on Eshan's machine.**
- The graph timings will differ on his hardware; the ratio (a 15-second freeze
  becoming a sub-second layout) is what should carry over.

---

## The whole-network graph never drew, and the retry did nothing (2026-08-18, second pass)

Two symptoms Eshan reported — "there's always those two nodes in the top left
corner" and "'Back to the whole network' isn't working" — with **one root cause
between them**, plus one control that could never have recovered from it.

### The ReferenceError

`runLayout` is a module-scope function. Two of its fcose options read a bare
`web`, which is a **component state variable** and is not in scope there:

```js
quality: web && nodeCount <= 400 ? "default" : "draft",
randomize: !(web && nodeCount <= 400),
```

Every call threw `ReferenceError: web is not defined`. It was invisible for two
reasons worth remembering:

1. **Only the fcose branch evaluates those lines.** `big ? {…fcose…} : {…cose…}`
   evaluates one object literal, so any graph of ≤ `FCOSE_ABOVE` (250) nodes
   never touched them. The fixture ownership network is ~160 nodes, so it
   passed every check that had been run against it. Eshan's corpus clears 250,
   and his did not.
2. **Both call paths swallowed it.** `loadWholeWeb` had a `finally` and no
   `catch`; `expand` caught everything and reported "nothing further to expand
   there".

### Why that looked like two stray dots

`loadWholeWeb` adds every element to the canvas and *then* lays it out. The
throw landed in between, so:

- the elements stayed on the canvas at cytoscape's default un-positioned
  origin — a clump at the top-left corner, which is the "two nodes" that were
  always there;
- `setWeb(g)` and `setNodeCount(...)` never ran, so React believed the canvas
  was empty and drew **"Pick a vessel and seed the graph to begin"** on top of
  the clump it did not know about.

### Why the button could not recover

`web` stayed null, so "← Back to the whole network" stayed on screen. Its
entire implementation was `setParams({})` — clearing `?seed=` so the loader
effect, which depends on `params`, re-runs. But after a failed load **the URL
is already parameterless**, so `location.search` did not change, the effect
never re-ran, and the button did nothing however many times it was pressed.

### Fixes

- `runLayout(cy, nodeCount, { isWeb })` — the flag is a parameter, threaded
  from the two whole-network call sites. `SPECTRAL_MAX_NODES` (400) is now a
  named constant next to `FCOSE_ABOVE`.
- `loadWholeWeb` **catches**: it clears the half-built canvas, records the
  error, and the panel says the network could not be drawn and offers a retry.
  A view that reports its own failure is debuggable; one that draws two dots
  is not.
- `expand` distinguishes "the fetch found nothing" from "we threw after adding
  elements", and no longer reports the second as the first.
- The button calls `loadWholeWeb()` directly when there is nothing in the URL
  to clear, and reads "↻ Retry the whole network" after a failure.
- The empty state has three messages now — failed, no ownership edges, nothing
  asked for — because they mean different things.

**Measured after the fix, in Chromium against the stub API:** 350 nodes (the
spectral path, which had never once executed without throwing) draws in
**1.1 s**; 1,500 nodes (the draft path) in **2.3 s**; the retry button restores
the network from a failed parameterless load.

### Found while verifying

MapLibre was rejecting the radar coverage ring outright —
`layers.radar-coverage-line.paint.line-dasharray: data expressions not
supported` — and dropping the property, so **both rings drew solid**. The
dashed outer ring is the entire point of drawing two: solid is the station's
reach for a small craft, dashed for a large ship, and the band between them is
"big ships only". Drawn solid, the picture promised skiff coverage out to the
tanker horizon, which is the exact misreading the two-ring design exists to
prevent. Split into one filtered layer per band.

### Status

- 🟡 **Sandbox-green.** Verified in a real headless Chromium against a stub API
  at 200, 350 and 1,500 nodes, including the failure-and-retry path. **Not run
  against the real backend.** The radar-ring fix removes the console error but
  the dashes themselves were not eyeballed — the basemap tiles do not load in
  this sandbox.
- ⬜ **The lesson worth keeping:** the fixture graph is smaller than the real
  one, and a `?:` only evaluates one branch. A code path the fixture cannot
  reach is a code path nothing has ever run.

---

## The graph's visual system, and one authority drawn twice (2026-08-20)

Four reports. Three were proportion; one was a duplicate node with a real cause.

### The symbols were sized for a different type scale

Node radii came from `nodeTypeSize` and had never moved, but two things around
them had: labels were pinned to a constant SCREEN size, and the type scale was
rebuilt at 11px. A vessel was a 30px dot beside an 11px name — a button with a
caption, not a labelled node. Radii cut about 40%, ratios untouched, so size
still means importance and nothing else.

### The arrowheads were the loudest mark on the canvas

Cytoscape derives arrowhead size from **edge width**. Node diameters go through
`densityScale` (0.41 on a 1,500-node graph) and edge widths did not — so on a
dense view the dots shrank to 41% and the arrowheads stayed at 100%. Pinheads
under enormous triangles, which is exactly how it was reported. `applyScreenScale`
now puts edge widths through the same density factor, and `arrow-scale` drops
0.7 -> 0.6. One factor applied to every mark keeps the system coherent at any
node count.

### The control panel could not be scrolled to

`.graph-help` had no height limit. On a short window the Context checkboxes and
the legend ran off the bottom of the viewport with no way to reach them —
controls that exist and cannot be clicked. Capped to the canvas with its own
scrollbar and `overscroll-behavior: contain`, so a wheel gesture that reaches
the end of the panel does not fall through and zoom the graph. The panel copy
was also cut to facts (the reasoning moved into `title=` tooltips), which on a
760px-tall viewport is the difference between overflowing and fitting with room
to spare.

### Why there were two OFAC nodes

Not a rendering bug — **two genuinely distinct nodes**:

| node id | props | designations |
|---|---|---|
| `authority:OFAC` | `fictional: false` | the real matches |
| `authority:SCENARIO-SDN` | same name, same issuing body | the generated ones |

`graph/from_landed.py` relabels the second to display as "OFAC" by an explicit
operator decision, so the demo reads as one system. The consequence nobody
accounted for: the graph draws two identical diamonds with the designations
split between them, which reads as two regulators — a picture that is simply
false.

Fixed in the **serving layer** (`merge_duplicate_authorities`), not the store:
authority nodes sharing a (type, label) are drawn as one, edges are rewired and
de-duplicated. Three properties it holds, each with a test:

  * the **real** id survives, so clicking the node shows the real regulator's
    register and reference rather than the stand-in's;
  * the merged node is flagged generated if **any** part of it was — never the
    other way round;
  * **only authorities merge, and only by (type, label)**. Merging on label
    alone would collapse two genuinely different hulls that share a name, which
    is the opposite of what this product is for.

Nothing in the store changes: both ids remain, both keep their own
`is_synthetic`, and every split count and real-versus-generated query behaves
exactly as before. Re-separating them is a matter of giving the second node a
different display name again, at which point this merges nothing.

### Also

`tests/test_sanctions_match.py::test_no_identity_landed_is_a_clear_message` was
**already failing on main** — PR #36 changed the hint to name
`python -m maritime_isr.cli` (because the `maritime-isr` console script only
exists after a `pip install`) and this assertion was left pointing at the old
wording. Updated. Full suite: 663 passed, 33 skipped, 0 failed.

### Status

- 🟡 **Sandbox-green, and this time against a real backend.** Verified in
  Chromium against uvicorn serving a corpus generated here by
  `scenario generate` + `run_scenario_pipeline.py` — 222 vessels, 29 companies,
  161-node ownership network. **Still not Eshan's corpus.**
- The `merge_duplicate_authorities` behaviour is unit-tested and does not
  depend on a populated graph.

## The timeline player ran and nothing on the map moved (2026-08-21)

Reported by Eshan, in those words: *"when i run the timeline player, nothing
happens. nothing moves on the map. everything on the map stays the same."*

Correct report, and the play button was innocent — the clock really was
advancing. **The scrubber was scrubbing the wrong window.**

### Two windows, treated as one

`/corpus-window` returned the **corpus** span: the union of the four GFW event
tables and `ais_position`. But the scrubber's only job is to interpolate AIS
tracks to a clock, so the only days it can move anything on are the days that
have positions. On the laptop corpus those are not the same span at all:

| | span | days |
|---|---|---|
| corpus window (what the scrubber played) | 2012-01-04 → 2026-07-25 | 5,317 |
| AIS positions (what can actually move) | 2026-06-04 → 2026-07-25 | 52 |

The 2012 start is a thin tail of real GFW identity and loitering records —
`scenario/world.py` documents it — while the eight-week narrative sits at the
dense end. So **99.04% of the scrubber covered years holding no positions**, and
the consequences compounded:

* At `SECONDS_PER_DAY = 7`, one playthrough of 5,317 days takes **10.3 hours**.
  Press play and the clock leaves 2012 at 8.6 corpus-days per minute; the first
  vessel would appear after about ten hours of watching.
* The whole motion band is 0.96% of the bar — **9.6 of the slider's 1,000
  steps**. Nearly every drag lands in an empty year.
* The playhead defaulted to `t = 1`, the window's end, which on this corpus is
  53 minutes past the last AIS position. **The map opened with zero vessels
  drawn**, and the status line said "200 vessels on AIS" while showing none.

A scenario-only corpus has no 2012 tail, so its two windows coincide and the
player worked perfectly in every sandbox it had ever been run in. That is why
this survived to the demo.

### The fix

**Server** — `/corpus-window` now returns four timestamps, not two:
`start`/`end` (the corpus) and `motion_start`/`motion_end` (the `ais_position`
extent), plus a `note` naming both spans whenever they differ. `/stats` and the
incident report are untouched: they still get the corpus window, which is what
they mean. The two agree on the keys they share, which the test now asserts as a
subset rather than as equality.

**Client** — the scrubber plays `motion_*` and falls back to the corpus window
only when no positions are landed at all (the real-feed case, ADR-005). It says
so: the server's note renders in the notes bar, and the status line carries an
"· AIS window" suffix whenever it is playing less than the whole corpus. A time
control silently covering a different span from the map under it is precisely
the kind of quiet mismatch CLAUDE.md §4 exists to forbid.

**The playhead parks where the fleet is.** Defaulting to the end of the window
put the clock on the one instant guaranteed to be nearly empty — a vessel is
drawn only while the clock sits inside her own track, and by then almost every
track has finished. It now lands on the busiest instant (max simultaneous
tracks, one sweep over the span endpoints), and stays out of the way the moment
the operator touches the scrubber or presses play. On the reproduction corpus
that is 0 vessels on arrival before, **97 after**.

**Playback is capped end to end** at `MAX_PLAYTHROUGH_S = 120`. Seconds-per-day
is the right pace for a window of weeks and an absurd one for a window of years;
the cap is what keeps the no-AIS fallback watchable instead of a ten-hour
progress bar.

**The status line counts what is on screen**, `N of M vessels moving`, not the
corpus total. The old text could read "200 vessels on AIS" over an empty map —
it did, in the screenshot of the bug.

### Verified

Reproduced and fixed **in Chromium against a live uvicorn backend**, on a corpus
deliberately shaped like Eshan's: `scenario generate` here, plus a three-row
non-synthetic 2012 partition written into `gfw_loitering` to recreate the real
GFW tail. The check counts vessel-coloured pixels in the MapLibre canvas across
a playback, because a canvas hash also catches repaint jitter.

| | before | after |
|---|---|---|
| clock advances | yes | yes |
| vessel dots on arrival | 0 | 97 of 200 |
| **vessel positions change while playing** | **no** | **yes** |
| clock reaches after 12 s of play | 2012-01-06 | 2026-06-30 |

Before-state screenshot: playing, slider pinned at the far left, clock at
2012-01-06, "200 vessels on AIS", and not one blue dot on the map — Eshan's
report exactly.

Full suite: **662 passed, 37 skipped, 2 failed**. Both failures reproduce
identically on the base commit (checked in a worktree at `9861df1`) and are
artefacts of *this* sandbox, not of the change:

* `test_api_exercise.py::test_ports_non_empty_and_split` — the gazetteer is
  empty here because only `scenario generate` was run, not the full
  `tools/run_scenario_pipeline.py` that lands ports.
* `test_sanctions_match.py::test_no_identity_landed_is_a_clear_message` —
  **a brittle test, worth fixing separately.** It hard-codes the
  `python -m maritime_isr.cli` spelling of a hint that `config.CLI` resolves
  *per machine* (short form when the console script is on PATH, `python -m`
  when it is not — see `_cli_command`). A `pip install -e .` here put the
  script on PATH, so `CLI` is `maritime-isr` and the assertion misses. It will
  fail for anyone who pip-installs the package, which is the documented install
  path; it should assert against `config.CLI` rather than one of its branches.
  Left alone: out of scope for this fix.

### Status

- 🟡 **Verified against a real backend in a real browser, on a corpus shaped
  like the laptop's — but still not the laptop.** The 2012 tail here is three
  rows synthesised to match `data_profiles/real_corpus_profile.json`; the real
  one is a handful of genuine GFW records. The shape is what matters and the
  shape is reproduced, but the exit test is Eshan pressing play on his own
  corpus and seeing ships move.
- The `motion_start`/`motion_end` and note behaviour is unit-tested, including
  the laptop-corpus numbers and the no-AIS-at-all branch, neither of which the
  sandbox corpus can reach on its own.
---

## The whole-network graph was collapsing, not just crowded (2026-08-20, third pass)

Reported as "most of the texts and nodes are not visible at all". The cause was
not styling. Two independent defects, both found by measuring through a live
`cy` handle in a browser rather than by looking at screenshots.

### 1. fcose's draft mode cannot lay out a disconnected forest

This graph, at Eshan's scale, is **276 connected components** — nearly all of
them one company and the three to five hulls it operates. `quality: "draft"`,
the fast path adopted to escape the 14-second spectral freeze, **collapses
every small component onto a single point.** Measured headless on a
corpus-shaped 1,499-node graph, with real `Math.random` (the seeded PRNG was
checked separately and is uniform — 20,000 unique values, flat deciles, so it
is not the cause):

| | bounding box | distinct node positions | median spacing |
|---|---|---|---|
| fcose draft | 250 × 442 | **23 of 961** | **0** |
| forest layout | 3724 × 2898 | 1499 of 1499 | 46 |

Twenty-three positions for nine hundred nodes. Tuning `nodeRepulsion`,
`gravity`, `nodeSeparation` and `packComponents` moved the bounding box and
changed the collapse not at all, because a collapse is not a spacing problem.

**The fix is to stop asking a global force simulation to draw a forest.** Each
component now gets what suits its shape, and the components are shelf-packed
into rows about as wide as the whole is tall:

  * a **star** (the common case) — highest-degree node centred, the rest on a
    ring. Instant, deterministic, and a truthful picture of "this company
    operates these hulls";
  * a **large component** — `concentric` by degree. Measured on the real shape
    (a sanctions authority, its designated companies, their fleets; 126 nodes,
    18-unit diameters), median nearest-neighbour spacing: fcose draft **0**,
    fcose spectral **8** (overlapping), cose **67** but 546 ms and O(n²),
    concentric **48** in 20 ms. Concentric imposes rings by degree rather than
    discovering structure, which is worth stating — for an ownership graph it
    is a fair reading, and the seeded neighbourhood view keeps a real force
    layout, which is where structure is actually read.

### 2. Node sizes were divided by the zoom BEFORE the layout ran

So the layout's geometry depended on where the camera was, and the loop ran
away instead of converging: a tighter layout raised the fit zoom, a higher zoom
shrank the model sizes handed to the next layout, which packed tighter still.
Measured on the same graph before the fix: model box **559 × 287** for 1,499
nodes and a fit zoom of **2.65** — the fit ZOOMED IN, because the graph had
collapsed to a speck.

The two concerns are separated for good now:

  * **Layout space is zoom-independent** (`applyLayoutSizes`). Nothing the
    layout reads comes from `cy.zoom()`.
  * **Render space is clamped, not pinned**: `clamp(nominal × zoom, floor,
    nominal)`. Capped so zooming in never inflates a vessel into a saucer,
    floored so zooming out never erases it. A clamp of a monotone function
    cannot oscillate, and it is applied only after a layout has finished.

The two-pass fit/rescale "fixed point" that used to reconcile the circularity
is gone with it.

### 3. Labels now get out of each other's way

A flat budget was not enough: labels are pinned to a constant SCREEN size, so
the density that matters is how close two land in PIXELS. A cap of 55 still
stacked all 25 company names around 300 px of arc in a concentric core. The
view now walks candidates best-first (designated, then companies, then the
focus network, then hubs) and draws one only if it is clear of every label
already drawn — recomputed when the zoom settles, so zooming in reveals more
names the way a map does.

Measured on the 1,499-node view: **0 overlapping label pairs** at fit (68
labels) and **0** after zooming in four steps (298 labels).

### 4. The view opens fitted

It used to open framed on the focus neighbourhood — which, measured, put 123
of 1,499 nodes on screen under a panel reading "1,499 entities". A number the
canvas contradicts by 92% is how an operator concludes the view is broken.

### Verified

| view | nodes | in viewport | labels | overlapping pairs |
|---|---|---|---|---|
| whole network (real backend) | 161 | 161 | 41 | 0 |
| + flag context | 272 | 272 | 49 | 0 |
| seeded neighbourhood | 14 | 14 | 14 | 0 |
| corpus-shaped stand-in | 1,499 | 1,499 | 68 | 0 |

### Two things worth keeping

- **`window.__cy` is left exposed on purpose.** Every number above was measured
  through it in a real browser. The collapse was invisible from outside: the
  panel reported 1,499 entities, no error was thrown, and the screenshots just
  looked bad.
- **The pytest suite writes to the real `data/` directory.** A full run
  emptied `data/graph.sqlite` (886 nodes -> 31) and the Graph view went blank
  for a reason that had nothing to do with the code under test. Rebuilt with
  `python -m maritime_isr.cli graph-populate`. Worth fixing separately — a test
  run should not be able to destroy the operator's corpus.
---

## The map said everything and distinguished nothing (2026-08-22)

Reported as *"the map key and the map have become too cluttered and crowded"*,
alongside a question that turned out to be the actual finding:

> which ones are the actual vessels? which are the loitering ones (they stay
> stationary in one place for days as the timeline player progresses)

**Nothing on that map was loitering in place.** Exactly one layer moves — the
interpolated AIS positions. Every other coloured dot is an event marker pinned
where something once happened, deliberately not filtered by the clock so that
nothing blinks during playback. That decision is right and stays. What was
wrong is that the map never said so, and drew both as a small coloured circle,
so a pin that has never moved read as a ship that had stopped.

### What was actually wrong

| | before |
|---|---|
| layers on at first paint | **18 of 24** |
| layers drawn as circles r4–r8 | **11** |
| distinct meanings sharing `#b0221b` | **3** (encounters, AIS gaps, alert markers) |
| checkboxes in one flat scrolling column | **24** |
| anything stating that event pins ignore the clock | **nothing** |

Every default-on layer had been turned on for a real reason, each recorded in
its own past session — "the footprints were the one unambiguously real thing and
they were hidden", "a contact drawn without its coverage ring invites the
out-of-coverage misreading". Each was right on its own. Nobody ever added them
up. That is the failure mode worth remembering: **a view degrades by locally
correct decisions, so no single commit looks wrong in review.**

### The fix

**The key is four groups**, collapsible, each with one line saying what kind of
thing it holds — *Live traffic* ("moves with the timeline"), *Past behaviour*
("fixed pins marking where something happened; these do not move with the
timeline"), *Satellite & radar*, *Geography*. A collapsed group still shows
`n/m`, because collapsing is meant to reduce reading, not to hide state.

**Mark weight carries the family.** MapLibre circles give no silhouette control
without sprites, so the hierarchy is weight, which is the axis that governs what
the eye reaches first anyway: live = large, saturated, thick white halo;
history = small, translucent, hairline; alerts = a *ring* rather than a disc,
because an alert is an annotation about a position rather than another object
at one. Sensor marks keep their filled/hollow encoding — that distinction
carries meaning and is not ours to flatten.

**The opening set is five layers**: vessels, event density, alerts, Sentinel-1
footprints, drawn areas. Density is strictly better than the four individual
event layers it summarises, which is why they start off.

**AIS gaps left the encounter red** for near-black. What a gap is, is a
*silence*. The colour says that much and no more — it is deliberately not a
claim that the silence was intentional, which needs demonstrated receiver
coverage at the position (ADR-005, CLAUDE.md §6).

**`/tracks` reports its cap.** It capped at `max_vessels=200` and returned
`note: None` regardless — measured here, **200 of 208** vessels drawn with
nothing anywhere saying so. `/events` has reported its cap since ADR-024; this
was the one endpoint still breaking the rule. Truncation is measured against
what the *cap* dropped, never against what the decimator skipped: a vessel with
one position legitimately has no track, and counting that as truncation would
fire the warning on every corpus and train the operator to ignore it.

**Alert markers stopped inventing a position.** The marker was drawn at
`events.find(e => e.vessel_id === subject)` — the vessel's *earliest* located
event, because events arrive ordered by `start_time`. A flag raised last week
could be pinned to a port call two months earlier. Now: her track interpolated
to the alert's own timestamp; failing that, her event nearest **in time**, with
the label saying which; failing both, no marker. A vessel with no track and no
located event has no defensible position, and dropping a marker on the sea
anyway is the map manufacturing evidence.

**The notes bar stopped covering the key.** Both panels sat at `left: 12px`, and
the notes paint later — so the disclosures covered the controls they qualify.

### The graph, same session

**607 identity edges all read "identified as"** — the one phrase they have in
common and the one that carries no information. A vessel points at an MMSI, an
IMO, a call sign and a name; an IMO is welded to the hull and a name is paint.
The edges already carried `props["kind"]`; nothing shipped it to the client.
Now `identity_kind` rides the edge payload, gated on the closed
`schemas.keys.IDENTITY_KINDS` vocabulary so a populator/UI drift degrades to the
generic label instead of rendering a raw spelling on the canvas.

Measured on the landed graph: **225 MMSI, 224 name, 99 call sign, 59 IMO, 0
unlabelled.**

*The trap here, and it would have been invisible:* `GraphEdge` is a pydantic
model and pydantic drops undeclared keys. Adding the field to the service alone
would have worked on `/graph/all` and silently not on
`/vessels/{id}/neighbourhood` — the same canvas labelling the same edge two
different ways depending on which view opened it. There is a test pinning it.

**Hovering an edge did nothing.** `cy.on("mouseover", ...)` was bound to `node`
only, so pointing at a relationship gave no highlight, no label and no cursor
change; the only way to learn what a link was, was to click it. Edge hover now
raises the edge and both endpoints, and reveals that one edge's label — the sole
place in this view where hover uncovers hidden text, because the whole-network
view sets `shownLabel` to `""` on all ~1,900 edges, so a hover that neither
highlights nor names answers nothing. The style rule is **last in the
stylesheet** on purpose: cytoscape resolves by source order and
`edge[current = 0]` pins ended edges to opacity 0.28, so declared any earlier,
hovering a closed relationship would have raised everything about it except its
visibility.

### Explicitly NOT done

- **Event pins still ignore the timeline.** Offered and declined — they are
  context for the window, not moments in it, and blinking pins during playback
  is the failure the current behaviour exists to prevent.
- **No synthetic labelling was added to the map.** Offered and declined. Note
  that everything that moves on this map is still synthetic (ADR-005: no free
  AIS for this AOI), and the map does not say so; the Vessels list and the
  incident report still carry the label.

### Verified

Chromium against a live uvicorn backend on a freshly generated scenario corpus
(`scenario generate --seed 7` + `graph-populate`), measured through the DOM and
a live `cy` handle rather than read off a screenshot:

| check | result |
|---|---|
| key renders four groups, not a flat list | `[Live traffic, Past behaviour, Satellite & radar, Geography]` |
| collapsed groups still report state | `1/2, 2/6, 1/5, 1/7` |
| opening layer set | 3 of 8 visible toggles on |
| "these do not move with the timeline" on screen | present |
| `/tracks` truncation reaches the notes bar | "showing 200 of 208 vessels" |
| gaps no longer share the encounter red | `rgb(176,34,27)` vs `rgb(31,41,51)` |
| identity edges carry a kind over HTTP | 607 edges, 0 unlabelled |
| hovering an edge highlights it | class applied, both endpoints raised, label revealed, cleared on mouseout |

`python -m pytest -q` → **694 passed, 18 skipped, 1 failed**. The one failure is
`test_sanctions_match.py::test_no_identity_landed_is_a_clear_message`, which
reproduces identically on the base commit: it hard-codes the
`python -m maritime_isr.cli` branch of `config.CLI` and so fails wherever the
console script is on PATH, which is what `pip install -e .` does. **Still worth
fixing separately** — it should assert against `config.CLI`, not one of its
branches. It has now been the reported pre-existing failure for two sessions
running; it is cheap and it is noise on every run.

### Status

- 🟡 **Verified against a real backend in a real browser, on a generated
  scenario corpus — not on the laptop.** The exit test is Eshan opening the map
  and being able to say which marks are ships without asking.
- The one basemap-tile failure in the browser run is this sandbox's proxy
  blocking `basemaps.cartocdn.com`; every layer this change touches is served
  from localhost and rendered fine.

### Next up

- The brittle `config.CLI` assertion above.
- Nothing on the map distinguishes real from synthetic. Declined this session
  and correctly so — it was not what was asked — but it remains the largest
  honesty gap on the primary screen, and it should be a deliberate decision
  rather than an omission that persists by default.
