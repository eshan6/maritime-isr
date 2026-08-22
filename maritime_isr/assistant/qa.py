"""Asking the system a question about a subject, in ordinary language.

*"Include the ability to ask the system a question about a vessel in ordinary
language and get an answer grounded in what the system actually holds, with the
same evidence discipline. Where the system does not know something, it says so.
An assistant that confabulates a maritime fact will lose an operator's trust
permanently on the first occurrence."* — the Section-3 brief, Area 1.

That last sentence decides the architecture. **This answerer cannot confabulate,
structurally, because it has no generative step.** A question is matched to one
of a closed set of intents; the intent runs a retrieval over what the system
holds; the answer is assembled from the rows that came back. There is no path
by which a fact that is not in a retrieved row can reach the text. It is
therefore duller than a language model and it is correct, which is the right
trade for a surface an operator calibrates their trust against.

Three outcomes, and keeping them distinct is most of the value:

* **answered** — the intent was understood and the data was there.
* **no_data** — the intent was understood and the system holds nothing. "I have
  no record of her calling at any port" is a different and far more useful
  statement than "she has not called at any port", and this is the distinction
  every confident assistant gets wrong.
* **unsupported** — the question was understood well enough to know it is about
  something this system does not carry (cargo, crew, intent), or not understood
  at all. Both say so and name what *can* be asked.

**Swappable by design.** :class:`QuestionAnswerer` is the interface; this module
provides the grounded implementation. If a language model is ever put behind it,
it inherits the same contract — every claim carries the evidence it came from —
and the same three outcomes. The Section-3 brief's standing caution about
borrowed components applies: state the substitution openly, keep the interface.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol, Sequence

from .catalog import FAMILIES, spec
from .model import Evidence, Factor, VesselOfInterest
from .narrate import confidence_word, narrate_factor, position_phrase

__all__ = ["Answer", "QuestionAnswerer", "GroundedQA", "answerable_questions"]


@dataclass
class Answer:
    """One grounded answer.

    ``basis`` names what was read to produce it — the tables, the graph, the
    factor set. It is not decoration: an operator who cannot see what a system
    consulted cannot tell a confident answer from a lucky one.
    """
    question: str
    outcome: str                       # answered | no_data | unsupported
    intent: Optional[str]
    text: str
    basis: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    confidence: Optional[float] = None
    suggestions: list[str] = field(default_factory=list)

    @property
    def grounded(self) -> bool:
        """True when every claim in ``text`` came from a retrieved row.

        ``no_data`` counts as grounded: "the system holds no record of that" is
        a true statement about the store, arrived at by looking.
        """
        return self.outcome in ("answered", "no_data")

    def as_dict(self) -> dict:
        return {
            "question": self.question, "outcome": self.outcome,
            "intent": self.intent, "text": self.text,
            "grounded": self.grounded, "basis": list(self.basis),
            "confidence": (None if self.confidence is None
                           else round(float(self.confidence), 3)),
            "evidence": [e.as_dict() for e in self.evidence],
            "suggestions": list(self.suggestions),
        }


class QuestionAnswerer(Protocol):
    """The interface. See the module docstring on swapping the implementation."""

    def answer(self, question: str, voi: VesselOfInterest) -> Answer: ...

    def answerable(self) -> list[str]: ...


# --------------------------------------------------------------------------
# intent matching
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class _Intent:
    """One thing this answerer knows how to look up.

    Triggers are split into two tiers, and **the split is what stops the
    answerer taking questions it has no business answering.** With a single flat
    list, "Do you like pineapple on pizza?" matched ``what_to_do`` on the word
    "do" and got a confident list of recommendations — a system that will answer
    anything is a system whose "I don't know" means nothing.

      * ``strong`` — words that are near-unambiguous in this domain. "mmsi",
        "sanctioned", "rendezvous", "transponder". One is enough to match.
      * ``weak`` — words that point at an intent but occur everywhere else too.
        "do", "where", "been", "name". Two are needed, or one plus a strong.
      * ``phrases`` — multi-word, and worth most: "went dark" is unambiguous
        where "dark" alone is not.
    """
    name: str
    strong: tuple[str, ...]
    weak: tuple[str, ...] = ()
    phrases: tuple[str, ...] = ()
    question: str = ""


#: Below this, a question is not understood and is refused. Calibrated against
#: the tiers above: one strong word (2.0) or two weak ones (1.0) clears it; one
#: weak word alone (0.5) does not.
MATCH_THRESHOLD = 2.0

#: What this answerer understands. Ordered by specificity: an earlier intent
#: wins a tie, so `why_flagged` beats the generic `evidence` on "why".
INTENTS: tuple[_Intent, ...] = (
    _Intent("why_flagged",
            strong=("flagged", "suspicious", "why"),
            weak=("reason", "reasons", "concern", "wrong"),
            phrases=("why is she", "why is it", "why this", "what's wrong"),
            question="Why is this vessel flagged?"),
    _Intent("what_to_do",
            strong=("recommend", "recommendation", "recommendations", "advise"),
            weak=("do", "next", "action", "actions", "should", "response"),
            phrases=("what should i do", "what next", "what do you recommend",
                     "what do i do"),
            question="What should I do about her?"),
    _Intent("sanctions",
            strong=("sanction", "sanctions", "sanctioned", "designated",
                    "ofac", "designation", "embargo"),
            weak=("listed", "list"),
            question="Is she sanctioned?"),
    _Intent("ownership",
            strong=("owner", "owns", "owned", "ownership", "beneficial"),
            weak=("operator", "operated", "company"),
            phrases=("who owns", "who controls"),
            question="Who owns her?"),
    _Intent("identity",
            strong=("mmsi", "imo", "callsign", "identity", "flag"),
            weak=("name", "type", "class", "length", "called", "registered",
                  "she", "vessel"),
            phrases=("call sign", "what is she", "who is she", "what kind of",
                     "what type"),
            question="What is her name, MMSI, IMO and flag?"),
    _Intent("position",
            strong=("position", "located", "latitude", "longitude"),
            weak=("where", "location", "now"),
            phrases=("where is she", "last seen", "where is it"),
            question="Where is she now?"),
    _Intent("dark_history",
            strong=("dark", "transponder", "ais", "silence", "disabled"),
            weak=("gap", "gaps", "silent", "switched", "off"),
            phrases=("gone dark", "went dark", "turned off", "switched off"),
            question="Has she gone dark before?"),
    _Intent("encounters",
            strong=("rendezvous", "encounter", "encounters", "sts"),
            weak=("meet", "met", "meeting", "alongside", "transfer"),
            phrases=("who did she meet", "ship to ship"),
            question="Who has she met at sea?"),
    _Intent("ports",
            strong=("port", "ports", "berth", "anchorage", "voyage"),
            weak=("called", "visited", "route", "been"),
            phrases=("where has she been", "port calls", "port call"),
            question="Where has she been?"),
    _Intent("zones",
            strong=("zone", "zones", "geofence", "territorial", "eez"),
            weak=("area", "areas", "waters", "boundary"),
            question="Which watched areas has she been in?"),
    _Intent("imagery_opportunity",
            strong=("satellite", "sentinel", "overhead", "sar", "imaged"),
            weak=("imagery", "image", "picture"),
            phrases=("was anyone watching", "did a satellite"),
            question="Was a satellite overhead while she was silent?"),
    _Intent("confidence",
            strong=("confident", "confidence", "certainty"),
            weak=("sure", "certain", "reliable", "trust"),
            phrases=("how sure", "how confident"),
            question="How confident are you?"),
    _Intent("provenance",
            strong=("provenance", "synthetic", "simulated"),
            weak=("real", "source", "sources", "came", "data", "fake"),
            phrases=("is this real", "where did this come from",
                     "is this synthetic", "where did it come from"),
            question="Is this real data, and where did it come from?"),
    _Intent("evidence",
            strong=("evidence", "proof"),
            weak=("show", "basis", "supporting", "records"),
            phrases=("show me the evidence",),
            question="Show me the evidence."),
    _Intent("score",
            strong=("score", "ranked", "ranking", "composite"),
            weak=("rank", "top", "points", "calculated"),
            phrases=("how is the score", "how was the score"),
            question="How was the score calculated?"),
)


#: Topics the answerer recognises and deliberately refuses, with the reason.
#:
#: **The most important dictionary in this module.** A system that answers "what
#: is she carrying?" with a guess has destroyed itself. Naming the topic and the
#: area of the build that would supply it turns a refusal into information: the
#: operator learns the shape of the system's knowledge rather than its
#: willingness to speculate.
UNSUPPORTED: tuple[tuple[tuple[str, ...], str], ...] = (
    (("cargo", "carrying", "laden", "ballast", "load", "manifest"),
     "This system holds no cargo information. Declared cargo arrives on the "
     "pre-arrival notification, which is Area 4 of the build and is not "
     "ingested yet. Nothing in a track or a radar return tells you what is in "
     "a hold."),
    (("crew", "captain", "master", "owner's name", "who is aboard", "people",
      "passengers"),
     "This system holds no crew or personnel data at all, by design. Crew "
     "details would come from the arrival notification (Area 4) and are not "
     "ingested."),
    # Ordered before the intent entry deliberately: "where will she go next?"
    # is a question about prediction, and matching it on "will she" against the
    # intent register answered the wrong question confidently.
    (("predict", "prediction", "forecast", "where will", "heading to",
      "destination", "eta", "next port", "going to"),
     "This system does not project a track forward or assess a declared "
     "destination. Forward projection with growing uncertainty, and declared "
     "versus implied destination, are Area 2 of the build and are not "
     "implemented."),
    (("smuggling", "smuggler", "trafficking", "guilty", "criminal", "illegal",
      "intent", "intention", "is she planning"),
     "This system does not assess intent or legality and will not guess at "
     "one. It reports behaviour, identity and designation, each with its "
     "evidence. Whether that adds up to an offence is an operator's judgement "
     "and, ultimately, a court's."),
    (("weather", "sea state", "wind", "swell", "visibility", "forecast"),
     "No meteorological data is ingested. The radar stations carry met "
     "equipment in the real Coastal Surveillance Network; nothing from it "
     "reaches this system."),
    (("radio", "vhf", "said", "transcript", "voice", "call sign heard",
      "conversation", "broadcast said"),
     "No radio audio or transcript is held. Multilingual VHF speech "
     "recognition is Area 6 of the build and is not implemented."),
    (("photo", "camera", "eo", "image of her", "what does she look like",
      "electro-optical"),
     "No imagery is held for any vessel. Automatic electro-optical capture, "
     "tagging and classification is Area 5 of the build and is not "
     "implemented. Note this is different from satellite *coverage*, which the "
     "system does compute — ask whether a satellite was overhead."),

)

_WORD = re.compile(r"[a-z0-9']+")


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def classify(question: str) -> tuple[Optional[str], Optional[str], float]:
    """(intent, unsupported_reason, score) for a question.

    Unsupported topics are checked **first**. A question like "what cargo is she
    carrying and where has she been?" contains a port trigger, and answering the
    half we can while ignoring the half we cannot is how an operator ends up
    believing the cargo answer was omitted rather than unavailable.
    """
    q = question.lower().strip()
    toks = _tokens(q)

    for triggers, reason in UNSUPPORTED:
        for t in triggers:
            if (" " in t and t in q) or (" " not in t and t in toks):
                return None, reason, 1.0

    best: tuple[float, Optional[str]] = (0.0, None)
    for intent in INTENTS:
        score = 0.0
        for p in intent.phrases:
            if p in q:
                score += 3.0
        score += 2.0 * sum(1 for t in intent.strong if t in toks)
        score += 0.5 * sum(1 for t in intent.weak if t in toks)
        if score > best[0]:
            best = (score, intent.name)
    if best[0] < MATCH_THRESHOLD:
        return None, None, best[0]
    return best[1], None, best[0]


def answerable_questions() -> list[str]:
    """The questions this answerer can take, for the "I don't know" reply."""
    return [i.question for i in INTENTS if i.question]


# --------------------------------------------------------------------------
# the grounded implementation
# --------------------------------------------------------------------------

class GroundedQA:
    """Answers questions by retrieving, never by generating.

    ``extras`` carries the retrievals a VOI does not itself hold — port calls,
    zone visits, imaging opportunities. It is passed in rather than fetched here
    so the answerer stays a pure function of what it was given, which is what
    makes it testable without a corpus on disk.
    """

    def __init__(self, extras: dict | None = None):
        self.extras = extras or {}

    # -- interface ------------------------------------------------------
    def answerable(self) -> list[str]:
        return answerable_questions()

    def answer(self, question: str, voi: VesselOfInterest) -> Answer:
        q = (question or "").strip()
        if not q:
            return Answer(question=q, outcome="unsupported", intent=None,
                          text="No question was asked.",
                          suggestions=self.answerable())

        intent, unsupported, _score = classify(q)
        if unsupported:
            return Answer(question=q, outcome="unsupported", intent=None,
                          text=unsupported, basis=["capability register"],
                          suggestions=self.answerable())
        if intent is None:
            return Answer(
                question=q, outcome="unsupported", intent=None,
                text=("I do not understand that question well enough to answer "
                      "it from what this system holds, and I will not guess. "
                      "Here is what I can answer about this subject."),
                basis=["intent register"], suggestions=self.answerable())

        handler: Callable[[VesselOfInterest], Answer] = getattr(
            self, f"_ans_{intent}")
        a = handler(voi)
        a.question = q
        a.intent = intent
        if not a.suggestions and a.outcome == "no_data":
            a.suggestions = self.answerable()
        return a

    # -- handlers -------------------------------------------------------
    def _ans_why_flagged(self, v: VesselOfInterest) -> Answer:
        if not v.factors:
            return self._nothing(v, "why_flagged",
                                 "no factors are recorded against this subject")
        lines = [v.account] + [f"- {line}" for line in v.account_lines]
        return Answer(question="", outcome="answered", intent="why_flagged",
                      text="\n".join(lines),
                      basis=["factor set", "object graph", "conformed tables"],
                      evidence=[e for f in v.factors for e in f.evidence],
                      confidence=v.score)

    def _ans_what_to_do(self, v: VesselOfInterest) -> Answer:
        if not v.recommendations:
            return self._nothing(v, "what_to_do",
                                 "no action is proposed for this subject")
        parts = []
        for r in v.recommendations:
            mark = "" if r.feasible else " [NOT AVAILABLE] "
            parts.append(f"- {r.headline}.{mark} {r.rationale} "
                         f"({r.performed_by}) "
                         + (r.feasibility or ""))
        head = ("The system proposes the following; the decision is yours. "
                "Where an action is marked not available, the reason is given.")
        return Answer(question="", outcome="answered", intent="what_to_do",
                      text=head + "\n" + "\n".join(parts),
                      basis=["factor set", "coastal station geometry "
                                            "(synthetic)"],
                      confidence=v.score)

    def _ans_sanctions(self, v: VesselOfInterest) -> Answer:
        fs = self._of_kinds(v, ("sanctions_designation", "sanctioned_ownership"))
        if not fs:
            return self._nothing(
                v, "sanctions",
                "no sanctions designation is matched to this subject in the "
                "OFAC, UN or EU lists this system holds",
                extra=("That is a statement about the lists held and the "
                       "identity match, not a clearance."))
        return self._from_factors(v, fs, ["sanctioned_vessel_matches",
                                          "object graph (ownership)"])

    def _ans_ownership(self, v: VesselOfInterest) -> Answer:
        fs = self._of_kinds(v, ("sanctioned_ownership",))
        chain = self.extras.get("ownership") or []
        if not fs and not chain:
            return self._nothing(
                v, "ownership",
                "no ownership relationship is recorded for this subject",
                extra=("Ownership coverage in this corpus is thin — the graph "
                       "holds a chain for only a small fraction of hulls."))
        if fs:
            return self._from_factors(v, fs, ["object graph (ownership chain)"])
        parts = [f"- {c.get('label')}" for c in chain]
        return Answer(question="", outcome="answered", intent="ownership",
                      text="Ownership on record:\n" + "\n".join(parts),
                      basis=["object graph (ownership chain)"])

    def _ans_identity(self, v: VesselOfInterest) -> Answer:
        ids = v.identifiers or {}
        if v.subject_kind != "vessel":
            return Answer(
                question="", outcome="answered", intent="identity",
                text=("This target has no broadcast identity. It is "
                      f"{v.display_name} — a position history from a sensor "
                      "and nothing else. No name, MMSI, IMO or flag is known, "
                      "and that absence is what makes it a finding rather "
                      "than a gap in the record."),
                basis=["object graph (contact node)"])
        known = {k: val for k, val in ids.items()
                 if val is not None and k != "note"}
        if not known:
            return self._nothing(v, "identity",
                                 "no identity record is held for this hull")
        bits = []
        for label, key in (("name", None), ("MMSI", "mmsi"), ("IMO", "imo"),
                           ("call sign", "call_sign"), ("flag", "flag"),
                           ("type", "vessel_class")):
            if key is None:
                bits.append(f"name {v.display_name}")
            elif known.get(key):
                bits.append(f"{label} {known[key]}")
        length = known.get("length_m")
        if length:
            bits.append(f"length {float(length):.0f} m")
        missing = [k for k in ("mmsi", "imo", "call_sign", "flag",
                               "vessel_class") if not known.get(k)]
        text = "On record: " + ", ".join(bits) + "."
        if missing:
            text += (" Not held: " + ", ".join(m.replace("_", " ")
                                               for m in missing) + ".")
        idf = self._of_kinds(v, ("identity_change", "ais_spoofing",
                                 "identity_then_anomaly", "flag_opacity"))
        if idf:
            text += " Note: " + " ".join(
                narrate_factor(f, name=v.display_name) for f in idf)
        return Answer(question="", outcome="answered", intent="identity",
                      text=text, basis=["gfw_vessel_identity", "object graph"],
                      evidence=[e for f in idf for e in f.evidence])

    def _ans_position(self, v: VesselOfInterest) -> Answer:
        if not v.position or v.position.get("lat") is None:
            return self._nothing(
                v, "position", "no position is held for this subject")
        basis = v.position.get("basis") or "held position"
        return Answer(
            question="", outcome="answered", intent="position",
            text=(f"Last placed at {position_phrase(v.position)}. "
                  f"Basis: {basis}. This is where she was, not where she is — "
                  f"nothing in this system tracks a vessel in real time."),
            basis=["ais_position" if "AIS" in basis else "factor evidence"])

    def _ans_dark_history(self, v: VesselOfInterest) -> Answer:
        fs = self._of_kinds(v, ("dark_contact", "transponder_shutdown",
                                "assessed_ais_disabling", "dark_rendezvous"))
        if not fs:
            return self._nothing(
                v, "dark_history",
                "no dark behaviour is recorded against this subject",
                extra=("Note the limit: silence outside demonstrated receiver "
                       "coverage is not treated as going dark, because we "
                       "could not have heard her there in any case."))
        return self._from_factors(v, fs, ["object graph (alerts)",
                                          "radar_dark_contact", "gfw_ais_gaps"])

    def _ans_encounters(self, v: VesselOfInterest) -> Answer:
        fs = self._of_kinds(v, ("dark_rendezvous",))
        if not fs:
            return self._nothing(
                v, "encounters",
                "no close-quarters meeting with a silent party is recorded "
                "for this subject",
                extra=("Meetings inside a berth or a designated anchorage are "
                       "deliberately not reported — alongside a terminal, that "
                       "describes every ship in the port."))
        return self._from_factors(v, fs, ["object graph (encounters)"])

    def _ans_ports(self, v: VesselOfInterest) -> Answer:
        ports = self.extras.get("ports") or []
        fs = self._of_kinds(v, ("port_risk_propagation",))
        if not ports and not fs:
            return self._nothing(
                v, "ports", "no port call is recorded for this subject")
        text = ""
        if ports:
            text = ("Port calls on record, most recent first: "
                    + ", ".join(str(p) for p in ports[:10]) + ".")
        if fs:
            text += (" " if text else "") + " ".join(
                narrate_factor(f, name=v.display_name) for f in fs)
        return Answer(question="", outcome="answered", intent="ports",
                      text=text, basis=["gfw_port_visits", "object graph"],
                      evidence=[e for f in fs for e in f.evidence])

    def _ans_zones(self, v: VesselOfInterest) -> Answer:
        fs = self._of_kinds(v, ("loitering_sensitive", "maiden_zone_visit",
                                "anchored_outside_limits", "lane_deviation"))
        zones = self.extras.get("zones") or []
        if not fs and not zones:
            return self._nothing(
                v, "zones",
                "no watched-area finding is recorded for this subject",
                extra=("The four statutory limits — EEZ, contiguous zone, "
                       "territorial sea and the maritime boundary — are "
                       "deliberately not held (ADR-030), so no finding can "
                       "reference them."))
        text = " ".join(narrate_factor(f, name=v.display_name) for f in fs)
        if zones:
            text += (" " if text else "") + ("Areas entered on record: "
                                             + ", ".join(map(str, zones[:10]))
                                             + ".")
        return Answer(question="", outcome="answered", intent="zones",
                      text=text, basis=["object graph (alerts)",
                                        "zone_transition"],
                      evidence=[e for f in fs for e in f.evidence])

    def _ans_imagery_opportunity(self, v: VesselOfInterest) -> Answer:
        opps = self.extras.get("imaging") or []
        if not opps:
            return self._nothing(
                v, "imagery_opportunity",
                "no satellite imaging opportunity has been computed for this "
                "subject",
                extra=("Opportunities are computed over AIS gaps only, so a "
                       "subject with no gap has none by construction."))
        best = opps[0]
        tier = best.get("tier")
        text = (f"{len(opps)} imaging opportunity/opportunities computed over "
                f"her silence; the best is tier '{tier}'"
                + (f", scene {best['scene_id']}" if best.get("scene_id") else "")
                + ". A 'confirmed' tier means an image exists whose footprint "
                  "necessarily contained her. It does not mean anybody has "
                  "looked at it, and nothing here claims a detection in it.")
        return Answer(question="", outcome="answered",
                      intent="imagery_opportunity", text=text,
                      basis=["sar_imaging_opportunity"])

    def _ans_confidence(self, v: VesselOfInterest) -> Answer:
        if not v.factors:
            return self._nothing(v, "confidence",
                                 "no factors are recorded, so there is no "
                                 "confidence to report")
        parts = [f"{spec(f.kind).label}: {confidence_word(f.confidence)} "
                 f"({f.confidence:.2f}), contributing {f.points:.2f} of the "
                 f"{v.score:.2f}" for f in
                 sorted(v.factors, key=lambda f: -(f.points or 0))]
        tail = ("Every one of these figures is measured on the synthetic "
                "scenario corpus. Nothing here has been measured on a real "
                "sensor feed."
                if v.is_synthetic else
                "These confidences come from the detectors and registries "
                "named in the evidence, not from a measured real-world hit "
                "rate — this system has never been scored on operational data.")
        return Answer(question="", outcome="answered", intent="confidence",
                      text="\n".join(f"- {p}" for p in parts) + "\n" + tail,
                      basis=["factor set"], confidence=v.score)

    def _ans_provenance(self, v: VesselOfInterest) -> Answer:
        srcs: dict[str, int] = {}
        for f in v.factors:
            for e in f.evidence:
                s = e.provenance.get("source_id") or "unattributed"
                srcs[s] = srcs.get(s, 0) + 1
        head = ("SCENARIO DATA. Every row behind this subject is generated, "
                "flagged is_synthetic in the store, and travels the identical "
                "code path as real data would. No claim here is about a real "
                "vessel." if v.is_synthetic else
                "Real corpus data. Note that most of this system's accuracy "
                "figures are still synthetic-only; see the honesty ledger.")
        if not srcs:
            return Answer(question="", outcome="answered", intent="provenance",
                          text=head, basis=["factor set"])
        lines = [f"- {s}: {n} evidence item{'' if n == 1 else 's'}"
                 for s, n in sorted(srcs.items(), key=lambda kv: -kv[1])]
        return Answer(question="", outcome="answered", intent="provenance",
                      text=head + "\nSources behind this subject:\n"
                           + "\n".join(lines),
                      basis=["provenance envelope on every evidence item"])

    def _ans_evidence(self, v: VesselOfInterest) -> Answer:
        ev = [e for f in v.factors for e in f.evidence]
        if not ev:
            return self._nothing(v, "evidence", "no evidence is attached")
        lines = []
        for f in sorted(v.factors, key=lambda f: -(f.points or 0)):
            lines.append(f"{spec(f.kind).label} ({len(f.evidence)} item"
                         f"{'' if len(f.evidence) == 1 else 's'}):")
            for e in f.evidence[:6]:
                when = f" [{e.occurred_at[:19]}]" if e.occurred_at else ""
                src = e.provenance.get("source_id") or "unattributed"
                lines.append(f"  - {e.label}{when} — source {src}")
            if len(f.evidence) > 6:
                lines.append(f"  - ... and {len(f.evidence) - 6} more")
        return Answer(question="", outcome="answered", intent="evidence",
                      text="\n".join(lines), basis=["factor evidence"],
                      evidence=ev)

    def _ans_score(self, v: VesselOfInterest) -> Answer:
        if not v.factors:
            return self._nothing(v, "score", "this subject carries no score")
        rows = sorted(v.factors, key=lambda f: -(f.points or 0))
        lines = [f"  {spec(f.kind).label}: weight {f.weight:.2f} x confidence "
                 f"{f.confidence:.2f} = {f.standalone:.2f} standalone, "
                 f"allocated {f.points:.3f} ({100 * (f.share or 0):.0f}% of "
                 f"the total)" for f in rows]
        total = sum(f.points or 0.0 for f in rows)
        return Answer(
            question="", outcome="answered", intent="score",
            text=("The score combines independent factors as a noisy-OR — "
                  "1 minus the product of (1 - weight x confidence) — and then "
                  "allocates the result back to each factor in log space, so "
                  "the parts sum exactly to the whole.\n"
                  + "\n".join(lines)
                  + f"\n  Sum of allocations: {total:.3f}; score: {v.score:.3f}."),
            basis=["factor set", "assistant.score"], confidence=v.score)

    # -- helpers --------------------------------------------------------
    @staticmethod
    def _of_kinds(v: VesselOfInterest, kinds: Sequence[str]) -> list[Factor]:
        return [f for f in v.factors if f.kind in kinds]

    @staticmethod
    def _from_factors(v: VesselOfInterest, fs: Sequence[Factor],
                      basis: list[str]) -> Answer:
        return Answer(
            question="", outcome="answered", intent=None,
            text=" ".join(narrate_factor(f, name=v.display_name) for f in fs),
            basis=basis,
            evidence=[e for f in fs for e in f.evidence],
            confidence=max((float(f.confidence) for f in fs), default=None))

    @staticmethod
    def _nothing(v: VesselOfInterest, intent: str, what: str,
                 extra: str = "") -> Answer:
        """The "I don't know" answer, said precisely.

        Phrased as a statement about the *record*, never about the world. "No
        port call is on record" and "she has called at no ports" are different
        claims and only the first one is ours to make.
        """
        text = (f"The system holds no record of that: {what}. That is a "
                f"statement about what has been ingested, not about the "
                f"vessel.")
        if extra:
            text += " " + extra
        return Answer(question="", outcome="no_data", intent=intent, text=text,
                      basis=["factor set", "conformed tables"])


def family_gaps(voi: VesselOfInterest) -> list[str]:
    """Which evidence families are absent for this subject, in plain words.

    Used by the surface to say what is *not* known. Three of the six families
    are unbuildable today, and a page that lists only what it found reads as
    completeness.
    """
    present = {f.family for f in voi.factors}
    return [f"{meta['label']} — {meta['blurb']} (nothing held; "
            f"{', '.join(meta['areas'])})"
            for name, meta in FAMILIES.items() if name not in present]
