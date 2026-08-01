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

from . import background, decoys, group_a, group_b, group_c, group_d, group_e
from . import misses

#: Execution order. Background first, then the spine's early identity events,
#: then everything else roughly in time order.
ALL = (
    *background.SCENARIOS,
    *group_b.SCENARIOS,      # B3 cascade opens in week 1
    *group_a.SCENARIOS,
    *group_c.SCENARIOS,
    *group_d.SCENARIOS,
    *group_e.SCENARIOS,
    *decoys.SCENARIOS,
    *misses.SCENARIOS,
)


def run_all(world) -> list[str]:
    """Run every scenario against the world. Returns the names that ran."""
    ran = []
    for fn in ALL:
        fn(world)
        ran.append(fn.__name__)
    return ran
