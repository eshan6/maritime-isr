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

from ..config import cfg
from ..db import connect
from .checks import check_coverage, report_landed
from .landing import land_table, read_table, stamp_envelope

SOURCE_ID = "ofac-vessel-match"
MATCH_TABLE = "sanctioned_vessel_matches"

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


# --------------------------------------------------------------------------
# loading the two sides
# --------------------------------------------------------------------------

def load_ofac_vessels(con, as_of: datetime | None = None) -> list[dict]:
    """The sanctioned-hull side: OFAC SDN rows where sdn_type is 'vessel'.

    Uses the latest snapshot unless `as_of` is given. Snapshots are versioned
    (see registries.py), so asking "was this vessel sanctioned on the date of
    the event?" stays answerable — that is what the versioning is for.
    """
    if as_of is None:
        row = con.execute(
            "SELECT max(as_of) FROM registry_snapshots WHERE source_id='ofac-sdn'"
        ).fetchone()
        if not row or row[0] is None:
            return []
        as_of = row[0]

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
        imo = None
        m = re.search(r"IMO\s*(\d{7})", r[9] or "", re.I)
        if m:
            imo = m.group(1)
        out.append({
            "ofac_ent_num": r[0],
            "ofac_name": r[1],
            "ofac_program": r[2],
            "ofac_call_sign": r[3],
            "ofac_vessel_type": r[4],
            "ofac_tonnage": r[5],
            "ofac_gross_tonnage": r[6],
            "ofac_flag": r[7],
            "ofac_owner": r[8],
            "ofac_imo": imo,
            "sanctions_as_of": r[10],
            "_name_key": normalise_name(r[1]),
            "_cs_key": normalise_call_sign(r[3]),
        })
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
        print(f"[ofac-match] dropped {len(ambiguous)} ambiguous name key(s) — "
              "two or more sanctioned entities share them, so a name match "
              "cannot identify which")
    return by_imo, by_cs, by_name


def run(as_of: datetime | None = None) -> int:
    """Match identified vessels against sanctioned hulls and land the results."""
    vessels = load_identified_vessels()
    if not vessels:
        print("[ofac-match] no vessel identity landed. "
              "Run `maritime-isr ingest gfw-vessels` first.")
        return 0

    con = connect()
    ofac = load_ofac_vessels(con, as_of)
    if not ofac:
        print("[ofac-match] no OFAC vessel rows found. "
              "Run `maritime-isr ingest registries --only ofac` first.")
        return 0

    by_imo, by_cs, by_name = build_indexes(ofac)
    print(f"[ofac-match] {len(vessels):,} identity records vs {len(ofac):,} sanctioned "
          f"vessels (IMO index {len(by_imo)}, call sign {len(by_cs)}, name {len(by_name)})")

    rows: list[dict] = []
    tiers = {t: 0 for t in TIER_ORDER}
    for v in vessels:
        hit = match_one(v, by_imo, by_cs, by_name)
        if hit is None:
            continue
        ofac_row, tier = hit
        tiers[tier] += 1
        conf = TIER_CONFIDENCE[tier]

        row = {
            "vessel_id": v["vessel_id"],
            "mmsi": v.get("mmsi"),
            "imo": v.get("imo"),
            "ship_name": v.get("ship_name"),
            "call_sign": v.get("call_sign"),
            "flag": v.get("flag"),
            "identity_record_kind": v.get("record_kind"),
            "identity_valid_from": v.get("valid_from"),
            "identity_valid_to": v.get("valid_to"),
            "match_tier": tier,
            # An explicit boolean beats forcing every reader to remember the
            # threshold, and makes "candidate" impossible to overlook.
            "is_finding": conf >= FINDING_THRESHOLD,
            "ofac_ent_num": ofac_row["ofac_ent_num"],
            "ofac_name": ofac_row["ofac_name"],
            "ofac_program": ofac_row["ofac_program"],
            "ofac_call_sign": ofac_row["ofac_call_sign"],
            "ofac_vessel_type": ofac_row["ofac_vessel_type"],
            "ofac_flag": ofac_row["ofac_flag"],
            # The organisation side of the chain, straight from OFAC.
            "ofac_owner": ofac_row["ofac_owner"],
            "ofac_imo": ofac_row["ofac_imo"],
            "sanctions_as_of": ofac_row["sanctions_as_of"],
        }
        matched_at = ofac_row["sanctions_as_of"] or datetime.now(timezone.utc)
        if isinstance(matched_at, str):
            matched_at = datetime.now(timezone.utc)
        if matched_at.tzinfo is None:
            matched_at = matched_at.replace(tzinfo=timezone.utc)
        stamp_envelope(
            row, source_id=SOURCE_ID,
            source_ref=f"{v['vessel_id']}:{ofac_row['ofac_ent_num']}:{tier}",
            acquired_at=matched_at, confidence=conf,
        )
        rows.append(row)

    if not rows:
        print("[ofac-match] no matches. That is a real result, not a failure — "
              "the AOI vessel population and the SDN vessel list may simply not "
              "overlap in this window.")
        return 0

    written = land_table(rows, table=MATCH_TABLE,
                         key_fields=("vessel_id", "ofac_ent_num", "match_tier"),
                         day_field="sanctions_as_of")

    # Report what LANDED, not what we built. The natural key is
    # (vessel_id, ofac_ent_num, match_tier), so one vessel matching one OFAC
    # entity via both its registry AND self-reported identity records collapses
    # to a single row. On the first live run that gap was 173 built vs 127
    # landed — printing the pre-merge count overstated the result by 36%.
    report_landed("ofac-match", MATCH_TABLE, written, len(rows), noun="match")

    problems = check_coverage(MATCH_TABLE, rows)
    for p in problems:
        print(f"[ofac-match] COVERAGE FAILURE: {p}")

    findings = sum(1 for r in rows if r["is_finding"])
    print(f"[ofac-match]   {len({r['vessel_id'] for r in rows})} distinct vessel(s), "
          f"{len({r['ofac_ent_num'] for r in rows})} distinct sanctioned entit(ies)")
    print("[ofac-match]   by tier: " + "   ".join(
        f"{t}: {tiers[t]}" for t in TIER_ORDER))
    print(f"[ofac-match]   {findings} finding(s), {len(rows) - findings} candidate(s) "
          "needing review")
    if tiers["name"]:
        print("[ofac-match]   NOTE: name-only matches are CANDIDATES, not findings. "
              "Vessel names change and collide; treat them as leads to verify.")
    if tiers["call_sign"]:
        print("[ofac-match]   NOTE: call-sign-only matches are CANDIDATES. Call signs "
              "are reassigned and short ones collide; only call sign WITH name "
              "agreement is a finding.")
    return 0
