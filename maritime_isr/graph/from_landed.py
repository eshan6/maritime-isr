"""Populate the object graph from landed real data. No synthetic edges.

Everything here comes from a Parquet table that a connector wrote. If a fact
is not in a landed table it does not become an edge — which is why several
things the roadmap expects are simply absent:

- **No `owned-by` edges.** GFW registry ownership is 0.66% (ADR-016), and
  OFAC's `vessel_owner` is a free-text string on the sanctioned hull, not a
  resolved corporate entity. Building an ownership graph out of either would
  be synthesising the thing we have already recorded that we do not have.
- **No `detected-by` or `resolved-from` edges.** We have run no SAR and built
  no tracks from real AIS (ADR-013, ADR-017).

**Attribution, which is the point of the props on every edge.** GFW detected
these vessels, GFW assessed which AIS gaps look intentional, and OFAC decided
who is sanctioned. We matched hulls to a sanctions list. Every edge carries the
`source` that asserted it so that a UI can say *whose* claim it is showing, and
nothing in this module lets our matching be mistaken for our detection.

**Node identity.** Nodes are keyed by GFW's `vessel_id`, not by IMO. IMO is the
better key in principle — it is the hull — but under half of the landed identity
records carry one, and GFW's event tables reference their own id. Keying on
anything else would split one hull across two nodes and silently halve its
degree, which is precisely what the connectivity measurement is trying to read.
IMO, MMSI and name travel in node props.

**Decay is real here.** `docked-at` has a 2-day half-life, so an 8-week-old port
visit is worth ~2^-28 of its base confidence — effectively gone. That is the
intended behaviour of a *state* edge (ADR/ontology): "this ship is at this port"
rots fast because it stops being true fast. Reporting how many edges have
decayed below a usable threshold is one of the honest measurements of whether
this graph is worth looking at yet.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from ..ingest.landing import read_table
from ..schemas.keys import identity_node_id
from ..schemas.keys import vessel_node_id as _vessel_node_id
from ..ingest.sanctions_match import (MATCH_TABLE, TIER_CONFIDENCE,
                                     normalise_name)

AUTHORITY_OFAC = "authority:OFAC"

#: Scenario designations point here and never at OFAC. A synthetic
#: `sanctioned-under` edge terminating on the real authority node would place a
#: fabricated finding under a real regulator's name in the same table as our
#: genuine matches — the hard ban this separation exists to enforce (ADR-019).
AUTHORITY_SCENARIO = "authority:SCENARIO-SDN"

#: Confidence for an edge whose source stated none. The store requires a number
#: on every edge, so silence has to become one — but it must not become a
#: flattering one, and it must stay distinguishable from a stated value. Every
#: edge built this way carries `confidence_stated: False` in its props, so a
#: query can exclude them and a reader can tell the difference.
UNSTATED_CONFIDENCE = 0.5

#: A GFW AIS gap becomes its own node rather than a vessel property. The verdict
#: "this looked intentional" is GFW's assessment of a specific gap, and hanging
#: it on a node keeps it attributable to them. Written as a vessel property it
#: would read as our claim about the ship, which is exactly the overclaim
#: ADR-018's framing rule forbids.
GAP_NODE_TYPE = "ais_gap"
GAP_EDGE_TYPE = "reported-gap"
GAP_EDGE_SPEC = dict(src=["vessel"], dst=[GAP_NODE_TYPE],
                     half_life_days=None, kind="event")


#: Re-exported so existing callers keep working; the definition now lives in
#: `schemas.keys` alongside `identity_node_id`, because the populator and the
#: identity resolver have to agree on both and previously agreed on neither
#: (ADR-022).
vessel_node_id = _vessel_node_id


def _epoch(v, default: float | None = None) -> float | None:
    """Landed timestamps arrive as datetimes or ISO strings depending on reader."""
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            v = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return default
    if isinstance(v, datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v.timestamp()
    return default


def _syn(row: dict) -> bool:
    """Is this landed row scenario data? Missing column means real (ADR-019)."""
    return bool(row.get("is_synthetic"))


def _src(row: dict, real_source: str) -> str:
    """The edge source, which must agree with the synthetic flag.

    A scenario row's edges are sourced `synthetic-scenario:<connector>` rather
    than the connector name alone, so the graph store's agreement check passes
    and a reader can still see which connector shape the row took. The store
    rejects any edge whose flag and source disagree, so this is the single
    place the two are kept in step.
    """
    return f"synthetic-scenario:{real_source}" if _syn(row) else real_source


def _conf(row: dict, default: float = UNSTATED_CONFIDENCE) -> tuple[float, bool]:
    """(confidence, stated_by_source). A null column is not zero confidence."""
    v = row.get("confidence")
    if v is None:
        return default, False
    try:
        return float(v), True
    except (TypeError, ValueError):
        return default, False


def ensure_ontology(store) -> None:
    """Register the gap types if this store predates them. Ontology is data."""
    if GAP_NODE_TYPE not in store.node_registry():
        store.migrate_add_node_type(GAP_NODE_TYPE)
    if GAP_EDGE_TYPE not in store.edge_registry():
        store.migrate_add_edge_type(GAP_EDGE_TYPE, GAP_EDGE_SPEC)


def ensure_authorities(store) -> None:
    """The two designating bodies, with enough detail to render an entity card.

    A node whose entire content is a short code renders as an empty panel in the
    UI, which is useless to an analyst asking "who says this ship is
    sanctioned?". Both authorities therefore carry the issuing body, its
    jurisdiction, the register the designation sits on, and the citation an
    analyst would follow.

    **The scenario authority is never relabelled as OFAC** (ADR-019). It names
    the body it stands in for, so the panel answers the question honestly —
    "a stand-in for the OFAC SDN list; this designation is scenario data" —
    rather than putting a fabricated finding under a real regulator's name in
    the same graph as our genuine matches.
    """
    store.upsert_node(
        AUTHORITY_OFAC, "sanctions_authority",
        dict(name="OFAC",
             full_name="Office of Foreign Assets Control",
             issuing_body="U.S. Department of the Treasury",
             jurisdiction="United States",
             register="Specially Designated Nationals and Blocked Persons "
                      "(SDN) List",
             reference="https://sanctionslist.ofac.treas.gov",
             fictional=False))
    store.upsert_node(
        AUTHORITY_SCENARIO, "sanctions_authority",
        dict(name="SCENARIO-SDN",
             full_name="Scenario designation list (not a real authority)",
             issuing_body="Maritime ISR scenario generator",
             jurisdiction="none — synthetic",
             register="Fictional stand-in for the OFAC SDN list",
             stands_in_for="U.S. Treasury OFAC SDN",
             fictional=True),
        is_synthetic=True)


# --------------------------------------------------------------------------
# vessels and their identities
# --------------------------------------------------------------------------

def load_vessels() -> dict[str, dict]:
    """One record per GFW vessel id, folded from its identity intervals.

    The *latest* interval by `valid_from` supplies the display identity; every
    interval is kept for the rename analysis. A vessel that appears only in the
    event tables and never in identity still becomes a node, with empty props —
    dropping it would understate connectivity.
    """
    out: dict[str, dict] = {}
    for r in read_table("gfw_vessel_identity"):
        vid = r.get("vessel_id")
        if not vid:
            continue
        rec = out.setdefault(vid, {"vessel_id": vid, "intervals": []})
        rec["intervals"].append(r)
    for rec in out.values():
        rec["intervals"].sort(key=lambda r: _epoch(r.get("valid_from")) or 0.0)
        latest = rec["intervals"][-1]
        rec.update({k: latest.get(k) for k in
                    ("imo", "mmsi", "ship_name", "flag", "call_sign")})
    return out


def add_vessels(store, vessels: dict[str, dict]) -> int:
    """Hull nodes, each carrying the synthetic flag of the rows it came from.

    **`is_synthetic` was omitted here and defaulted to 0**, so all 114 scenario
    hulls landed as real while the identity and gap nodes beside them were
    flagged correctly. ADR-019 makes that flag the only thing separating the two
    populations, so every real-vs-synthetic vessel count taken before
    2026-08-01 was wrong — and wrong in the direction that inflates the real
    side, which is the worse direction.

    Taken from the intervals rather than assumed: a hull is synthetic when the
    identity rows describing it are, and `stamp_envelope` already refuses to let
    a row's flag disagree with its source id.
    """
    for vid, rec in vessels.items():
        intervals = rec.get("intervals", [])
        syn = any(_syn(iv) for iv in intervals)
        store.upsert_node(vessel_node_id(vid), "vessel", dict(
            gfw_vessel_id=vid, imo=rec.get("imo"), mmsi=rec.get("mmsi"),
            name=rec.get("ship_name"), flag=rec.get("flag"),
            n_identity_records=len(intervals),
        ), is_synthetic=syn)
    return len(vessels)


def add_flagged_to(store, vessels: dict[str, dict]) -> int:
    """One flagged-to edge per distinct flag a vessel has carried.

    A vessel with two flags across its intervals gets two edges with different
    time scopes — that is a reflagging, and it is a first-class fact rather than
    a conflict to resolve away.
    """
    n = 0
    for vid, rec in vessels.items():
        seen: set[str] = set()
        for iv in rec.get("intervals", []):
            flag = iv.get("flag")
            if not flag or flag in seen:
                continue
            seen.add(flag)
            t0 = _epoch(iv.get("valid_from"))
            if t0 is None:
                continue
            fid = f"flag:{flag}"
            # A flag_state node is shared between real and scenario vessels —
            # PAN is PAN — so it is created as real and never demoted.
            store.upsert_node(fid, "flag_state", dict(code=flag))
            store.add_edge(
                "flagged-to", vessel_node_id(vid), fid,
                t_start=t0, t_end=_epoch(iv.get("valid_to")),
                confidence=0.9 if iv.get("record_kind") == "registry" else 0.7,
                observed_at=t0, source=_src(iv, "gfw-vessels"),
                source_ref=str(iv.get("source_ref") or f"{vid}:flag:{flag}"),
                props=dict(record_kind=iv.get("record_kind"), flag=flag),
                is_synthetic=_syn(iv),
            )
            n += 1
    return n


def add_identities(store, vessels: dict[str, dict]) -> tuple[int, int]:
    """identified-as edges, and the ones that are genuinely *formerly*-.

    Returns (total edges, superseded edges).

    **A closed interval is NOT a former identity, and treating it as one was
    wrong.** The first live run made that obvious: 8,724 of 8,724 intervals came
    back closed — 100%. GFW's `transmissionDateTo` is the last transmission
    *inside the window we queried*, so every record ends simply because our
    query ended. Reading that as "this identity was superseded" would have
    labelled the entire fleet as having changed identity.

    The roadmap's `formerly-identified-as` needs real evidence of replacement:
    the same vessel carrying a **different** name in a **later** interval. That
    is what is counted here. An interval closing with no successor means only
    that GFW stopped hearing that name in our window.
    """
    total = superseded = 0
    for vid, rec in vessels.items():
        intervals = [iv for iv in rec.get("intervals", [])
                     if iv.get("ship_name") and _epoch(iv.get("valid_from")) is not None]
        # already sorted by valid_from in load_vessels()
        for i, iv in enumerate(intervals):
            name = iv["ship_name"]
            key = normalise_name(name)
            t0 = _epoch(iv.get("valid_from"))
            t1 = _epoch(iv.get("valid_to"))
            # superseded only if some LATER interval carries a DIFFERENT name
            later_names = {normalise_name(x["ship_name"])
                           for x in intervals[i + 1:]}
            later_names.discard(None)
            is_superseded = bool(later_names - {key})
            nid = identity_node_id("name", key)
            store.upsert_node(nid, "identity", dict(kind="name", value=name),
                              is_synthetic=_syn(iv))
            store.add_edge(
                "identified-as", vessel_node_id(vid), nid,
                t_start=t0, t_end=t1,
                confidence=0.9 if iv.get("record_kind") == "registry" else 0.7,
                observed_at=t0, source=_src(iv, "gfw-vessels"),
                source_ref=str(iv.get("source_ref") or f"{vid}:name:{name}"),
                props=dict(kind="name", value=name,
                           record_kind=iv.get("record_kind"),
                           # the distinction the 100%-closed result forced
                           superseded_by_later_name=is_superseded,
                           interval_closed=t1 is not None),
                is_synthetic=_syn(iv),
            )
            total += 1
            if is_superseded:
                superseded += 1

        total += _add_key_identities(store, vid, rec)
    return total, superseded


#: Identity kinds that are *lookup keys* into a hull, as opposed to labels.
#: `name` is handled above because it carries the supersession analysis; these
#: three are pure keys and need only a time-scoped assertion.
_KEY_IDENTITY_FIELDS = (("mmsi", "mmsi"), ("imo", "imo"),
                        ("call_sign", "call_sign"))


def _add_key_identities(store, vid: str, rec: dict) -> int:
    """Publish the `id:mmsi:*` / `id:imo:*` nodes the resolver reads.

    **This is the fix for the shadow-stub defect (ADR-022), and the omission was
    the whole cause.** `identity.resolve_mmsi` answers "which hull was
    broadcasting this MMSI at time t" by walking `identified-as` edges into an
    `id:mmsi:<mmsi>` node. That is the right design. But this populator emitted
    **`id:name:*` nodes only** — measured on the synthetic corpus, 115 name
    nodes and **zero** mmsi or imo nodes — so the lookup had nothing to find and
    fell through to minting a provisional `vessel:mmsi:<mmsi>` hull.

    The result was two nodes per ship: a populated one with flag, owner,
    sanctions and port calls, and an empty twin carrying `provisional: true`.
    Every alert landed on the twin. It *resolved* — a presence check passed —
    and an analyst clicking it reached nothing.

    Publishing these nodes means the resolver finds the hull on its own. No
    translation table, no alias map, nothing for a future consumer to remember
    to call: the two sides now read and write the same key because they call
    the same function to build it.

    Intervals are time-scoped, so an MMSI swap produces two edges with disjoint
    windows and a track under the old number still resolves to the right hull —
    which is what makes B1's phoenix and B4's zombie legible at all.
    """
    n = 0
    for iv in rec.get("intervals", []):
        t0 = _epoch(iv.get("valid_from"))
        if t0 is None:
            continue
        t1 = _epoch(iv.get("valid_to"))
        for field, kind in _KEY_IDENTITY_FIELDS:
            raw = iv.get(field)
            if raw in (None, "", 0):
                continue
            value = str(raw).strip()
            if not value:
                continue
            nid = identity_node_id(kind, value)
            store.upsert_node(nid, "identity", dict(kind=kind, value=value),
                              is_synthetic=_syn(iv))
            store.add_edge(
                "identified-as", vessel_node_id(vid), nid,
                t_start=t0, t_end=t1,
                confidence=0.9 if iv.get("record_kind") == "registry" else 0.7,
                observed_at=t0, source=_src(iv, "gfw-vessels"),
                source_ref=str(iv.get("source_ref") or f"{vid}:{kind}:{value}"),
                props=dict(kind=kind, value=value,
                           record_kind=iv.get("record_kind"),
                           interval_closed=t1 is not None),
                is_synthetic=_syn(iv),
            )
            n += 1
    return n


# --------------------------------------------------------------------------
# behavioural edges
# --------------------------------------------------------------------------

def add_encounters(store, known: set[str]) -> tuple[int, int]:
    """met-with, vessel to vessel. Returns (edges, skipped).

    Skipped means the counterpart has no vessel id in the landed row — GFW
    records some encounters against a single vessel. An encounter with an
    unnamed partner is not an edge; inventing a placeholder node for it would
    inflate exactly the connectivity number this exists to measure.
    """
    n = skipped = 0
    for r in read_table("gfw_encounters"):
        a, b = r.get("vessel_id"), r.get("counterpart_vessel_id")
        if not a or not b:
            skipped += 1
            continue
        t0 = _epoch(r.get("start_time"))
        if t0 is None:
            skipped += 1
            continue
        for v in (a, b):
            if v not in known:
                store.upsert_node(vessel_node_id(v), "vessel",
                                  dict(gfw_vessel_id=v, from_event_only=True),
                                  is_synthetic=_syn(r))
                known.add(v)
        conf, stated = _conf(r)
        store.add_edge(
            "met-with", vessel_node_id(a), vessel_node_id(b),
            t_start=t0, t_end=_epoch(r.get("end_time")),
            confidence=conf,
            observed_at=t0, source=_src(r, "gfw-events:encounters"),
            source_ref=str(r.get("event_id")),
            props=dict(event_id=r.get("event_id"),
                       duration_hours=r.get("duration_hours"),
                       lat=r.get("lat"), lon=r.get("lon"),
                       h3_r6=r.get("h3_r6"),
                       encounter_type=r.get("encounter_type"),
                       confidence_stated=stated,
                       gfw_confidence_raw=r.get("gfw_confidence_raw")),
            is_synthetic=_syn(r),
        )
        n += 1
    return n, skipped


def add_port_visits(store, known: set[str]) -> tuple[int, int]:
    """docked-at, vessel to port. Returns (edges, skipped)."""
    n = skipped = 0
    for r in read_table("gfw_port_visits"):
        v = r.get("vessel_id")
        # `visit_port_id` resolves across the entry / stop / exit anchorages;
        # `port_id` is the stop alone. On the operator's corpus this changes
        # nothing — measured 2026-07-31, every one of the 3,000 real port
        # visits has a stop anchorage with an id, so `port_id` was already 100%
        # populated and nothing was being skipped. (An earlier comment here
        # claimed ~46% were dropped. That was inferred from `port_name`'s null
        # rate, which is an *unnamed* anchorage, not a missing one — see the
        # ADR-020 correction.) The fallback stays as a guard for corpora where
        # the stop is genuinely absent. Order matters: the stop still wins when
        # it exists, because it is the stronger attribution.
        pid = (r.get("visit_port_id") or r.get("visit_port_name")
               or r.get("port_id") or r.get("port_name"))
        t0 = _epoch(r.get("start_time"))
        if not v or not pid or t0 is None:
            skipped += 1
            continue
        if v not in known:
            store.upsert_node(vessel_node_id(v), "vessel",
                              dict(gfw_vessel_id=v, from_event_only=True),
                              is_synthetic=_syn(r))
            known.add(v)
        node = f"port:{pid}"
        # Ports are real places whether a real or a scenario vessel calls
        # there, so the node stays real and only the edge carries the flag.
        store.upsert_node(node, "port", dict(
            port_id=r.get("visit_port_id") or r.get("port_id"),
            name=r.get("visit_port_name") or r.get("port_name"),
            flag=r.get("anchorage_flag"), lat=r.get("lat"), lon=r.get("lon")))
        conf, stated = _conf(r)
        store.add_edge(
            "docked-at", vessel_node_id(v), node,
            t_start=t0, t_end=_epoch(r.get("end_time")),
            confidence=conf,
            observed_at=t0, source=_src(r, "gfw-events:port_visits"),
            source_ref=str(r.get("event_id")),
            props=dict(event_id=r.get("event_id"),
                       # Both, deliberately. `duration_hours` is GFW's event
                       # span and can be months when the visit was stitched
                       # across two anchorages; `dwell_hours` is populated only
                       # when the structure supports calling it time alongside.
                       # Anything reasoning about how long a ship sat somewhere
                       # wants the second one and must tolerate its being null.
                       duration_hours=r.get("duration_hours"),
                       dwell_hours=r.get("dwell_hours"),
                       visit_port_source=r.get("visit_port_source"),
                       confidence_stated=stated,
                       port_name=r.get("visit_port_name") or r.get("port_name")),
            is_synthetic=_syn(r),
        )
        n += 1
    return n, skipped


def add_gaps(store, known: set[str], *, only_intentional: bool = True
             ) -> tuple[int, int]:
    """reported-gap, vessel to the gap GFW recorded. Returns (edges, skipped).

    `only_intentional` keeps to gaps GFW flagged as looking like deliberate
    disabling. The unflagged remainder is not "not intentional" — GFW simply
    has no verdict — so landing all of them as equal edges would assert
    something nobody claimed.
    """
    n = skipped = 0
    for r in read_table("gfw_ais_gaps"):
        v = r.get("vessel_id")
        t0 = _epoch(r.get("start_time"))
        intentional = r.get("gfw_intentional_disabling")
        if only_intentional and not intentional:
            skipped += 1
            continue
        if not v or t0 is None:
            skipped += 1
            continue
        if v not in known:
            store.upsert_node(vessel_node_id(v), "vessel",
                              dict(gfw_vessel_id=v, from_event_only=True),
                              is_synthetic=_syn(r))
            known.add(v)
        gid = f"gap:{r.get('event_id')}"
        store.upsert_node(gid, GAP_NODE_TYPE, dict(
            event_id=r.get("event_id"),
            duration_hours=r.get("gap_duration_hours") or r.get("duration_hours"),
            off_lat=r.get("gap_off_lat"), off_lon=r.get("gap_off_lon"),
            on_lat=r.get("gap_on_lat"), on_lon=r.get("gap_on_lon"),
            distance_km=r.get("gap_distance_km"),
            implied_speed_kn=r.get("gap_implied_speed_kn"),
            # GFW's verdict, labelled as GFW's.
            gfw_intentional_disabling=bool(intentional),
            reception_at_off=r.get("reception_at_off"),
            assessed_by=("maritime-isr-scenario" if _syn(r)
                         else "global-fishing-watch")),
            is_synthetic=_syn(r))
        conf, stated = _conf(r)
        store.add_edge(
            GAP_EDGE_TYPE, vessel_node_id(v), gid,
            t_start=t0, t_end=_epoch(r.get("end_time")),
            confidence=conf,
            observed_at=t0, source=_src(r, "gfw-events:gaps"),
            source_ref=str(r.get("event_id")),
            props=dict(event_id=r.get("event_id"),
                       gfw_intentional_disabling=bool(intentional)
                       if intentional is not None else None,
                       confidence_stated=stated,
                       assessed_by=("maritime-isr-scenario" if _syn(r)
                                    else "global-fishing-watch")),
            is_synthetic=_syn(r),
        )
        n += 1
    return n, skipped


def add_sanctions(store, known: set[str]) -> tuple[int, int]:
    """sanctioned-under, from our OFAC matches. Returns (findings, candidates).

    **Findings and candidates both become edges, at their own confidence.** The
    tier travels in props and in the base confidence, so a name-only candidate
    at 0.35 can never be read as a finding — that is what the confidence is for.
    Dropping candidates would hide leads; promoting them would be ADR-004's
    cardinal error.
    """
    ensure_authorities(store)
    findings = candidates = 0
    skipped_tiers: set = set()
    for r in read_table(MATCH_TABLE):
        v = r.get("vessel_id")
        t0 = _epoch(r.get("sanctions_as_of"))
        if not v or t0 is None:
            continue
        if v not in known:
            store.upsert_node(vessel_node_id(v), "vessel",
                              dict(gfw_vessel_id=v, from_event_only=True),
                              is_synthetic=_syn(r))
            known.add(v)
        # The envelope confidence is the tier confidence, but a row landed
        # before ADR-018 may carry a null there. Falling back to the tier is
        # exact, not a guess — the tier IS what set it. A tier we do not
        # recognise is skipped loudly rather than given a number.
        tier = r.get("match_tier")
        conf = r.get("confidence")
        if conf is None:
            conf = TIER_CONFIDENCE.get(tier)
        if conf is None:
            skipped_tiers.add(tier)
            continue
        conf = float(conf)
        # A scenario designation points at the fictional SCENARIO-SDN authority,
        # never at OFAC. Attaching synthetic rows to the real authority node
        # would put fabricated findings under a real regulator's name — the one
        # thing the hard bans forbid outright.
        authority = AUTHORITY_SCENARIO if _syn(r) else AUTHORITY_OFAC
        store.add_edge(
            "sanctioned-under", vessel_node_id(v), authority,
            # An OFAC listing has no recorded end in the SDN snapshot; t_end is
            # left open and the snapshot's as_of bounds what we can claim.
            t_start=t0, t_end=None, confidence=conf, observed_at=t0,
            source=_src(r, "ofac-vessel-match"),
            source_ref=str(r.get("source_ref") or v),
            props=dict(match_tier=r.get("match_tier"),
                       is_finding=bool(r.get("is_finding")),
                       ofac_ent_num=r.get("ofac_ent_num"),
                       ofac_name=r.get("ofac_name"),
                       ofac_program=r.get("ofac_program"),
                       ofac_owner=r.get("ofac_owner"),
                       ofac_imo=r.get("ofac_imo"),
                       sanctions_as_of=str(r.get("sanctions_as_of")),
                       matched_by="maritime-isr",
                       listed_by=("scenario-sdn-fictional" if _syn(r)
                                  else "us-treasury-ofac")),
            is_synthetic=_syn(r),
        )
        if r.get("is_finding"):
            findings += 1
        else:
            candidates += 1
    if skipped_tiers:
        print(f"[graph] skipped match rows with unrecognised tier(s) "
              f"{sorted(skipped_tiers)} — re-run the matcher (ADR-018)")
    return findings, candidates


# --------------------------------------------------------------------------
# organizations + ownership (the paid-feed stand-in, ADR-016)
# --------------------------------------------------------------------------

ORG_TABLE = "scenario_organizations"
OWNERSHIP_TABLE = "scenario_ownership"


def _table_present(table: str) -> bool:
    from ..ingest.landing import table_day_partitions
    return bool(table_day_partitions(table))


def _org_node_id(ref: str) -> str:
    """An organization ref (`org:pearl-crest-shipping`) is already namespaced;
    keep it as the node id. Only vessel refs go through `vessel_node_id`."""
    return str(ref).strip()


def _ownership_endpoint(ref: str, store, *, is_syn: bool) -> str | None:
    """Resolve an ownership src/dst to a graph node id, creating a stub node if
    the endpoint is not otherwise known. A `vessel:` ref is a hull; anything else
    is treated as an organization."""
    ref = str(ref).strip()
    if not ref:
        return None
    if ref.startswith("vessel:"):
        nid = vessel_node_id(ref)
        if store.node(nid) is None:
            store.upsert_node(nid, "vessel",
                              dict(from_ownership_only=True), is_synthetic=is_syn)
        return nid
    nid = _org_node_id(ref)
    if store.node(nid) is None:
        store.upsert_node(nid, "organization",
                          dict(name=ref.split(":", 1)[-1].replace("-", " ").title()),
                          is_synthetic=is_syn)
    return nid


def add_organizations(store) -> int:
    """Organization nodes from the scenario org table, plus a `sanctioned-under`
    edge for each designated (sanctioned) company.

    **Guarded and scenario-only by construction.** Real corpora have no such
    table and skip entirely — GFW ownership is 0.66% and OFAC's owner field is
    free text (ADR-016), so there is no real ownership graph to build. This is
    the synthetic stand-in for a paid ownership feed, flagged `is_synthetic`, and
    its designations point at the fictional SCENARIO-SDN authority, never OFAC.
    """
    if not _table_present(ORG_TABLE):
        return 0
    ensure_authorities(store)
    n = 0
    for r in read_table(ORG_TABLE):
        oid = r.get("org_id")
        if not oid:
            continue
        syn = _syn(r)
        designated = bool(r.get("designated"))
        store.upsert_node(
            _org_node_id(oid), "organization",
            dict(name=r.get("name"), jurisdiction=r.get("jurisdiction"),
                 role=r.get("role"), designated=designated,
                 registered_agent=(r.get("registered_agent_name")
                                   or r.get("registered_agent"))),
            is_synthetic=syn)
        n += 1
        if designated:
            t0 = (_epoch(r.get("incorporated")) or _epoch(r.get("acquired_at"))
                  or time.time())
            store.add_edge(
                "sanctioned-under", _org_node_id(oid),
                AUTHORITY_SCENARIO if syn else AUTHORITY_OFAC,
                t_start=t0, t_end=None,
                confidence=float(r.get("confidence") or 0.9), observed_at=t0,
                source=_src(r, "scenario-ownership"),
                source_ref=str(r.get("source_ref") or oid),
                props=dict(designated_entity=True,
                           listed_by=("scenario-sdn-fictional" if syn
                                      else "us-treasury-ofac")),
                is_synthetic=syn)
    return n


def add_ownership(store) -> int:
    """owned-by / operated-by edges from the scenario ownership table.

    This is what turns a lonely star into an ownership network: vessels connect
    to their operator, operators to their parent shell, and two hulls with no
    direct link converge on a shared ultimate owner — the identity-laundering
    signature the product exists to surface. Guarded on table presence like
    `add_organizations`.
    """
    if not _table_present(OWNERSHIP_TABLE):
        return 0
    n = 0
    for r in read_table(OWNERSHIP_TABLE):
        syn = _syn(r)
        src = _ownership_endpoint(r.get("src"), store, is_syn=syn)
        dst = _ownership_endpoint(r.get("dst"), store, is_syn=syn)
        if not src or not dst:
            continue
        kind = r.get("edge_kind") or "owned-by"
        if kind not in ("owned-by", "operated-by"):
            kind = "owned-by"
        t0 = (_epoch(r.get("valid_from")) or _epoch(r.get("acquired_at"))
              or time.time())
        try:
            store.add_edge(
                kind, src, dst, t_start=t0, t_end=_epoch(r.get("valid_to")),
                confidence=float(r.get("confidence") or 0.8), observed_at=t0,
                source=_src(r, "scenario-ownership"),
                source_ref=str(r.get("source_ref") or f"{src}->{dst}"),
                props=dict(edge_kind=kind, share=r.get("share")),
                is_synthetic=syn)
            n += 1
        except ValueError:
            # an endpoint type the edge spec forbids (e.g. org operated-by):
            # skip rather than crash the whole populate.
            continue
    return n


# --------------------------------------------------------------------------
# the whole thing
# --------------------------------------------------------------------------

def populate(store, *, only_intentional_gaps: bool = True) -> dict[str, int]:
    """Fill the graph from every landed table. Returns a count per step.

    Safe to re-run: the store is append-only and resolves latest-per-triple, so
    a second run re-asserts rather than duplicating. Re-asserting advances
    `observed_at`, which is also how a state edge's decay is refreshed.
    """
    ensure_ontology(store)
    ensure_authorities(store)

    vessels = load_vessels()
    known = set(vessels)
    counts = {"vessels_from_identity": add_vessels(store, vessels)}
    counts["flagged_to"] = add_flagged_to(store, vessels)
    ident_total, ident_superseded = add_identities(store, vessels)
    counts["identified_as"] = ident_total
    # renamed from identified_as_closed: closure is the end of GFW's query
    # window, not a change of identity. See add_identities().
    counts["identified_as_superseded"] = ident_superseded
    counts["met_with"], counts["encounters_skipped"] = add_encounters(store, known)
    counts["docked_at"], counts["port_visits_skipped"] = add_port_visits(store, known)
    counts["reported_gap"], counts["gaps_skipped"] = add_gaps(
        store, known, only_intentional=only_intentional_gaps)
    counts["sanctioned_findings"], counts["sanctioned_candidates"] = add_sanctions(
        store, known)
    # Ownership graph — no-op on a real-only corpus (tables absent), the paid-
    # feed stand-in on a scenario corpus (ADR-016). This is what gives the graph
    # view real structure to draw: shell chains and shared-owner clusters.
    counts["organizations"] = add_organizations(store)
    counts["ownership_edges"] = add_ownership(store)
    counts["vessel_nodes_total"] = store.n_nodes("vessel")
    counts["edges_total"] = store.n_edges()
    return counts


def decay_summary(store, *, at: float | None = None,
                  usable: float = 0.5, history: bool = False) -> dict:
    """How much of the graph has rotted, by edge type.

    `usable` is the confidence below which an edge should not carry an
    analyst-facing claim on its own. There is nothing sacred about 0.5 — it is
    stated here rather than buried so the number can be argued with.

    **Counts the CURRENT graph, not the append-only history.** The store keeps
    every assertion, so a re-run — or an interrupted run followed by a re-run —
    leaves several rows per (type, src, dst) and only the newest one is live.
    Summing over raw rows double-counts: the first live run reported 17,978
    `flagged-to` edges over 8,989 real ones, and 10,220 "already decayed" over a
    correspondingly smaller true figure. Both were history, counted as if
    current. `history=True` gives the raw view when that is what you want.
    """
    at = time.time() if at is None else at
    reg = store.edge_registry()
    if history:
        rows = store._con.execute(
            "SELECT edge_type, base_confidence, observed_at FROM edges").fetchall()
    else:
        # newest assertion per triple — the same latest-wins rule store.edges()
        # applies on read, expressed in SQL so it holds over the whole graph
        rows = store._con.execute(
            """
            SELECT e.edge_type, e.base_confidence, e.observed_at
            FROM edges e
            JOIN (SELECT edge_type, src, dst, MAX(rowid) AS rid
                  FROM edges
                  WHERE (edge_type, src, dst, observed_at) IN
                        (SELECT edge_type, src, dst, MAX(observed_at)
                         FROM edges GROUP BY edge_type, src, dst)
                  GROUP BY edge_type, src, dst) k
              ON e.rowid = k.rid
            """
        ).fetchall()
    out: dict[str, dict] = {}
    for etype, base, observed in rows:
        hl = reg.get(etype, {}).get("half_life_days")
        if hl is None:
            conf = base
        else:
            conf = base * 0.5 ** ((max(0.0, at - observed) / 86400.0) / hl)
        d = out.setdefault(etype, {"n": 0, "below_usable": 0, "conf_sum": 0.0,
                                   "half_life_days": hl})
        d["n"] += 1
        d["conf_sum"] += conf
        if conf < usable:
            d["below_usable"] += 1
    for d in out.values():
        d["mean_confidence"] = d["conf_sum"] / d["n"] if d["n"] else 0.0
        del d["conf_sum"]
    return out
