# Maritime ISR — Phase 0 (Pipes)

Free-path fusion-intelligence prototype for the Arabian Sea / Indian west-coast
EEZ (AOI: 5–25°N, 60–78°E). Phase 0 lands free data automatically and makes it
watchable. No ML, no fusion yet — that's Phases 1–3.

## What Phase 0 delivers (units 0.0–0.5)
- **0.0** repo skeleton, canonical schemas, provenance envelope, H3 helper, config loader
- **0.1** Copernicus Sentinel-1 GRD connector (STAC/OData + resumable idempotent R2 downloader)
- **0.2** SNAP preprocessing chain (install stub here; implemented in the 0.2 session)
- **0.3** aisstream.io live AIS consumer (systemd service, hourly Parquet)
- **0.4** NOAA historical AIS + GFW SAR detections + registries (OFAC, versioned/diffed)
- **0.5** inspection dashboard v0 (AOI frame + AIS tracks + S1 footprints)

## Storage model (why it's built this way)
Live AIS must stream 24/7 independent of your laptop, stay free, and survive VM
loss. So: the **aisstream consumer runs as a systemd service on the always-on
Oracle VM**, writing hourly Parquet to local disk; a **cron mirrors closed
partitions to Cloudflare R2** (free tier, zero egress to Cloudflare). Readers
resolve paths via `MISR_STORE_BACKEND` = `local` | `r2` | `mirror` (default
`mirror`: hot data local, durable copy in R2). Nothing downstream hard-codes a
path — everything goes through `store.py` / `db.py`.

## Setup
```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .              # add: pip install -e '.[sar]'  on the SNAP box (0.2)
cp .env.example .env          # fill in credentials as each unit needs them
python -m maritime_isr.config # 0.0 exit test: prints AOI + env status
pytest                        # 14 tests green
```

## Running the connectors
```bash
maritime-isr ingest s1 --days 90        # 0.1 backfill (needs CDSE + R2)
maritime-isr ingest ais --hours 72      # 0.3 (or run forever via systemd)
maritime-isr ingest noaa --month 2025-01# 0.4 historical
maritime-isr ingest gfw                 # 0.4 GFW SAR detections
maritime-isr ingest registries          # 0.4 OFAC snapshot (versioned)
python -m maritime_isr.inspect.export_v0 # 0.5 dashboard snapshot
# then open maritime_isr/inspect/v0/index.html?src=../../../data/inspect_v0_snapshot.json
```

## Deploy the always-on pieces (VM)
```bash
sudo cp maritime_isr/infra/aisstream.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now aisstream
crontab maritime_isr/infra/crontab.example   # R2 mirror + registry refresh + snapshot
```

## Phase 0 exit criteria (from the spec)
- [ ] `maritime-isr ingest s1 --days 90` completes; catalog shows ~25–45 scenes/orbit; re-run downloads nothing new
- [ ] 72h continuous AIS, <1% parser drop rate, dedup verified, queryable in DuckDB
- [ ] GFW SAR detections queryable; sanctions carry as-of dates; re-run diffs not dupes
- [ ] inspection dashboard shows yesterday's AIS tracks + which scenes cover them

## Standing rules in force
Raw immutable; provenance on every record; confidence + time-scope on every
edge; new source = new connector to canonical schema; inspection dashboards get
zero polish before Phase 6.
