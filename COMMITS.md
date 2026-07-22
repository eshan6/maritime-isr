# Phase 0 — per-unit commit plan

Commit in this order (each maps to one execution-spec unit):

## 0.0 — repo skeleton + schemas
```
git add pyproject.toml .gitignore .env.example README.md \
        maritime_isr/__init__.py maritime_isr/config.py maritime_isr/h3util.py \
        maritime_isr/store.py maritime_isr/db.py maritime_isr/writer.py \
        maritime_isr/writer_detections.py maritime_isr/cli.py \
        maritime_isr/schemas/ tests/
git commit -m "0.0: repo skeleton, canonical schemas, provenance envelope, H3 helper, config, storage layer"
```

## 0.1 — Copernicus S1 connector
```
git add maritime_isr/ingest/__init__.py maritime_isr/ingest/copernicus.py
git commit -m "0.1: Copernicus Sentinel-1 GRD connector (STAC/OData + resumable idempotent R2 downloader)"
```

## 0.2 — SNAP preprocessing (install stub only in Phase 0)
```
git add maritime_isr/infra/install_snap.sh
git commit -m "0.2: SNAP install script stub (chain implemented in 0.2 session)"
```

## 0.3 — aisstream live connector + service
```
git add maritime_isr/ingest/aisstream.py maritime_isr/infra/aisstream.service
git commit -m "0.3: aisstream.io live AIS consumer + systemd service"
```

## 0.4 — historical AIS + GFW + registries
```
git add maritime_isr/ingest/noaa_ais.py maritime_isr/ingest/gfw.py \
        maritime_isr/ingest/registries.py maritime_isr/infra/mirror_cron.py \
        maritime_isr/infra/crontab.example
git commit -m "0.4: NOAA historical AIS, GFW SAR detections, versioned registries, R2 mirror cron"
```

## 0.5 — inspection dashboard
```
git add maritime_isr/inspect/
git commit -m "0.5: inspection dashboard v0 (AOI frame, AIS tracks, S1 footprints)"
```

---

# Unit 0.2 — SNAP preprocessing chain (implemented)

```
git add maritime_isr/infra/install_snap.sh \
        maritime_isr/process/s1_preprocess.py \
        maritime_isr/process/validate_sigma0.py \
        maritime_isr/process/snap_doctor.py \
        maritime_isr/cli.py tests/test_preprocess.py
git commit -m "0.2: SNAP preprocessing chain (pyroSAR gpt), sigma0 validator, doctor, memory-capped install"
```
