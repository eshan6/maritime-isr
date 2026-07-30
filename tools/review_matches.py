"""Review OFAC vessel matches before treating any of them as a finding.

144 IMO matches is a significant claim. Before it becomes one, three things need
checking, and all three are about **our** possible errors rather than the data:

1. **Distinct hulls vs identity records.** One vessel with several identity
   intervals matches more than once. The headline number must be vessels.
2. **IMO extraction quality.** OFAC has no IMO column — we regex it out of
   free-text remarks. If that regex catches a *former* IMO, a hull number, or
   any other 7-digit string, the match is wrong and reported at 0.95 confidence.
   This prints the remark text so you can see what it matched against.
3. **Name plausibility.** An IMO match with wildly different names is not
   automatically wrong — renaming is exactly what sanctioned vessels do, and
   catching that is the point. But it should be *visible*, not silent.

    python tools/review_matches.py            # summary + IMO evidence
    python tools/review_matches.py --all      # every match
    python tools/review_matches.py --csv out.csv
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from maritime_isr.db import connect  # noqa: E402
from maritime_isr.ingest.landing import read_table  # noqa: E402
from maritime_isr.ingest.sanctions_match import MATCH_TABLE, normalise_name  # noqa: E402


def load_ofac_remarks(con) -> dict[str, str]:
    """ent_num -> remarks, so we can show what the IMO regex actually read."""
    try:
        rows = con.execute(
            "SELECT ent_num, remarks FROM ofac_sdn WHERE remarks IS NOT NULL"
        ).fetchall()
    except Exception:  # noqa: BLE001
        return {}
    return {r[0]: r[1] for r in rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="print every match")
    ap.add_argument("--csv", default=None, help="write all matches to a CSV")
    args = ap.parse_args()

    rows = read_table(MATCH_TABLE)
    if not rows:
        print("No matches landed. Run: maritime-isr ingest sanctions-match")
        return 1

    con = connect()
    remarks = load_ofac_remarks(con)

    by_tier: dict[str, list] = {}
    for r in rows:
        by_tier.setdefault(r["match_tier"], []).append(r)

    imo_rows = by_tier.get("imo", [])
    name_rows = by_tier.get("name", [])
    cs_rows = by_tier.get("call_sign", [])

    print("=" * 84)
    print("OFAC VESSEL MATCH REVIEW")
    print("=" * 84)
    print(f"  match rows (identity records) : {len(rows):,}")
    print(f"  DISTINCT vessels              : {len({r['vessel_id'] for r in rows}):,}"
          "   <-- the honest headline number")
    print(f"  distinct sanctioned entities  : {len({r['ofac_ent_num'] for r in rows}):,}")
    print()
    print(f"  by IMO       {len(imo_rows):>5}   findings   "
          f"({len({r['vessel_id'] for r in imo_rows}):,} distinct vessels)")
    print(f"  by call sign {len(cs_rows):>5}   findings   "
          f"({len({r['vessel_id'] for r in cs_rows}):,} distinct vessels)")
    print(f"  by name only {len(name_rows):>5}   CANDIDATES "
          f"({len({r['vessel_id'] for r in name_rows}):,} distinct vessels)")

    # ---- check 2: what did the IMO regex actually read? -------------------
    print("\n" + "=" * 84)
    print("IMO EVIDENCE — what the regex extracted the IMO from")
    print("=" * 84)
    print("Look for remarks where the 7-digit number is NOT the vessel's current")
    print("IMO — a former IMO, an owner registration, a hull number. Those would")
    print("be false matches reported at 0.95 confidence.\n")

    suspicious = 0
    shown = 0
    for r in imo_rows:
        rem = remarks.get(r["ofac_ent_num"], "") or ""
        hits = re.findall(r"\b\d{7}\b", rem)
        multi = len(set(hits)) > 1
        prefix = re.search(r"(\w+\s+\w+\s+)?IMO\s*\d{7}", rem, re.I)
        context = prefix.group(0) if prefix else "(no 'IMO' keyword found!)"
        flag = ""
        if multi:
            flag = "  <-- MULTIPLE 7-digit numbers in remarks"
            suspicious += 1
        elif not prefix:
            flag = "  <-- IMO not keyword-anchored"
            suspicious += 1
        if flag or args.all or shown < 8:
            print(f"  ent {r['ofac_ent_num']:>6}  IMO {r['ofac_imo']}  "
                  f"read from: {context!r}{flag}")
            shown += 1

    print(f"\n  {suspicious} of {len(imo_rows)} IMO matches have questionable extraction.")
    if suspicious == 0:
        print("  All IMO values were keyword-anchored with a single 7-digit number.")

    # ---- check 3: name plausibility on IMO matches ------------------------
    print("\n" + "=" * 84)
    print("NAME COMPARISON on IMO matches")
    print("=" * 84)
    print("A mismatch here is NOT necessarily an error — renaming is what")
    print("sanctioned vessels do, and detecting it is the point. But it should")
    print("be visible.\n")

    agree = differ = 0
    examples = []
    for r in imo_rows:
        ours = normalise_name(r.get("ship_name"))
        theirs = normalise_name(r.get("ofac_name"))
        if ours and theirs:
            if ours == theirs:
                agree += 1
            else:
                differ += 1
                if len(examples) < 12:
                    examples.append((r.get("ship_name"), r.get("ofac_name"),
                                     r.get("flag"), r.get("ofac_flag")))
    print(f"  names agree : {agree}")
    print(f"  names differ: {differ}   (candidate rename/reflag events)")
    if examples:
        print(f"\n  {'our name':<26} {'OFAC name':<26} {'our flag':<10} OFAC flag")
        print("  " + "-" * 78)
        for ours, theirs, f1, f2 in examples:
            print(f"  {str(ours)[:25]:<26} {str(theirs)[:25]:<26} "
                  f"{str(f1)[:9]:<10} {f2}")

    # ---- name-only candidates --------------------------------------------
    if name_rows:
        print("\n" + "=" * 84)
        print(f"NAME-ONLY CANDIDATES ({len(name_rows)}) — verify before believing")
        print("=" * 84)
        for r in name_rows[: (None if args.all else 15)]:
            print(f"  {str(r.get('ship_name'))[:28]:<30} mmsi={r.get('mmsi')}  "
                  f"-> OFAC {r['ofac_ent_num']} {str(r['ofac_name'])[:28]} "
                  f"[{r.get('ofac_program')}]")
        if not args.all and len(name_rows) > 15:
            print(f"  ... {len(name_rows) - 15} more (use --all)")

    if args.csv:
        out = Path(args.csv)
        cols = sorted({k for r in rows for k in r})
        with out.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k) for k in cols})
        print(f"\nwrote {len(rows)} rows -> {out}")

    print("\n" + "=" * 84)
    print("Until the IMO evidence above is reviewed, the honest statement is:")
    print(f"  '{len({r['vessel_id'] for r in imo_rows})} vessels in the AOI event data match an "
          "OFAC-sanctioned hull by IMO,")
    print("   pending verification of the IMO extraction.'")
    print("NOT '144 sanctioned vessels detected'.")
    print("=" * 84)
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
