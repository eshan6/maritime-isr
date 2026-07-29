# STATE.md — Living Build Status

**This is the memory between sessions.** It is the one file that changes every
session. `CLAUDE.md`, `ARCHITECTURE.md`, `DECISIONS.md` are stable contracts;
**this** file tracks reality.

> **Claude Code: at the end of every session, update this file** — move units
> between status buckets, record what was verified vs assumed, log anything now
> broken, and set "Next up." If you don't update it, the next session starts blind.

**Last updated:** 2026-07-29, after the repair + download-only ingest rework
(spec units 0.1 / 0.3 / 0.4). Merged to `main` as `fdb5449`; docs at `c59352b`.

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

Phase 0 code plus the synthetic Phase 1–6 prototype are written and green in the
sandbox (**202 tests**). The repo now runs in **download-only laptop mode**
(ADR-013): no Oracle VM, no R2, no systemd, no SNAP, no live AIS capture. The
ingest rework (units 0.1 / 0.3 / 0.4) added connectors that *can* download real
GFW events, vessel identity, sanctions, ports and Sentinel-1 catalog metadata —
but **none has been run against a live API, and no real data has been landed.**
The sandbox cannot reach any data host (proxy-blocked) and has no GFW token, so
that code is "built, unverified on host." The one genuine host verification so
far is `doctor` passing on the Windows laptop. No AIS captured, no SAR scene
downloaded or preprocessed, no dark vessel detected on real data.

---

## Unit status

| Unit | What it is | Status | Notes |
|---|---|---|---|
| 0.0 | Repo skeleton + canonical schemas + H3 helper + config loader | 🟡 | Schema round-trip tests green in sandbox. |
| 0.1 | Copernicus Sentinel-1 GRD connector | 🟠 | Catalog-only path needs **no credentials** and is the amended exit test (ADR-014). Imagery download PARKED. Never run live. |
| 0.2 | SNAP preprocessing chain (pyroSAR) + install script + `doctor` cmd | 🟡 | Install script memory-capped (12 G heap / tile cache / 4 threads). **Never run on a host** — and ARM is unvalidated (see OPEN QUESTIONS). Fiddliest unit; budget a full session. |
| 0.3 | aisstream live AIS consumer + systemd service | ⬜ | **PARKED** (ADR-013) — needs an always-on host. Exit test deferred verbatim, not amended. |
| 0.4 | GFW + versioned OFAC/UN/EU/WPI registries | 🟠 | Token in place, `doctor` green. Diff-on-refresh + as-of dates written and fixture-tested; **never run live**. SAR clause amended (ADR-014). NOAA half PARKED — US EEZ only. |
| 0.5 | Inspection dashboard v0 (AOI frame, AIS tracks, scene footprints) | 🟡 | Throwaway/ugly by design. Verifies once real AIS + scenes are landing. |
| 1.0 → 6.3 | Phases 1–6 (synthetic prototype) | 🟡 | Implemented and green on the synthetic suites. Every metric is synthetic-only. |

**Ingest rework detail (units 0.1 / 0.3 / 0.4), 2026-07-29:**

| Work | Status | Notes |
|---|---|---|
| Repair of partial-upload breakage | ✅ | Import break was already fixed in `69b0e82`; residual was regenerating gitignored synthetic data + deleting a re-uploaded `RENAME_AFTER_UPLOAD.md`. Suite green. |
| `DATA_SOURCES.md` reconnaissance | ✅ | Desk research, sourced. Genuinely complete. |
| Laptop-mode hardening + `doctor` | ✅ | **Host-verified on the Windows laptop** — first ✅ in the project. |
| GFW connectors (events, identity, SAR) | 🟠 | Fixture-tested only. Never run against the live API. |
| Sanctions / WPI / S1 catalog | 🟠 | Parsers fixture-tested. Never run against live publishers. EU URL unconfirmed. |
| Ingest report + smoke test | 🟡 | `tools/d1_report.py` proven against fixture data through the real code path. Real numbers still outstanding. |

**Test tally:** 202 tests passing in-sandbox. Sandbox-green ≠ host-verified — do
**not** report this as "202 tests prove it works on real data." Every number the
system has ever produced comes from synthetic fixtures or fixture-driven tests.

**Host-only bugs found once real hardware was involved (3):** `pytz` missing for
DuckDB `TIMESTAMPTZ` parameter binding; non-idempotent same-`as_of` registry
refresh; and `.env` never being read at all. None were visible in the sandbox.

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

Under ADR-013 (download-only laptop mode) the VM is no longer the top of the
list — ingest can be verified on the laptop right now.

1. **Run the ingest units on the laptop** (0.1 catalog-only, 0.4 GFW + registries)
   and paste back `python tools/d1_report.py`. Token is in place and `doctor`
   passes. This is the only thing standing between us and the project's first
   real data. **Not closed until the report prints real, non-zero row counts —
   fixture numbers do not count.**
2. **Confirm the EU sanctions URL** — it may be token-gated; the connector
   reports and skips rather than failing, so a live run will tell us.
3. **The H3 unification session (ADR-015).** One helper, resolutions 6/7/8/9 all
   computed directly from coordinates, duplicate deleted, eight fusion-core
   modules touched, harness re-run and baselines restated. **Its own session.**
   Blocking before Phase 3 consumes ingest data.
4. Then Phase 1 (units 1.1–1.4) — unblocked, needs no AIS and no VM.
5. The Oracle VM remains the prerequisite for SAR imagery, SNAP and live AIS
   (all PARKED per ADR-013) — but none of those block the ingest units or Phase 1.

## Known broken / rough / watch

- **GFW SAR is offline upstream since 2026-07-03**, pending their migration to
  Sentinel-1C/1D, with a ≥1 month gap announced. Both SAR paths degrade to a
  clear message. Re-check before assuming a SAR pull will return anything.
- **Per-detection SAR has no API.** Only gridded counts are automatable; vessel
  length and AIS-match status come from a manual portal CSV export. See
  DATA_SOURCES.md.
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
- **TWO H3 helpers at THREE resolutions — a live defect, now ADR-015.** Upgraded
  from "stray constant" after reading the spec: this is a functional break, not
  cosmetic.

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
