"""Group D — graph and ownership structure.

The corporate skeleton is built once in `cast.build_ownership`; these scenarios
give the vessels enough movement to exist in the behavioural tables and then
record what the structure is supposed to mean.

**Two of these four are traps, and they are the more valuable pair.** D1 is a
genuine convergence that risk should propagate across. D3 and D4 are shared
service providers — a technical manager and a port agent — and sharing one is
not evidence of anything. A corporate service provider with one bad client has
dozens of ordinary ones, and treating the shared node as guilt-by-association
would light up the whole book of business.

So D3 and D4 are recorded with `expected_detection=False` on the alerting path
while remaining legitimately *surfaceable* as candidates. The distinction the
system has to make is between "here is a link worth a human minute" and "here is
a finding" — and getting that wrong in the permissive direction is how an
analyst learns to stop opening the queue.
"""
from __future__ import annotations

from ..cast import (ORG_BENEFICIAL, ORG_MANAGER_1, ORG_MANAGER_2,
                    ORG_PHOENIX_NEW, ORG_PHOENIX_OLD)
from ..geography import PORTS
from ..primitives.port_call import build_port_call
from ..truth import (DECOY, FAMILY_GRAPH, TRUE_ANOMALY, ScenarioTruth)
from ..world import ScenarioWorld, week
from .common import V, add_port_visit, emit


def _routine_voyage(world: ScenarioWorld, key: str, port: str, t_start,
                    from_port: str = "Karachi", *, wait_h: float = 6.0,
                    dwell_h: float = 24.0):
    """Ordinary movement so a graph-scenario vessel is not a floating node."""
    r = world.rng
    v = V(world, key)
    pts, spec = build_port_call(
        v, port, arrive_from=PORTS[from_port], t_start=t_start, rng=r,
        anchorage_hours=wait_h, berth_hours=dwell_h)
    emit(world, key, pts)
    return spec


# --------------------------------------------------------------------------
# D1 — ownership convergence
# --------------------------------------------------------------------------

def d1_ownership_convergence(world: ScenarioWorld) -> None:
    spine = V(world, "spine")
    other = V(world, "converge_b")

    spec = _routine_voyage(world, "converge_b", "Kandla", week(6, hours=8),
                           from_port="Mangalore", wait_h=11.0, dwell_h=31.0)
    add_port_visit(world, "D1", "converge_b", spec)

    chain = world.corporate.beneficial_owner(other.entity_id, max_hops=3)

    world.truth.add(ScenarioTruth(
        scenario_id="D1", scenario_family=FAMILY_GRAPH,
        truth_class=TRUE_ANOMALY,
        entity_ids=[spine.entity_id, other.entity_id, ORG_BENEFICIAL],
        t_start=week(6), t_end=spec.t_depart, expected_detection=True,
        expected_anomaly_types=["port_risk_propagation"],
        notes=(f"Two vessels with no direct interaction and different "
               f"immediate operators share a designated beneficial owner two "
               f"hops up ({ORG_BENEFICIAL}; chain from converge_b: "
               f"{' -> '.join(chain) or 'none'}). Unreachable in one hop, "
               f"which is what makes it a graph finding rather than a lookup. "
               f"Risk should propagate to the second vessel from the first.")))


# --------------------------------------------------------------------------
# D2 — corporate phoenixing
# --------------------------------------------------------------------------

def d2_corporate_phoenix(world: ScenarioWorld) -> None:
    c = world.corporate
    old, new = c.orgs[ORG_PHOENIX_OLD], c.orgs[ORG_PHOENIX_NEW]
    same_agent = c.shares_agent(ORG_PHOENIX_OLD, ORG_PHOENIX_NEW)
    same_addr = c.shares_address(ORG_PHOENIX_OLD, ORG_PHOENIX_NEW)
    if not (same_agent and same_addr):
        raise AssertionError("D2 requires the successor to share agent+address")

    fleet = sorted({e.src for e in c.edges if e.dst == ORG_PHOENIX_NEW
                    and e.src.startswith("vessel:")})

    world.truth.add(ScenarioTruth(
        scenario_id="D2", scenario_family=FAMILY_GRAPH,
        truth_class=TRUE_ANOMALY,
        entity_ids=[ORG_PHOENIX_OLD, ORG_PHOENIX_NEW, *fleet],
        t_start=old.dissolved, t_end=week(8, hours=100),
        expected_detection=True,
        expected_anomaly_types=["identity_then_anomaly"],
        notes=(f"{old.name} is designated, dissolves on "
               f"{old.dissolved:%Y-%m-%d}, and {new.name} incorporates "
               f"{(new.incorporated - old.dissolved).days} day(s) later at the "
               f"same address ({new.address}) through the same registered "
               f"agent ({new.registered_agent}), retaining "
               f"{len(fleet)} hull(s). The agent and address are nodes, not "
               f"strings, so the link is a traversal rather than a fuzzy "
               f"match — which is the whole reason they are modelled that way.")))


# --------------------------------------------------------------------------
# D3 — shared technical manager (CANDIDATE, never a finding)
# --------------------------------------------------------------------------

def d3_manager_linkage(world: ScenarioWorld) -> None:
    c = world.corporate
    managed = sorted({e.src for e in c.edges
                      if e.dst == ORG_MANAGER_1 and e.src.startswith("vessel:")})
    innocent = [m for m in managed if not m.endswith("brazen")]

    for i, key in enumerate(("managed_1", "managed_2", "managed_3")):
        spec = _routine_voyage(world, key, ("Mundra", "Kochi", "JNPT")[i],
                               week(3, hours=20 + i * 26),
                               from_port=("Karachi", "Mangalore", "Karachi")[i])
        add_port_visit(world, "D3", key, spec)

    world.truth.add(ScenarioTruth(
        scenario_id="D3", scenario_family=FAMILY_GRAPH,
        truth_class=DECOY,
        entity_ids=[ORG_MANAGER_1, *managed],
        t_start=week(3), t_end=week(5), expected_detection=False,
        notes=(f"{len(innocent)} unrelated vessels share technical manager "
               f"{c.orgs[ORG_MANAGER_1].name}, which also manages a designated "
               f"hull. This must surface as a CANDIDATE for review and must "
               f"never become a finding: a ship manager with one designated "
               f"client is an ordinary ship manager, and its other clients are "
               f"innocent. Alerting on all of them is guilt by association and "
               f"would fire on most of the world's managed tonnage.")))


# --------------------------------------------------------------------------
# D4 — shared port agent
# --------------------------------------------------------------------------

def d4_port_agent_convergence(world: ScenarioWorld) -> None:
    c = world.corporate
    shared = sorted({e.src for e in c.edges
                     if e.dst == ORG_MANAGER_2 and e.src.startswith("vessel:")})

    s1 = _routine_voyage(world, "agent_share_1", "Kochi", week(4, hours=14),
                         from_port="Mumbai")
    s2 = _routine_voyage(world, "agent_share_2", "Mundra", week(7, hours=22),
                         from_port="Karachi")
    add_port_visit(world, "D4", "agent_share_1", s1)
    add_port_visit(world, "D4", "agent_share_2", s2)

    world.truth.add(ScenarioTruth(
        scenario_id="D4", scenario_family=FAMILY_GRAPH,
        truth_class=DECOY, entity_ids=[ORG_MANAGER_2, *shared],
        t_start=week(4), t_end=s2.t_depart, expected_detection=False,
        notes=("Two vessels share a port agent and never touch — different "
               "ports, three weeks apart, no encounter, no common voyage. A "
               "shared agent is a commercial convenience and is one of the "
               "densest, least informative edges in any maritime graph. If "
               "this fires, the graph is finding structure rather than "
               "behaviour, and every port in the AOI becomes a hub.")))


SCENARIOS = (
    d1_ownership_convergence,
    d2_corporate_phoenix,
    d3_manager_linkage,
    d4_port_agent_convergence,
)
