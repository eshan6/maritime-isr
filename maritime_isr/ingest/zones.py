"""Connector: a real maritime-boundary file becomes zone rows.

**This is the only way an exclusive economic zone, a contiguous zone, a
territorial sea or a maritime boundary line enters this system.** The project
declines to derive or transcribe those (see `zones/derive.py` for why), so
until this connector is run against a real file those kinds are simply absent,
and the analyses that need them report that they are idle rather than returning
an empty list and looking healthy.

Where to get the file, on a machine that can reach it:

  * **Marine Regions** (VLIZ) — `marineregions.org/downloads.php`. "Maritime
    Boundaries" v12 publishes EEZ, territorial seas (12 nm), contiguous zones
    (24 nm) and archipelagic waters as GeoJSON or shapefile, with the treaty
    status of each boundary recorded. This is the authoritative open source and
    it is what the property mapping below is written against.
  * A national hydrographic office publication, if one is available to you.

Run it with:

    maritime-isr ingest zones --path eez_v12.geojson --kind eez
    maritime-isr ingest zones --path territorial_seas_v4.geojson

**GeoJSON only, and that is deliberate.** Shapefile support would mean adding
`fiona`/`geopandas` — two large binary dependencies with a GDAL build behind
them — to a project whose install story is `pip install -e .` on a Windows
laptop. Every source above offers GeoJSON, and `ogr2ogr` converts one in a
single command for anything that does not.

The connector does **not** invent a kind it cannot determine. A feature it
cannot classify is skipped and counted, and the count is printed, because
silently filing a contiguous zone as an EEZ would produce a layer that is
wrong in the one way nobody would check.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Optional

import shapely
from shapely.geometry import shape

from ..zones.geometry import cells_covering, geom_to_wkt
from ..zones.model import ZONE_KINDS, Zone
from ..zones.store import land_zones
from .checks import report_landed
from .landing import land_raw

__all__ = ["TABLE", "conform_features", "run", "kind_from_properties"]

TABLE = "maritime_zone"

#: How a Marine Regions feature says what it is. `POL_TYPE` carries values like
#: "200NM", "Joint regime", "Overlapping claim"; the territorial-sea and
#: contiguous-zone downloads are separate files whose features carry
#: "12NM"/"24NM". Mapped explicitly rather than by substring, because
#: "Overlapping claim" is a real value and must not silently become an EEZ.
_POL_TYPE_TO_KIND = {
    "200nm": "eez",
    "24nm": "contiguous_zone",
    "12nm": "territorial_sea",
    "joint regime": "eez",
    "joint regime (200nm)": "eez",
}

#: Property keys that might carry a human name, in preference order.
_NAME_KEYS = ("name", "NAME", "GEONAME", "geoname", "TERRITORY1",
              "SOVEREIGN1", "title", "zone_name")

#: Property keys that might carry the kind directly, for a hand-made file.
_KIND_KEYS = ("kind", "zone_kind", "type", "ZONE_KIND")


def kind_from_properties(props: dict, default: Optional[str] = None
                         ) -> Optional[str]:
    """Work out what kind of zone a feature is, or admit that we cannot.

    Order matters: an explicit `kind` written by whoever made the file beats an
    inference from Marine Regions' own vocabulary, which beats the caller's
    `--kind` default. A caller who passes `--kind eez` against a mixed file
    should still get territorial seas filed correctly.
    """
    for k in _KIND_KEYS:
        v = props.get(k)
        if isinstance(v, str) and v.strip().lower() in ZONE_KINDS:
            return v.strip().lower()
    pol = props.get("POL_TYPE") or props.get("pol_type")
    if isinstance(pol, str):
        got = _POL_TYPE_TO_KIND.get(pol.strip().lower())
        if got:
            return got
    return default


def _name_of(props: dict, fallback: str) -> str:
    for k in _NAME_KEYS:
        v = props.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return fallback


def conform_features(features: Iterable[dict], *, default_kind: Optional[str],
                     authority: str, source_ref: str,
                     confidence: float) -> tuple[list[Zone], list[str]]:
    """GeoJSON features → zones. Returns (zones, reasons features were skipped).

    Skips rather than guesses, and returns the reasons so the caller can print
    them. A connector that quietly drops a third of its input is how a layer
    ends up with a hole nobody notices until an analysis is inexplicably quiet.
    """
    zones: list[Zone] = []
    skipped: list[str] = []
    for i, feat in enumerate(features):
        props = feat.get("properties") or {}
        geom_json = feat.get("geometry")
        if not geom_json:
            skipped.append(f"feature {i}: no geometry")
            continue
        kind = kind_from_properties(props, default_kind)
        if kind is None:
            skipped.append(
                f"feature {i}: cannot determine kind — pass --kind, or add a "
                f"`kind` property (one of {', '.join(sorted(ZONE_KINDS))})")
            continue
        if kind not in ZONE_KINDS:
            skipped.append(f"feature {i}: unknown kind {kind!r}")
            continue
        try:
            geom = shape(geom_json)
        except Exception as e:                                   # noqa: BLE001
            skipped.append(f"feature {i}: unreadable geometry ({e})")
            continue
        if geom.is_empty:
            skipped.append(f"feature {i}: empty geometry")
            continue
        if not geom.is_valid:
            # A self-intersecting ring is common in published boundary files
            # and `buffer(0)` is the standard repair. Recorded on the row, not
            # swallowed: a repaired boundary is a slightly different boundary.
            repaired = geom.buffer(0)
            if repaired.is_empty or not repaired.is_valid:
                skipped.append(f"feature {i}: invalid geometry, unrepairable")
                continue
            geom = repaired
            props = {**props, "_repaired": True}

        name = _name_of(props, f"{kind} {i}")
        zid = f"zone:{kind}:" + _stable(f"{source_ref}|{name}|{i}")
        note = ("Geometry repaired with a zero-width buffer on import; the "
                "boundary differs slightly from the source file. "
                if props.get("_repaired") else "")
        zones.append(Zone(
            zone_id=zid, kind=kind, name=name, wkt=geom_to_wkt(geom),
            authority=authority,
            method=f"imported verbatim from {source_ref}",
            confidence=confidence,
            cells=cells_covering(geom),
            facility=None,
            note=note + f"Imported from {source_ref}. This project did not "
                        f"derive or adjust this boundary."))
    return zones, skipped


def _stable(s: str) -> str:
    import hashlib
    return hashlib.sha1(s.encode()).hexdigest()[:10]


def run(path: str | Path, *, kind: Optional[str] = None,
        authority: Optional[str] = None, confidence: float = 0.9,
        clip_to_aoi: bool = True) -> dict:
    """Land a GeoJSON zone file. Raw first, then conformed rows.

    `clip_to_aoi` is on by default and matters more than it looks: a world EEZ
    file is 280 features and most of a gigabyte of coordinates, and landing all
    of it would put the Caribbean in a table this system will only ever query
    over the Arabian Sea. Clipping keeps the layer the size of the problem.
    """
    from ..config import AOI_V1

    p = Path(path)
    payload = p.read_bytes()
    land_raw("zones", p.name, payload)
    doc = json.loads(payload)
    feats = (doc.get("features") if doc.get("type") == "FeatureCollection"
             else [doc])

    src_ref = p.name
    zones, skipped = conform_features(
        feats, default_kind=kind,
        authority=authority or f"imported:{p.name}",
        source_ref=src_ref, confidence=confidence)

    if clip_to_aoi:
        aoi = shapely.from_wkt(AOI_V1.wkt)
        kept: list[Zone] = []
        for z in zones:
            g = shapely.from_wkt(z.wkt)
            if not g.intersects(aoi):
                continue
            clipped = g.intersection(aoi)
            if clipped.is_empty:
                continue
            kept.append(Zone(
                zone_id=z.zone_id, kind=z.kind, name=z.name,
                wkt=geom_to_wkt(clipped), authority=z.authority,
                method=z.method + ", clipped to AOI v1",
                confidence=z.confidence, cells=cells_covering(clipped),
                facility=z.facility,
                note=z.note + (" Clipped to the AOI box; the seaward and "
                               "along-coast edges of this polygon are the AOI "
                               "boundary, not the boundary of the zone.")))
        zones = kept

    written = land_zones(zones, source_id=f"zones:{p.name}",
                         source_ref=src_ref, is_synthetic=False)
    report_landed("zones", TABLE, written.get(TABLE, {}), len(zones),
                  noun="zone")
    if skipped:
        print(f"[zones] skipped {len(skipped)} feature(s):")
        for line in skipped[:10]:
            print(f"[zones]   {line}")
        if len(skipped) > 10:
            print(f"[zones]   ... and {len(skipped) - 10} more")
    return {"zones": len(zones), "skipped": skipped, "written": written}
