"""The cast — persistent entities, generated once, shared across scenarios.

**Sharing is the whole point.** A corpus where every scenario owns a private set
of vessels and companies produces a graph of disconnected islands, which is
exactly the star-shaped structure the real data already gives us and exactly
what STATE.md concluded was not worth drawing. The value here is that the same
hull appears in five scenarios, the same manager appears above three unrelated
vessels, and the same registered agent survives a company's dissolution — so
traversal has something to find.

**The narrative spine.** One vessel threads the whole window: she reflags twice
early (B3), conducts the canonical transfer in week 3 (A1), disappears in week 4
and returns under a new identity in week 5 (B1), her beneficial owner surfaces
in week 6 (D1), and her port-call sequence completes in week 8 (E4). Everything
else populates the world around her. A demo needs a story, and a story needs one
subject who is present throughout.

**Cast size, and an honest deviation.** The build plan asks for 45-60 vessels.
The named cast is inside that. The fishing-fleet-aggregation decoy asks
separately for forty vessels converging on one ground, and a fleet of eight
would not resemble the mass rendezvous the decoy exists to test — so that fleet
is generated as bulk background traffic *on top of* the named cast, and the
generation report prints both numbers rather than blending them. Shrinking the
fleet to protect a headline count would have quietly destroyed the decoy.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from .identifiers import mint_sanctions_ref
from .primitives.org import Organization
from . import commercial
from .primitives.vessel import make_vessel, vessel_name
from .world import T0, ScenarioWorld, week

# --------------------------------------------------------------------------
# organisations
# --------------------------------------------------------------------------

ORG_DESIGNATED_A = "org:pearl-crest-shipping"
ORG_DESIGNATED_B = "org:zephyr-marine-holdings"
ORG_OPAQUE_MH = "org:atoll-tonnage-mh"
ORG_OPAQUE_SC = "org:victoria-maritime-sc"
ORG_OPAQUE_AE = "org:jafza-chartering"
ORG_MANAGER_1 = "org:northwind-ship-management"
ORG_MANAGER_2 = "org:blue-anchor-technical"
ORG_CLEAN_A = "org:cecil-street-tankers"
ORG_CLEAN_B = "org:leadenhall-bulk"
ORG_PHOENIX_OLD = "org:harrow-lines"
ORG_PHOENIX_NEW = "org:harrow-maritime-services"

#: Beneficial owner sitting two hops above the D1 pair.
ORG_BENEFICIAL = ORG_DESIGNATED_A


def build_organizations(world: ScenarioWorld) -> None:
    """Eleven companies, deliberately entangled through agents and addresses."""
    c = world.corporate
    designation_day = week(2, hours=6)

    c.add_org(Organization(
        ORG_DESIGNATED_A, "Pearl Crest Shipping Ltd", "MHL",
        "agent:meridian", "addr:majuro-trust", role="owner",
        incorporated=T0 - timedelta(days=1600), designated=True,
        notes="designated; sits two hops above the D1 pair"))
    c.add_org(Organization(
        ORG_DESIGNATED_B, "Zephyr Marine Holdings FZE", "ARE-FZ",
        "agent:cedarpoint", "addr:jafza-one", role="owner",
        incorporated=T0 - timedelta(days=1100), designated=True,
        notes="designated; owns the brazen operator (A5)"))

    for oid, name, juris, agent, addr in (
        (ORG_OPAQUE_MH, "Atoll Tonnage Inc", "MHL", "agent:meridian",
         "addr:majuro-trust"),
        (ORG_OPAQUE_SC, "Victoria Maritime Ltd", "SYC", "agent:harbourline",
         "addr:victoria-house"),
        (ORG_OPAQUE_AE, "JAFZA Chartering FZC", "ARE-FZ", "agent:cedarpoint",
         "addr:jafza-one"),
    ):
        c.add_org(Organization(oid, name, juris, agent, addr,
                               role="intermediary",
                               incorporated=T0 - timedelta(days=900),
                               notes="opaque intermediary"))

    c.add_org(Organization(
        ORG_MANAGER_1, "Northwind Ship Management Pte Ltd", "SGP",
        "agent:crestwell", "addr:cecil-street", role="manager",
        incorporated=T0 - timedelta(days=2500),
        notes="technical manager across otherwise unrelated vessels (D3)"))
    c.add_org(Organization(
        ORG_MANAGER_2, "Blue Anchor Technical Services", "IND",
        "agent:crestwell", "addr:cecil-street", role="manager",
        incorporated=T0 - timedelta(days=2000),
        notes="second shared manager"))

    c.add_org(Organization(
        ORG_CLEAN_A, "Cecil Street Tankers Pte Ltd", "SGP",
        "agent:crestwell", "addr:cecil-street", role="operator",
        incorporated=T0 - timedelta(days=3000),
        notes="clean commercial operator"))
    c.add_org(Organization(
        ORG_CLEAN_B, "Leadenhall Bulk Carriers Ltd", "GBR",
        "agent:harbourline", "addr:leadenhall", role="operator",
        incorporated=T0 - timedelta(days=4000),
        notes="clean commercial operator"))

    # D2: the phoenix. Same agent, same address, new name, after designation.
    c.add_org(Organization(
        ORG_PHOENIX_OLD, "Harrow Lines Ltd", "SYC", "agent:harbourline",
        "addr:victoria-house", role="operator",
        incorporated=T0 - timedelta(days=2200),
        dissolved=week(5, hours=9), designated=True,
        notes="designated, then dissolved — D2"))
    c.add_org(Organization(
        ORG_PHOENIX_NEW, "Harrow Maritime Services Ltd", "SYC",
        "agent:harbourline", "addr:victoria-house", role="operator",
        incorporated=week(5, hours=30), successor_of=ORG_PHOENIX_OLD,
        notes="re-registered successor, same agent and address — D2"))

    # Sanctions listings on the FICTIONAL list. Never a real OFAC entry number.
    named_designations = (ORG_DESIGNATED_A, ORG_DESIGNATED_B, ORG_PHOENIX_OLD)
    for i, oid in enumerate(named_designations):
        org = c.orgs[oid]
        world.add_sanction(dict(
            entry_id=mint_sanctions_ref(i + 1),
            registry="OFAC",
            name=org.name,
            entry_type="entity",
            imo=None,
            flag=org.jurisdiction,
            program="SDN",
            as_of=designation_day,
            target_entity_id=oid,
        ))

    # The commercial fleet's operators, including the designated minority.
    # Added after the named ones so their sanctions serials never renumber.
    commercial.build_commercial_orgs(world, designation_day=designation_day)
    commercial.commercial_sanction_entries(
        world, designation_day=designation_day,
        first_serial=len(named_designations) + 1)


# --------------------------------------------------------------------------
# vessels
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class CastEntry:
    key: str
    vessel_class: str
    role: str
    notes: str
    flag: str | None = None
    ais_expected: bool = True
    size: float | None = None


#: Principal cast. `key` is what scenarios reference; the entity id is
#: `vessel:<key>`. Order is fixed, so identifier serials are stable across runs
#: and across catalogue growth.
PRINCIPALS: tuple[CastEntry, ...] = (
    # ---- true-anomaly hulls ----
    CastEntry("spine", "Suezmax", "true_anomaly",
              "narrative spine: B3 cascade, A1 transfer, B1 phoenix, D1, E4",
              flag="PAN", size=0.62),
    CastEntry("receiver_alpha", "Aframax", "true_anomaly",
              "A1 receiver; also E7 return-to-position", flag="COM"),
    CastEntry("chain_a", "product_tanker", "true_anomaly", "A2 daisy chain hop 1 donor"),
    CastEntry("chain_b", "product_tanker", "true_anomaly", "A2 hop 1 receiver / hop 2 donor"),
    CastEntry("chain_c", "Aframax", "true_anomaly", "A2 hop 2 receiver / hop 3 donor"),
    CastEntry("chain_d", "product_tanker", "true_anomaly", "A2 hop 3 receiver, delivers Mundra"),
    CastEntry("spoofer", "Aframax", "true_anomaly", "A3 spoof-and-swap broadcaster"),
    CastEntry("spoof_partner", "product_tanker", "true_anomaly", "A3 actual counterpart"),
    CastEntry("partial_dark", "VLCC", "true_anomaly", "A4 degrading reporting interval"),
    CastEntry("brazen", "Suezmax", "true_anomaly",
              "A5 designated hull that never goes dark", flag="IRN"),
    CastEntry("identity_break", "bulker", "true_anomaly", "B2 full identity break"),
    CastEntry("zombie", "general_cargo", "true_anomaly", "B4 IMO of a hull scrapped 2019"),
    CastEntry("clone_real", "product_tanker", "true_anomaly", "B5 the real hull"),
    CastEntry("clone_ghost", "product_tanker", "true_anomaly", "B5 the ghost broadcaster"),
    CastEntry("voyage_flag", "Aframax", "true_anomaly", "B6 voyage-specific reflagging"),
    CastEntry("converge_b", "bulker", "true_anomaly", "D1 second vessel under the same BO"),
    CastEntry("phantom", "general_cargo", "true_anomaly", "C1 implausible replayed track"),
    CastEntry("berth_ghost", "bulker", "true_anomaly", "C2 AIS berthed, SAR empty"),
    CastEntry("kinematics", "VLCC", "true_anomaly", "C3 impossible speed jumps"),
    CastEntry("cable_loiter", "general_cargo", "true_anomaly",
              "E1 survey-capable, station-keeping on the cable approach"),
    CastEntry("rig_prowler", "general_cargo", "true_anomaly", "E2 slow passes at Bombay High"),
    CastEntry("naval_intruder", "general_cargo", "true_anomaly", "E3 exercise-area intrusion"),
    CastEntry("iuu_fisher", "fishing", "true_anomaly", "E6 unauthorised fishing in EEZ"),
    CastEntry("iuu_reefer", "reefer", "true_anomaly", "E6 catch carrier, the graph link"),

    # ---- decoys: look wrong, are right ----
    CastEntry("bunker_barge", "product_tanker", "decoy",
              "legitimate bunkering supplier at a designated anchorage"),
    CastEntry("bunker_client", "bulker", "decoy", "receives legitimate bunkers"),
    CastEntry("faulty_txp", "general_cargo", "decoy",
              "genuine transponder failure, then a maintenance call"),
    CastEntry("shadow_gap", "product_tanker", "decoy",
              "silence in demonstrably poor reception — must resolve to unknown"),
    CastEntry("congested", "Aframax", "decoy", "40 h slow circling off Mundra, no berth"),
    CastEntry("clean_sale", "bulker", "decoy",
              "routine ownership sale and reflagging, no behavioural change"),
    CastEntry("survey_declared", "general_cargo", "decoy",
              "declared cable survey with authorisation on file"),
    CastEntry("navy_dark", "naval", "decoy",
              "naval vessel with AIS off — normal, must never be flagged dark",
              flag="IND", ais_expected=False),
    CastEntry("saga_one", "product_tanker", "decoy", "name collision: SAGA #1"),
    CastEntry("saga_two", "general_cargo", "decoy", "name collision: SAGA #2"),
    CastEntry("monsoon_diverter", "bulker", "decoy",
              "weather diversion that looks evasive"),
    CastEntry("clean_neighbour", "product_tanker", "decoy",
              "shares an anchorage with a designated vessel by berth assignment "
              "only — the most important decoy in the set"),
    CastEntry("storage_1", "VLCC", "decoy", "E5 floating storage off Gujarat"),
    CastEntry("storage_2", "Suezmax", "decoy", "E5 floating storage"),
    CastEntry("storage_3", "Aframax", "decoy", "E5 floating storage"),
    CastEntry("storage_4", "Suezmax", "decoy", "E5 floating storage"),
    CastEntry("storage_5", "VLCC", "decoy", "E5 floating storage"),
    CastEntry("storage_6", "Aframax", "decoy", "E5 floating storage"),
    CastEntry("managed_1", "bulker", "decoy", "D3 shares a technical manager"),
    CastEntry("managed_2", "general_cargo", "decoy", "D3 shares a technical manager"),
    CastEntry("managed_3", "product_tanker", "decoy", "D3 shares a technical manager"),
    CastEntry("agent_share_1", "bulker", "decoy", "D4 shared port agent, never touch"),
    CastEntry("agent_share_2", "product_tanker", "decoy", "D4 shared port agent"),

    # C4's interference cluster gets its own hulls. Reusing scenario vessels
    # here put several of them in two places at once — the cluster runs for 20
    # hours near the northwest boundary while those same vessels were working
    # ports 800 nm away — and a hull that teleports is not a decoy, it is a
    # corrupt fixture. Sharing vessels across scenarios is the point of the
    # cast, but sharing them across the same *hours* is not possible, and the
    # occupancy check in `world.add_track` now makes that a build failure
    # rather than a turn-rate anomaly to be puzzled over later.
    *(CastEntry(f"gps_{i:02d}",
                ("bulker", "product_tanker", "general_cargo", "Aframax")[i % 4],
                "decoy", "C4 GPS-interference cluster")
      for i in range(12)),

    # ---- deliberate misses ----
    CastEntry("dhow_sub_floor", "dhow", "deliberate_miss",
              "22 m craft transferring off Makran — below the SAR size floor"),
    CastEntry("dhow_partner", "dhow", "deliberate_miss", "the other side of that transfer"),
    CastEntry("offshore_gap", "product_tanker", "deliberate_miss",
              "goes dark at 12N 61E, far outside demonstrated reception"),

    # ---- pure background traffic ----
    CastEntry("bg_1", "VLCC", "background", "Gulf to Sikka laden transit"),
    CastEntry("bg_2", "Suezmax", "background", "Gulf to Vadinar"),
    CastEntry("bg_3", "product_tanker", "background", "coastal product run"),
    CastEntry("bg_4", "bulker", "background", "Mundra to Kochi"),
    CastEntry("bg_5", "bulker", "background", "Kandla outbound"),
    CastEntry("bg_6", "general_cargo", "background", "west-coast feeder"),
    CastEntry("bg_7", "general_cargo", "background", "Mangalore to JNPT"),
    CastEntry("bg_8", "reefer", "background", "Kochi reefer run"),
    CastEntry("bg_9", "product_tanker", "background", "Karachi to Mumbai"),
    CastEntry("bg_10", "Aframax", "background", "Gulf of Oman entry, Vadinar"),
    CastEntry("bg_11", "fishing", "background", "Gujarat coastal fishing"),
    CastEntry("bg_12", "fishing", "background", "Gujarat coastal fishing"),
)

#: The fishing-fleet-aggregation decoy. Sized to the phenomenon, not to the
#: headline cast count — see the module docstring.
FISHING_FLEET_SIZE = 40


def entity_id(key: str) -> str:
    return f"vessel:{key}"


def build_vessels(world: ScenarioWorld) -> None:
    """Mint the principal cast, then the fishing fleet."""
    used_names: set[str] = set()

    for entry in PRINCIPALS:
        serial = world.take_serial()
        v = make_vessel(
            world.rng, world.profile, entry.vessel_class,
            serial=serial, entity_id=entity_id(entry.key),
            flag=entry.flag, used_names=used_names, role=entry.role,
            ais_expected=entry.ais_expected, notes=entry.notes,
            size=entry.size,
        )
        world.add_vessel(v)

    # The name-collision decoy: two unrelated hulls, one name, different IMOs.
    # The real corpus already proves this is common, and a system that treats a
    # shared name as a shared identity fails on ordinary traffic long before it
    # meets an evasive vessel.
    saga_name = "SAGA"
    world.vessel(entity_id("saga_one")).name = saga_name
    world.vessel(entity_id("saga_two")).name = saga_name
    for key in ("saga_one", "saga_two"):
        eid = entity_id(key)
        for iv in world.identity.intervals:
            if iv.vessel_entity_id == eid and iv.field_name == "name":
                iv.value = saga_name

    for i in range(FISHING_FLEET_SIZE):
        serial = world.take_serial()
        v = make_vessel(
            world.rng, world.profile, "fishing",
            serial=serial, entity_id=f"vessel:fleet_{i:02d}",
            used_names=used_names, role="decoy",
            notes="fishing-fleet aggregation decoy")
        world.add_vessel(v)

    # The commercial fleet is minted LAST, so growing it never renumbers the
    # named cast — same seed, same hulls, for every scenario that references one.
    commercial.build_commercial_vessels(world, used_names)


def build_ownership(world: ScenarioWorld) -> None:
    """Wire vessels to companies, and companies to each other.

    Two structures matter and are built deliberately:

    **D1's convergence.** `spine` and `converge_b` have no direct interaction and
    different immediate operators, but both operators are owned by the same
    designated beneficial owner two hops up. That is the shape risk propagation
    is supposed to find, and it is unreachable in one hop.

    **D3's manager linkage.** Three unrelated vessels share a technical manager
    who also manages a designated vessel. This must surface as a *candidate*,
    never a finding — a ship manager with a bad client is not thereby a bad
    manager, and every one of its other clients is innocent until something else
    says otherwise.
    """
    c = world.corporate
    start = T0 - timedelta(days=400)

    def own(vkey: str, org: str, kind: str = "operated-by",
            conf: float = 0.75, frm=None, to=None, notes: str = ""):
        c.link(kind, entity_id(vkey), org, frm or start, to,
               confidence=conf, notes=notes)

    # --- D1: two hops to a shared, designated beneficial owner ---
    own("spine", ORG_OPAQUE_MH, notes="D1 leg 1")
    own("converge_b", ORG_OPAQUE_SC, notes="D1 leg 1")
    c.link("owned-by", ORG_OPAQUE_MH, ORG_BENEFICIAL, start, None,
           confidence=0.62, notes="D1 leg 2 — the convergence")
    c.link("owned-by", ORG_OPAQUE_SC, ORG_BENEFICIAL, start, None,
           confidence=0.58, notes="D1 leg 2 — the convergence")

    # --- A5: the brazen operator, openly owned by a designated entity ---
    own("brazen", ORG_DESIGNATED_B, kind="owned-by", conf=0.9,
        notes="A5 — designated ownership, no attempt to hide")

    # --- D2: the phoenix keeps its fleet across the re-registration ---
    for vkey in ("identity_break", "zombie"):
        own(vkey, ORG_PHOENIX_OLD, to=week(5, hours=9),
            notes="D2 — held by the dissolved entity")
        own(vkey, ORG_PHOENIX_NEW, frm=week(5, hours=30),
            notes="D2 — retained by the successor")
    c.link("owned-by", ORG_PHOENIX_NEW, ORG_PHOENIX_OLD, week(5, hours=30),
           None, confidence=0.45,
           notes="D2 — successor relationship, asserted at low confidence")

    # --- D3: shared technical manager, one of whose clients is designated ---
    for vkey in ("managed_1", "managed_2", "managed_3"):
        own(vkey, ORG_MANAGER_1, kind="operated-by", conf=0.7,
            notes="D3 — shared manager, must surface as CANDIDATE")
    own("brazen", ORG_MANAGER_1, kind="operated-by", conf=0.7,
        notes="D3 — the designated client of the same manager")

    # --- clean operators, so 'has an owner' is not itself a signal ---
    for vkey in ("bunker_barge", "clean_sale", "saga_one", "bg_1", "bg_2",
                 "bg_3", "bg_9", "bg_10"):
        own(vkey, ORG_CLEAN_A, conf=0.85, notes="clean operator")
    for vkey in ("clean_neighbour", "saga_two", "bg_4", "bg_5", "bg_6",
                 "bg_7", "monsoon_diverter", "congested"):
        own(vkey, ORG_CLEAN_B, conf=0.85, notes="clean operator")
    for vkey in ("agent_share_1", "agent_share_2"):
        own(vkey, ORG_MANAGER_2, kind="operated-by", conf=0.7,
            notes="D4 — shared agent, no vessel-to-vessel contact")
    for vkey in ("chain_a", "chain_b", "chain_c", "chain_d"):
        own(vkey, ORG_OPAQUE_AE, conf=0.55, notes="A2 chain, common charterer")

    commercial.build_commercial_ownership(world)


def build_cast(world: ScenarioWorld) -> None:
    build_organizations(world)
    build_vessels(world)
    build_ownership(world)


def principal_keys(role: str | None = None) -> list[str]:
    return [e.key for e in PRINCIPALS if role is None or e.role == role]


def fleet_keys() -> list[str]:
    return [f"fleet_{i:02d}" for i in range(FISHING_FLEET_SIZE)]
