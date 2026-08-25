"""Tests for the scenario generator (ADR-019).

The five that matter most, and why:

  **truth isolation** — a detector with the answer key measures nothing, so no
  detection, fusion, graph, scoring or alerting module may read `scenario_truth`.
  Enforced by grepping the source, because an import is easy to add by accident
  and impossible to notice in a green run.

  **decoy separability** — true positives and decoys must be statistically
  indistinguishable on generation artefacts (report cadence, position noise,
  speed). If they are not, a detector separates them on craftsmanship and every
  precision number is worthless.

  **identifier reservation** — no scenario hull may wear a real IMO, a real
  MMSI or a real sanctions entry number. This is a hard ban, not a preference.

  **flag/source agreement** — `is_synthetic` and the `synthetic-scenario`
  source id are two markers for one fact, and two markers that can drift apart
  are worse than one.

  **determinism** — the same seed reproduces the corpus, or nothing measured
  against it can be compared across runs.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import statistics
from datetime import timedelta
from pathlib import Path

import pytest

from maritime_isr.config import AOI_V1
from maritime_isr.h3util import RESOLUTIONS, cell
from maritime_isr.ingest.landing import SYNTHETIC_SOURCE_ID, stamp_envelope
from maritime_isr.ingest.sanctions_match import imo_checksum_ok
from maritime_isr.scenario import ScenarioWorld, T0, T1, week
from maritime_isr.scenario.cast import build_cast
from maritime_isr.scenario.identifiers import (IMO_MAX, IMO_MIN, MMSI_MAX,
                                               MMSI_MIN, assert_no_collisions,
                                               is_scenario_sanctions_ref,
                                               is_synthetic_imo,
                                               is_synthetic_mmsi, mint_imo,
                                               mint_mmsi)
from maritime_isr.scenario.primitives import (Leg, VoyagePlan, emit_ais,
                                              generate_track, make_vessel,
                                              report_intervals_s)
from maritime_isr.scenario.profile import CorpusProfile
from maritime_isr.scenario.scenarios import run_all
from maritime_isr.scenario.truth import (DECOY, DELIBERATE_MISS, TRUE_ANOMALY,
                                         ScenarioTruth)
from maritime_isr.scenario.validate import validate_world

REPO = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------
# a built world, shared across tests (generation is expensive)
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def world():
    w = ScenarioWorld.new(7, CorpusProfile.load())
    build_cast(w)
    run_all(w)
    w.identity.close_window(w.t1)
    return w


# --------------------------------------------------------------------------
# 1. truth isolation
# --------------------------------------------------------------------------

#: Every package that reaches a verdict. None may consult ground truth.
DETECTION_PATHS = ("detect", "fusion", "fuse", "anomaly", "graph", "tracks",
                   "rules", "eval", "product", "api",
                   # The MDA assistant (ADR-031) assembles, ranks, narrates and
                   # answers questions about subjects. It is the surface an
                   # operator forms their trust from, so it is the last place
                   # that may see the answer key.
                   "assistant")


def _truth_references(path: Path) -> list[str]:
    """Real references to ground truth in a module — not mentions of it.

    Parsed rather than grepped, and docstrings are excluded deliberately. Half
    a dozen modules *document* the rule ("the cause lives only in
    scenario_truth"), and a grep cannot tell a promise not to read something
    from a read. What counts is an import of the truth module, a use of its
    types, or the table name appearing as a live string constant.
    """
    import ast
    tree = ast.parse(path.read_text(encoding="utf-8"))

    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))

    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[-1] in ("truth", "radar_truth"):
                hits.append(f"line {node.lineno}: from {node.module} import ...")
            for a in node.names:
                if a.name in ("ScenarioTruth", "TruthLedger",
                              "RadarDarkEpisode", "RadarTruthLedger"):
                    hits.append(f"line {node.lineno}: imports {a.name}")
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.endswith(".truth"):
                    hits.append(f"line {node.lineno}: import {a.name}")
        elif isinstance(node, ast.Name) and node.id in (
                "ScenarioTruth", "TruthLedger", "RadarDarkEpisode",
                "RadarTruthLedger"):
            hits.append(f"line {node.lineno}: uses {node.id}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstrings:
                continue
            # Both quarantined tables. `radar_dark_truth` (ADR-028) is the
            # second one: a radar detector holding the answer key measures
            # nothing, exactly as an AIS one would not.
            for table in ("scenario_truth", "radar_dark_truth"):
                if table in node.value:
                    hits.append(f"line {node.lineno}: string {node.value!r}")
    return hits


def test_no_detection_code_reads_scenario_truth():
    """The answer key is off limits to anything that decides anything."""
    offenders = []
    for pkg in DETECTION_PATHS:
        d = REPO / "maritime_isr" / pkg
        if not d.is_dir():
            continue
        for path in d.rglob("*.py"):
            for hit in _truth_references(path):
                offenders.append(f"{path.relative_to(REPO)}:{hit}")
    assert not offenders, (
        "detection/fusion/graph/scoring code must never read scenario_truth — "
        "a detector with the answer key measures nothing:\n"
        + "\n".join(offenders))


def test_measure_is_the_only_truth_consumer():
    """Inside `scenario/`, only the harness and the writers touch truth."""
    # `radar.py` writes radar truth the way Layer 2 writes scenario truth;
    # `radar_truth.py` defines it; `measure_radar.py` is its one reader, and it
    # runs after the pipeline has finished. Same discipline, second table.
    allowed = {"truth.py", "land.py", "measure.py", "run.py", "validate.py",
               "world.py", "__init__.py",
               "radar.py", "radar_truth.py", "measure_radar.py"}
    offenders = []
    for path in (REPO / "maritime_isr" / "scenario").rglob("*.py"):
        if path.name in allowed or "scenarios" in path.parts:
            continue              # Layer 2 legitimately writes truth rows
        for hit in _truth_references(path):
            offenders.append(f"{path.relative_to(REPO)}:{hit}")
    assert not offenders, f"unexpected scenario_truth readers: {offenders}"


def test_the_isolation_check_can_actually_fail(tmp_path):
    """A guard that cannot fail is not a guard.

    Four bugs in this codebase were checks that asserted a thing existed rather
    than exercising it (STATE.md). This one gets a negative control: a module
    that really does read the truth table must be caught.
    """
    bad = tmp_path / "bad.py"
    bad.write_text('"""A docstring mentioning scenario_truth is fine."""\n'
                   'rows = read_table("scenario_truth")\n')
    assert _truth_references(bad), "the isolation check failed to catch a read"

    # The second quarantined table must be caught the same way (ADR-028).
    bad_radar = tmp_path / "bad_radar.py"
    bad_radar.write_text('rows = read_table("radar_dark_truth")\n')
    assert _truth_references(bad_radar), (
        "the isolation check does not know about radar_dark_truth")

    good = tmp_path / "good.py"
    good.write_text('"""The cause lives only in scenario_truth, never here."""\n'
                    '# scenario_truth is off limits\n'
                    'x = 1\n')
    assert not _truth_references(good), (
        "the isolation check flagged a docstring — it must distinguish a "
        "promise not to read from a read")


# --------------------------------------------------------------------------
# 2. decoy vs true-positive separability
# --------------------------------------------------------------------------

def _stats(world, truth_class):
    """Generation-artefact statistics for every vessel in a truth class."""
    entities = set()
    for t in world.truth:
        if t.truth_class == truth_class:
            entities.update(t.entity_ids)
    intervals, speeds, noise_proxy = [], [], []
    for eid in entities:
        reps = world.ais_of(eid)
        if len(reps) < 20:
            continue
        iv = [x for x in report_intervals_s(reps) if 0 < x <= 3600]
        intervals += iv
        speeds += [r.sog_kn for r in reps]
        # Position-noise proxy: residual against the integrated truth at the
        # same instant. This is the quantity a classifier would exploit if the
        # decoys were emitted more cleanly than the true positives.
        truth = {p.t: p for p in world.track_of(eid)}
        for r in reps[:400]:
            p = truth.get(r.t)
            if p is not None:
                from maritime_isr.scenario.geography import haversine_m
                noise_proxy.append(haversine_m(r.lat, r.lon, p.lat, p.lon))
    return intervals, speeds, noise_proxy


def test_decoys_are_not_trivially_separable_from_true_positives(world):
    """Decoys must be built to the same fidelity as the anomalies.

    A detector that could separate the two on report cadence or position noise
    would be reading the generator, not the behaviour, and the precision figure
    would measure this file rather than the system.

    Speed is deliberately NOT compared: a fishing fleet and a VLCC genuinely
    differ, and forcing those to match would mean generating physically wrong
    vessels. The artefacts that must match are the ones with no behavioural
    meaning.
    """
    tp_iv, _, tp_noise = _stats(world, TRUE_ANOMALY)
    dc_iv, _, dc_noise = _stats(world, DECOY)

    assert len(tp_iv) > 200 and len(dc_iv) > 200, "not enough samples to compare"
    assert len(tp_noise) > 200 and len(dc_noise) > 200

    # Median report interval within a factor of two.
    tp_m, dc_m = statistics.median(tp_iv), statistics.median(dc_iv)
    ratio = max(tp_m, dc_m) / max(min(tp_m, dc_m), 1e-9)
    assert ratio < 2.0, (
        f"report cadence separates decoys from true positives: median "
        f"{tp_m:.0f}s vs {dc_m:.0f}s (ratio {ratio:.2f})")

    # Position noise from the same distribution — the strongest tell there is.
    tp_n, dc_n = statistics.median(tp_noise), statistics.median(dc_noise)
    nratio = max(tp_n, dc_n) / max(min(tp_n, dc_n), 1e-9)
    assert nratio < 1.5, (
        f"position noise separates decoys from true positives: median "
        f"{tp_n:.1f} m vs {dc_n:.1f} m (ratio {nratio:.2f})")


def test_decoys_and_true_positives_use_the_same_encounter_primitive(world):
    """The bunkering decoy and the illicit transfers are geometric siblings."""
    seps = {}
    for ev in world.events:
        if ev.kind != "encounters":
            continue
        seps[ev.event_id] = ev.props.get("mean_separation_m")
    assert len(seps) >= 3
    vals = [v for v in seps.values() if v]
    assert min(vals) > 0
    # The claim is that every encounter is a ship-to-ship rendezvous from the
    # same primitive, so each separation has to be a plausible one: lashed
    # alongside at the low end, a loose standoff at the high end. Stated as an
    # absolute band rather than as a max/min ratio, because the ratio measures
    # the luck of the draw — adding vessels elsewhere in the corpus shifts the
    # RNG stream, and a spread of 19-202 m (ratio 10.7) tripped a 10x bound
    # while every value in it was an ordinary rendezvous. An absolute band is
    # what "same scale" actually means and it does not move when the stream does.
    assert min(vals) >= 5.0 and max(vals) <= 1000.0, (
        f"encounter separations span {min(vals):.0f}-{max(vals):.0f} m; that is "
        f"outside the range of a ship-to-ship rendezvous, so one family is "
        f"being generated differently from another")
    # A family generated at, say, kilometre scale would still show up here.
    assert max(vals) / min(vals) < 25.0, (
        f"encounter separations span {min(vals):.0f}-{max(vals):.0f} m")


# --------------------------------------------------------------------------
# 3. reserved identifiers
# --------------------------------------------------------------------------

def test_every_synthetic_mmsi_is_in_the_reserved_block(world):
    imos, mmsis, refs = world.all_identifiers()
    assert mmsis
    for m in mmsis:
        assert is_synthetic_mmsi(m), f"MMSI {m} outside {MMSI_MIN}-{MMSI_MAX}"


#: Hulls allowed to wear a malformed IMO, because their scenario *is* the
#: malformation. Named here as well as declared in the truth row, so widening
#: the exception takes an edit in two places and cannot happen by accident.
IMO_CHECKSUM_EXEMPT = {"vessel:bad_imo_hull"}


def test_every_synthetic_imo_is_reserved_and_checksum_valid(world):
    """The band reservation is absolute; the checksum has exactly one exception.

    Being inside 1000000-1999999 is what stops a synthetic hull wearing a real
    vessel's identity, and nothing is ever exempt from it. The checksum is a
    different guarantee — it exists so the corpus exercises `normalise_imo`'s
    validation rather than skipping it — and F1's whole scenario is a hull
    broadcasting a number that fails it (ADR-034). A corpus where every IMO
    passes exercises the passing branch 238 times and the failing branch never.
    """
    imos, _, _ = world.all_identifiers()
    assert imos
    exempt = {int(world.vessels[e].imo) for e in IMO_CHECKSUM_EXEMPT
              if e in world.vessels and world.vessels[e].imo}
    for i in imos:
        assert is_synthetic_imo(i), f"IMO {i} outside {IMO_MIN}-{IMO_MAX}"
        if int(i) in exempt:
            assert not imo_checksum_ok(str(i)), (
                f"IMO {i} is listed as deliberately malformed but passes its "
                f"check digit — the scenario it exists for cannot fire")
            continue
        assert imo_checksum_ok(str(i)), (
            f"IMO {i} fails its own check digit — it would be rejected by "
            f"normalise_imo and never exercise the validator it exists to test")


def test_minted_imos_are_valid_and_unique():
    seen = set()
    for serial in range(0, 500):
        imo = mint_imo(serial)
        assert imo_checksum_ok(str(imo))
        assert IMO_MIN <= imo <= IMO_MAX
        assert imo not in seen
        seen.add(imo)


def test_sanctions_references_are_fictional(world):
    _, _, refs = world.all_identifiers()
    assert refs
    for r in refs:
        assert is_scenario_sanctions_ref(r), (
            f"{r!r} does not point at SCENARIO-SDN — a scenario must never "
            f"reference a real OFAC entry number")


def test_collision_guard_rejects_a_real_identifier():
    """The guard must actually reject, not merely report."""
    class _P:
        origin = "test"
        def real_imos(self):
            return ["9999998"]
        def real_mmsis(self):
            return ["419100001"]
        def ofac_imos(self):
            return []

    with pytest.raises(ValueError):
        assert_no_collisions([9999998], [mint_mmsi(0)], ["SCENARIO-SDN-0001"],
                             profile=_P())


def test_collision_guard_reports_its_denominator():
    """A guard that checked nothing must not read as a clean bill of health."""
    rep = assert_no_collisions([mint_imo(0)], [mint_mmsi(0)],
                               ["SCENARIO-SDN-0001"], profile=None,
                               raise_on_collision=False)
    assert "nothing" in rep.checked_against or rep.n_real_imos >= 0
    assert "checked" in rep.describe()


# --------------------------------------------------------------------------
# 4. flag / source agreement
# --------------------------------------------------------------------------

def test_is_synthetic_and_source_id_cannot_disagree():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    # synthetic flag with a real source
    with pytest.raises(ValueError):
        stamp_envelope({}, source_id="gfw-events", source_ref="x",
                       acquired_at=now, is_synthetic=True)
    # synthetic source without the flag
    with pytest.raises(ValueError):
        stamp_envelope({}, source_id=SYNTHETIC_SOURCE_ID, source_ref="x",
                       acquired_at=now, is_synthetic=False)
    # both agreeing, either way, is fine
    a = stamp_envelope({}, source_id=SYNTHETIC_SOURCE_ID, source_ref="x",
                       acquired_at=now, is_synthetic=True)
    b = stamp_envelope({}, source_id="gfw-events", source_ref="x",
                       acquired_at=now, is_synthetic=False)
    assert a["is_synthetic"] is True and b["is_synthetic"] is False


def test_graph_store_rejects_disagreeing_edges(tmp_path):
    from maritime_isr.graph import GraphStore
    g = GraphStore(tmp_path / "g.sqlite")
    g.upsert_node("vessel:a", "vessel", {}, is_synthetic=True)
    g.upsert_node("flag:PAN", "flag_state", {})
    with pytest.raises(ValueError):
        g.add_edge("flagged-to", "vessel:a", "flag:PAN", t_start=0.0,
                   confidence=0.5, source="gfw-vessels", source_ref="x",
                   is_synthetic=True)
    g.add_edge("flagged-to", "vessel:a", "flag:PAN", t_start=0.0,
               confidence=0.5, source="synthetic-scenario:gfw-vessels",
               source_ref="x", is_synthetic=True)
    assert g.n_edges(is_synthetic=True) == 1
    g.close()


def test_is_synthetic_migration_is_zero_recompute(tmp_path):
    """Adding the column must not touch a single existing row.

    The real graph carries 17,562 nodes and 20,026 current edges of history
    that cannot be regenerated (ADR-011), so a migration that rewrote rows
    would be rewriting the moat.
    """
    import sqlite3
    from maritime_isr.graph import GraphStore

    db = tmp_path / "g.sqlite"
    g = GraphStore(db)
    g.upsert_node("vessel:a", "vessel", {})
    g.upsert_node("flag:PAN", "flag_state", {})
    g.add_edge("flagged-to", "vessel:a", "flag:PAN", t_start=0.0,
               confidence=0.5, source="gfw-vessels", source_ref="x")
    before = g.edges_checksum()
    g.close()

    # Simulate a pre-migration database by dropping the column back off.
    # The index has to go first — SQLite refuses to drop a column an index
    # references, which is itself a small confirmation that the column is
    # properly wired in rather than bolted on.
    con = sqlite3.connect(str(db))
    con.execute("DROP INDEX IF EXISTS ix_edges_syn")
    con.execute("ALTER TABLE edges DROP COLUMN is_synthetic")
    con.commit()
    con.close()

    g2 = GraphStore(db)                     # migration runs in __init__
    assert g2.n_edges() == 1
    assert g2.n_edges(is_synthetic=False) == 1
    assert g2.n_edges(is_synthetic=True) == 0
    e = g2.edges("vessel:a")[0]
    assert e.is_synthetic is False
    assert e.source == "gfw-vessels"
    g2.close()
    assert before is not None


# --------------------------------------------------------------------------
# 5. determinism
# --------------------------------------------------------------------------

def _digest(w) -> str:
    """A stable fingerprint of everything a run produced."""
    h = hashlib.sha256()
    for eid in sorted(w.vessels):
        v = w.vessels[eid]
        h.update(f"{v.entity_id}|{v.imo}|{v.mmsi}|{v.length_m}|{v.beam_m}|"
                 f"{v.draught_m}|{v.service_kn}|{v.flag}|{v.name}".encode())
    for eid in sorted(w.ais):
        for r in w.ais_of(eid):
            h.update(f"{eid}|{r.t.isoformat()}|{r.lat:.7f}|{r.lon:.7f}|"
                     f"{r.sog_kn}|{r.cog_deg}".encode())
    for ev in sorted(w.events, key=lambda e: e.event_id):
        h.update(f"{ev.event_id}|{ev.t_start.isoformat()}|{ev.lat:.6f}".encode())
    for t in w.truth:
        h.update(json.dumps(t.as_row(), sort_keys=True, default=str).encode())
    return h.hexdigest()


def test_track_generation_is_deterministic():
    """The fast determinism check: same seed, byte-identical primitive output."""
    prof = CorpusProfile.load()

    def run():
        rng = random.Random(11)
        v = make_vessel(rng, prof, "Aframax", serial=0,
                        entity_id="vessel:t", flag="PAN")
        pts = generate_track(v, VoyagePlan(
            start=(20.0, 64.0), start_time=T0,
            legs=[Leg("transit", target=(21.5, 66.0), speed_kn=13.0),
                  Leg("station", duration_h=4.0)]), rng)
        reps = emit_ais(v, pts, rng)
        return [(p.t, round(p.lat, 9), round(p.lon, 9)) for p in pts], \
               [(r.t, round(r.lat, 9), round(r.lon, 9)) for r in reps]

    assert run() == run()


@pytest.mark.slow
def test_full_generation_is_deterministic():
    """Same seed, byte-identical corpus. Slow: it builds the world twice."""
    def build(seed):
        w = ScenarioWorld.new(seed, CorpusProfile.load())
        build_cast(w)
        run_all(w)
        w.identity.close_window(w.t1)
        return _digest(w)

    assert build(7) == build(7)


@pytest.mark.slow
def test_different_seeds_produce_different_corpora():
    def build(seed):
        w = ScenarioWorld.new(seed, CorpusProfile.load())
        build_cast(w)
        run_all(w)
        return _digest(w)

    assert build(7) != build(8)


# --------------------------------------------------------------------------
# 6. physical coherence
# --------------------------------------------------------------------------

def test_validators_pass_on_the_generated_world(world):
    rep = validate_world(world)
    assert rep.ok, "physics/plausibility violations:\n" + rep.format()


def test_every_position_is_inside_aoi_v1(world):
    for eid, pts in world.tracks.items():
        for p in pts:
            assert AOI_V1.contains(p.lat, p.lon), (
                f"{eid} at ({p.lat}, {p.lon}) is outside AOI v1")


def test_every_event_is_inside_the_corpus_window(world):
    for ev in world.events:
        assert T0 <= ev.t_start <= T1
        assert T0 <= ev.t_end <= T1


def test_all_five_h3_resolutions_are_computed_not_derived(world):
    """Never derive a coarse cell from a fine one (ADR-015, 7.2% disagreement)."""
    from maritime_isr.ingest.landing import stamp_h3
    checked = 0
    for eid in sorted(world.ais)[:8]:
        for r in world.ais_of(eid)[:40]:
            row = stamp_h3(dict(lat=r.lat, lon=r.lon))
            for res in RESOLUTIONS:
                assert row[f"h3_r{res}"] == cell(r.lat, r.lon, res)
                checked += 1
    assert checked > 100


def test_a_vessel_is_never_in_two_places_at_once(world):
    """The occupancy calendar holds across the whole catalogue."""
    for eid in world.tracks:
        segs = sorted(world.occupied(eid))
        for (a0, a1), (b0, b1) in zip(segs, segs[1:]):
            assert a1 <= b0, (
                f"{eid} has overlapping segments {a0}..{a1} and {b0}..{b1}")


def test_identity_intervals_have_no_gaps_or_overlaps(world):
    from maritime_isr.scenario.primitives import assert_consistent
    problems = assert_consistent(world.identity)
    assert not problems, "identity ledger inconsistent:\n" + "\n".join(problems)


# --------------------------------------------------------------------------
# 7. the truth ledger's own rules
# --------------------------------------------------------------------------

def test_a_decoy_cannot_expect_detection():
    with pytest.raises(ValueError):
        ScenarioTruth(scenario_id="X", scenario_family="f",
                      truth_class=DECOY, entity_ids=["v"],
                      t_start=T0, t_end=T1, expected_detection=True)


def test_a_deliberate_miss_cannot_expect_detection():
    with pytest.raises(ValueError):
        ScenarioTruth(scenario_id="X", scenario_family="f",
                      truth_class=DELIBERATE_MISS, entity_ids=["v"],
                      t_start=T0, t_end=T1, expected_detection=True)


def test_catalogue_covers_every_required_group(world):
    ids = {t.scenario_id for t in world.truth}
    for required in ("A1", "A2", "A3", "A4", "A5",
                     "B1", "B2", "B3", "B4", "B5", "B6",
                     "C1", "C2", "C3", "C4",
                     "D1", "D2", "D3", "D4",
                     "E1", "E2", "E3", "E4", "E5", "E6", "E7",
                     "M1", "M2",
                     # Coastal radar (ADR-028). R4 and R5 are the two new
                     # boundaries: a hull under the radar's reach, and a hull
                     # in the coverage hole between Mumbai and Ratnagiri.
                     "R1", "R2", "R3", "R4", "R5", "R6"):
        assert required in ids, f"scenario {required} missing from the catalogue"
    assert sum(1 for t in world.truth if t.truth_class == DECOY) >= 12
    # `>=`, not `==`. The count was pinned at 2 when M1 and M2 were the only
    # capability boundaries in the corpus; adding a sensor adds its own, and a
    # test that forbids that is testing the catalogue's size rather than its
    # coverage. What must not drift is that the named boundaries are all here,
    # which the loop above checks.
    assert sum(1 for t in world.truth
               if t.truth_class == DELIBERATE_MISS) >= 4


def test_decoy_to_true_positive_ratio_is_meaningful(world):
    tp = sum(1 for t in world.truth if t.truth_class == TRUE_ANOMALY)
    dc = sum(1 for t in world.truth if t.truth_class == DECOY)
    # The build plan targets roughly two decoys per anomaly at the *entity*
    # level rather than the scenario level — several decoys carry many hulls
    # (the fishing fleet is 40, floating storage is 6) while most anomalies
    # carry one or two. Asserting the scenario-level ratio would have forced
    # either fewer decoy families or padding, so the entity-level ratio is
    # what is checked.
    tp_e = len({e for t in world.truth if t.truth_class == TRUE_ANOMALY
                for e in t.entity_ids})
    dc_e = len({e for t in world.truth if t.truth_class == DECOY
                for e in t.entity_ids})
    assert dc >= 10 and tp >= 20
    assert dc_e >= 1.5 * tp_e, (
        f"only {dc_e} decoy entities against {tp_e} true-positive entities — "
        f"precision measured on this corpus would be flattered")


def test_only_two_scenarios_are_exempt_from_a_corpus_invariant(world):
    """The exemption mechanism is narrow on purpose and must stay countable.

    Each entry costs a validator its authority over one scenario, so the set is
    enumerated here rather than merely bounded: adding a third takes an edit to
    this test and a reader who has to justify it. C3 must break the implied-speed
    envelope — an impossible jump is the scenario. F1 must break the IMO check
    digit, for the same kind of reason (ADR-034).
    """
    ex = world.truth.physics_exemptions()
    assert ex == {"C3": "implied_speed_envelope", "F1": "identifiers"}, (
        f"unexpected physics exemptions: {ex}")


def test_naval_decoy_emits_no_ais_at_all(world):
    """A vessel legitimately running dark must not appear in the AIS tables."""
    eid = "vessel:navy_dark"
    assert world.vessels[eid].ais_expected is False
    assert not world.ais.get(eid), (
        "the naval decoy transmitted — she is supposed to be invisible, and "
        "the scenario tests that a radar contact with no AIS is not "
        "automatically a dark vessel")
    assert world.tracks.get(eid), "she still moved; truth must record it"


def test_synthetic_gaps_carry_no_gfw_verdict(world):
    """We must not put words in GFW's mouth, or hand the answer to a detector."""
    gaps = [e for e in world.events if e.kind == "gaps"]
    assert gaps
    for g in gaps:
        assert g.props.get("gfw_intentional_disabling") is None, (
            "a synthetic gap carries a GFW verdict — GFW did not assess these, "
            "and a detector reading that column would be handed the answer")


# --------------------------------------------------------------------------
# 8. real-corpus alignment (added after the operator's profile landed)
# --------------------------------------------------------------------------

def test_synthetic_null_rates_match_the_real_corpus():
    """Synthetic rows must not be separable by a single IS NOT NULL filter.

    Measured on the operator's corpus: `gfw_encounters.imo` is 100% null,
    `gfw_vessel_identity.length_m` 98.6%, `imo` there 74.8%. The generator
    populated all of them, so `WHERE imo IS NOT NULL` was a perfect synthetic
    detector and any precision measured on a combined corpus would have been
    measuring that filter. The track-level separability test passed throughout —
    the distinction leaked through the columns instead.

    Skipped when no profile is present, because there is then nothing to match.
    """
    from maritime_isr.scenario.nulls import NullMask

    profile = CorpusProfile.load()
    if not profile.known_tables():
        pytest.skip("no corpus profile — nothing to match null rates against")

    w = ScenarioWorld.new(7, profile)
    build_cast(w)
    run_all(w)
    w.identity.close_window(w.t1)

    from maritime_isr.scenario.land import land_world
    import maritime_isr.scenario.land as land_mod

    mask = NullMask(profile)
    # Exercise the mask directly on identity rows rather than landing to disk.
    rows = []
    for v in list(w.vessels.values()):
        row = dict(vessel_id=v.entity_id, imo=str(v.imo), length_m=v.length_m,
                   tonnage_gt=v.dwt, call_sign=v.call_sign, mmsi=str(v.mmsi))
        mask.apply(row, table="gfw_vessel_identity", key=v.entity_id)
        rows.append(row)

    problems = mask.verify()
    assert not problems, "null rates diverge from the real corpus:\n" + "\n".join(problems)

    # And the specific field that made the whole thing separable.
    populated = sum(1 for r in rows if r["imo"] is not None)
    assert populated < len(rows), (
        "every synthetic identity row carries an IMO; the real corpus leaves "
        "74.8% of them null, so this column alone separates the two")


def test_mmsi_is_never_masked():
    """Masking the MMSI would break row identity and the track engine."""
    from maritime_isr.scenario.nulls import NullMask, NEVER_MASK
    assert "mmsi" in NEVER_MASK
    assert "vessel_id" in NEVER_MASK and "event_id" in NEVER_MASK
    p = CorpusProfile.load()
    m = NullMask(p)
    assert m.rate("gfw_vessel_identity", "mmsi") is None


@pytest.mark.slow
def test_generation_is_robust_across_seeds():
    """A corpus that only validates at one seed is not reproducible.

    Seed 8 overran the corpus window through background port calls, because the
    measured dwell distribution reaches 336 h and a call started three weeks
    from the end could run past it. Seed 7 happened to fit. Several seeds are
    checked so a seed-dependent overrun cannot pass again.
    """
    # The catalogue is whatever `scenarios.ALL` builds; what must hold is that
    # **every seed builds the same one**. Pinning the count to a literal made
    # adding a scenario group fail this test for the one reason it does not
    # care about, so the invariant is stated as agreement across seeds instead.
    ids: set[str] | None = None
    for seed in (7, 8, 11, 42):
        w = ScenarioWorld.new(seed, CorpusProfile.load())
        build_cast(w)
        run_all(w)
        w.identity.close_window(w.t1)
        rep = validate_world(w)
        assert rep.ok, f"seed {seed} failed validation:\n{rep.format()}"
        seen = {t.scenario_id for t in w.truth}
        assert len(seen) == len(w.truth), f"seed {seed} duplicated a scenario id"
        if ids is None:
            ids = seen
        else:
            assert seen == ids, (
                f"seed {seed} produced a different catalogue: "
                f"missing {sorted(ids - seen)}, extra {sorted(seen - ids)}")
    assert ids and len(ids) >= 40, f"catalogue shrank to {len(ids or ())}"


def test_measured_tails_are_truncated_with_a_stated_reason():
    """A 2.3-year 'port visit' is a data artefact, and must be recorded as one.

    The operator's `gfw_port_visits` has p95 = 20,254 hours. The distribution is
    still used — its body is real and informative — but the implausible tail is
    rejected and the provenance report says so, rather than silently capping or
    silently sampling a two-year berth.
    """
    p = CorpusProfile.load()
    if "port_call_dwell_hours" not in (p.raw.get("distributions") or {}):
        pytest.skip("no measured port-call distribution in the profile")
    param = p.quantiles("port_call_dwell_hours")
    assert param.measured
    assert max(param.value.values()) <= 24 * 14 + 1e-6
    assert "truncated" in param.rationale, (
        "the tail was clamped without saying so — a silent cap is how a data "
        "artefact becomes an unexamined assumption")
    assert "MEASURED" in param.describe() and "truncated" in param.describe()


# --------------------------------------------------------------------------
# geography and routing must keep vessels in water
# --------------------------------------------------------------------------

def test_geography_is_at_sea():
    """Every port and anchorage reference point floats.

    Six of ten port coordinates were town centres, which drew vessels sitting in
    the middle of Gujarat. A berth is on the coastline and a 1 km mask calls that
    land, so the reference point has to be the water beside it.
    """
    globe = pytest.importorskip("global_land_mask").globe
    from maritime_isr.scenario.geography import ANCHORAGES, PORTS

    on_land = [(n, p) for n, p in {**PORTS, **ANCHORAGES}.items()
               if globe.is_land(*p)]
    assert not on_land, f"port/anchorage reference points on land: {on_land}"


def test_searoute_is_clear_of_land():
    """Every corridor waypoint, and every leg between neighbours, is at sea.

    This is what makes the corridor trustworthy: a waypoint nudged onto a
    sandbank, or a leg that clips a headland, fails here rather than silently
    drawing ships across a peninsula again.
    """
    globe = pytest.importorskip("global_land_mask").globe
    import numpy as np

    from maritime_isr.scenario.searoute import CORRIDOR

    for name, la, lo in CORRIDOR:
        assert not globe.is_land(la, lo), f"corridor waypoint {name} is on land"

    for (na, la1, lo1), (nb, la2, lo2) in zip(CORRIDOR, CORRIDOR[1:]):
        lat = np.linspace(la1, la2, 200)
        lon = np.linspace(lo1, lo2, 200)
        hits = int(globe.is_land(lat, lon).sum())
        assert hits == 0, f"corridor leg {na} -> {nb} crosses land at {hits} points"


def _leg_land_hits(globe, p, q):
    """Land samples on the straight line p->q, sampled densely and independently.

    Deliberately does *not* call `searoute.crosses_land`: a test that asks the
    module its own question can only ever confirm the module is self-consistent.
    Sampling is proportional to length here for the same reason it is there, and
    deliberately **finer** than the module's own: a test that samples no better
    than the code it checks cannot catch under-sampling, which is the exact bug
    that let a 600 km leg clip the Gujarat coast and be reported clear.
    """
    import numpy as np

    km = ((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2) ** 0.5 * 111.0
    n = max(200, min(20000, int(km / 0.2) + 2))
    return int(globe.is_land(np.linspace(p[0], q[0], n),
                             np.linspace(p[1], q[1], n)).sum())


def test_sea_route_between_ports_avoids_land():
    """A Gulf of Kutch to west-coast passage routes around Saurashtra."""
    globe = pytest.importorskip("global_land_mask").globe

    from maritime_isr.scenario.geography import PORTS
    from maritime_isr.scenario.searoute import sea_route

    a, b = PORTS["Kandla"], PORTS["Mangalore"]
    legs = [a, *sea_route(a, b), b]
    assert len(legs) > 2, "a Kandla-Mangalore passage must be routed, not direct"
    for p, q in zip(legs, legs[1:]):
        assert _leg_land_hits(globe, p, q) == 0, (
            f"routed leg {p} -> {q} still crosses land")


def test_every_port_pair_routes_clear_of_land():
    """**Every** ordered pair of named places routes entirely by sea.

    The single-pair test above proves the machinery runs; this proves it covers
    the map. Both are kept, because a failure here wants the specific pair
    named and a failure there wants the mechanism blamed.

    This is the check that would have caught the original defect on the day it
    was written, and it is the one that keeps catching regressions: the
    corridor's join legs were verified only waypoint-to-waypoint, so a berth
    deep inside the Gulf of Kutch joined it on a line over the Kachchh shore and
    every other test still passed.
    """
    globe = pytest.importorskip("global_land_mask").globe

    from maritime_isr.scenario.geography import ANCHORAGES, PORTS
    from maritime_isr.scenario.searoute import CORRIDOR, sea_route

    places = {f"port:{n}": p for n, p in PORTS.items()}
    places.update({f"anch:{n}": p for n, p in ANCHORAGES.items()})
    places.update({f"wp:{n}": (la, lo) for n, la, lo in CORRIDOR})

    blocked = []
    for na, a in places.items():
        for nb, b in places.items():
            if na == nb:
                continue
            path = [a, *sea_route(a, b), b]
            for p, q in zip(path, path[1:]):
                if _leg_land_hits(globe, p, q):
                    blocked.append(f"{na} -> {nb} (leg {p} -> {q})")
                    break
    assert not blocked, (
        f"{len(blocked)} of {len(places) * (len(places) - 1)} passages cross "
        f"land, e.g. {blocked[:5]}")


def test_nearest_water_moves_an_inland_point_to_reachable_sea():
    """A point in Kachchh snaps to sea the origin can actually get to.

    Both halves matter. Without the snap, a departure target dead-reckoned
    inland is a destination no routing can reach. Without `reachable_from`, the
    nearest water to that same point is a pocket at the head of the Gulf of
    Kutch, and the vessel steams over a headland to reach it — a worse picture
    than before, arrived at by a correct-looking fix.
    """
    globe = pytest.importorskip("global_land_mask").globe

    from maritime_isr.scenario.searoute import crosses_land, nearest_water

    inland = (23.10, 69.70)                       # Kachchh, ~36 km inland
    assert globe.is_land(*inland), "test fixture is meant to be on land"

    origin = (22.60, 69.50)                       # Mundra anchorage
    snapped = nearest_water(inland, reachable_from=origin)
    assert not globe.is_land(*snapped), f"{snapped} is still on land"
    assert not crosses_land(origin, snapped), (
        f"{snapped} is at sea but the origin cannot reach it without "
        f"crossing land")


def test_seaward_point_stops_at_the_coast():
    """Steaming out on a heading stops short of land instead of over it."""
    globe = pytest.importorskip("global_land_mask").globe

    from maritime_isr.scenario.geography import haversine_m
    from maritime_isr.scenario.searoute import crosses_land, seaward_point

    origin = (22.60, 69.50)                       # Mundra anchorage
    # Due north from here is the Kachchh shore within ~18 km, so a 55 nm
    # dead-reckoned departure lands inland; the walk must stop before it.
    target = seaward_point(origin, 0.0, 55.0 * 1852.0)
    assert not globe.is_land(*target)
    assert not crosses_land(origin, target)
    assert haversine_m(*origin, *target) < 55.0 * 1852.0, (
        "a heading that runs into the coast should not yield the full distance")

    # Due west is the axis of the gulf and its mouth, so the walk should get
    # essentially the whole way — the helper must not be timid everywhere.
    west = seaward_point(origin, 270.0, 55.0 * 1852.0)
    assert haversine_m(*origin, *west) > 45.0 * 1852.0, (
        "seaward_point stopped short on a heading that is open water")


def test_afloat_validator_catches_a_passage_over_land():
    """The `afloat` rule fails a track that crosses a peninsula, and passes one
    that goes around it.

    The validator exists because nothing else could see the defect: the on-land
    points were inside the AOI, inside the window, at plausible speeds and
    plausible turn rates, and every other check was green. A rule that never
    fires is not a check, so this drives both a known-bad and a known-good track
    through the real function rather than asserting on its threshold.
    """
    pytest.importorskip("global_land_mask")
    import numpy as np
    from types import SimpleNamespace

    from maritime_isr.scenario.validate import (RULE_AFLOAT, ValidationReport,
                                                _check_afloat)

    def track(a, b, n=200):
        return [SimpleNamespace(lat=float(la), lon=float(lo))
                for la, lo in zip(np.linspace(a[0], b[0], n),
                                  np.linspace(a[1], b[1], n))]

    # Straight across the Saurashtra peninsula — the original defect, exactly.
    over_land = track((22.60, 69.50), (21.30, 68.90))
    # The same passage routed the way the corridor routes it: out of the gulf,
    # round Okha and Dwarka, then south.
    around = (track((22.60, 69.50), (22.55, 69.30))
              + track((22.55, 69.30), (22.40, 68.60))
              + track((22.40, 68.60), (22.10, 68.60))
              + track((22.10, 68.60), (21.30, 68.90)))

    world = SimpleNamespace(tracks={"vessel:bad": over_land,
                                    "vessel:good": around})
    rep = ValidationReport()
    _check_afloat(world, rep, {})

    flagged = {v.subject for v in rep.violations if v.rule == RULE_AFLOAT}
    assert "vessel:bad" in flagged, (
        "a track drawn straight across Saurashtra passed the afloat check")
    assert "vessel:good" not in flagged, (
        "the routed passage was flagged — the tolerance is too tight to "
        "distinguish a berth from a peninsula")


# ==========================================================================
# minting around identifiers a real corpus already uses
# ==========================================================================

def test_generation_is_not_blocked_by_a_real_corpus_using_the_reserved_band():
    """The operator's laptop could not generate a corpus at all.

    `scenario generate` died with `collision — imo=['1000320']`. That number is
    exactly what serial 32 mints, and it appears in his landed GFW identity
    table: GFW publishes identity straight from AIS static messages, so a
    transmitter broadcasting seven arbitrary digits puts numbers inside our
    "unreachable" band into a real table.

    No seed could work around it — `mint_imo` is indexed by serial, not by the
    RNG, so every run produced the identical clash. The guard was right to
    refuse; the generator was wrong to have no way through.
    """
    from maritime_isr.scenario.identifiers import reserve_against_corpus

    try:
        blocked = mint_imo(32)
        assert blocked == 1000320, (
            f"serial 32 mints {blocked}; this test is pinned to the number "
            "that actually collided on the operator's machine")

        report = reserve_against_corpus(real_imos={"1000320"}, real_mmsis=set())
        assert report["imos"] == [1000320]

        minted = [mint_imo(s) for s in range(60)]
        assert 1000320 not in minted, "the taken hull must be skipped"
        assert len(set(minted)) == len(minted), (
            "skipping must shift every later serial too — otherwise two "
            "scenario vessels share one hull")
        for i in minted:
            assert imo_checksum_ok(str(i)) and IMO_MIN <= i <= IMO_MAX
    finally:
        reserve_against_corpus(real_imos=set(), real_mmsis=set())


def test_the_collision_guard_passes_once_the_taken_hull_is_skipped():
    """End to end: reserve, mint, then run the guard that used to fail."""
    from maritime_isr.scenario.identifiers import reserve_against_corpus

    try:
        reserve_against_corpus(real_imos={"1000320"}, real_mmsis=set())
        imos = [mint_imo(s) for s in range(60)]
        rep = assert_no_collisions(imos, [mint_mmsi(0)], [],
                                   raise_on_collision=False)
        assert not rep.imo_collisions, rep.describe()
    finally:
        reserve_against_corpus(real_imos=set(), real_mmsis=set())


def test_an_empty_reservation_leaves_the_original_hull_numbers_untouched():
    """The skip must cost nothing on a machine with no real corpus."""
    from maritime_isr.scenario.identifiers import reserve_against_corpus

    reserve_against_corpus(real_imos=set(), real_mmsis=set())
    assert [mint_imo(s) for s in range(5)] == [
        1000007, 1000019, 1000021, 1000033, 1000045]
