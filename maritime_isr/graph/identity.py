"""Identity persistence (roadmap 4.2) — vessels as persistent entities.

The stable key is the hull (IMO number, physical and welded to the ship);
MMSI, name, and flag are CLOTHING — mutable identities recorded as
time-scoped identified-as edges. An identity change (MMSI swap, rename,
reflag) closes the old edge, opens a new one, and emits an
identity_changed event — a first-class fact, not a data error. The
roadmap's "formerly-identified-as" is exactly an identified-as edge whose
interval has been closed by such an event: the identity-laundering edge.

`resolve_mmsi(store, mmsi, at)` answers "which HULL was broadcasting this
MMSI at time t" — the join every downstream ingest uses to attach tracks,
detections, and encounters to the right persistent vessel even across a
swap. Vessels never seen in any registry get a provisional
vessel:mmsi:<m> entity; a later registry fold can absorb it.
"""
from __future__ import annotations

import time

from ..schemas.keys import identity_node_id
from ..schemas.keys import vessel_node_id as _canonical_vessel_node_id


def vessel_id(imo: int | None, mmsi: int | None = None) -> str:
    """A hull id minted from an identifier, when no source id is available.

    **This is the fallback, not the primary key.** The primary key is the
    source's own vessel id, minted by `schemas.keys.vessel_node_id`, and a hull
    should reach this function only when nothing has published an `id:imo:*` or
    `id:mmsi:*` node for it — which after ADR-022 means the vessel is genuinely
    unknown to the registry, not merely that the populator forgot to say so.

    Both branches route through the one canonical constructor, so the namespace
    separator, the stripping rule and the shape cannot drift from the populator's
    again. They already had: this module said `vessel:mmsi:<m>` while the
    populator said `vessel:gfw:<id>`, and neither knew the other existed.
    """
    if imo:
        return _canonical_vessel_node_id(str(imo), source="imo")
    return _canonical_vessel_node_id(str(mmsi), source="mmsi")


def identity_id(kind: str, value) -> str:
    """Kept as the module's public name; the definition is now shared.

    The populator constructs the very same ids when it publishes identity nodes,
    because it imports the same function. That is the whole repair — the two
    sides previously built this string independently and one of them only ever
    built it for `kind="name"`.
    """
    return identity_node_id(kind, value)


def _current_identity(store, vid: str, kind: str):
    """The open identified-as edge of this kind, if any."""
    for e in store.edges(vid, "identified-as"):
        if e.props.get("kind") == kind and e.t_end is None:
            return e
    return None


def fold_registry_snapshot(store, snapshot: dict, *, source_ref: str) -> list[dict]:
    """Fold one registry snapshot into the graph. Diffs against current
    identities; every difference is an identity_changed event. Returns the
    events emitted (for the caller's accounting)."""
    as_of = snapshot["as_of_epoch"]
    events = []
    for v in snapshot["vessels"]:
        vid = vessel_id(v.get("imo"), v.get("mmsi"))
        store.upsert_node(vid, "vessel",
                          dict(imo=v.get("imo"), name=v.get("name"),
                               flag=v.get("flag"),
                               length_m=v.get("length_m"),
                               registry_as_of=as_of))
        for kind in ("mmsi", "name", "flag"):
            new_val = v.get(kind)
            if new_val is None:
                continue
            cur = _current_identity(store, vid, kind)
            if cur is not None and cur.props.get("value") == new_val:
                # unchanged: refresh the assertion (observed_at advances)
                store.add_edge("identified-as", vid, cur.dst,
                               t_start=cur.t_start, t_end=None,
                               confidence=0.95, observed_at=as_of,
                               source="registry", source_ref=source_ref,
                               props=cur.props)
                continue
            if cur is not None:
                # identity change: close the old interval —
                # this IS the formerly-identified-as edge
                store.close_edge("identified-as", vid, cur.dst, t_end=as_of,
                                 source="registry", source_ref=source_ref)
                ev = dict(field=kind, old=cur.props.get("value"),
                          new=new_val, vessel=vid)
                store.emit("identity_changed", vid, as_of, ev)
                events.append(ev)
            iid = identity_id(kind, new_val)
            store.upsert_node(iid, "identity", dict(kind=kind, value=new_val))
            store.add_edge("identified-as", vid, iid,
                           t_start=as_of, t_end=None, confidence=0.95,
                           observed_at=as_of, source="registry",
                           source_ref=source_ref,
                           props=dict(kind=kind, value=new_val))
        # flag as a flagged-to state edge too (the graph-traversal form)
        if v.get("flag"):
            fid = f"flag:{v['flag']}"
            store.upsert_node(fid, "flag_state", dict(code=v["flag"]))
            store.add_edge("flagged-to", vid, fid, t_start=as_of, t_end=None,
                           confidence=0.9, observed_at=as_of,
                           source="registry", source_ref=source_ref)
    return events


#: The scenario MMSI reservation. Duplicated as literals rather than imported
#: from `scenario/` so the graph layer keeps no dependency on the generator —
#: this rule must hold in a checkout where the scenario package is absent.
_RESERVED_MMSI_MIN = 999_000_000
_RESERVED_MMSI_MAX = 999_999_999


def resolve_mmsi(store, mmsi: int, at: float | None = None) -> str:
    """MMSI + time → vessel entity id. Walks identified-as edges INTO the
    id:mmsi node, honoring time scopes, so a track under a swapped-away
    MMSI still lands on the hull that was broadcasting it at the time.
    Unknown MMSIs get a provisional vessel entity."""
    at = time.time() if at is None else at
    iid = identity_id("mmsi", mmsi)
    if store.node(iid) is not None:
        cands = [e for e in store.edges(iid, "identified-as", direction="in",
                                        history=True)
                 if e.t_start <= at and (e.t_end is None or e.t_end > at)]
        if cands:
            return sorted(cands, key=lambda e: e.observed_at)[-1].src
    vid = vessel_id(None, mmsi)
    if store.node(vid) is None:
        # An MMSI in the reserved 999xxxxxx block cannot belong to a real
        # vessel: 999 is not an assignable Maritime Identification Digit, so
        # the block is structurally unreachable by a transmitting ship
        # (ADR-019). A provisional entity minted from one is therefore
        # scenario data, and saying so here is what keeps the real/synthetic
        # split honest all the way through to the alert table.
        #
        # This is a fact about the *identifier*, not about ground truth — no
        # scenario_truth is consulted and none could be.
        synthetic = _RESERVED_MMSI_MIN <= int(mmsi) <= _RESERVED_MMSI_MAX
        store.upsert_node(vid, "vessel", dict(mmsi=mmsi, provisional=True),
                          is_synthetic=synthetic)
        store.upsert_node(iid, "identity", dict(kind="mmsi", value=mmsi),
                          is_synthetic=synthetic)
        store.add_edge("identified-as", vid, iid, t_start=0.0, t_end=None,
                       confidence=0.5, observed_at=at,
                       source=("synthetic-scenario:ais_provisional" if synthetic
                               else "ais_provisional"),
                       source_ref="mmsi_only",
                       props=dict(kind="mmsi", value=mmsi),
                       is_synthetic=synthetic)
    return vid


#: Node type and prefix for a track whose sensor knows no identity.
CONTACT_PREFIX = "contact"

#: Station-id prefix the scenario radar network uses. Kept beside the
#: reserved-MMSI constants for the same reason: it is a fact about an
#: *identifier space*, not about ground truth, and it is what lets a contact
#: node inherit the right `is_synthetic` flag without anything consulting the
#: answer key.
_SYNTHETIC_STATION_PREFIX = "SYN-"


def contact_node_id(track_key: str, *, source: str) -> str:
    """The graph node for a target we can see but cannot name.

    Namespaced by sensor because the key spaces are unrelated: station `MUM-1`
    numbering a track `7` has nothing to do with MMSI 7.
    """
    return f"{CONTACT_PREFIX}:{source}:{track_key}"


def track_subject_id(store, track, at: float | None = None) -> str:
    """The graph node an alert about this track should land on. ADR-028.

    **Every detector in the anomaly library called `resolve_mmsi(store,
    tr.mmsi)` and that is where the identity assumption actually lived.** It
    reads perfectly until a sensor arrives whose tracks have no MMSI, at which
    point `resolve_mmsi(store, None)` mints `vessel:mmsi:None` — a node that
    resolves, passes every presence check, and is a different fiction for every
    radar track in the picture. The loitering and port-risk rules would have
    "worked" on radar and produced garbage subjects.

    So the question is asked of the track rather than assumed of the column:

      * a track whose key IS an identity resolves through `resolve_mmsi` exactly
        as before, landing on the hull that was broadcasting it at time `t`;
      * a track whose key is not an identity gets a **contact** node —
        `contact:radar:<station>:<n>`. That is an honest object: something is
        there, it has a position history, and we do not know who it is. It can
        later be joined to a hull by the correlation stage, which is a
        conclusion with evidence rather than an assumption baked into a node id.

    The contact node is created on demand so an alert always has a subject that
    exists, with `named=False` on it, because a UI that cannot tell an anonymous
    contact from an identified vessel will render one as the other.
    """
    if getattr(track, "has_identity", False):
        return resolve_mmsi(store, track.mmsi, at=at)

    return ensure_contact_node(store, track.track_key, source=track.source.name)


def ensure_contact_node(store, track_key: str, *, source: str) -> str:
    """The contact node for this sensed target, created if it does not exist.

    **Minting the id is not the same as creating the node**, and every caller
    needs both: an alert whose subject names a node the graph does not hold
    resolves to nothing, which is the shadow-stub failure ADR-022 was written
    about. `contact_node_id` is the string; this is the object.
    """
    nid = contact_node_id(track_key, source=source)
    if store.node(nid) is None:
        # Same reserved-block reasoning as `resolve_mmsi`: the scenario radar
        # stations are named with a reserved prefix, so a contact minted from
        # one is scenario data. No ground truth is consulted.
        synthetic = str(track_key).startswith(_SYNTHETIC_STATION_PREFIX)
        store.upsert_node(
            nid, CONTACT_PREFIX,
            dict(sensor=source, track_key=track_key, named=False,
                 note="a sensed target with no broadcast identity"),
            is_synthetic=synthetic)
    return nid


def current_mmsi(store, vid: str, at: float | None = None) -> int | None:
    """The MMSI this hull was broadcasting at time t (identified-as edges,
    time-scoped) — the inverse of resolve_mmsi."""
    at = time.time() if at is None else at
    cands = [e for e in store.edges(vid, "identified-as", history=True)
             if e.props.get("kind") == "mmsi"
             and e.t_start <= at and (e.t_end is None or e.t_end > at)]
    if not cands:
        return None
    return sorted(cands, key=lambda e: e.observed_at)[-1].props.get("value")
