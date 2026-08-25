"""A notification to a vessel — and a refusal when it does not fit.

*"Resolve each notification to a vessel entity, and treat non-resolution as a
finding rather than a failure — a notification that cannot be matched to any
track, or a vessel arriving with no notification at all, are both exactly the
kind of gap the Coast Guard wants surfaced."* — the brief, Area 4.

**This module hedges toward refusing, and the reason is asymmetric cost.** A
notification attached to no hull is a gap on a list somebody reviews. A
notification attached to the *wrong* hull is a false accusation with paperwork
behind it: the arrival-window rule then compares one ship's declared ETA with
another ship's track, and every contradiction it finds is manufactured. So the
matcher moves down a ladder of decreasing certainty and stops at the first rung
that holds:

1. **IMO.** A permanent hull number that survives renaming and reflagging. If
   it matches, nothing else is consulted.
2. **Call sign.** Issued with the flag and changed only when the flag changes.
3. **Name, normalised.** The weakest, because names are typed by hand and
   repeat across hulls — the corpus already contains two unrelated vessels both
   called SAGA, which is why a name match that hits more than one hull resolves
   to none.

**There is no fuzzy name matching, and that is a decision rather than an
omission.** Edit distance would resolve "GRANITE TRUIMPH" and would equally
resolve "GRANITE TRIUMPH II", a different ship. What is done instead is
*normalisation*: strip the prefixes and punctuation a form adds ("M.V.",
"M/V", full stops), collapse whitespace, and compare exactly. That recovers the
mangling a clerk introduces without inventing a similarity threshold nobody can
defend to an analyst.

A transposition survives normalisation and stays unresolved. That is the
correct outcome and it is what the "non-resolution is a finding" clause is for.
"""
from __future__ import annotations

import re
from typing import Optional

__all__ = ["resolve_notification", "normalise_vessel_name", "NAME_PREFIXES",
           "merge_identity_sources"]

#: Prefixes a form puts in front of a name that are not part of it.
NAME_PREFIXES = ("MV", "MT", "MS", "SS", "M V", "M T", "M/V", "M/T", "M.V.",
                 "M.T.", "MOTOR VESSEL", "MOTOR TANKER")


def normalise_vessel_name(name) -> Optional[str]:
    """A name reduced to the letters and digits that identify the hull.

    Strips punctuation and a leading vessel-type prefix, collapses whitespace,
    uppercases. `"M.V. Granite Triumph"` and `"GRANITE TRIUMPH"` become one
    string; `"GRANITE TRUIMPH"` does not, and is meant not to.
    """
    if name in (None, ""):
        return None
    s = re.sub(r"[^A-Za-z0-9 ]+", " ", str(name)).upper()
    s = re.sub(r"\s+", " ", s).strip()
    for prefix in sorted(NAME_PREFIXES, key=len, reverse=True):
        p = re.sub(r"[^A-Z0-9 ]+", " ", prefix).strip()
        if s.startswith(p + " "):
            s = s[len(p) + 1:].strip()
            break
    # A dropped space is the one mangling normalisation can undo safely,
    # because removing every space is a *lossless* transform on both sides: it
    # cannot make two different names collide unless they were already the same
    # letters in the same order.
    return s or None


def _collapsed(name: Optional[str]) -> Optional[str]:
    return name.replace(" ", "") if name else None


def merge_identity_sources(*sources) -> list[dict]:
    """One identity row per hull, from every source that says anything.

    **A hull's identity is not one table's opinion of it.** The registry
    (`gfw_vessel_identity`) is patchy by construction — a third of its rows
    carry no IMO — while the same hull may have broadcast that IMO in an AIS
    message 5 every six hours for a month. A resolver reading only the registry
    cannot use an identifier the system demonstrably holds, drops to the weakest
    rung of the ladder, and reports "no IMO in the form matches a hull we hold"
    about a form whose IMO matches perfectly. That reads as a gap in the
    *paperwork* when it is a gap in one table, and it is the difference between
    a finding and a filing error.

    Merging is union, not override: the first source to state a field wins, and
    a later source only fills what is still empty. Sources are therefore passed
    most-trusted first. Nothing is invented — a hull no source names stays
    unnamed, which is what makes her notifications unresolvable and correctly
    so.
    """
    merged: dict[str, dict] = {}
    for source in sources:
        for row in (source or []):
            vid = row.get("vessel_id")
            if not vid:
                continue
            into = merged.setdefault(vid, {"vessel_id": vid})
            for key in ("imo", "call_sign", "ship_name"):
                value = row.get(key)
                if value not in (None, "") and not into.get(key):
                    into[key] = value
    return list(merged.values())


def resolve_notification(fields: dict, registry) -> tuple[Optional[str], Optional[str], float]:
    """(vessel_id, how, confidence) for a notification's declared identity.

    `registry` is an iterable of dicts with `vessel_id` and any of `imo`,
    `call_sign`, `ship_name` — the shape `gfw_vessel_identity` lands. Passing
    the landed rows rather than a bespoke index keeps this honest: the matcher
    sees exactly what the system holds about a hull, including the nulls.

    Confidence is what the *identifier* is worth, not how sure the string
    comparison is. An IMO match is 0.95 because an IMO identifies a hull; a name
    match is 0.6 because a name identifies a hull only when it happens to be
    unique, and the caller has to be able to tell those apart.
    """
    by_imo: dict[str, set] = {}
    by_call: dict[str, set] = {}
    by_name: dict[str, set] = {}
    for row in registry:
        vid = row.get("vessel_id")
        if not vid:
            continue
        imo = str(row.get("imo") or "").strip()
        if imo:
            by_imo.setdefault(imo, set()).add(vid)
        call = str(row.get("call_sign") or "").strip().upper()
        if call:
            by_call.setdefault(call, set()).add(vid)
        name = _collapsed(normalise_vessel_name(row.get("ship_name")))
        if name:
            by_name.setdefault(name, set()).add(vid)

    imo = (fields.get("imo").value if fields.get("imo") else None)
    if imo:
        hit = by_imo.get(str(imo).strip())
        if hit and len(hit) == 1:
            return next(iter(hit)), "imo", 0.95

    call = (fields.get("call_sign").value if fields.get("call_sign") else None)
    if call:
        hit = by_call.get(str(call).strip().upper())
        if hit and len(hit) == 1:
            return next(iter(hit)), "call_sign", 0.8

    name = (fields.get("vessel_name").value if fields.get("vessel_name")
            else None)
    key = _collapsed(normalise_vessel_name(name))
    if key:
        hit = by_name.get(key)
        if hit and len(hit) == 1:
            return next(iter(hit)), "name", 0.6
        if hit and len(hit) > 1:
            # Two hulls, one name. The corpus contains exactly this (SAGA), and
            # picking either would be a coin flip dressed as an identification.
            return None, "name_ambiguous", 0.0

    return None, None, 0.0
