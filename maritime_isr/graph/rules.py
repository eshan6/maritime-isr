"""Graph event engine + rules (roadmap 4.4).

Every ingest emits events; rules subscribe and traverse. The canonical
chain, working end to end, is the phase's reason to exist:

    Ship A met-with Ship B → traverse B's owned-by (≤3 hops) →
    owner sanctioned-under OFAC at event time → alert on A with the FULL
    evidence chain attached.

Traversal safety is not optional: visited-set cycle protection and a
node budget (TRAVERSAL_MAX_NODES) — shell-company loops exist in real
registries and an unbounded walk is a denial-of-service on your own graph.

Chain confidence = MIN over the chain's decayed edge confidences: the
weakest-link rule, chosen because it is explainable in one sentence to an
analyst ("this alert is only as strong as the ownership record it rests
on"). Sanctions validity is checked AS OF EVENT TIME — a listing that
started after the rendezvous does not convict it retroactively.
"""
from __future__ import annotations

import hashlib

from ..config import (ALERT_MIN_CONFIDENCE, TRAVERSAL_MAX_HOPS_OWNERSHIP,
                      TRAVERSAL_MAX_NODES)


def _edge_evidence(store, e, at: float) -> dict:
    return dict(edge=e.edge_type, src=e.src, dst=e.dst,
                confidence=round(store.edge_confidence(e, at=at), 4),
                observed_at=e.observed_at, source=e.source,
                source_ref=e.source_ref, props=e.props)


def ownership_chains(store, vessel: str, at: float):
    """All ownership paths from a vessel, ≤ TRAVERSAL_MAX_HOPS_OWNERSHIP
    org hops, cycle-protected, budgeted. Yields (org_node_id, path_edges)."""
    budget = TRAVERSAL_MAX_NODES
    frontier = [(vessel, [])]
    visited = {vessel}
    hops = 0
    while frontier and hops < TRAVERSAL_MAX_HOPS_OWNERSHIP and budget > 0:
        nxt = []
        for node, path in frontier:
            for et in ("owned-by", "operated-by"):
                for e in store.edges(node, et, as_of=at):
                    budget -= 1
                    if budget <= 0:
                        return
                    if e.dst in visited:
                        continue        # cycle protection
                    visited.add(e.dst)
                    yield e.dst, path + [e]
                    nxt.append((e.dst, path + [e]))
        frontier = nxt
        hops += 1


def _active_sanctions(store, org: str, at: float):
    return [e for e in store.edges(org, "sanctioned-under", as_of=at)]


class SanctionedOwnerRendezvous:
    """met_with → counterpart's ownership → sanctions → alert on subject."""
    name = "sanctioned_owner_rendezvous"
    subscribes = ("met_with",)

    def handle(self, store, event) -> list[str]:
        subject = event["subject"]
        counterpart = event["payload"]["counterpart"]
        at = event["ts"]
        met = [e for e in store.edges(subject, "met-with", history=True)
               if e.dst == counterpart and
               e.props.get("encounter_id") == event["payload"].get("encounter_id")]
        if not met:
            return []
        alerts = []
        for org, path in ownership_chains(store, counterpart, at):
            for sanc in _active_sanctions(store, org, at):
                chain = [_edge_evidence(store, met[-1], at)] + \
                        [_edge_evidence(store, e, at) for e in path] + \
                        [_edge_evidence(store, sanc, at)]
                conf = min(c["confidence"] for c in chain)
                if conf < ALERT_MIN_CONFIDENCE:
                    continue
                aid = "alr_" + hashlib.sha1(
                    f"{self.name}|{subject}|{counterpart}|{org}|{at:.0f}"
                    .encode()).hexdigest()[:12]
                store.add_alert(aid, self.name, subject, at, conf, chain,
                                anomaly_type="dark_rendezvous", score=conf)
                alerts.append(aid)
        return alerts


class SanctionedOwnerDarkGap:
    """gap_confirmed (SAR-confirmed intentional silence, from Phase 3) on a
    vessel whose OWN ownership chain is sanctioned — the second rule, here
    to prove the engine generalizes past its founding example."""
    name = "sanctioned_owner_dark_gap"
    subscribes = ("gap_confirmed",)

    def handle(self, store, event) -> list[str]:
        if event["payload"].get("gap_type") != "INTENTIONAL_SILENCE":
            return []
        subject = event["subject"]
        at = event["ts"]
        alerts = []
        for org, path in ownership_chains(store, subject, at):
            for sanc in _active_sanctions(store, org, at):
                chain = [dict(edge="gap_confirmed", src=subject,
                              dst=event["payload"].get("scene_id", "?"),
                              confidence=round(
                                  event["payload"].get("confidence", 0.8), 4),
                              observed_at=at, source="fusion_core",
                              source_ref=event["payload"].get("detection_id", ""),
                              props=dict(gap_type="INTENTIONAL_SILENCE"))] + \
                        [_edge_evidence(store, e, at) for e in path] + \
                        [_edge_evidence(store, sanc, at)]
                conf = min(c["confidence"] for c in chain)
                if conf < ALERT_MIN_CONFIDENCE:
                    continue
                aid = "alr_" + hashlib.sha1(
                    f"{self.name}|{subject}|{org}|{at:.0f}".encode()
                ).hexdigest()[:12]
                store.add_alert(aid, self.name, subject, at, conf, chain,
                                anomaly_type="dark_vessel", score=conf)
                alerts.append(aid)
        return alerts


DEFAULT_RULES = [SanctionedOwnerRendezvous(), SanctionedOwnerDarkGap()]


def process_events(store, rules=None) -> dict:
    """Drain the event queue through the rule set. Returns accounting."""
    rules = DEFAULT_RULES if rules is None else rules
    by_type: dict[str, list] = {}
    for r in rules:
        for et in r.subscribes:
            by_type.setdefault(et, []).append(r)
    n_events, fired = 0, []
    for ev in store.pending_events():
        n_events += 1
        for r in by_type.get(ev["event_type"], []):
            fired.extend(r.handle(store, ev))
        store.mark_processed(ev["event_id"])
    return dict(events_processed=n_events, alerts_fired=fired)
