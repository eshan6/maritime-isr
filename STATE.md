# STATE.md — Living Build Status

**This is the memory between sessions.** It is the one file that changes every
session. `CLAUDE.md`, `ARCHITECTURE.md`, `DECISIONS.md` are stable contracts;
**this** file tracks reality.

> **Claude Code: at the end of every session, update this file** — move units
> between status buckets, record what was verified vs assumed, log anything now
> broken, and set "Next up." If you don't update it, the next session starts blind.

**Last updated:** _(fill in date + git SHA each session)_

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

Phase 0 code is written and passes its sandbox tests, and the repo is uploaded to
GitHub. **Nothing has run on real infrastructure yet, because the Oracle VM is not
provisioned.** Eshan is currently on a **Windows laptop only.** So the whole of
Phase 0 sits at "built, sandbox-green / unverified on host" — not "done." No live
AIS has been captured, no SAR scene has been downloaded or preprocessed on the
host, and no dark vessel has been detected on real data (nor could one be — that
capability first exists at Phase 3).

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
| 1.0 → 6.3 | Phases 1–6 | ⬜ | Not started. See execution spec for unit definitions. |

**Test tally:** 18 tests passing in-sandbox across Phase 0. Sandbox-green ≠
host-verified — do not report this as "18 tests prove it works on real data."

---

## Blocking dependency chain (what unblocks what)

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

1. **Provision the Oracle Cloud always-free ARM VM** (4 cores / 24 GB). Prereq for
   everything runnable.
2. **Validate the ARM question immediately** on the fresh VM (OPEN QUESTION #1) —
   before investing a full session in SNAP debugging on an architecture that might
   not support it.
3. Then work the Phase 0 host-verification chain in dependency order above,
   flipping units 🟡 → ✅ as real runs come back.
4. Session sequence resumes per the execution spec (Session 1 = units 0.0 + 0.1
   on real infra).

---

## Known broken / rough / watch

- **SNAP + ARM is unproven** (see OPEN QUESTIONS #1). This is the biggest single
  unknown in the build.
- Every accuracy or capability statement so far is **synthetic-suite only.** Real
  precision must be re-measured on the host and will be lower.
- GitHub web-UI upload workflow: dotfiles are shipped renamed
  (`gitignore.txt`, `env.example.txt`) with a `RENAME_AFTER_UPLOAD.md`. If a fresh
  clone behaves oddly, check those were renamed back after upload.

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

3. **During bootstrap (no VM), what is `MISR_STORE_BACKEND`?** Presumably `local`
   on the laptop, switching to `mirror` on the VM once R2 is wired. Confirm the
   intended transition point so paths don't get baked wrong.

4. **Registry refresh cadence.** Sanctions/port lists refresh on cron with
   diff-on-refresh, but the interval isn't pinned numerically (daily? weekly?).
   Pick before wiring the 0.4 cron entries.

5. **The two-week live precision sample (unit 3.2)** requires the VM capturing live
   for a continuous fortnight. That's a wall-clock dependency, not a coding one —
   flag the calendar cost when Phase 3 approaches so it isn't a surprise.

6. **Roadmap doc naming.** `bastion-product-roadmap.md` keeps the retired "Bastion"
   codename in its title (see ADR-012). Leave as-is as a historical artifact, or
   rename the file? Cosmetic; confirm before touching, since Eshan tracks commits
   per-unit.
