"""Plain-language accounts of why a subject is on the list.

The Section-3 brief sets the bar precisely: *"Not a table of feature values.
Sentences a duty officer can read aloud on a radio call."* That is the test
every line here has to pass, and it rules out three tempting shortcuts:

* **No feature names.** "sog_p90 2.1" is not a sentence. "She held under two
  knots for six hours" is.
* **No unattributed claims.** Where somebody else asserted the fact — GFW
  assessed a gap, OFAC designated a hull — the sentence says so. The alternative
  is a system that quietly takes credit and then cannot defend the finding.
* **No hedging into meaninglessness.** "Possible anomalous behaviour detected"
  tells a watchkeeper nothing. Give the number, the place and the duration.

Written as templates rather than free text generation, and deliberately so:
these sentences are read aloud on a radio and pasted into an incident report, so
they must be reproducible, checkable against the evidence they came from, and
incapable of asserting something the system does not hold. A generated sentence
that invents a fact would fail the one requirement the assistant exists to
satisfy.
"""
from __future__ import annotations

from typing import Optional, Sequence

from .catalog import FAMILIES, spec
from .model import Factor

__all__ = ["narrate_factor", "narrate_subject", "confidence_word",
           "position_phrase"]


def confidence_word(c: float) -> str:
    """Confidence as a word, because a decimal is not readable on a radio.

    The bands are stated rather than tuned: below 0.4 the system is offering a
    lead, above 0.85 it is willing to stand behind the claim, and the middle is
    where an analyst's judgement is actually required.
    """
    c = float(c)
    if c >= 0.85:
        return "high confidence"
    if c >= 0.6:
        return "moderate confidence"
    if c >= 0.4:
        return "low confidence"
    return "weak, a lead only"


def _fmt_pos(lat: Optional[float], lon: Optional[float]) -> Optional[str]:
    if lat is None or lon is None:
        return None
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"{abs(lat):.2f}°{ns} {abs(lon):.2f}°{ew}"


def position_phrase(pos: dict) -> str:
    p = _fmt_pos(pos.get("lat"), pos.get("lon"))
    if not p:
        return "position unknown"
    when = pos.get("at")
    return f"{p}" + (f" at {when}" if when else "")


def _hours(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _date(iso_ts: Optional[str]) -> str:
    return (iso_ts or "")[:10] or "an unrecorded date"


# --------------------------------------------------------------------------
# per-factor sentences
# --------------------------------------------------------------------------

def narrate_factor(f: Factor, *, name: str) -> str:
    """One sentence about one factor, subject-first so it composes."""
    d = f.detail or {}
    n = int(d.get("occurrences") or 1)
    again = f" This has happened {n} times." if n > 1 else ""
    conf = confidence_word(f.confidence)

    if f.kind == "dark_vessel":
        length = d.get("length_m")
        size = f" about {float(length):.0f} m long" if length else ""
        where = _fmt_pos(d.get("lat"), d.get("lon"))
        # The inferred profile, when Area 3 supplied one. "Probable fishing
        # vessel, loitering, no transponder" is intelligence; a position is not.
        statement = d.get("statement")
        profile = (f" From her motion alone: {statement}" if statement else "")
        return (f"Radar is holding a contact{size}"
                + (f" at {where}" if where else "")
                + ", and nothing is broadcasting an AIS position there. "
                + f"The system rates that {conf}.{profile}{again}")

    if f.kind == "transponder_shutdown":
        mins = _hours(d.get("dark_minutes")) or 0.0
        mmsi = d.get("mmsi")
        who = (f"She was identified as MMSI {mmsi} and then her transponder "
               f"stopped" if mmsi else "Her transponder stopped")
        return (f"{who}, while radar kept holding her for a further "
                f"{mins / 60:.1f} hours. {conf.capitalize()}.{again}")

    if f.kind == "dark_rendezvous":
        where = _fmt_pos(d.get("lat"), d.get("lon"))
        return (f"{name} held close quarters with another target"
                + (f" at {where}" if where else "")
                + ", and one of the two was unexplained at the time, nothing "
                  "was broadcasting for it. That is the signature of a "
                  f"ship-to-ship transfer. {conf.capitalize()}.{again}")

    if f.kind == "loitering_sensitive":
        h = _hours(d.get("hours"))
        zone = d.get("zone") or "a watched area"
        return (f"{name} sat"
                + (f" for {h:.1f} hours" if h else "")
                + f" inside {zone}, away from any berth or designated "
                  f"anchorage. {conf.capitalize()}.{again}")

    if f.kind == "lane_deviation":
        km = _hours(d.get("dist_km"))
        return (f"{name} was"
                + (f" {km:.0f} km" if km else " well")
                + " outside every customary shipping lane for a sustained "
                  f"period while still making way. {conf.capitalize()}.{again}")

    if f.kind == "anchored_outside_limits":
        h = _hours(d.get("hours"))
        return (f"{name} anchored"
                + (f" for {h:.1f} hours" if h else "")
                + " inside territorial waters but outside every declared port "
                  f"or anchorage. {conf.capitalize()}.{again}")

    if f.kind == "maiden_zone_visit":
        zone = d.get("zone") or "an area"
        return (f"{name} entered {zone} for the first time on record, having "
                f"worked this coast elsewhere. {conf.capitalize()}.{again}")

    if f.kind == "vessel_interaction":
        kind = str(d.get("kind") or "interaction").replace("_", " ")
        h = _hours(d.get("hours"))
        cross = (" This one pairs a named hull with an unidentified contact, "
                 "the kind of event no single sensor can see."
                 if d.get("cross_sensor") else "")
        follower = d.get("follower")
        lead = (f"{name} was the following vessel." if follower else "")
        return (f"{name} was {kind} with another track"
                + (f" for {h:.1f} hours" if h else "")
                + f". {d.get('reason') or ''}".rstrip()
                + f" {conf.capitalize()}.{cross} {lead}{again}".rstrip())

    if f.kind == "notable_activity":
        act = str(d.get("activity") or "unusual motion").replace("_", " ")
        h = _hours(d.get("hours"))
        local = d.get("local_baseline")
        tail = ""
        if isinstance(local, dict) and local.get("statement"):
            tail = (" Against what is normal in this area: "
                    + local["statement"]
                    + (" That is unusual here."
                       if local.get("unusual") else
                       " That is ordinary here, which is why this is scored "
                       "down rather than raised."))
        return (f"{name} was {act}"
                + (f" for {h:.1f} hours" if h else "")
                + f". {d.get('reason') or ''}".rstrip()
                + f" {conf.capitalize()}.{tail}{again}")

    if f.kind == "identity_contradiction":
        statements = d.get("statements") or []
        n_c = int(d.get("n_contradictions") or len(statements) or 1)
        lead = (f"{name} declares an identity that does not hold together, "
                f"{n_c} contradiction{'' if n_c == 1 else 's'}.")
        return (lead + " " + " ".join(str(s) for s in statements[:3])
                + f" {conf.capitalize()}.")

    if f.kind == "voyage_contradiction":
        statements = d.get("statements") or []
        dest = d.get("declared_destination")
        lead = (f"{name} broadcast a voyage her own track contradicts"
                + (f", she declared {dest}." if dest else "."))
        return (lead + " " + " ".join(str(x) for x in statements[:2])
                + f" {conf.capitalize()}.")

    if f.kind == "paperwork_contradiction":
        statements = d.get("statements") or []
        doc = d.get("document")
        fmt = str(d.get("document_format") or "").replace("_", " ")
        where = (d.get("locators") or [None])[0]
        # **The passage, not the field name.** The brief's bar for Area 4 is
        # that an extracted value is only evidence if it can be traced back to
        # its source text, and a sentence read aloud on a radio is exactly where
        # that has to hold: "the form says" is worth nothing without the line.
        quoted = (d.get("passages") or [None])[0]
        source = ""
        if doc:
            source = (f" Read from {doc}"
                      + (f" ({fmt})" if fmt else "")
                      + (f", {where}" if where else "") + ".")
        shown = f' The form reads: "{quoted}".' if quoted else ""
        return (f"{name}'s arrival notification says something her own track "
                f"disproves. " + " ".join(str(x) for x in statements[:2])
                + f" {conf.capitalize()}.{source}{shown}")

    if f.kind == "imagery_type_mismatch":
        # **Name both types and where the picture came from.** The whole value
        # of this factor over the behavioural ones is that it rests on a
        # photograph, so the sentence has to say which camera, at what range and
        # in what light — a watchkeeper deciding whether to believe it is really
        # asking how good the look was. It also has to say the image is
        # simulated, because in this build it always is (ADR-037) and a factor
        # that let a reader assume otherwise would be the overclaim CLAUDE.md §5
        # exists to prevent.
        imaged = d.get("imaged_type")
        declared = d.get("declared_class") or d.get("declared_group")
        station = d.get("station")
        quality = d.get("image_quality")
        looks = d.get("corroborating_captures")
        model = d.get("model_name")
        seen = (f"images as {imaged}" if imaged else "images as another kind")
        says = (f"while she broadcasts the type of {declared}"
                if declared else "while her transponder says otherwise")
        where = f" Taken from the {station} camera." if station else ""
        howgood = (f" Image quality {float(quality):.2f}."
                   if quality not in (None, "") else "")
        corrob = ""
        if looks and int(looks) > 1:
            corrob = (f" {int(looks)} separate looks agree, at different range "
                      f"and aspect, which is what separates a mismatch from a "
                      f"bad photograph.")
        sim = (f" The image is simulated and carries no pixels; {model} read it."
               if model else " The image is simulated and carries no pixels.")
        return (f"A camera {seen} {says}, and a hull does not change shape "
                f"between messages. {conf.capitalize()}.{where}{howgood}"
                f"{corrob}{sim}")

    if f.kind == "notification_unmatched":
        declared = d.get("declared_name") or "an unnamed vessel"
        imo = d.get("imo") or d.get("declared_imo")
        port = d.get("arrival_port")
        return (f"An arrival notification was filed for {declared}"
                + (f", IMO {imo}" if imo else "")
                + (f", inbound to {port}" if port else "")
                + ", and no hull this system holds matches it. "
                + f"{d.get('reason') or ''}".rstrip()
                + f" {conf.capitalize()}.")

    if f.kind == "arrival_without_notification":
        port = d.get("port") or "a port"
        return (f"{name} arrived and berthed at {port}, and no arrival "
                f"notification was ever received for her. {conf.capitalize()}."
                f"{again}")

    if f.kind == "assessed_ais_disabling":
        n_gaps = int(d.get("n_gaps") or 1)
        return (f"Global Fishing Watch assessed "
                f"{n_gaps} AIS gap{'' if n_gaps == 1 else 's'} on this hull as "
                f"deliberate disabling, the transponder went quiet and they "
                f"judged it intentional. That is their assessment, carried "
                f"here with attribution; this system did not compute it.")

    if f.kind == "ais_spoofing":
        et = str(d.get("event_type") or "").upper()
        # A null must never reach a sentence somebody reads aloud. "MMSI None"
        # is how a system stops being believed, and it is one missing dict key
        # away at all times — so the identifier is a phrase that disappears when
        # it is absent rather than a slot that renders whatever it holds.
        mmsi = d.get("mmsi")
        on_mmsi = f" on MMSI {mmsi}" if mmsi else ""
        if et == "DUPLICATE_MMSI":
            which = f"MMSI {mmsi}" if mmsi else "one MMSI"
            return (f"Two separate hulls were broadcasting {which} at the "
                    f"same time. One of them is not who it says it is. "
                    f"{conf.capitalize()}.{again}")
        return (f"{name} reported a position jump no ship could physically "
                f"make{on_mmsi}, the track teleports. "
                f"{conf.capitalize()}.{again}")

    if f.kind == "identity_then_anomaly":
        days = _hours(d.get("gap_days"))
        follow = str(d.get("followed_by") or "dark behaviour").replace("_", " ")
        when = (f"within {days:.0f} days" if days and days >= 1
                else "on the same day")
        return (f"{name} changed identity, a rename, a reflag or an MMSI swap "
                f"and {when} afterwards showed {follow}. That sequence is the "
                f"identity-laundering pattern. {conf.capitalize()}.{again}")

    if f.kind == "identity_change":
        n_ch = int(d.get("n_changes") or 1)
        return (f"{name} is on record under more than one identity: "
                f"{n_ch} change{'' if n_ch == 1 else 's'} of name, flag or "
                f"MMSI. Routine on sale, and worth knowing about a hull "
                f"already under suspicion.")

    if f.kind == "flag_opacity":
        bits = []
        if d.get("flag_of_convenience"):
            bits.append("flies a flag of convenience")
        nr = int(d.get("n_reflags") or 0)
        if nr:
            bits.append(f"has reflagged {nr} time{'' if nr == 1 else 's'} "
                        "on record")
        return (f"{name} " + " and ".join(bits or ["carries an opaque flag "
                                                   "history"]) + ".")

    if f.kind == "sanctions_designation":
        regs = " and ".join(d.get("registries") or ["OFAC"])
        tier = ("her IMO number, which survives renaming and reflagging"
                if d.get("matched_on_imo") else "her call sign and name")
        return (f"{name} matches a vessel designated under {regs}. The match "
                f"is on {tier}. The designation is {regs}'s decision; the "
                f"identity match between their list and this hull is ours. "
                f"{conf.capitalize()}.")

    if f.kind == "sanctioned_ownership":
        hops = d.get("hops")
        org = d.get("organisation")
        if org and hops:
            return (f"{name} is owned or operated, {hops} step"
                    f"{'' if hops == 1 else 's'} up the ownership chain, by "
                    f"{org}, which is under sanctions. The hull itself is not "
                    f"listed. {conf.capitalize()}.")
        return (f"{name} sits in an ownership chain that reaches a designated "
                f"entity. The hull itself is not listed. {conf.capitalize()}.")

    if f.kind == "port_risk_propagation":
        ports = d.get("ports") or []
        listed = ", ".join(str(p) for p in ports) or "ports this system "\
                                                     "carries a risk weight for"
        return (f"{name} has called at {listed}. That is a fact about a trade "
                f"route rather than about this ship, and it is the weakest "
                f"thing on this list.")

    # Unreached in practice: every registered kind has a sentence above, and
    # `spec()` refuses an unregistered one. This exists so that adding a kind
    # and forgetting its sentence degrades to something true and dull rather
    # than to a KeyError in front of an operator.
    s = spec(f.kind)
    return f"{name}: {s.label.lower()}, {s.blurb}. {confidence_word(f.confidence).capitalize()}."


# --------------------------------------------------------------------------
# the whole account
# --------------------------------------------------------------------------

def narrate_subject(*, name: str, subject_kind: str, score: float,
                    factors: Sequence[Factor], position: dict,
                    is_synthetic: bool) -> tuple[str, list[str]]:
    """The opening paragraph and the per-factor lines.

    Returns ``(account, lines)``. The account is what a duty officer reads
    first; the lines are what they read out. Factors are ordered by the points
    they contributed, so the sentence a watchkeeper reads first is the reason
    the vessel is highest on the list — not the reason that happened to be
    collected first.
    """
    ordered = sorted(factors, key=lambda f: -(f.points or 0.0))
    lines = [narrate_factor(f, name=name) for f in ordered]

    lead = ("Unidentified target. Nothing has broadcast an "
            "identity for it." if subject_kind != "vessel" else "")

    if ordered:
        top = ordered[0]
        pct = int(round(100 * (top.share or 0.0)))
        driver = (f"Most of that ({pct}%) is {spec(top.kind).label.lower()}."
                  if pct >= 40 else
                  "No single factor dominates; the score is the combination.")
    else:
        driver = ""

    fam_names = []
    for f in ordered:
        label = FAMILIES[f.family]["label"].lower()
        if label not in fam_names:
            fam_names.append(label)
    across = (f" Evidence spans {len(fam_names)} of "
              f"{len(FAMILIES)} evidence families: {', '.join(fam_names)}."
              if fam_names else "")

    head = (f"{name} scores {score:.2f} on "
            f"{len(ordered)} factor{'' if len(ordered) == 1 else 's'}. {driver}"
            f"{across}")
    where = position_phrase(position) if position else ""
    if where and where != "position unknown":
        head += f" Last placed at {where}."
    if lead:
        head = lead + " " + head
    if is_synthetic:
        head += (" SCENARIO DATA. Every figure here is measured on the "
                 "synthetic corpus and says nothing about any real vessel.")
    return head, lines
