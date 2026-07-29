"""D1 — static registries: OFAC SDN, UN consolidated, EU consolidated, WPI ports.

**Versioned snapshots, never overwrites.** Each refresh writes a new immutable
snapshot stamped with an `as_of` date, and records a diff against the previous
snapshot (what was added, what was removed). We never update a row in place.

**Why version instead of overwrite.** Sanctions have as-of dates. An entity
sanctioned last year may be delisted now; one delisted last year may be
relisted. A graph edge saying "this vessel is sanctioned" with no date attached
is exactly the stale-fact-asserted-as-current failure CLAUDE.md §4.3 exists to
prevent. Keeping every snapshot means we can always answer "was this true on the
date of the event?" rather than only "is it true today?".

**Plain English for the lists:**
- **OFAC SDN** — the US Treasury's Specially Designated Nationals list. Includes
  vessels by name, call sign, tonnage and flag, which is unusually useful for us:
  most sanctions lists name companies and people, this one names hulls.
- **UN consolidated** — the UN Security Council's combined sanctions list.
- **EU consolidated** — the EU's combined financial sanctions list.
- **WPI** — the World Port Index, a public catalogue of the world's ports with
  positions and harbour attributes. Not a sanctions list; we need it because
  "loitering near a port" and "loitering in open water" mean different things,
  and you cannot tell them apart without knowing where the ports are.

All four are small — low tens of MB combined, well inside the 1 GB budget.
"""
from __future__ import annotations

import csv
import io
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone

import requests

from ..config import cfg
from ..db import connect
from ..h3util import index_both
from ..schemas import git_sha, utcnow
from .landing import land_raw

# --------------------------------------------------------------------------
# source URLs
# --------------------------------------------------------------------------
OFAC_SDN_CSV = "https://www.treasury.gov/ofac/downloads/sdn.csv"
UN_CONSOLIDATED_XML = "https://scsanctions.un.org/resources/xml/en/consolidated.xml"
# The EU list is served from the Commission's FSD service and REQUIRES a token
# query parameter — without it the endpoint returns 403, which is what our first
# live run hit. `dG9rZW4tMjAxNw` is the long-standing public token (base64 of
# "token-2017") published in the Commission's own RSS feed of download links.
# Override with MISR_EU_SANCTIONS_URL if the Commission rotates it.
EU_CONSOLIDATED_XML = (
    "https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content"
    "?token=dG9rZW4tMjAxNw"
)
# NGA World Port Index. The key in the path changes between editions; override
# with MISR_WPI_URL when NGA republishes.
WPI_ZIP = (
    "https://msi.nga.mil/api/publications/download"
    "?type=view&key=16920959/SFH00000/UpdatedPub150.zip"
)

TIMEOUT_S = 180


class RegistryUnavailable(RuntimeError):
    """A registry could not be fetched. Other registries still refresh."""


# --------------------------------------------------------------------------
# snapshot bookkeeping
# --------------------------------------------------------------------------

def _ensure_snapshot_meta(con) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS registry_snapshots (
            source_id VARCHAR, as_of TIMESTAMPTZ, n_rows INTEGER,
            pipeline_version VARCHAR, PRIMARY KEY (source_id, as_of)
        )
        """
    )


def _record_snapshot(con, source_id: str, as_of: datetime, n_rows: int) -> None:
    con.execute(
        "INSERT INTO registry_snapshots VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
        [source_id, as_of, n_rows, git_sha()],
    )


def _clear_snapshot(con, table: str, as_of: datetime) -> None:
    """Drop any rows already stored for this exact as_of, so a re-run converges.

    Versioning is per as_of date: refreshing on a *new* date appends a new
    snapshot and keeps the old one. But refreshing twice on the *same* date is a
    repeat of one observation, not two observations — without this, a re-run
    silently doubles the snapshot and every count downstream is wrong.
    """
    try:
        con.execute(f"DELETE FROM {table} WHERE as_of = ?", [as_of])
    except Exception:  # noqa: BLE001 - table may not exist on first run
        pass


def _diff(con, table: str, source_id: str, key_col: str, as_of) -> tuple[int, int]:
    """Compare the newest two snapshots of a source; return (added, removed)."""
    versions = con.execute(
        "SELECT DISTINCT as_of FROM registry_snapshots WHERE source_id=? "
        "ORDER BY as_of DESC LIMIT 2",
        [source_id],
    ).fetchall()
    if len(versions) < 2:
        n = con.execute(f"SELECT count(*) FROM {table} WHERE as_of=?", [as_of]).fetchone()[0]
        return (n, 0)
    new_v, old_v = versions[0][0], versions[1][0]
    added = con.execute(
        f"SELECT count(*) FROM (SELECT {key_col} FROM {table} WHERE as_of=? "
        f"EXCEPT SELECT {key_col} FROM {table} WHERE as_of=?)", [new_v, old_v]
    ).fetchone()[0]
    removed = con.execute(
        f"SELECT count(*) FROM (SELECT {key_col} FROM {table} WHERE as_of=? "
        f"EXCEPT SELECT {key_col} FROM {table} WHERE as_of=?)", [old_v, new_v]
    ).fetchone()[0]
    return added, removed


def _fetch(url: str, source_id: str, filename: str) -> bytes:
    """GET a registry file and land the exact bytes in the immutable raw store.

    Retries 5xx with backoff. NGA's WPI endpoint returns 503 intermittently —
    our first live run hit one — and a government file server having a bad
    minute should not cost us the whole refresh.
    """
    delay = 3.0
    last_status = None
    for attempt in range(1, 4):
        try:
            resp = requests.get(url, timeout=TIMEOUT_S)
        except requests.RequestException as e:
            if attempt == 3:
                raise RegistryUnavailable(f"{source_id}: network error — {e}") from e
            time.sleep(delay)
            delay *= 2
            continue

        if resp.status_code < 400:
            land_raw(source_id, filename, resp.content)
            return resp.content

        last_status = resp.status_code
        if resp.status_code >= 500 and attempt < 3:
            print(f"[registries] {source_id}: HTTP {resp.status_code}, "
                  f"retry {attempt}/3 in {delay:.0f}s")
            time.sleep(delay)
            delay *= 2
            continue
        break

    hint = ("The publisher may have moved or gated the file; "
            "record the working URL in DATA_SOURCES.md.")
    if last_status == 403:
        hint = "403 usually means a required access token is missing from the URL."
    elif last_status and last_status >= 500:
        hint = ("Server-side error that persisted across 3 retries — the publisher "
                "is likely down. Try again later; nothing else is affected.")
    raise RegistryUnavailable(f"{source_id}: HTTP {last_status} from {url}. {hint}")


# --------------------------------------------------------------------------
# OFAC SDN
# --------------------------------------------------------------------------

def parse_ofac(text: str) -> list[dict]:
    """Parse the OFAC SDN CSV.

    Layout (no header row):
      0 ent_num, 1 name, 2 sdn_type, 3 program, 4 title, 5 call_sign,
      6 vessel_type, 7 tonnage, 8 gross_tonnage, 9 vessel_flag,
      10 vessel_owner, 11 remarks
    OFAC writes the literal string '-0-' for an empty field.
    """
    def clean(v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return None if v in ("", "-0-") else v

    rows = []
    for r in csv.reader(io.StringIO(text)):
        if len(r) < 4:
            continue
        rows.append({
            "ent_num": clean(r[0]),
            "name": clean(r[1]),
            "sdn_type": clean(r[2]),
            "program": clean(r[3]),
            "title": clean(r[4]) if len(r) > 4 else None,
            "call_sign": clean(r[5]) if len(r) > 5 else None,
            "vessel_type": clean(r[6]) if len(r) > 6 else None,
            "tonnage": clean(r[7]) if len(r) > 7 else None,
            "gross_tonnage": clean(r[8]) if len(r) > 8 else None,
            "vessel_flag": clean(r[9]) if len(r) > 9 else None,
            "vessel_owner": clean(r[10]) if len(r) > 10 else None,
            "remarks": clean(r[11]) if len(r) > 11 else None,
        })
    return rows


def refresh_ofac(con, as_of: datetime) -> int:
    print("[registries] fetching OFAC SDN ...")
    payload = _fetch(OFAC_SDN_CSV, "ofac-sdn", f"sdn_{as_of:%Y%m%d}.csv")
    rows = parse_ofac(payload.decode("utf-8-sig", errors="replace"))

    con.execute(
        """
        CREATE TABLE IF NOT EXISTS ofac_sdn (
            ent_num VARCHAR, name VARCHAR, sdn_type VARCHAR, program VARCHAR,
            title VARCHAR, call_sign VARCHAR, vessel_type VARCHAR,
            tonnage VARCHAR, gross_tonnage VARCHAR, vessel_flag VARCHAR,
            vessel_owner VARCHAR, remarks VARCHAR,
            as_of TIMESTAMPTZ, pipeline_version VARCHAR
        )
        """
    )
    _clear_snapshot(con, "ofac_sdn", as_of)
    con.executemany(
        "INSERT INTO ofac_sdn VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(r["ent_num"], r["name"], r["sdn_type"], r["program"], r["title"],
          r["call_sign"], r["vessel_type"], r["tonnage"], r["gross_tonnage"],
          r["vessel_flag"], r["vessel_owner"], r["remarks"], as_of, git_sha())
         for r in rows],
    )
    _record_snapshot(con, "ofac-sdn", as_of, len(rows))
    added, removed = _diff(con, "ofac_sdn", "ofac-sdn", "ent_num", as_of)
    vessels = sum(1 for r in rows if (r["sdn_type"] or "").lower() == "vessel")
    print(f"[registries] OFAC as_of={as_of:%Y-%m-%d} rows={len(rows)} "
          f"({vessels} vessels) (+{added} / -{removed} vs prior)")
    return len(rows)


# --------------------------------------------------------------------------
# UN consolidated
# --------------------------------------------------------------------------

def _text(node, tag: str) -> str | None:
    el = node.find(tag)
    return el.text.strip() if el is not None and el.text else None


def parse_un(xml_bytes: bytes) -> list[dict]:
    """Parse the UN consolidated list XML (individuals and entities)."""
    root = ET.fromstring(xml_bytes)
    rows = []
    for kind, path in (("individual", ".//INDIVIDUAL"), ("entity", ".//ENTITY")):
        for node in root.findall(path):
            name_parts = [
                _text(node, "FIRST_NAME"), _text(node, "SECOND_NAME"),
                _text(node, "THIRD_NAME"), _text(node, "FOURTH_NAME"),
            ]
            name = " ".join(p for p in name_parts if p) or None
            rows.append({
                "data_id": _text(node, "DATAID"),
                "name": name,
                "entity_kind": kind,
                "un_list_type": _text(node, "UN_LIST_TYPE"),
                "reference_number": _text(node, "REFERENCE_NUMBER"),
                "listed_on": _text(node, "LISTED_ON"),
                "comments": _text(node, "COMMENTS1"),
            })
    return rows


def refresh_un(con, as_of: datetime) -> int:
    print("[registries] fetching UN consolidated ...")
    payload = _fetch(UN_CONSOLIDATED_XML, "un-consolidated", f"un_{as_of:%Y%m%d}.xml")
    rows = parse_un(payload)

    con.execute(
        """
        CREATE TABLE IF NOT EXISTS un_consolidated (
            data_id VARCHAR, name VARCHAR, entity_kind VARCHAR,
            un_list_type VARCHAR, reference_number VARCHAR, listed_on VARCHAR,
            comments VARCHAR, as_of TIMESTAMPTZ, pipeline_version VARCHAR
        )
        """
    )
    _clear_snapshot(con, "un_consolidated", as_of)
    con.executemany(
        "INSERT INTO un_consolidated VALUES (?,?,?,?,?,?,?,?,?)",
        [(r["data_id"], r["name"], r["entity_kind"], r["un_list_type"],
          r["reference_number"], r["listed_on"], r["comments"], as_of, git_sha())
         for r in rows],
    )
    _record_snapshot(con, "un-consolidated", as_of, len(rows))
    added, removed = _diff(con, "un_consolidated", "un-consolidated", "data_id", as_of)
    print(f"[registries] UN as_of={as_of:%Y-%m-%d} rows={len(rows)} "
          f"(+{added} / -{removed} vs prior)")
    return len(rows)


# --------------------------------------------------------------------------
# EU consolidated
# --------------------------------------------------------------------------

def parse_eu(xml_bytes: bytes) -> list[dict]:
    """Parse the EU consolidated list XML.

    The EU schema is namespaced and has changed shape between versions, so this
    walks defensively by local tag name rather than binding to one namespace.
    """
    root = ET.fromstring(xml_bytes)

    def local(el) -> str:
        return el.tag.split("}")[-1]

    rows = []
    for entity in root.iter():
        if local(entity) != "sanctionEntity":
            continue
        logical_id = entity.get("logicalId") or entity.get("euReferenceNumber")
        name = None
        birth_or_reg = None
        programme = None
        for child in entity.iter():
            tag = local(child)
            if tag == "nameAlias" and name is None:
                name = child.get("wholeName") or child.get("firstName")
            elif tag == "regulation" and programme is None:
                programme = child.get("programme") or child.get("numberTitle")
            elif tag in ("birthdate", "identification") and birth_or_reg is None:
                birth_or_reg = child.get("birthdate") or child.get("number")
        rows.append({
            "logical_id": logical_id,
            "name": name,
            "programme": programme,
            "identifier": birth_or_reg,
        })
    return rows


def refresh_eu(con, as_of: datetime) -> int:
    import os

    url = os.getenv("MISR_EU_SANCTIONS_URL", EU_CONSOLIDATED_XML)
    print("[registries] fetching EU consolidated ...")
    payload = _fetch(url, "eu-consolidated", f"eu_{as_of:%Y%m%d}.xml")
    rows = parse_eu(payload)

    con.execute(
        """
        CREATE TABLE IF NOT EXISTS eu_consolidated (
            logical_id VARCHAR, name VARCHAR, programme VARCHAR,
            identifier VARCHAR, as_of TIMESTAMPTZ, pipeline_version VARCHAR
        )
        """
    )
    _clear_snapshot(con, "eu_consolidated", as_of)
    con.executemany(
        "INSERT INTO eu_consolidated VALUES (?,?,?,?,?,?)",
        [(r["logical_id"], r["name"], r["programme"], r["identifier"], as_of, git_sha())
         for r in rows],
    )
    _record_snapshot(con, "eu-consolidated", as_of, len(rows))
    added, removed = _diff(con, "eu_consolidated", "eu-consolidated", "logical_id", as_of)
    print(f"[registries] EU as_of={as_of:%Y-%m-%d} rows={len(rows)} "
          f"(+{added} / -{removed} vs prior)")
    return len(rows)


# --------------------------------------------------------------------------
# WPI ports
# --------------------------------------------------------------------------

def _f(v):
    try:
        return float(v) if v not in (None, "", "NULL") else None
    except (TypeError, ValueError):
        return None


def parse_wpi(csv_text: str) -> list[dict]:
    """Parse the WPI port CSV. Column names vary by edition, so match loosely."""
    reader = csv.DictReader(io.StringIO(csv_text))
    rows = []
    for r in reader:
        low = {k.lower().strip(): v for k, v in r.items() if k}

        def pick(*names):
            for n in names:
                for k, v in low.items():
                    if k == n or k.replace("_", " ") == n:
                        if v not in (None, ""):
                            return v
            return None

        lat = _f(pick("latitude", "lat", "ycoord", "y"))
        lon = _f(pick("longitude", "lon", "xcoord", "x"))
        if lat is None or lon is None:
            continue
        rows.append({
            "port_id": pick("world port index number", "wpi number", "index_no", "port_number"),
            "port_name": pick("main port name", "port name", "portname", "name"),
            "country": pick("country code", "country"),
            "lat": lat,
            "lon": lon,
            "harbor_size": pick("harbor size", "harborsize"),
            "harbor_type": pick("harbor type", "harbortype"),
        })
    return rows


def refresh_wpi(con, as_of: datetime) -> int:
    import os

    url = os.getenv("MISR_WPI_URL", WPI_ZIP)
    print("[registries] fetching WPI ports ...")
    payload = _fetch(url, "wpi-ports", f"wpi_{as_of:%Y%m%d}.zip")

    # The download is a zip containing a CSV; tolerate a bare CSV too.
    csv_text = None
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            for name in zf.namelist():
                if name.lower().endswith(".csv"):
                    csv_text = zf.read(name).decode("utf-8-sig", errors="replace")
                    break
    except zipfile.BadZipFile:
        csv_text = payload.decode("utf-8-sig", errors="replace")

    if csv_text is None:
        raise RegistryUnavailable("wpi-ports: no CSV found inside the downloaded archive")

    rows = parse_wpi(csv_text)

    con.execute(
        """
        CREATE TABLE IF NOT EXISTS wpi_ports (
            port_id VARCHAR, port_name VARCHAR, country VARCHAR,
            lat DOUBLE, lon DOUBLE, harbor_size VARCHAR, harbor_type VARCHAR,
            h3_r7 VARCHAR, h3_r9 VARCHAR, in_aoi BOOLEAN,
            as_of TIMESTAMPTZ, pipeline_version VARCHAR
        )
        """
    )
    packed = []
    for r in rows:
        r7, r9 = index_both(r["lat"], r["lon"])
        packed.append((
            r["port_id"], r["port_name"], r["country"], r["lat"], r["lon"],
            r["harbor_size"], r["harbor_type"], r7, r9,
            cfg.aoi.contains(r["lat"], r["lon"]), as_of, git_sha(),
        ))
    _clear_snapshot(con, "wpi_ports", as_of)
    con.executemany(
        "INSERT INTO wpi_ports VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", packed
    )
    _record_snapshot(con, "wpi-ports", as_of, len(rows))
    added, removed = _diff(con, "wpi_ports", "wpi-ports", "port_id", as_of)
    in_aoi = sum(1 for p in packed if p[9])
    print(f"[registries] WPI as_of={as_of:%Y-%m-%d} rows={len(rows)} "
          f"({in_aoi} inside the AOI) (+{added} / -{removed} vs prior)")
    return len(rows)


# --------------------------------------------------------------------------
# entrypoint
# --------------------------------------------------------------------------

REFRESHERS = {
    "ofac": refresh_ofac,
    "un": refresh_un,
    "eu": refresh_eu,
    "wpi": refresh_wpi,
}


def run(only: str | None = None) -> int:
    """Refresh every registry. One failing source does not stop the others."""
    con = connect()
    _ensure_snapshot_meta(con)
    as_of = utcnow()

    names = [only] if only else list(REFRESHERS)
    failures = []
    for name in names:
        try:
            REFRESHERS[name](con, as_of)
        except RegistryUnavailable as e:
            print(f"[registries] SKIPPED {name}: {e}")
            failures.append(name)
        except Exception as e:  # noqa: BLE001
            print(f"[registries] FAILED {name}: {type(e).__name__}: {e}")
            failures.append(name)

    ok = [n for n in names if n not in failures]
    print(f"[registries] done — refreshed {ok or 'nothing'}; "
          f"{'failed: ' + ', '.join(failures) if failures else 'no failures'}")
    return 1 if len(failures) == len(names) else 0
