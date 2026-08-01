"""Does a name change precede an AIS gap? Asked of real data, answered honestly.

**The hypothesis.** A sanctioned vessel that has changed its name is doing
identity work. A vessel whose AIS goes quiet in a way Global Fishing Watch
assesses as deliberate is doing concealment work. If the same hulls do both,
inside the same window, that is a two-signal pattern found organically rather
than injected — the first thing this system would have produced that a synthetic
test could not.

**What this is not.** It is not proof of a rename. Comparing our landed OFAC
snapshot to GFW's identity record tells us the two sources disagree about a
vessel's name; it does not tell us a rename happened, when it happened, or which
name came first. A registry transition record would tell us that, and we do not
have one. Everything below is therefore a *candidate*, and the script says so at
every step.

    python tools/analytic_rename_gap.py
    python tools/analytic_rename_gap.py --window-days 90
    python tools/analytic_rename_gap.py --csv out.csv

Expect zero. Report zero if it is zero — a null result on 98 vessels over an
8-week window is a finding about the free data, not a failure.
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from maritime_isr.ingest.landing import read_table  # noqa: E402

#: Real rows only, and it must stay that way.
#:
#: Once the scenario generator has run (ADR-019), these tables hold synthetic
#: rows alongside real ones in the same partitions. This tool quotes numbers
#: that go into STATE.md and into external material — "98 vessels matched" —
#: and blending three scenario hulls into that figure would turn a measured
#: finding into a fabricated one. The hard ban is explicit: never blend real
#: and synthetic into a single quoted number.
#:
#: Scenario counts are available separately from
#: `python -m maritime_isr.cli scenario status`.
def real_rows(table: str) -> list[dict]:
    rows = read_table(table)
    syn = sum(1 for r in rows if r.get("is_synthetic"))
    if syn:
        print(f"[real-only] {table}: excluded {syn:,} synthetic row(s); "
              f"{len(rows) - syn:,} real row(s) remain")
    return [r for r in rows if not r.get("is_synthetic")]


from maritime_isr.ingest.sanctions_match import (  # noqa: E402
    MATCH_TABLE, normalise_name,
)

RULE = "=" * 88


def _dt(v) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, str):
        try:
            d = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    return None


def name_mismatches(matches: list[dict]) -> list[dict]:
    """IMO-tier matches where our name and OFAC's name disagree.

    IMO tier only. A name-tier match agrees on the name by construction, and a
    call-sign match without name agreement is too weak to build an identity
    claim on (ADR-018).
    """
    out = []
    for m in matches:
        if m.get("match_tier") != "imo":
            continue
        ours = normalise_name(m.get("ship_name"))
        theirs = normalise_name(m.get("ofac_name"))
        if ours and theirs and ours != theirs:
            out.append(m)
    return out


def split_by_freshness(rows: list[dict], identity_by_vessel: dict[str, list[dict]]
                       ) -> tuple[list[dict], list[dict], list[dict]]:
    """Split name disagreements by which source's record is newer.

    Returns (gfw_newer, ofac_newer, undated).

    - **GFW newer** — GFW carries a name OFAC's snapshot does not. If a rename
      happened, it most likely **postdates the listing**, which is the evasion
      story: get listed, change name.
    - **OFAC newer** — the listing is more recent than anything GFW has. A
      difference here is more likely clerical, or a name that predates the
      designation.

    **This is weak evidence and must be labelled as such.** Comparing two
    snapshot dates infers a direction of change from *when we downloaded
    things*, not from any record of the change itself. A registry transition
    record — "vessel X, formerly Y, effective date Z" — would settle it. Snapshot
    arithmetic cannot.

    **Worse than weak on a single snapshot — structurally biased.** `as_of` is
    the day we *downloaded* the SDN list, not the day OFAC designated the
    vessel; the CSV carries no designation date. A GFW identity interval's
    `valid_from` is when that identity started transmitting, which is always
    before the day we ran the download. So with one snapshot the comparison
    resolves to "OFAC newer" for essentially every row, and the resulting split
    is an artifact of our download schedule rather than a fact about vessels.
    Measured on the first live run: **53 of 53 landed in "OFAC newer", 0 in
    "GFW newer"** — exactly the degenerate outcome this predicts.

    `freshness_is_informative()` detects that condition. The split only starts
    carrying signal once we hold **two or more OFAC snapshots** and can watch a
    name change *between* them, which is what the versioned-snapshot design in
    `registries.py` exists to make possible. It is a matter of waiting, not of
    writing a better query.
    """
    gfw_newer, ofac_newer, undated = [], [], []
    for m in rows:
        ofac_as_of = _dt(m.get("sanctions_as_of"))
        intervals = identity_by_vessel.get(m["vessel_id"], [])
        gfw_latest = max(
            (d for d in (_dt(i.get("valid_from")) for i in intervals) if d),
            default=None)
        if ofac_as_of is None or gfw_latest is None:
            undated.append({**m, "_gfw_latest": gfw_latest,
                            "_ofac_as_of": ofac_as_of})
        elif gfw_latest > ofac_as_of:
            gfw_newer.append({**m, "_gfw_latest": gfw_latest,
                              "_ofac_as_of": ofac_as_of})
        else:
            ofac_newer.append({**m, "_gfw_latest": gfw_latest,
                               "_ofac_as_of": ofac_as_of})
    return gfw_newer, ofac_newer, undated


def freshness_is_informative(matches: list[dict]) -> tuple[bool, str]:
    """Can the freshness split mean anything yet? Returns (usable, reason).

    One OFAC snapshot cannot establish a direction of change — see
    `split_by_freshness`. Saying so is the whole job here: a split that always
    answers the same way is not evidence, and printing its buckets without this
    check would be a number reported over unverified reality.
    """
    dates = {str(m.get("sanctions_as_of"))[:10] for m in matches
             if m.get("sanctions_as_of") is not None}
    if len(dates) <= 1:
        only = next(iter(dates), "unknown")
        return False, (
            f"only ONE OFAC snapshot is landed (as_of {only}). `as_of` is the day "
            "we downloaded the list, not the day OFAC designated the vessel — the "
            "SDN CSV has no designation date. Every GFW identity interval starts "
            "before our download, so this split resolves to 'OFAC newer' by "
            "construction and tells us nothing about renaming."
        )
    return True, f"{len(dates)} OFAC snapshots landed ({', '.join(sorted(dates))})"


def gap_flag_census() -> dict[str, int]:
    """How the intentional-disabling flag is distributed over ALL landed gaps.

    Reporting "0 flagged" without the denominator is the reported-vs-landed bug
    in a new costume: 0 out of 5 gaps and 0 out of 5,000 mean very different
    things, and 0 because the column is entirely null means a third thing again
    — a mapping bug rather than a finding.
    """
    census = {"total": 0, "flagged_true": 0, "explicit_false": 0, "null": 0,
              "with_vessel_id": 0}
    for r in real_rows("gfw_ais_gaps"):
        census["total"] += 1
        if r.get("vessel_id"):
            census["with_vessel_id"] += 1
        v = r.get("gfw_intentional_disabling")
        if v is None:
            census["null"] += 1
        elif v:
            census["flagged_true"] += 1
        else:
            census["explicit_false"] += 1
    return census


def intentional_gaps() -> dict[str, list[dict]]:
    """vessel_id -> gaps GFW flagged as looking like intentional disabling."""
    out: dict[str, list[dict]] = {}
    for r in real_rows("gfw_ais_gaps"):
        if not r.get("gfw_intentional_disabling"):
            continue
        vid = r.get("vessel_id")
        if vid:
            out.setdefault(vid, []).append(r)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window-days", type=int, default=None,
                    help="only count gaps within N days of the OFAC snapshot "
                         "(default: the whole landed window)")
    ap.add_argument("--csv", default=None)
    args = ap.parse_args(argv)

    matches = real_rows(MATCH_TABLE)
    if not matches:
        print("No matches landed. Run: maritime-isr ingest sanctions-match")
        return 1

    identity_by_vessel: dict[str, list[dict]] = {}
    for r in real_rows("gfw_vessel_identity"):
        vid = r.get("vessel_id")
        if vid:
            identity_by_vessel.setdefault(vid, []).append(r)

    print(RULE)
    print("IDENTITY CHANGE, THEN ANOMALY — organic cross-reference on landed data")
    print(RULE)

    imo_rows = [m for m in matches if m.get("match_tier") == "imo"]
    imo_vessels = {m["vessel_id"] for m in imo_rows}
    mism = name_mismatches(matches)
    mism_vessels = {m["vessel_id"] for m in mism}

    print(f"  IMO-tier matches            : {len(imo_rows):,} rows, "
          f"{len(imo_vessels):,} distinct vessels")
    print(f"  ...carrying a DIFFERENT name: {len(mism):,} rows, "
          f"{len(mism_vessels):,} distinct vessels")
    print("  A different name is not a rename. It is our snapshot and GFW's record")
    print("  disagreeing, which has several explanations besides renaming.")

    # ---- freshness split -------------------------------------------------
    gfw_newer, ofac_newer, undated = split_by_freshness(mism, identity_by_vessel)
    print("\n" + RULE)
    print("SPLIT BY RECORD FRESHNESS")
    print(RULE)
    print(f"  GFW record newer than the OFAC snapshot : {len(gfw_newer):>4} row(s), "
          f"{len({m['vessel_id'] for m in gfw_newer}):>4} vessel(s)")
    print("      -> if a rename occurred it likely POSTDATES the listing (evasion).")
    print(f"  OFAC snapshot newer than GFW's record   : {len(ofac_newer):>4} row(s), "
          f"{len({m['vessel_id'] for m in ofac_newer}):>4} vessel(s)")
    print("      -> more likely clerical, or a name predating the designation.")
    print(f"  Undated on one side                     : {len(undated):>4} row(s)")
    usable, reason = freshness_is_informative(matches)
    if not usable:
        print("\n  !! THE SPLIT ABOVE CARRIES NO SIGNAL. Do not read it.")
        print(f"     {reason}")
        print("     A split that can only ever answer one way is not evidence. This")
        print("     becomes informative once a SECOND OFAC snapshot is landed and a")
        print("     name change can be seen happening BETWEEN them — which is what")
        print("     the versioned snapshots in registries.py were built for. It is a")
        print("     matter of waiting for the next refresh, not of a better query.")
        print("     Until then the honest statement is: 53 vessels carry a name that")
        print("     differs from OFAC's, and we cannot say which came first.")
    else:
        print(f"\n  Freshness comparison is usable: {reason}")
        print("  Only the first population is a story, and it is still a weak one:")
        print("  inferring the DIRECTION of a change from snapshot dates is weaker")
        print("  evidence than a registry transition record, which we do not have.")
        print("  Do not upgrade this to 'renamed after being sanctioned'.")

    # ---- the cross-reference --------------------------------------------
    gaps = intentional_gaps()
    print("\n" + RULE)
    print("CROSS-REFERENCE AGAINST GFW-FLAGGED INTENTIONAL AIS GAPS")
    print(RULE)
    n_gap_rows = sum(len(v) for v in gaps.values())
    census = gap_flag_census()
    print(f"  AIS gap rows landed in total          : {census['total']:,}")
    print(f"    flagged intentionalDisabling=true   : {census['flagged_true']:,}")
    print(f"    explicitly false                    : {census['explicit_false']:,}")
    print(f"    no verdict from GFW (null)          : {census['null']:,}")
    print(f"  usable (flagged AND has a vessel id)  : {n_gap_rows:,} "
          f"across {len(gaps):,} vessel(s)")
    if census["total"] == 0:
        print("\n  ZERO gap rows are landed at all. Nothing can cross-reference against")
        print("  an empty table — this is a missing-input condition, not a null result.")
    elif census["flagged_true"] == 0 and census["explicit_false"] == 0:
        print("\n  Every landed gap has a NULL verdict — GFW assessed none of them.")
        print("  With the column entirely null, check the mapping is reading GFW's")
        print("  field before concluding anything about the vessels.")
    elif census["flagged_true"] == 0:
        print(f"\n  GFW assessed {census['explicit_false']:,} gap(s) and flagged none as")
        print("  intentional. The column is populated, so this is GFW's verdict")
        print("  rather than a mapping bug — a real, if small, negative result.")

    hits = []
    for m in mism:
        vid = m["vessel_id"]
        for g in gaps.get(vid, []):
            if args.window_days is not None:
                a, b = _dt(m.get("sanctions_as_of")), _dt(g.get("start_time"))
                if a and b and abs((a - b).days) > args.window_days:
                    continue
            hits.append((m, g))

    print(f"\n  VESSELS THAT ARE BOTH name-mismatched AND gap-flagged: "
          f"{len({m['vessel_id'] for m, _ in hits})}")
    if not hits:
        print("\n  Zero. Reported as zero.")
        if census["flagged_true"] == 0:
            print("  Note WHY it is zero: one side of this cross-reference is empty.")
            print("  With no GFW-flagged gap at all, the intersection was zero before")
            print("  the name-mismatch side was even consulted. That is a weaker kind")
            print("  of null than 'both populations exist and do not overlap', and it")
            print("  should not be reported as though the two had been compared.")
        else:
            print("  With the landed volumes above, this is what the arithmetic")
            print("  predicts: both populations are small and drawn from thousands of")
            print("  vessels. A null result says the free 8-week window does not")
            print("  contain the pattern — not that the pattern does not exist, and")
            print("  not that the query is wrong.")
        print("  The query is worth keeping: it costs nothing to re-run on a wider")
        print("  window or a paid feed, and it is the shape of the real product.")
    else:
        print()
        for m, g in hits:
            print(f"  {str(m.get('ship_name'))[:28]:<30} vessel_id={m['vessel_id']}")
            print(f"      OFAC name   : {m.get('ofac_name')}  "
                  f"[{m.get('ofac_program')}] ent {m.get('ofac_ent_num')}")
            print(f"      IMO         : {m.get('ofac_imo')}  (check-digit validated)")
            print(f"      gap         : {g.get('event_id')}  "
                  f"start {g.get('start_time')}  "
                  f"{g.get('gap_duration_hours')} h")
            print(f"      gap flagged intentional BY GFW, not by us")
            print()
        print("  Read this as: GFW recorded these vessels going quiet in a way GFW")
        print("  assessed as deliberate, and our OFAC match on the same hull carries")
        print("  a different name than the sanctions list. Two independent signals on")
        print("  one hull. It is a lead worth an analyst's hour, not a conclusion.")

    if args.csv:
        out = Path(args.csv)
        cols = ["vessel_id", "ship_name", "ofac_name", "ofac_imo", "ofac_program",
                "sanctions_as_of", "freshness", "gap_event_id", "gap_start",
                "gap_duration_hours"]
        # keyed by the match's natural key — split_by_freshness returns copies,
        # so identity comparison would silently label everything ""
        bucket = {(m["vessel_id"], m["ofac_ent_num"]): lbl for lbl, rows in
                  (("gfw_newer", gfw_newer), ("ofac_newer", ofac_newer),
                   ("undated", undated)) for m in rows}
        with out.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            for m in mism:
                for g in gaps.get(m["vessel_id"], [None]):
                    w.writerow({
                        "vessel_id": m["vessel_id"],
                        "ship_name": m.get("ship_name"),
                        "ofac_name": m.get("ofac_name"),
                        "ofac_imo": m.get("ofac_imo"),
                        "ofac_program": m.get("ofac_program"),
                        "sanctions_as_of": m.get("sanctions_as_of"),
                        "freshness": bucket.get(
                            (m["vessel_id"], m["ofac_ent_num"]), ""),
                        "gap_event_id": (g or {}).get("event_id"),
                        "gap_start": (g or {}).get("start_time"),
                        "gap_duration_hours": (g or {}).get("gap_duration_hours"),
                    })
        print(f"\nwrote {out}")

    print("\n" + RULE)
    print("Neither the vessels nor the gaps here were detected by us. GFW detected")
    print("them and GFW assessed the gaps. We matched hulls against OFAC's list.")
    print(RULE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
