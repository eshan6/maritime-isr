# STATE.md — Living Build Status

**This is the memory between sessions.** It is the one file that changes every
session. `CLAUDE.md`, `ARCHITECTURE.md`, `DECISIONS.md` are stable contracts;
**this** file tracks reality.

> **Claude Code: at the end of every session, update this file** — move units
> between status buckets, record what was verified vs assumed, log anything now
> broken, and set "Next up." If you don't update it, the next session starts blind.

**Last updated:** 2026-07-28, after the D1 repair + download-only ingest session
(branch `claude/maritime-isr-repair-ingest-d2jrbz`).

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
sandbox (**190 tests**). The repo has switched to **download-only laptop mode**:
no Oracle VM, no R2, no systemd, no SNAP, no live AIS capture. D1 added
connectors that *can* download real GFW events, vessel identity, sanctions, port
and Sentinel-1-catalog data, but **none of them has been run against a live API
yet** — the sandbox has no GFW token and its network policy blocks every data
host, so all D1 code is "built, unverified on host." **No real data has been
landed.** No live AIS has been captured, no SAR scene downloaded or preprocessed,
and no dark vessel detected on real data (nor could one be — that capability
first exists at Phase 3, and it is now additionally blocked by there being no
free source of raw AIS positions for this AOI; see DATA_SOURCES.md).

---

## Unit status

| Unit | What it is | Status | Notes |
|---|---|---|---|
| 0.0 | Repo skeleton + canonical schemas + H3 helper + config loader | 🟡 | Schema round-trip tests green in sandbox. |
| 0.1 | Copernicus Sentinel-1 GRD connector | 🟡 | Written; needs Copernicus account + R2 token + a real backfill to verify. |
| 0.2 | SNAP preprocessing chain (pyroSAR) + install script + `doctor` cmd | 🟡 | Install script memory-capped (12 G heap / tile cache / 4 threads). **Never run on a host** — and ARM is unvalidated (see OPEN QUESTIONS). Fiddliest unit; budget a full session. |
| 0.3 | aisstream live AIS consumer + systemd service | 🟡 | Needs aisstream key + the VM running it as a service for a 72 h capture to verify. |
| 0.4 | NOAA historical AIS + GFW + versioned OFAC/UN/EU/WPI registries | 🟡 | Needs GFW key; diff-on-refresh + as-of dates written, unverified on real pulls. |
| 0.5 | Inspection dashboard v0 (AOI frame, AIS tracks, scene footprints) | 🟡 | Throwaway/ugly by design. Verifies once real AIS + scenes are landing. |
| 1.0 → 6.3 | Phases 1–6 (synthetic prototype) | 🟡 | Implemented and green on the synthetic suites. Every metric is synthetic-only. |
| D1.A | Repair partial-upload breakage | 🟡 | Import breakage was already fixed in `69b0e82`; residual fix was regenerating gitignored synthetic data + removing a re-uploaded `RENAME_AFTER_UPLOAD.md`. Full suite green. |
| D1.B1 | Data-source reconnaissance → `DATA_SOURCES.md` | ✅ | Desk research, complete and sourced. The one unit genuinely *done*. |
| D1.B2 | Laptop-mode hardening + `doctor` rewrite | 🟡 | Backend default flipped to `local`; doctor checks the laptop, not the VM. Passes in sandbox; **needs a run on Eshan's Windows machine.** |
| D1.B3 | GFW connectors (events, vessel identity, SAR) | 🟠 | Code complete, tested against response-shaped fixtures. **Never run against the live API** — no token, host blocked. |
| D1.B4 | Sanctions (OFAC/UN/EU), WPI ports, S1 catalog | 🟠 | Parsers tested against fixtures. **Never run against the live publishers.** EU URL unconfirmed. |
| D1.B5 | Report script + smoke test | 🟡 | `tools/d1_report.py` verified against fixture data landed through the real code path. |

**Test tally:** 190 tests passing in-sandbox. Sandbox-green ≠ host-verified — do
**not** report this as "190 tests prove it works on real data." Every number the
system has ever produced comes from synthetic fixtures.

---

## Blocking dependency chain (what unblocks what)

**Under laptop mode, the D1 chain needs no VM at all:**

```
Free GFW token ──► ingest gfw-events ──► ingest gfw-vessels ──┐
                                                              ├─► d1_report.py
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

**Nothing downstream of "VM provisioned" can be host-verified until the VM exists.**
The VM is therefore the single highest-leverage next action.

A handoff to Claude Cowork was prepared to drive the Oracle Cloud VM setup
end-to-end; Eshan handles account creation, card verification, and credential
steps personally.

---

## Next up

Under download-only laptop mode the VM is no longer the top of the list — the
D1 connectors can be verified on the laptop right now.

1. **Get a free GFW API token** and put it in `.env`. This unblocks all of D1.B3.
2. **Run `maritime-isr doctor` on the Windows laptop** and paste the output back.
   This is the first thing in the whole project that can flip 🟡 → ✅.
3. **Run the D1 downloads** (`ingest gfw-events`, `gfw-vessels`, `registries`,
   `s1 --catalog-only`) and paste back `python tools/d1_report.py`. That converts
   D1.B3/B4 from 🟠 to ✅ and gives us the first real data in the project.
4. **Confirm the EU sanctions URL** — the one in `registries.py` may be gated;
   the connector reports it rather than failing, so a run will tell us.
5. **Decide the AIS-track question** (new OPEN QUESTION #7). It determines what
   the near-term product actually is, and it is the biggest open call.
6. The Oracle VM remains the prerequisite for SAR imagery, SNAP and live AIS —
   but none of those block D1.

---

## Known broken / rough / watch

- **GFW SAR is offline upstream since 2026-07-03**, pending their migration to
  Sentinel-1C/1D, with a ≥1 month gap announced. Both SAR paths degrade to a
  clear message. Re-check before assuming a SAR pull will return anything.
- **Per-detection SAR has no API.** Only gridded counts are automatable; vessel
  length and AIS-match status come from a manual portal CSV export. See
  DATA_SOURCES.md.
- **No free raw historical AIS for this AOI.** Marine Cadastre is US EEZ only, so
  `ingest/noaa_ais.py` can never contribute a row here. This is a structural gap,
  not a bug — it constrains what the product can be. See OPEN QUESTION #7.
- **The EU sanctions URL is unconfirmed** — it may require a token. The connector
  reports and skips rather than failing the other three.
- **`maritime-isr-execution-spec.md` and `bastion-product-roadmap.md` are not in
  the repo.** They are referenced as the canonical build contracts but were never
  uploaded. Unit-level exit tests for D1 were therefore inferred from CLAUDE.md /
  ARCHITECTURE.md / DECISIONS.md. **Upload them.**
- **`H3_RESOLUTION = 6` in `config.py`** (prototype constant) contradicts
  CLAUDE.md §3, which specifies res 7 for joins and res 9 for fine matching.
  `h3util.py` correctly uses 7/9 and all D1 connectors go through it, so nothing
  D1 landed is affected — but the stray constant should be reconciled before
  Phase 3 gating is tuned. Not touched this session (existing phase code).
- **SNAP + ARM is unproven** (OPEN QUESTION #1) — deferred, not blocking D1.
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

7. **THE BIG ONE — what is the product, given there is no free raw AIS for this
   AOI?** Established 2026-07-28 (see DATA_SOURCES.md). We can get GFW's
   *derived* AIS events (encounters, loitering, port visits, gaps) but not the
   underlying position tracks. Phase 3's association engine was designed to match
   our own SAR contacts against our own AIS tracks; without the tracks it has
   nothing to associate. Options:

   **(a) Re-aim at enrichment.** Treat GFW's gap/encounter events as the
   dark-vessel signal and make our contribution the *fusion* — sanctions
   exposure, ownership chains, satellite-pass geometry, risk scoring, the
   evidence-chain product surface. Everything in Phases 4–6 still applies. This
   is deliverable now with zero new funding, but we are enriching someone else's
   detection rather than making our own.

   **(b) Fund satellite AIS (Spire or similar).** Restores the original design
   end to end. Costs money and needs a deploy host.

   **(c) Long live capture on an always-on host.** aisstream.io is free and the
   connector is already written, but it only hears coastal traffic and builds
   history forward from switch-on — no backfill, and weeks of wall-clock before
   Phase 3 has enough to test against.

   These are not mutually exclusive — (a) now, (b) or (c) later — but the choice
   changes what we can honestly claim the system does. **Do not let this be
   decided by default.**
