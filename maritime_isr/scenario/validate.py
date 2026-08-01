"""Validators — the build fails if the corpus is not physically coherent.

Every check here runs over **all** generated data, decoys and background
included. A corpus with one impossible track in it is worse than no corpus: it
sits in the same tables as real findings, and whatever it teaches a detector is
a lie that will be measured as a capability.

The checks, and why each one earns its place:

  * **In AOI** — a position outside 5-25N / 60-78E is outside everything the
    system is scoped to, and would silently distort any bbox-filtered query.
  * **Implied speed within class** — the single most load-bearing check. It is
    computed on the *emitted reports*, which is where an integration bug or a
    bad shadow offset would actually show up.
  * **Turn rate** — a 330 m hull that hinges through 90 degrees between reports
    is separable from real data by any classifier, for free.
  * **Report intervals** — plausible for the vessel's speed and status, and not
    on a fixed grid.
  * **Identifiers** — reserved bands, checksum-valid IMOs, no collision with a
    real hull, no reference to a real sanctions entry.
  * **All five H3 resolutions, computed from lat/lon** — and specifically *not*
    derived from one another, which disagrees for ~7% of positions (ADR-015).
  * **Envelope complete and in agreement** with `is_synthetic`.
  * **Encounter geometry coherent** — hulls no closer than their beams allow,
    no "transfer" that is really a crossing.
  * **Temporal** — everything inside the real corpus window.

**One exemption mechanism, deliberately narrow.** C3 has to violate the implied-
speed envelope; that is the scenario. It declares `physics_exemption` in its
truth row and is whitelisted **by scenario id and by rule name** — so C3 gets a
pass on implied speed and on nothing else, and no other scenario gets a pass on
anything. A global "allow violations" switch would have been three lines
shorter and would have made every future violation invisible.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..config import AOI_V1, MAX_FEASIBLE_SPEED_KN
from ..h3util import RESOLUTIONS, cell
from ..ingest.sanctions_match import imo_checksum_ok
from .geography import haversine_m
from .identifiers import (SYNTHETIC_SOURCE_ID, assert_no_collisions,
                          is_scenario_sanctions_ref, is_synthetic_imo,
                          is_synthetic_mmsi)
from .primitives.ais import report_intervals_s
from .primitives.encounter import measure_rendezvous
from .primitives.identity import assert_consistent
from .primitives.track import max_turn_rate_deg_s
from .world import in_window

#: Rule names, so an exemption can name exactly one.
RULE_SPEED = "implied_speed_envelope"
RULE_TURN = "turn_rate"
RULE_AOI = "in_aoi"
RULE_INTERVAL = "report_interval"
RULE_H3 = "h3_resolutions"
RULE_ENVELOPE = "provenance_envelope"
RULE_IDENTIFIERS = "identifiers"
RULE_TEMPORAL = "corpus_window"
RULE_GEOMETRY = "encounter_geometry"
RULE_IDENTITY = "identity_intervals"
RULE_OBSERVABLE = "observable"
RULE_VISIT_STRUCTURE = "port_visit_structure"

#: Speed tolerance over the class maximum. Position noise on a short interval
#: can inflate an implied speed slightly, and rejecting that would be rejecting
#: the noise model rather than a physics error. 1.35x with a 90 s floor on the
#: interval keeps the check meaningful without being a noise detector.
SPEED_TOLERANCE = 1.35
MIN_INTERVAL_FOR_SPEED_S = 90.0

#: Longest interval that is still a report rather than a gap. Beyond this the
#: emitter has legitimately stopped (suppression, or out of coverage) and the
#: pair is not an interval to judge.
MAX_PLAUSIBLE_INTERVAL_S = 8 * 3600.0


@dataclass
class Violation:
    rule: str
    subject: str
    detail: str
    scenario_id: str = ""

    def __str__(self) -> str:
        where = f" [{self.scenario_id}]" if self.scenario_id else ""
        return f"{self.rule}: {self.subject}{where} — {self.detail}"


@dataclass
class ValidationReport:
    violations: list[Violation] = field(default_factory=list)
    checks_run: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    def add(self, rule, subject, detail, scenario_id="") -> None:
        self.violations.append(Violation(rule, subject, detail, scenario_id))

    def count(self, rule: str, n: int) -> None:
        self.checks_run[rule] = self.checks_run.get(rule, 0) + n

    def format(self) -> str:
        lines = ["validation"]
        for rule in sorted(self.checks_run):
            bad = sum(1 for v in self.violations if v.rule == rule)
            status = "FAIL" if bad else "ok"
            lines.append(f"  {rule:<26}{status:>6}   "
                         f"{self.checks_run[rule]:,} checked, {bad} violation(s)")
        for w in self.warnings:
            lines.append(f"  WARNING: {w}")
        if self.violations:
            lines.append("")
            lines.append(f"  {len(self.violations)} violation(s):")
            for v in self.violations[:40]:
                lines.append(f"    {v}")
            if len(self.violations) > 40:
                lines.append(f"    ... and {len(self.violations) - 40} more")
        return "\n".join(lines)


def _entity_scenarios(world) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for t in world.truth:
        for e in t.entity_ids:
            out.setdefault(e, []).append(t.scenario_id)
    return out


def validate_world(world) -> ValidationReport:
    rep = ValidationReport()
    exemptions = world.truth.physics_exemptions()
    ent_scen = _entity_scenarios(world)

    def exempt(entity_id: str, rule: str) -> str | None:
        """Return the exempting scenario id, if this entity has one for `rule`."""
        for sid in ent_scen.get(entity_id, []):
            if exemptions.get(sid) == rule:
                return sid
        return None

    _check_positions(world, rep, ent_scen)
    _check_speeds(world, rep, ent_scen, exempt)
    _check_turn_rates(world, rep, ent_scen)
    _check_intervals(world, rep, ent_scen)
    _check_identifiers(world, rep)
    _check_identity(world, rep)
    _check_geometry(world, rep)
    _check_temporal(world, rep)
    _check_h3_and_envelope(world, rep)
    _check_observable(world, rep, ent_scen)
    _check_visit_structure(world, rep)

    if world.clipped:
        rep.warnings.append(
            f"{len(world.clipped)} clipping event(s) at the window edge, e.g. "
            f"{world.clipped[0]}")
    return rep


# --------------------------------------------------------------------------

def _check_positions(world, rep, ent_scen) -> None:
    n = 0
    for eid_, pts in world.tracks.items():
        for p in pts:
            n += 1
            if not AOI_V1.contains(p.lat, p.lon):
                rep.add(RULE_AOI, eid_,
                        f"track point ({p.lat:.4f}, {p.lon:.4f}) at {p.t} is "
                        f"outside AOI v1",
                        ",".join(ent_scen.get(eid_, [])))
                break                       # one report per vessel is enough
    for eid_ in world.ais:
        for r in world.ais_of(eid_):
            n += 1
            if not AOI_V1.contains(r.lat, r.lon):
                rep.add(RULE_AOI, eid_,
                        f"AIS report ({r.lat:.4f}, {r.lon:.4f}) at {r.t} is "
                        f"outside AOI v1",
                        ",".join(ent_scen.get(eid_, [])))
                break
    rep.count(RULE_AOI, n)


def _check_speeds(world, rep, ent_scen, exempt) -> None:
    """Two different questions, deliberately checked separately.

    **Did we generate motion the vessel cannot perform?** That is a property of
    the *integrated truth*, which contains no measurement noise at all, so it is
    checked tightly against the class envelope. A violation here is a real
    physics bug in the generator.

    **Do the emitted reports contain impossible-looking jumps?** They
    legitimately do, and conflating this with the first question was wrong. The
    emitter injects GPS noise with a heavy tail — 0.4% of reports carry a
    ~220 m sigma error, which is realistic — and two adjacent outliers pointing
    opposite ways across a 90 s interval imply better than 25 knots without
    anything being wrong with the vessel. Real AIS does exactly this, which is
    precisely why the track builder treats impossible kinematics as a
    first-class spoof tell rather than discarding them (CLAUDE.md §6), and why
    `MAX_FEASIBLE_SPEED_KN` exists at 60 knots as the physical cap.

    So the emitted stream is checked against that cap instead. Holding it to the
    class envelope would have meant either deleting the noise model or padding
    the tolerance until the check stopped meaning anything — and a corpus with
    unnaturally clean position error is separable from real data on the tails
    alone.
    """
    n_truth = n_emitted = 0

    # --- 1. integrated truth: strict, no noise to excuse anything ---
    for eid_ in world.tracks:
        v = world.vessels[eid_]
        pts = world.track_of(eid_)
        worst, worst_at = 0.0, None
        for a, b in zip(pts, pts[1:]):
            dt = (b.t - a.t).total_seconds()
            if dt <= 0 or dt > MAX_PLAUSIBLE_INTERVAL_S:
                continue
            n_truth += 1
            kn = haversine_m(a.lat, a.lon, b.lat, b.lon) / dt * 3600.0 / 1852.0
            if kn > worst:
                worst, worst_at = kn, b.t
        if worst > v.max_kn * 1.05:
            rep.add(RULE_SPEED, eid_,
                    f"integrated truth reaches {worst:.1f} kn at {worst_at}, "
                    f"above {v.vessel_class} max {v.max_kn} kn — the generator "
                    f"produced motion this hull cannot perform",
                    ",".join(ent_scen.get(eid_, [])))

    # --- 2. emitted reports: capped at what is physically feasible at all ---
    for eid_ in world.ais:
        v = world.vessels[eid_]
        ex = exempt(eid_, RULE_SPEED)
        reports = world.ais_of(eid_)
        worst, worst_at = 0.0, None
        for a, b in zip(reports, reports[1:]):
            dt = (b.t - a.t).total_seconds()
            if dt < MIN_INTERVAL_FOR_SPEED_S or dt > MAX_PLAUSIBLE_INTERVAL_S:
                continue
            n_emitted += 1
            kn = haversine_m(a.lat, a.lon, b.lat, b.lon) / dt * 3600.0 / 1852.0
            if kn > worst:
                worst, worst_at = kn, b.t
        if worst > MAX_FEASIBLE_SPEED_KN:
            if ex:
                rep.warnings.append(
                    f"{eid_}: emitted implied speed {worst:.1f} kn exceeds the "
                    f"{MAX_FEASIBLE_SPEED_KN:.0f} kn feasibility cap — exempted "
                    f"by {ex}, which declares this violation in scenario_truth")
            else:
                rep.add(RULE_SPEED, eid_,
                        f"emitted reports imply {worst:.1f} kn at {worst_at}, "
                        f"beyond the {MAX_FEASIBLE_SPEED_KN:.0f} kn feasibility "
                        f"cap — not explicable by position noise",
                        ",".join(ent_scen.get(eid_, [])))
    rep.count(RULE_SPEED, n_truth + n_emitted)


def _check_turn_rates(world, rep, ent_scen) -> None:
    n = 0
    for eid_, pts in world.tracks.items():
        if len(pts) < 2:
            continue
        n += len(pts) - 1
        v = world.vessels[eid_]
        rot = max_turn_rate_deg_s(sorted(pts, key=lambda p: p.t))
        # 1.6x: the integrator caps turn per step exactly, but a track that
        # concatenates segments can show one larger step at a join where the
        # vessel legitimately resumed on a new heading after a pause.
        if rot > v.rot_deg_per_s * 1.6 + 0.05:
            rep.add(RULE_TURN, eid_,
                    f"turn rate {rot:.2f} deg/s exceeds {v.vessel_class} limit "
                    f"{v.rot_deg_per_s} deg/s",
                    ",".join(ent_scen.get(eid_, [])))
    rep.count(RULE_TURN, n)


def _check_intervals(world, rep, ent_scen) -> None:
    n = 0
    for eid_ in world.ais:
        reports = world.ais_of(eid_)
        iv = [x for x in report_intervals_s(reports)
              if x <= MAX_PLAUSIBLE_INTERVAL_S]
        n += len(iv)
        if not iv:
            continue
        if min(iv) < 1.5:
            rep.add(RULE_INTERVAL, eid_,
                    f"report interval {min(iv):.1f} s is below anything "
                    f"ITU-R M.1371 Class A produces",
                    ",".join(ent_scen.get(eid_, [])))
        # A perfectly regular series is a synthetic tell in its own right.
        if len(iv) > 30 and len(set(round(x) for x in iv)) == 1:
            rep.add(RULE_INTERVAL, eid_,
                    f"every one of {len(iv)} intervals is exactly "
                    f"{iv[0]:.0f} s — real reception is never that regular",
                    ",".join(ent_scen.get(eid_, [])))
    rep.count(RULE_INTERVAL, n)


def _check_identifiers(world, rep) -> None:
    imos, mmsis, refs = world.all_identifiers()
    rep.count(RULE_IDENTIFIERS, len(imos) + len(mmsis) + len(refs))

    for imo in imos:
        if not is_synthetic_imo(imo):
            rep.add(RULE_IDENTIFIERS, str(imo),
                    "IMO outside the reserved 1000000-1999999 band")
        elif not imo_checksum_ok(str(imo)):
            rep.add(RULE_IDENTIFIERS, str(imo),
                    "synthetic IMO fails its own check digit — it would be "
                    "rejected by normalise_imo and never exercise the "
                    "validator it is meant to test")
    for mmsi in mmsis:
        if not is_synthetic_mmsi(mmsi):
            rep.add(RULE_IDENTIFIERS, str(mmsi),
                    "MMSI outside the reserved 999xxxxxx block")
    for ref in refs:
        if not is_scenario_sanctions_ref(ref):
            rep.add(RULE_IDENTIFIERS, str(ref),
                    "sanctions reference does not point at the fictional "
                    "SCENARIO-SDN list")

    # The real check: collision against the corpus, or against the profile.
    try:
        col = assert_no_collisions(imos, mmsis, refs, profile=world.profile,
                                   raise_on_collision=False)
        if not col.clean:
            rep.add(RULE_IDENTIFIERS, "collision-guard", col.describe())
        if not col.is_live_check:
            rep.warnings.append(
                f"identifier collision guard ran against "
                f"{col.checked_against} rather than the landed corpus "
                f"({col.n_real_imos} IMO(s), {col.n_real_mmsis} MMSI(s), "
                f"{col.n_ofac_imos} OFAC IMO(s) known). Re-run where the "
                f"corpus lives for a live check.")
    except Exception as exc:                                # noqa: BLE001
        rep.add(RULE_IDENTIFIERS, "collision-guard",
                f"guard failed to run: {type(exc).__name__}: {exc}")


def _check_identity(world, rep) -> None:
    problems = assert_consistent(world.identity)
    rep.count(RULE_IDENTITY, len(world.identity.intervals))
    for p in problems:
        rep.add(RULE_IDENTITY, "identity-ledger", p)


def _check_geometry(world, rep) -> None:
    n = 0
    for ev in world.events:
        if ev.kind != "encounters" or not ev.counterpart_entity_id:
            continue
        n += 1
        a = world.vessels.get(ev.entity_id)
        b = world.vessels.get(ev.counterpart_entity_id)
        if a is None or b is None:
            continue
        pa = [p for p in world.track_of(a.entity_id)
              if ev.t_start <= p.t <= ev.t_end]
        pb = [p for p in world.track_of(b.entity_id)
              if ev.t_start <= p.t <= ev.t_end]
        if not pa or not pb:
            rep.add(RULE_GEOMETRY, ev.event_id,
                    "encounter has no overlapping track samples — the two "
                    "vessels are not co-timed")
            continue
        try:
            spec = measure_rendezvous(pa, pb, ev.t_start, ev.t_end)
        except ValueError as exc:
            rep.add(RULE_GEOMETRY, ev.event_id, str(exc))
            continue
        floor = max(a.min_separation_m(), b.min_separation_m())
        if spec.min_separation_m < floor * 0.9:
            rep.add(RULE_GEOMETRY, ev.event_id,
                    f"hulls {spec.min_separation_m:.0f} m apart, closer than "
                    f"their beams allow ({floor:.0f} m)")
        if spec.max_closing_speed_kn > 3.0:
            rep.add(RULE_GEOMETRY, ev.event_id,
                    f"separation changing at {spec.max_closing_speed_kn:.1f} kn "
                    f"— a crossing, not a rendezvous")
    rep.count(RULE_GEOMETRY, n)


def _check_temporal(world, rep) -> None:
    n = 0
    for eid_ in world.ais:
        for r in world.ais_of(eid_):
            n += 1
            if not in_window(r.t):
                rep.add(RULE_TEMPORAL, eid_,
                        f"AIS report at {r.t} is outside the corpus window "
                        f"{world.t0:%Y-%m-%d}..{world.t1:%Y-%m-%d}")
                break
    for ev in world.events:
        n += 1
        if not (in_window(ev.t_start) and in_window(ev.t_end)):
            rep.add(RULE_TEMPORAL, ev.event_id,
                    f"event spans {ev.t_start}..{ev.t_end}, outside the "
                    f"corpus window")
    for t in world.truth:
        n += 1
        if not (in_window(t.t_start) and in_window(t.t_end)):
            rep.add(RULE_TEMPORAL, t.scenario_id,
                    f"truth window {t.t_start}..{t.t_end} is outside the "
                    f"corpus window")
    rep.count(RULE_TEMPORAL, n)


def _check_observable(world, rep, ent_scen) -> None:
    """Every scenario must leave *some* evidence a detector could find.

    A vessel operating entirely offshore legitimately lands no AIS positions:
    our own reception is terrestrial, satellite AIS is unfunded (ADR-005), and
    the offshore silence in this corpus is the honest consequence. That is not
    a bug — it is the same coverage hole the real system has, and scenarios
    like A1 depend on it.

    What *is* a bug is a scenario with no observable evidence at all: no landed
    position, no landed event, nothing. B5's ghost was generated in the deep
    basin, landed zero rows, and the duplicate-MMSI collision it exists to test
    could never have been seen. That would have been scored as a detector miss
    when in truth the corpus never contained the evidence — a silently
    worthless row in the recall denominator.

    So the check is at scenario granularity: at least one participating entity
    must have landed either AIS or an event. Per-vessel silence is reported as
    a warning, because it is usually correct and occasionally a clue.
    """
    with_events: set[str] = set()
    for ev in world.events:
        with_events.add(ev.entity_id)
        if ev.counterpart_entity_id:
            with_events.add(ev.counterpart_entity_id)

    n = 0
    for t in world.truth:
        n += 1
        evidence = [e for e in t.entity_ids
                    if world.ais.get(e) or e in with_events]
        # A scenario whose participants are all legitimately non-transmitting
        # — the naval decoy — has silence AS its content. Requiring evidence
        # from her would be requiring her to break the scenario.
        all_silent_by_design = all(
            not world.vessels[e].ais_expected
            for e in t.entity_ids if e in world.vessels)
        if not evidence and not all_silent_by_design:
            rep.add(RULE_OBSERVABLE, t.scenario_id,
                    f"no participating entity landed any AIS report or any "
                    f"event — the scenario has no observable evidence at all "
                    f"and would be scored as a detector miss when the corpus "
                    f"never contained anything to find "
                    f"(entities: {', '.join(t.entity_ids)})",
                    t.scenario_id)

    silent = sorted(eid for eid, v in world.vessels.items()
                    if v.ais_expected and world.tracks.get(eid)
                    and not world.ais.get(eid))
    if silent:
        rep.warnings.append(
            f"{len(silent)} vessel(s) moved but landed no AIS — offshore, "
            f"outside terrestrial reception. Expected under ADR-005, but check "
            f"the scenario still has evidence: e.g. {', '.join(silent[:4])}")
    rep.count(RULE_OBSERVABLE, n)


#: How far the achieved class mix may sit from the profile's before it counts
#: as a violation. Generous, because a few hundred synthetic visits cannot hit
#: a fraction precisely and the check is that the mix is the right *shape*, not
#: that it matches to three decimals.
VISIT_MIX_TOLERANCE = 0.15


def _check_visit_structure(world, rep) -> None:
    """Port-visit structure: internally coherent, and the real mix.

    Two failures, both silent without this. **Coherence**: `dwell_hours` is
    populated exactly when the vessel stopped and entered and left the same
    anchorage, so a row with a dwell and no stop is impossible — it would only
    ever occur on synthetic data, and anything downstream that trusts the
    relationship would break there and nowhere else. **Mix**: if every synthetic
    visit is a clean dwell, `WHERE dwell_hours IS NULL` separates the two
    populations perfectly, which is the null-rate failure family again.

    Read back from the landed table rather than the in-memory world, for the
    same reason the H3 check is: the question is whether the rows that exist are
    right, not whether the code that wrote them looked right.
    """
    from ..ingest.landing import read_table
    from .land import T_PORT_VISITS

    try:
        rows = [r for r in read_table(T_PORT_VISITS) if r.get("is_synthetic")]
    except Exception:                                          # noqa: BLE001
        return
    if not rows:
        return

    counts: dict[str, int] = {}
    for r in rows:
        stop = r.get("visit_has_stop")
        agree = r.get("visit_anchorages_agree")
        dwell = r.get("dwell_hours")
        if (dwell is not None) != bool(stop and agree is True):
            rep.add(RULE_VISIT_STRUCTURE, str(r.get("event_id")),
                    f"dwell_hours={dwell!r} but visit_has_stop={stop!r} and "
                    f"visit_anchorages_agree={agree!r} — a dwell exists exactly "
                    f"when the vessel stopped and entered and left the same "
                    f"anchorage, so this row is jointly impossible")
            break
        if r.get("port_name") is not None and not stop:
            rep.add(RULE_VISIT_STRUCTURE, str(r.get("event_id")),
                    "port_name is populated on a visit with no observed stop, "
                    "but the real mapper reads it from the stop anchorage and "
                    "from nowhere else")
            break
        cls = ("dwell" if dwell is not None
               else "no_stop" if stop is False and agree is not None
               else "anchorages_differ" if agree is False
               else "unknown")
        counts[cls] = counts.get(cls, 0) + 1

    target = world.profile.visit_structure().value
    n = len(rows)
    for cls, want in target.items():
        got = counts.get(cls, 0) / n
        if abs(got - want) > VISIT_MIX_TOLERANCE:
            rep.add(RULE_VISIT_STRUCTURE, cls,
                    f"{got:.1%} of {n:,} synthetic port visits are '{cls}' but "
                    f"the profile says {want:.1%} — a filter on dwell_hours or "
                    f"port_name would separate synthetic rows from real ones")
    rep.count(RULE_VISIT_STRUCTURE, n)


def _check_h3_and_envelope(world, rep) -> None:
    """Verify H3 and the envelope on rows read back from disk.

    Deliberately reads the landed tables rather than checking the in-memory
    world: the question is whether the *rows that exist* are correct, and an
    in-memory check would pass even if the landing path dropped a column. This
    is the same lesson as the ADR-018 join guard — exercise the thing, do not
    assert that it exists.
    """
    from ..ingest.landing import read_table
    from .land import ALL_TABLES

    n = 0
    for table in ALL_TABLES:
        try:
            rows = read_table(table)
        except Exception:                                  # noqa: BLE001
            continue
        syn = [r for r in rows if r.get("is_synthetic")]
        for r in syn[:4000]:                # bounded: this is a sampling check
            n += 1
            missing = [c for c in ("source_id", "source_ref", "acquired_at",
                                   "ingested_at", "pipeline_version",
                                   "is_synthetic")
                       if r.get(c) is None]
            if missing:
                rep.add(RULE_ENVELOPE, table,
                        f"row missing envelope fields {missing}")
                break
            if r["source_id"] != SYNTHETIC_SOURCE_ID:
                rep.add(RULE_ENVELOPE, table,
                        f"is_synthetic row carries source_id "
                        f"{r['source_id']!r}, not {SYNTHETIC_SOURCE_ID!r}")
                break
        # H3 on positioned rows
        positioned = [r for r in syn
                      if r.get("lat") is not None and r.get("lon") is not None]
        for r in positioned[:2000]:
            n += 1
            bad = []
            for res in RESOLUTIONS:
                col = f"h3_r{res}"
                got = r.get(col)
                if got is None:
                    bad.append(f"{col} missing")
                    continue
                want = cell(float(r["lat"]), float(r["lon"]), res)
                if got != want:
                    # This is the ADR-015 failure: a cell that does not match
                    # direct computation means it was derived, not computed.
                    bad.append(f"{col}={got} but direct computation gives {want}")
            if bad:
                rep.add(RULE_H3, table, "; ".join(bad[:3]))
                break
    rep.count(RULE_H3, n)
    rep.count(RULE_ENVELOPE, n)
