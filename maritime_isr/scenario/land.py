"""Write a generated world into the same tables real data lands in.

**Same tables, one flag.** Scenario rows go into `gfw_encounters`,
`gfw_loitering`, `gfw_port_visits`, `gfw_ais_gaps`, `gfw_vessel_identity`,
`ais_position` and `sanctioned_vessel_matches` — the exact tables the connectors
write and the exact tables the graph populator, the track engine and the fusion
core read. They are distinguished only by `is_synthetic` and by a
`synthetic-scenario` source id, which the envelope stamper refuses to let
disagree.

The reason is in ADR-019 and is worth restating: data in a parallel schema
proves nothing about the real system. If scenario rows took a different path —
their own tables, their own views, their own reader — then a green scenario run
would only demonstrate that the scenario path works. Landing them here means a
bug in the real reader breaks the scenario run, which is the entire point.

**Every table gets all five H3 resolutions**, computed from lat/lon by the one
shared helper, never derived from one another (ADR-015). `stamp_h3` already does
this; the discipline here is simply to call it on every positioned row and to
have the validator check the result rather than trusting the call.

`scenario_truth` lands too, in its own table, and is the one thing here that no
detection code may read.
"""
from __future__ import annotations

import hashlib
from datetime import timezone

from ..ingest.landing import land_table, stamp_envelope, stamp_h3
from ..fusion.radar_ais import (CONTACT_TABLE as T_RADAR_CONTACT,
                                CORRELATION_TABLE as T_RADAR_CORR)
from ..ingest.radar import TABLE as T_RADAR
from ..zones.transitions import ZONE_TRANSITION_TABLE as T_ZONE_TRANSITION
from ..ingest.sanctions_match import MATCH_TABLE
from .identifiers import SYNTHETIC_SOURCE_ID
from .nulls import NullMask, _draw as _draw01
from .radar_truth import TABLE as RADAR_TRUTH_TABLE
from .truth import TABLE as TRUTH_TABLE
from .world import ScenarioWorld

#: Table names, all shared with the real connectors except the truth table.
T_IDENTITY = "gfw_vessel_identity"
T_ENCOUNTERS = "gfw_encounters"
T_LOITERING = "gfw_loitering"
T_PORT_VISITS = "gfw_port_visits"
T_GAPS = "gfw_ais_gaps"
T_POSITIONS = "ais_position"
T_DETECTIONS = "scenario_detections"
#: Scenario sanctions listings. **Deliberately NOT "sanctions".** The real OFAC
#: snapshot lives in DuckDB as `ofac_sdn` with an entirely different shape, and
#: there is no conformed `sanctions` table on the operator's machine at all —
#: writing one would have invented a table the real system does not have.
T_SANCTIONS = "scenario_sanctions"
T_ORGS = "scenario_organizations"
T_OWNERSHIP = "scenario_ownership"

EVENT_TABLES = {
    "encounters": T_ENCOUNTERS,
    "loitering": T_LOITERING,
    "port_visits": T_PORT_VISITS,
    "gaps": T_GAPS,
}

#: Every table the generator writes. `scenario clear` uses this list, so a new
#: table added above is cleared automatically — a clear that silently missed a
#: table would leave orphan synthetic rows behind and quietly poison the next
#: real-vs-synthetic split.
ALL_TABLES = (T_IDENTITY, *EVENT_TABLES.values(), T_POSITIONS, T_DETECTIONS,
              T_SANCTIONS, T_ORGS, T_OWNERSHIP, MATCH_TABLE, T_RADAR,
              # Derived radar products. Listed so `scenario clear`
              # removes them too — a clear that silently missed a
              # table would leave orphan synthetic rows behind.
              T_RADAR_CORR, T_RADAR_CONTACT,
              # Zone transitions (ADR-030), for exactly the same reason and
              # after exactly the same bug: the first pipeline run over a
              # regenerated corpus landed 11,110 rows where it had computed
              # 5,654, because the previous corpus's transitions were still
              # there under different ids. `maritime_zone` and
              # `maritime_zone_cell` are deliberately NOT here — the geometry
              # layer is not scenario data, and clearing it would delete the
              # operator's own drawn areas every time the corpus is rebuilt.
              T_ZONE_TRANSITION,
              TRUTH_TABLE, RADAR_TRUTH_TABLE)


#: Anchorage ids a "different exit anchorage" can land on. Real places in the
#: AOI, so a stitched visit names somewhere a ship could plausibly have gone.
_OTHER_ANCHORAGES = ("Sikka", "Vadinar", "Mundra", "Kandla", "JNPT",
                     "Mumbai", "Mangalore", "Kochi")

#: Registry of the state each modelled port belongs to. GFW carries a flag on
#: every anchorage record; leaving it null on synthetic rows would be one more
#: column a filter could split the corpus on.
_PORT_FLAG = {"Karachi": "PAK", "Gwadar": "PAK"}


#: Fraction of real GFW anchorage records that carry no `name` at all.
#: Measured on the operator's corpus 2026-07-31 by `port_visit_forensics.py`:
#: `intermediateAnchorage.name` present on 1,633 of 3,000 (54.4%), start on
#: 54.5%, end on 57.0% — while `id`, `flag`, `lat`, `lon` and `topDestination`
#: are present on **100%**. So an unnamed anchorage is the normal case, not a
#: defect, and a synthetic corpus where every anchorage is named is separable
#: from the real one by `WHERE anchorage_name IS NULL`.
ANCHORAGE_NAME_NULL_RATE = 0.456


def _anchorage_record(name: str | None, aid: str | None, prefix: str,
                      *, at_dock: bool | None, keep_name: bool = True) -> dict:
    """One anchorage in the shape `ingest.gfw_events._anchorage_fields` writes.

    Real port visits carry a full record per anchorage — id, name, flag,
    position, whether the vessel was at a dock, distance from shore, GFW's own
    anchorage key and the destination vessels calling there declare. Emitting
    only the id and name would leave seven columns populated on real rows and
    null on every synthetic one, which is the separability problem this whole
    area exists to avoid, arriving through the least interesting door.

    `keep_name` is decided once per visit by the caller, not once per anchorage,
    because the real records agree within a visit: an unnamed anchorage tends to
    be unnamed at entry, stop and exit alike.
    """
    from .geography import ANCHORAGES, PORTS
    from .scenarios.common import _shore_km

    from ..ingest.gfw_events import _ANCHORAGE_KEYS

    if not (name or aid):
        return {f"{prefix}_{k}": None for k in _ANCHORAGE_KEYS}
    pos = (ANCHORAGES.get(name) or PORTS.get(name)) if name else None
    return {
        f"{prefix}_id": aid,
        # Dropped at the measured rate; `top_destination` below is kept, which
        # is the shape the real records have and the reason a demo can still
        # render a readable place for an anchorage GFW never named.
        f"{prefix}_name": name if keep_name else None,
        f"{prefix}_flag": _PORT_FLAG.get(name or "", "IND"),
        f"{prefix}_lat": round(pos[0], 4) if pos else None,
        f"{prefix}_lon": round(pos[1], 4) if pos else None,
        f"{prefix}_at_dock": at_dock,
        f"{prefix}_distance_from_shore_km": (round(_shore_km(*pos), 1)
                                             if pos else None),
        # sha256, not `hash()` — Python's string hash is salted per process, so
        # using it here would break byte-identical regeneration at a fixed seed.
        f"{prefix}_anchorage_id": (
            hashlib.sha256((aid or name).encode()).hexdigest()[:8]
            if (aid or name) else None),
        f"{prefix}_top_destination": (name or "").upper() or None,
    }


def assign_visit_structures(rows: list[dict], profile) -> None:
    """Give a batch of port visits the real structure mix, in place.

    **Stratified, not sampled.** Drawing each visit's class independently is the
    obvious implementation and it does not work at this scale: the corpus holds
    45 port visits, so a 40% class lands anywhere from 26% to 56% on ordinary
    luck, and the first seed tried came out at 64%. A mix that is only right in
    expectation is not right — a filter on `dwell_hours` would still separate
    the two populations, which is the whole thing this exists to prevent.

    So the rows are ordered by a hash of their event id and sliced at the
    cumulative fractions. The mix is then exact to rounding, and *which* visit
    gets which class is still deterministic, seed-stable and unrelated to
    anything a scenario controls — ordering by hash rather than by time matters,
    because ordering by time would hand every dwell to the first fortnight.
    """
    if not rows:
        return
    w = profile.visit_structure().value
    keys = sorted(w)
    total = sum(w[k] for k in keys) or 1.0

    ordered = sorted(rows, key=lambda r: _draw01(
        "gfw_port_visits", "visit_structure", str(r.get("event_id") or "")))
    n = len(ordered)
    i = 0
    for j, k in enumerate(keys):
        # The last class absorbs the rounding remainder, so every row is
        # assigned exactly once and no visit is left without a structure.
        stop = n if j == len(keys) - 1 else min(n, i + round(n * w[k] / total))
        for r in ordered[i:stop]:
            apply_visit_structure(r, k)
        i = stop


def apply_visit_structure(row: dict, cls: str) -> dict:
    """Give a synthetic port visit the same structure real ones have, in place.

    **Why a synthetic port visit cannot just be a clean dwell.** A GFW port
    visit is stitched from up to four sub-events and not all of them line up.
    Measured on the real corpus 2026-07-31: every visit has an observed stop,
    but **13% have entry and exit at different anchorages**, and `port_name` —
    which comes from the stop anchorage and nowhere else — is null on 45.6%,
    because the anchorage is present but unnamed. A generator that emits a
    named stop and matching endpoints on every single visit makes
    `WHERE dwell_hours IS NULL` a real-row detector, which is the null-rate
    failure family from `nulls.py` arriving through a third door.

    The class comes from `assign_visit_structures`, which allocates it from a
    **hash of the event id** rather than the generator's RNG — for the same
    reason masking does: which visits are dwells must not depend on how many
    random numbers the rest of the corpus happened to consume.

    The fields are then derived by the *same rule the real mapper applies*
    (`ingest.gfw_events._port_visit_structure`), rather than each being drawn at
    its own measured rate. Independent draws would produce rows that are
    marginally right and jointly impossible — a dwell with no stop — and a
    consumer that trusts the relationship would break on synthetic data only.

    **What this deliberately does not reproduce: the multi-week tail on
    `duration_hours`.** Real spans reach 2.3 years because GFW stitched across
    an interval longer than this entire eight-week window, and manufacturing
    that would mean emitting an event that never happened. So port-visit
    `duration_hours` remains a channel on which the two populations differ, it
    is recorded here rather than papered over, and `dwell_hours` — the field
    anything reasoning about time alongside should use — is the matched one.
    """
    u = _draw01("gfw_port_visits", "anchorage_pick",
                str(row.get("event_id") or ""))
    # Whether GFW named this visit's anchorages at all. Drawn once per visit,
    # not once per anchorage, because the real records agree within a visit.
    named = _draw01("gfw_port_visits", "anchorage_named",
                    str(row.get("event_id") or "")) >= ANCHORAGE_NAME_NULL_RATE
    port = row.get("port_name")
    pid = row.get("port_id")
    duration = row.get("duration_hours")

    # Deterministic second anchorage for the stitched case.
    other = _OTHER_ANCHORAGES[int(u * 1e6) % len(_OTHER_ANCHORAGES)]
    if other == port:
        other = _OTHER_ANCHORAGES[(int(u * 1e6) + 1) % len(_OTHER_ANCHORAGES)]
    other_id = f"anch:{other.lower()}"

    if cls == "dwell":
        stop, agree, end_id, end_name = True, True, pid, port
    elif cls == "no_stop":
        # No stop means no intermediate anchorage, which means `port_id` and
        # `port_name` are null on the real row too — they are read from the
        # stop and from nothing else. Nulling them here is not masking, it is
        # the same absence.
        stop, agree, end_id, end_name = False, True, pid, port
        row["port_id"] = row["port_name"] = None
    elif cls == "anchorages_differ":
        stop, agree, end_id, end_name = True, False, other_id, other
    else:                                    # unknown: too few anchorage ids
        stop, agree, end_id, end_name = False, None, None, None
        row["port_id"] = row["port_name"] = None

    # `port_name` is read from the stop anchorage's `name` and from nowhere
    # else, so it inherits that column's nullity exactly. Without this it would
    # be 100% populated on synthetic rows against 54.4% on real ones — the same
    # separability hole, one column over.
    if not named:
        row["port_name"] = None

    row.update({
        **_anchorage_record(port, pid, "start_anchorage", at_dock=False,
                            keep_name=named),
        **_anchorage_record(port if stop else None, pid if stop else None,
                            "anchorage", at_dock=True, keep_name=named),
        **_anchorage_record(end_name, end_id, "end_anchorage", at_dock=False,
                            keep_name=named),
        "visit_confidence": {"dwell": 4, "anchorages_differ": 3,
                             "no_stop": 2}.get(cls),
        "visit_has_stop": stop,
        "visit_anchorages_agree": agree,
        "visit_port_id": pid if stop else (pid or end_id),
        # Falls back to the destination when GFW named nothing, which is what
        # lets a demo render ALANG instead of `ind-ind-76`.
        "visit_port_name": ((port if stop else (port or end_name)) if named
                            else (port or end_name or "").upper() or None),
        "visit_port_source": ("intermediate" if stop
                              else "start" if pid or port
                              else "end" if end_id or end_name else None),
        # Synthetic anchorages are always named, so the name always comes from
        # the anchorage rather than from a topDestination fallback. Emitting the
        # column keeps the shape identical to a real row; the *value* differing
        # is a real fidelity gap and is recorded in the module docstring rather
        # than faked by nulling a name we do have.
        "visit_port_name_source": ("anchorage_name"
                                   if (port if stop else (port or end_name))
                                   else None),
        "dwell_hours": duration if (stop and agree) else None,
        "port_visit_id": f"pv:{row.get('event_id')}",
        # The connector carries distance context on both ends of every event.
        "end_distance_from_shore_km": row.get("start_distance_from_shore_km"),
        "end_distance_from_port_km": row.get("start_distance_from_port_km"),
    })
    return row


def _stamp(row: dict, *, source_ref: str, acquired_at, confidence=None) -> dict:
    """Envelope + H3, the two things no row may land without."""
    stamp_envelope(row, source_id=SYNTHETIC_SOURCE_ID, source_ref=source_ref,
                   acquired_at=acquired_at.astimezone(timezone.utc),
                   confidence=confidence, is_synthetic=True)
    return stamp_h3(row)


def land_world(world: ScenarioWorld) -> dict[str, int]:
    """Land everything. Returns rows **actually written**, per table.

    The count comes back from `land_table`, which reports the size of each
    partition after the merge — not the number of rows handed to it. Reporting
    what we attempted rather than what landed is a bug class this codebase has
    already produced four times (STATE.md), and it is not going to be repeated
    here: `scenario status` reads these tables back from disk.
    """
    written: dict[str, int] = {}
    # Null-rate matching against the real corpus. Without this, synthetic rows
    # are separable from real ones by a single IS NOT NULL filter on any field
    # the real data leaves empty — see nulls.py.
    mask = NullMask(world.profile)
    world.null_mask = mask

    def land(rows: list[dict], table: str, key_fields, day_field="acquired_at"):
        for r in rows:
            mask.apply(r, table=table,
                       key=str(r.get("vessel_id") or r.get("event_id")
                               or r.get("source_ref") or ""))
        if not rows:
            written.setdefault(table, 0)
            return
        w = land_table(rows, table=table, key_fields=key_fields,
                       day_field=day_field)
        written[table] = written.get(table, 0) + sum(w.values())

    # ---- identity intervals ----
    identity_rows = []
    for v in world.vessels.values():
        ivs = world.identity.for_vessel(v.entity_id)
        # Fold the per-field intervals into the row shape GFW lands: one record
        # per distinct identity state, carrying name, flag, mmsi, imo together.
        boundaries = sorted({iv.valid_from for iv in ivs}
                            | {iv.valid_to for iv in ivs if iv.valid_to})
        for i, t in enumerate(boundaries[:-1] if len(boundaries) > 1
                              else boundaries):
            snap = world.identity.snapshot_at(v.entity_id, t)
            nxt = boundaries[i + 1] if i + 1 < len(boundaries) else world.t1
            superseded = any(
                iv.superseded for iv in ivs
                if iv.valid_to is not None and iv.valid_to == nxt)
            row = dict(
                vessel_id=v.entity_id,
                record_kind="registry",
                mmsi=str(snap.get("mmsi") or ""),
                imo=str(snap.get("imo") or ""),
                ship_name=snap.get("name"),
                normalised_name=(snap.get("name") or "").upper() or None,
                call_sign=snap.get("call_sign"),
                flag=snap.get("flag"),
                length_m=v.length_m,
                width_m=v.beam_m,
                draught_m=v.draught_m,
                tonnage_gt=v.dwt,
                vessel_class=v.vessel_class,
                gear_types=None,
                registry_source="synthetic-scenario",
                valid_from=t,
                valid_to=nxt,
                # The distinction the real populator had to learn: an interval
                # that ends because the identity changed, versus one that ends
                # because the window did.
                interval_superseded=bool(superseded),
            )
            identity_rows.append(_stamp(
                row, source_ref=f"{v.entity_id}:identity:{i}", acquired_at=t))
    land(identity_rows, T_IDENTITY,
         ("vessel_id", "record_kind", "mmsi", "ship_name", "valid_from"),
         day_field="valid_from")

    # ---- behaviour events ----
    by_kind: dict[str, list[dict]] = {k: [] for k in EVENT_TABLES}
    for ev in world.events:
        v = world.vessels.get(ev.entity_id)
        row = dict(
            event_id=ev.event_id,
            event_kind=ev.kind,
            event_type=ev.kind.rstrip("s").upper(),
            start_time=ev.t_start,
            end_time=ev.t_end,
            lat=ev.lat, lon=ev.lon,
            vessel_id=ev.entity_id,
            mmsi=str(v.mmsi) if v else None,
            imo=str(v.imo) if v else None,
            ship_name=v.name if v else None,
            flag=v.flag if v else None,
            vessel_type=v.vessel_class if v else None,
            counterpart_vessel_id=ev.counterpart_entity_id,
            counterpart_mmsi=(str(world.vessels[ev.counterpart_entity_id].mmsi)
                              if ev.counterpart_entity_id in world.vessels
                              else None),
            counterpart_name=(world.vessels[ev.counterpart_entity_id].name
                              if ev.counterpart_entity_id in world.vessels
                              else None),
            counterpart_flag=(world.vessels[ev.counterpart_entity_id].flag
                              if ev.counterpart_entity_id in world.vessels
                              else None),
            **ev.props,
        )
        row.setdefault("duration_hours",
                       (ev.t_end - ev.t_start).total_seconds() / 3600.0)
        by_kind[ev.kind].append(_stamp(
            row, source_ref=ev.event_id, acquired_at=ev.t_start,
            confidence=None))
    # Allocated over the whole batch rather than per row — see the docstring.
    assign_visit_structures(by_kind["port_visits"], world.profile)
    for kind, table in EVENT_TABLES.items():
        land(by_kind[kind], table, ("event_id",), day_field="start_time")

    # ---- AIS positions ----
    pos_rows = []
    for eid_, reports in world.ais.items():
        v = world.vessels[eid_]
        for rep in reports:
            # The MMSI on a position row is the one in force at that instant,
            # which is what makes B1's phoenix and B5's clone land correctly:
            # the same hull broadcasts different numbers at different times.
            mmsi_at = world.identity.current(eid_, "mmsi", at=rep.t)
            row = dict(
                mmsi=int(mmsi_at.value if mmsi_at else v.mmsi),
                imo=int(v.imo),
                lat=rep.lat, lon=rep.lon,
                sog_kn=rep.sog_kn, cog_deg=rep.cog_deg,
                heading_deg=rep.heading_deg,
                nav_status=rep.nav_status,
                msg_type=1,
                ts=rep.t,
                receiver=rep.receiver,
                n_receipts=1,
                vessel_id=eid_,
            )
            pos_rows.append(_stamp(
                row, source_ref=f"{eid_}:{rep.t.isoformat()}",
                acquired_at=rep.t))
    land(pos_rows, T_POSITIONS, ("mmsi", "ts", "lat", "lon"), day_field="ts")

    # ---- SAR contacts ----
    det_rows = []
    for c in world.sar_contacts:
        row = dict(
            detection_id=c.detection_id, lat=c.lat, lon=c.lon, ts=c.t,
            length_m=c.length_m, score=0.9, scene_id=c.scene_id,
            matched_mmsi=None,
        )
        det_rows.append(_stamp(row, source_ref=c.detection_id, acquired_at=c.t,
                               confidence=0.9))
    land(det_rows, T_DETECTIONS, ("detection_id",), day_field="ts")

    # ---- sanctions listings and vessel matches ----
    sanc_rows, match_rows = [], []
    designated_orgs = {s["target_entity_id"]: s for s in world.sanctions
                       if s.get("target_entity_id")}
    for s in world.sanctions:
        row = dict(registry=s["registry"], entry_id=s["entry_id"],
                   name=s["name"], entry_type=s["entry_type"],
                   imo=s.get("imo"), flag=s.get("flag"),
                   program=s["program"], as_of=s["as_of"],
                   valid_from=s["as_of"], valid_to=None,
                   target_entity_id=s.get("target_entity_id"))
        sanc_rows.append(_stamp(row, source_ref=s["entry_id"],
                                acquired_at=s["as_of"]))
    land(sanc_rows, T_SANCTIONS, ("registry", "entry_id", "as_of"),
         day_field="as_of")

    # A vessel is matched when its operator or owner is a designated org. The
    # tier is `imo` because the link runs through a hull we minted — but the
    # authority is the fictional list, never OFAC.
    for e in world.corporate.edges:
        if e.dst not in designated_orgs or not e.src.startswith("vessel:"):
            continue
        v = world.vessels.get(e.src)
        if v is None:
            continue
        s = designated_orgs[e.dst]
        row = dict(
            vessel_id=v.entity_id, match_tier="imo", is_finding=True,
            ofac_ent_num=s["entry_id"], ofac_name=s["name"],
            ofac_program=s["program"], ofac_owner=s["name"],
            ofac_imo=None, sanctions_as_of=s["as_of"],
            vessel_name=v.name, vessel_imo=str(v.imo), vessel_flag=v.flag,
            registry=s["registry"],
            # `ofac_name` means two different things across the two corpora and
            # a reader cannot tell which without this column. The real matcher
            # only ever matches `sdn_type='vessel'` rows, so its `ofac_name` is
            # the LISTED VESSEL's name; here the designation is against an ORG
            # and the vessel is reached through ownership, so `ofac_name` is a
            # company. Without the distinction, "our name disagrees with the
            # listing" fires on every scenario row — a hull name never equals a
            # company name — and the identity-laundering signal it is supposed
            # to carry becomes noise.
            listed_entity_type="organisation",
        )
        match_rows.append(_stamp(row, source_ref=f"{v.entity_id}:{s['entry_id']}",
                                 acquired_at=s["as_of"], confidence=0.95))
    land(match_rows, MATCH_TABLE, ("vessel_id", "ofac_ent_num"),
         day_field="sanctions_as_of")

    # ---- corporate structure ----
    org_rows = []
    for o in world.corporate.orgs.values():
        row = dict(
            org_id=o.entity_id, name=o.name, jurisdiction=o.jurisdiction,
            registered_agent=o.registered_agent,
            registered_agent_name=world.corporate.agents.get(o.registered_agent),
            address_id=o.address,
            address_text=world.corporate.addresses.get(o.address),
            role=o.role, designated=o.designated,
            incorporated=o.incorporated, dissolved=o.dissolved,
            successor_of=o.successor_of, notes=o.notes,
        )
        org_rows.append(_stamp(row, source_ref=o.entity_id,
                               acquired_at=o.incorporated or world.t0))
    land(org_rows, T_ORGS, ("org_id",), day_field="acquired_at")

    own_rows = []
    for i, e in enumerate(world.corporate.edges):
        row = dict(edge_kind=e.kind, src=e.src, dst=e.dst,
                   valid_from=e.valid_from, valid_to=e.valid_to,
                   share=e.share, notes=e.notes)
        own_rows.append(_stamp(row, source_ref=f"own:{i}",
                               acquired_at=max(e.valid_from, world.t0),
                               confidence=e.confidence))
    land(own_rows, T_OWNERSHIP, ("edge_kind", "src", "dst", "valid_from"),
         day_field="acquired_at")

    # ---- coastal radar ----
    #
    # **Landed through `ingest.radar`, not written here.** Every other block in
    # this function builds rows and hands them to `land`, because the GFW-shaped
    # tables have no connector of their own in this codebase — the scenario
    # generator IS their producer. Radar does have a connector, and routing
    # scenario plots around it would have left the connector untested while
    # claiming radar was "a connector, not a core change". So the simulator
    # produces feed records and the connector conforms, validates, stamps and
    # lands them, exactly as it would for a real station feed.
    if world.radar is not None and world.radar.plots:
        from ..ingest.radar import land_plots
        w = land_plots((p.as_feed_record() for p in world.radar.plots),
                       source_id=SYNTHETIC_SOURCE_ID,
                       source_ref="synthetic-radar-network",
                       is_synthetic=True)
        written[T_RADAR] = sum(w.values())
    else:
        written.setdefault(T_RADAR, 0)

    # ---- ground truth, quarantined ----
    truth_rows = []
    for t in world.truth:
        row = t.as_row()
        truth_rows.append(_stamp(row, source_ref=t.scenario_id,
                                 acquired_at=t.t_start))
    land(truth_rows, TRUTH_TABLE, ("scenario_id",), day_field="t_start")

    radar_truth_rows = []
    if world.radar is not None:
        for ep in world.radar.truth:
            row = ep.as_row()
            radar_truth_rows.append(_stamp(row, source_ref=ep.episode_id,
                                           acquired_at=ep.t_start))
    land(radar_truth_rows, RADAR_TRUTH_TABLE, ("episode_id",),
         day_field="t_start")

    return written
