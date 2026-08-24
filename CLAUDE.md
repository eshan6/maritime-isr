# CLAUDE.md — Maritime ISR Operating Contract

> This file is read on **every** invocation. Keep it under ~400 lines.
> Detail lives in `ARCHITECTURE.md`, `DECISIONS.md`, `STATE.md`, `GLOSSARY.md`.
> If a rule here has no reason attached, treat that as a bug and ask.

---

## 0. What this project is

Maritime ISR is a **maritime intelligence, surveillance, and reconnaissance
prototype**. Its job: find **dark vessels** — ships that have switched off their
AIS transponder to hide — in the Arabian Sea and Indian west-coast waters, by
fusing free satellite radar imagery (SAR), live ship-broadcast position data
(AIS), and public registries (sanctions lists, port databases). The output is a
ranked, evidence-backed list of vessels behaving suspiciously, with a full chain
of *why* attached to each one.

**Done (the M6 demo)** looks like this: on a laptop, against a live backend with
no rehearsed data, a non-engineer can open a map of the Indian Ocean, find last
week's dark vessels, click one, read the plain-English reason it was flagged, and
export a one-click incident report — in under five minutes.

**The naming rule.** The project is **Maritime ISR** / `maritime_isr`, always. An
older planning document (`bastion-product-roadmap.md`) uses the codename
"Bastion." That name is **retired**. Do not create files, modules, classes, or
docs named Bastion. If you see "Bastion" in the roadmap, read it as "Maritime
ISR." Do not "helpfully" reintroduce the old name.

---

## 1. Who you are talking to

The human operator (**Eshan**) is a **non-technical beginner** by his own
description. This is not false modesty — write for it.

- **Explain every domain term in plain English the first time it appears in a
  session.** AIS, SAR, MMSI, sigma-nought, CFAR, Kalman filter, uncertainty cone,
  Hungarian assignment — none of these are assumed knowledge. See `GLOSSARY.md`;
  when you use a term, give the one-line plain version alongside it.
- **Eshan runs the code; you write it.** He cannot read a stack trace and know
  what you intended. When you hand him something to run, tell him the exact
  command, what a *success* looks like, and what a *failure* looks like, so he can
  report back accurately. He is your hands on the infrastructure — you cannot
  reach his machine or VM.
- **Never overclaim capability.** This is a hard rule, not a tone preference.
  Distinguish, every time, between:
  - **"built to do"** — code exists that is designed to do X, and
  - **"currently doing"** — X has been run on real data and the result measured.
  Most of this system is currently in the first category. Say so. See §5.
- He pushes back on technical overclaiming and wants honest framing. If something
  is fragile, unverified, or synthetic-only, lead with that, don't bury it.

---

## 2. Stack — decided, with reasons (do not relitigate)

These were chosen deliberately. If you think one is wrong, raise it as an OPEN
QUESTION in `STATE.md`; do not silently swap it.

| Choice | Reason it was made |
|---|---|
| **Python 3.11+**, single monorepo | One language, one repo; the whole pipeline is data plumbing + ML, all Python-native. No polyglot overhead for a solo prototype. |
| **DuckDB over Parquet**, not Postgres | Columnar analytical queries over AIS/detection/track tables are the whole workload. DuckDB reads Parquet files directly with zero server to run, back up, or pay for. A Postgres server is an operational tax we don't need until concurrent writers force it — nothing here does yet. |
| **Cloudflare R2** for raw scenes/chips | Zero egress fees (the killer feature — SAR scenes are large and we re-read them). Free tier covers prototype scale. |
| **H3, resolution 7** for joins; **res 9** for fine matching | See §3. This is load-bearing architecture, not a detail. |
| **pyroSAR wrapping ESA SNAP** (`gpt` + workflow XMLs), **not** the `snappy` Python bridge | `snappy` is notoriously brittle to install and version-pin. pyroSAR drives SNAP's command-line `gpt` tool with XML graphs — reproducible, scriptable, far less install pain. |
| **cron + Python entrypoints**, no Airflow | The orchestration is "run these scripts on a schedule." Airflow is a server, a database, and a UI we'd have to operate for no benefit at this scale. |
| **h3 v4 library** — use the `latlng_to_cell` API | v4 renamed the functions from v3 (`geo_to_h3` → `latlng_to_cell`). Pin v4 and use v4 names; mixing versions silently breaks joins. |
| **React + MapLibre GL** on Vercel free tier; **FastAPI** backend on the Oracle VM, reached via **Cloudflare tunnel** | Free hosting both ends; the tunnel exposes the VM API without opening inbound ports (safer, and Oracle's firewall is fiddly). |
| **Compute: Oracle Cloud always-free ARM VM** (4 cores, 24 GB RAM) | $0 standing cost, enough RAM for SNAP. **Not yet provisioned** — see `STATE.md`. ARM (aarch64) raises real questions for SNAP and ML — see OPEN QUESTIONS. |

---

## 3. The one architectural idea everything depends on: H3

Every record with a location — every AIS position, every SAR contact, every scene
footprint — is stamped with the **H3 cell** it falls in. H3 is a grid that
carves the earth into hexagons; **res 7** hexagons are ~5 km across, **res 9** are
~170 m across.

Why this matters more than any other single decision: in Phase 3 we have to ask,
for each radar blip, "which ships could be here?" Without a shared grid that's a
runtime geometry problem (compute distances between every blip and every track —
slow, and it gets slower as data grows). With a shared H3 grid it becomes a
**hash join**: same cell = candidate, done. AIS, SAR, and footprints must all
carry H3 cells computed the **same way** or the joins silently miss matches.

**The contract:** anything landing a located record computes its H3 res-7 and
res-9 cells at ingest, using the shared helper in `schemas/` (do not hand-roll
`latlng_to_cell` calls per module — one helper, one version, everywhere). Full
detail in `ARCHITECTURE.md`.

---

## 4. Hard invariants (violating these corrupts the product silently)

1. **Provenance envelope on every record, in every store. No exceptions.**
   Every row carries: `source_id`, `source_ref`, `acquired_at`, `ingested_at`,
   `pipeline_version` (the git SHA that processed it), and `confidence` (nullable).
   *Reason:* the product **is** trust. A flag an analyst can't trace to its source
   and its code version is worthless — worse, it's a landmine. If you can't stamp
   provenance, you can't land the record.

2. **Raw is immutable; everything downstream is reproducible.**
   Never mutate a raw landed file. Every derived output must be regenerable from
   `raw + git SHA`. *Reason:* this is the only defense against silent corruption —
   if a bug is found, you re-run from raw and get a clean result, rather than
   discovering your "clean" layer was quietly wrong for weeks.

3. **Confidence on every assertion, time-scope on every edge.**
   In the graph (Phase 4+), no naked facts. Every edge carries provenance,
   a confidence, and `valid_from`/`valid_to`. Ownership ends; sanctions have
   as-of dates; a fact true last year may be false now. *Reason:* a stale fact
   asserted as current is how the system embarrasses itself in front of an operator.

4. **High precision before high recall — always, for anything analyst-facing.**
   Tune so that of every 10 alerts, **≥7 survive human review**, even if that
   means missing half the real dark vessels. Recall only rises as measured
   precision holds. *Reason:* alert fatigue kills trust *before* accuracy problems
   do. An analyst who sees three false alarms stops opening the fourth. This is a
   stated product policy, not a tuning accident — see DECISIONS ADR-004.

5. **Every source is a connector, never a core change.**
   New data source = new module in `ingest/` that maps into the canonical schema.
   The fusion core (`fusion/`) must never learn a source-specific hack. *Reason:*
   the whole commercial thesis is "paid/classified feeds slot in later without a
   rewrite." Any connector that forces a change in `fusion/` is a Phase 3 design
   bug — fix the core, don't special-case the connector.

6. **Synthetic ≠ real. Never quote a synthetic number as if it were a real one.**
   Every accuracy figure to date comes from deterministic synthetic test suites
   with injected ground truth. Real-feed precision **will be lower** and must be
   re-measured on the deploy host before any number is stated externally. When you
   report a metric, label its source: "on the synthetic suite" vs "on live data."
   *Reason:* see §1 and §5 — overclaiming is the cardinal sin here.

---

## 5. "Built to do" vs "currently doing" — the honesty ledger

This system is early. Be precise about status in **every** capability claim.

- **Built + verified in sandbox:** code exists, tests pass in Claude's sandbox.
  This is *not* verification on real data or the real host.
- **Built, unverified on host:** code exists, has **never run** on the Oracle VM
  or against live feeds (most of Phase 0 is here — the VM isn't provisioned yet).
- **Currently doing:** has run on real data on the real host, result measured.
  **Almost nothing is here yet.** Do not imply otherwise.

When Eshan (or anyone) asks "can it detect dark vessels?" the honest answer today
is: *the code to do so is being built unit by unit; the first dark-vessel output
arrives at Phase 3, and no dark vessel has been detected on real data yet.* Say
that, not "yes."

---

## 6. Anti-patterns — things a reasonable engineer would try that are WRONG here

- **Greedy per-contact matching in association (Phase 3).** BANNED. Matching each
  radar contact to its single nearest track, one at a time, double-assigns tracks
  and **manufactures phantom dark vessels** (a real ship gets "used up" by a
  nearby contact, leaving its true contact looking unmatched). Use **global
  assignment** (Hungarian / Jonker-Volgenant) across the whole scene at once.

- **Radiometric terrain flattening in SNAP over ocean.** Do **not** enable it.
  Terrain correction here is **geocoding only** (positioning pixels correctly).
  Radiometric flattening triggers SNAP's DEM-tile download, which **hangs**. Over
  open ocean there's no terrain to flatten anyway. See DECISIONS ADR-006.

- **Asserting "intentional silence" offshore.** A ship with no AIS in an area
  where we have **no receiver coverage** is not dark — we just can't hear it.
  Intentional-silence may only be asserted **inside demonstrated coverage**;
  offshore gaps default to `unknown` until the paid satellite-AIS feed (Spire) is
  funded. Calling an out-of-coverage gap "dark" is a false positive by construction.

- **Splitting a track by chip / matching by chip in train-val-test.** When
  building ML datasets, split by **scene**, never by chip. Chips from the same
  scene leak information across the split and inflate metrics.

- **Treating an MMSI collision or impossible-speed jump as a data error to
  discard.** Two ships broadcasting the same MMSI, or a ship "teleporting," is a
  **spoofing tell** — log it as a first-class signal, don't silently drop it.

- **Building pretty inspection dashboards before Phase 6.** Inspection views
  (`inspect/`) are deliberately ugly, throwaway, zero-polish. Any hour spent
  styling them is stolen from the fusion core. Polish happens once, at Phase 6.

- **Backfilling the graph later.** The object graph (Phase 4) accumulates edge
  history from the day it turns on and **cannot be backfilled** — you can't
  retroactively know a ship was here last month if you weren't recording. This is
  why the graph turns on early even at prototype accuracy.

---

## 7. Layout & conventions

Package root is `maritime_isr/`. Module boundaries are firm:

```
ingest/    one module per source; lands raw + normalizes to canonical schema
process/   SAR preprocessing, detection, track building, features
           (activity classification, forward projection, vessel-type
            inference and vessel-to-vessel interactions live in `tracks/` —
            they read motion only, so radar and AIS get the same answer
            without a source-specific branch; ADR-032, ADR-033)
fusion/    association engine + dark-vessel logic (THE fusion core — keep it source-agnostic)
           (an empty `fuse/` package also exists, unused — do not put code there)
           `contact_profile.py` describes a contact that correlates to nothing —
           inferred type + activity + zone (ADR-033). It PROFILES, never
           re-decides darkness: the cascade owns that verdict.
assistant/ the MDA assistant (ADR-031): the ranked Vessel of Interest object —
           factor catalog, decomposable score, plain-language narration,
           recommended next actions, grounded Q&A. ASSEMBLES, never detects:
           a collector that started detecting would be a second, uncalibrated
           copy of a rule that already exists.
baselines.py  per-area behavioural baselines (ADR-032) — what normal looks like
           in *this* cell, derived from landed positions and landed as an
           inspectable artifact. Reports distributions; never decides.
graph/     ontology, edge store, event engine, confidence decay
rules/     anomaly library, risk scoring
eval/      the permanent evaluation harness
api/       FastAPI serving layer
ui/        React + MapLibre (Vercel-deployable)
inspect/   throwaway inspection dashboards (ugly on purpose)
infra/     cron entries, VM setup scripts, R2 config
schemas/   canonical schemas (versioned) + the shared H3 helper
```

- **Storage backend is env-selected:** `MISR_STORE_BACKEND` ∈ `local` | `r2` |
  `mirror`. Path resolution goes through the storage abstraction
  (`store.py` / `db.py` / `writer.py`), never hard-coded paths. During bootstrap
  (no VM yet) `local` is expected; `mirror` writes local and copies closed
  partitions to R2.
- **Credentials via env vars**, loaded by the config loader. Never commit secrets;
  `.env.example` documents the variables (see README).
- **CLI shape:** entrypoints are invoked as `maritime-isr <verb> <target> [opts]`
  (e.g. `maritime-isr ingest s1 --days 90`, `maritime-isr doctor`).
- **Per-unit commit discipline:** one logical unit per commit, tracked in
  `COMMITS.md`. Deliverables are packaged as web-upload zips with dotfiles renamed
  (`gitignore.txt`, `env.example.txt`) because Eshan uploads via GitHub's web UI,
  which chokes on leading-dot files.

---

## 8. Verification — a unit is NOT done until this passes

The build is organized into numbered **units** (0.0 → 6.3) defined in
`maritime-isr-execution-spec.md` (the canonical build contract). For each unit:

1. **The exit test in the spec passes on Eshan's machine / the VM** — not just in
   the sandbox. Sandbox-green is necessary, not sufficient. A unit stays open
   until Eshan pastes back a passing run from real infrastructure.
2. **The evaluation harness runs on every model change**, results logged per git
   SHA. No exceptions. Silent regression here corrupts everything downstream and
   nobody sees it until analysts stop trusting alerts.
3. **`STATE.md` is updated** at end of session: unit status, what's verified vs
   assumed, anything now broken, and the next unit. This file is the memory
   between sessions — update it or the next session starts blind.

If the exit test can't run yet (e.g. it needs the VM, which doesn't exist),
the unit is **"built, unverified on host"** — say so in STATE.md, don't mark it
complete.

---

## 9. When in doubt

- **Don't guess — ask.** Where the plan is genuinely undecided, `STATE.md` has an
  OPEN QUESTIONS section. If you hit an undecided fork, add to it and ask Eshan
  rather than inventing an answer he'll have to unwind later.
- **Read the spec, not your memory of it.** `maritime-isr-execution-spec.md` is
  the build contract; `ARCHITECTURE.md` and `DECISIONS.md` carry the reasoning.
  When they conflict with a vague recollection, the files win.
