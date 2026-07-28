# Maritime ISR

A maritime intelligence, surveillance, and reconnaissance prototype that hunts for
**dark vessels** — ships that switch off their AIS transponder to hide — in the
Arabian Sea and Indian west-coast waters. It does this by fusing three kinds of
free, public data:

- **SAR** (satellite radar imagery) — sees ships regardless of weather, day or
  night, whether or not they're broadcasting.
- **AIS** (the position signal ships legally broadcast) — tells us who's who, when
  it's on.
- **Public registries** — sanctions lists, ownership records, port databases.

When the radar sees a ship that the AIS picture can't explain, that's a candidate
dark vessel. The system ranks those candidates by suspicion and attaches a
plain-English chain of evidence to each one.

**New here? Read the docs in this order:** `CLAUDE.md` (the operating rules) →
`ARCHITECTURE.md` (how data flows) → `STATE.md` (what's actually built) →
`GLOSSARY.md` (every term explained) → `DECISIONS.md` (why things are the way they
are). The canonical build plan is `maritime-isr-execution-spec.md`.

---

## Honest capability statement (read this before believing any claim)

> The **code** for the data-landing layer (Phase 0) is written and passes its
> tests **in a sandbox**. **Nothing has yet run on real infrastructure** — the
> deployment VM is not provisioned. No live AIS has been captured on-host, no SAR
> scene preprocessed on-host, and **no dark vessel has been detected on real
> data.** Dark-vessel detection, the first sellable capability, first *exists* at
> Phase 3 and has not been reached.
>
> All accuracy numbers to date come from **synthetic test data with injected
> ground truth.** Real-world precision will be lower and must be re-measured on the
> deploy host before any figure is quoted. When you read "built to do X" here, that
> is **not** the same as "currently doing X." See `CLAUDE.md` §5.

This honesty is a project rule, not modesty. Do not restate prototype status as
production capability.

---

## Repository layout

```
maritime_isr/
  ingest/    connectors — one module per data source
  process/   SAR preprocessing, ship detection, track building, features
  fuse/      association engine + dark-vessel logic (the fusion core)
  graph/     object graph: ontology, edges, event engine, confidence decay
  rules/     anomaly library + risk scoring
  eval/      the permanent evaluation harness
  api/       FastAPI serving layer
  ui/        React + MapLibre frontend (Vercel-deployable)
  inspect/   throwaway inspection dashboards (ugly on purpose, pre-Phase 6)
  infra/     cron entries, VM setup scripts, R2 config
  schemas/   canonical schemas (versioned) + the shared H3 helper
```

---

## Setup

**Prerequisites**
- Python 3.11+
- An Oracle Cloud always-free ARM VM (4 cores / 24 GB) for anything that runs
  continuously — live AIS capture, preprocessing, the API. **Not yet provisioned;**
  see `STATE.md`.
- ESA SNAP on the VM (installed via the script in `infra/`, memory-capped for
  24 GB). SNAP-on-ARM is unvalidated — see `STATE.md` OPEN QUESTIONS.

**Accounts / keys (all free tiers)**
- Copernicus Data Space (Sentinel-1 downloads)
- Cloudflare R2 (raw scene storage) — bucket + API token
- aisstream.io (live AIS websocket)
- Global Fishing Watch (vessel tracks + published SAR detections)

**Install**
```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
cp .env.example .env        # then fill in credentials (see below)
```

> **GitHub web-upload note:** dotfiles are shipped renamed (`gitignore.txt`,
> `env.example.txt`) so GitHub's web uploader accepts them. After upload, rename
> them back to `.gitignore` and `.env.example` — see `RENAME_AFTER_UPLOAD.md`.

---

## Environment variables (in `.env`)

| Variable | Purpose |
|---|---|
| `MISR_STORE_BACKEND` | `local` \| `r2` \| `mirror` — where stores read/write. `local` during laptop bootstrap; `mirror` on the VM (local + R2 copy of closed partitions). |
| `COPERNICUS_USER` / `COPERNICUS_PASS` | Copernicus Data Space credentials |
| `R2_ACCOUNT_ID` / `R2_ACCESS_KEY` / `R2_SECRET_KEY` / `R2_BUCKET` | Cloudflare R2 |
| `AISSTREAM_KEY` | aisstream.io API key |
| `GFW_TOKEN` | Global Fishing Watch API token |

Never commit `.env`. `.env.example` documents the full set.

---

## Running the entrypoints

CLI shape is `maritime-isr <verb> <target> [options]`. Available so far
(all Phase 0 — verify on-host before trusting):

```bash
# Health check: confirms env vars, SNAP install, dependencies
maritime-isr doctor

# Sentinel-1 SAR: backfill the AOI (idempotent — re-runs download nothing new)
maritime-isr ingest s1 --days 90

# Preprocess landed scenes (orbit → noise removal → calibration → terrain-correct → COG)
maritime-isr process s1

# Live AIS: run as a systemd service on the VM (see infra/), or foreground to test
maritime-isr ingest ais

# Historical AIS / GFW / registries
maritime-isr ingest noaa
maritime-isr ingest gfw
maritime-isr ingest registries

# Inspection dashboard v0 (open the generated HTML in a browser)
maritime-isr inspect v0
```

Each command's exit test is defined in `maritime-isr-execution-spec.md`. A command
"working in the sandbox" is not the same as its exit test passing on the VM.

---

## The AOI

Arabian Sea + Indian west-coast EEZ, bounding box **5°N–25°N, 60°E–78°E.**
Locked in the config loader; do not change it casually — many sanity checks
(expected scene counts, coverage model) assume it.

---

## Cost

Target operating cost is **$0**. The only planned paid feed (Spire satellite AIS)
is stubbed and deferred until a demo justifies it. See `DECISIONS.md` ADR-001 /
ADR-005.
