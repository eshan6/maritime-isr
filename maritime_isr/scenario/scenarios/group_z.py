"""Group Z — the maritime zone layer, and what it can and cannot decide.

The zone layer (ADR-030) unlocks four named analyses. Three of them make
judgements and therefore need ground truth; the fourth is a query and needs
none. This group authors the three, plus two decoys and one deliberate miss,
because a group of true positives alone measures recall and says nothing about
precision — and precision is the binding constraint here (ADR-004).

  Z1  a hull that has worked this coast for weeks turns up somewhere new
      — the maiden visit, and the reason the rule needs a history qualifier
  Z2  a coastal passage well outside every established corridor for a day
      — lane deviation
  Z3  stopped for nine hours inside territorial waters and clear of every
      facility — anchoring outside port limits
  Z4  DECOY: a liner on her ordinary rotation, calling at four ports she has
      called at before. Nothing here is new and nothing should fire.
  Z5  DECOY: a bulker riding out weather at a designated anchorage for eleven
      hours. Stopped, for a long time, in territorial waters — and lawful,
      which is exactly what the anchorage layer exists to protect
  Z6  DECOY: stopped for eight hours 40 nm offshore — outside every facility
      and outside any territorial sea. Lawful, and stays lawful the day a real
      boundary is loaded

**Z3 depends on a territorial sea that this project refuses to derive** (see
`zones/derive.py`). On a checkout where nobody has run `maritime-isr ingest
zones` it is unfindable, and the pipeline says so by name rather than letting
it count as a miss the detector earned. That is the difference between "the
rule failed" and "the rule was never given the geometry it needs", and
conflating them would let a missing boundary look like a broken detector for as
long as anybody cared to not look.

Note what is NOT here: a scenario asserting that crossing the IMBL is an
offence. The line is disputed and undelimited seaward of Sir Creek, and a
generated corpus that treated a crossing as ground-truth wrongdoing would bake
a contested legal claim into the answer key — the worst possible place for one,
because the measurement would then reward a detector for making it.
"""
from __future__ import annotations

from ...ports import PORTS
from ..geography import destination
from ..primitives.track import Leg, VoyagePlan, generate_track
from ..truth import (DECOY, FAMILY_BEHAVIOURAL, FAMILY_DECOY, TRUE_ANOMALY,
                     ScenarioTruth)
from ..world import ScenarioWorld, week
from .common import V, emit, hours, schedule_arrival


def _anch(port: str) -> tuple[float, float]:
    """The charted waiting area off a port. Water by definition."""
    from ...ports import ANCHORAGES
    return ANCHORAGES[port]


def _off(port: str, bearing_deg: float, km: float) -> tuple[float, float]:
    """A position `km` seaward of a gazetteer port on a bearing.

    Written against the gazetteer rather than as bare coordinates for the same
    reason group R is written against the station network: a port whose
    coordinate is corrected takes its scenarios with it, and a scenario cannot
    silently drift out of the area it was written to sit in.
    """
    lat, lon = PORTS[port]
    return destination(lat, lon, bearing_deg, km * 1000.0)


# --------------------------------------------------------------------------
# Z1 — the maiden visit
# --------------------------------------------------------------------------

def z1_first_visit_after_a_settled_pattern(world: ScenarioWorld) -> None:
    """Four weeks working Gujarat, then a first-ever call at Mormugao.

    **The scenario is designed to fail the naive version of the rule.** An
    unqualified "no prior presence in this zone" test fires on every vessel the
    first time it is seen anywhere, so it would flag her opening call at Sikka
    too — and every other hull's opening call, 168 of them. What makes this a
    finding is the *pattern before it*: she is a hull we have watched work three
    Gujarat facilities for a month, and then she is somewhere 900 km south she
    has never been.

    So she visits three distinct zones first, deliberately, and the fourth is
    the one that should fire.
    """
    r = world.rng
    v = V(world, "zone_newcomer")
    t0 = week(1, hours=4)

    # Three settled circuits in the Gulf of Kachchh: Sikka, Vadinar, Mundra.
    #
    # **The waiting legs are the scenario, not padding.** The first version
    # strung the calls back to back and the whole "four weeks of settled work"
    # collapsed into four days — a claim the docstring made and the track did
    # not support, which would have made any measurement of a history-dependent
    # rule meaningless. Each circuit now ends with days at anchor, which is what
    # a liner working a rotation actually does between fixtures.
    legs = [
        Leg("transit", target=_off("Sikka", 250.0, 6.0), speed_kn=11.0),
        Leg("moored", duration_h=14.0),
        Leg("transit", target=_anch("Sikka"), speed_kn=9.0),
        Leg("station", duration_h=140.0, radius_m=2500.0),      # ~6 days
        Leg("transit", target=_off("Vadinar", 230.0, 6.0), speed_kn=10.0),
        Leg("moored", duration_h=16.0),
        Leg("transit", target=_anch("Vadinar"), speed_kn=9.0),
        Leg("station", duration_h=150.0, radius_m=2500.0),      # ~6 days
        Leg("transit", target=_off("Mundra", 210.0, 7.0), speed_kn=10.5),
        Leg("moored", duration_h=18.0),
        Leg("transit", target=_anch("Mundra"), speed_kn=9.0),
        Leg("station", duration_h=160.0, radius_m=2500.0),      # ~7 days
        Leg("transit", target=_off("Sikka", 250.0, 6.0), speed_kn=11.0),
        Leg("moored", duration_h=12.0),
        Leg("transit", target=_anch("Sikka"), speed_kn=9.0),
        Leg("station", duration_h=150.0, radius_m=2500.0),      # ~6 days
        # ... and then south, to a coast she has never worked.
        Leg("transit", target=_off("Porbandar", 220.0, 30.0), speed_kn=12.0),
        Leg("transit", target=_off("Mumbai", 250.0, 40.0), speed_kn=12.5),
        Leg("transit", target=_off("Mormugao", 260.0, 8.0), speed_kn=12.0),
        Leg("moored", duration_h=20.0),
    ]
    pts = generate_track(v, VoyagePlan(
        start=_off("Sikka", 250.0, 40.0), start_time=t0, legs=legs), r)
    emit(world, "zone_newcomer", pts)

    world.truth.add(ScenarioTruth(
        scenario_id="Z1", scenario_family=FAMILY_BEHAVIOURAL,
        truth_class=TRUE_ANOMALY,
        entity_ids=[v.entity_id], t_start=t0, t_end=pts[-1].t,
        expected_detection=True,
        expected_anomaly_types=["maiden_zone_visit"],
        notes=("Four weeks of settled Gujarat work, then a first-ever call at "
               "Mormugao 900 km south. The prior pattern is what makes it a "
               "finding rather than a debut; see MAIDEN_MIN_PRIOR_ZONES.")))


# --------------------------------------------------------------------------
# Z2 — off every established route
# --------------------------------------------------------------------------

def z2_well_off_the_lane(world: ScenarioWorld) -> None:
    """A day-long passage in open water between the coastal and deep-sea routes.

    The gap between the west-coast coastal corridor and the Hormuz-Malacca
    track is a few hundred kilometres wide off Maharashtra, and a laden ship
    steaming steadily through the middle of it is not taking either route. She
    stays above the speed floor throughout, so this is a *passage* off-route
    rather than a drift — the loitering rule owns drifting and this one must
    not double-count it.
    """
    r = world.rng
    v = V(world, "off_lane_runner")
    t0 = week(3, hours=9)

    legs = [
        Leg("transit", target=(19.60, 68.90), speed_kn=12.0),
        Leg("transit", target=(18.10, 68.40), speed_kn=12.5),
        Leg("transit", target=(16.60, 68.60), speed_kn=12.0),
        Leg("transit", target=(15.20, 69.30), speed_kn=11.5),
    ]
    pts = generate_track(v, VoyagePlan(
        start=(21.00, 69.20), start_time=t0, legs=legs), r)
    emit(world, "off_lane_runner", pts)

    world.truth.add(ScenarioTruth(
        scenario_id="Z2", scenario_family=FAMILY_BEHAVIOURAL,
        truth_class=TRUE_ANOMALY,
        entity_ids=[v.entity_id], t_start=t0, t_end=pts[-1].t,
        expected_detection=True,
        expected_anomaly_types=["lane_deviation"],
        notes=("Steaming steadily for a day through the gap between the "
               "coastal corridor and the deep-sea track. NOTE: on synthetic "
               "data this measurement is optimistic by construction — the "
               "generator and the lane centrelines share a router, so ordinary "
               "traffic sits on the lanes almost by definition.")))


# --------------------------------------------------------------------------
# Z3 — anchored where she should not be
# --------------------------------------------------------------------------

def z3_anchored_outside_port_limits(world: ScenarioWorld) -> None:
    """Nine hours stopped inside territorial waters, clear of every facility.

    Six nautical miles off the Konkan coast: comfortably inside a 12 nm
    territorial sea, comfortably outside every port area, anchorage and
    terminal in the layer. The shape of a vessel doing something it should have
    declared — waiting to be told where to go, transferring, or simply not
    where its paperwork says.

    **Findable only if a territorial sea has been loaded.** See the module
    docstring: the measurement distinguishes "the rule missed her" from "the
    rule had no boundary to test against", because those are different failures
    and only one of them is the detector's.
    """
    r = world.rng
    v = V(world, "outside_limits")
    t0 = week(5, hours=11)

    hold = destination(17.30, 73.10, 250.0, 11.0 * 1000.0)   # ~6 nm off Konkan
    legs = [
        Leg("transit", target=hold, speed_kn=10.0),
        Leg("station", duration_h=9.0, radius_m=600.0),
        Leg("transit", target=_off("Mumbai", 240.0, 25.0), speed_kn=11.0),
    ]
    pts = generate_track(v, VoyagePlan(
        start=destination(*hold, 200.0, 60.0 * 1000.0), start_time=t0,
        legs=legs), r)
    emit(world, "outside_limits", pts)

    world.truth.add(ScenarioTruth(
        scenario_id="Z3", scenario_family=FAMILY_BEHAVIOURAL,
        truth_class=TRUE_ANOMALY,
        entity_ids=[v.entity_id], t_start=t0, t_end=pts[-1].t,
        expected_detection=True,
        expected_anomaly_types=["anchored_outside_limits"],
        notes=("Nine hours stopped ~6 nm off the Konkan coast, outside every "
               "facility. UNFINDABLE until a territorial sea is loaded — this "
               "project will not derive one (zones/derive.py), so on a bare "
               "checkout this reads as MISSED and the pipeline says why.")))


# --------------------------------------------------------------------------
# Z4 — the settled liner: nothing new, nothing should fire
# --------------------------------------------------------------------------

def z4_settled_rotation(world: ScenarioWorld) -> None:
    """A liner working the same four ports she always works.

    The decoy for the maiden-visit rule, and the one that would catch a naive
    implementation immediately: she visits four zones, repeatedly, and every
    one of them after the first is a *return*. A rule that fired on her would be
    reporting a schedule.
    """
    r = world.rng
    v = V(world, "settled_liner")
    t0 = week(1, hours=10)

    circuit = ["Mundra", "Kandla", "Mundra", "Sikka", "Kandla", "Mundra",
               "Sikka", "Kandla"]
    legs = []
    for port in circuit:
        legs.append(Leg("transit", target=_off(port, 230.0, 7.0), speed_kn=11.0))
        legs.append(Leg("moored", duration_h=11.0))
    pts = generate_track(v, VoyagePlan(
        start=_off("Mundra", 230.0, 45.0), start_time=t0, legs=legs), r)
    emit(world, "settled_liner", pts)

    world.truth.add(ScenarioTruth(
        scenario_id="Z4", scenario_family=FAMILY_DECOY, truth_class=DECOY,
        entity_ids=[v.entity_id], t_start=t0, t_end=pts[-1].t,
        expected_detection=False,
        notes=("Eight calls across three Gulf of Kachchh facilities she has "
               "used before. Every visit after the first is a return; a maiden-"
               "visit rule that fires here is reporting a timetable.")))


# --------------------------------------------------------------------------
# Z5 — lawfully stopped, for a long time, in territorial waters
# --------------------------------------------------------------------------

def z5_waiting_at_the_anchorage(world: ScenarioWorld) -> None:
    """Eleven hours at the Kandla anchorage. Stopped, inside, and lawful.

    The decoy for the anchoring rule, and the reason the anchorage layer is a
    separate layer rather than a wider port radius. This vessel matches every
    condition the rule tests except the one that matters — she is in a
    designated waiting area. Kandla's anchorage sits 30 km from the Kandla berth
    coordinate, so a port radius alone can never reach it, which is exactly the
    defect that put 29 merchants into the loitering queue on 2026-08-01.
    """
    r = world.rng
    v = V(world, "anchorage_waiter")
    t0 = schedule_arrival(world, "anchorage_waiter",
                          _off("Kandla", 220.0, 20.0), week(5, hours=3))

    from ...ports import ANCHORAGES
    a_lat, a_lon = ANCHORAGES["Kandla"]
    legs = [
        Leg("transit", target=(a_lat, a_lon), speed_kn=9.0),
        Leg("station", duration_h=11.0, radius_m=900.0),
        Leg("transit", target=_off("Kandla", 220.0, 6.0), speed_kn=8.0),
        Leg("moored", duration_h=10.0),
    ]
    pts = generate_track(v, VoyagePlan(
        start=_off("Kandla", 220.0, 40.0), start_time=t0, legs=legs), r)
    emit(world, "anchorage_waiter", pts)

    world.truth.add(ScenarioTruth(
        scenario_id="Z5", scenario_family=FAMILY_DECOY, truth_class=DECOY,
        entity_ids=[v.entity_id], t_start=t0, t_end=pts[-1].t,
        expected_detection=False,
        notes=("Eleven hours stopped in the Kandla designated anchorage, then "
               "alongside. Stopped, long, and inside territorial waters — and "
               "entirely ordinary. If the anchorage layer is not consulted "
               "this fires, which is the 2026-08-01 loitering defect reached "
               "from a different direction.")))


# --------------------------------------------------------------------------
# Z6 — stopped, for hours, and nobody's business
# --------------------------------------------------------------------------

def z6_stopped_on_the_high_seas(world: ScenarioWorld) -> None:
    """Eight hours stopped 40 nm off Porbandar — outside any territorial sea.

    The decoy for the *first* condition of the anchoring rule, where Z5 is the
    decoy for the second. Z5 is inside territorial waters and lawful because
    she is in a designated anchorage; this one is outside every facility too,
    and lawful because she is on the high seas. A vessel drifting or working in
    international waters is not anchored outside anybody's port limits, and a
    rule that fired here would be asserting jurisdiction the geometry does not
    support.

    **She is deliberately inside terrestrial AIS reception.** The first draft
    put her off the Makran coast, where nothing hears her — which would have
    made her a miss caused by *reception* rather than by the rule reasoning
    correctly about a boundary. A scenario whose silence has two possible
    causes measures neither, so she was moved to where the evidence exists and
    the only question left is the one under test.

    She is also robust to the boundary arriving: load India's real territorial
    sea tomorrow and she is still 40 nm outside it, so this decoy keeps
    working rather than turning into a false positive the day the layer is
    completed.
    """
    r = world.rng
    v = V(world, "makran_holder")
    t0 = week(6, hours=8)

    hold = _off("Porbandar", 210.0, 74.0)     # ~40 nm SSW of Porbandar
    legs = [
        Leg("transit", target=hold, speed_kn=9.5),
        Leg("station", duration_h=8.0, radius_m=700.0),
        Leg("transit", target=_off("Porbandar", 240.0, 30.0), speed_kn=10.0),
    ]
    pts = generate_track(v, VoyagePlan(
        start=_off("Porbandar", 200.0, 120.0), start_time=t0, legs=legs), r)
    emit(world, "makran_holder", pts)

    world.truth.add(ScenarioTruth(
        scenario_id="Z6", scenario_family=FAMILY_DECOY, truth_class=DECOY,
        entity_ids=[v.entity_id], t_start=t0, t_end=pts[-1].t,
        expected_detection=False,
        notes=("Eight hours stopped ~40 nm off Porbandar, outside every "
               "facility AND outside any territorial sea. Lawful. Tests the "
               "rule's first condition, where Z5 tests its second; stays a "
               "decoy rather than becoming a false positive on the day a real "
               "territorial sea is loaded.")))


SCENARIOS = (
    z1_first_visit_after_a_settled_pattern,
    z2_well_off_the_lane,
    z3_anchored_outside_port_limits,
    z4_settled_rotation,
    z5_waiting_at_the_anchorage,
    z6_stopped_on_the_high_seas,
)
