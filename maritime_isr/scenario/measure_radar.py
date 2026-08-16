"""Score the dark contacts against what the radar picture actually contained.

**The second of two modules allowed to read ground truth, and it runs after the
pipeline has finished.** `measure.py` scores alerts against `scenario_truth`;
this scores dark *contacts* against `radar_dark_truth`. They answer different
questions and both are needed: one asks "did the product raise the right alerts
about the right vessels", the other asks "did the sensor fusion find the
unexplained targets", and a system can pass either while failing the other.

Three numbers come out, and the third is the one that decides whether the queue
is usable:

  **recall** — of the dark episodes a station could actually see, how many
  produced a contact. The denominator excludes episodes explainable by AIS
  coverage and episodes on hulls entitled to be dark, because a correct system
  is required *not* to fire on those.

  **precision** — of the contacts produced, how many landed on a real dark
  episode. ADR-004 puts a figure on this: seven in ten must survive review.

  **the false-positive breakdown** — *what* the wrong ones were. A precision
  figure alone is a number to argue about; "four of the six were sea clutter and
  one was a naval unit" is a work list. The classes are deliberately named after
  causes an engineer can act on, and one of them — the naval unit — is named
  after a cause nobody can act on at this layer, which is the honest way to
  argue for the next one.

Matching a produced contact to an episode is by **time overlap and position
inside the episode's own bounding box**, with a margin. Not by vessel: the
pipeline never knew which vessel it was, and letting the measurement match on
identity would score a capability the product does not have.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..ingest.landing import read_table
from .radar_network import FIXED_TARGETS
from .radar_truth import TABLE as RADAR_TRUTH_TABLE

#: How far outside an episode's own bounding box a contact may sit and still be
#: the same thing. Generous, because the contact's representative position is
#: the middle of a dark run and the box is where the plots were: 15 km is about
#: 40 minutes of merchant steaming, which is the resolution the epoch grid has
#: anyway.
MATCH_MARGIN_KM = 15.0

#: How far outside an episode's time window a contact may sit. One correlation
#: epoch either side plus slack.
MATCH_SLACK_MIN = 60.0

#: A contact this close to a known fixed installation is that installation.
FIXED_TARGET_RADIUS_KM = 2.0


def _hav_km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(min(1.0, a)))


def _ts(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, datetime):
        return (v if v.tzinfo else v.replace(tzinfo=timezone.utc)).timestamp()
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def load_truth() -> list[dict]:
    return read_table(RADAR_TRUTH_TABLE)


@dataclass
class RadarMeasurement:
    episodes_total: int = 0
    episodes_findable: int = 0
    episodes_detected: int = 0
    episodes_missed: list[dict] = field(default_factory=list)
    #: Episodes we required the system NOT to find, and whether it stayed quiet.
    excluded_coverage: int = 0
    excluded_coverage_fired: int = 0
    excluded_naval: int = 0
    excluded_naval_fired: int = 0

    contacts_total: int = 0
    contacts_true: int = 0
    contacts_false: int = 0
    fp_by_cause: dict = field(default_factory=dict)

    suppressed: dict = field(default_factory=dict)

    @property
    def recall(self) -> float | None:
        return (self.episodes_detected / self.episodes_findable
                if self.episodes_findable else None)

    @property
    def precision(self) -> float | None:
        return (self.contacts_true / self.contacts_total
                if self.contacts_total else None)


def _inside(ep: dict, lat: float, lon: float) -> bool:
    """Is this position inside the episode's box, plus the margin?"""
    dlat = MATCH_MARGIN_KM / 111.0
    dlon = MATCH_MARGIN_KM / (111.0 * max(math.cos(math.radians(lat)), 0.2))
    return (ep["lat_min"] - dlat <= lat <= ep["lat_max"] + dlat
            and ep["lon_min"] - dlon <= lon <= ep["lon_max"] + dlon)


def _overlaps(ep: dict, t0: float, t1: float) -> bool:
    e0 = (_ts(ep["t_start"]) or 0.0) - MATCH_SLACK_MIN * 60.0
    e1 = (_ts(ep["t_end"]) or 0.0) + MATCH_SLACK_MIN * 60.0
    return t0 <= e1 and e0 <= t1


def _classify_false_positive(v: dict, correlations_by_id: dict) -> str:
    """Name the cause of a wrong contact, so the number is a work list.

    Only sources the *measurement* can see are consulted — the fixed-target
    gazetteer, which is scenario geography, and the contact's own properties.
    Nothing here reaches into `scenario_truth` to ask which vessel it was.
    """
    lat, lon = v.get("lat"), v.get("lon")
    if lat is not None and lon is not None:
        for ft in FIXED_TARGETS:
            if _hav_km(lat, lon, ft.lat, ft.lon) <= FIXED_TARGET_RADIUS_KM:
                return "fixed installation (static layer let it through)"
    c = correlations_by_id.get(v.get("correlation_id")) or {}
    n_plots = c.get("n_plots") or 0
    dark_min = c.get("dark_minutes") or 0.0
    if n_plots and n_plots <= 8:
        return "short-lived return (probable sea clutter)"
    if dark_min and dark_min < 90.0:
        return "brief unexplained interval"
    if (v.get("length_m") or 0) < 40.0:
        return "small target, size gate cleared marginally"
    return "unexplained"


def measure_radar(verdicts: list[dict], correlations: list[dict],
                  truth_rows: list[dict] | None = None) -> RadarMeasurement:
    """Score produced dark contacts against the radar answer key."""
    truth_rows = load_truth() if truth_rows is None else truth_rows
    m = RadarMeasurement()

    from collections import Counter
    m.suppressed = dict(Counter(v["status"] for v in verdicts))

    contacts = [v for v in verdicts if v["status"] == "dark_candidate"]
    m.contacts_total = len(contacts)
    by_id = {c["correlation_id"]: c for c in correlations}

    findable = [e for e in truth_rows
                if e.get("expected_detection")]
    excluded_cov = [e for e in truth_rows if e.get("explainable_by_coverage")]
    excluded_nav = [e for e in truth_rows
                    if e.get("unavoidable_false_positive")]
    m.episodes_total = len(truth_rows)
    m.episodes_findable = len(findable)
    m.excluded_coverage = len(excluded_cov)
    m.excluded_naval = len(excluded_nav)

    # Which contact, if any, lands on which episode. A contact may satisfy more
    # than one episode of the same vessel (her dark run was fragmented into
    # two); it counts for each, because the system did find her both times.
    matched_contacts: set[str] = set()

    def hits(ep: dict) -> list[dict]:
        out = []
        for v in contacts:
            lat, lon = v.get("lat"), v.get("lon")
            if lat is None or lon is None:
                continue
            t = _ts(v.get("ts")) or 0.0
            c = by_id.get(v.get("correlation_id")) or {}
            t0 = _ts(c.get("dark_from")) or t
            t1 = _ts(c.get("dark_to")) or t
            if _overlaps(ep, t0, t1) and _inside(ep, lat, lon):
                out.append(v)
        return out

    for ep in findable:
        h = hits(ep)
        if h:
            m.episodes_detected += 1
            matched_contacts.update(v["candidate_id"] for v in h)
        else:
            m.episodes_missed.append(ep)

    for ep in excluded_cov:
        if hits(ep):
            m.excluded_coverage_fired += 1
            matched_contacts.update(v["candidate_id"] for v in hits(ep))
    for ep in excluded_nav:
        if hits(ep):
            m.excluded_naval_fired += 1
            matched_contacts.update(v["candidate_id"] for v in hits(ep))

    # Precision counts a contact as TRUE only when it lands on an episode we
    # wanted found. A contact on a naval unit or on an out-of-reception vessel
    # is a false positive — it is a contact an analyst opens and closes.
    true_ids: set[str] = set()
    for ep in findable:
        true_ids.update(v["candidate_id"] for v in hits(ep))
    m.contacts_true = len(true_ids)
    m.contacts_false = m.contacts_total - m.contacts_true

    fp_counts: dict[str, int] = {}
    for v in contacts:
        if v["candidate_id"] in true_ids:
            continue
        cause = _classify_false_positive(v, by_id)
        # A contact matching an excluded episode gets the more informative name.
        for ep in excluded_nav:
            if v in hits(ep):
                cause = ("naval unit operating without AIS — no sensor-level "
                         "rule can exclude this")
                break
        else:
            for ep in excluded_cov:
                if v in hits(ep):
                    cause = ("vessel outside AIS reception — the coverage check "
                             "should have suppressed this")
                    break
        fp_counts[cause] = fp_counts.get(cause, 0) + 1
    m.fp_by_cause = fp_counts
    return m


def format_radar_measurement(m: RadarMeasurement) -> str:
    def pct(v):
        return f"{v:.0%}" if v is not None else "n/a"

    lines = ["=" * 76,
             "dark-contact results (measured against radar_dark_truth)",
             "=" * 76]
    lines.append(f"radar dark episodes in the picture : {m.episodes_total}")
    lines.append(f"  ... a correct system should find : {m.episodes_findable}")
    lines.append(f"  ... explainable by AIS coverage  : {m.excluded_coverage} "
                 f"(fired on {m.excluded_coverage_fired} — each one is the "
                 f"out-of-coverage error)")
    lines.append(f"  ... legitimately dark (naval)    : {m.excluded_naval} "
                 f"(fired on {m.excluded_naval_fired})")
    lines.append("")
    lines.append(f"dark contacts produced : {m.contacts_total}")
    lines.append(f"  landed on a real episode : {m.contacts_true}")
    lines.append(f"  did not                  : {m.contacts_false}")
    lines.append("")
    lines.append(f"precision {pct(m.precision)}    recall {pct(m.recall)}"
                 f"    (ADR-004 target: precision >= 70%)")
    lines.append(f"  detected {m.episodes_detected} of {m.episodes_findable} "
                 f"findable episodes")

    if m.fp_by_cause:
        lines.append("")
        lines.append("false positives, by cause:")
        for cause, n in sorted(m.fp_by_cause.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {n:>4}  {cause}")

    if m.episodes_missed:
        lines.append("")
        lines.append("missed episodes:")
        for ep in m.episodes_missed:
            lines.append(
                f"  {str(ep.get('entity_id')):<28}"
                f"{ep.get('duration_min', 0):>7.0f} min  "
                f"n={ep.get('n_plots')}  L={ep.get('length_m')}  "
                f"{ep.get('cause')}  [{ep.get('station_ids')}]")

    if m.suppressed:
        lines.append("")
        lines.append("cascade verdicts (every suppression is recorded, not "
                     "dropped — 'why is this NOT dark' stays answerable):")
        for status, n in sorted(m.suppressed.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {n:>5}  {status}")

    lines.append("")
    lines.append("These are SYNTHETIC-SUITE numbers, measured on a simulated "
                 "radar network over simulated traffic. They say nothing about "
                 "performance on a real Coastal Surveillance Network feed, "
                 "which has never been seen by this system (CLAUDE.md §4.6).")
    return "\n".join(lines)
