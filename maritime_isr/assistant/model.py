"""The objects the MDA assistant serves: Evidence, Factor, Recommendation,
VesselOfInterest.

**One object sits at the centre of this system: a ranked Vessel of Interest,
carrying its reasons.** Everything else in the Section-3 build brief feeds it.
These dataclasses are that object, and their shape encodes three rules that are
not negotiable anywhere in this project:

  * **No naked assertion.** Every :class:`Factor` carries a confidence and the
    :class:`Evidence` it rests on; every :class:`Evidence` carries the full
    provenance envelope of the row it came from (CLAUDE.md §4.1/§4.3). A factor
    that cannot name its evidence cannot be constructed — the constructor
    refuses it.
  * **The score decomposes to the factor.** A composite that an operator cannot
    take apart is worthless to them, so :class:`VesselOfInterest` never carries
    a number without the per-factor allocation that sums to it (see
    :mod:`.score`).
  * **Synthetic stays visible on the surface, not merely in the database**
    (ADR-019, and the Section-3 brief's standing caution). Every object here
    carries ``is_synthetic`` and it propagates upward: a VOI is synthetic if any
    factor on it is.

**A "Vessel" of Interest need not be a vessel.** Measured on seed 7, 52 of 55
alerts in this system land on a ``contact:`` or ``detection:`` node rather than
a hull — a target nobody can name, which is precisely what makes it a finding.
So the subject of this object is whatever the system can reason about, and
:attr:`VesselOfInterest.subject_kind` says which of the two it is. Forcing every
row to be a named hull would have discarded the dark-vessel path entirely, which
is the one capability the Coast Guard requirement most needs.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from ..config import PIPELINE_VERSION

__all__ = ["Evidence", "Factor", "Recommendation", "VesselOfInterest",
           "Suppression", "iso", "stable_id"]


def iso(epoch: float | None) -> Optional[str]:
    """Unix seconds -> ISO-8601 UTC, or None. The API emits nothing else."""
    if epoch is None:
        return None
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).isoformat()


def stable_id(prefix: str, *parts: Any) -> str:
    """A deterministic id for a derived object.

    Deterministic because these ids travel to the UI, into an exported incident
    report and back in a follow-up question. A random id would change on every
    rebuild and every one of those references would rot.
    """
    body = "|".join("" if p is None else str(p) for p in parts)
    return f"{prefix}_{hashlib.sha1(body.encode()).hexdigest()[:12]}"


@dataclass(frozen=True)
class Evidence:
    """One retrievable thing that supports a factor.

    ``ref`` is a pointer the surface can follow — an alert id, a zone id, a
    scene id, a track id. The Section-3 brief names three evidence types
    explicitly (tracks, imagery, radio transcripts); ``kind`` is open beyond
    those because the registries and the graph produce evidence too, and a
    closed list would have to be edited by every future area.
    """
    kind: str
    label: str
    ref: Optional[str] = None
    occurred_at: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    confidence: Optional[float] = None
    detail: dict = field(default_factory=dict)
    #: source / source_ref / acquired_at / ingested_at / pipeline_version.
    provenance: dict = field(default_factory=dict)
    is_synthetic: bool = False

    def as_dict(self) -> dict:
        # Attribution is added on the way out rather than at every construction
        # site. There are a dozen places that build an Evidence and one place
        # that serialises it, so this is the only point where "every evidence
        # item names a source an operator could go and check" can be a
        # guarantee rather than a convention twelve callers have to remember.
        from .attribution import describe
        return {
            "kind": self.kind, "label": self.label, "ref": self.ref,
            "occurred_at": self.occurred_at, "lat": self.lat, "lon": self.lon,
            "confidence": self.confidence, "detail": self.detail,
            "provenance": describe(self.provenance),
            "is_synthetic": self.is_synthetic,
        }


@dataclass
class Factor:
    """One named reason a subject is on the list.

    A factor is the unit the whole product is built from: it is what the score
    decomposes into, what the plain-language account narrates, what a
    recommendation is tied back to, and what a follow-up question is answered
    from. Its ``kind`` must be registered in :mod:`.catalog`, which is where the
    weight, the family and the narration live — so adding a new kind of
    suspicion is an edit in one place rather than five.

    ``points`` and ``share`` are filled in by :func:`.score.score_factors` after
    the whole set is known; they are None on a factor considered alone.
    """
    kind: str
    subject_id: str
    headline: str
    confidence: float
    evidence: list[Evidence]
    occurred_at: Optional[str] = None
    detail: dict = field(default_factory=dict)
    #: Set by the catalog at construction; carried so a consumer never has to
    #: re-look-up the registry to render a row.
    family: str = ""
    area: str = ""
    weight: float = 0.0
    #: Filled by :func:`.score.score_factors`.
    points: Optional[float] = None
    share: Optional[float] = None
    standalone: Optional[float] = None
    is_synthetic: bool = False
    factor_id: str = ""

    def __post_init__(self) -> None:
        if not self.evidence:
            raise ValueError(
                f"factor {self.kind!r} on {self.subject_id!r} has no evidence. "
                "A factor without evidence is a naked assertion and this "
                "product's entire thesis is that an operator can trace every "
                "point of a score back to something retrievable (CLAUDE.md "
                "§4.1/§4.3).")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError(
                f"factor {self.kind!r} confidence {self.confidence!r} is "
                "outside [0,1]")
        if not self.factor_id:
            self.factor_id = stable_id("fct", self.kind, self.subject_id,
                                       self.occurred_at)
        if any(e.is_synthetic for e in self.evidence):
            self.is_synthetic = True

    def as_dict(self) -> dict:
        return {
            "factor_id": self.factor_id, "kind": self.kind,
            "family": self.family, "area": self.area,
            "subject_id": self.subject_id, "headline": self.headline,
            "confidence": round(float(self.confidence), 4),
            "weight": round(float(self.weight), 4),
            "standalone": None if self.standalone is None
                          else round(self.standalone, 4),
            "points": None if self.points is None else round(self.points, 4),
            "share": None if self.share is None else round(self.share, 4),
            "occurred_at": self.occurred_at,
            "detail": self.detail,
            "n_evidence": len(self.evidence),
            "evidence": [e.as_dict() for e in self.evidence],
            "is_synthetic": self.is_synthetic,
        }


@dataclass(frozen=True)
class Recommendation:
    """What the assistant proposes the watchkeeper do next, and why.

    **The half that makes this an assistant rather than a report**, and the half
    that is easiest to fake. Three fields exist to stop it being faked:

      * ``because_factors`` ties the proposal to the factors that motivated it,
        so the reasoning is inspectable rather than oracular.
      * ``performed_by`` says whether the system can carry the action out or is
        instructing a human. Almost everything here is the latter today.
      * ``feasible`` / ``feasibility`` is *computed*, not asserted — "call her on
        VHF" is not advice if she is 300 km beyond the nearest station, and a
        recommendation engine that cannot tell the difference will burn an
        operator's trust on its second use.
    """
    action: str
    headline: str
    rationale: str
    because_factors: list[str]
    priority: int
    performed_by: str                 # "operator" | "system"
    feasible: bool = True
    feasibility: str = ""
    #: What the system itself can do towards this today. Honest by requirement:
    #: several of these actions are named in areas of the brief that are not
    #: built, and saying so is the point (CLAUDE.md §5).
    system_capability: str = ""
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "action": self.action, "headline": self.headline,
            "rationale": self.rationale,
            "because_factors": list(self.because_factors),
            "priority": self.priority, "performed_by": self.performed_by,
            "feasible": self.feasible, "feasibility": self.feasibility,
            "system_capability": self.system_capability,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class Suppression:
    """A subject that carried a signal and was deliberately kept off the list.

    The radar cascade already established that "why is this NOT flagged" has to
    be answerable from the product rather than only from a terminal (ADR-028).
    The same applies here and more so, because this list is the surface an
    operator forms their model of the system from: a queue that silently drops
    things is one an analyst cannot calibrate against.
    """
    subject_id: str
    reason: str
    explanation: str
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"subject_id": self.subject_id, "reason": self.reason,
                "explanation": self.explanation, "detail": self.detail}


@dataclass
class VesselOfInterest:
    """A ranked subject with its reasons, its evidence and what to do next."""
    subject_id: str
    subject_kind: str                 # "vessel" | "contact"
    display_name: str
    score: float
    factors: list[Factor]
    recommendations: list[Recommendation] = field(default_factory=list)
    identifiers: dict = field(default_factory=dict)
    position: dict = field(default_factory=dict)
    account: str = ""
    account_lines: list[str] = field(default_factory=list)
    as_of: Optional[str] = None
    is_synthetic: bool = False
    rank: Optional[int] = None

    @property
    def families(self) -> list[str]:
        seen: list[str] = []
        for f in self.factors:
            if f.family not in seen:
                seen.append(f.family)
        return seen

    def as_dict(self, *, with_evidence: bool = True) -> dict:
        out = {
            "subject_id": self.subject_id,
            "subject_kind": self.subject_kind,
            "display_name": self.display_name,
            "rank": self.rank,
            "score": round(float(self.score), 4),
            "as_of": self.as_of,
            "identifiers": self.identifiers,
            "position": self.position,
            "account": self.account,
            "account_lines": list(self.account_lines),
            "families": self.families,
            "n_factors": len(self.factors),
            "factors": [f.as_dict() for f in self.factors],
            "recommendations": [r.as_dict() for r in self.recommendations],
            "is_synthetic": self.is_synthetic,
            "pipeline_version": PIPELINE_VERSION,
        }
        if not with_evidence:
            for f in out["factors"]:
                f.pop("evidence", None)
        return out
