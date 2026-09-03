"""The wider fleet — the traffic that makes the picture a picture.

The corpus this module widens had 253 hulls: the 120-odd named cast the
scenarios need, a 40-boat fishing fleet, and 96 generic merchants from
`commercial.py` that all ran the same three-port coastal rotation. That is
enough to exercise every detector and it is not enough to look like the Arabian
Sea. Opened on a map it reads as sparse; opened in the Watch tab it reads as a
handful of ships, most of them interesting, which is the opposite of the problem
an operator actually has.

**What this module adds is variety, not volume.** Volume alone would have been
one line: raise `DEFAULT_FLEET_SIZE`. That would have produced four hundred more
hulls all doing the same thing, and it would have made the corpus *worse* in a
specific, measurable way — see below.

The one rule that shapes everything here
----------------------------------------
`tracks/vessel_type.py` infers what kind of ship a radar contact is **from
motion alone**, and it is trained on this corpus with the declared class as its
label. So a hull labelled `fishing` that steams a straight line at 13 knots is
not a harmless bit of filler: it is a mislabelled training example, and it moves
the measured confusion matrix — the number the product quotes when it says what
it can and cannot tell apart. Every archetype below therefore pairs a class with
a **behaviour**, and `scenarios/fleet_traffic.py` generates that behaviour and
nothing else for that archetype. :data:`ARCHETYPES` carries the kinematic
envelope each one must land inside, and `validate.py` fails the build when a
hull falls outside her own archetype's band. A label and its motion cannot drift
apart without the generator saying so.

Four classes had to be added to the generator to do this honestly — container,
tug, osv, ferry (see `profile.CLASS_PRIORS`). Three of them are the reason the
existing corpus's type classifier looked better than it was: it had never been
shown a hull that works entirely inside a harbour, or one that spends a day
stationary next to an oil platform, so it had never had the chance to be wrong
about one.

Additivity, and why it is enforced rather than intended
-------------------------------------------------------
Every hull here is minted **after** every existing hull, from a **derived RNG**
(`world.seed ^ FLEET_RNG_SALT`), and every scenario that moves them draws from
that same derived stream rather than from `world.rng`. This is not tidiness. The
comment on `cast.LATE_ADDITIONS` records what happens otherwise: adding a single
Suezmax through the shared stream re-rolled the whole background fleet behind it
and moved the vessel-type model's measured coarse accuracy from above its 75%
floor to 65% — a number that looked exactly like a regression and was not one.
Four hundred hulls added the same way would have made every previously measured
figure in this project incomparable in one commit.

So the corpus generated at seed 7 before this module existed is still, hull for
hull and fix for fix, inside the corpus generated at seed 7 after it. The one
thing that legitimately changes is the paperwork: `group_p` files a pre-arrival
notification for every port call in the window, and there are now more calls.

Base rate
---------
Almost every hull here is **boring**, and that is the deliberate part. See
`scenarios/group_w.py` for the number chosen and the ADR-004 argument for it.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import timedelta

from .primitives.org import Organization
from .primitives.vessel import make_vessel

#: Salt for the derived RNG. Any stream that touches a fleet hull comes from
#: `random.Random(world.seed ^ FLEET_RNG_SALT)` or a child of it, never from
#: `world.rng` — see the module docstring.
FLEET_RNG_SALT = 0x5EA1


def fleet_rng(world, *, salt: int = 0) -> random.Random:
    """The fleet's own random stream, deterministic in the world's seed."""
    return random.Random(world.seed ^ FLEET_RNG_SALT ^ (salt * 0x9E37))


# ==========================================================================
# archetypes
# ==========================================================================

@dataclass(frozen=True)
class Archetype:
    """A class of ship *and* the way that class actually moves.

    `sog_p50`, `sog_p90` and `turn_deg_min` are the envelope the generated
    motion has to land inside, in knots and in degrees of course change per
    minute. They are stated here — beside the trade, where an author reads them
    — rather than inside the validator, because the pairing is the point: an
    archetype is a promise that this label and this motion belong together, and
    the validator is only the thing that checks the promise was kept.

    The bands are wide. They are not a tuning surface and they are not fitted to
    the output; they are the range within which the *label* is still true, so
    that a trawler steaming a straight line at 13 knots fails the build and a
    trawler having an unusually brisk day does not.

    **Which end of each band carries the meaning, and why it is not symmetric.**

    `sog_p50` is bounded above and, for every class but the trawler, not below.
    A merchant's median speed over her own track is close to zero and that is
    not a defect: she spends twenty hours alongside for every six steaming, so
    half her positions are a berth. Requiring a floor made the build fail on
    forty perfectly ordinary hulls, and the only way to satisfy it would have
    been to stop generating berths — which would delete the port picture to
    protect a statistic. The *ceiling* is where the meaning is: a hull labelled
    `fishing` whose median is eleven knots is a merchant wearing a trawler's
    label, and that is the failure this catches.

    The trawler is the exception and keeps her floor, because for her the claim
    runs the other way: fishing must be the majority of her track, or the label
    is false. `fleet_traffic._WORK_TO_PASSAGE` is what makes it true.

    `sog_p90` is bounded below, and that is where "she is a fast ship" lives. A
    container hull that never exceeds nine knots never got out of the approach,
    whatever her registry says. The trawler's `sog_p90` floor is the loosest in
    the table and deliberately so: a boat working a ground twenty miles out
    spends nine tenths of her track fishing, so even her ninetieth percentile is
    a working speed. For her the discriminating claims are the median ceiling
    and the turn-rate floor, and the band says so by not pretending otherwise.
    """
    key: str
    vessel_class: str
    count: int
    trade: str
    motion: str
    sog_p50: tuple[float, float]
    sog_p90: tuple[float, float]
    turn_deg_min: tuple[float, float]

    def hull_key(self, i: int) -> str:
        return f"fl_{self.key}_{i:02d}"

    def hull_keys(self) -> list[str]:
        return [self.hull_key(i) for i in range(self.count)]

    def violations(self, m: "Motion") -> list[str]:
        """Which of this archetype's three bands `m` falls outside of.

        Empty means the label and the motion agree. The strings are written to
        be read by whoever broke it, so each one names the band it missed and
        restates what the archetype claims to be — a bare "0.42 not in
        (1.50, 30.00)" tells an author nothing about which promise was broken.
        """
        out = []
        for name, got, (lo, hi) in (
                ("median speed", m.sog_p50, self.sog_p50),
                ("90th-percentile speed", m.sog_p90, self.sog_p90),
                ("mean turn rate", m.turn_deg_min, self.turn_deg_min)):
            if not (lo <= got <= hi):
                unit = "deg/min" if "turn" in name else "kn"
                out.append(f"{name} {got:.2f} {unit} is outside "
                           f"{lo:.2f}-{hi:.2f} {unit}")
        return out


@dataclass(frozen=True)
class Motion:
    """What a hull actually did, in the three terms an archetype promises.

    Measured on the **integrated truth**, not the emitted AIS. The question this
    answers is whether the generator produced the motion the label claims, and
    the emitter's position noise and decimation are a separate concern with
    their own checks — folding them in here would mean a hull could pass or fail
    on how often she happened to report.
    """
    sog_p50: float
    sog_p90: float
    turn_deg_min: float
    n_points: int


#: Longest gap between two integrated points that still counts as continuous
#: motion. A hull's track is several segments — a passage, a berth, another
#: passage — and the join between two of them is a jump in time during which she
#: did whatever she liked. Averaging a course change across that join would
#: charge her a turn she took over two days as though she took it in a minute.
MOTION_MAX_STEP_MIN = 60.0


def measure_motion(points) -> Motion:
    """Summarise an integrated track the way :class:`Archetype` states its band.

    One definition, used by both the validator and any test that wants to ask
    the same question — because two definitions of "her turn rate" that drift
    apart would let a hull pass the build and fail the classifier.
    """
    from .primitives.track import angular_diff_deg

    pts = sorted(points, key=lambda p: p.t)
    sog = sorted(p.sog_kn for p in pts)
    n = len(sog)
    if n == 0:
        return Motion(0.0, 0.0, 0.0, 0)
    p50 = sog[n // 2]
    p90 = sog[min(n - 1, int(n * 0.90))]

    turned = 0.0
    minutes = 0.0
    for a, b in zip(pts, pts[1:]):
        dt_min = (b.t - a.t).total_seconds() / 60.0
        if dt_min <= 0.0 or dt_min > MOTION_MAX_STEP_MIN:
            continue
        turned += abs(angular_diff_deg(a.cog_deg, b.cog_deg))
        minutes += dt_min
    return Motion(p50, p90, turned / minutes if minutes else 0.0, n)


#: The wider fleet, by trade.
#:
#: **Counts are the mix of the Arabian Sea, not a round number split evenly.**
#: The Indian west coast carries far more small fishing craft than anything
#: else, then coastal cargo and product tankers, then a thin tail of crude and
#: container tonnage. A corpus with equal numbers of VLCCs and trawlers would be
#: easier to classify and would be a picture of nowhere.
ARCHETYPES: tuple[Archetype, ...] = (
    Archetype(
        "box", "container", 40,
        trade="liner container service on a fixed west-coast rotation",
        motion=("long straight legs at 17-21 kn between scheduled calls, short "
                "priority berths, almost no waiting at anchor"),
        sog_p50=(0.0, 21.5), sog_p90=(14.0, 23.5), turn_deg_min=(0.0, 3.5)),
    Archetype(
        "feeder", "general_cargo", 46,
        trade="coastal feeder working the smaller west-coast berths",
        motion=("11-14 kn coastal passages that follow the coastline, so more "
                "course changes than a deep-sea leg, and long berths"),
        sog_p50=(0.0, 14.5), sog_p90=(9.0, 16.0), turn_deg_min=(0.0, 5.0)),
    Archetype(
        "product", "product_tanker", 50,
        trade="refined-product runs into the Gulf of Kutch terminals",
        motion=("12-15 kn passages, a short wait at the terminal anchorage, "
                "then a long berth while she discharges"),
        sog_p50=(0.0, 15.0), sog_p90=(10.5, 16.5), turn_deg_min=(0.0, 4.5)),
    Archetype(
        "aframax", "Aframax", 20,
        trade="crude in from the Gulf of Oman to Vadinar and Sikka",
        motion=("one long entry leg from the northwest corridor at 13-15 kn, "
                "very few course changes, then a multi-day discharge"),
        sog_p50=(0.0, 15.5), sog_p90=(11.0, 17.0), turn_deg_min=(0.0, 3.0)),
    Archetype(
        "vlcc", "VLCC", 12,
        trade="very large crude carrier discharging at Sikka",
        motion=("the slowest and straightest thing in the picture: 12-15 kn, a "
                "turn rate limited by 300,000 tonnes of inertia"),
        sog_p50=(0.0, 15.5), sog_p90=(10.0, 17.0), turn_deg_min=(0.0, 2.5)),
    Archetype(
        "bulker", "bulker", 46,
        trade="dry bulk waiting for a berth at Kandla and Mundra",
        motion=("a passage at 12-14 kn and then a day or more at anchor — the "
                "anchorage wait is most of her track, which is why her median "
                "speed sits far below her passage speed"),
        sog_p50=(0.0, 13.5), sog_p90=(10.0, 15.5), turn_deg_min=(0.0, 6.0)),
    Archetype(
        "reefer", "reefer", 16,
        trade="refrigerated cargo running fish and produce north to Mumbai",
        motion=("the fastest merchant class here — 17-20 kn, because the cargo "
                "is perishable, and short turnarounds"),
        sog_p50=(0.0, 21.0), sog_p90=(15.0, 22.5), turn_deg_min=(0.0, 4.0)),
    Archetype(
        "trawler", "fishing", 90,
        trade="coastal trawlers working the Gujarat and Konkan grounds",
        motion=("transit out at 8-10 kn, then thirty to sixty hours of working "
                "the ground at 2.5-4 kn with a course change every half hour — "
                "a third of a merchant's speed and several times her turn "
                "rate. The working spell has to outlast the passage or the "
                "median is a steaming speed and the label is false"),
        sog_p50=(1.5, 8.0), sog_p90=(3.0, 12.5), turn_deg_min=(1.0, 30.0)),
    Archetype(
        "tug", "tug", 24,
        trade="harbour tugs and berthing assistance at the major ports",
        motion=("short darting jobs inside one harbour: out at 9-11 kn, an hour "
                "or two working alongside, back to the mooring. Tiny spread, a "
                "turn rate several times any merchant's, and — the part nothing "
                "else in the fleet shares — she never leaves the harbour"),
        sog_p50=(0.0, 8.0), sog_p90=(4.0, 14.0), turn_deg_min=(1.0, 40.0)),
    Archetype(
        "osv", "osv", 16,
        trade="offshore supply vessels serving the Bombay High installations",
        motion=("a 10-13 kn run out to the field, then twelve to thirty hours "
                "holding station alongside a platform, then back"),
        sog_p50=(0.0, 12.5), sog_p90=(9.0, 15.0), turn_deg_min=(0.0, 12.0)),
    Archetype(
        "ferry", "ferry", 14,
        trade="passenger ferries on short fixed harbour runs",
        motion=("the same twenty-mile leg over and over at 14-17 kn with a "
                "turnaround at each end — high speed, small spread, and a "
                "repeat rate nothing else in the picture has"),
        sog_p50=(0.0, 17.5), sog_p90=(8.0, 20.5), turn_deg_min=(0.0, 8.0)),
    Archetype(
        "dhow", "dhow", 20,
        trade="small coastal dhows moving cargo between minor landings",
        motion=("6-8 kn inshore hops of a few hours with long spells at anchor "
                "between them, never far from the beach"),
        sog_p50=(0.0, 8.5), sog_p90=(4.0, 10.5), turn_deg_min=(0.0, 12.0)),
)

ARCHETYPE_BY_KEY: dict[str, Archetype] = {a.key: a for a in ARCHETYPES}


#: Hulls the wider fleet's own scenarios need (group W), minted after the bulk.
#:
#: **Separate from `ARCHETYPES` on purpose.** A hull that a scenario places by
#: hand cannot also be running an ordinary rotation — `world.add_track` refuses
#: to put one ship in two places at once, and rightly. Keeping the scenario
#: hulls in their own list means `fleet_traffic` can move *every* archetype hull
#: without a skip list, which is one fewer place for a hull to be silently
#: dropped from the picture.
#:
#: (key, vessel_class) — the archetype each belongs to is in her key.
SCENARIO_HULLS: tuple[tuple[str, str], ...] = (
    # ---- true anomalies (group W) ----
    ("w_iuu_a", "fishing"),          # W1 unlicensed pair trawling in the EEZ
    ("w_iuu_b", "fishing"),          # W1 her partner
    ("w_catch_carrier", "reefer"),   # W2 takes their catch at sea
    ("w_dark_tug", "tug"),           # W3 a tow that switches off inside cover
    ("w_stray_ferry", "ferry"),      # W4 leaves her fixed run and stops at sea
    ("w_osv_rogue", "osv"),          # W5 offshore transfer to a dhow
    ("w_dhow_runner", "dhow"),       # W5 the other side of it
    ("w_coast_dark", "product_tanker"),   # W6 goes dark inside radar cover
    ("w_dhow_meet_a", "dhow"),       # W7 two dhows meeting at night
    ("w_dhow_meet_b", "dhow"),       # W7

    # ---- decoys: each looks like the anomaly above it and is innocent ----
    ("wd_licensed_a", "fishing"),    # WD1 licensed pair, identical pattern
    ("wd_licensed_b", "fishing"),
    ("wd_licensed_c", "fishing"),
    ("wd_port_reefer", "reefer"),    # WD2 transhipment, but alongside a berth
    ("wd_port_trawler", "fishing"),
    ("wd_faulty_tug", "tug"),        # WD3 a genuine transponder failure
    ("wd_held_ferry", "ferry"),      # WD4 held off the terminal, berth busy
    ("wd_standby_osv", "osv"),       # WD5 contracted platform standby
    ("wd_hole_tanker", "product_tanker"),  # WD6 silent in the coverage hole
    ("wd_inshore_dhow_a", "dhow"),   # WD7 dhows fishing together inshore
    ("wd_inshore_dhow_b", "dhow"),
    ("wd_inshore_dhow_c", "dhow"),
    ("wd_liner_a", "container"),     # WD8 a liner keeping an exact schedule
    ("wd_liner_b", "container"),
    ("wd_queue_a", "bulker"),        # WD9 lawful anchorage queue at Kandla
    ("wd_queue_b", "bulker"),
    ("wd_queue_c", "bulker"),
)


def fleet_keys() -> list[str]:
    """Every hull this module mints, bulk first then the scenario hulls."""
    out: list[str] = []
    for a in ARCHETYPES:
        out += a.hull_keys()
    out += [k for k, _ in SCENARIO_HULLS]
    return out


def bulk_size() -> int:
    return sum(a.count for a in ARCHETYPES)


def total_size() -> int:
    return bulk_size() + len(SCENARIO_HULLS)


def archetype_of(key: str) -> Archetype | None:
    """The archetype a bulk hull belongs to, or None for a scenario hull."""
    if not key.startswith("fl_"):
        return None
    return ARCHETYPE_BY_KEY.get(key[3:].rsplit("_", 1)[0])


# ==========================================================================
# companies
# ==========================================================================

#: Operators of the wider fleet.
#:
#: A separate set from `commercial.COMMERCIAL_ORGS` so the graph gains new
#: structure rather than nine more hulls hanging off the same eighteen nodes —
#: an operator with sixty vessels is a hub that makes every traversal from any
#: of them useless. Jurisdictions are the ones that actually dominate this
#: trade, for the reason `commercial.py` gives: an offshore incorporation must
#: not be a tell by itself.
FLEET_ORGS: tuple[tuple[str, str, str], ...] = (
    ("flt-kutch-liner", "Kutch Liner Services Ltd", "IND"),
    ("flt-arabian-box", "Arabian Box Lines FZE", "ARE-FZ"),
    ("flt-sahyadri-coastal", "Sahyadri Coastal Shipping Ltd", "IND"),
    ("flt-porbandar-marine", "Porbandar Marine Company Ltd", "IND"),
    ("flt-gulf-product", "Gulf Product Carriers Pte Ltd", "SGP"),
    ("flt-veraval-tankers", "Veraval Tanker Company Ltd", "IND"),
    ("flt-oman-crude", "Oman Crude Transport LLC", "ARE"),
    ("flt-hormuz-navigation", "Hormuz Navigation SA", "PAN"),
    ("flt-deccan-bulk", "Deccan Bulk Holdings Ltd", "MLT"),
    ("flt-kandla-drybulk", "Kandla Dry Bulk Pvt Ltd", "IND"),
    ("flt-malabar-cold", "Malabar Cold Chain Ltd", "IND"),
    ("flt-konkan-fisheries", "Konkan Fisheries Cooperative", "IND"),
    ("flt-saurashtra-fishing", "Saurashtra Fishing Society", "IND"),
    ("flt-harbour-towage", "West Coast Harbour Towage Ltd", "IND"),
    ("flt-offshore-logistics", "Offshore Logistics India Ltd", "IND"),
    ("flt-coastal-ferries", "Coastal Ferry Services Ltd", "IND"),
    ("flt-creek-traders", "Creek Traders Marine LLC", "ARE"),
    ("flt-southern-reefer", "Southern Reefer Lines Ltd", "LBR"),
)

#: The designated minority.
#:
#: Three of eighteen, and they are chosen so the designation lands on hulls of
#: *different* archetypes — a fishing cooperative, a small-craft trader and an
#: open-registry reefer operator. Concentrating them on one trade would have
#: made "is sanctioned" a proxy for "is a tanker", and the risk score would then
#: be measuring the generator. Kept a small minority for the reason
#: `commercial.DESIGNATED_COMMERCIAL` gives: a signal that fires on a third of
#: the fleet has stopped being a signal.
DESIGNATED_FLEET = ("flt-creek-traders", "flt-southern-reefer",
                    "flt-hormuz-navigation")

#: Which operator runs which archetype. A tug company does not run VLCCs, and a
#: graph in which every company runs one of everything has no shape to traverse.
OPERATOR_OF: dict[str, tuple[str, ...]] = {
    "box": ("flt-kutch-liner", "flt-arabian-box"),
    "feeder": ("flt-sahyadri-coastal", "flt-porbandar-marine",
               "flt-creek-traders"),
    "product": ("flt-gulf-product", "flt-veraval-tankers"),
    "aframax": ("flt-oman-crude", "flt-hormuz-navigation"),
    "vlcc": ("flt-oman-crude", "flt-hormuz-navigation"),
    "bulker": ("flt-deccan-bulk", "flt-kandla-drybulk"),
    "reefer": ("flt-malabar-cold", "flt-southern-reefer"),
    "trawler": ("flt-konkan-fisheries", "flt-saurashtra-fishing"),
    "tug": ("flt-harbour-towage",),
    "osv": ("flt-offshore-logistics",),
    "ferry": ("flt-coastal-ferries",),
    "dhow": ("flt-creek-traders", "flt-porbandar-marine"),
}

#: Operators for the group-W hulls, by key prefix. The anomaly hulls are spread
#: across ordinary companies rather than parked under the designated ones: a
#: corpus in which every bad actor is already on a list has nothing left to
#: find, and the graph question — *who else does this company run* — only means
#: something when the answer is usually "nobody interesting".
SCENARIO_OPERATOR: dict[str, str] = {
    "w_iuu": "flt-saurashtra-fishing",
    "w_catch": "flt-southern-reefer",
    "w_dark": "flt-harbour-towage",
    "w_stray": "flt-coastal-ferries",
    "w_osv": "flt-offshore-logistics",
    "w_dhow": "flt-creek-traders",
    "w_coast": "flt-gulf-product",
    "wd_licensed": "flt-konkan-fisheries",
    "wd_port": "flt-malabar-cold",
    "wd_faulty": "flt-harbour-towage",
    "wd_held": "flt-coastal-ferries",
    "wd_standby": "flt-offshore-logistics",
    "wd_hole": "flt-veraval-tankers",
    "wd_inshore": "flt-porbandar-marine",
    "wd_liner": "flt-kutch-liner",
    "wd_queue": "flt-kandla-drybulk",
}


def org_id(slug: str) -> str:
    return f"org:{slug}"


def _operator_for(key: str) -> str:
    """Which company runs this hull. Deterministic in the key, never random."""
    a = archetype_of(key)
    if a is not None:
        pool = OPERATOR_OF[a.key]
        return pool[int(key.rsplit("_", 1)[1]) % len(pool)]
    for prefix, slug in SCENARIO_OPERATOR.items():
        if key.startswith(prefix):
            return slug
    return "flt-sahyadri-coastal"


def build_fleet_orgs(world, *, designation_day) -> None:
    from .cast import T0
    c = world.corporate
    for n, (slug, name, juris) in enumerate(FLEET_ORGS):
        designated = slug in DESIGNATED_FLEET
        c.add_org(Organization(
            org_id(slug), name, juris,
            f"agent:flt-{n % 5}", f"addr:flt-{n % 6}",
            role="operator",
            incorporated=T0 - timedelta(days=900 + n * 70),
            designated=designated,
            notes=("designated fleet operator" if designated
                   else "ordinary fleet operator")))


def fleet_sanction_entries(world, *, designation_day, first_serial: int) -> int:
    """Listings for the designated fleet operators, on the fictional list.

    Returns how many were added so the caller can keep serials unique. Never a
    real OFAC entry number (ADR-019) — these terminate on their own
    `authority:SCENARIO-SDN` node.
    """
    from .identifiers import mint_sanctions_ref
    n = 0
    for slug in DESIGNATED_FLEET:
        org = world.corporate.orgs.get(org_id(slug))
        if org is None:
            continue
        world.add_sanction(dict(
            entry_id=mint_sanctions_ref(first_serial + n),
            registry="OFAC",
            name=org.name,
            entry_type="entity",
            imo=None,
            flag=org.jurisdiction,
            program="SDN",
            as_of=designation_day,
            target_entity_id=org_id(slug),
        ))
        n += 1
    return n


# ==========================================================================
# hulls
# ==========================================================================

def build_fleet_vessels(world, used_names: set[str]) -> None:
    """Mint every hull in the wider fleet.

    Minted **last** in the cast and from the fleet's own RNG, so every serial
    and every draw belonging to a hull that existed before this module is
    untouched. Serials still come from the shared counter, because a duplicate
    serial is a genuine identifier collision; only the random draws are
    isolated. Same argument as `cast.LATE_ADDITIONS`, at four hundred times the
    scale — which is what makes it worth enforcing rather than remembering.
    """
    rng = fleet_rng(world)
    for a in ARCHETYPES:
        for i in range(a.count):
            world.add_vessel(make_vessel(
                rng, world.profile, a.vessel_class,
                serial=world.take_serial(),
                entity_id=f"vessel:{a.hull_key(i)}",
                used_names=used_names, role="background",
                notes=f"wider fleet — {a.trade}"))

    for key, vessel_class in SCENARIO_HULLS:
        world.add_vessel(make_vessel(
            rng, world.profile, vessel_class,
            serial=world.take_serial(), entity_id=f"vessel:{key}",
            used_names=used_names, role="scenario",
            notes="wider fleet — group W participant"))


def build_fleet_ownership(world) -> None:
    """Wire every fleet hull to an operator, with a time-scoped, graded edge.

    **Every edge carries provenance, a confidence and a validity interval**
    (CLAUDE.md invariant 3) — `CorporateWorld.link` is the only way an edge is
    made here, and it takes all three. The confidences differ by relationship
    and say why: an operator is a commercial fact that a port agent, a charter
    fixture and a class record all corroborate, so it is asserted high; the
    technical-manager overlay below is an inference from a shared address and is
    asserted low.

    A minority of hulls also carry a `managed-by` edge to a *second* company, so
    the graph has two-hop structure inside the ordinary fleet as well as inside
    the named cast. Without it every path between two ordinary hulls runs
    through a flag or a port, which is the star shape the real corpus already
    has and STATE.md already concluded is not worth drawing.
    """
    from .cast import T0
    c = world.corporate
    start = T0 - timedelta(days=400)
    rng = fleet_rng(world, salt=3)

    for key in fleet_keys():
        slug = _operator_for(key)
        c.link("operated-by", f"vessel:{key}", org_id(slug), start, None,
               confidence=0.8, notes="wider fleet operator")

    # The manager overlay: about one hull in six is technically managed by a
    # company other than her operator. Deliberately cross-cutting — a manager
    # takes work where it comes from, so his clients share nothing else.
    managers = ("flt-sahyadri-coastal", "flt-porbandar-marine",
                "flt-harbour-towage", "flt-offshore-logistics")
    for key in fleet_keys():
        if rng.random() >= 0.17:
            continue
        slug = managers[rng.randrange(len(managers))]
        if slug == _operator_for(key):
            continue
        c.link("managed-by", f"vessel:{key}", org_id(slug), start, None,
               confidence=0.55,
               notes=("technical management, inferred from a shared "
                      "correspondence address — a candidate, never a finding"))
