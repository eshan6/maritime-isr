"""The commercial fleet — the bulk of the world, generated rather than written.

The named cast in `cast.py` exists to stage 40 specific scenarios; every hull
there is hand-placed because a scenario needs it somewhere precise. That gives a
corpus with sharp structure and very little *volume* — 114 vessels, 45 port
visits, 3 sanctions matches — which is enough to exercise the detectors and far
too thin to look like a working picture of the Arabian Sea.

This module supplies the volume: an ordinary merchant fleet working the same
real ports, owned by an ordinary set of companies, a few of which are designated.
Nothing here stages an anomaly and nothing here writes a `scenario_truth` row —
these vessels exist to be the sea the scenarios happen in.

**Why generated and not written out.** The interesting question a demo answers is
"can you find the one bad hull among the ordinary ones", and that needs the
ordinary ones to be numerous, individually unremarkable, and structurally similar
to the interesting ones. Ninety hand-written background vessels would be ninety
opportunities to accidentally make the background distinguishable.

**Cost, stated because it is the reason the size is a parameter.** Every vessel
here emits real AIS through the same integrator and emitter as the cast, so the
corpus grows in positions as well as in rows, and both generation and the track
engine slow accordingly. `MISR_SCENARIO_FLEET` overrides the count for a bigger
picture or a faster run; the default is chosen to roughly triple the visible
world while keeping a full generate-and-run inside a coffee break on a laptop.

**What stays invariant** (all of it enforced elsewhere, none of it special-cased
here): identifiers come from the reserved synthetic ranges through the same
minting path, so the collision guard covers them; fields are null-masked at the
measured real rates, so the fleet is not separable by `WHERE imo IS NOT NULL`;
every row carries the provenance envelope and `is_synthetic`; and the corpus
window bounds every event, so a call started late is truncated rather than
overrunning.
"""
from __future__ import annotations

import os
from datetime import timedelta

from .primitives.org import Organization
from .primitives.vessel import make_vessel

#: Vessels in the commercial fleet. Override with MISR_SCENARIO_FLEET.
#: 0 disables the layer entirely and returns the corpus to the named cast alone.
DEFAULT_FLEET_SIZE = 96


def fleet_size() -> int:
    raw = os.getenv("MISR_SCENARIO_FLEET")
    if raw is None:
        return DEFAULT_FLEET_SIZE
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_FLEET_SIZE


#: Companies operating the fleet. Deliberately mundane names in the jurisdictions
#: that actually dominate ship operation in this trade, so a flag-of-convenience
#: or an offshore incorporation is not by itself a tell — the named cast's
#: opaque structures have to be distinguishable by their SHAPE, not by looking
#: like the only offshore companies in the corpus.
COMMERCIAL_ORGS: tuple[tuple[str, str, str], ...] = (
    ("com-arabian-gulf-tankers", "Arabian Gulf Tankers LLC", "ARE"),
    ("com-konkan-shipping", "Konkan Shipping Company Ltd", "IND"),
    ("com-westline-bulk", "Westline Bulk Pte Ltd", "SGP"),
    ("com-malabar-carriers", "Malabar Carriers Ltd", "IND"),
    ("com-orient-star-marine", "Orient Star Marine SA", "PAN"),
    ("com-deccan-tanker", "Deccan Tanker Management Ltd", "IND"),
    ("com-blue-meridian", "Blue Meridian Shipping Ltd", "MLT"),
    ("com-gulf-orient-lines", "Gulf Orient Lines FZE", "ARE-FZ"),
    ("com-saraswati-marine", "Saraswati Marine Services Ltd", "IND"),
    ("com-cape-comorin", "Cape Comorin Navigation Ltd", "IND"),
    ("com-indus-maritime", "Indus Maritime Company Ltd", "PAK"),
    ("com-emerald-coast", "Emerald Coast Shipping Ltd", "LBR"),
    ("com-southern-cross-bulk", "Southern Cross Bulk Ltd", "GBR"),
    ("com-anchorline-tankers", "Anchorline Tankers Pte Ltd", "SGP"),
    ("com-sagar-logistics", "Sagar Logistics Private Ltd", "IND"),
    ("com-north-star-chartering", "North Star Chartering Ltd", "MHL"),
    ("com-coral-bay-marine", "Coral Bay Marine Ltd", "SYC"),
    ("com-trident-shipmanagement", "Trident Shipmanagement Ltd", "IND"),
)

#: Commercial operators that are themselves designated. Every vessel they run
#: becomes a sanctions match through the ordinary path in `land.py` — no
#: special-casing — which is what gives the vessels table and the graph a
#: realistic number of sanctioned hulls instead of three.
#:
#: Kept a small minority on purpose. If a large share of the fleet were
#: sanctioned, "is sanctioned" would stop being a discriminating signal and the
#: precision figure would be measuring the generator's generosity.
DESIGNATED_COMMERCIAL = ("com-orient-star-marine", "com-indus-maritime",
                         "com-coral-bay-marine")

#: Class mix, roughly the traffic these lanes actually carry: crude and product
#: tankers into the Gulf of Kutch terminals, bulk and general cargo down the
#: west coast, a little reefer and fishing.
CLASS_MIX: tuple[str, ...] = (
    "product_tanker", "bulker", "general_cargo", "product_tanker",
    "Aframax", "bulker", "general_cargo", "Suezmax",
    "product_tanker", "reefer", "bulker", "general_cargo",
)


def org_id(slug: str) -> str:
    return f"org:{slug}"


def fleet_key(i: int) -> str:
    return f"com_{i:02d}"


def fleet_keys() -> list[str]:
    return [fleet_key(i) for i in range(fleet_size())]


def build_commercial_orgs(world, *, designation_day) -> None:
    """Companies first, then their designations on the fictional list."""
    if fleet_size() == 0:
        return
    c = world.corporate
    from .cast import T0

    for n, (slug, name, juris) in enumerate(COMMERCIAL_ORGS):
        designated = slug in DESIGNATED_COMMERCIAL
        c.add_org(Organization(
            org_id(slug), name, juris,
            f"agent:com-{n % 6}", f"addr:com-{n % 7}",
            role="operator",
            incorporated=T0 - timedelta(days=1200 + n * 90),
            designated=designated,
            notes=("designated commercial operator" if designated
                   else "ordinary commercial operator")))


def commercial_sanction_entries(world, *, designation_day, first_serial: int):
    """Listing rows for the designated commercial operators.

    Returns the number of entries added, so the caller can keep serials unique.
    The listings go on the fictional SCENARIO-SDN register and never carry a real
    OFAC entry number (ADR-019).
    """
    if fleet_size() == 0:
        return 0
    from .identifiers import mint_sanctions_ref
    n = 0
    for slug in DESIGNATED_COMMERCIAL:
        org = world.corporate.orgs.get(org_id(slug))
        if org is None:
            continue
        world.add_sanction(dict(
            entry_id=mint_sanctions_ref(first_serial + n),
            registry="SCENARIO-SDN",
            name=org.name,
            entry_type="entity",
            imo=None,
            flag=org.jurisdiction,
            program="SCENARIO-DEMO",
            as_of=designation_day,
            target_entity_id=org_id(slug),
        ))
        n += 1
    return n


def build_commercial_vessels(world, used_names: set[str]) -> None:
    """Mint the fleet. Minted LAST so every existing serial is unchanged.

    Serial order is what determines a vessel's identifiers, so appending here
    means growing the fleet never renumbers the named cast — the same seed keeps
    producing the same hulls for every scenario that references one.
    """
    for i in range(fleet_size()):
        world.add_vessel(make_vessel(
            world.rng, world.profile, CLASS_MIX[i % len(CLASS_MIX)],
            serial=world.take_serial(), entity_id=f"vessel:{fleet_key(i)}",
            used_names=used_names, role="background",
            notes="commercial fleet — ordinary traffic"))


def build_commercial_ownership(world) -> None:
    """Spread the fleet across the operators, a handful of hulls each.

    Fleets of five to eight are what an operator of this size actually runs, and
    it is also what makes the graph worth opening: a vessel expands to its
    operator, and the operator expands to its siblings.
    """
    if fleet_size() == 0:
        return
    from .cast import T0
    c = world.corporate
    start = T0 - timedelta(days=400)
    for i in range(fleet_size()):
        slug = COMMERCIAL_ORGS[i % len(COMMERCIAL_ORGS)][0]
        c.link("operated-by", f"vessel:{fleet_key(i)}", org_id(slug),
               start, None, confidence=0.8,
               notes="commercial fleet operator")
