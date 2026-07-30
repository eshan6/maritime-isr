# STATE.md — Living Build Status

**This is the memory between sessions.** It is the one file that changes every
session. `CLAUDE.md`, `ARCHITECTURE.md`, `DECISIONS.md` are stable contracts;
**this** file tracks reality.

> **Claude Code: at the end of every session, update this file** — move units
> between status buckets, record what was verified vs assumed, log anything now
> broken, and set "Next up." If you don't update it, the next session starts blind.

**Last updated:** 2026-07-30. Matcher corrections landed (ADR-018), the
reported-vs-landed bug class turned into a check (`ingest/checks.py`), Phase 1
and xView3 recorded as deferred (ADR-017), and the graph populator plus two
analytics written **but not yet run on the real data** — that is Eshan's next
run, see "Next up".

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

**Test tally:** 273 tests passing in-sandbox. Sandbox-green ≠ host-verified — do
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

### Still outstanding

**The connectivity number does not exist yet.** `graph-populate` did not run —
the `maritime-isr` console script is not installed on the laptop. Use
`python tools/graph_report.py`, which is the same code.

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
