"""vessel_factory — a hull with class-correlated physics.

A vessel is not a label. Its length, beam, draught and tonnage move together
(they are all consequences of the same hull form), and its speed envelope,
acceleration and rate of turn follow from its displacement. A 300,000 t VLCC
cannot accelerate like a fishing boat, and a generator that lets it produces
tracks that a physics validator — or an analyst — can separate from real ones
instantly.

So dimensions are **correlated, not independently sampled**. One `size` draw per
hull, in [0,1], places it within its class, and every dimension is read off that
same draw with a little independent scatter. A long VLCC is also a wide, deep,
heavy one, which is what real ships are like. Sampling length and beam
independently would produce 340 m hulls with 55 m beams alongside 300 m hulls
with 62 m beams, and the length/beam ratio — a quantity a discriminator could
learn — would be noise instead of a class property.

Where the corpus can supply a class's real length distribution, it does (see
`profile.py`); beam, draught and speed come from published class figures because
GFW does not carry them at all.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..identifiers import mint_imo, mint_mmsi

#: Every class the generator can build. Order is fixed so a seeded run is stable.
#:
#: **Appended to, never reordered.** The four classes after `naval` were added
#: when the corpus was widened (see `scenario/fleet.py`): a picture of the
#: Arabian Sea that contains no container ship, no harbour tug, no offshore
#: supply vessel and no ferry is not a thin picture, it is a wrong one — and a
#: type classifier trained on a fleet that has never seen a tug will confidently
#: call the first one it meets a fishing boat.
CLASSES = (
    "VLCC", "Suezmax", "Aframax", "product_tanker", "bulker", "reefer",
    "general_cargo", "fishing", "dhow", "naval",
    "container", "tug", "osv", "ferry",
)

#: Classes that carry liquid cargo — the ones an STS transfer scenario can use
#: as a donor or receiver without the geometry being absurd.
TANKER_CLASSES = ("VLCC", "Suezmax", "Aframax", "product_tanker")

#: Name construction. Two-part names built from real maritime naming
#: conventions. Kept deliberately generic; a name is not an identifier here
#: (the IMO is), and the collision guard rejects any that matches a real OFAC
#: listing so a scenario can never look like a real designation.
_PREFIXES = (
    "GULF", "OCEAN", "STAR", "PACIFIC", "ATLANTIC", "DESERT", "SILVER",
    "GOLDEN", "NORTHERN", "SOUTHERN", "EASTERN", "CRYSTAL", "ROYAL",
    "IMPERIAL", "GRAND", "BLUE", "GREEN", "MORNING", "EVENING", "NOBLE",
    "SEA", "CORAL", "PEARL", "AMBER", "IRON", "GRANITE", "MERIDIAN",
)
_SUFFIXES = (
    "TRADER", "VOYAGER", "PIONEER", "HORIZON", "GLORY", "SPIRIT", "PROSPERITY",
    "HARMONY", "ENDEAVOUR", "MARINER", "NAVIGATOR", "EXPRESS", "CARRIER",
    "PROGRESS", "FORTUNE", "TRIUMPH", "SENTINEL", "GUARDIAN", "BREEZE",
    "CURRENT", "PASSAGE", "MERCHANT", "ENTERPRISE", "SUMMIT", "ZENITH",
)

#: Call-sign prefixes by flag, roughly following ITU allocations. Not exhaustive
#: — enough that a call sign looks like it belongs to its flag.
_CALLSIGN_PREFIX = {
    "PAN": "3E", "LBR": "A8", "MHL": "V7", "SGP": "9V", "IND": "AT",
    "MLT": "9H", "BHS": "C6", "HKG": "VR", "CYP": "5B", "ARE": "A6",
    "IRN": "EP", "PAK": "AP", "COM": "D6", "GMB": "C5", "CMR": "TJ",
    "TZA": "5I", "VCT": "J8", "PLW": "T8",
}
_CALLSIGN_TAIL = "ABCDEFGHIJKLMNPQRSTUVWXYZ23456789"


@dataclass
class SyntheticVessel:
    """A scenario hull. Immutable physics; identity can change over time.

    `name`, `flag`, `mmsi` and `call_sign` are the *current* identity — identity
    events (B-group scenarios) rewrite them and record the change with proper
    interval closing. `imo`, and the physical dimensions, do not change: that
    asymmetry is precisely what B1 and B4 are built to exercise.
    """
    entity_id: str
    vessel_class: str
    imo: int
    mmsi: int
    name: str
    flag: str
    call_sign: str
    length_m: float
    beam_m: float
    draught_m: float
    dwt: float
    service_kn: float
    max_kn: float
    accel_kn_per_min: float
    rot_deg_per_s: float
    #: Set on hulls whose AIS is legitimately off (naval). The dark-vessel logic
    #: must never flag these; the decoy set depends on it.
    ais_expected: bool = True
    role: str = "background"
    notes: str = ""
    #: Identity history, appended to by the identity primitive.
    identity_history: list = field(default_factory=list)

    @property
    def displacement_proxy(self) -> float:
        """Rough displacement scale, used for encounter-geometry sanity."""
        return self.length_m * self.beam_m * self.draught_m

    def min_separation_m(self) -> float:
        """Closest two hulls of this size can plausibly sit, centre to centre.

        Alongside with fenders means beams touching, so centres are about one
        beam apart, plus a working margin. A transfer geometry that puts two
        VLCCs 10 m apart centre-to-centre is not a tight transfer, it is a
        collision — and a validator that did not know this would pass it.
        """
        return self.beam_m * 1.15 + 8.0


def _call_sign(rng, flag: str) -> str:
    pre = _CALLSIGN_PREFIX.get(flag, "3E")
    return pre + "".join(rng.choice(_CALLSIGN_TAIL) for _ in range(4))


def vessel_name(rng, used: set[str] | None = None) -> str:
    """A plausible two-part vessel name, unique within `used` if given."""
    for _ in range(200):
        n = f"{rng.choice(_PREFIXES)} {rng.choice(_SUFFIXES)}"
        if used is None or n not in used:
            if used is not None:
                used.add(n)
            return n
    # Exhausted the pool — fall back to a numbered variant rather than
    # silently returning a duplicate, which would create an unintended
    # name-collision scenario on top of the deliberate one.
    n = f"{rng.choice(_PREFIXES)} {rng.choice(_SUFFIXES)} {rng.randint(2, 99)}"
    if used is not None:
        used.add(n)
    return n


def make_vessel(rng, profile, vessel_class: str, *, serial: int,
                entity_id: str, flag: str | None = None,
                name: str | None = None, used_names: set[str] | None = None,
                role: str = "background", ais_expected: bool = True,
                notes: str = "", size: float | None = None) -> SyntheticVessel:
    """Build one hull of `vessel_class`, dimensions correlated through `size`.

    `serial` indexes the reserved identifier bands, so a hull's IMO and MMSI are
    a deterministic function of its position in the cast rather than of the RNG
    stream — which means adding a scenario later does not silently renumber
    every existing hull's identity.
    """
    if vessel_class not in CLASSES:
        raise ValueError(f"unknown vessel class {vessel_class!r}")
    dims = profile.class_dims(vessel_class).value

    # ONE size draw drives every dimension: real hulls are self-consistent.
    size = rng.random() if size is None else size

    def read_spec(spec, pos: float) -> float:
        """Read a (low, typical, high) spec at position `pos`, with scatter.

        Piecewise-linear through the typical value so the mode still sits where
        the class says it does, then +-3% independent scatter so hulls are not
        perfectly collinear — real sister ships differ slightly, and perfect
        collinearity is itself a detectable artefact.
        """
        low, mode, high = (float(x) for x in spec)
        v = (low + (mode - low) * (pos / 0.5) if pos <= 0.5
             else mode + (high - mode) * ((pos - 0.5) / 0.5))
        return v * rng.uniform(0.97, 1.03)

    length = read_spec(dims["length_m"], size)
    beam = read_spec(dims["beam_m"], size)
    draught = read_spec(dims["draught_m"], size)
    dwt = read_spec(dims.get("dwt") or dims.get("tonnage_gt"), size)
    # Bigger hulls of a class are marginally slower, so service speed is read at
    # the mirrored position: the biggest hull in a class gets the low end of the
    # speed envelope, not an independent draw.
    service = read_spec(dims["service_kn"], 1.0 - size)

    flag = flag or profile.sample_flag(rng)
    name = name if name is not None else vessel_name(rng, used_names)

    return SyntheticVessel(
        entity_id=entity_id,
        vessel_class=vessel_class,
        imo=mint_imo(serial),
        mmsi=mint_mmsi(serial),
        name=name,
        flag=flag,
        call_sign=_call_sign(rng, flag),
        length_m=round(length, 1),
        beam_m=round(beam, 1),
        draught_m=round(draught, 2),
        dwt=round(dwt),
        service_kn=round(service, 2),
        max_kn=float(dims["max_kn"]),
        accel_kn_per_min=float(dims["accel_kn_per_min"]),
        rot_deg_per_s=float(dims["rot_deg_per_s"]),
        ais_expected=ais_expected,
        role=role,
        notes=notes,
    )
