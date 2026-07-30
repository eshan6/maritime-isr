"""D1 — Global Fishing Watch vessel identity connector.

Downloads identity records for every vessel that appeared in the landed event
tables, and lands them with **time-scoped** identity facts.

**Why identity history matters here.** A ship's name, flag and MMSI can all be
changed; its IMO number normally cannot. A vessel that changes name and flag
shortly before an encounter is behaving differently from one that has been the
same ship for a decade — but you can only see that if you record *when* each
identity fact was true. That is why every row below carries `valid_from` /
`valid_to` rather than a single "current" name.

**Plain English for the identifiers:**
- **MMSI** (GFW calls it `ssvid`) — the nine-digit number in the AIS broadcast.
  Changeable, reusable, sometimes shared by two ships at once (which is itself a
  spoofing tell, not a data error — CLAUDE.md §6).
- **IMO** — permanent hull number, survives renaming and reflagging.
- **Flag** — the country the ship is registered in, ISO3.
- **Call sign** — the radio identifier.

This connector lands three tables:
- `gfw_vessel_identity` — one row per identity *interval* per vessel
- `gfw_vessel_owners`   — ownership intervals (feeds the Phase 4 graph)
- `gfw_vessel_current`  — one row per vessel, the latest known summary

Ownership and registry intervals map onto the graph's `valid_from`/`valid_to`
edge requirement (CLAUDE.md §4.3). No graph code is modified in this session.
"""
from __future__ import annotations

from datetime import datetime, timezone

from . import gfw_client as gc
from .checks import report_landed
from .landing import land_raw_json, land_table, read_table, stamp_envelope

SOURCE_ID = "gfw-vessels"

IDENTITY_TABLE = "gfw_vessel_identity"
OWNERS_TABLE = "gfw_vessel_owners"
CURRENT_TABLE = "gfw_vessel_current"

# Event tables we harvest vessel ids from.
EVENT_TABLES = (
    "gfw_encounters",
    "gfw_loitering",
    "gfw_port_visits",
    "gfw_ais_gaps",
)

VESSEL_DATASET = "public-global-vessel-identity:latest"

# Stop a systemically broken run early instead of failing thousands of times.
MAX_CONSECUTIVE_FAILURES = 15


def _parse_ts(v) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    s = str(v).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def vessel_ids_from_events() -> list[str]:
    """Every distinct GFW vessel id appearing in the landed event tables.

    Includes encounter counterparts — the other ship in a rendezvous is exactly
    the one you most want identified.
    """
    ids: set[str] = set()
    for table in EVENT_TABLES:
        for row in read_table(table):
            for field in ("vessel_id", "counterpart_vessel_id"):
                v = row.get(field)
                if v:
                    ids.add(str(v))
    return sorted(ids)


def fetch_vessel(vessel_id: str) -> dict | None:
    """Fetch one vessel's full identity record, including history.

    Parameter names verified against the official client's `VesselDetailParams`,
    not guessed. Three things the detail endpoint is fussy about:

    * `dataset` is **singular** here. The events endpoint takes `datasets[]`;
      sending `datasets[0]` to this one returns 422.
    * `includes` accepts only POTENTIAL_RELATED_SELF_REPORTED_INFO. OWNERSHIP
      and AUTHORIZATIONS belong to the *search* endpoint's enum, and sending
      them here is the other half of that 422.
    * `registries-info-data` defaults to NONE, which returns a vessel with no
      registry history and no owners at all — silently useless for our purpose.
      ALL is required.
    """
    try:
        payload = gc.get_json(
            f"vessels/{vessel_id}",
            params={
                "dataset": VESSEL_DATASET,
                "registries-info-data": "ALL",
                "includes[0]": "POTENTIAL_RELATED_SELF_REPORTED_INFO",
            },
        )
    except Exception as e:  # noqa: BLE001
        print(f"[gfw-vessels] {vessel_id}: fetch failed ({type(e).__name__}: {e})")
        return None
    return payload


def map_identity_rows(vessel_id: str, payload: dict) -> list[dict]:
    """One row per registry identity interval — the time-scoped identity facts."""
    rows = []
    entries = payload.get("registryInfo") or []
    for i, r in enumerate(entries):
        vf = _parse_ts(r.get("transmissionDateFrom")) or _parse_ts(r.get("dateFrom"))
        vt = _parse_ts(r.get("transmissionDateTo")) or _parse_ts(r.get("dateTo"))
        if vf is None:
            continue
        row = {
            "vessel_id": vessel_id,
            "record_kind": "registry",
            "mmsi": r.get("ssvid"),
            "imo": r.get("imo"),
            "ship_name": r.get("shipname"),
            "normalised_name": r.get("nShipname"),
            "call_sign": r.get("callsign"),
            "flag": r.get("flag"),
            "length_m": r.get("lengthM"),
            "tonnage_gt": r.get("tonnageGt"),
            "gear_types": ",".join(r.get("geartypes") or []) or None,
            "registry_source": ",".join(r.get("sourceCode") or []) or None,
            "valid_from": vf,
            "valid_to": vt,
        }
        stamp_envelope(
            row, source_id=SOURCE_ID, source_ref=f"{vessel_id}:registry:{i}",
            acquired_at=vf, confidence=None,
        )
        rows.append(row)

    # Self-reported identity — what the ship broadcast about itself. Disagreement
    # with the registry is a signal in its own right, so we keep both.
    for i, r in enumerate(payload.get("selfReportedInfo") or []):
        vf = _parse_ts(r.get("transmissionDateFrom")) or _parse_ts(r.get("firstTransmissionDate"))
        vt = _parse_ts(r.get("transmissionDateTo")) or _parse_ts(r.get("lastTransmissionDate"))
        if vf is None:
            continue
        row = {
            "vessel_id": vessel_id,
            "record_kind": "self_reported",
            "mmsi": r.get("ssvid"),
            "imo": r.get("imo"),
            "ship_name": r.get("shipname"),
            "normalised_name": r.get("nShipname"),
            "call_sign": r.get("callsign"),
            "flag": r.get("flag"),
            "length_m": None,
            "tonnage_gt": None,
            "gear_types": None,
            "registry_source": "self-reported",
            "valid_from": vf,
            "valid_to": vt,
        }
        stamp_envelope(
            row, source_id=SOURCE_ID, source_ref=f"{vessel_id}:self:{i}",
            acquired_at=vf, confidence=None,
        )
        rows.append(row)
    return rows


def map_owner_rows(vessel_id: str, payload: dict) -> list[dict]:
    """Ownership intervals — who owned this hull, and between which dates."""
    rows = []
    for i, o in enumerate(payload.get("registryOwners") or []):
        vf = _parse_ts(o.get("dateFrom"))
        vt = _parse_ts(o.get("dateTo"))
        if vf is None:
            continue
        row = {
            "vessel_id": vessel_id,
            "owner_name": o.get("name"),
            "owner_flag": o.get("flag"),
            "mmsi": o.get("ssvid"),
            "owner_source": ",".join(o.get("sourceCode") or []) or None,
            "valid_from": vf,
            "valid_to": vt,
        }
        stamp_envelope(
            row, source_id=SOURCE_ID, source_ref=f"{vessel_id}:owner:{i}",
            acquired_at=vf, confidence=None,
        )
        rows.append(row)
    return rows


def map_current_row(vessel_id: str, payload: dict) -> dict | None:
    """A single latest-known summary row per vessel, for cheap lookup."""
    entries = (payload.get("registryInfo") or []) + (payload.get("selfReportedInfo") or [])
    if not entries:
        return None

    def sort_key(r):
        return _parse_ts(r.get("transmissionDateTo")) or _parse_ts(r.get("lastTransmissionDate")) \
            or datetime.min.replace(tzinfo=timezone.utc)

    latest = max(entries, key=sort_key)
    seen = sort_key(latest)
    if seen == datetime.min.replace(tzinfo=timezone.utc):
        seen = datetime.now(timezone.utc)

    row = {
        "vessel_id": vessel_id,
        "mmsi": latest.get("ssvid"),
        "imo": latest.get("imo"),
        "ship_name": latest.get("shipname"),
        "call_sign": latest.get("callsign"),
        "flag": latest.get("flag"),
        "length_m": latest.get("lengthM"),
        "tonnage_gt": latest.get("tonnageGt"),
        "n_identity_records": len(entries),
        "n_owners": len(payload.get("registryOwners") or []),
        # How many distinct names/flags/MMSIs this hull has used. A high count is
        # not proof of anything, but it is a reason to look.
        "n_distinct_names": len({e.get("shipname") for e in entries if e.get("shipname")}),
        "n_distinct_flags": len({e.get("flag") for e in entries if e.get("flag")}),
        "n_distinct_mmsi": len({e.get("ssvid") for e in entries if e.get("ssvid")}),
        "last_seen": seen,
    }
    stamp_envelope(
        row, source_id=SOURCE_ID, source_ref=f"{vessel_id}:current",
        acquired_at=seen, confidence=None,
    )
    return row


def run(limit: int | None = None) -> int:
    """Fetch identity for every vessel appearing in the landed event tables."""
    ids = vessel_ids_from_events()
    if not ids:
        print("[gfw-vessels] no vessel ids found in the event tables. "
              "Run `maritime-isr ingest gfw-events` first.")
        return 0
    if limit:
        ids = ids[:limit]

    print(f"[gfw-vessels] fetching identity for {len(ids)} vessels")
    identity_rows: list[dict] = []
    owner_rows: list[dict] = []
    current_rows: list[dict] = []
    failed = 0
    consecutive_failures = 0

    for n, vid in enumerate(ids, 1):
        payload = fetch_vessel(vid)
        if payload is None:
            failed += 1
            consecutive_failures += 1
            # Abort rather than grind through thousands of identical failures.
            # If the first N in a row all fail, the fault is systemic — a wrong
            # id field, a bad dataset slug, a revoked token — and every further
            # request is a guaranteed waste of the operator's time.
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES and not identity_rows:
                print(
                    f"\n[gfw-vessels] ABORTING after {consecutive_failures} consecutive "
                    f"failures and zero successes.\n"
                    f"  This is a systemic fault, not bad luck — continuing through the "
                    f"remaining {len(ids) - n:,} vessels would only repeat it.\n"
                    f"  Most likely: the vessel ids harvested from the event tables are "
                    f"not GFW vessel ids.\n"
                    f"  Diagnose with:  python tools/inspect_raw_event.py"
                )
                return 1
            continue
        consecutive_failures = 0
        land_raw_json(SOURCE_ID, f"vessel_{vid}.json", payload)
        identity_rows.extend(map_identity_rows(vid, payload))
        owner_rows.extend(map_owner_rows(vid, payload))
        cur = map_current_row(vid, payload)
        if cur:
            current_rows.append(cur)
        if n % 25 == 0:
            print(f"[gfw-vessels]   {n}/{len(ids)}")

    if identity_rows:
        w = land_table(identity_rows, table=IDENTITY_TABLE,
                       key_fields=("vessel_id", "record_kind", "mmsi", "ship_name",
                                   "valid_from"),
                       day_field="valid_from")
        report_landed("gfw-vessels", IDENTITY_TABLE, w, len(identity_rows),
                      noun="identity interval")
    if owner_rows:
        w = land_table(owner_rows, table=OWNERS_TABLE,
                       key_fields=("vessel_id", "owner_name", "valid_from"),
                       day_field="valid_from")
        report_landed("gfw-vessels", OWNERS_TABLE, w, len(owner_rows),
                      noun="ownership interval")
    if current_rows:
        w = land_table(current_rows, table=CURRENT_TABLE,
                       key_fields=("vessel_id",), day_field="last_seen")
        report_landed("gfw-vessels", CURRENT_TABLE, w, len(current_rows),
                      noun="vessel summary")
    if failed:
        print(f"[gfw-vessels] {failed} vessel lookups failed (logged above)")
    return 0
