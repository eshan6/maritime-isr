# Annexure-2 — Proposed Technical Solution (Detailed)

*iDEX DISC 14 · Challenge 82 · Indian Coast Guard —*
*Integration of AI/ML in Coastal Surveillance Network (CSN) Software*
*(To be reproduced on the applicant's letterhead, if available.)*

---

## 0. How to read this document

The challenge brief names six areas (3.1 – 3.6). **Five of them already exist as
working, measured code** in the Maritime ISR prototype; the sixth (3.3, VHF
ASR/NLP) is designed but not built, and this document says so plainly rather
than implying completeness.

Every accuracy figure quoted here is measured on a **deterministic synthetic
corpus with injected ground truth** — 674 vessels, 816,356 AIS position reports,
a simulated coastal-radar picture over the same vessel truth, and 381 generated
port documents. Synthetic figures flatter a system. Real-feed precision will be
lower and re-measurement on ICG data is Deliverable 8, not an afterthought. Any
statement in this document that is a *design intent* rather than a *measurement*
is marked **[to build]**.

---

## 1. Technical Architecture & Approach

### 1.1 The shape of the system

The prototype is a three-tier data platform with an analytics layer above it and
one operator surface on top. The tiers are never mixed.

```
        RAW (immutable)              NORMALISED                 DERIVED
        ───────────────              ──────────                 ───────

 CSN coastal radar ──► radar track reports ──┐
                                              │
 AIS (dynamic, msg 1/2/3) ──► position ───────┤
 AIS (static,  msg 5)     ──► voyage          │
                              declarations    ├──► TRACKS ──┐
 Sentinel-1 SAR ──► scene files ──► calibrated│    (per-hull │
                    (immutable)    COG + CFAR │     memory,  │
                                   + CNN ──► CONTACTS        │
                                                             ├──► ASSOCIATION ──► DARK-VESSEL
 PANS attachments ──► passages ──► arrival ───┤   (global assignment)   CASCADE
 (PDF/scan/DOCX/XLSX)              records    │                              │
 e-PANS portal feed ──────────────────────────┤                              │
                                              │                              ▼
 EO station cameras ──► captures bound ───────┤                        OBJECT GRAPH
                        to a track            │                    (vessels, owners,
 OFAC / UN / EU / registries ──► versioned ───┘                     edges — each with
                                 snapshots                        confidence + validity)
                                                                             │
 VHF audio  [to build] ──► transcripts ──────────────────────────────────────┤
                                                                             ▼
                                                                   ANOMALY RULES + RISK
                                                                             │
                                                                             ▼
                                                          RANKED VESSELS OF INTEREST
                                                          (score · evidence · narrative)
                                                                             │
                                                                             ▼
                                                          API ──► OPERATOR UI + REPORT
```

* **RAW** — exactly what the sensor or the agency gave us, landed immutably and
  never edited.
* **NORMALISED** — raw mapped into canonical versioned schemas, provenance
  stamped, spatial cells computed. Regenerable from raw.
* **DERIVED** — contacts, tracks, associations, dark verdicts, graph edges,
  scores. Regenerable from normalised plus the code version.

Everything downstream is reproducible from **raw + code commit**. That is the
whole anti-corruption strategy: when a defect is found, the fix is a re-run, not
an archaeology exercise across a mutated database.

### 1.2 The load-bearing decision: one shared spatial index

Every located record — an AIS fix, a radar contact, a SAR detection, a scene
footprint, a camera arc, a port polygon — is stamped at ingest with the **H3
hexagonal cell** it falls in, at resolution 7 (≈ 5 km) and resolution 9
(≈ 170 m), by one shared helper used by every module.

This is what makes correlation tractable. The question "which tracks could this
radar blip be?" is otherwise a runtime geometry problem whose cost grows with
the square of the picture. On a shared grid it is a **hash join**: same cell,
candidate; done. The rule is enforced as a contract — a module that hand-rolls
its own cell computation is a defect, because two versions of the same index
silently miss matches rather than failing loudly.

### 1.3 The provenance envelope

Every row, in every store, without exception, carries:

| Field | Meaning |
|---|---|
| `source_id` | which connector produced it |
| `source_ref` | the identifier *at the source* — the message, the scene, the document, the portal record |
| `acquired_at` | when the source observed it |
| `ingested_at` | when we landed it |
| `pipeline_version` | the exact code commit that processed it |
| `confidence` | how much the producing method is entitled to claim (nullable) |

If provenance cannot be stamped, the record is not landed. The reason is
operational rather than architectural: **the product is trust.** A flag an
officer cannot trace back to a source and a code version is not merely
unhelpful, it is a liability at the point where a boarding decision has to be
justified.

On top of this sits an explicit distinction between **origin** — the outside
body a fact came from (a registry, an agency, a sensor) — and **derivation** —
what this system then did to it. Attribution is attached at serialisation, so
naming a source an officer could independently check is a structural guarantee
rather than a convention every developer has to remember. The system's own
storage is never cited as a source.

### 1.4 Connectors, never core changes

A new data source is a new module that maps into the canonical schema. The
fusion core never learns a source-specific special case. This is not tidiness —
it is the property that makes classified or paid feeds slot in later without a
rewrite, and it is enforced by treating any connector that *forces* a change in
the core as a defect in the core.

### 1.5 The six challenge areas, as built

---

#### 3.1 — Classification of Radar Data

**Requirement.** Radar gives kinematics and position and nothing else. Classify
type, activities, and interactions between vessels.

**Approach.** Every feature the classifier reads is **motion**: speed
distribution, turn rate, course persistence, stop-and-go structure, track
sinuosity, dwell geometry. No feature reads an identifier, a message rate, or a
sensor name — and a test asserts the feature vector is **byte-identical** for
the same track presented as AIS and as radar. That is what allows a model
trained on AIS-labelled tracks to be applied to radar tracks that carry no
labels at all, and it is why the same behaviour gets the same answer from either
sensor.

Datasets are split **by hull, never by track**. Two tracks from one vessel on
opposite sides of a split let the model memorise the ship rather than the class.

**The published class vocabulary is derived, not declared.** A hand-written list
of coarse classes is a claim about the world; what this system is entitled to
make is a claim about its own model. `confusable_groups` reads the measured
confusion matrix and merges any pair mistaken for each other more than 25% of
the time. If a future feature genuinely separates two classes, the groups shrink
on their own and no list needs editing.

Measured over 209 tracks, held-out hulls *(synthetic corpus)*:

| Metric | Value |
|---|---|
| Fine-grained accuracy | 65% |
| **Coarse (derived vocabulary) accuracy** | **90%** |
| Derived vocabulary | `fishing`, `general_cargo`, `merchant`, `reefer` |
| Provably not separable from motion | `[Aframax, bulker, product_tanker]`, `[Suezmax, VLCC]` |

Fishing is unmistakable — a third of the speed and three times the turn rate.
The tanker/bulker/cargo cluster is *not* separable from motion and never will
be, because a laden bulker and a laden product tanker at 13 knots on a
great-circle course are doing the same thing. **Saying so is the product**, and
it is a direct response to the brief's own instruction that a small set of
classes the system can genuinely separate beats a long list it guesses at.

**Interactions.** Company, shadowing, converging-and-holding and transfer
pattern are detected from relative geometry over time. The separation threshold
was set by measurement, not taste: across two corpus draws, eleven coincidental
close pairs were observed and **none closer than 5,337 m**, while three authored
relationships all sat **inside 4,245 m**. Claims are therefore made only inside
2.5 nm. A fishing fleet transiting together is a formation by every naive
geometric test and by no useful one.

**Contact profiling.** A radar contact that correlates to nothing is the case
the requirement most needs answered, and "unidentified contact" is the least
useful thing to put in front of a watchkeeper. The profiler returns an inferred
type, an inferred activity and the zone she is in, each with its own confidence.
It **profiles and never re-decides darkness** — the dark-vessel cascade owns that
verdict, and a second module quietly re-deciding it would be an uncalibrated
duplicate of a rule that already exists.

---

#### 3.2 — Automatic detection and classification by EO sensor, associated to a radar/AIS track

**Requirement.** Cameras are operated manually. There is no automatic capture
against a track, no classification against a library, no tagging of the image to
the track, and no alert on mismatch.

**Approach.** Four things are asked for and **only one of them needs pixels.**
Capture without operator intervention, bind the image to a track, classify
against a library, alert on disagreement — three of those are control and fusion
logic. Image classification is the commodity half of this problem; the loop
around it is the defensible half. So the loop is built, and the classifier sits
behind a replaceable interface.

**Cueing is a global assignment, not a ranked list.** The obvious build sorts
tracks by suspicion and gives each its best camera. That is greedy per-target
matching, and it fails for the same reason greedy matching fails in
radar-to-AIS association: the three most suspicious contacts are frequently
inside one station's arc, so a greedy pass hands that station's camera to all
three, breaks the tie arbitrarily, and leaves fifteen cameras idle. Each time
slot is solved with a Jonker-Volgenant assignment over cameras × candidates.

**Priority is three terms with stated weights:**

```
priority = (0.55 · suspicion  +  0.30 · information gain  +  0.15 · staleness)
           × expected image quality
```

*Information gain* — what a photograph would actually resolve — is the term a
naive build omits, and without it the network spends every slot re-photographing
the top of the ranked list. A contact nobody can name scores 1.00; a hull whose
declared identity no image has checked, 0.55; a hull an image already agreed
with, 0.10; a hull an image already **disagreed** with, 0.85, which is what sends
the camera back for the corroborating second look.

Cameras that would otherwise sit idle are not wasted: below-threshold targets
remain in the same assignment under a penalty larger than any value spread, so
they take only a camera nothing else could use, and they are marked
`opportunistic` on the tasking so a fill can never inflate the number that
measures real demand.

**The classifier is genuinely swappable, and the swap is demonstrated rather
than asserted.** Two implementations ship, and the test suite defines a **third
inside the test file** and substitutes it into the running loop. All three
produce bound, landed captures; the verdicts differ; nothing in the cueing, the
tagging or the mismatch rule changes between runs. A customer's vision model,
or an ICG-supplied one, drops in by satisfying the same interface.

**The mismatch rule compares at the AIS ship-type family and requires
corroboration.** Both halves were forced by measurement. A single confident look
was producing a ten-to-one false-positive rate; requiring that contradicting
looks be a majority of the looks that decided anything, and that at least two
corroborate, brought false accusations to **0 – 0.36% per look**. Classifier
confidence is *fitted* so that mean reported confidence equals measured
accuracy under the capture's own conditions — a confidence that does not track
the hit rate is decoration, and this system's whole thesis is that an officer
can calibrate their trust against it.

**Measured on the synthetic corpus:** 2 mismatch alerts, both on hulls authored
to declare a false type, **zero false positives**.

**Two capability boundaries are stated rather than tuned away.** A head useful
against a merchant to about 20 km can only produce imagery good enough to
*contradict* a declared identity inside about 8 km in this coast's monsoon
visibility — "we can see her" and "we can prove something about her" are
different ranges. And sister ships are not separable in the features an image
gives: two observations of the same hull sit 0.12 apart at the median, while the
closest pair of *different* hulls sits 0.11 apart. The distributions overlap, no
radius separates them, so identification is offered on **margin** — when a hull
is distinctive against the library — and refused when she is one of a class.

**Honest status.** There is no camera. Every capture in the prototype runs
through a simulated `CaptureSource` behind the same interface a real head will
use, and **every landed row says so** (`capture_mode='simulated'`, empty image
reference, the model's own provenance string recording that it has never seen an
image). Camera utilisation is 4.5%, with 89,480 deferrals attributable to
`no_camera_in_reach` — that is a 20 km lens against the Arabian Sea, a physical
fact rather than a scheduling defect, and it is reported as such.

---

#### 3.3 — Multilingual ASR & NLP for VHF radio communication

**Requirement.** VHF is recorded at every radar station but there is no software
to convert analogue voice to searchable English text across the languages
actually spoken on the coast.

**Status: designed, not built.** This is the one area of the six with no
prototype. It is named here rather than glossed, and the system already reflects
its absence honestly: `radio` exists as a declared-and-empty evidence family in
the assistant's factor catalogue, and asked about radio traffic the assistant
answers *"no radio audio or transcript is held; multilingual VHF speech
recognition is not implemented"* rather than producing a plausible sentence.

**Proposed build [to build].**

1. **Channel-segmented capture and voice activity detection.** Continuous
   per-channel recording is already produced by the station software; the module
   consumes it, segments on speech activity, and discards silence and squelch
   before any model runs.
2. **Multilingual ASR.** Fine-tune open Indic speech models on maritime VHF
   audio for the principal coastal-state languages, plus accented maritime
   English. Maritime VHF is a favourable domain in one respect — the vocabulary
   is small, procedural and heavily templated (call signs, positions, courses,
   intentions, standard phrases) — and unfavourable in another: narrow-band
   analogue audio, heavy noise, code-switching mid-transmission. The build
   budget assumes both.
3. **Normalisation to searchable English**, preserving the original-language
   transcript alongside the translation, because a translation is a derivation
   and the original is the evidence.
4. **Entity and intent extraction** — call signs, spoken MMSI and vessel names,
   spoken positions, declared destination and intention — with the same
   three-valued discipline as every other rule: *contradiction*, *clear*, *not
   checkable*.
5. **Linkage to a track**, by transmission time, the receiving station's arc,
   and any spoken position. Linkage that cannot be made confidently is a
   *finding*, not a guess.
6. **The sixth evidence family.** The extracted claims enter the assistant as
   `radio` factors — for example, a declared destination on VHF that contradicts
   the AIS declaration and the observed track — filling the one slot the factor
   catalogue currently reports as empty.

**Why the rest of the system makes this cheap.** The hard parts of turning a
transcript into intelligence — evidence with provenance, three-valued outcomes,
a score that decomposes, linkage to a track, a place on the operator's screen —
are already built and are shared with the other five areas. What this area adds
is an acoustic front end and an extractor, not a new pipeline.

---

#### 3.4 — Predictive analysis for AIS tracks

**Requirement.** AIS carries kinematics, position and self-declared static
information, and the existing software cannot classify the authenticity of that
static information or the activities of the vessel.

**(a) Authenticity is arithmetic before it is inference.** Two checks carry most
of the value and neither needs a model.

* **The IMO check digit** is a checksum over the first six digits. It rejects
  **90.3% of random seven-digit strings** — a figure verified inside the test
  suite rather than quoted, because that number *is* the rule's justification.
* **The MMSI's Maritime Identification Digits** are allocated by the ITU to a
  flag administration. A hull broadcasting a Panamanian prefix while declaring
  an Indian flag states two incompatible things about itself in one message
  stream.
* **Registry consistency** (name, call sign, vessel type) is a rule in its own
  right, with confidences that differ by field and say why: a call sign is
  issued with the flag and changes only when the flag does, so a mismatch there
  is strong; a name changes on sale and registries lag, so a mismatch there is
  weaker.

The MID-to-flag table is **deliberately partial**. This rule's entire value is
that it almost never produces a false positive, and the fastest way to destroy
that is one wrong row. An unallocated MID produces *not checkable* — no claim at
all — rather than a guess.

**(b) Every check has three outcomes, not two:** *contradiction*, *ok*, and
*not checkable*. An absent IMO number is a gap in the record; reporting it as a
contradiction would fire on most of an honest fleet. Equally, a surface has to
be able to distinguish "we looked and she is fine" from "we could not look".
Summaries report all three, because a check that is *not checkable* on 95% of a
corpus has told you almost nothing, and a report of contradictions alone would
present that silence as a clean bill of health.

**(c) Declared voyage.** AIS message 5 is landed as **its own table**, not as
columns on the position report — a vessel sends a hundred positions per voyage
declaration, and folding them together either repeats the declaration a hundred
times or leaves 99% of rows carrying a null nobody can interpret. Measured on
the synthetic corpus: **3,091 declarations over 131 hulls**, and two rules —
*an arrival no hull could physically make*, and *a destination she never steered
towards* — producing **2 alerts, both true positives, zero false positives.**

**(d) Activity classification** lives with the track engine and reads motion
only, so radar and AIS get the same answer (see 3.1). Anchored, drifting,
loitering, fishing-pattern, transiting, manoeuvring-in-company.

**(e) Forward projection, and the discipline of not promoting it.**
Route-aware projection — a flow field fitted to observed traffic, conditioned on
the vessel's present heading — measured on held-out hulls:

| Horizon | Dead reckoning | Route-aware | Change |
|---|---|---|---|
| 3 h, median position error | 4.70 nm | **2.91 nm** | −38% |
| 6 h, median position error | 16.56 nm | **9.92 nm** | −40% |
| 6 h, on established lane | — | — | **−47%** |

The decisive conditioner was **heading**, not vessel type and not the hull's own
history. An unconditioned flow field measured *worse* than dead reckoning,
because a cell containing a waypoint holds both the inbound and the outbound
course, and asked which is hers it returns the one nearest her present heading —
the inbound one — and steers her straight through the corner.

**It is deliberately not a suspicion factor, and the mechanism is why.** As a
discriminator its precision is 0.09 – 0.33 against a base rate of 0.15 — at or
below chance — where the project's own policy floor is 0.70. The gain is at the
median (−26% to −43% at p50) and almost absent at the tail (−1% to −6% at p90),
and **the uncertainty cone is sized at p90**. A predictor that improves where
the cone is not cannot tighten the cone and therefore cannot discriminate
better. A test asserts that projection is *not* registered as a suspicion
factor, so a future contributor cannot quietly promote it. It travels as an
assertion an officer can act on — where she will be in three hours — not as an
accusation.

**(f) Per-area behavioural baselines** are derived from landed positions and
landed as an inspectable artifact: what normal speed, course dispersion, dwell
and traffic density look like in *this* cell at *this* hour. Baselines
**report distributions and never decide**; a rule reads them.

**(g) Coverage honesty.** A vessel with no AIS in an area where there is no
receiver coverage is not dark — we simply cannot hear her. Intentional silence
is asserted only inside demonstrated coverage; outside it the answer is
`unknown`. Calling an out-of-coverage gap "dark" is a false positive by
construction, and it is the single easiest way for a system like this to
generate impressive-looking nonsense.

**(h) Spoofing tells are signals, not errors.** Two hulls broadcasting the same
MMSI, or a vessel "teleporting" at an impossible speed, are logged as
first-class findings. The reflex to discard them as data-quality problems throws
away exactly the evidence the requirement is asking for.

---

#### 3.5 — Retrieval-augmented application for PANS data

**Requirement.** PANS arrives as PDF, Word and spreadsheet attachments by
e-mail. It contains vital information but cannot be stored in a structured
database or fused with AIS because of its format. The solution must also be
compatible with e-PANS from the National Logistics Portal (Marine) when
available.

**Approach — one record shape for every format, including the electronic one.**
Readers exist per format; all of them produce the *same* intermediate
`Label: value` passages; one format-blind extractor consumes those; one resolver
attaches the record to a hull. **The e-PANS portal feed is another reader on the
same extractor, not a second pipeline.** That is what makes "the electronic feed
drops in without rework" a property demonstrated by shared code rather than
asserted in a design note — a change to date parsing cannot fix the portal and
break the fax, because there is only one date parser.

**Per-field provenance with a locator an analyst can point at.** Every extracted
value carries the passage it came from, *where in the document* that was —
`page 1 (scanned)`, `PANS!A5`, `table 2 row 3` — the method, and a confidence
**earned by that method**: 1.0 for a spreadsheet cell, 0.97 for a PDF text
layer, and whatever the OCR engine itself reports (floored and capped) for a
scan. A character offset satisfies a schema and helps nobody.

**Measured on 381 generated port documents** — five formats (PDF, scanned PDF
with no text layer, DOCX, XLSX, electronic), six document kinds (PANS, arrival
and departure reports, crew lists, cargo manifests, port clearances), six house
styles modelled on real letterheads (Deendayal Kandla, JNPA Nhava Sheva, Mundra,
NMPA Mangalore, Cochin, and an agent's form), with 224 honest documents against
157 authored to lie in eight specific ways. The answer key is written
**outside** the inbox, deliberately, so nothing in the reading path can see it.

| | |
|---|---|
| Documents unread | **0 of 381** |
| Overall field recall | **85.7%** (2,872 of 3,351) |
| Electronic feed | 99.4% |
| DOCX | 88.2% |
| XLSX | 84.5% |
| PDF (text layer) | 79.6% |
| PDF (scanned, OCR) | 76.7% |
| Resolution to a hull | 308 correct · **3 wrong** · 70 declined |
| Rule outcomes | 50 contradiction · 172 ok · 711 not checkable |

**The finding worth acting on, reported rather than buried: the extractor is
format-blind but it is not house-blind.** Across formats the spread is 77% to
99%. Across *issuing agencies* it is not: four houses read at 99%+, **Cochin
reads at 43.1%** and Mangalore at 72.4%. One agency's layout defeats the passage
model. So the claim "a new source drops in without rework" is true for four
houses out of six on this corpus, and closing that is scheduled work in Phase 3
rather than a claim already banked.

**Non-resolution is a finding, in both directions** — a form naming a hull
nothing holds, and a hull berthing with no form. There is deliberately **no
fuzzy name matching**: normalisation recovers what is lossless (prefixes,
punctuation, a dropped space), and a transposition stays unresolved, because
edit distance would resolve `GRANITE TRUIMPH` to `GRANITE TRIUMPH` and would
equally resolve `GRANITE TRIUMPH II`, which is a different ship. Seventy
declines are the design working. Three wrong resolutions are the design failing
quietly, and they are logged as an open defect rather than netted off.

**Fusion with AIS.** Rules compare the paperwork against the track: a declared
last port the vessel never visited, an arrival outside the declared window, a
declared ballast condition against a laden draught. All three-valued, because a
fifth of real forms omit a field and a scan loses two more to OCR — folding "the
form did not say" into "the form was fine" reports a clean inbox nobody read.

**On "RAG" specifically.** The retrieval and citation half is built and is where
the safety lives: an answer is assembled from retrieved rows, and **no fact that
is not in a retrieved row can reach the text.** The generation half is
deliberately *not* a free language model in the answer path — the question
answerer matches a closed set of intents, retrieves, and assembles. It is duller
than a language model and it **cannot confabulate**, which is the correct trade
for the one surface an officer calibrates their trust against. A generative
layer can be substituted behind the same interface **[to build]**, and the
retrieval, provenance and citation scaffolding it would need already exists.

---

#### 3.6 — AI-based MDA assistant for the watchkeeper

**Requirement.** The watchkeeper investigates suspicious movement over radio and
may miss activities of the vessel. An assistant is needed to surface factors of
suspicion for investigation.

**(a) The subject of a Vessel of Interest need not be a vessel.** Measured on
the corpus, **52 of 55 alerts land on a contact or detection node, not on a
named hull.** A target nobody can name is precisely what makes a finding, so the
assistant ranks *subjects* and states which kind each is. Requiring a named hull
would have discarded the dark-vessel path — the capability the requirement most
needs — from its own queue.

**(b) The score decomposes exactly, or it is not a score.** Factors combine as a
noisy-OR over independent evidence, `1 − Π(1 − wₖ·cₖ)`. That does not decompose
additively, but its logarithm does, exactly, so the result is allocated back in
log space and `Σ points == score` to floating point — an identity asserted in
the tests, because it is the whole claim. An officer who reads *"0.81, of which
0.42 is the sanctions designation and 0.39 the dark radar contact"* can argue
with the system. One who reads *"0.81, driven mainly by sanctions"* cannot.

**(c) A repeated fact and a restated fact are not the same thing.** Four
loitering episodes are four things that happened, and each raises the claim. A
designation arriving from the sanctions match table *and* from walking the
graph's ownership chain is **one fact seen twice** — combining that as two
independent observations took nineteen hulls to 0.97 confidence on the first
build, producing a system that sounds *more* certain the more places it looks.
Occurrences and restatements are now distinguished; a restatement takes the
maximum and keeps the corroboration as evidence.

**(d) Recommended actions state capability and compute feasibility.** "Call her
on VHF" is not advice if she is beyond the nearest station, so range is worked
out from the actual station geometry using the same horizon function the radar
model uses, and an infeasible action is returned **with its reason** rather than
hidden. Every recommendation records who performs it and whether the system can
perform it — and for most of them the honest answer today is "this instructs a
human". **Nothing here is autonomous:** there is no path from this module to an
action.

**(e) The question answerer has no generative step, and that is the design.**
Three outcomes are kept distinct, and the middle one carries most of the value:
*answered*; *no data* — understood, and the system holds nothing, phrased as a
statement about the **record** rather than about the vessel; and *unsupported* —
about something this system does not carry, naming which part of the build would
carry it. Asked "what was said on VHF?", it names area 3.3 and says it is not
implemented.

**(f) Six evidence families, keyed to this brief's own areas.** Motion and
behaviour, declared identity, ownership and designation, arrival notifications,
electro-optical, and radio. A coverage function reports which families a given
picture actually contains — the honest version of a progress bar — and `radio`
is currently declared and empty.

**(g) One operator surface.** The product previously had three near-duplicate
screens; they are now one **Watch** view with two lenses over the same facts:
*by vessel* (one row per hull, ranked, every detection about her gathered
underneath) and *by event* (a chronological queue, newest first). Neither is a
filter of the other, because an officer investigates a *ship* and works a
*watch*, and those are different verbs. Disposition controls live on the alert
in both lenses, so recording a decision never requires changing screen — and
those dispositions feed the evaluation harness.

Three-valued rule outcomes are rendered by **shape as well as colour** (a dashed
rule and a "?" for *not checkable*), because colour alone cannot carry a
three-way distinction for a colour-blind reader.

---

## 2. Innovation

The detection rules in this domain are largely public. What is proprietary here
is the discipline around them — the properties that decide whether a watchkeeper
still opens the alerts in month six.

1. **An exactly decomposable suspicion score.** Most risk scores are a weighted
   blend that cannot be taken apart. This one allocates every point back to a
   named factor carrying its own evidence, with the identity asserted in tests.
   An unexplainable score is unsellable to a navy and to an insurer alike.

2. **Three-valued rules everywhere.** *Contradiction* / *clear* / **not
   checkable**. "We could not check" is a first-class answer, never folded into
   "fine". This is the difference between a system that reports a clean inbox
   and a system that reports an inbox nobody read.

3. **Sensor-blind features, asserted rather than claimed.** One model serves
   radar and AIS because the feature vector is provably identical for the same
   track from either sensor. The brief's requirement that behaviours be
   recognisable from either source is met structurally, not by two models kept
   loosely in step.

4. **A class vocabulary derived from the measured confusion matrix.** The system
   publishes the classes it can genuinely separate and names the ones it cannot,
   and the list updates itself as the model improves.

5. **Global assignment wherever targets compete.** Radar-to-AIS association and
   EO camera cueing are both optimal assignments, never greedy nearest-match.
   Greedy matching in association manufactures phantom dark vessels — a real
   ship gets "used up" by a nearby contact, leaving her true contact looking
   unmatched — and greedy cueing starves whole camera networks.

6. **The connector claim is demonstrated by shared code, not asserted.** The
   e-PANS electronic feed is another reader on the same extractor as the scanned
   fax. A source that forced a change in the fusion core would be treated as a
   defect in the core.

7. **Provenance and reproducibility as invariants.** Every row names its source
   and the code commit that produced it; every derived artefact is regenerable
   from raw plus that commit. Attribution separates *origin* (an outside body)
   from *derivation* (what we did to it), and this system's own storage is never
   cited as a source.

8. **Refusal is a designed output.** The resolver declines rather than fuzzy-
   matching a transposed name onto a different ship. Identification declines
   when a hull is one of a class of sisters. Forward projection was measured and
   **not promoted** to a suspicion factor because its precision did not clear
   the policy floor. Each refusal is a false positive that never reached an
   officer.

9. **Coverage honesty.** Silence where there is no receiver is `unknown`, never
   `dark`.

10. **Precision as stated policy, not a tuning accident.** The floor is that at
    least seven of every ten alerts survive human review, even at the cost of
    missing real targets. Recall rises only as measured precision holds. Alert
    fatigue destroys trust before accuracy problems do.

---

## 3. Implementation & Feasibility

### 3.1 What exists today

| | |
|---|---|
| Python modules | 190 |
| Lines of Python | ~60,000 |
| Test files / suite | 49 files; **967 passed, 37 skipped** on the recorded full run |
| Synthetic corpus | 674 vessels · 816,356 AIS positions · simulated CSN radar picture · 381 port documents |
| Live data already landed | 27,791 GFW event and vessel-identity rows; 26,824 sanctions and scene-catalogue rows; 636 Sentinel-1 scene records |
| Operator surface | Map · Watch · Radar · Vessels · Graph, plus one-click incident report |

### 3.2 Deployment shape

The runtime is deliberately small: a FastAPI service, a React + MapLibre
frontend, and DuckDB reading Parquet directly. **There is no database server to
operate, back up, or licence.** The whole stack has been sized to run on a
4-core / 24 GB ARM host, which means a shore station rather than a data centre.

* **On-premise and air-gappable.** No component requires outbound internet at
  run time. Satellite catalogue refresh is the only network-dependent path and
  it degrades cleanly when offline.
* **Data sovereignty.** Nothing leaves the station. The cloud storage tier used
  in the prototype is an environment-selected backend (`local` / `mirror` /
  object store) and resolves through one storage abstraction, so an ICG
  deployment simply selects local.
* **Scheduling is cron plus Python entry points** — no orchestration server to
  operate for what is fundamentally "run these jobs on a schedule".

### 3.3 Integration with the existing CSN

Integration happens at the connector boundary in every case:

| CSN element | Integration path |
|---|---|
| Coastal radar tracks from ROS/ROC | A connector landing into the canonical radar track report schema; correlation, dark filtering and the UI are already wired to it. |
| AIS (dynamic and static) | Existing connectors for live streaming AIS and for message 5 voyage declarations. |
| PANS mailbox | Format readers already built for PDF, scanned PDF, DOCX and XLSX. |
| e-PANS (National Logistics Portal — Marine) | Another reader on the same extractor. Adding it changes no downstream code. |
| EO heads at stations | A real capture source replaces the simulated one behind the existing `CaptureSource` interface. |
| VHF recordings | New module **[to build]**, entering as the sixth evidence family. |

### 3.4 Scalability

The architecture's scaling story is the shared spatial index. Correlation is a
hash join on H3 cells rather than pairwise geometry, so cost grows with the
number of *occupied cells* rather than with the square of the picture. Storage
is columnar Parquet partitioned by time and area, so a query touching one day
and one sector reads one day and one sector. The corpus has been exercised at
674 hulls and 816,356 position reports end to end, with generation completing in
about five minutes.

### 3.5 Verification discipline

A unit is not finished when its tests pass in a development sandbox. It is
finished when its exit test passes **on the target infrastructure**, the
evaluation harness has been re-run and its results logged against the code
commit, and the build-state record has been updated. Sandbox-green is necessary
and not sufficient, and the project's own status ledger distinguishes three
states — *built and sandbox-verified*, *built and unverified on host*, and
*currently doing on real data* — with almost nothing yet in the third.

---

## 4. Challenges & Mitigation

| # | Risk | Why it is real | Mitigation |
|---|---|---|---|
| 1 | **Every performance figure is synthetic.** | A generated corpus with injected ground truth flatters any system measured on it. Real-feed precision will be lower. | Re-measurement on ICG data is Phase 1–2 work with its own exit criteria, and no figure is quoted externally until it has been measured on the real feed. The evaluation harness runs on every model change, logged per commit. |
| 2 | **The document extractor is format-blind but not house-blind** (Cochin 43.1% against 99%+ elsewhere). | One agency's layout defeats the passage model, which threatens the "drops in without rework" claim. | Diagnosed and logged as an open question rather than averaged away. Phase 3 adds per-house reader coverage with a ≥ 90% per-agency gate before the area is called done. |
| 3 | **Three documents resolved to the wrong hull.** | 70 declines are the design working; 3 wrong attachments are the design failing quietly, and each is a document attached to the wrong ship. | Root-caused in Phase 3; the resolver's bias moves further toward declining, since a decline costs an analyst a minute and a wrong attachment costs the evidence chain. |
| 4 | **AIS identity checks cannot be measured on synthetic data by construction.** | The reserved MMSI block that stops a synthetic hull wearing a real vessel's identity also makes a genuine flag contradiction unbuildable. | Measured in Phase 2 on landed real AIS. Until then the checks are reported as built-and-unmeasured, never as validated. |
| 5 | **Forward projection improves the median, not the tail.** | The uncertainty cone is sized at the tail, so a median improvement cannot tighten it, and the module cannot discriminate better. | Not promoted to a suspicion factor; a test enforces this. Re-evaluated in Phase 2 against real traffic, where the corpus's single deterministic corridor no longer flatters the flow field. |
| 6 | **EO can see further than it can prove.** ~20 km to detect, ~8 km to contradict a declared identity in monsoon visibility. | A mismatch alert generated beyond that range would be an overclaim. | Stated as a capability boundary; cueing weights information gain so slots are spent where a photograph would actually resolve something. |
| 7 | **Camera capacity is a physical limit** — 4.5% utilisation, 89,480 deferrals for `no_camera_in_reach`. | A 20 km lens against the Arabian Sea covers a small fraction of the picture. | Reported honestly rather than dressed up. Mitigated by cue *quality*, not by implying coverage that does not exist. Utilisation is reported split so opportunistic fills cannot inflate the demand figure. |
| 8 | **VHF ASR training data for coastal-state languages is scarce.** | Indic maritime speech corpora barely exist, and VHF audio is narrow-band and noisy. | Fine-tune open Indic ASR models; bootstrap from the ICG's own recorded conversations under an agreed data arrangement; exploit the small, procedural VHF vocabulary. Phase 4, with the module kept declared-and-empty until it is measured. |
| 9 | **Alert fatigue.** | An officer who sees three false alarms stops opening the fourth, and no accuracy improvement recovers that. | Precision floor of ≥ 70% surviving review, enforced as policy before recall is raised. Watchkeeper dispositions are captured in the UI and feed the harness. |
| 10 | **SAR preprocessing on ARM is unvalidated**, and the deployment host is not yet provisioned. | The imagery chain depends on a large Java toolchain whose ARM behaviour has not been tested. | Validated in Phase 1 with an x86 fallback held in reserve; the SAR path is independent of areas 3.1–3.6, so a delay there does not block the challenge deliverables. |
| 11 | **A generative RAG layer could confabulate.** | The requirement names RAG, and a language model in the answer path can produce a fluent, sourceless, wrong sentence in front of an officer. | The answer path is retrieval-and-assembly with no generative step: no fact absent from a retrieved row can reach the text. A generative layer, if added, sits behind the same interface with the same citation requirement. |

---

## 5. Visuals & Supporting Data

### 5.1 Capability status against the challenge's own six areas

| Challenge area | Built? | Measured result *(synthetic corpus)* | Not yet |
|---|---|---|---|
| **3.1** Classification of radar data | Yes | 90% coarse / 65% fine on held-out hulls; class vocabulary derived from the confusion matrix; interactions claimed only inside 2.5 nm | No real CSN radar feed; interaction rules measured on few positives |
| **3.2** EO auto-capture, classification, tagging, mismatch alert | Loop yes, camera no | 2 mismatch alerts, 0 false positives; false accusations 0–0.36% per look; classifier swap demonstrated with three implementations | No physical camera; every capture is simulated and every row says so |
| **3.3** Multilingual VHF ASR & NLP | **No** | — | The entire area. Designed, budgeted to Phase 4, declared-and-empty in the assistant today |
| **3.4** Predictive analysis of AIS tracks | Yes | Voyage rules 2 alerts / 2 true positives / 0 false positives; projection 2.91 nm at 3 h, 9.92 nm at 6 h; IMO check digit rejects 90.3% of random strings | Identity arithmetic unmeasurable on synthetic identities; projection deliberately not a suspicion factor |
| **3.5** RAG application for PANS | Yes | 381 documents, 0 unread, 85.7% field recall; e-PANS reader shares the extractor; 308 correct / 3 wrong / 70 declined resolutions | Cochin house at 43.1%; no generative layer; not run against a real mailbox |
| **3.6** MDA assistant | Yes | Score decomposes to floating-point exactness; 52 of 55 alerts on unnamed subjects; five of six evidence families populated | `radio` family empty; never run on operational infrastructure |

### 5.2 Radar-to-AIS correlation and the dark-vessel cascade *(synthetic)*

| Metric | Value |
|---|---|
| Correlation of resolvable radar tracks to the right hull | 98.2% |
| Dark-contact precision | 100% |
| Dark-contact recall | 43% – 62%, varying by corpus draw |

Recall varies across draws because the denominator is a handful of authored
episodes; **precision is the number the policy constrains and it is the number
that holds.** Reporting a recall figure with a denominator of seven episodes as
a capability measurement would be dishonest, and it is not reported as one.

### 5.3 Evidence-family coverage, as the system itself reports it

```
motion    ██████████  populated   (3.1, 3.4)
identity  ██████████  populated   (3.4)
network   ██████████  populated   (registries + ownership graph)
paperwork ██████████  populated   (3.5)
imagery   ██████████  populated   (3.2)
radio     ░░░░░░░░░░  declared and EMPTY  (3.3 — not implemented)
```

The system reports its own holes. A coverage function returns which families a
given picture actually contains, so an officer is never shown a complete-looking
assessment that silently omits an entire class of evidence.

---

## 6. Any other relevant details

### 6.1 The honesty ledger

The project maintains a three-state capability ledger and this proposal is
written from it:

* **Built and verified in a development sandbox** — code exists and its tests
  pass. This is where almost all of the work above sits.
* **Built, unverified on the target host** — code exists and has never run on
  operational infrastructure. The deployment host is not yet provisioned.
* **Currently doing on real data** — has run against a live feed and the result
  has been measured. **Almost nothing is here yet**, and the proposal does not
  pretend otherwise.

Asked today whether the system detects dark vessels, the accurate answer is
that the code to do so is built and measured on a synthetic corpus, and **no
dark vessel has been detected on real operational data.** Under this project it
would be, and the measurement would be published against a code commit.

### 6.2 Why this posture is a technical argument, not modesty

Three of the eleven risks in section 4 were found *because* the system is built
to report what it cannot check. The extractor's house-blindness, the three wrong
document resolutions, and the fact that forward projection does not discriminate
were all discovered by instrumentation that a system optimised for a demonstration
would not have carried. In a domain where the output authorises a boarding, a
system that reports its own limits is not a weaker product than one that does
not. It is the only kind that survives contact with an operator.

### 6.3 What the project buys the ICG

* Five of six areas arriving as **already-built, already-measured code** rather
  than as a specification, with the twelve months spent on real-data
  measurement and the sixth area rather than on first construction.
* An architecture where **paid, classified or future feeds enter as connectors**
  and the fusion core does not change — the property that decides whether this
  is a system or a one-off.
* An evidence chain that survives cross-examination: every assertion traceable
  to a source and to the exact code version that produced it.

---

*Prepared from the Maritime ISR repository. All quantitative results in this
document are measured on a deterministic synthetic corpus with injected ground
truth unless explicitly stated otherwise, and are labelled accordingly. No
figure in this document is a measurement on operational Indian Coast Guard data.*
