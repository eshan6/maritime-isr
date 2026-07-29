# DATA_SOURCES.md — what we can and cannot download

**Purpose:** settle, once, what data is actually obtainable for our area of
interest under download-only laptop mode, so we never re-litigate it. Every
claim below is dated and sourced. When a source changes, edit this file and move
the old claim to the History section rather than deleting it.

**Area of interest (AOI):** 5–25°N, 60–78°E — Arabian Sea and the Indian west
coast. Defined once in `maritime_isr/config.py` as `AOI_V1`.

**Reconnaissance date:** 2026-07-28. **Verified by:** reading the official
Global Fishing Watch Python client source
(`github.com/GlobalFishingWatch/gfw-api-python-client`, cloned at the above
date) plus GFW platform-update posts. The rendered docs site
(`api-doc.globalfishingwatch.org`, `globalfishingwatch.org/our-apis`) returns
HTTP 403 to automated fetches, so endpoint shapes below come from the client
library source, which is authoritative for request/response models.

---

## Plain-English glossary for this file

- **AIS** — Automatic Identification System. A radio transponder that ships
  broadcast: "I am MMSI 123456789, here is my position, speed and heading."
  Switching it off is how a vessel goes *dark*.
- **MMSI** — the nine-digit ID number in an AIS broadcast. Think of it as the
  ship's radio call number. It can be changed or faked.
- **IMO number** — a permanent hull ID that does *not* change when a ship is
  renamed or reflagged. Harder to fake than MMSI.
- **SAR** — Synthetic Aperture Radar. A satellite radar that sees ships through
  cloud and at night, because it supplies its own illumination. It sees a metal
  hull whether or not the ship is broadcasting AIS — which is the whole point.
- **Sentinel-1** — the European Space Agency's free SAR satellite programme.
  Our SAR imagery source.
- **Gridded / aggregated data** — instead of "a ship at 18.42°N, 68.13°E", you
  get "7 detections somewhere inside this square". Useful for heat maps, not for
  identifying an individual vessel.
- **Point detection** — one row per radar blip, with its own position, size
  estimate and confidence. This is what dark-vessel work needs.
- **Encounter / loitering / port visit / AIS gap** — GFW's derived behaviour
  events. An *encounter* is two vessels close together and slow for a while
  (possible transfer at sea). *Loitering* is one vessel going slowly in open
  water for a while. An *AIS gap* is a vessel that stopped broadcasting and
  later resumed.

---

## Summary table

| Source | What we want | Obtainable? | How |
|---|---|---|---|
| GFW SAR vessel detections — **per detection** | position, time, `length_m`, AIS-matched/unmatched | ⚠️ **Not via API** | Manual browser download from GFW Data Download Portal only |
| GFW SAR presence — **gridded** | detection counts per cell per day | ✅ Yes, via API | `POST /v3/4wings/report`, dataset `public-global-sar-presence:latest` |
| GFW SAR — **currently** | any SAR data at all | 🔴 **Offline since 2026-07-03** | Sentinel-1C/1D pipeline migration; ≥1 month gap announced |
| GFW encounters | two-vessel rendezvous events | ✅ Yes | `POST /v3/events`, `public-global-encounters-events:latest` |
| GFW loitering | slow-transit-in-open-water events | ✅ Yes | `POST /v3/events`, `public-global-loitering-events:latest` |
| GFW port visits | port entry/exit events | ✅ Yes | `POST /v3/events`, `public-global-port-visits-events:latest` |
| GFW AIS gaps | AIS-disabling events | ✅ Yes | `POST /v3/events`, `public-global-gaps-events:latest` |
| GFW vessel identity | name/flag/MMSI/owner history with date ranges | ✅ Yes | `GET /v3/vessels/search`, `GET /v3/vessels/{id}` |
| **Raw historical AIS positions, our AOI** | per-ship position tracks | 🔴 **No free source** | See "Raw AIS" below — this is the structural gap |
| OFAC SDN sanctions | sanctioned entities/vessels | ✅ Yes | Public CSV/XML download |
| UN consolidated sanctions | sanctioned entities/vessels | ✅ Yes | Public XML download |
| EU consolidated sanctions | sanctioned entities/vessels | ✅ Yes | Public download (see note) |
| WPI world port index | port locations/attributes | ✅ Yes | NGA public download |
| Sentinel-1 scene catalog (metadata) | footprints + acquisition times | ✅ Yes | Copernicus Data Space OData/STAC, no login for search |

Legend: ✅ obtainable · ⚠️ obtainable with a caveat that changes the design ·
🔴 blocked / not obtainable.

---

## Global Fishing Watch (GFW)

**Base URL:** `https://gateway.api.globalfishingwatch.org/v3/`
**Auth:** bearer token — `Authorization: Bearer <GFW_API_TOKEN>`. Free
registration; token issued from the GFW API portal.
**Rate limits:** 50,000 requests/day, 1,550,000/month. Far beyond anything this
project will use — a full 8-week AOI pull is on the order of tens of requests.

### 1. SAR vessel detections — the important caveat

**What we assumed:** that the API returns individual SAR detections with
position, timestamp, length estimate and matched/unmatched-to-AIS status.

**What is actually true:** the API returns *gridded aggregates*, not per-detection
rows. The 4Wings report response model
(`resources/fourwings/report/models/response.py`) has these fields:

```
date, detections (int, a COUNT), flag, geartype, hours, vesselIDs, vesselId,
vesselType, entryTimestamp, exitTimestamp, firstTransmissionDate,
lastTransmissionDate, imo, mmsi, callsign, dataset, report_dataset,
shipName, lat, lon
```

`lat`/`lon` are the **grid cell centre**; `detections` is a **count within that
cell**. Spatial resolution is selectable but coarse: `LOW` ≈ 0.1° (~11 km) or
`HIGH` ≈ 0.01° (~1.1 km). There is **no `length_m` field and no
matched/unmatched flag** in the API response.

The per-detection fields we want — `length_m`, `presence_score`,
`matching_score`, `fishing_score`, AIS-matched vs unmatched — exist only in the
**Data Download Portal**, which is a browser-based CSV export, not an API
endpoint. The Bulk Download API (`POST /v3/bulk-reports`) exists but its dataset
enum contains exactly one dataset — `public-fixed-infrastructure-data:latest`
(oil rigs and wind farms) — **not** SAR vessel detections.

**Why this matters for us:** a gridded count of "7 detections in this 1 km cell"
cannot be fed to the Phase 3 association engine, which matches an individual
radar contact to an individual AIS track. Grid cells are not contacts. Using
them as if they were would manufacture exactly the phantom dark vessels
`CLAUDE.md` §6 forbids.

**Consequence:** per-detection SAR is a **manual, human-in-the-loop download**,
not an automatable connector. The connector can still land it — but from a CSV
file Eshan downloads by hand, not from a live API pull.

### 2. SAR data is currently offline

GFW announced on **2026-07-03** that the SAR vessel detections dataset and the
fixed infrastructure dataset are **offline across the map, the APIs and the Data
Download Portal**. Cause: both rely on Sentinel-1, and GFW is migrating its
pipelines from the retired Sentinel-1A/1B to the newer Sentinel-1C/1D
satellites. GFW anticipates **a data gap of at least one month** from that date.

As of today (2026-07-28) that outage is ~3.5 weeks old and has not been
announced as resolved. For an 8-week backfill window (≈2026-06-02 → 2026-07-28),
roughly the **first four weeks may have data and the last 3.5 weeks are
expected empty**, and even the historical portion is subject to the portal being
down.

Source:
<https://globalfishingwatch.org/platform-update/sar-vessel-detection-and-fixed-infrastructure-datasets-issue/>

### 3. Events API — fully available ✅

`POST /v3/events` with a JSON body. All four event families we want are
present as datasets:

```
public-global-encounters-events:latest
public-global-loitering-events:latest
public-global-port-visits-events:latest
public-global-gaps-events:latest
public-global-fishing-events:latest        (bonus, also available)
```

Body fields (from `resources/events/base/models/request.py`):

```
datasets[]        required — dataset ids above
types[]           ENCOUNTER | FISHING | GAP | GAP_START | LOITERING | PORT | PORT_VISIT
start_date        inclusive
end_date          exclusive
geometry          GeoJSON — our AOI polygon goes here
region            alternative: {dataset, id} for named regions (EEZ/MPA/RFMO)
confidences[]     "2" low | "3" medium | "4" high
encounter_types[] CARRIER-FISHING, FISHING-BUNKER, FISHING-TANKER, ...
duration          minimum event duration, minutes
flags[]           ISO3 flag filter
vessel_types[]    BUNKER | CARGO | CARRIER | FISHING | TANKER-adjacent | ...
vessels[]         restrict to specific vessel ids
```

Query params: `limit` (default 99999), `offset` (pagination), `sort`.

**Critically, `geometry` accepts an arbitrary GeoJSON polygon**, so our AOI is
directly expressible — no need to fall back on named regions. And omitting
`vessels` returns events for *all* vessels in the window, which is what an
area-based sweep needs.

This is the strongest available signal for us, and it is **unaffected by the SAR
outage** because these events are derived from AIS, not radar.

### 4. Vessel identity API — available ✅

`GET /v3/vessels/search` (query/where + `includes`), and `GET /v3/vessels` for
lookup by id. The response carries genuine identity *history*, which is what
Phase 4's time-scoped graph edges need:

- `registryInfo[]` — ssvid (MMSI), flag, shipname, callsign, imo, `lengthM`,
  `tonnageGt`, with `transmissionDateFrom` / `transmissionDateTo`
- `registryOwners[]` — owner name, flag, with `dateFrom` / `dateTo`
- `registryPublicAuthorizations[]` — with `dateFrom` / `dateTo`
- `selfReportedInfo[]` — what the vessel broadcast about itself (which can
  disagree with the registry — that disagreement is itself a signal)
- `combinedSourceInfo[]` — gear types and ship types with `yearFrom`/`yearTo`

The date ranges on owners and registry entries map directly onto the
`valid_from`/`valid_to` requirement in `CLAUDE.md` §4.3. This is a good fit.

### 5. Other GFW endpoints seen in the client

- `POST /v3/events/stats` — event counts, useful for sanity-checking a pull
- `GET /v3/insights/vessels` — IUU-risk insight bundles
- `GET /v3/datasets/public-eez-areas/context-layers`,
  `public-mpa-all`, `public-rfmo` — reference geometries (EEZ boundaries etc.)

---

## Raw historical AIS positions — the structural gap 🔴

**Confirmed: there is no free bulk download of raw historical AIS positions for
our AOI.**

- **NOAA / Marine Cadastre** publishes per-broadcast AIS point data as
  daily/zonal CSVs, 2009–2025 — but it is sourced from the **US Coast Guard's
  national receiver network** and is subsetted to the **US Exclusive Economic
  Zone**. Our AOI is the Arabian Sea. Coverage there is **zero**, not sparse.
  This means `maritime_isr/ingest/noaa_ais.py` can never contribute a single row
  for `AOI_V1`; it is only useful if the AOI moves to US waters.
  Source: <https://coast.noaa.gov/digitalcoast/data/vesseltraffic.html>
- **aisstream.io** (wired in `ingest/aisstream.py`) is a *live* websocket feed,
  not a historical archive. It only yields data while a machine is connected and
  listening. Under download-only laptop mode the laptop cannot stay on, so this
  connector is **PARKED**.
- **Satellite AIS (Spire, ORBCOMM, exactEarth)** covers open ocean where shore
  receivers cannot hear, but is **commercial and paid**. Not funded.

**Consequence — read this carefully, it constrains the product:** we can obtain
GFW's *derived* AIS events (encounters, gaps, port visits, loitering) but **not
the underlying position tracks** that produced them. That means the Phase 3
association engine has no AIS track side to associate SAR contacts against, for
this AOI, from free sources.

This does **not** stall the project, but it changes what the near-term product
is. GFW's `gaps` events are themselves a dark-vessel signal computed by someone
with the position data we lack. The honest framing is that we would be *fusing
and enriching GFW's dark-vessel signal with sanctions, ownership and satellite
pass geometry*, rather than *independently detecting dark vessels from raw
AIS + our own SAR detections*. The latter needs either paid satellite AIS or a
long live capture on an always-on host.

Per `CLAUDE.md` §6: an AIS gap in an area with **no receiver coverage is not
evidence of intentional silence.** GFW gap events carry their own confidence and
their own coverage assumptions; those must be preserved, not re-asserted as our
own finding.

---

## Sanctions and registries ✅

Handled by the existing `ingest/registries.py` connector, which already
implements versioned snapshots with as-of dates and diff-on-refresh (never
overwrite — append a new immutable snapshot and record added/removed).

- **OFAC SDN** — `https://www.treasury.gov/ofac/downloads/sdn.csv`
  (plus `sdn_advanced.xml` for richer vessel fields incl. IMO/call sign).
  Public, no key, refreshed on US Treasury's schedule.
- **UN consolidated list** —
  `https://scsanctions.un.org/resources/xml/en/consolidated.xml`. Public XML.
- **EU consolidated list** — published via the EU Sanctions Map / FISMA. The
  machine-readable feed historically requires a (free) token on some endpoints;
  confirm the current no-token URL at implementation time and record it here.
- **WPI (World Port Index)** — NGA Maritime Safety Information, public ZIP
  download. Port name, position, harbour attributes. Small (a few MB).

All four are tiny relative to the 1 GB budget — low tens of MB combined.

---

## Sentinel-1 scene catalog — metadata only ✅

Copernicus Data Space Ecosystem (CDSE) OData/STAC catalog:
`https://catalogue.dataspace.copernicus.eu/odata/v1/Products`

Searching the catalog (footprints, acquisition timestamps, orbit, product type)
needs **no credentials**; only *downloading the imagery itself* needs a CDSE
login. Since we want metadata only, this is a free, keyless, kilobyte-scale
pull — a few thousand scene records for 8 weeks over the AOI.

**Why we want it even with no imagery:** it lets every SAR detection be joined
to the actual satellite pass that produced it, which gives us (a) the *time* a
patch of ocean was genuinely observed, and (b) the ability to distinguish "no
detection because nothing was there" from "no detection because nothing looked."
That distinction is the difference between a real dark-vessel finding and a
coverage artefact.

The existing `ingest/copernicus.py` connector does catalog *and* imagery
download to R2; under laptop mode only the catalog half is used, and the R2
download path is **PARKED**.

---

## What is parked under download-only laptop mode

Per the operating mode (no Oracle VM, no R2, no systemd, no SNAP, no live
capture), these are parked — **not deleted**, and marked with a
`PARKED: awaiting deploy host` marker in the module docstring:

| Module | Why parked |
|---|---|
| `ingest/aisstream.py` | Needs an always-on host to hold a websocket open |
| `infra/aisstream.service` | systemd unit; no Linux host |
| `infra/mirror_cron.py` | Mirrors closed partitions to R2; no R2 |
| `ingest/copernicus.py` (imagery download half) | R2 target + GB-scale scenes |
| `process/s1_preprocess.py`, `validate_sigma0.py` | Need ESA SNAP installed |
| `ingest/noaa_ais.py` | US EEZ only — structurally cannot serve `AOI_V1` |

---

## Disk budget

Total allowance for downloaded data: **under 1 GB**.

| Table | Expected size |
|---|---|
| GFW events (4 types, 8 weeks, AOI) | low tens of MB |
| GFW vessel identity (vessels seen in events) | a few MB |
| GFW SAR gridded presence (if pulled) | a few MB |
| GFW SAR per-detection CSV (manual) | tens of MB |
| Sanctions snapshots (OFAC/UN/EU, versioned) | ~10–30 MB per snapshot set |
| WPI ports | a few MB |
| Sentinel-1 scene catalog metadata | single-digit MB |

Comfortably inside 1 GB. The budget is only at risk if SAR *imagery* is ever
downloaded, which laptop mode explicitly excludes.

---

## History

- **2026-07-28** — initial reconnaissance. Recorded: GFW SAR per-detection data
  is portal-only (not API); GFW SAR is offline since 2026-07-03 pending
  Sentinel-1C/1D migration; raw historical AIS is unobtainable free for this
  AOI; events + vessel identity APIs are healthy and AOI-scopable.

---

## Sources

- GFW API Python client (authoritative for endpoint/response shapes) —
  <https://github.com/GlobalFishingWatch/gfw-api-python-client>
- GFW API documentation (403s to automated fetch; browse manually) —
  <https://globalfishingwatch.org/our-apis/documentation>
- GFW SAR outage notice —
  <https://globalfishingwatch.org/platform-update/sar-vessel-detection-and-fixed-infrastructure-datasets-issue/>
- GFW SAR detections in the 4Wings API (May 2024 release) —
  <https://globalfishingwatch.org/platform-update/2024-may-global-fishing-watch-apis-new-dataset-in-4wings-api-featuring-vessel-detections-from-sentinel-1-sar/>
- GFW SAR detections in the Data Download Portal (May 2024 release) —
  <https://globalfishingwatch.org/platform-update/2024-may-data-download-portal-new-dataset-released-featuring-vessel-detections-from-sentinel-1-sar/>
- NOAA / Marine Cadastre vessel traffic (US EEZ scope) —
  <https://coast.noaa.gov/digitalcoast/data/vesseltraffic.html>
- Copernicus Data Space Ecosystem catalog —
  <https://catalogue.dataspace.copernicus.eu/>
