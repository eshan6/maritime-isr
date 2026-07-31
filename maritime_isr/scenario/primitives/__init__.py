"""Layer 1 — the primitives every scenario is composed from.

Nothing in this package knows what a scenario *means*. `build_rendezvous` does
not know whether it is building an illicit transfer or a bunkering; `build_gap`
does not encode why the transponder stopped. That ignorance is deliberate and is
what makes the corpus measurable: true positives and decoys come out of the same
code with the same fidelity, so a detector that separates them must be
separating them on behaviour.

Layer 2 (`scenario.scenarios`) supplies the meaning, and only Layer 2 writes to
`scenario_truth`.
"""
from .ais import (AisReport, Suppression, emit_ais, median_interval_s,
                  report_intervals_s, transmit_interval_s)
from .encounter import (RendezvousSpec, build_rendezvous, coherent,
                        measure_rendezvous, near_miss)
from .gap import (CAUSES, EQUIPMENT_FAILURE, INTENTIONAL, OUT_OF_COVERAGE,
                  RECEIVER_SHADOW, GapSpec, build_gap, degrade_ramp,
                  implied_speed_across_gap, plausible_placement)
from .identity import (IDENTITY_FIELDS, IdentityEvent, IdentityInterval,
                       IdentityLedger, assert_consistent)
from .org import (CorporateWorld, Organization, OwnershipEdge,
                  build_agents_and_addresses)
from .port_call import (PortCallSpec, build_anchorage_stay, build_port_call,
                        sequence_ports, transit_between)
from .track import (Leg, TrackPoint, VoyagePlan, generate_track,
                    implied_speed_kn, point_at, sanity_check)
from .vessel import CLASSES, TANKER_CLASSES, SyntheticVessel, make_vessel

__all__ = [
    "AisReport", "Suppression", "emit_ais", "median_interval_s",
    "report_intervals_s", "transmit_interval_s",
    "RendezvousSpec", "build_rendezvous", "coherent", "measure_rendezvous",
    "near_miss",
    "CAUSES", "EQUIPMENT_FAILURE", "INTENTIONAL", "OUT_OF_COVERAGE",
    "RECEIVER_SHADOW", "GapSpec", "build_gap", "degrade_ramp",
    "implied_speed_across_gap", "plausible_placement",
    "IDENTITY_FIELDS", "IdentityEvent", "IdentityInterval", "IdentityLedger",
    "assert_consistent",
    "CorporateWorld", "Organization", "OwnershipEdge",
    "build_agents_and_addresses",
    "PortCallSpec", "build_anchorage_stay", "build_port_call",
    "sequence_ports", "transit_between",
    "Leg", "TrackPoint", "VoyagePlan", "generate_track", "implied_speed_kn",
    "point_at", "sanity_check",
    "CLASSES", "TANKER_CLASSES", "SyntheticVessel", "make_vessel",
]
