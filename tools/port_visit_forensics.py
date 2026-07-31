"""Why are GFW port visits years long? Read the raw payloads and find out.

    python tools/port_visit_forensics.py

Reads `data/raw/gfw-events/` only. Writes nothing, touches no conformed table,
uses no network.

**Why this exists.** The first explanation was wrong and the data said so. The
theory was that the multi-year spans came from visits GFW had stitched together
without observing a stop — so `dwell_hours` would be populated only on
structurally sound visits and the tail would fall away. The rebuild's audit
measured it: **100% of the 3,000 real port visits have an observed stop, 87%
have entry and exit at the same anchorage, and the "clean dwell" p95 is still
827 days.** Structure does not explain the tail.

So stop theorising and ask the payloads four questions:

1. **Is the pull truncated?** The corpus holds exactly 3,000 port visits. Round
   numbers in a count nobody chose are result caps, and a capped pull is a
   biased sample of an unknown population — every distribution measured from it
   inherits that bias.

2. **Does GFW's own `durationHours` agree with `end - start`?** If it does, the
   spans are what GFW means to report and the question moves upstream. If it
   does not, we are computing a duration GFW would not recognise, and which of
   the two is right decides what `duration_hours` should even be.

3. **Is this length-biased sampling?** This is the explanation that needs no bug
   anywhere. The connector asks for events *overlapping* an eight-week window. A
   visit lasting fourteen years overlaps every possible window; a visit lasting
   twelve hours only overlaps if it happens to fall inside. So an overlap query
   over-samples long events in direct proportion to their length, and the
   observed duration distribution is **not** the distribution of port-call
   durations — it is that distribution multiplied by duration. Restricting to
   visits *fully contained* in the window removes the bias, at the cost of
   discarding the true long stays. If the contained subset looks like ordinary
   port calls, the tail was a sampling artefact all along and the profiler, not
   the ingest, is what needs fixing.

4. **What do the extreme records actually contain?** Printed verbatim. Every
   inference in this area so far has come from reasoning about a field's meaning
   rather than looking at it, and that is exactly how the first explanation went
   wrong.
"""
from __future__ import annotations

import json
import re
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from maritime_isr.config import cfg                              # noqa: E402
from maritime_isr.ingest.gfw_events import SOURCE_ID, _parse_ts  # noqa: E402

KIND = "port_visits"
#: Filenames are `<kind>_<startYYYYMMDD>_<endYYYYMMDD>.json` — see `fetch_kind`.
_WINDOW_RE = re.compile(r"_(\d{8})_(\d{8})\.json$")


def _ts(ev: dict, key: str):
    """Timestamp for `start`/`end`, whichever shape GFW used.

    Mirrors `map_event`'s parsing exactly. Reading it differently here would
    make the forensics measure something the pipeline does not.
    """
    v = ev.get(key)
    if isinstance(v, dict):
        return _parse_ts(v.get("time"))
    return _parse_ts(v) or _parse_ts(ev.get(f"{key}Date"))


def _q(vals: list[float], qs=(0.05, 0.25, 0.5, 0.75, 0.95, 1.0)) -> dict:
    v = sorted(x for x in vals if x is not None)
    if not v:
        return {}
    return {q: v[min(len(v) - 1, max(0, int(round(q * (len(v) - 1)))))]
            for q in qs}


def _fmt(h: float | None) -> str:
    if h is None:
        return "-"
    return f"{h:,.1f}h" if h < 48 else f"{h:,.0f}h ({h / 24:,.1f}d)"


def _row(label: str, q: dict) -> str:
    cells = "".join(f"{_fmt(q.get(k)):>20}" for k in (0.05, 0.25, 0.5, 0.75,
                                                     0.95, 1.0))
    return f"  {label:<34}{cells}"


def main() -> int:
    root = cfg.data_root / "raw" / SOURCE_ID
    files = sorted(root.glob(f"day=*/{KIND}_*.json")) if root.exists() else []
    if not files:
        print(f"no raw payloads under {root}. Nothing to inspect — and that "
              f"itself is the finding: without raw, none of this is "
              f"answerable and the conformed table cannot be re-derived.")
        return 1

    # ---- 1. is the pull truncated? --------------------------------------
    print("1. PULL SIZE — is 3,000 a number GFW chose, or a cap?\n")
    print(f"  {'file':<44}{'records':>10}{'window':>26}")
    windows: list[tuple[datetime, datetime]] = []
    events: dict[str, dict] = {}
    total_records = 0
    for p in files:
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"  {p.name:<44}  unreadable ({exc})")
            continue
        if isinstance(payload, dict):
            payload = payload.get("entries") or payload.get("events") or []
        m = _WINDOW_RE.search(p.name)
        win = ""
        if m:
            w0 = datetime.strptime(m.group(1), "%Y%m%d").replace(
                tzinfo=timezone.utc)
            w1 = datetime.strptime(m.group(2), "%Y%m%d").replace(
                tzinfo=timezone.utc)
            windows.append((w0, w1))
            win = f"{w0:%Y-%m-%d}..{w1:%Y-%m-%d}"
        total_records += len(payload)
        print(f"  {p.name:<44}{len(payload):>10,}{win:>26}")
        for ev in payload:
            if isinstance(ev, dict) and (ev.get("id") or ev.get("eventId")):
                events[str(ev.get("id") or ev.get("eventId"))] = ev

    print(f"\n  {total_records:,} record(s) across {len(files)} file(s), "
          f"{len(events):,} unique")
    for n in (1000, 2000, 3000, 5000, 10000):
        if total_records == n or len(events) == n:
            print(f"  ** EXACTLY {n:,}. That is a result cap, not a count of "
                  f"what is out there. Every distribution measured from this "
                  f"table is a sample of unknown size and unknown selection, "
                  f"and the profiler must say so. **")

    if not events:
        return 1

    # ---- 2. GFW's durationHours vs end - start --------------------------
    print("\n2. WHOSE DURATION IS IT? — GFW's `durationHours` vs `end - start`\n")
    gfw_h, calc_h, both, disagree = [], [], 0, 0
    worst = None
    for ev in events.values():
        g = ev.get("durationHours")
        s, e = _ts(ev, "start"), _ts(ev, "end")
        c = (e - s).total_seconds() / 3600.0 if s and e else None
        if g is not None:
            gfw_h.append(float(g))
        if c is not None:
            calc_h.append(c)
        if g is not None and c is not None:
            both += 1
            d = abs(float(g) - c)
            if d > max(1.0, 0.01 * c):
                disagree += 1
                if worst is None or d > worst[0]:
                    worst = (d, ev.get("id"), float(g), c)

    print(f"  events carrying GFW `durationHours` : {len(gfw_h):,} of "
          f"{len(events):,}")
    print(f"  events where `end - start` computes : {len(calc_h):,}")
    print(f"  both present                        : {both:,}")
    print(f"  disagree by >1h or >1%              : {disagree:,}")
    if worst:
        print(f"  worst disagreement: event {worst[1]} — GFW says "
              f"{_fmt(worst[2])}, end-start says {_fmt(worst[3])}")
    if not gfw_h:
        print("  ** GFW sends NO durationHours on port visits. Every duration "
              "in the table is ours, computed as end - start. **")

    print(f"\n{'':36}{'p05':>20}{'p25':>20}{'p50':>20}{'p75':>20}"
          f"{'p95':>20}{'max':>20}")
    if gfw_h:
        print(_row("GFW durationHours", _q(gfw_h)))
    print(_row("end - start", _q(calc_h)))

    # ---- 3. length-biased sampling --------------------------------------
    print("\n3. LENGTH BIAS — an overlap query over-samples long events\n")
    if not windows:
        print("  cannot tell: no query window recoverable from the filenames")
    else:
        w0 = min(w[0] for w in windows)
        w1 = max(w[1] for w in windows)
        print(f"  query window: {w0:%Y-%m-%d} .. {w1:%Y-%m-%d} "
              f"({(w1 - w0).days} days)")

        contained, straddling, before_window = [], [], 0
        for ev in events.values():
            s, e = _ts(ev, "start"), _ts(ev, "end")
            if not (s and e):
                continue
            h = (e - s).total_seconds() / 3600.0
            if s < w0:
                before_window += 1
            (contained if s >= w0 and e <= w1 else straddling).append(h)

        n = len(contained) + len(straddling)
        print(f"  fully inside the window : {len(contained):,} "
              f"({len(contained) / n:.1%})")
        print(f"  crossing an edge        : {len(straddling):,} "
              f"({len(straddling) / n:.1%})")
        print(f"  starting BEFORE the window opened: {before_window:,} "
              f"({before_window / n:.1%})")
        print()
        print(f"{'':36}{'p05':>20}{'p25':>20}{'p50':>20}{'p75':>20}"
              f"{'p95':>20}{'max':>20}")
        print(_row("all visits", _q(contained + straddling)))
        print(_row("contained (unbiased)", _q(contained)))
        print(_row("crossing an edge", _q(straddling)))

        if contained and straddling:
            mc = statistics.median(contained)
            ms = statistics.median(straddling)
            print(f"\n  median contained {_fmt(mc)} vs median straddling "
                  f"{_fmt(ms)} — a ratio of {ms / mc:,.0f}x." if mc else "")
            print("  If the contained subset looks like ordinary port calls "
                  "while the straddling one carries the whole tail, the tail "
                  "is a sampling artefact of asking for OVERLAPPING events, "
                  "not a defect in the data or in our mapping. The fix is then "
                  "in the profiler — measure the contained subset — and NOT in "
                  "ingest, which should keep landing exactly what GFW returned.")

    # ---- 4. the extremes, verbatim --------------------------------------
    print("\n4. THE FIVE LONGEST, PRINTED RATHER THAN REASONED ABOUT\n")
    ranked = []
    for ev in events.values():
        s, e = _ts(ev, "start"), _ts(ev, "end")
        if s and e:
            ranked.append(((e - s).total_seconds() / 3600.0, ev))
    ranked.sort(key=lambda x: -x[0])
    for h, ev in ranked[:5]:
        print(f"  --- {_fmt(h)} ---")
        print("  " + json.dumps(ev, indent=2, default=str)[:2400]
              .replace("\n", "\n  "))
        print()

    # ---- 5. shape of the anchorage records ------------------------------
    print("5. ANCHORAGE RECORDS — which parts are actually populated\n")
    fields = Counter()
    for ev in events.values():
        pv = ev.get("port_visit") or ev.get("portVisit") or {}
        if not isinstance(pv, dict):
            continue
        for slot in ("startAnchorage", "intermediateAnchorage", "endAnchorage"):
            a = pv.get(slot)
            if isinstance(a, dict):
                fields[f"{slot}.present"] += 1
                for k in ("id", "name", "flag", "lat", "lon"):
                    if a.get(k) is not None:
                        fields[f"{slot}.{k}"] += 1
    n = len(events)
    for k, v in sorted(fields.items()):
        print(f"  {k:<44}{v:>8,}  ({v / n:.1%})")
    print("\n  `port_name` comes from intermediateAnchorage.name and from "
          "nowhere else. If `.present` is 100% while `.name` is ~54%, then the "
          "45.6% null rate on port_name is an unnamed anchorage, NOT a missing "
          "stop — and any claim built on 'no stop' is wrong.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
