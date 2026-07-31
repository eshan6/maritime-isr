# STATE.md — Living Build Status

**This is the memory between sessions.** It is the one file that changes every
session. `CLAUDE.md`, `ARCHITECTURE.md`, `DECISIONS.md` are stable contracts;
**this** file tracks reality.

> **Claude Code: at the end of every session, update this file** — move units
> between status buckets, record what was verified vs assumed, log anything now
> broken, and set "Next up." If you don't update it, the next session starts blind.

**Last updated:** 2026-07-31 (second pass). The real corpus profile landed
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
vessel detected by us.** GFW's 5 gap events are GFW's finding, not ours. Our own
dark-vessel detection needs SAR contacts matched against AIS tracks, and neither
is obtainable free for this AOI (see DATA_SOURCES.md). The synthetic Phase 1–6
prototype remains green in-sandbox and every metric it produces is synthetic.

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

**Test tally:** 390 tests passing in-sandbox. Sandbox-green ≠ host-verified — do
**not** report this as "273 tests prove it works on real data." Every number the
system has ever produced comes from synthetic fixtures or fixture-driven tests.

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

**Everything below is built and sandbox-green. None of it has run on the real
landed tables** — those live on Eshan's laptop, not in the sandbox. The numbers
these produce are the point; do not quote any of them until he pastes a run back.

### Run these four, in this order, and paste the output back

    1. python -m maritime_isr.cli ingest sanctions-match
    2. python tools/review_matches.py            # DONE - 98/98 pass
    3. python tools/analytic_rename_gap.py       # DONE - re-run after the fix
    4. python tools/graph_report.py              # NOT YET RUN

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
