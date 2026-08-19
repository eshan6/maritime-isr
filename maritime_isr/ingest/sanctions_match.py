"""ADR-016a — match identified vessels against sanctioned hulls, directly.

**Why this exists.** The roadmap's canonical chain assumes you traverse
*vessel → owned-by → owner sanctioned-under OFAC*. Measured on the first live
run (2026-07-29), GFW registry ownership covers **61 intervals across 9,184
vessels — 0.66%**. At that coverage the chain has an owner to look up for
roughly 1 vessel in 150, so it will effectively never fire on free data.

So we invert it. OFAC SDN names **1,516 vessels outright**, each with call sign,
vessel type, tonnage, flag and a `vessel_owner` field. Matching our identified
vessels straight against those gives a sanctioned-vessel finding that needs no
ownership edge at all, and OFAC's own `vessel_owner` column supplies the
organisation side — the org graph is built from the sanctions list rather than
from GFW.

**Three registries, not one.** `registries.py` has always landed UN consolidated
and EU consolidated alongside OFAC — 1,011 and 6,017 rows on the 2026-07-29 run
— and until now nothing read them. They are matched here too, but they are *not*
the same kind of list and the difference is load-bearing:

- **OFAC SDN has a vessel record type.** `sdn_type='vessel'` rows carry call
  sign, vessel type, tonnage and flag, so all four match tiers are available.
- **UN and EU have no vessel schema at all.** UN rows are individuals and
  entities (`data_id, name, entity_kind, un_list_type, comments`); EU rows are
  `logical_id, name, programme, identifier`. Neither has a call-sign column, a
  flag column, or a vessel-type column. A designated *ship* appears as an entity
  whose free text happens to mention it.

So UN and EU contribute through **IMO extracted from free text** — the same
keyword-anchored, check-digit-validated extraction OFAC's `remarks` already
needs — and they contribute a name match **only** when the designation row
carries positive evidence that it names a vessel (see :func:`looks_like_vessel`).
Matching a vessel name against an arbitrary UN entity name would mostly hit
trading companies and people, which is a name collision dressed as a sanctions
hit — exactly the false positive ADR-004 exists to prevent.

**Match precedence is load-bearing: IMO > call sign + name > call sign > name.**

- **IMO** is a permanent hull number. It survives renaming and reflagging, and
  is the only identifier here that is hard to fake. An IMO match is a finding.
- **Call sign + name agreement** is two independent identifiers agreeing. Weaker
  than an IMO but strong enough to assert: a finding.
- **Call sign alone** is *not*. Call signs are assigned by the flag state, are
  **reused after reassignment**, and short ones collide internationally — a
  four-character call sign is not a globally unique key and never was. So a
  call-sign-only hit is a **CANDIDATE**, promoting to a finding only when the
  name corroborates it.
- **Name** is freely changeable and collides constantly — real fleets contain
  many hulls called some variant of OCEAN STAR. **A name-only match is a
  CANDIDATE, never a finding**, and carries low confidence per CLAUDE.md §4.3.

That gradient is the whole point. Treating a name collision as a sanctions hit
is precisely the false positive that destroys analyst trust (ADR-004), so the
match tier travels with every row and downstream code must respect it.

Nothing here touches the fusion core (CLAUDE.md §4.5) — this is ingest-side
enrichment producing its own table.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from ..config import CLI, cfg
from ..db import connect
from .checks import check_coverage, report_landed
from .landing import land_table, read_table, stamp_envelope

MATCH_TABLE = "sanctioned_vessel_matches"

#: Provenance `source_id` per registry. Each row names the list it actually came
#: from rather than a single blended id — a finding an analyst cannot trace to a
#: specific published list is not traceable at all (CLAUDE.md §4.1). The OFAC
#: value is unchanged from before UN/EU were added, so rows landed by earlier
#: runs keep the same provenance.
SOURCE_ID_BY_REGISTRY = {
    "OFAC": "ofac-vessel-match",
    "UN": "un-vessel-match",
    "EU": "eu-vessel-match",
}
#: Kept for callers that still import it; OFAC remains the default registry.
SOURCE_ID = SOURCE_ID_BY_REGISTRY["OFAC"]

#: Registry precedence when two lists designate the same hull. OFAC first
#: because its vessel rows are the only ones carrying call sign, flag and
#: tonnage — the match is the same, but the evidence shown to an analyst is
#: richer. The other designations are still recorded as their own rows.
REGISTRY_ORDER = ("OFAC", "UN", "EU")

# Confidence by match tier. Deliberately wide gaps — the tiers are not
# interchangeable and the numbers should make that obvious downstream.
#
# `call_sign` sits BELOW the finding threshold on purpose. Call signs are
# flag-state assigned and reused after reassignment, so a bare call-sign hit is
# a lead, not an assertion. It becomes `call_sign_name` — and a finding — only
# when the vessel name agrees too, which is two independent identifiers.
TIER_CONFIDENCE = {
    "imo": 0.95,
    "call_sign_name": 0.80,
    "call_sign": 0.40,
    "name": 0.35,
}
# Below this, a row is a candidate for review rather than an assertion.
FINDING_THRESHOLD = 0.50

# Ordered strongest-first, so downstream code can rank tiers without hard-coding
# the confidence numbers.
TIER_ORDER = ("imo", "call_sign_name", "call_sign", "name")

# Vessel-type prefixes that carry no identifying information, in the form they
# take AFTER punctuation has become whitespace — "M/V" normalises to "M V", so
# the table must hold "M V" and not "m/v".
#
# Stripped from the FRONT only. Dropping a bare "M" or "V" anywhere in a name
# would corrupt legitimate ones, and a vessel really can be called "V OCEAN".
# Longest first, so "M V" is tried before "M".
_PREFIXES = (
    "M V", "M T", "M S", "S S", "F V", "MV", "MT", "MS", "SS", "FV",
    "MSV", "MOPU", "THE", "VESSEL", "EX", "AKA",
)


def normalise_name(name: str | None) -> str | None:
    """Collapse a vessel name to a comparable key.

    Uppercase, strip punctuation, drop prefix noise, squeeze whitespace. GFW
    names arrive with artefacts like 'ADEL>K>QAYYUM' — those '>' separators are
    real, seen in the live data — so punctuation becomes spaces rather than
    being deleted, which would fuse words together.
    """
    if not name:
        return None
    s = re.sub(r"[^A-Z0-9]+", " ", str(name).upper()).strip()
    s = re.sub(r"\s+", " ", s)

    # Peel leading type prefixes, repeatedly — "EX M/V FOO" has two.
    changed = True
    while changed and s:
        changed = False
        for p in _PREFIXES:
            if s == p:
                return None                     # nothing but prefix
            if s.startswith(p + " "):
                s = s[len(p) + 1:]
                changed = True
                break
    return s or None


def normalise_call_sign(cs: str | None) -> str | None:
    if not cs:
        return None
    s = re.sub(r"[^A-Z0-9]", "", str(cs).upper())
    return s or None


def imo_checksum_ok(digits: str) -> bool:
    """Validate an IMO number's check digit.

    The seventh digit is a checksum: multiply the first six by 7, 6, 5, 4, 3, 2,
    sum them, and the last digit of that sum must equal the seventh digit.

    This is **independent evidence** from the extraction check. Verifying we
    read the right characters out of OFAC's remarks says nothing about whether
    the number itself is a real IMO — a transcription error in OFAC's own text
    would pass extraction and still be wrong. Measured here: the checksum
    rejects 90.3% of random 7-digit strings, so a passing number is very
    unlikely to be a typo.
    """
    if len(digits) != 7 or not digits.isdigit():
        return False
    d = [int(c) for c in digits]
    return sum(d[i] * (7 - i) for i in range(6)) % 10 == d[6]


def normalise_imo(imo, *, require_checksum: bool = True) -> str | None:
    """IMO is 7 digits, sometimes prefixed 'IMO'. Return the bare digits.

    A value that is not 7 digits, or that fails its check digit, is rejected
    rather than matched loosely — a wrong IMO match would be reported at 0.95
    confidence, which is exactly the kind of confident error that is worse than
    no answer at all.

    `require_checksum=False` exists only so the review tool can count how many
    values would have been rejected; the matcher always validates.
    """
    if imo is None:
        return None
    digits = re.sub(r"\D", "", str(imo))
    if len(digits) != 7:
        return None
    if require_checksum and not imo_checksum_ok(digits):
        return None
    return digits


#: A 7-digit number anchored to the literal token "IMO". Anchoring is the whole
#: point: sanctions free text is full of 7-digit passport, registration and
#: licence numbers, and a bare 7-digit grab would match those. Combined with the
#: check digit (which rejects 90.3% of random 7-digit strings) this is the same
#: two-independent-checks standard `review_matches.py` verified 98 of 98 against.
_IMO_IN_TEXT = re.compile(r"\bIMO\b[^0-9]{0,12}(\d{7})", re.I)

#: Tokens that mark a sanctions designation as naming a ship rather than a
#: person or a company. Deliberately narrow — a false positive here promotes a
#: company name into the vessel name-matching pool, which is the collision this
#: whole module is built to avoid.
_VESSEL_MARKERS = (
    "vessel", "ship", "tanker", "cargo", "bulk carrier", "motor vessel",
    "flag", "imo number", "imo no", "call sign", "gross tonnage",
    "former name", "deadweight",
)


def extract_imo_from_text(text: str | None) -> str | None:
    """Pull a checksum-valid IMO out of sanctions free text, or return None.

    OFAC writes IMO into `remarks`, the UN into `comments`, and the EU into its
    `identifier` field. None of the three has an IMO column, so all three go
    through here rather than through three near-identical regexes.
    """
    if not text:
        return None
    for m in _IMO_IN_TEXT.finditer(str(text)):
        if imo_checksum_ok(m.group(1)):
            return m.group(1)
    return None


def looks_like_vessel(*texts: str | None) -> bool:
    """Does this designation carry positive evidence that it names a ship?

    Applied to UN and EU rows only, because those lists have no vessel record
    type to filter on. Returning False does **not** discard the designation — it
    only withholds it from *name* matching, which is the tier that collides.
    An IMO match is accepted from any row, vessel-marked or not: an IMO is a
    hull number and nothing else carries one.
    """
    blob = " ".join(str(t) for t in texts if t).lower()
    return any(marker in blob for marker in _VESSEL_MARKERS)


# --------------------------------------------------------------------------
# loading the two sides
# --------------------------------------------------------------------------

def _latest_as_of(con, snapshot_source_id: str) -> datetime | None:
    row = con.execute(
        "SELECT max(as_of) FROM registry_snapshots WHERE source_id = ?",
        [snapshot_source_id],
    ).fetchone()
    return row[0] if row and row[0] is not None else None


def _designation(*, registry: str, ref, name, program, as_of,
                 call_sign=None, vessel_type=None, tonnage=None,
                 gross_tonnage=None, flag=None, owner=None, imo=None,
                 name_matchable: bool = True) -> dict:
    """One sanctioned-hull candidate, normalised across the three registries.

    The `ofac_*` keys are kept as the landed column names for every registry —
    they are the schema six downstream readers already bind to, and renaming
    them to `sanction_*` would break the graph populator, the review tool, the
    rename/gap analytic and the API panel at once for no gain. `registry` is the
    field that says which list a row actually came from, and it is landed
    alongside so nothing has to infer it from the column name.
    """
    return {
        "registry": registry,
        "ofac_ent_num": str(ref) if ref is not None else None,
        "ofac_name": name,
        "ofac_program": program,
        "ofac_call_sign": call_sign,
        "ofac_vessel_type": vessel_type,
        "ofac_tonnage": tonnage,
        "ofac_gross_tonnage": gross_tonnage,
        "ofac_flag": flag,
        "ofac_owner": owner,
        "ofac_imo": imo,
        "sanctions_as_of": as_of,
        "_name_key": normalise_name(name) if name_matchable else None,
        "_cs_key": normalise_call_sign(call_sign),
    }


def load_ofac_vessels(con, as_of: datetime | None = None) -> list[dict]:
    """The sanctioned-hull side: OFAC SDN rows where sdn_type is 'vessel'.

    Uses the latest snapshot unless `as_of` is given. Snapshots are versioned
    (see registries.py), so asking "was this vessel sanctioned on the date of
    the event?" stays answerable — that is what the versioning is for.
    """
    if as_of is None:
        as_of = _latest_as_of(con, "ofac-sdn")
        if as_of is None:
            return []

    rows = con.execute(
        """
        SELECT ent_num, name, program, call_sign, vessel_type, tonnage,
               gross_tonnage, vessel_flag, vessel_owner, remarks, as_of
        FROM ofac_sdn
        WHERE lower(coalesce(sdn_type,'')) = 'vessel'
          AND as_of = ?
        """,
        [as_of],
    ).fetchall()

    out = []
    for r in rows:
        # OFAC does not have an IMO column; when present it is written into the
        # free-text remarks, e.g. "Vessel Registration Identification IMO 9123456".
        out.append(_designation(
            registry="OFAC", ref=r[0], name=r[1], program=r[2], as_of=r[10],
            call_sign=r[3], vessel_type=r[4], tonnage=r[5], gross_tonnage=r[6],
            flag=r[7], owner=r[8], imo=extract_imo_from_text(r[9]),
        ))
    return out


def load_un_vessels(con, as_of: datetime | None = None) -> list[dict]:
    """The UN consolidated list, restricted to rows that can name a hull.

    UN rows are individuals and entities; there is no vessel type. Individuals
    are dropped outright — a person is not a ship, and a vessel name matching a
    person's name is pure collision. Entities are kept, but a UN entity is
    admitted to *name* matching only when its text carries a vessel marker.
    Every entity remains eligible for an IMO match.
    """
    if as_of is None:
        as_of = _latest_as_of(con, "un-consolidated")
        if as_of is None:
            return []
    try:
        rows = con.execute(
            """
            SELECT data_id, name, un_list_type, comments, as_of
            FROM un_consolidated
            WHERE lower(coalesce(entity_kind,'')) = 'entity'
              AND as_of = ?
            """,
            [as_of],
        ).fetchall()
    except Exception:
        # The table only exists once `ingest registries --only un` has run.
        return []

    out = []
    for data_id, name, list_type, comments, snap in rows:
        imo = extract_imo_from_text(comments) or extract_imo_from_text(name)
        vesselish = looks_like_vessel(name, comments)
        if not imo and not vesselish:
            continue
        out.append(_designation(
            registry="UN", ref=data_id, name=name,
            program=list_type, as_of=snap, imo=imo,
            name_matchable=vesselish,
        ))
    return out


def load_eu_vessels(con, as_of: datetime | None = None) -> list[dict]:
    """The EU consolidated list, restricted the same way as the UN list.

    EU rows carry `identifier`, which holds a birthdate for a person and a
    registration number for an entity — sometimes an IMO, keyword-anchored the
    same way. There is no entity/individual split to filter on here, so the
    vessel-marker test does all the work.
    """
    if as_of is None:
        as_of = _latest_as_of(con, "eu-consolidated")
        if as_of is None:
            return []
    try:
        rows = con.execute(
            """
            SELECT logical_id, name, programme, identifier, as_of
            FROM eu_consolidated
            WHERE as_of = ?
            """,
            [as_of],
        ).fetchall()
    except Exception:
        return []

    out = []
    for logical_id, name, programme, identifier, snap in rows:
        imo = extract_imo_from_text(identifier) or extract_imo_from_text(name)
        vesselish = looks_like_vessel(name, identifier, programme)
        if not imo and not vesselish:
            continue
        out.append(_designation(
            registry="EU", ref=logical_id, name=name,
            program=programme, as_of=snap, imo=imo,
            name_matchable=vesselish,
        ))
    return out


def load_sanctioned_vessels(con, as_of: datetime | None = None,
                            registries: tuple[str, ...] = REGISTRY_ORDER
                            ) -> list[dict]:
    """Every sanctioned-hull candidate across the requested registries.

    `as_of` pins all three to the same date when given. Left None — the normal
    case — each registry uses its own latest snapshot, because the three lists
    are published on independent schedules and forcing a shared date would
    silently drop whichever one refreshed least recently.
    """
    loaders = {"OFAC": load_ofac_vessels, "UN": load_un_vessels,
               "EU": load_eu_vessels}
    out: list[dict] = []
    for reg in registries:
        loader = loaders.get(reg)
        if loader is None:
            continue
        rows = loader(con, as_of)
        if rows:
            n_imo = sum(1 for r in rows if r["ofac_imo"])
            n_name = sum(1 for r in rows if r["_name_key"])
            print(f"[sanctions-match] {reg}: {len(rows):,} designation(s) "
                  f"({n_imo:,} with an IMO, {n_name:,} name-matchable)")
        out += rows
    return out


def load_identified_vessels() -> list[dict]:
    """Our side: every vessel we have identity for, from the GFW pull."""
    seen: dict[str, dict] = {}
    for row in read_table("gfw_vessel_identity"):
        vid = row.get("vessel_id")
        if not vid:
            continue
        # One vessel can have several identity intervals; keep each distinct
        # (name, call sign, imo) combination, because a match against a FORMER
        # identity is still a match — that is the point of identity history.
        key = f"{vid}|{row.get('ship_name')}|{row.get('call_sign')}|{row.get('imo')}"
        seen.setdefault(key, {
            "vessel_id": vid,
            "mmsi": row.get("mmsi"),
            "imo": row.get("imo"),
            "ship_name": row.get("ship_name"),
            "call_sign": row.get("call_sign"),
            "flag": row.get("flag"),
            "record_kind": row.get("record_kind"),
            "valid_from": row.get("valid_from"),
            "valid_to": row.get("valid_to"),
        })
    return list(seen.values())


# --------------------------------------------------------------------------
# matching
# --------------------------------------------------------------------------

def match_one(vessel: dict, ofac_by_imo: dict, ofac_by_cs: dict,
              ofac_by_name: dict) -> tuple[dict, str] | None:
    """Return (ofac_row, tier) for the strongest match, or None.

    Precedence is checked in order and the first hit wins. It never falls
    through to a weaker tier after a stronger one matched — a name agreement
    adds nothing to an IMO match and must not inflate its confidence.

    The one place tiers *do* combine is the call sign. A call-sign hit is
    checked against the name on the same OFAC row: agreement promotes it to
    `call_sign_name` (a finding), disagreement or a missing name on either side
    leaves it at `call_sign` (a candidate). Absence of a name is not evidence
    against, so it does not demote further — it just fails to promote.
    """
    imo = normalise_imo(vessel.get("imo"))
    if imo and imo in ofac_by_imo:
        return ofac_by_imo[imo], "imo"

    nk = normalise_name(vessel.get("ship_name"))

    cs = normalise_call_sign(vessel.get("call_sign"))
    if cs and cs in ofac_by_cs:
        row = ofac_by_cs[cs]
        corroborated = bool(nk) and nk == row.get("_name_key")
        return row, "call_sign_name" if corroborated else "call_sign"

    if nk and nk in ofac_by_name:
        return ofac_by_name[nk], "name"

    return None


def build_indexes(ofac: list[dict]) -> tuple[dict, dict, dict]:
    """Index the sanctioned side once. Ambiguous name keys are DROPPED.

    If two different sanctioned entities normalise to the same name, a name
    match cannot identify which — so the key is removed rather than resolved
    arbitrarily. Silently picking one would attach a specific entity, program
    and owner to a vessel on no evidence.
    """
    by_imo: dict[str, dict] = {}
    by_cs: dict[str, dict] = {}
    name_counts: dict[str, int] = {}
    by_name: dict[str, dict] = {}

    for r in ofac:
        if r["ofac_imo"]:
            by_imo.setdefault(r["ofac_imo"], r)
        if r["_cs_key"]:
            by_cs.setdefault(r["_cs_key"], r)
        if r["_name_key"]:
            name_counts[r["_name_key"]] = name_counts.get(r["_name_key"], 0) + 1
            by_name.setdefault(r["_name_key"], r)

    ambiguous = {k for k, n in name_counts.items() if n > 1}
    for k in ambiguous:
        by_name.pop(k, None)
    if ambiguous:
        print(f"[sanctions-match] dropped {len(ambiguous)} ambiguous name key(s) — "
              "two or more sanctioned entities share them, so a name match "
              "cannot identify which")
    return by_imo, by_cs, by_name


def _match_rows_for_registry(vessels: list[dict], designations: list[dict],
                             registry: str) -> tuple[list[dict], dict[str, int]]:
    """Match every identity record against one registry's designations.

    Indexes are built **per registry**, not over the union of all three. Two
    reasons, and the first is the important one:

    1. It keeps OFAC's result identical to what it was before UN and EU were
       added. A shared name index would let a UN entity make an OFAC name key
       "ambiguous" and drop it, silently changing a published number (126
       matches, 98 by IMO) for a reason that has nothing to do with OFAC.
    2. Two lists designating the same hull is **corroboration, not ambiguity**.
       It should land as two rows an analyst can see agree, not collapse to one.
    """
    by_imo, by_cs, by_name = build_indexes(designations)
    print(f"[sanctions-match] {registry}: index sizes — IMO {len(by_imo):,}, "
          f"call sign {len(by_cs):,}, name {len(by_name):,}")

    rows: list[dict] = []
    tiers = {t: 0 for t in TIER_ORDER}
    source_id = SOURCE_ID_BY_REGISTRY.get(registry, SOURCE_ID)

    for v in vessels:
        hit = match_one(v, by_imo, by_cs, by_name)
        if hit is None:
            continue
        des, tier = hit
        tiers[tier] += 1
        conf = TIER_CONFIDENCE[tier]

        row = {
            "vessel_id": v["vessel_id"],
            "mmsi": v.get("mmsi"),
            "imo": v.get("imo"),
            "ship_name": v.get("ship_name"),
            "call_sign": v.get("call_sign"),
            "flag": v.get("flag"),
            # The API panel and the findings screen read `vessel_*`; the older
            # `ship_name`/`flag`/`imo` spellings are kept because the graph
            # populator and the review tool bind to them. Writing both is how
            # the real matcher's output stops diverging from the scenario
            # generator's, which wrote only `vessel_*` — the reason the
            # sanctions panel rendered blank vessel fields on the real corpus.
            "vessel_name": v.get("ship_name"),
            "vessel_flag": v.get("flag"),
            "vessel_imo": normalise_imo(v.get("imo")) or v.get("imo"),
            "identity_record_kind": v.get("record_kind"),
            "identity_valid_from": v.get("valid_from"),
            "identity_valid_to": v.get("valid_to"),
            "match_tier": tier,
            # An explicit boolean beats forcing every reader to remember the
            # threshold, and makes "candidate" impossible to overlook.
            "is_finding": conf >= FINDING_THRESHOLD,
            "registry": registry,
            # What kind of thing the sanctions list actually designated, so a
            # reader knows what `ofac_name` holds. This matcher only ever loads
            # rows that name a hull, so it is always "vessel"; the scenario
            # generator writes "organisation" for a match reached through
            # ownership. Comparing our vessel's name against a company name is
            # not a name disagreement, and without this column it reads as one.
            "listed_entity_type": "vessel",
            "ofac_ent_num": des["ofac_ent_num"],
            "ofac_name": des["ofac_name"],
            "ofac_program": des["ofac_program"],
            "ofac_call_sign": des["ofac_call_sign"],
            "ofac_vessel_type": des["ofac_vessel_type"],
            "ofac_flag": des["ofac_flag"],
            # The organisation side of the chain, straight from the list.
            "ofac_owner": des["ofac_owner"],
            "ofac_imo": des["ofac_imo"],
            "sanctions_as_of": des["sanctions_as_of"],
        }
        matched_at = des["sanctions_as_of"] or datetime.now(timezone.utc)
        if isinstance(matched_at, str):
            matched_at = datetime.now(timezone.utc)
        if matched_at.tzinfo is None:
            matched_at = matched_at.replace(tzinfo=timezone.utc)
        stamp_envelope(
            row, source_id=source_id,
            source_ref=f"{v['vessel_id']}:{registry}:{des['ofac_ent_num']}:{tier}",
            acquired_at=matched_at, confidence=conf,
        )
        rows.append(row)
    return rows, tiers


def run(as_of: datetime | None = None,
        registries: tuple[str, ...] = REGISTRY_ORDER) -> int:
    """Match identified vessels against sanctioned hulls and land the results."""
    vessels = load_identified_vessels()
    if not vessels:
        print("[sanctions-match] no vessel identity landed. "
              f"Run `{CLI} ingest gfw-vessels` first.")
        return 0

    con = connect()
    designations = load_sanctioned_vessels(con, as_of, registries)
    if not designations:
        print("[sanctions-match] no sanctioned vessel rows found in any of "
              f"{', '.join(registries)}. "
              f"Run `{CLI} ingest registries` first.")
        return 0

    print(f"[sanctions-match] {len(vessels):,} identity records vs "
          f"{len(designations):,} designation(s) across "
          f"{len({d['registry'] for d in designations})} registr(ies)")

    rows: list[dict] = []
    tiers = {t: 0 for t in TIER_ORDER}
    per_registry: dict[str, int] = {}
    for reg in registries:
        subset = [d for d in designations if d["registry"] == reg]
        if not subset:
            continue
        reg_rows, reg_tiers = _match_rows_for_registry(vessels, subset, reg)
        rows += reg_rows
        per_registry[reg] = len(reg_rows)
        for t, n in reg_tiers.items():
            tiers[t] += n

    if not rows:
        print("[sanctions-match] no matches. That is a real result, not a "
              "failure — the AOI vessel population and the designated-vessel "
              "lists may simply not overlap in this window.")
        return 0

    # `registry` joins the natural key. Without it, an OFAC row and a UN row for
    # the same vessel and the same tier would collide on re-landing and one
    # would silently overwrite the other — losing exactly the corroboration
    # that matching three lists is for.
    written = land_table(rows, table=MATCH_TABLE,
                         key_fields=("vessel_id", "registry", "ofac_ent_num",
                                     "match_tier"),
                         day_field="sanctions_as_of")

    # Report what LANDED, not what we built. The natural key collapses one
    # vessel matching one entity via both its registry AND self-reported
    # identity records to a single row. On the first live run that gap was 173
    # built vs 127 landed — printing the pre-merge count overstated it by 36%.
    report_landed("sanctions-match", MATCH_TABLE, written, len(rows), noun="match")

    problems = check_coverage(MATCH_TABLE, rows)
    for p in problems:
        print(f"[sanctions-match] COVERAGE FAILURE: {p}")

    findings = sum(1 for r in rows if r["is_finding"])
    print(f"[sanctions-match]   {len({r['vessel_id'] for r in rows})} distinct "
          f"vessel(s), {len({(r['registry'], r['ofac_ent_num']) for r in rows})} "
          "distinct sanctioned entit(ies)")
    print("[sanctions-match]   by registry: " + "   ".join(
        f"{reg}: {n}" for reg, n in per_registry.items()))
    print("[sanctions-match]   by tier: " + "   ".join(
        f"{t}: {tiers[t]}" for t in TIER_ORDER))
    print(f"[sanctions-match]   {findings} finding(s), {len(rows) - findings} "
          "candidate(s) needing review")

    # A hull designated by more than one list is the strongest corroboration
    # this module can produce, and it is worth naming rather than leaving an
    # analyst to notice two rows sitting next to each other.
    by_vessel: dict[str, set[str]] = {}
    for r in rows:
        by_vessel.setdefault(r["vessel_id"], set()).add(r["registry"])
    corroborated = {v: regs for v, regs in by_vessel.items() if len(regs) > 1}
    if corroborated:
        print(f"[sanctions-match]   {len(corroborated)} vessel(s) designated by "
              "MORE THAN ONE registry — independent lists agreeing on the same "
              "hull is the strongest corroboration available here")

    if tiers["name"]:
        print("[sanctions-match]   NOTE: name-only matches are CANDIDATES, not "
              "findings. Vessel names change and collide; treat them as leads "
              "to verify.")
    if tiers["call_sign"]:
        print("[sanctions-match]   NOTE: call-sign-only matches are CANDIDATES. "
              "Call signs are reassigned and short ones collide; only call sign "
              "WITH name agreement is a finding.")
    if any(r["registry"] in ("UN", "EU") for r in rows):
        print("[sanctions-match]   NOTE: UN and EU have no vessel record type. "
              "Their matches come from an IMO extracted from free text, or from "
              "a designation whose text positively marks it as a vessel. "
              "Neither list supplies a call sign, so no UN/EU row can reach the "
              "call_sign_name tier.")
    return 0
