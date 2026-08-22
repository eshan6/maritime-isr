"""Is the identity this vessel broadcasts internally consistent? — Area 2.

*"The existing software cannot judge whether that static information is
authentic. The ask is anomaly detection in static information."* — the IDEX
Challenge 82 brief, Area 2.

Four checks, ordered by how cheap and how certain each one is. The first two are
the ones the Section-3 brief singles out as "cheap, precise, and currently
missing", and they are worth building before anything cleverer because they are
**arithmetic, not inference**:

1. **The IMO check digit.** The seventh digit of an IMO number is a checksum
   over the first six. It either passes or it does not. It rejects 90.3% of
   random seven-digit strings, so a failure is a real fact about the number
   rather than a judgement about the ship.
2. **The MMSI country prefix against the declared flag.** The first three digits
   of an MMSI are the Maritime Identification Digits, allocated by the ITU to a
   flag administration. A hull broadcasting a Panamanian MID while declaring an
   Indian flag is stating two incompatible things about itself in the same
   message stream. This produces almost no false positives *provided* the MID
   table refuses to guess — see :data:`MID_TO_FLAG`.
3. **Registry consistency.** Name, call sign and vessel type as broadcast
   against name, call sign and vessel type as recorded. Promoted here from
   "a component buried in a score" to a rule of its own, because a contradiction
   between what a ship says and what a registry holds is a finding an operator
   can act on, not a term in a float.
4. **MMSI structural validity.** Nine digits, and not one of the reserved
   non-vessel forms (coast station, SAR aircraft, AtoN, craft associated with a
   parent ship). A vessel broadcasting an aid-to-navigation MMSI is not an aid
   to navigation.

**Where these can and cannot fire, stated plainly.** The scenario corpus mints
every MMSI inside the reserved 999-block and every IMO with a valid check digit
(ADR-019 / `scenario.identifiers`), deliberately, so that synthetic identifiers
can never collide with real ones. The consequence is that **checks 1 and 2
cannot fire on scenario data by construction** — 999 is not an assigned MID and
a generated IMO always passes. They are built to run against the landed **real**
GFW identity corpus, where the MMSIs and flags are real, and that is where their
precision must be measured. Until that run happens on the laptop, they are
"built, unverified on host" (CLAUDE.md §5) and the tests here exercise them on
fixtures.

Check 3 fires on either corpus.
"""
from __future__ import annotations

import hashlib
from typing import Iterable, Optional

from ..ingest.sanctions_match import imo_checksum_ok

__all__ = ["check_imo", "check_mmsi_flag", "check_mmsi_form",
           "check_registry_consistency", "check_identity", "IdentityFinding",
           "MID_TO_FLAG", "mid_of"]


#: ITU Maritime Identification Digits to ISO 3166-1 alpha-3 flag code.
#:
#: **Deliberately partial, and that is the design.** This rule's entire value is
#: that it almost never produces a false positive, and the fastest way to
#: destroy that is a wrong row: a hull correctly flagged to a state this table
#: misattributes would be reported as an identity contradiction at high
#: confidence, which is exactly the confident error that is worse than no
#: answer. So the table carries only allocations that are stable and
#: well-known — the flags of this area of operations, the major open registries,
#: and the largest national fleets — and an MMSI whose MID is absent produces
#: **no claim at all** rather than a guess. `check_mmsi_flag` returns
#: ``unknown_mid`` for those, and the count of them is worth watching: if it is
#: most of a corpus, extend the table rather than trusting the silence.
#:
#: Several administrations hold more than one MID; every one of theirs maps to
#: the same flag code, so the direction of the lookup is unambiguous.
MID_TO_FLAG: dict[int, str] = {
    # --- the area of operations ---------------------------------------
    419: "IND",                                   # India
    405: "PAK",                                   # Pakistan
    417: "LKA",                                   # Sri Lanka
    455: "MDV",                                   # Maldives
    461: "OMN",                                   # Oman
    470: "ARE", 471: "ARE",                       # United Arab Emirates
    403: "SAU",                                   # Saudi Arabia
    422: "IRN",                                   # Iran
    425: "IRQ",                                   # Iraq
    466: "QAT",                                   # Qatar
    447: "KWT",                                   # Kuwait
    408: "BHR",                                   # Bahrain
    473: "YEM", 475: "YEM",                       # Yemen
    438: "JOR",                                   # Jordan
    468: "SYR",                                   # Syria
    428: "ISR",                                   # Israel
    401: "AFG",                                   # Afghanistan
    459: "NPL",                                   # Nepal
    # --- the large open registries ------------------------------------
    351: "PAN", 352: "PAN", 353: "PAN", 354: "PAN", 355: "PAN",
    356: "PAN", 357: "PAN", 370: "PAN", 371: "PAN", 372: "PAN",
    373: "PAN", 374: "PAN",                       # Panama
    636: "LBR", 637: "LBR",                       # Liberia
    538: "MHL",                                   # Marshall Islands
    215: "MLT", 229: "MLT", 248: "MLT", 249: "MLT", 256: "MLT",  # Malta
    209: "CYP", 210: "CYP", 212: "CYP",           # Cyprus
    616: "COM",                                   # Comoros
    671: "TGO",                                   # Togo
    667: "SLE",                                   # Sierra Leone
    613: "CMR",                                   # Cameroon
    642: "LBY",                                   # Libya
    677: "TZA", 674: "TZA",                       # Tanzania / Zanzibar
    576: "VUT", 577: "VUT",                       # Vanuatu
    525: "IDN",                                   # Indonesia
    # --- major national fleets and neighbours -------------------------
    412: "CHN", 413: "CHN", 414: "CHN",           # China
    477: "HKG",                                   # Hong Kong
    416: "TWN",                                   # Taiwan
    431: "JPN", 432: "JPN",                       # Japan
    440: "KOR", 441: "KOR",                       # Republic of Korea
    445: "PRK",                                   # DPR Korea
    533: "MYS",                                   # Malaysia
    563: "SGP", 564: "SGP", 565: "SGP", 566: "SGP",  # Singapore
    567: "THA",                                   # Thailand
    574: "VNM",                                   # Viet Nam
    548: "PHL",                                   # Philippines
    503: "AUS",                                   # Australia
    512: "NZL",                                   # New Zealand
    273: "RUS",                                   # Russian Federation
    272: "UKR",                                   # Ukraine
    271: "TUR",                                   # Turkiye
    237: "GRC", 239: "GRC", 240: "GRC", 241: "GRC",  # Greece
    247: "ITA",                                   # Italy
    224: "ESP", 225: "ESP",                       # Spain
    226: "FRA", 227: "FRA", 228: "FRA",           # France
    211: "DEU", 218: "DEU",                       # Germany
    244: "NLD", 245: "NLD", 246: "NLD",           # Netherlands
    232: "GBR", 233: "GBR", 234: "GBR", 235: "GBR",  # United Kingdom
    219: "DNK", 220: "DNK",                       # Denmark
    257: "NOR", 258: "NOR", 259: "NOR",           # Norway
    265: "SWE", 266: "SWE",                       # Sweden
    230: "FIN",                                   # Finland
    236: "GIB",                                   # Gibraltar
    316: "CAN",                                   # Canada
    338: "USA", 366: "USA", 367: "USA", 368: "USA", 369: "USA",  # USA
    345: "MEX",                                   # Mexico
    710: "BRA",                                   # Brazil
    622: "EGY",                                   # Egypt
    664: "SDN",                                   # Sudan
    657: "NGA",                                   # Nigeria
    242: "MAR",                                   # Morocco
    672: "TUN",                                   # Tunisia
    605: "DZA",                                   # Algeria
}

#: MMSI leading forms that are not a ship's own identity. A hull broadcasting
#: one of these is misrepresenting what kind of station it is.
#:
#: ``00`` coast station, ``111`` SAR aircraft, ``99`` aid to navigation,
#: ``98`` craft associated with a parent ship, ``970/972/974`` AIS-SART, MOB
#: and EPIRB. Each has a defined meaning in ITU-R M.585 and none of them is
#: "a merchant vessel under way".
_NON_VESSEL_PREFIXES: tuple[tuple[str, str], ...] = (
    ("111", "a search-and-rescue aircraft"),
    ("970", "an AIS search-and-rescue transmitter"),
    ("972", "an AIS man-overboard device"),
    ("974", "an AIS EPIRB"),
    ("99", "an aid to navigation"),
    ("98", "a craft associated with a parent ship"),
    ("00", "a coast station"),
    ("0", "a group of ships or a coast station"),
)


class IdentityFinding:
    """One contradiction in what a vessel declares about itself.

    Deliberately not a :class:`~maritime_isr.assistant.model.Factor`: this
    module is a *rule*, and the assistant layer is what turns rule output into a
    ranked factor. Keeping the two apart is what stops a detector learning about
    the surface it feeds.
    """

    __slots__ = ("check", "outcome", "confidence", "statement", "detail")

    def __init__(self, check: str, outcome: str, confidence: float,
                 statement: str, detail: dict | None = None):
        self.check = check
        #: ``contradiction`` | ``ok`` | ``not_checkable``
        self.outcome = outcome
        self.confidence = confidence
        self.statement = statement
        self.detail = detail or {}

    @property
    def is_contradiction(self) -> bool:
        return self.outcome == "contradiction"

    def as_dict(self) -> dict:
        return {"check": self.check, "outcome": self.outcome,
                "confidence": round(float(self.confidence), 3),
                "statement": self.statement, "detail": self.detail}

    def __repr__(self) -> str:                                   # pragma: no cover
        return f"<IdentityFinding {self.check} {self.outcome}>"


def _digits(value) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    if s.upper().startswith("IMO"):
        s = s[3:].strip()
    # A Parquet round-trip turns an integer column into a float, so "9074729"
    # arrives as "9074729.0" and every check-digit test would fail on the whole
    # corpus at once. The same trap ADR-022 hit on the MMSI join.
    if s.endswith(".0"):
        s = s[:-2]
    return s if s.isdigit() else None


def _is_project_reserved(digits: str) -> bool:
    """Is this an MMSI **this project minted** into its reserved block?

    **The scenario generator's reserved block collides with an ITU reserved
    form, and the collision fires a contradiction on the entire synthetic
    fleet.** `scenario.identifiers` mints into 999000000-999999999 precisely
    because 999 is not and cannot be an assigned MID, which makes it safe
    against collision with a real hull. ITU-R M.585 separately reserves the
    two-digit prefix `99` for aids to navigation. Both facts are true, and
    together they made `check_mmsi_form` report all 222 scenario vessels as
    "misrepresenting what kind of station it is" — a contradiction on every
    hull in the corpus, which is the queue-flooding failure ADR-004 exists to
    prevent, arriving through the one detector built to have no false
    positives.

    This is **a rule about an identifier space, not about ground truth** — the
    same line `graph.identity.sensor_ref_is_synthetic` already walks. It
    consults which block a number was minted from, never whether the row is
    real, and it is imported from the one module that owns that block so the
    two cannot drift apart.
    """
    from ..scenario.identifiers import is_synthetic_mmsi
    return bool(is_synthetic_mmsi(digits))


def mid_of(mmsi) -> Optional[int]:
    """The Maritime Identification Digits of an MMSI, or None if it has none.

    A ship-station MMSI is nine digits and its MID is the first three. The
    reserved forms in :data:`_NON_VESSEL_PREFIXES` do not carry a MID in that
    position at all, so reading one would produce a nonsense country.
    """
    d = _digits(mmsi)
    if d is None or len(d) != 9:
        return None
    if _is_project_reserved(d) or any(d.startswith(p)
                                      for p, _ in _NON_VESSEL_PREFIXES):
        return None
    return int(d[:3])


# --------------------------------------------------------------------------
# 1. the IMO check digit
# --------------------------------------------------------------------------

def check_imo(imo) -> IdentityFinding:
    """Arithmetic, and it either passes or it does not.

    Reuses ``ingest.sanctions_match.imo_checksum_ok`` rather than restating the
    arithmetic. Two implementations of one checksum is two chances to disagree,
    and the disagreement would be invisible: both would validate most numbers.
    """
    d = _digits(imo)
    if d is None:
        return IdentityFinding(
            "imo_check_digit", "not_checkable", 0.0,
            "No IMO number is on record for this hull, so its check digit "
            "cannot be tested. That is a gap in the record, not a finding.",
            {"imo": None})
    if len(d) != 7:
        return IdentityFinding(
            "imo_check_digit", "contradiction", 0.8,
            f"The IMO number on record, {d}, is {len(d)} digits long. An IMO "
            f"number is seven. This is a malformed identifier rather than a "
            f"false one, so it is more likely a data-entry fault than a "
            f"deception — but it is not a valid IMO.",
            {"imo": d, "length": len(d)})
    if imo_checksum_ok(d):
        return IdentityFinding(
            "imo_check_digit", "ok", 0.0,
            f"IMO {d} passes its check digit.", {"imo": d})
    return IdentityFinding(
        "imo_check_digit", "contradiction", 0.9,
        f"IMO {d} fails its own check digit. The seventh digit of an IMO "
        f"number is a checksum over the first six, and this one does not "
        f"match — the number is not a validly issued IMO. The checksum "
        f"rejects about 90% of random seven-digit strings, so this is very "
        f"unlikely to be chance.",
        {"imo": d})


# --------------------------------------------------------------------------
# 2. the MMSI country prefix against the declared flag
# --------------------------------------------------------------------------

def check_mmsi_flag(mmsi, flag) -> IdentityFinding:
    """Does the MID the hull broadcasts agree with the flag it declares?

    Returns ``not_checkable`` rather than a contradiction whenever the answer is
    genuinely unknown — an absent flag, an absent MMSI, or a MID this project
    does not carry an allocation for. That asymmetry is the whole reason this
    rule is safe to run: silence is cheap and a wrong contradiction is not.
    """
    d = _digits(mmsi)
    declared = (str(flag).strip().upper() if flag else None) or None

    if d is None or len(d) != 9:
        return IdentityFinding(
            "mmsi_flag_agreement", "not_checkable", 0.0,
            "No nine-digit MMSI is on record, so the country prefix cannot be "
            "read.", {"mmsi": d, "flag": declared})
    if declared is None:
        return IdentityFinding(
            "mmsi_flag_agreement", "not_checkable", 0.0,
            f"MMSI {d} is on record but no flag is, so there is nothing to "
            f"compare the country prefix against.", {"mmsi": d, "flag": None})

    mid = mid_of(d)
    if mid is None:
        reserved = _is_project_reserved(d)
        return IdentityFinding(
            "mmsi_flag_agreement", "not_checkable", 0.0,
            (f"MMSI {d} was minted into this project's reserved scenario "
             f"block, so it carries no country digits to compare."
             if reserved else
             f"MMSI {d} is not a ship-station identity — it uses a reserved "
             f"prefix — so it carries no country digits to compare."),
            {"mmsi": d, "flag": declared,
             "reason": ("project_reserved_block" if reserved
                        else "reserved_prefix")})

    expected = MID_TO_FLAG.get(mid)
    if expected is None:
        return IdentityFinding(
            "mmsi_flag_agreement", "not_checkable", 0.0,
            f"MMSI {d} begins with {mid}, which this system holds no flag "
            f"allocation for, so no comparison is made. The table is "
            f"deliberately partial: guessing here would produce confident "
            f"false contradictions.",
            {"mmsi": d, "flag": declared, "mid": mid, "reason": "unknown_mid"})

    if expected == declared:
        return IdentityFinding(
            "mmsi_flag_agreement", "ok", 0.0,
            f"MMSI {d} begins with {mid}, allocated to {expected}, which "
            f"agrees with the declared flag.",
            {"mmsi": d, "flag": declared, "mid": mid, "expected": expected})

    return IdentityFinding(
        "mmsi_flag_agreement", "contradiction", 0.85,
        f"This hull broadcasts MMSI {d} — the prefix {mid} is allocated to "
        f"{expected} — while declaring the flag of {declared}. Those are two "
        f"incompatible statements about the same ship in the same message "
        f"stream. A reflag that has not been followed by an MMSI reissue looks "
        f"exactly like this and is the innocent explanation; it is still worth "
        f"a call.",
        {"mmsi": d, "flag": declared, "mid": mid, "expected": expected})


# --------------------------------------------------------------------------
# 3. MMSI structural form
# --------------------------------------------------------------------------

def check_mmsi_form(mmsi) -> IdentityFinding:
    """Nine digits, and not one of the reserved non-vessel forms."""
    d = _digits(mmsi)
    if d is None:
        return IdentityFinding("mmsi_form", "not_checkable", 0.0,
                               "No MMSI is on record for this hull.",
                               {"mmsi": None})
    if len(d) != 9:
        return IdentityFinding(
            "mmsi_form", "contradiction", 0.75,
            f"The MMSI on record, {d}, is {len(d)} digits. An MMSI is nine.",
            {"mmsi": d, "length": len(d)})
    if _is_project_reserved(d):
        return IdentityFinding(
            "mmsi_form", "not_checkable", 0.0,
            f"MMSI {d} was minted into this project's reserved scenario block, "
            f"which overlaps an ITU reserved form. Its structure says nothing "
            f"about a vessel either way, so no claim is made. See "
            f"`_is_project_reserved`.",
            {"mmsi": d, "reason": "project_reserved_block"})
    for prefix, what in _NON_VESSEL_PREFIXES:
        if d.startswith(prefix):
            return IdentityFinding(
                "mmsi_form", "contradiction", 0.8,
                f"MMSI {d} begins with {prefix}, which ITU-R M.585 reserves "
                f"for {what} — not for a vessel's own identity. A ship "
                f"broadcasting it is misrepresenting what kind of station "
                f"it is.",
                {"mmsi": d, "reserved_prefix": prefix, "reserved_for": what})
    return IdentityFinding("mmsi_form", "ok", 0.0,
                           f"MMSI {d} is a well-formed ship-station identity.",
                           {"mmsi": d})


# --------------------------------------------------------------------------
# 4. registry consistency
# --------------------------------------------------------------------------

def _norm_name(v) -> Optional[str]:
    if v is None:
        return None
    s = " ".join(str(v).upper().split())
    # Registries differ on punctuation and on the M/V, M/T, MV prefixes. A
    # comparison that treated "M/V SEA STAR" and "SEA STAR" as different names
    # would report a contradiction on a large fraction of an honest corpus.
    for prefix in ("M/V ", "M/T ", "MV ", "MT ", "M.V. ", "M.T. "):
        if s.startswith(prefix):
            s = s[len(prefix):]
    return "".join(c for c in s if c.isalnum() or c == " ").strip() or None


def check_registry_consistency(*, broadcast: dict, registry: dict
                               ) -> list[IdentityFinding]:
    """Name, call sign and vessel type as broadcast against as recorded.

    ``broadcast`` and ``registry`` are dicts with any of ``name``,
    ``call_sign``, ``vessel_class``. A field absent from either side is not
    compared — an absent value is a gap in the record and reporting it as a
    disagreement would fire on most of an honest corpus.

    **The confidences differ by field and the reason is worth stating.** A call
    sign is issued with the flag and changes only when the flag does, so a
    disagreement is a strong signal. A name changes on sale and registries lag,
    so a disagreement is common and weak on its own. A vessel type is a
    classification two sources can legitimately disagree about — "general cargo"
    and "bulker" are the same hull to some registries — so it is weakest.
    """
    out: list[IdentityFinding] = []
    checks = (
        ("name", _norm_name, 0.45,
         "changes on sale and registries lag, so this alone is a lead"),
        ("call_sign", lambda v: (str(v).upper().strip() or None) if v else None,
         0.7,
         "a call sign is issued with the flag and changes only when the flag "
         "does, so a disagreement is a strong signal"),
        ("vessel_class", _norm_name, 0.3,
         "two registries can legitimately classify one hull differently"),
    )
    for field, norm, conf, why in checks:
        b, r = norm(broadcast.get(field)), norm(registry.get(field))
        label = field.replace("_", " ")
        if b is None or r is None:
            out.append(IdentityFinding(
                f"registry_{field}", "not_checkable", 0.0,
                f"The {label} is missing on "
                + ("the broadcast side" if b is None else "the registry side")
                + ", so the two cannot be compared.",
                {"broadcast": b, "registry": r}))
            continue
        if b == r:
            out.append(IdentityFinding(
                f"registry_{field}", "ok", 0.0,
                f"The broadcast {label} matches the registry.",
                {"broadcast": b, "registry": r}))
            continue
        out.append(IdentityFinding(
            f"registry_{field}", "contradiction", conf,
            f"This hull broadcasts the {label} “{b}” while the "
            f"registry records “{r}”. Noted at moderate confidence "
            f"because {why}.",
            {"broadcast": b, "registry": r}))
    return out


# --------------------------------------------------------------------------
# the whole check
# --------------------------------------------------------------------------

def check_identity(*, mmsi=None, imo=None, flag=None, name=None,
                   call_sign=None, vessel_class=None,
                   registry: dict | None = None) -> list[IdentityFinding]:
    """Every check, over one hull's declared identity.

    Returns every outcome, including ``ok`` and ``not_checkable``, because a
    surface has to be able to say "we looked and it was fine" and "we could not
    look" as separate things. A caller wanting only the contradictions filters
    on :attr:`IdentityFinding.is_contradiction`.
    """
    out = [check_imo(imo), check_mmsi_form(mmsi), check_mmsi_flag(mmsi, flag)]
    if registry:
        out += check_registry_consistency(
            broadcast={"name": name, "call_sign": call_sign,
                       "vessel_class": vessel_class},
            registry=registry)
    return out


def finding_id(vessel_id: str, check: str) -> str:
    """A stable id for one contradiction on one hull.

    Derived from the hull and the check rather than from the values, so
    re-running against a refreshed registry snapshot does not mint a new
    finding for the same standing disagreement.
    """
    return "idf_" + hashlib.sha1(
        f"{vessel_id}|{check}".encode()).hexdigest()[:12]


def summarise(findings: Iterable[IdentityFinding]) -> dict:
    """Counts by outcome — what fired, what passed, what could not be asked.

    The third number is the one that matters most when reading a corpus-wide
    run: a check that is `not_checkable` on 95% of hulls has told you almost
    nothing, and a summary that reported only contradictions would present that
    silence as a clean bill of health.
    """
    counts: dict[str, dict[str, int]] = {}
    for f in findings:
        counts.setdefault(f.check, {"contradiction": 0, "ok": 0,
                                    "not_checkable": 0})
        counts[f.check][f.outcome] += 1
    return counts
