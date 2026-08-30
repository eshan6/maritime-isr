"""Who says so — turning a provenance envelope into a sentence an operator can
challenge.

**A source is the outside body or feed a claim came from. This system's own
storage is not a source.** The envelope on every record (CLAUDE.md §4.1) carries
machine fields — `source_id`, `source_ref` — and those are correct as identifiers
and must not change. What was wrong was rendering them raw: the assistant put
`source graph / events` under an accusation, which names a SQLite table and a
column inside this repository. An operator asked to trust a flag cannot audit
"graph / events"; there is nobody to ring up and no record to pull. It reads like
citing a folder on someone's laptop, and against a product whose entire thesis is
traceable trust, that is worse than saying nothing.

Two fields fix it, and they are deliberately separate because they answer
different questions:

* **origin** — the body, register or feed the underlying facts came from. This is
  the thing an operator could go and check independently.
* **derivation** — what this system then did to those facts to reach the
  assertion in front of them. Empty when the record was landed as-is; populated
  whenever we computed, compared, joined or inferred, because a derived claim
  carried under its source's name is the source asserting something it never
  said.

The distinction is the same one ADR-017/018 draws for GFW and OFAC: a dark-vessel
assessment is GFW's finding carried through, a designation is OFAC's decision,
and the identity match between their record and our hull is **ours**. That
sentence is exactly what `derivation` exists to hold.

Nothing here invents attribution. Where a source id is not in the table below it
is humanised rather than dressed up, and an absent one says so plainly — "not
attributed" is an honest answer and a visible defect; a plausible-looking source
name for an unattributed record would be a fabrication.
"""
from __future__ import annotations

from typing import Optional

__all__ = ["origin_of", "describe", "SOURCE_ORIGIN", "UNATTRIBUTED"]

UNATTRIBUTED = "not attributed"

#: `source_id` -> the name of the body, register or feed it stands for.
#:
#: Keys are the ids actually stamped by the connectors in `ingest/` and the rule
#: modules in `anomaly/`. Values are what an operator would name if asked "and
#: who says that?" — an authority, a published dataset, or, for our own rule
#: modules, this system stated as itself rather than hidden behind a module path.
SOURCE_ORIGIN: dict[str, str] = {
    # ---- outside bodies and published datasets -------------------------
    "ofac": "US Treasury OFAC — Specially Designated Nationals list",
    "ofac-vessel-match": "US Treasury OFAC — Specially Designated Nationals list",
    "ofac_vessel_match": "US Treasury OFAC — Specially Designated Nationals list",
    "un": "UN Security Council Consolidated List",
    "eu": "EU Consolidated Financial Sanctions List",
    "gfw": "Global Fishing Watch",
    "gfw_vessel_identity": "Global Fishing Watch — vessel identity records",
    "gfw_encounters": "Global Fishing Watch — encounters",
    "gfw_port_visits": "Global Fishing Watch — port visits",
    "gfw_loitering": "Global Fishing Watch — loitering events",
    "gfw-gaps": "Global Fishing Watch — AIS gap assessments",
    "gfw_gaps": "Global Fishing Watch — AIS gap assessments",
    "ais": "AIS broadcast (terrestrial receivers)",
    "ais_position": "AIS position reports",
    "ais_voyage": "AIS voyage declarations (message 5)",
    "sentinel1": "ESA Copernicus Sentinel-1 synthetic-aperture radar",
    "s1": "ESA Copernicus Sentinel-1 synthetic-aperture radar",
    "csn": "Coastal Surveillance Network radar",
    "radar": "Coastal Surveillance Network radar",
    "pans": "Pre-arrival notifications filed by agents",
    "pans_resolver": "Pre-arrival notifications filed by agents",
    "port_gazetteer": "Port gazetteer",

    # ---- this system's own rule modules --------------------------------
    #
    # Named as ours, explicitly. A rule that reads landed records and reaches a
    # conclusion is making an assertion of its own, and attributing it to the
    # feed it read would put words in the feed's mouth.
    "identity_rules": "Maritime ISR identity rules",
    "anomaly_library": "Maritime ISR anomaly library",
    "imagery_rules": "Maritime ISR imagery rules",
    "voyage_rules": "Maritime ISR voyage rules",
    "paperwork_rules": "Maritime ISR paperwork rules",
    "fusion": "Maritime ISR fusion core",
    "graph": "Maritime ISR object graph",

    # ---- the scenario corpus -------------------------------------------
    #
    # Never disguised. A generated hull's evidence says it was generated, in the
    # same field an operator reads for a real one (ADR-019, CLAUDE.md §4.6).
    "synthetic-scenario": "Generated scenario corpus — not a real observation",
}

#: `source_ref` values that are internal storage rather than an external
#: reference, mapped to what the assertion was actually derived FROM.
#:
#: These are the two that were being printed raw. Both are real derivations this
#: system performs; neither is a place anyone outside could look.
_DERIVED_REFS: dict[str, tuple[str, str]] = {
    "events": (
        "Vessel identity records (registry and AIS static reports)",
        "derived by this system: consecutive identity records held for this "
        "hull were compared field by field, and each difference recorded as a "
        "change",
    ),
    "ownership_chains": (
        "Corporate registry records held in the object graph",
        "derived by this system: ownership links were followed from this hull "
        "to a designated company, and the number of steps is the number of "
        "companies between them",
    ),
}


def origin_of(source_id: Optional[str]) -> str:
    """The name an operator would use for this source. Never a module path."""
    if not source_id:
        return UNATTRIBUTED
    sid = str(source_id).strip()
    if sid in SOURCE_ORIGIN:
        return SOURCE_ORIGIN[sid]
    # An id we have no entry for is humanised, not decorated: underscores and
    # hyphens become spaces so it reads as words rather than as a filename, and
    # nothing is added that would imply an authority we cannot name.
    return sid.replace("_", " ").replace("-", " ")


def describe(provenance: Optional[dict]) -> dict:
    """Add `origin` and `derivation` to a provenance envelope, in place-safe copy.

    The machine fields are left exactly as they are — they are the invariant
    (CLAUDE.md §4.1) and other code joins on them. This only adds the two
    reader-facing strings, so a surface can render attribution without any view
    having to know the id vocabulary.
    """
    p = dict(provenance or {})
    sid = p.get("source_id")
    ref = str(p.get("source_ref") or "")
    if ref in _DERIVED_REFS:
        origin, derivation = _DERIVED_REFS[ref]
        p["origin"] = origin
        p["derivation"] = derivation
    else:
        p["origin"] = origin_of(sid)
        p["derivation"] = None
    return p
