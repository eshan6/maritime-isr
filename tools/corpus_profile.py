"""Measure the real landed corpus into a small, committable profile.

**Run this on the machine that holds the data.** It reads the landed conformed
tables, derives the distributions the scenario generator needs, and writes
`data_profiles/real_corpus_profile.json` — quantiles and counts, not rows. The
output is on the order of a hundred kilobytes and contains no narrative content:
durations, dimensions, flag frequencies, and the identifier lists the collision
guard needs to prove no synthetic hull wears a real number.

    python tools/corpus_profile.py

*Success* looks like a table of distributions each with a non-zero row count,
ending in `wrote data_profiles/real_corpus_profile.json`.

*Failure* looks like `no landed tables found` — that means the connectors have
not run on this machine, or `MISR_DATA_ROOT` points somewhere else.

**A distribution backed by zero rows is not written.** The generator treats a
missing distribution as "fall back to a published prior and say so", which is
the honest outcome; writing an empty quantile map would let a fallback
masquerade as a measurement. Every distribution that *is* written carries the
count behind it, and that count travels all the way into the generation report.

**Why quantiles rather than the raw values.** Five numbers preserve the shape of
a distribution including its right tail, which is where the interesting
behaviour lives — loitering durations are not symmetric, and a mean and standard
deviation would erase exactly the part a scenario needs to reproduce.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from maritime_isr.config import PIPELINE_VERSION, repo_root          # noqa: E402
from maritime_isr.ingest.landing import read_table                   # noqa: E402
from maritime_isr.scenario.profile import CLASS_PRIORS               # noqa: E402

OUT_PATH = repo_root() / "data_profiles" / "real_corpus_profile.json"

QUANTILES = (0.05, 0.25, 0.50, 0.75, 0.95)

_DAY = timedelta(days=1)

#: Filenames the events connector lands raw under: `<kind>_<from>_<to>.json`.
_WINDOW_RE = __import__("re").compile(r"_(\d{8})_(\d{8})\.json$")


def query_window() -> tuple[datetime, datetime] | None:
    """The window the connector actually asked GFW for, from the raw filenames.

    **This is needed to measure a duration honestly, and its absence is why the
    port-call figure was wrong.** The connector requests events *overlapping* an
    eight-week window. A port visit lasting fourteen years overlaps every
    possible window; one lasting twelve hours only overlaps if it falls inside.
    So an overlap query over-samples long events **in direct proportion to their
    length**, and the durations in the landed table are not the distribution of
    port calls — they are that distribution multiplied by duration.

    That is arithmetic, not a hypothesis about GFW. Any quantile taken over the
    whole table inherits it, which is how `port_call_dwell_hours` came out with
    a p95 of 2.3 years while every visit in it was structurally sound.

    Restricting to visits fully inside the window removes the bias exactly, at a
    known cost: genuine long stays are excluded, so the result understates the
    real tail. That trade is the right way round here — the figure feeds a
    generator that must not put a background vessel alongside for two years —
    and both numbers are written to the profile so the difference is visible
    rather than argued about.
    """
    from maritime_isr.config import cfg
    root = cfg.data_root / "raw" / "gfw-events"
    if not root.exists():
        return None
    lo = hi = None
    for p in root.glob("day=*/*.json"):
        m = _WINDOW_RE.search(p.name)
        if not m:
            continue
        a = datetime.strptime(m.group(1), "%Y%m%d").replace(tzinfo=timezone.utc)
        b = datetime.strptime(m.group(2), "%Y%m%d").replace(tzinfo=timezone.utc)
        lo = a if lo is None else min(lo, a)
        hi = b if hi is None else max(hi, b)
    return (lo, hi) if lo and hi else None


def _as_dt(v):
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, str) and len(v) >= 10:
        try:
            d = datetime.fromisoformat(v.replace("Z", "+00:00"))
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None

#: How GFW's free-text `vessel_type` maps onto the classes the generator builds.
#: GFW's taxonomy is coarse (CARGO, FISHING, TANKER...), so this cannot recover a
#: VLCC/Suezmax split — the tanker classes share whatever the tanker rows say and
#: the class priors keep them distinguishable. Recorded rather than hidden: the
#: profile marks tanker sub-class dimensions as measured only when the corpus
#: genuinely separates them.
TYPE_MAP = {
    "TANKER": ["product_tanker"],
    "CARGO": ["general_cargo", "bulker"],
    "BULK": ["bulker"],
    "FISHING": ["fishing"],
    "REEFER": ["reefer"],
    "CARRIER": ["reefer"],
    "PASSENGER": [],
    "SUPPORT": [],
}


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def quantiles_of(values: list[float]) -> dict | None:
    """Quantile map, or None when there is nothing to measure."""
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    out = {}
    for q in QUANTILES:
        if len(vals) == 1:
            out[str(q)] = vals[0]
            continue
        pos = q * (len(vals) - 1)
        lo = int(pos)
        hi = min(lo + 1, len(vals) - 1)
        f = pos - lo
        out[str(q)] = vals[lo] + f * (vals[hi] - vals[lo])
    return out


def _read(table: str) -> list[dict]:
    try:
        rows = read_table(table)
    except Exception as exc:                                  # noqa: BLE001
        print(f"  {table:<28} unreadable ({type(exc).__name__}: {exc})")
        return []
    # Real rows only. Once the generator has run, the same tables hold synthetic
    # rows too, and profiling those would make the generator sample from its own
    # output — a feedback loop that would drift further from reality every run.
    real = [r for r in rows if not r.get("is_synthetic")]
    print(f"  {table:<28} {len(real):>8,} real row(s)"
          + (f"  ({len(rows) - len(real):,} synthetic excluded)"
             if len(rows) != len(real) else ""))
    return real


#: Every table the scenario generator writes into. Their schemas decide whether
#: synthetic rows can merge into the same day partitions as real rows.
SHARED_TABLES = ("gfw_vessel_identity", "gfw_encounters", "gfw_loitering",
                 "gfw_port_visits", "gfw_ais_gaps", "sanctioned_vessel_matches",
                 "ais_position")


def dump_schemas() -> dict:
    """Column names, inferred types and null rates for every shared table.

    **This is the compatibility check, and it is the reason the profile matters
    beyond realism.** Scenario rows land into the *same day partitions* as real
    rows (ADR-019), which means `land_table` reads the existing partition,
    merges, and rewrites it through `pa.Table.from_pylist`. If a column holds a
    string in real rows and an int in synthetic ones, Arrow raises and the
    write fails — on a partition containing real data.

    So this reports, per column: the Python types actually present, the null
    rate, and one redacted example. Types let the generator be checked against
    reality; **null rates matter just as much**, because a field that is 95%
    null in the real corpus but always populated in synthetic rows makes the
    two trivially separable, which is its own failure (STATE.md: "null that
    looks populated is the same failure family").

    No vessel names, positions or identifiers are included here — those live in
    the identifiers block, which the collision guard needs. Examples are
    truncated and numeric values are reported as their type only.
    """
    out = {}
    for table in SHARED_TABLES:
        try:
            rows = read_table(table)
        except Exception as exc:                              # noqa: BLE001
            out[table] = dict(error=f"{type(exc).__name__}: {exc}")
            continue
        real = [r for r in rows if not r.get("is_synthetic")]
        if not real:
            out[table] = dict(n_rows=0, note="no real rows on this machine")
            print(f"  {table:<28} no real rows")
            continue

        cols = {}
        keys = set()
        for r in real:
            keys.update(r.keys())
        for k in sorted(keys):
            types = set()
            n_null = 0
            example = None
            for r in real:
                v = r.get(k)
                if v is None:
                    n_null += 1
                    continue
                types.add(type(v).__name__)
                if example is None:
                    # Strings are truncated; everything else reports its type
                    # only, so no identifier or position leaves the machine here.
                    example = (v[:24] if isinstance(v, str) else f"<{type(v).__name__}>")
            cols[k] = dict(types=sorted(types),
                           null_rate=round(n_null / len(real), 4),
                           example=example)
        out[table] = dict(n_rows=len(real), columns=cols)
        print(f"  {table:<28} {len(real):>8,} real row(s), {len(cols)} column(s)")
    return out


def build_profile() -> dict:
    print("reading landed tables:")
    identity = _read("gfw_vessel_identity")
    encounters = _read("gfw_encounters")
    loitering = _read("gfw_loitering")
    port_visits = _read("gfw_port_visits")
    gaps = _read("gfw_ais_gaps")
    matches = _read("sanctioned_vessel_matches")

    if not any((identity, encounters, loitering, port_visits)):
        print("\nno landed tables found — have the connectors run on this "
              "machine, and does MISR_DATA_ROOT point at the data?")
        return {}

    dists: dict[str, dict] = {}

    def add(key: str, values: list, source_table: str) -> None:
        q = quantiles_of([_num(v) for v in values])
        n = len([v for v in values if _num(v) is not None])
        if q and n:
            dists[key] = dict(n_rows=n, source_table=source_table, quantiles=q)
            print(f"  + {key:<32} n={n:<8,} median={q['0.5']:.2f}")
        else:
            print(f"  - {key:<32} no usable rows — generator will use its prior")

    # ---- behavioural durations ----
    add("encounter_duration_hours",
        [r.get("duration_hours") for r in encounters], "gfw_encounters")
    add("loiter_duration_hours",
        [r.get("duration_hours") for r in loitering], "gfw_loitering")
    # **Dwell, measured without the length bias.** See `query_window`: the whole
    # table over-samples long visits in proportion to their length, so the only
    # honest denominator for a *port-call duration* is the set of visits that
    # both began and ended inside the window we asked for. Two other filters
    # apply on top, and all three are reported:
    #
    #   * `dwell_hours` where present — a visit whose entry and exit anchorages
    #     differ is not measuring time at one place (13% of the real corpus).
    #   * the containment filter, which is the one that removes the 2.3 years.
    #
    # The unfiltered span is written separately as `port_visit_span_hours`, so
    # the difference between the two is visible in the profile rather than
    # argued about later.
    win = query_window()
    if win:
        w0, w1 = win
        contained = [r for r in port_visits
                     if (_as_dt(r.get("start_time")) or w0 - _DAY) >= w0
                     and (_as_dt(r.get("end_time")) or w1 + _DAY) <= w1]
        print(f"  query window {w0:%Y-%m-%d}..{w1:%Y-%m-%d}: "
              f"{len(contained):,} of {len(port_visits):,} port visits "
              f"({len(contained) / max(len(port_visits), 1):.1%}) began AND "
              f"ended inside it — the rest are length-biased and excluded from "
              f"the dwell figure")
    else:
        contained = port_visits
        print("  ! no raw filenames to recover the query window from, so the "
              "dwell figure CANNOT be de-biased. It will over-state port-call "
              "duration by however much the overlap query over-sampled long "
              "visits. Keep data/raw/ to fix this.")

    dwell = [r.get("dwell_hours") for r in contained
             if r.get("dwell_hours") is not None]
    if dwell:
        add("port_call_dwell_hours", dwell,
            "gfw_port_visits.dwell_hours, window-contained")
    else:
        if any(r.get("dwell_hours") is not None for r in port_visits):
            print("  ! no window-contained visit carries dwell_hours")
        else:
            print("  ! no row carries dwell_hours — this table predates the "
                  "port-visit structure fields. Run tools/rebuild_conformed.py.")
        add("port_call_dwell_hours",
            [r.get("duration_hours") for r in contained],
            "gfw_port_visits.duration_hours, window-contained")

    add("port_visit_span_hours",
        [r.get("duration_hours") for r in port_visits],
        "gfw_port_visits, UNFILTERED and length-biased")

    # Port calls per vessel over the corpus window. The denominator is vessels
    # that made at least one call — vessels with zero calls are not in this
    # table at all, so including an implicit zero for the whole fleet would
    # measure our query's scope rather than vessel behaviour.
    per_vessel = Counter(r.get("vessel_id") for r in port_visits
                         if r.get("vessel_id"))
    add("port_calls_per_vessel", list(per_vessel.values()), "gfw_port_visits")

    # ---- distance context, useful for placing loiters honestly ----
    add("loiter_distance_from_shore_km",
        [r.get("start_distance_from_shore_km") for r in loitering],
        "gfw_loitering")
    add("encounter_distance_from_shore_km",
        [r.get("start_distance_from_shore_km") for r in encounters],
        "gfw_encounters")
    add("gap_duration_hours",
        [r.get("gap_duration_hours") or r.get("duration_hours") for r in gaps],
        "gfw_ais_gaps")

    # ---- dimensions by class ----
    # One length distribution per class the corpus can speak to. GFW's
    # vessel_type is coarse, so several generator classes may share a source
    # distribution; the class prior still supplies beam, draught and speed,
    # which GFW does not carry at all.
    lengths_by_class: dict[str, list] = defaultdict(list)
    tonnage_by_class: dict[str, list] = defaultdict(list)
    type_by_vessel = {}
    for r in encounters + loitering + port_visits:
        if r.get("vessel_id") and r.get("vessel_type"):
            type_by_vessel[r["vessel_id"]] = str(r["vessel_type"]).upper()

    for r in identity:
        vt = type_by_vessel.get(r.get("vessel_id"), "")
        classes = []
        for token, mapped in TYPE_MAP.items():
            if token in vt:
                classes = mapped
                break
        for c in classes:
            if _num(r.get("length_m")):
                lengths_by_class[c].append(_num(r["length_m"]))
            if _num(r.get("tonnage_gt")):
                tonnage_by_class[c].append(_num(r["tonnage_gt"]))

    for c in sorted(CLASS_PRIORS):
        lens = lengths_by_class.get(c, [])
        tons = tonnage_by_class.get(c, [])
        if not lens:
            print(f"  - class_dims:{c:<22} no corpus rows — prior only")
            continue
        dims = {}
        ql = quantiles_of(lens)
        # (low, typical, high) in the shape the class prior uses, so the
        # generator's sampler needs no special case for a measured class.
        dims["length_m"] = [ql["0.05"], ql["0.5"], ql["0.95"]]
        if tons:
            qt = quantiles_of(tons)
            dims["tonnage_gt"] = [qt["0.05"], qt["0.5"], qt["0.95"]]
        dists[f"class_dims:{c}"] = dict(
            n_rows=len(lens), source_table="gfw_vessel_identity", dims=dims)
        print(f"  + class_dims:{c:<22} n={len(lens):<8,} "
              f"median length={ql['0.5']:.1f} m")

    # ---- port-visit structure ----
    # What fraction of real port visits are dwells, and what the rest are. The
    # generator needs these fractions to emit the same mix, because a synthetic
    # corpus where every visit is a clean dwell is separable from the real one
    # by `WHERE dwell_hours IS NULL` — the null-rate failure family again, from
    # a third direction.
    if port_visits and any(r.get("visit_has_stop") is not None
                           for r in port_visits):
        n = len(port_visits)
        cls = Counter()
        for r in port_visits:
            if r.get("dwell_hours") is not None:
                cls["dwell"] += 1
            elif r.get("visit_has_stop") is False:
                cls["no_stop"] += 1
            elif r.get("visit_anchorages_agree") is False:
                cls["anchorages_differ"] += 1
            else:
                cls["unknown"] += 1
        dists["port_visit_structure"] = dict(
            n_rows=n, source_table="gfw_port_visits",
            fractions={k: v / n for k, v in cls.items()})
        print(f"  + port_visit_structure           n={n:<8,} "
              + ", ".join(f"{k} {v / n:.1%}" for k, v in cls.most_common()))
    else:
        print("  - port_visit_structure           table predates the visit "
              "structure fields — run tools/rebuild_conformed.py")

    # ---- flags ----
    flags = Counter(str(r["flag"]).strip().upper() for r in identity
                    if r.get("flag"))
    if flags:
        total = sum(flags.values())
        dists["flag_distribution"] = dict(
            n_rows=total, source_table="gfw_vessel_identity",
            weights={k: v / total for k, v in flags.most_common(40)})
        print(f"  + flag_distribution              n={total:<8,} "
              f"top={flags.most_common(3)}")

    # ---- identifiers, for the collision guard ----
    real_imos = sorted({str(r["imo"]).strip() for r in identity
                        if r.get("imo") not in (None, "")})
    real_mmsis = sorted({str(r["mmsi"]).strip() for r in identity
                         if r.get("mmsi") not in (None, "")})
    ofac_imos = sorted({str(r["ofac_imo"]).strip() for r in matches
                        if r.get("ofac_imo") not in (None, "")})
    # The full OFAC snapshot is a DuckDB table (`ofac_sdn`), not a conformed
    # parquet table — there is no `sanctions` directory at all. Reading the
    # wrong place gave the collision guard a denominator of 121 (only the IMOs
    # that happened to match) instead of the full ~1,500-vessel list.
    try:
        from maritime_isr.ingest.ofac_lookup import ofac_imos_from_duckdb
        duck = ofac_imos_from_duckdb()
        print(f"  ofac_sdn (duckdb)            {len(duck):>8,} vessel IMO(s)")
        ofac_imos.extend(duck)
    except Exception as exc:                                   # noqa: BLE001
        print(f"  ofac_sdn (duckdb)            unavailable ({exc})")
    ofac_imos = sorted(set(ofac_imos))

    # ---- corpus window ----
    times = []
    for rows, key in ((encounters, "start_time"), (loitering, "start_time"),
                      (port_visits, "start_time"), (gaps, "start_time")):
        for r in rows:
            v = r.get(key)
            if isinstance(v, datetime):
                times.append(v)
            elif isinstance(v, str) and len(v) >= 10:
                times.append(v)
    window = {}
    if times:
        as_str = sorted(str(t) for t in times)
        window = dict(start=as_str[0][:19], end=as_str[-1][:19])

    print("\nschemas of the tables scenario rows will share:")
    schemas = dump_schemas()

    return dict(
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        pipeline_version=PIPELINE_VERSION,
        corpus_window=window,
        schemas=schemas,
        distributions=dists,
        identifiers=dict(real_imos=real_imos, real_mmsis=real_mmsis,
                         ofac_imos=ofac_imos),
    )


def main() -> int:
    prof = build_profile()
    if not prof:
        return 1
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(prof, indent=1, sort_keys=True),
                        encoding="utf-8")
    n_dist = len(prof["distributions"])
    ids = prof["identifiers"]
    size_kb = OUT_PATH.stat().st_size / 1024
    n_sch = sum(1 for v in prof.get("schemas", {}).values() if v.get("n_rows"))
    print(f"\n{n_sch} table schema(s) captured")
    print(f"{n_dist} distribution(s), "
          f"{len(ids['real_imos'])} real IMO(s), "
          f"{len(ids['real_mmsis'])} real MMSI(s), "
          f"{len(ids['ofac_imos'])} OFAC IMO(s)")
    print(f"corpus window: {prof['corpus_window'].get('start')} .. "
          f"{prof['corpus_window'].get('end')}")
    print(f"wrote {OUT_PATH} ({size_kb:.0f} KB)")
    print("\nCommit this file — it is what lets the generator say "
          "'sampled from N real rows' and have it be true.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
