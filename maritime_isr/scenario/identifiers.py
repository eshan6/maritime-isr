"""Reserved identifier space for scenario entities, and the collision guard.

**The hard rule this module enforces: no scenario entity ever wears a real
vessel's identity.** A synthetic hull that borrows a real IMO, a real MMSI or a
real OFAC entry number is not a test fixture — it is a false accusation sitting
in the same tables as our findings, and no downstream `is_synthetic` filter can
undo the damage once a number has been quoted.

Three reservations, each with a reason:

**MMSI — the 999xxxxxx block.** An MMSI's first three digits are its Maritime
Identification Digits, the flag-state code. Assigned MIDs run 201-775; **999 is
not and cannot be an assigned MID**, so the entire 999000000-999999999 block is
structurally unreachable by a real transmitting vessel. This is a stronger
guarantee than "we checked and it was free" — it stays true as new vessels are
registered.

**IMO — the 1xxxxxx block.** IMO ship identification numbers are 7 digits with
a check digit, and the assignment series runs from 5000000 upward (it is a
continuation of the Lloyd's Register series). The 1000000-1999999 band is not
in that series. Synthetic IMOs are drawn from it and are **checksum-valid**, so
they exercise `normalise_imo`'s check-digit validation exactly as a real number
would — a synthetic corpus whose IMOs all failed validation would silently skip
the code path it is meant to test.

**Sanctions — the fictional SCENARIO-SDN list.** Synthetic sanctions references
name a list that does not exist, with entry numbers of the form
`SCENARIO-SDN-0001`. They never point at a real OFAC entry number, so a
scenario can never be mistaken for a real designation.

**The range reservations are defence in depth, not the guarantee.** The actual
guarantee is `assert_no_collisions`, which checks every generated identifier
against the real landed corpus and the real OFAC snapshot *at generation time*.
On a machine where those tables exist that is a live check against real data; in
a sandbox without them it falls back to the identifier lists captured in the
corpus profile, and says which of the two it did. A check that silently degrades
to checking nothing is the failure mode this project has already been bitten by
four times (see STATE.md) — so this one reports its own denominator.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..ingest.sanctions_match import imo_checksum_ok

# --------------------------------------------------------------------------
# the reserved blocks (recorded in DECISIONS.md as ADR-019)
# --------------------------------------------------------------------------

MMSI_MIN = 999_000_000
MMSI_MAX = 999_999_999

IMO_MIN = 1_000_000
IMO_MAX = 1_999_999

#: The fictional sanctions list synthetic designations point at.
SCENARIO_SDN = "SCENARIO-SDN"

#: The source_id every synthetic row carries in its provenance envelope. The
#: `is_synthetic` flag and this value must always agree — a test asserts they
#: cannot disagree, because two independent markers that can drift apart are
#: worse than one.
SYNTHETIC_SOURCE_ID = "synthetic-scenario"


def is_synthetic_mmsi(mmsi) -> bool:
    try:
        return MMSI_MIN <= int(mmsi) <= MMSI_MAX
    except (TypeError, ValueError):
        return False


def is_synthetic_imo(imo) -> bool:
    try:
        return IMO_MIN <= int(imo) <= IMO_MAX
    except (TypeError, ValueError):
        return False


def is_scenario_sanctions_ref(ref) -> bool:
    return bool(ref) and str(ref).upper().startswith(SCENARIO_SDN)


# --------------------------------------------------------------------------
# minting
# --------------------------------------------------------------------------

def imo_check_digit(prefix6: int) -> int:
    """The seventh digit for a six-digit IMO prefix.

    Multiply the six digits by 7, 6, 5, 4, 3, 2, sum, take the last digit —
    the same arithmetic `imo_checksum_ok` validates, expressed forwards.
    """
    d = [int(c) for c in f"{prefix6:06d}"]
    return sum(d[i] * (7 - i) for i in range(6)) % 10


def mint_imo(serial: int) -> int:
    """A checksum-valid IMO inside the reserved band, from a 0-based serial.

    `serial` indexes the band deterministically, so the same cast always gets
    the same hulls. Raises rather than wrapping if the band is exhausted —
    silently reusing a number would give two scenario vessels one hull.
    """
    prefix = 100_000 + serial
    if prefix > 199_999:
        raise ValueError(f"reserved IMO band exhausted at serial {serial}")
    imo = prefix * 10 + imo_check_digit(prefix)
    if not imo_checksum_ok(str(imo)):          # belt and braces; the arithmetic
        raise AssertionError(f"minted IMO {imo} fails its own check digit")
    if not (IMO_MIN <= imo <= IMO_MAX):
        raise AssertionError(f"minted IMO {imo} escaped the reserved band")
    return imo


def mint_mmsi(serial: int) -> int:
    """An MMSI inside the reserved 999 block, from a 0-based serial."""
    mmsi = MMSI_MIN + serial
    if mmsi > MMSI_MAX:
        raise ValueError(f"reserved MMSI band exhausted at serial {serial}")
    return mmsi


def mint_sanctions_ref(serial: int) -> str:
    return f"{SCENARIO_SDN}-{serial:04d}"


# --------------------------------------------------------------------------
# the collision guard
# --------------------------------------------------------------------------

@dataclass
class CollisionReport:
    """What was actually checked, against what, and what it found.

    `checked_against` names the source of the real identifier sets and
    `n_real_imos` / `n_real_mmsis` are its denominators, so a run against an
    empty corpus cannot be read as a clean bill of health.
    """
    checked_against: str
    n_real_imos: int
    n_real_mmsis: int
    n_ofac_imos: int
    imo_collisions: list
    mmsi_collisions: list
    sanctions_collisions: list

    @property
    def clean(self) -> bool:
        return not (self.imo_collisions or self.mmsi_collisions
                    or self.sanctions_collisions)

    @property
    def is_live_check(self) -> bool:
        return self.checked_against == "landed corpus"

    def describe(self) -> str:
        base = (f"checked {self.n_real_imos} real IMO(s), "
                f"{self.n_real_mmsis} real MMSI(s), "
                f"{self.n_ofac_imos} OFAC IMO(s) from {self.checked_against}")
        if self.clean:
            return base + " — no collisions"
        return (base + f" — COLLISIONS: imo={self.imo_collisions[:5]} "
                f"mmsi={self.mmsi_collisions[:5]} "
                f"sanctions={self.sanctions_collisions[:5]}")


def _real_identifiers_from_corpus() -> tuple[set, set, set] | None:
    """Real IMOs, MMSIs and OFAC IMOs read from the landed tables.

    Returns None when the corpus is not on this machine, which is the normal
    case in a sandbox. Real rows only: anything already flagged synthetic is
    excluded, so re-running the generator does not start colliding with itself.
    """
    try:
        from ..ingest.landing import read_table
    except Exception:                                   # pragma: no cover
        return None

    imos: set = set()
    mmsis: set = set()
    ofac_imos: set = set()
    saw_any = False

    try:
        rows = read_table("gfw_vessel_identity")
    except Exception:
        rows = []
    for r in rows:
        if r.get("is_synthetic"):
            continue
        saw_any = True
        if r.get("imo") not in (None, ""):
            imos.add(str(r["imo"]).strip())
        if r.get("mmsi") not in (None, ""):
            mmsis.add(str(r["mmsi"]).strip())

    # The full OFAC snapshot lives in DuckDB, not in a conformed table.
    try:
        from ..ingest.ofac_lookup import ofac_imos_from_duckdb
        from_duck = ofac_imos_from_duckdb()
        if from_duck:
            ofac_imos |= from_duck
            saw_any = True
    except Exception:                                       # noqa: BLE001
        pass

    for table in ("sanctioned_vessel_matches",):
        try:
            rows = read_table(table)
        except Exception:
            continue
        for r in rows:
            if r.get("is_synthetic"):
                continue
            saw_any = True
            for key in ("imo", "ofac_imo"):
                v = r.get(key)
                if v not in (None, ""):
                    ofac_imos.add(str(v).strip())

    return (imos, mmsis, ofac_imos) if saw_any else None


def assert_no_collisions(imos, mmsis, sanctions_refs, *,
                         profile=None, raise_on_collision: bool = True
                         ) -> CollisionReport:
    """Guarantee no scenario identifier touches a real hull or a real listing.

    Checks, in order of preference, against the landed corpus on this machine;
    failing that, against the identifier lists captured in the corpus profile.
    The report names which, so "no collisions" is never quoted without its
    denominator.
    """
    real = _real_identifiers_from_corpus()
    if real is not None:
        source = "landed corpus"
        real_imos, real_mmsis, ofac_imos = real
    elif profile is not None:
        source = f"corpus profile ({profile.origin})"
        real_imos = set(profile.real_imos())
        real_mmsis = set(profile.real_mmsis())
        ofac_imos = set(profile.ofac_imos())
    else:
        source = "nothing (no corpus, no profile)"
        real_imos = real_mmsis = ofac_imos = set()

    as_str_imo = {str(i).strip() for i in imos}
    as_str_mmsi = {str(m).strip() for m in mmsis}

    rep = CollisionReport(
        checked_against=source,
        n_real_imos=len(real_imos),
        n_real_mmsis=len(real_mmsis),
        n_ofac_imos=len(ofac_imos),
        imo_collisions=sorted(as_str_imo & (real_imos | ofac_imos)),
        mmsi_collisions=sorted(as_str_mmsi & real_mmsis),
        sanctions_collisions=sorted(
            r for r in sanctions_refs if not is_scenario_sanctions_ref(r)),
    )

    # Range violations are a collision with the reservation itself, and are
    # fatal regardless of what the corpus contains.
    out_of_band_imo = sorted(i for i in as_str_imo
                             if not is_synthetic_imo(i))
    out_of_band_mmsi = sorted(m for m in as_str_mmsi
                              if not is_synthetic_mmsi(m))
    rep.imo_collisions = sorted(set(rep.imo_collisions) | set(out_of_band_imo))
    rep.mmsi_collisions = sorted(set(rep.mmsi_collisions) | set(out_of_band_mmsi))

    if raise_on_collision and not rep.clean:
        raise ValueError(f"scenario identifier collision — {rep.describe()}")
    return rep
