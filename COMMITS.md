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

---

# Scenario corpus (ADR-019)

```
git add maritime_isr/scenario/ tools/corpus_profile.py \
        tools/run_scenario_pipeline.py tests/test_scenario.py \
        maritime_isr/ingest/landing.py maritime_isr/graph/store.py \
        maritime_isr/graph/from_landed.py maritime_isr/graph/identity.py \
        maritime_isr/cli.py DECISIONS.md STATE.md COMMITS.md
git commit -m "scenario: synthetic corpus in the real tables, flagged is_synthetic (ADR-019)"
```

Run order on a machine that holds the real data:

```
python tools/corpus_profile.py                      # measure the real corpus
python -m maritime_isr.cli scenario generate --seed 7
python -m maritime_isr.cli scenario status
python tools/run_scenario_pipeline.py               # pipeline + measurement
python -m maritime_isr.cli scenario clear           # remove every synthetic row
```

---

# Real-corpus repairs (ADR-020, ADR-021)

Four units, landed after the corpus profile came back from the laptop and the
generator was aligned to it. Each was found by measuring, not by reading code.

```
git commit -m "ingest: a port visit's span is not its dwell (ADR-020)"
git commit -m "data: correct the ADR-020 diagnosis, de-bias the duration, gate the demo"
git commit -m "ingest: ADR-020 resolved from the raw payloads — the data was fine"
git commit -m "fix: three zeros that were breakage, not measurement (ADR-021)"
git commit -m "ingest: render the port name an operator can read"
```

Run order on a machine that holds the real data. Everything here is offline —
`rebuild_conformed` reads `data/raw/`, so ADR-013 holds and the corpus window
does not move.

```
python tools/data_health.py                     # the demo gate; exits 1 on a blocker
python tools/restamp_h3.py --dry-run            # must report 0 cells added
python tools/rebuild_conformed.py --dry-run     # audit; watch `orphans`, must be 0
python tools/rebuild_conformed.py               # back up data/conformed first
python tools/corpus_profile.py                  # refresh the profile, now de-biased
python tools/port_visit_forensics.py            # raw only; the ADR-020 investigation
```

**Host-verified 2026-07-31**, all of the above run on the operator's laptop
against the real corpus: 81,516 H3 cells added with 0 corrected; `orphans: 0`
across all four event kinds; GFW confidence recovered from 0% to 100% on 3,000
port visits; `visit_port_name` from 54.4% to 100%; 5 of 5 AIS gaps flagged by
GFW as intentional disabling, where this repo had recorded zero.

---

# One canonical vessel key (ADR-022)

Single-focus session. Diagnose, fix structurally, guard with an exercise test,
re-measure without tuning.

```
git add maritime_isr/schemas/keys.py maritime_isr/schemas/__init__.py \
        maritime_isr/graph/from_landed.py maritime_isr/graph/identity.py \
        maritime_isr/graph/store.py maritime_isr/scenario/measure.py \
        tests/test_vessel_keyspace.py tests/test_graph_from_landed.py \
        DECISIONS.md STATE.md COMMITS.md
git commit -m "graph: one canonical vessel key, published by the side that owns it (ADR-022)"
```

Verification, in this order:

```
python -m pytest tests/test_vessel_keyspace.py -q   # the exercise test
python -m pytest tests/ -q                          # 446 green
python -m maritime_isr.cli scenario generate --seed 7
python tools/run_scenario_pipeline.py               # recall must be UNCHANGED
```

**Recall is expected to stay at 14%.** It did — 3 of 22 before and after,
precision 100% both times, 0 false positives across 16 decoys both times. The
keyspace defect governed what an alert *connected to*, not whether it was
raised, and reporting that plainly was the point of the session. What changed:
MMSI-to-hull resolution went from **0 of 103** to **102 of 103**, and an alert
now reaches a hull with a median of 4 edges instead of a provisional stub with 1.

---

## demo: show the data we already have — findings table, UN+EU, density, SAR contacts (ADR-024)

One logical unit: stop discarding landed data at the serving layer.

```
git add maritime_isr/api/{app,models,service}.py \
        maritime_isr/ingest/sanctions_match.py maritime_isr/cli.py \
        maritime_isr/scenario/land.py maritime_isr/scenario/scenarios/common.py \
        frontend/src/views/FindingsView.jsx frontend/src/views/MapView.jsx \
        frontend/src/App.jsx frontend/src/api.js frontend/dist \
        tests/test_sanctions_match.py tests/test_api_exercise.py \
        DECISIONS.md STATE.md COMMITS.md
git commit -m "demo: a ranked findings table, three sanctions registries, and the whole corpus on the map"
```

Verification, in this order:

```
python -m pytest tests/test_sanctions_match.py -q    # 47 green (16 new)
python -m pytest tests/test_api_exercise.py -q       # 28 green (15 new)
python -m pytest tests/ -q                           # 480 green
cd frontend && npm run build                         # dist/ rebuilt
python -m maritime_isr.api                           # open /findings and /
```

**On the laptop, additionally — the matcher must be re-run.**
`sanctioned_vessel_matches` gains `registry`, `listed_entity_type` and the
`vessel_*` fields, and `registry` joins the natural key:

```
python -m maritime_isr.cli ingest registries          # refresh UN + EU
python -m maritime_isr.cli ingest sanctions-match     # all three registries
```

*The number to look for:* how many findings UN and EU add beyond OFAC's 126.
**Zero is a reportable result** — those lists name far fewer vessels than OFAC.
`--registries OFAC` reproduces the pre-ADR-024 behaviour for comparison.

Scenario generation is unchanged in geometry — the only new column on a
scenario row is `listed_entity_type`, so the corpus is otherwise byte-identical
and the determinism test stays green.
