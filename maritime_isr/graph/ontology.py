"""Ontology v1 (roadmap 4.1) — small, correct, extensible.

The ontology is DATA, not code: types are registered rows in the graph
store's ontology table, versioned, so adding a type is an insert + version
bump — zero downtime, zero recompute of existing edges. The constants below
are v1's seed content; the store owns the live registry. "The schema is a
hypothesis until a navy has argued with it."

Decay policy (roadmap 4.3) is per-edge-type and encodes one distinction
that matters more than the numbers:

  STATE edges  (owned-by, flagged-to, docked-at ...) assert a condition
               that silently rots if never re-observed → half-life decay
               from observed_at, refreshed by re-assertion.
  EVENT edges  (met-with, detected-by, formerly-identified-as ...) record
               something that HAPPENED. Facts don't fade — they recede in
               time-scope. half_life=None, confidence stays at base.

sanctioned-under is an event-like assertion governed by its valid_from/
valid_to interval (as-of dates from the sanctions connector), not decay.
"""
from __future__ import annotations

ONTOLOGY_VERSION = 1

NODE_TYPES_V1 = [
    "vessel", "organization", "person", "port", "voyage", "encounter",
    "detection", "track", "alert", "sensor", "identity", "flag_state",
    "sanctions_authority",
    # A sensed target with no broadcast identity — a coastal-radar track that
    # nothing on AIS explains (ADR-028). Deliberately NOT a `vessel`: it is
    # almost certainly one, but the graph's job is to record what is known, and
    # what is known here is that something of roughly this size was at these
    # positions. Promoting it to a vessel node the moment it appears would put
    # an unnamed hull in the same keyspace as identified ships and let a
    # neighbourhood query return it as if it had a registry entry.
    "contact",
    # A maritime zone (ADR-030): a statutory limit, a facility area, a lane,
    # or an area the operator drew. Deliberately ONE node type for all of them —
    # the requirement is that a drawn box and a declared boundary are the same
    # kind of object as far as the rest of the system is concerned, and giving
    # the operator's polygon its own type would make that false in the one
    # place it is easiest to check.
    "zone",
    # An arrival notification (ADR-036). A node type of its own for the same
    # reason `contact` is one: it is a *document*, not a ship, and the finding
    # that matters most about it is precisely that it names a hull nothing has
    # seen. Attaching that alert to an invented vessel node would put a ship in
    # the graph that no sensor has ever observed — the shadow-stub failure
    # ADR-022 exists to prevent — and would then let a neighbourhood query
    # return a piece of paper as if it were a vessel.
    "notification",
    # An electro-optical capture (ADR-037). Its own type for the same reason
    # `notification` has one: it is an *artifact* — a photograph, or in this
    # build the record of one — and not a ship. Folding it onto the vessel it
    # depicts would lose the two facts that make it evidence: which camera took
    # it and when, and that a capture can depict a target nothing can name. It
    # also lets the graph hold a capture whose frame turned out to be empty,
    # which is a real answer about a radar track and belongs on the record.
    "eo_capture",
]

# name -> dict(src=[...], dst=[...], half_life_days=float|None, kind=state|event)
EDGE_TYPES_V1: dict[str, dict] = {
    "owned-by":      dict(src=["vessel", "organization"], dst=["organization", "person"],
                          half_life_days=365.0, kind="state"),
    "operated-by":   dict(src=["vessel"], dst=["organization", "person"],
                          half_life_days=270.0, kind="state"),
    "flagged-to":    dict(src=["vessel"], dst=["flag_state"],
                          half_life_days=730.0, kind="state"),
    # `contact` appears on the source side of the three sensed-behaviour edges
    # below. A radar contact demonstrably docks, meets and is detected; what it
    # cannot do is own, be flagged, or be sanctioned, because all three of those
    # are assertions about an identity it does not have.
    "docked-at":     dict(src=["vessel", "contact"], dst=["port"],
                          half_life_days=2.0, kind="state"),
    "met-with":      dict(src=["vessel", "contact"], dst=["vessel", "contact"],
                          half_life_days=None, kind="event"),
    "detected-by":   dict(src=["detection", "contact"], dst=["sensor"],
                          half_life_days=None, kind="event"),
    # The radar↔AIS correlation result (ADR-028): this sensed contact and this
    # hull are the same object, for this interval, with this confidence. It is
    # an *event* rather than a state because it is a per-interval finding —
    # a contact correlated for six hours and then not is the whole point, and
    # a state edge would flatten that into "these are the same ship".
    "correlates-with": dict(src=["contact"], dst=["vessel"],
                            half_life_days=None, kind="event"),
    "resolved-from": dict(src=["vessel"], dst=["track", "detection"],
                          half_life_days=None, kind="event"),
    "sanctioned-under": dict(src=["organization", "vessel", "person"],
                             dst=["sanctions_authority"],
                             half_life_days=None, kind="event"),
    # identity history: open interval = current identity; closed = former.
    # The roadmap's "formerly-identified-as" is an identified-as edge whose
    # t_end has been closed by an identity-change event.
    "identified-as": dict(src=["vessel"], dst=["identity"],
                          half_life_days=None, kind="state"),
    # --- the zone layer (ADR-030) ----------------------------------------
    # A crossing HAPPENED, so these are events and do not decay. A `contact`
    # may enter a zone — a radar track has a position history and needs no
    # identity to be somewhere — but only a `vessel` can be the subject of a
    # maiden-visit claim, because "she has never been here before" is an
    # assertion about a hull and a station track number is not one.
    "entered-zone":  dict(src=["vessel", "contact"], dst=["zone"],
                          half_life_days=None, kind="event"),
    "deviated-from-lane": dict(src=["vessel", "contact"], dst=["zone"],
                               half_life_days=None, kind="event"),
    "anchored-outside-limits": dict(src=["vessel", "contact"], dst=["zone"],
                                    half_life_days=None, kind="event"),
    # The pre-existing loitering edge, which had no registration at all: the
    # anomaly library has emitted `loiter-in-zone` since Phase 5 against a
    # `zone:<name>` destination that was never a node type. It validated only
    # because nothing checked. Now both halves exist.
    "loiter-in-zone": dict(src=["vessel", "contact"], dst=["zone"],
                           half_life_days=None, kind="event"),
    # --- the electro-optical loop (ADR-037) -------------------------------
    # A photograph WAS taken, so both are events and neither decays: the
    # picture does not become less true with age, though what it shows may
    # become less relevant — which is what `eo/cue.py`'s staleness term is for,
    # and it belongs in the scheduler rather than in a half-life here.
    #
    # `depicts` reaches a `contact` as well as a `vessel` on purpose. A camera
    # slewed onto a radar track nobody can name still took a picture of
    # something, and that capture is the strongest single thing the system can
    # offer about her — refusing to record it because the subject has no
    # identity would discard exactly the case the requirement is about.
    "depicts":       dict(src=["eo_capture"], dst=["vessel", "contact"],
                          half_life_days=None, kind="event"),
    "captured-by":   dict(src=["eo_capture"], dst=["sensor"],
                          half_life_days=None, kind="event"),
}


def validate_edge(edge_type: str, src_type: str, dst_type: str,
                  registry: dict[str, dict]) -> None:
    """Raise on edges the (live, possibly migrated) ontology doesn't allow."""
    spec = registry.get(edge_type)
    if spec is None:
        raise ValueError(f"unknown edge type {edge_type!r} "
                         f"(ontology has: {sorted(registry)})")
    if src_type not in spec["src"] or dst_type not in spec["dst"]:
        raise ValueError(
            f"{edge_type}: {src_type}->{dst_type} not allowed "
            f"(spec: {spec['src']}->{spec['dst']})")
