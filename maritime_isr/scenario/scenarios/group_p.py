"""Group P — the paperwork, and where it disagrees with the physics.

Area 4 of the IDEX Challenge 82 brief. *"Then generate the risk intelligence,
which is the actual point: compare what the notification declares against what
the track shows. Declared cargo against behaviour. Declared last port against
where the vessel actually was. Declared arrival window against observed
movement. Declared crew and ownership against registry and lists.
Contradictions between the paperwork and the physics become factors on the
Vessel of Interest."*

**The honest corpus is mostly honest.** Every vessel with a port call in the
window files a notification, and almost all of them file a true one. That
majority is not padding — it is the denominator. A paperwork rule measured only
against forged forms reports recall and nothing about how often it accuses an
agent who typed a date wrong, and the whole difficulty of Area 4 is that real
notifications are *sloppy* without being *dishonest*.

The authored contradictions:

  P1  declares a last port she was demonstrably nowhere near — she was 900 km
      away on the day she says she sailed from it
  P2  declares an arrival window her own track cannot meet, filed 48 hours out
  P3  no notification at all for a vessel that arrives and berths — the gap the
      requirement names explicitly
  P4  a notification for a vessel nothing in the picture can match — the other
      direction of the same gap
  P8  declares "Ballast — no cargo" while broadcasting a laden draught. The one
      cargo claim that can be checked against physics rather than inferred

And the decoys, each of which breaks a *different* naive rule:

  P5  DECOY: an ETA six hours out from her actual arrival. Paperwork is filed
      two days ahead and estimates are estimates; a rule that fires here fires
      on the whole fleet.
  P6  DECOY: the agent typed "GRANITE TRUIMPH". A transposition, unresolvable
      by exact match and *deliberately left unresolved* — the finding is "this
      form names a ship we cannot identify", not "this ship is suspicious".
  P7  DECOY: a scanned fax whose OCR loses two fields. An incomplete extraction
      is a fact about the document, not about the vessel.

**No document ever contains the truth flag.** The contradictions are authored
in what the paperwork *says* versus what the track already does; nothing in a
generated document names a scenario, and no extractor reads `scenario_truth`
(ADR-019).
"""
from __future__ import annotations

import random
from datetime import timedelta

from ..pans import NotificationSpec, eta_text, jitter_eta, mistype
from ..truth import (DECOY, FAMILY_DECOY, FAMILY_PAPERWORK,
                     TRUE_ANOMALY, ScenarioTruth)
from ..world import ScenarioWorld, week
from .common import V

__all__ = ["SCENARIOS", "build_notifications", "PANS_CARGOES"]

#: Cargo descriptions a form carries. Free text, as submitted.
PANS_CARGOES = (
    "Crude oil in bulk", "Gas oil", "Containerised general cargo",
    "Bulk cement", "Iron ore fines", "Refined products", "Bulk fertiliser",
    "Project cargo", "Frozen fish", "Bunkers only", "Ballast — no cargo",
)

#: Draught at or above which a hull is plainly carrying something, and the
#: phrases that declare she is not.
#:
#: **Deliberately duplicated from `anomaly.paperwork` rather than imported.**
#: The generator must not be defined by the detector it feeds: a corpus built
#: from a rule's own thresholds cannot falsify that rule, because every case it
#: contains was constructed to sit on the correct side of the line. These are
#: naval architecture and vocabulary, not tuning knobs, so both modules can hold
#: them honestly — and `tests/test_pans.py` asserts the two have not drifted,
#: which is the check that makes independence safe rather than merely separate.
LADEN_DRAUGHT_M = 12.0
BALLAST_PHRASES = ("BALLAST", "NO CARGO", "NIL CARGO", "IN BALLAST",
                   "LIGHT SHIP")

#: Agencies that file on a vessel's behalf.
_AGENTS = ("Coastal Marine Services", "Anchor Shipping Agency",
           "Meridian Port Agents", "Gateway Marine Pvt Ltd",
           "Sagar Shipping Services")

#: How far ahead of arrival a notification is filed. Real rules require 24-96
#: hours; the spread matters because the arrival-window rule measures the gap
#: between declaration and observation, and a fixed lead time would make that
#: gap a constant.
_FILE_LEAD_H = (24.0, 96.0)


def _honest_cargoes(v) -> tuple[str, ...]:
    """The cargoes this hull could truthfully declare, given what she broadcasts.

    **The background fleet's paperwork has to be honest, or the answer key is
    noise.** A cargo drawn uniformly declares "Ballast — no cargo" on one form
    in eleven, and the hull it lands on is broadcasting whatever draught her
    class broadcasts — so about a third of those forms contradict the physics by
    accident. `check_declared_ballast` then reports a contradiction it was
    handed, correctly, and the alert queue fills with vessels nobody authored
    and no analyst can be told anything true about.

    A contradiction between the paperwork and the track is the *product* here.
    It is authored as a scenario (P8) so that it can be counted, and it is
    excluded from the background so that the count means something.
    """
    if v.draught_m is not None and float(v.draught_m) >= LADEN_DRAUGHT_M:
        return tuple(c for c in PANS_CARGOES
                     if not any(p in c.upper() for p in BALLAST_PHRASES))
    return PANS_CARGOES


def _vessel_values(v, *, last_port, arrival_port, eta, rng,
                   cargo=None, owner=None):
    """The declared block for one hull, with her name typed by a human."""
    typed, _kind = mistype(v.name, rng)
    return {
        "vessel_name": typed,
        "imo": str(v.imo),
        "call_sign": v.call_sign,
        "flag": v.flag,
        "last_port": last_port,
        "arrival_port": arrival_port,
        "eta": eta_text(eta, rng),
        "cargo": cargo or rng.choice(_honest_cargoes(v)),
        "crew_count": str(rng.randint(12, 28)),
        "owner": owner or f"{v.name.title()} Shipping Ltd",
        "agent": rng.choice(_AGENTS),
    }


def _spec(world, key, *, index, fmt, received_at, values, omitted=()):
    v = V(world, key)
    nid = f"PANS-{index:04d}"
    return NotificationSpec(
        notification_id=nid, document_format=fmt, received_at=received_at,
        values=values, vessel_entity_id=v.entity_id if key else None,
        omitted=tuple(omitted))


# ==========================================================================
# P0 — the hulls the paperwork scenarios are about
# ==========================================================================

#: Where each paperwork hull sails from and to. Chosen so P1's declared last
#: port is somewhere her track demonstrably is not: she works the Karnataka
#: coast all window and the form says she sailed from Karachi, 1,500 km north.
_PAPER_VOYAGES = {
    "paper_false_origin":      ((13.30, 74.20), "Mundra", 4, 9),
    "paper_impossible_window": ((13.80, 73.90), "Kandla", 5, 14),
    "paper_no_filing":         ((14.10, 73.75), "Mangalore", 4, 20),
    "paper_honest_slip":       ((14.40, 73.60), "Mangalore", 6, 11),
    "paper_typo_name":         ((21.10, 69.20), "Vadinar", 6, 30),
    "paper_poor_scan":         ((19.40, 72.20), "JNPT", 7, 8),
    # P8 sails from inside the Gulf of Kutch, so her declared last port (Sikka,
    # ~66 km off her track) is *true*. Only the cargo claim is false, which is
    # what isolates the ballast check: a hull that lied about two things would
    # fire on whichever rule ran first and prove nothing about the second.
    # The point is mid-gulf and checked against the same land mask the afloat
    # validator uses — 22.00N/69.40E reads as water on a chart and is dry land
    # on the mask, which is precisely the failure that validator exists for.
    "paper_false_ballast":     ((22.55, 69.20), "Vadinar", 5, 6),
}


def p0_paperwork_hulls(world: ScenarioWorld) -> None:
    """Give the paperwork hulls a passage and an arrival to be wrong about.

    **The contradiction has to be between a document and a *track*, not between
    a document and nothing.** P1's whole finding is that her own AIS puts her
    900 km from the port her form names, so she needs an AIS track in the window
    that says where she really was; P3's finding is an arrival with no
    paperwork, so she needs a real arrival. A scenario that authored only the
    document would be measuring the extractor and calling it fusion.
    """
    from ..primitives.port_call import build_port_call
    from .common import add_port_visit, emit

    r = world.rng
    # **When she actually berths, not when the scenario nominally wanted her
    # to.** `build_port_call` sails her from `arrive_from` and she arrives when
    # the passage takes as long as it takes — up to two days off the `week()`
    # value the scenario names. Computing a filing time from the nominal value
    # produced *pre*-arrival notifications filed after the arrival, and the
    # authored ETAs then missed by an amount nobody wrote.
    arrivals: dict[str, object] = {}
    for key, (start, port, wk, hr) in _PAPER_VOYAGES.items():
        v = V(world, key)
        t0 = week(wk, hours=hr) - timedelta(hours=60)
        pts, spec = build_port_call(
            v, port, arrive_from=start, t_start=t0, rng=r,
            anchorage_hours=r.uniform(3.0, 14.0),
            berth_hours=r.uniform(18.0, 40.0))
        arrivals[key] = spec.t_arrive
        emit(world, key, pts)
        # `declare=False`: these hulls' notifications are authored below, and a
        # truthful AIS message-5 declaration alongside a false *paper* one would
        # be two different claims about one voyage — interesting, and not what
        # this group is measuring.
        #
        # **P8 is the exception, and has to be.** Her scenario is a paper claim
        # contradicted by her *draught*, and draught is carried in message 5 —
        # so a hull that declares nothing has no draught to contradict and the
        # check silently returns "not checkable". Suppressing her declaration
        # also kept her IMO out of the picture entirely, so her own form failed
        # to resolve and the scenario surfaced as an unidentifiable document
        # instead of the contradiction it was written to be. Her declaration is
        # truthful — destination Vadinar, laden draught — and only the paper
        # lies, which is exactly the contrast the scenario is for.
        add_port_visit(world, "P0", key, spec,
                       declare=(key == "paper_false_ballast"))
    world.pans_arrivals = arrivals


# ==========================================================================
# the honest majority, generated from the port calls the corpus already has
# ==========================================================================

def build_notifications(world: ScenarioWorld) -> list[NotificationSpec]:
    """One notification per port call, honest unless a scenario says otherwise.

    Reads the port-visit events the rest of the generator already produced, so
    a notification describes an arrival that actually happens. Building them
    from an independent list would produce a corpus where every notification is
    unmatched — which is the finding P3 and P4 exist to author deliberately,
    and worthless if it is also the background.

    Formats are assigned round-robin rather than at random. A random assignment
    at this corpus size leaves one format with three documents and makes any
    per-format measurement noise; round-robin gives every reader a comparable
    sample, which is what makes "the electronic feed drops in without rework"
    checkable.
    """
    from ..pans import FORMATS

    rng = random.Random(world.seed ^ 0x9A17)
    specs: list[NotificationSpec] = []
    skip = set(getattr(world, "pans_suppressed", ()) or ())

    visits = [e for e in world.events if e.kind == "port_visits"]
    visits.sort(key=lambda e: (e.t_start, e.entity_id))

    for i, ev in enumerate(visits):
        key = ev.entity_id.split(":", 1)[1]
        if ev.entity_id in skip:
            continue
        v = world.vessels.get(ev.entity_id)
        if v is None:
            continue
        port = (ev.props or {}).get("port_name")
        if not port:
            # Pre-arrival notification names a port. Where the corpus does not
            # know which port she called at, it has no business inventing one —
            # the previous fallback declared "Mundra" and the last-port rule
            # then contradicted a form the generator itself had made false.
            continue
        prior, prior_end = _previous_port(visits, ev)
        lead = rng.uniform(*_FILE_LEAD_H)
        received = ev.t_start - timedelta(hours=lead)
        if received < world.t0:
            continue
        # **She cannot declare a last port she has not reached yet.** The lead
        # is drawn independently of her previous call, so a 96-hour filing for
        # port N+1 could land before she berthed at port N — and the form then
        # states an origin that, at filing time, is in her future. The
        # last-port rule correctly contradicted those, on a vessel whose voyage
        # chain was entirely coherent. Filing starts once she is alongside.
        if prior_end is not None and received < prior_end:
            received = prior_end + timedelta(hours=rng.uniform(0.5, 4.0))
            if received >= ev.t_start:
                continue          # no room to file ahead: no notification
        values = _vessel_values(
            v, last_port=prior, arrival_port=port,
            eta=jitter_eta(ev.t_start, rng), rng=rng)
        # Real forms arrive incomplete. Dropping a field on about a fifth of
        # them is what stops the extractor being measured against a corpus
        # where every field is always present.
        omitted = tuple(k for k in ("crew_count", "owner", "call_sign", "cargo")
                        if rng.random() < 0.12)
        specs.append(_spec(world, key, index=len(specs) + 1,
                           fmt=FORMATS[i % len(FORMATS)],
                           received_at=received, values=values,
                           omitted=omitted))
    return specs


def _previous_port(visits, ev):
    """(where she sailed from, when she left there) per the corpus's own calls.

    The departure time comes back with the name because the caller needs it:
    a notification declaring "I sailed from Mundra" cannot be filed before she
    reached Mundra, and the filing lead is drawn without knowing when that was.
    """
    earlier = [e for e in visits
               if e.entity_id == ev.entity_id and e.t_end and e.t_end <= ev.t_start]
    if earlier:
        return ((earlier[-1].props or {}).get("port_name") or "sea",
                earlier[-1].t_end)
    return "sea", None


# ==========================================================================
# P1-P7 — the authored contradictions and the decoys
# ==========================================================================

def p_notifications(world: ScenarioWorld) -> None:
    """Author the paperwork scenarios and stash every spec on the world.

    Runs as a scenario so it sees the finished corpus: it needs the port calls
    the earlier groups produced, and a notification about an arrival that never
    happens is P4's job rather than an accident of ordering.
    """
    rng = random.Random(world.seed ^ 0x5C0F)
    specs = build_notifications(world)
    n = len(specs)
    arrived = getattr(world, "pans_arrivals", None) or {}

    def add(key, *, fmt, values, received_at, omitted=(), vessel=True):
        nonlocal n
        n += 1
        spec = NotificationSpec(
            notification_id=f"PANS-{n:04d}", document_format=fmt,
            received_at=received_at, values=values,
            vessel_entity_id=(V(world, key).entity_id if vessel and key
                              else None),
            omitted=tuple(omitted))
        specs.append(spec)
        return spec

    # ---- P1: a last port she was nowhere near --------------------------
    v = V(world, "paper_false_origin")
    t_arr = arrived.get("paper_false_origin") or week(4, hours=9)
    add("paper_false_origin", fmt="docx",
        received_at=t_arr - timedelta(hours=54),
        values=_vessel_values(v, last_port="Karachi",
                              arrival_port="Mundra",
                              eta=jitter_eta(t_arr, rng, hours=2.0), rng=rng,
                              cargo="Bulk cement"))
    world.truth.add(ScenarioTruth(
        scenario_id="P1", scenario_family=FAMILY_PAPERWORK,
        truth_class=TRUE_ANOMALY,
        entity_ids=[v.entity_id], t_start=t_arr - timedelta(hours=54),
        t_end=t_arr, expected_detection=True,
        expected_anomaly_types=["paperwork_contradiction"],
        notes=("Declares Karachi as her last port. Her own track has her off "
               "Karnataka, 900 km away, on the day she says she sailed. This "
               "is the paperwork-versus-physics case the requirement names.")))

    # ---- P2: an arrival window her track cannot meet -------------------
    v2 = V(world, "paper_impossible_window")
    t_arr2 = arrived.get("paper_impossible_window") or week(5, hours=14)
    add("paper_impossible_window", fmt="pdf",
        received_at=t_arr2 - timedelta(hours=48),
        values=_vessel_values(v2, last_port="Honnavar",
                              arrival_port="Kandla",
                              eta=t_arr2 - timedelta(hours=40), rng=rng,
                              cargo="Refined products"))
    world.truth.add(ScenarioTruth(
        scenario_id="P2", scenario_family=FAMILY_PAPERWORK, truth_class=TRUE_ANOMALY,
        entity_ids=[v2.entity_id], t_start=t_arr2 - timedelta(hours=48),
        t_end=t_arr2, expected_detection=True,
        expected_anomaly_types=["paperwork_contradiction"],
        notes=("Declares an arrival at Kandla forty hours before she could "
               "possibly be there, from a position her own track puts on the "
               "Karnataka coast.")))

    # ---- P4: a notification matching nothing in the picture ------------
    add(None, fmt="xlsx", vessel=False,
        received_at=week(3, hours=20),
        values={
            "vessel_name": "SEA HARRIER PRIDE",
            "imo": "1900513",
            "call_sign": "9XQZ2",
            "flag": "TGO",
            "last_port": "Bandar Abbas",
            "arrival_port": "Mundra",
            "eta": eta_text(week(4, hours=6), rng),
            "cargo": "Containerised general cargo",
            "crew_count": "19",
            "owner": "Harrier Lines FZE",
            "agent": "Gateway Marine Pvt Ltd",
        })
    world.truth.add(ScenarioTruth(
        scenario_id="P4", scenario_family=FAMILY_PAPERWORK,
        truth_class=TRUE_ANOMALY,
        # **The subject is the notification, not a vessel**, because there is
        # no vessel — that is the whole finding. A truth row needs a subject and
        # inventing a hull to hang it on would put a ship in the answer key that
        # the corpus does not contain.
        entity_ids=["notification:PANS-UNMATCHED"],
        t_start=week(3, hours=20), t_end=week(4, hours=6),
        expected_detection=True,
        expected_anomaly_types=["notification_unmatched"],
        notes=("A well-formed notification for a hull nothing in the picture "
               "holds: no AIS, no radar, no registry entry. The requirement "
               "names this as a gap to surface, not a parse failure.")))

    # ---- P5: DECOY, an ordinary estimate that missed by six hours ------
    v5 = V(world, "paper_honest_slip")
    t_arr5 = arrived.get("paper_honest_slip") or week(6, hours=11)
    add("paper_honest_slip", fmt="pdf",
        received_at=t_arr5 - timedelta(hours=50),
        values=_vessel_values(v5, last_port="Karwar",
                              arrival_port="Mangalore",
                              eta=t_arr5 + timedelta(hours=6.0), rng=rng))
    world.truth.add(ScenarioTruth(
        scenario_id="P5", scenario_family=FAMILY_DECOY, truth_class=DECOY,
        entity_ids=[v5.entity_id], t_start=t_arr5 - timedelta(hours=50),
        t_end=t_arr5, expected_detection=False, expected_anomaly_types=[],
        notes=("Filed fifty hours ahead, arrived six hours off the estimate. "
               "That is what an estimate is. A rule that fires here fires on "
               "the whole fleet.")))

    # ---- P6: DECOY, the agent's typo -----------------------------------
    v6 = V(world, "paper_typo_name")
    t_arr6 = arrived.get("paper_typo_name") or week(6, hours=30)
    typo = v6.name[:-3] + v6.name[-2] + v6.name[-3] + v6.name[-1]
    values6 = _vessel_values(v6, last_port="Porbandar",
                             arrival_port="Vadinar",
                             eta=jitter_eta(t_arr6, rng), rng=rng)
    values6["vessel_name"] = typo
    values6["imo"] = ""          # no IMO on the form: name is all there is
    add("paper_typo_name", fmt="docx",
        received_at=t_arr6 - timedelta(hours=36),
        values=values6, omitted=("imo", "call_sign"))
    world.truth.add(ScenarioTruth(
        scenario_id="P6", scenario_family=FAMILY_DECOY, truth_class=DECOY,
        entity_ids=[v6.entity_id], t_start=t_arr6 - timedelta(hours=36),
        t_end=t_arr6, expected_detection=False, expected_anomaly_types=[],
        notes=("A transposed pair of letters and no IMO on the form. She stays "
               "unresolved, and the finding is 'this form names a ship we "
               "cannot identify' — never a suspicion about the hull. Fuzzy "
               "matching would resolve her and would equally resolve a "
               "different ship with a similar name.")))

    # ---- P7: DECOY, a fax that OCR reads incompletely ------------------
    v7 = V(world, "paper_poor_scan")
    t_arr7 = arrived.get("paper_poor_scan") or week(7, hours=8)
    add("paper_poor_scan", fmt="pdf_scan",
        received_at=t_arr7 - timedelta(hours=40),
        values=_vessel_values(v7, last_port="Mumbai",
                              arrival_port="JNPT",
                              eta=jitter_eta(t_arr7, rng), rng=rng),
        omitted=("owner", "agent", "crew_count"))
    world.truth.add(ScenarioTruth(
        scenario_id="P7", scenario_family=FAMILY_DECOY, truth_class=DECOY,
        entity_ids=[v7.entity_id], t_start=t_arr7 - timedelta(hours=40),
        t_end=t_arr7, expected_detection=False, expected_anomaly_types=[],
        notes=("A scanned fax with three fields absent from the form and the "
               "rest read by OCR at reduced confidence. An incomplete "
               "extraction is a fact about the document, not about the "
               "vessel.")))

    # ---- P8: "no cargo" from a hull drawing a laden draught ------------
    #
    # The one cargo claim in the brief that can be *checked* rather than
    # inferred. A bulker declaring cement and riding high could be in ballast,
    # part-laden or lying, and motion does not separate those — so the general
    # case is not built. A hull declaring "no cargo" while broadcasting the
    # draught of a full one is arithmetic, and it needs a scenario of its own or
    # the check is carried by whatever the background happens to contradict.
    v8 = V(world, "paper_false_ballast")
    t_arr8 = arrived.get("paper_false_ballast") or week(5, hours=6)
    add("paper_false_ballast", fmt="xlsx",
        received_at=t_arr8 - timedelta(hours=44),
        values=_vessel_values(v8, last_port="Sikka", arrival_port="Vadinar",
                              eta=jitter_eta(t_arr8, rng), rng=rng,
                              cargo="Ballast — no cargo"))
    world.truth.add(ScenarioTruth(
        scenario_id="P8", scenario_family=FAMILY_PAPERWORK,
        truth_class=TRUE_ANOMALY,
        entity_ids=[v8.entity_id], t_start=t_arr8 - timedelta(hours=44),
        t_end=t_arr8, expected_detection=True,
        expected_anomaly_types=["paperwork_contradiction"],
        notes=("Declares 'Ballast — no cargo' on the form while broadcasting a "
               "laden Suezmax draught throughout the voyage. A hull in ballast "
               "does not draw that, and unlike a declared commodity this needs "
               "no cargo model to contradict.")))

    world.pans_specs = specs


def p3_arrival_with_no_notification(world: ScenarioWorld) -> None:
    """P3 — she berths and nobody filed for her.

    Implemented by suppression rather than by omission: her port call is real
    and lands like any other, and `build_notifications` is told to skip her. A
    scenario that simply never created the document would be indistinguishable
    from a generator bug.
    """
    v = V(world, "paper_no_filing")
    world.pans_suppressed = tuple(getattr(world, "pans_suppressed", ())) + (
        v.entity_id,)
    visits = [e for e in world.events
              if e.kind == "port_visits" and e.entity_id == v.entity_id]
    if not visits:
        return
    world.truth.add(ScenarioTruth(
        scenario_id="P3", scenario_family=FAMILY_PAPERWORK, truth_class=TRUE_ANOMALY,
        entity_ids=[v.entity_id], t_start=visits[0].t_start,
        t_end=visits[-1].t_end or visits[0].t_start,
        expected_detection=True,
        expected_anomaly_types=["arrival_without_notification"],
        notes=("She arrives, anchors and berths, and no notification was ever "
               "filed for her. The requirement names this gap in the same "
               "breath as the unmatched notification — the two are the same "
               "hole from opposite sides.")))


#: P3 must run before the builder so its suppression is in place; the builder
#: itself is inside `p_notifications`, which authors everything else.
SCENARIOS = (p0_paperwork_hulls, p3_arrival_with_no_notification,
             p_notifications)
