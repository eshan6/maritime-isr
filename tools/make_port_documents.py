"""Generate the port paperwork, then read it back through the real connector.

Run from the repo root::

    python tools/make_port_documents.py

What it does, in order:

1. Reads the corpus already on disk — **whatever vessels it holds**, no list of
   hulls anywhere in this file — and turns each recorded arrival into a voyage
   a document could be written about.
2. Writes real files: pre-arrival notifications, arrival and departure reports,
   crew lists, cargo manifests and port clearance certificates, on the
   letterheads of Kandla, Mundra, JNPT/Nhava Sheva, Mumbai, New Mangalore and
   Kochi, in six agency house styles, five file formats and four date
   notations — including scanned faxes with no text layer at all.
3. Reads every one of them back with the **existing** connector: the readers,
   the format-blind extractor, the resolver that refuses rather than guesses.
   Nothing here parses a document.
4. Lands the result into `arrival_notification` with the full provenance
   envelope, keyed so a vessel profile can look a hull's paperwork up.
5. Prints what actually happened — per format, per kind, per house — including
   what it failed to read.

**Every figure it prints is on the synthetic suite** (CLAUDE.md §4.6). These
documents were written by this repository. Real Coast Guard attachments will be
worse, and nothing here has ever seen one.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from maritime_isr.config import DATA_ROOT                     # noqa: E402
from maritime_isr.ingest.landing import read_table            # noqa: E402
from maritime_isr.ingest.pans.land import (FIELD_NAMES, TABLE,  # noqa: E402
                                           land_rows, read_inbox)
from maritime_isr.ingest.pans.readers import reader_availability  # noqa: E402
from maritime_isr.ingest.pans.resolve import merge_identity_sources  # noqa: E402
from maritime_isr.ingest.pans.service import (OUTCOMES,       # noqa: E402
                                              paperwork_outcomes, track_fixes)
from maritime_isr.scenario.pans import (DOCUMENTS_DIRNAME,    # noqa: E402
                                        DOCUMENT_KINDS, FORMATS, HOUSE_STYLES,
                                        build_document_specs,
                                        write_notifications)

#: The answer key. **Outside the inbox**, because `read_inbox` reads every
#: `.json` in the directory it is pointed at as a portal payload — an answer key
#: dropped in beside the documents would be ingested as one of them, which is
#: the kind of self-inflicted corpus defect ADR-036 already has a list of.
MANIFEST_NAME = "port_documents.manifest.json"


# --------------------------------------------------------------------------
# the corpus, as voyages a document could be written about
# --------------------------------------------------------------------------

def _voyages() -> list[dict]:
    """Every recorded arrival in the corpus, with the hull's declared identity.

    **Nothing here names a vessel.** The cast is minted elsewhere and grows; a
    document set built from a hardcoded list would describe a corpus rather
    than read one, and would break the day the fleet changed.
    """
    identity: dict[str, dict] = {}
    for row in read_table("gfw_vessel_identity"):
        vid = row.get("vessel_id")
        if not vid:
            continue
        into = identity.setdefault(vid, {"vessel_id": vid})
        for key in ("imo", "call_sign", "ship_name", "flag", "mmsi",
                    "draught_m"):
            value = row.get(key)
            if value not in (None, "") and not into.get(key):
                into[key] = value

    # Draught comes from what she broadcast in AIS message 5, because that is
    # what `check_declared_ballast` compares a declaration against. The registry
    # carries one too and it is a different number — a design figure rather than
    # today's condition — so the broadcast one wins where both exist.
    for row in read_table("ais_voyage"):
        vid = row.get("vessel_id")
        if not vid or row.get("draught_m") in (None, ""):
            continue
        identity.setdefault(vid, {"vessel_id": vid})["draught_m"] = \
            row["draught_m"]

    calls: dict[str, list] = defaultdict(list)
    for row in read_table("gfw_port_visits"):
        vid, start = row.get("vessel_id"), row.get("start_time")
        if not vid or start is None:
            continue
        calls[vid].append(row)
    for rows in calls.values():
        rows.sort(key=lambda r: r["start_time"])

    out: list[dict] = []
    for vid, rows in sorted(calls.items()):
        for i, row in enumerate(rows):
            port = row.get("port_name")
            if not port:
                # A stop with no named port is not an arrival anybody owes
                # paperwork for, and inventing a name for one would author a
                # contradiction the generator itself created.
                continue
            prior = rows[i - 1] if i else None
            ident = identity.get(vid, {})
            out.append(dict(
                vessel_id=vid,
                name=ident.get("ship_name") or "",
                imo=ident.get("imo"),
                call_sign=ident.get("call_sign"),
                flag=ident.get("flag"),
                owner=None,
                arrival_port=port,
                arrival_time=row["start_time"],
                last_port=(prior or {}).get("port_name"),
                prior_call_end=(prior or {}).get("end_time"),
                draught_m=ident.get("draught_m"),
            ))
    return out


def _registry() -> list[dict]:
    """What the system holds about a hull — the union, not one table's opinion."""
    identity = list(read_table("gfw_vessel_identity"))
    merged = merge_identity_sources(
        identity,
        [dict(vessel_id=r.get("vessel_id"), imo=r.get("imo"))
         for r in read_table("ais_voyage")])
    by_id = {r["vessel_id"]: dict(r) for r in merged}
    for row in identity:
        vid = row.get("vessel_id")
        if vid in by_id:
            for key in ("mmsi", "ship_name", "call_sign"):
                if row.get(key) and not by_id[vid].get(key):
                    by_id[vid][key] = row[key]
    return list(by_id.values())


# --------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------

def generate(out_dir: Path, *, seed: int, limit: int | None) -> list:
    voyages = _voyages()
    if limit:
        voyages = voyages[:limit]
    if not voyages:
        print("  no recorded arrivals in the corpus — nothing to write "
              "paperwork about. Generate a corpus first "
              "(python tools/run_scenario_pipeline.py).")
        return []
    taken = {str(r.get("imo")) for r in read_table("gfw_vessel_identity")
             if r.get("imo")}
    specs = build_document_specs(voyages, seed=seed, taken_imos=taken)

    if out_dir.exists():
        # **Raw is immutable, and a stale document is worse than none.** A file
        # left from an earlier corpus names a hull the current one may not hold,
        # which lands as an unmatched-notification finding that is indisputably
        # our own fault. The directory is emptied and rewritten rather than
        # merged into.
        shutil.rmtree(out_dir)
    written = write_notifications(specs, out_dir, seed=seed)
    print(f"  wrote {sum(v for v in written.values() if isinstance(v, int)):,} "
          f"document(s) to {out_dir}")
    print(f"    by format : {dict(sorted((k, v) for k, v in written.items() if isinstance(v, int)))}")
    print(f"    by kind   : {dict(Counter(s.document_kind for s in specs))}")
    print(f"    by house  : {dict(Counter(s.house_style for s in specs))}")
    print(f"    authored  : {dict(Counter(s.authored_case for s in specs))}")
    if written.get("_skipped"):
        print(f"    NOT WRITTEN (missing library): {written['_skipped']}")
    return specs


def _write_manifest(specs, path: Path) -> None:
    """The answer key, beside the inbox and never inside it."""
    path.write_text(json.dumps([
        dict(notification_id=s.notification_id,
             document_name=s.document_name,
             document_format=s.document_format,
             document_kind=s.document_kind,
             house_style=s.house_style,
             port=s.port,
             vessel_entity_id=s.vessel_entity_id,
             authored_case=s.authored_case,
             omitted=list(s.omitted),
             fields_written=sorted(k for k in s.values
                                   if k not in s.omitted
                                   and k in DOCUMENT_KINDS[s.document_kind].fields),
             expected=s.expected)
        for s in specs], indent=1), encoding="utf-8")


def read_back(out_dir: Path, *, land: bool) -> list[dict]:
    avail = reader_availability()
    missing = {k: v for k, v in avail.items() if v != "ok"}
    print(f"  readers        : {len(avail) - len(missing)}/{len(avail)} "
          f"available")
    for name, state in sorted(avail.items()):
        print(f"      {name:<12} {state}")
    registry = _registry()
    # **Read once, land what was read.** `land_inbox` reads the directory
    # itself, so calling it after `read_inbox` OCR'd every scanned fax paid for
    # every page twice and doubled the wall time of the slowest step in the run.
    rows = read_inbox(out_dir, registry, is_synthetic=True)
    if land:
        landed = land_rows(rows)
        print(f"  landed into {TABLE}: "
              f"{sum(v for v in landed.values() if isinstance(v, int)):,} row(s)")
    else:
        print(f"  NOT landed (--no-land); {len(rows):,} row(s) read only")
    return rows


class _ManifestSpec:
    """An answer-key entry, replayed from the manifest instead of regenerated.

    `--read-only` exists because **the documents are raw and raw is immutable**
    (CLAUDE.md §4.2). Re-measuring how well the connector reads a corpus is a
    question about the reader, not about the corpus, and answering it by
    rewriting every file would destroy the thing being measured — and would
    silently fail whenever the vessel corpus is mid-rebuild, since generation
    reads it and the documents do not.

    It carries only what the report and the rule check consult, so a field this
    class forgets is a `AttributeError` at the call site rather than a quietly
    wrong number.
    """

    __slots__ = ("notification_id", "document_name", "document_format",
                 "document_kind", "house_style", "port", "vessel_entity_id",
                 "authored_case", "omitted", "fields_written", "expected")

    def __init__(self, entry: dict):
        for name in self.__slots__:
            setattr(self, name, entry.get(name))
        self.omitted = tuple(self.omitted or ())
        self.fields_written = set(self.fields_written or ())
        self.expected = dict(self.expected or {})


def _specs_from_manifest(path: Path) -> list:
    return [_ManifestSpec(e) for e in
            json.loads(path.read_text(encoding="utf-8"))]


def _authored_fields(spec) -> set:
    """The fields this document was actually written with — the denominator.

    A field omitted on purpose is not a miss, and a field the kind never
    carries (a port clearance has no ETA) cannot be read off a page it is not
    printed on. Both are excluded here so the rate below measures reading
    rather than arithmetic.
    """
    written = getattr(spec, "fields_written", None)
    if written is not None:
        return set(written)
    return {k for k in spec.values
            if k not in spec.omitted
            and k in DOCUMENT_KINDS[spec.document_kind].fields}


def _recovered_fields(row: dict, kind) -> set:
    """The fields the connector actually got a value for, same universe."""
    allowed = DOCUMENT_KINDS[kind].fields if kind in DOCUMENT_KINDS else FIELD_NAMES
    return {name for name in FIELD_NAMES
            if name in allowed and row.get(name) not in (None, "")}


def report(specs, rows: list[dict]) -> dict:
    """What the connector actually got, per format, per kind and per house.

    **The rate is a recall over field *sets*, not a ratio of two counts.**
    It used to divide `fields_read` by the number of fields authored, which
    was wrong twice over and printed rates above 100%: the numerator counted
    every row including the ones with no answer-key entry, while the
    denominator counted only matched rows; and `fields_read` counts any field
    with a value, including fields outside the document kind's own field list,
    so a port clearance that yielded an ETA scored better than a perfect read.
    A "read rate" that can exceed 100% is not a measurement, and this is a
    corpus whose entire purpose is to be measured.

    What is counted now, per document, inside one field universe:
      * **recovered** — authored fields the connector got a value for.
      * **missed**    — authored fields it did not.
      * **spurious**  — fields it produced that the document was not written
                        with. These are not free: a value that came from
                        nowhere is a provenance failure, so they are printed.
    """
    by_id = {s.notification_id: s for s in specs}
    kinds = Counter()
    fmt_docs, fmt_unread = Counter(), Counter()
    fmt_want, fmt_got, fmt_spurious = Counter(), Counter(), Counter()
    house_docs, house_want, house_got = Counter(), Counter(), Counter()
    kind_mismatch = []
    unmatched = 0
    per_field_want, per_field_got = Counter(), Counter()

    for row in rows:
        spec = by_id.get(row.get("notification_id"))
        fmt = row.get("document_format")
        fmt_docs[fmt] += 1
        if row.get("unread_reason"):
            fmt_unread[fmt] += 1
        kinds[row.get("document_kind")] += 1
        if spec is None:
            # Counted and reported, never folded into the rate: a row with no
            # answer-key entry is a corpus defect, not a reading score.
            unmatched += 1
            continue
        want = _authored_fields(spec)
        got = _recovered_fields(row, spec.document_kind)
        hit = want & got
        fmt_want[fmt] += len(want)
        fmt_got[fmt] += len(hit)
        fmt_spurious[fmt] += len(got - want)
        house_docs[spec.house_style] += 1
        house_want[spec.house_style] += len(want)
        house_got[spec.house_style] += len(hit)
        for name in want:
            per_field_want[name] += 1
        for name in hit:
            per_field_got[name] += 1
        if row.get("document_kind") != spec.document_kind:
            kind_mismatch.append((spec.notification_id, spec.document_kind,
                                  row.get("document_kind")))

    def _rate(got: int, want: int) -> str:
        return f"{got / want:.1%}" if want else "n/a"

    print("\n  per format — of the fields each document was written with,")
    print("  how many the connector read back")
    print(f"    {'format':<12}{'docs':>6}{'unread':>8}{'authored':>10}"
          f"{'read':>7}{'missed':>8}{'spurious':>10}{'recall':>9}")
    for fmt in sorted(fmt_docs, key=str):
        want, got = fmt_want[fmt], fmt_got[fmt]
        print(f"    {str(fmt):<12}{fmt_docs[fmt]:>6}{fmt_unread[fmt]:>8}"
              f"{want:>10}{got:>7}{want - got:>8}{fmt_spurious[fmt]:>10}"
              f"{_rate(got, want):>9}")
    t_want, t_got = sum(fmt_want.values()), sum(fmt_got.values())
    print(f"    {'TOTAL':<12}{sum(fmt_docs.values()):>6}"
          f"{sum(fmt_unread.values()):>8}{t_want:>10}{t_got:>7}"
          f"{t_want - t_got:>8}{sum(fmt_spurious.values()):>10}"
          f"{_rate(t_got, t_want):>9}")
    if unmatched:
        print(f"    NOT IN ANSWER KEY: {unmatched} row(s), excluded from the "
              f"rate above")

    print("\n  per house style — six agencies asking the same twelve questions")
    print(f"    {'house':<20}{'docs':>6}{'authored':>10}{'read':>7}{'recall':>9}")
    for house in sorted(house_docs, key=str):
        want, got = house_want[house], house_got[house]
        print(f"    {str(house):<20}{house_docs[house]:>6}{want:>10}{got:>7}"
              f"{_rate(got, want):>9}")

    print("\n  per field — which of the twelve questions is hardest to read")
    print(f"    {'field':<14}{'authored':>10}{'read':>7}{'recall':>9}")
    for name in FIELD_NAMES:
        want, got = per_field_want[name], per_field_got[name]
        if want:
            print(f"    {name:<14}{want:>10}{got:>7}{_rate(got, want):>9}")

    print("\n  document kind, as read off the page")
    for kind, n in sorted(kinds.items(), key=lambda kv: str(kv[0])):
        print(f"    {str(kind):<20}{n:>6}")
    if kind_mismatch:
        print(f"    MISCLASSIFIED: {len(kind_mismatch)} — "
              f"{kind_mismatch[:5]}")

    resolved = Counter(r.get("resolved_by") for r in rows)
    print(f"\n  resolution     : {dict(resolved)}")
    # Did it resolve to the *right* hull? Presence is not correctness, and the
    # answer key is the only thing that can tell them apart.
    right = wrong = unresolved = 0
    for row in rows:
        spec = by_id.get(row.get("notification_id"))
        if spec is None:
            continue
        got = row.get("vessel_id")
        if not got:
            unresolved += 1
        elif got == spec.vessel_entity_id:
            right += 1
        else:
            wrong += 1
    print(f"    against the answer key: {right} correct hull, {wrong} WRONG "
          f"hull, {unresolved} declined to resolve")
    return dict(by_id=by_id, fmt_docs=fmt_docs, fmt_want=fmt_want,
                fmt_got=fmt_got, kinds=kinds, unmatched=unmatched,
                resolve_right=right, resolve_wrong=wrong,
                resolve_declined=unresolved)


def check_outcomes(specs, rows: list[dict]) -> dict:
    """Run the paperwork rules over every document and count the three answers.

    The verdicts come from `anomaly.paperwork` through
    `ingest.pans.service.paperwork_outcomes` — this file owns no rule. What it
    adds is the comparison against the answer key: of the documents authored to
    contradict a track, how many did the rules actually contradict, and of those
    authored to be uncheckable, how many did the rules correctly decline.
    """
    calls: dict[str, list] = defaultdict(list)
    for row in read_table("gfw_port_visits"):
        vid, start = row.get("vessel_id"), row.get("start_time")
        if vid and start is not None:
            calls[vid].append(row)
    for rows_ in calls.values():
        rows_.sort(key=lambda r: r["start_time"])

    # **The column is `ts`, not `timestamp`.** `schemas.AIS_POSITION` names it
    # `ts`; only `ais_voyage` calls it `timestamp`. Reading the wrong one here
    # returned None for every row, emptied the track for every hull, and sent
    # `check_last_port` down its "too little track" branch — so the whole corpus
    # came back `not_checkable` and the documents authored to lie about their
    # last port were scored as unreadable rather than as misses. A silent
    # all-not_checkable is exactly the failure the three-valued outcome exists
    # to make visible, so an empty track is shouted about below.
    #
    # `track_fixes` is the connector's own reader and is columnar: the position
    # table now holds over eight hundred thousand fixes and pulling it in as
    # dicts is what got an earlier process killed for running out of memory.
    fixes = track_fixes()
    if not fixes:
        print("    WARNING: no ais_position fixes were read at all — every "
              "last-port verdict below will be not_checkable for want of a "
              "track, which is a fault in this run and not a finding about "
              "the paperwork.")

    draughts: dict[str, float] = {}
    for row in read_table("ais_voyage"):
        vid, d = row.get("vessel_id"), row.get("draught_m")
        if vid and d not in (None, ""):
            draughts[vid] = float(d)

    by_id = {s.notification_id: s for s in specs}
    totals = Counter()
    per_check: dict[str, Counter] = defaultdict(Counter)
    authored = defaultdict(Counter)

    for row in rows:
        vid = row.get("vessel_id")
        if not vid:
            continue
        vcalls = calls.get(vid, [])
        filed = row.get("received_at")
        prior = [(c["lat"], c["lon"]) for c in vcalls
                 if c.get("lat") is not None and filed is not None
                 and c["start_time"] < filed]
        findings = paperwork_outcomes(
            row, fixes=fixes.get(vid, ()),
            arrivals=[(c["start_time"], c.get("port_name")) for c in vcalls],
            prior_calls=prior, draught_m=draughts.get(vid))
        spec = by_id.get(row.get("notification_id"))
        for f in findings:
            totals[f.outcome] += 1
            per_check[f.check][f.outcome] += 1
            if spec is not None and spec.expected.get(f.check):
                authored[(spec.authored_case, f.check)][
                    "hit" if f.outcome == spec.expected[f.check] else "miss"] += 1

    print("\n  paperwork rules — three answers, never two")
    print(f"    {'check':<28}" + "".join(f"{o:>16}" for o in OUTCOMES))
    for check in sorted(per_check):
        c = per_check[check]
        print(f"    {check:<28}" + "".join(f"{c.get(o, 0):>16}" for o in OUTCOMES))
    print(f"    {'TOTAL':<28}" + "".join(f"{totals.get(o, 0):>16}"
                                         for o in OUTCOMES))

    print("\n  against the answer key — what each authored case was meant to do")
    for (case, check), counts in sorted(authored.items()):
        hit, miss = counts["hit"], counts["miss"]
        n = hit + miss
        print(f"    {case:<24} {check:<26} {hit}/{n} as authored"
              + (f"   ({miss} NOT)" if miss else ""))
    return dict(totals=totals, per_check=per_check, authored=authored)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=None,
                    help="where to write the documents "
                         f"(default: <data root>/{DOCUMENTS_DIRNAME})")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--limit", type=int, default=None,
                    help="only the first N recorded arrivals")
    ap.add_argument("--no-land", action="store_true",
                    help="read the documents but do not write rows into "
                         f"{TABLE}")
    ap.add_argument("--no-checks", action="store_true",
                    help="skip the paperwork rules (they read the whole "
                         "position table and that is the slow part)")
    ap.add_argument("--read-only", action="store_true",
                    help="do NOT write any document. Read the corpus already "
                         "on disk back through the connector and report on it, "
                         "replaying the answer key from the manifest. Use this "
                         "to re-measure the reader without touching raw, and "
                         "when the vessel corpus is unavailable or being "
                         "rebuilt (generation needs it; reading does not).")
    args = ap.parse_args()

    out_dir = Path(args.out) if args.out else DATA_ROOT / DOCUMENTS_DIRNAME
    print(f"Maritime ISR — port paperwork corpus  (seed {args.seed})")
    print(f"  formats {list(FORMATS)}")
    print(f"  kinds   {sorted(DOCUMENT_KINDS)}")
    print(f"  houses  {sorted(HOUSE_STYLES)}\n")

    manifest = out_dir.parent / MANIFEST_NAME
    if args.read_only:
        if not out_dir.exists() or not manifest.exists():
            print(f"  --read-only, but there is no corpus to read: expected "
                  f"documents in {out_dir} and an answer key at {manifest}. "
                  f"Run this tool without --read-only to write them.")
            return
        specs = _specs_from_manifest(manifest)
        n_files = sum(1 for p in out_dir.iterdir() if p.is_file())
        print(f"  READ-ONLY: nothing written. {n_files:,} document(s) already "
              f"in {out_dir},\n    answer key {manifest} ({len(specs):,} entries)")
    else:
        specs = generate(out_dir, seed=args.seed, limit=args.limit)
        if not specs:
            return
        _write_manifest(specs, manifest)
        print(f"    answer key: {manifest} (outside the inbox, deliberately)")

    print("\n  reading them back through the connector")
    rows = read_back(out_dir, land=not args.no_land)
    report(specs, rows)
    if not args.no_checks:
        check_outcomes(specs, rows)

    print("\n  Every figure above is on the synthetic suite. These documents "
          "were\n  written by this repository; no real agency form has ever "
          "been read.")
    print(f"  Run at {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC.")


if __name__ == "__main__":
    main()
