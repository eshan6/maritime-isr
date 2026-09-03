"""Layer 2 — scenarios: primitives composed into meaning.

Every scenario is a function taking the world and returning nothing; it appends
tracks, reports, events and **exactly one truth row**. Order matters only where
one scenario depends on another having moved a vessel first — the narrative
spine's port-call laundering (E4) has to run after her phoenix reappearance
(B1), because she sails under the new identity.

`ALL` is the execution order. It is not alphabetical: background first so the
world exists, then the groups in an order that respects the temporal
choreography, then decoys and misses.
"""
from __future__ import annotations

from . import background, commercial_traffic, decoys, group_a, group_b
from . import group_c, group_d, group_e, group_f, group_o, group_p, group_r
from . import group_z
from . import fleet_traffic, group_w
from . import misses

#: Execution order. Background first, then the spine's early identity events,
#: then everything else roughly in time order.
ALL = (
    *background.SCENARIOS,
    *commercial_traffic.SCENARIOS,   # the ordinary fleet the scenarios hide in
    *group_b.SCENARIOS,      # B3 cascade opens in week 1
    *group_a.SCENARIOS,
    *group_c.SCENARIOS,
    *group_d.SCENARIOS,
    *group_e.SCENARIOS,
    # The coastal-radar group runs after the offshore groups because several of
    # its hulls are new and none of them is shared — placement is unconstrained,
    # and running it last keeps the existing choreography exactly as it was.
    *group_r.SCENARIOS,
    # The zone group runs after the radar group for the same reason: its hulls
    # are new and unshared, so placement is unconstrained, and appending keeps
    # the existing temporal choreography byte-identical.
    *group_z.SCENARIOS,
    # The factor group runs after the zone group for the third instance of
    # the same reason: every hull in it is new and unshared, so appending
    # leaves the existing temporal choreography byte-identical.
    *group_f.SCENARIOS,
    # The electro-optical group runs after the factor group for the fourth
    # instance of the same reason: every hull in it is new and unshared, so
    # appending leaves the existing temporal choreography byte-identical.
    *group_o.SCENARIOS,
    *decoys.SCENARIOS,
    *misses.SCENARIOS,
    # The wider fleet runs after every named group and before the paperwork.
    #
    # **After**, for the fifth instance of the argument above and the strongest
    # one: every hull it touches is new and unshared, and — unlike the groups
    # above — it draws from its own derived RNG rather than `world.rng`, so
    # inserting it here leaves the existing choreography byte-identical rather
    # than merely equivalent. Appending four hundred hulls through the shared
    # stream would have re-rolled the whole corpus behind them (see
    # `scenario/fleet.py`).
    #
    # **Before the paperwork**, and that is load-bearing: `group_p` files one
    # pre-arrival notification per port call in the window, so a fleet that
    # arrived after it had run would land four hundred berthings with no
    # notification on file — which is precisely the P3 contradiction, fired
    # four hundred times by our own ordering rather than by anything a vessel
    # did.
    *fleet_traffic.SCENARIOS,
    *group_w.SCENARIOS,
    # The paperwork group runs LAST and that is load-bearing, not stylistic:
    # it builds one notification per port call and therefore needs every port
    # call the corpus will ever contain to exist already. Running it earlier
    # would file for the arrivals authored before it and silently miss the
    # rest, which reads as a corpus full of unnotified arrivals (ADR-036).
    *group_p.SCENARIOS,
)


def run_all(world) -> list[str]:
    """Run every scenario against the world. Returns the names that ran."""
    ran = []
    for fn in ALL:
        fn(world)
        ran.append(fn.__name__)
    return ran
