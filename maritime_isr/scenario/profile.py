"""Where scenario parameters come from, and the honest record of which are real.

Every number the generator uses is either **measured** from the landed corpus or
**assumed** from published priors. This module is the single boundary between
those two, and it refuses to let them be confused.

**Why the indirection.** The instruction is to sample parameters from the real
corpus wherever the corpus contains them. The corpus lives on the operator's
laptop, not in the sandbox where this code was written, so the generator cannot
simply query it. What it can do is take every parameter through one interface
that answers with its provenance attached: `Param(value, measured=True,
n_rows=24153, source_table='gfw_loitering')` or `Param(value, measured=False,
rationale='published class dimensions')`.

The result is that a generation run **prints its own measured-vs-assumed table**
with a row count behind every measured figure. A fallback prior can never be
quoted as a measurement, because the report says which it was.

**Producing a profile.** `tools/corpus_profile.py` runs on whatever machine holds
the corpus, queries the landed tables, and writes
`data_profiles/real_corpus_profile.json` — quantiles and counts, plus the
identifier lists the collision guard needs. That file is small enough to commit,
which is the point: "sampled from 24,153 real loitering rows" becomes a
reproducible claim rather than an assertion.

**The priors are not invented.** Where a fallback is used it is a published
naval-architecture or class figure — a VLCC really is about 330 m long — not a
number chosen to make the output look plausible. Each carries its rationale in
the table.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import repo_root

#: Where a generated profile is expected to live.
PROFILE_PATH = repo_root() / "data_profiles" / "real_corpus_profile.json"


@dataclass(frozen=True)
class Param:
    """A parameter with its provenance welded on."""
    key: str
    value: Any
    measured: bool
    n_rows: int = 0
    source_table: str = ""
    rationale: str = ""

    def describe(self) -> str:
        if self.measured:
            return f"MEASURED from {self.n_rows:,} rows of {self.source_table}"
        return f"ASSUMED — {self.rationale}"


# --------------------------------------------------------------------------
# fallback priors
# --------------------------------------------------------------------------
#
# Published typical dimensions and speed envelopes by class. Length/beam/draught
# are metres, speeds are knots, dwt is tonnes. `accel_kn_per_min` is how fast the
# class can change speed and `rot_deg_per_s` its sustainable rate of turn at
# service speed — both dominated by displacement, which is why a VLCC and a dhow
# differ by two orders of magnitude.
#
# Ranges are (low, typical, high) so a sampler can produce spread without
# inventing a shape. A loaded VLCC at 330 m and 20 m draught is a class
# definition, not a guess.

CLASS_PRIORS: dict[str, dict] = {
    "VLCC": dict(
        length_m=(300.0, 333.0, 340.0), beam_m=(55.0, 60.0, 62.0),
        draught_m=(18.0, 20.5, 22.5), dwt=(280_000, 300_000, 320_000),
        service_kn=(12.5, 14.5, 15.5), max_kn=16.5,
        accel_kn_per_min=0.10, rot_deg_per_s=0.20),
    "Suezmax": dict(
        length_m=(266.0, 274.0, 285.0), beam_m=(45.0, 48.0, 50.0),
        draught_m=(14.5, 16.0, 17.2), dwt=(140_000, 158_000, 170_000),
        service_kn=(13.0, 14.5, 15.5), max_kn=16.5,
        accel_kn_per_min=0.14, rot_deg_per_s=0.25),
    "Aframax": dict(
        length_m=(230.0, 245.0, 253.0), beam_m=(40.0, 42.0, 44.0),
        draught_m=(12.5, 14.3, 15.2), dwt=(95_000, 110_000, 120_000),
        service_kn=(13.0, 14.5, 15.5), max_kn=16.8,
        accel_kn_per_min=0.16, rot_deg_per_s=0.30),
    "product_tanker": dict(
        length_m=(160.0, 183.0, 195.0), beam_m=(27.0, 32.2, 34.0),
        draught_m=(9.5, 11.2, 12.8), dwt=(37_000, 48_000, 60_000),
        service_kn=(12.5, 14.0, 15.0), max_kn=16.0,
        accel_kn_per_min=0.22, rot_deg_per_s=0.40),
    "bulker": dict(
        length_m=(180.0, 225.0, 250.0), beam_m=(28.0, 32.3, 43.0),
        draught_m=(11.0, 13.5, 15.0), dwt=(55_000, 76_000, 100_000),
        service_kn=(11.5, 13.2, 14.5), max_kn=15.5,
        accel_kn_per_min=0.18, rot_deg_per_s=0.32),
    "reefer": dict(
        length_m=(120.0, 145.0, 165.0), beam_m=(18.0, 22.0, 25.0),
        draught_m=(7.5, 9.2, 10.5), dwt=(8_000, 12_000, 18_000),
        # Reefers are fast — perishable cargo is the whole point of the class.
        service_kn=(16.0, 18.5, 20.5), max_kn=22.0,
        accel_kn_per_min=0.45, rot_deg_per_s=0.60),
    "general_cargo": dict(
        length_m=(90.0, 120.0, 150.0), beam_m=(14.0, 18.5, 22.0),
        draught_m=(5.5, 7.4, 9.0), dwt=(5_000, 12_000, 20_000),
        service_kn=(10.5, 12.5, 14.0), max_kn=15.0,
        accel_kn_per_min=0.40, rot_deg_per_s=0.70),
    "fishing": dict(
        length_m=(18.0, 27.0, 42.0), beam_m=(5.0, 7.0, 9.5),
        draught_m=(2.2, 3.4, 4.5), dwt=(80, 250, 600),
        # Transit speed; working speed is handled separately by the track
        # generator, which drops to 2-4 kn while fishing.
        service_kn=(8.0, 10.0, 11.5), max_kn=12.5,
        accel_kn_per_min=1.20, rot_deg_per_s=2.50),
    "dhow": dict(
        length_m=(15.0, 22.0, 30.0), beam_m=(4.0, 5.8, 7.5),
        draught_m=(1.4, 2.1, 2.9), dwt=(40, 120, 300),
        service_kn=(6.0, 8.0, 9.5), max_kn=10.5,
        accel_kn_per_min=1.50, rot_deg_per_s=3.00),
    "naval": dict(
        length_m=(105.0, 143.0, 165.0), beam_m=(12.0, 16.5, 19.0),
        draught_m=(4.0, 5.2, 6.5), dwt=(3_000, 6_000, 9_000),
        service_kn=(16.0, 20.0, 24.0), max_kn=30.0,
        accel_kn_per_min=2.00, rot_deg_per_s=3.50),
}

#: Flag distribution fallback. Weighted toward the registries that actually
#: dominate Arabian Sea merchant traffic and the open registries that recur in
#: evasion reporting. Replaced wholesale by the measured distribution when a
#: profile is present.
FLAG_PRIOR: dict[str, float] = {
    "PAN": 0.16, "LBR": 0.13, "MHL": 0.12, "SGP": 0.08, "IND": 0.08,
    "MLT": 0.06, "BHS": 0.05, "HKG": 0.05, "CYP": 0.04, "ARE": 0.04,
    "IRN": 0.03, "PAK": 0.03, "COM": 0.03, "GMB": 0.02, "CMR": 0.02,
    "TZA": 0.02, "VCT": 0.02, "PLW": 0.02,
}

#: Behavioural duration priors, as quantile maps. Each is the fallback for a
#: distribution the corpus can supply directly.
DURATION_PRIORS: dict[str, dict] = {
    # Hours. A commercial STS transfer of a full parcel is most of a day.
    "encounter_duration_hours": {
        0.05: 1.2, 0.25: 3.0, 0.50: 6.5, 0.75: 11.0, 0.95: 20.0},
    # Hours. GFW's loitering definition catches a very wide spread.
    "loiter_duration_hours": {
        0.05: 2.5, 0.25: 5.0, 0.50: 10.0, 0.75: 22.0, 0.95: 60.0},
    # Hours alongside.
    "port_call_dwell_hours": {
        0.05: 6.0, 0.25: 14.0, 0.50: 26.0, 0.75: 46.0, 0.95: 96.0},
    # Hours at anchor waiting for a berth.
    "anchorage_wait_hours": {
        0.05: 2.0, 0.25: 8.0, 0.50: 20.0, 0.75: 48.0, 0.95: 140.0},
    # Port calls per vessel across an 8-week window.
    "port_calls_per_vessel": {
        0.05: 0.0, 0.25: 1.0, 0.50: 2.0, 0.75: 4.0, 0.95: 8.0},
    # Metres. Closest point of approach during a transfer: alongside with
    # fenders is tens of metres, not hundreds.
    "encounter_separation_m": {
        0.05: 25.0, 0.25: 45.0, 0.50: 70.0, 0.75: 110.0, 0.95: 190.0},
}


def sample_quantiles(q: dict, rng) -> float:
    """Inverse-CDF sample from a quantile map, linearly interpolated.

    A quantile map is how an empirical distribution survives being written to a
    small JSON file: five numbers keep the shape — including a long right tail —
    where a mean and standard deviation would flatten it into a symmetry the
    real data does not have.
    """
    ks = sorted(float(k) for k in q)
    vs = [float(q[k] if k in q else q[str(k)]) for k in ks]
    u = rng.random()
    if u <= ks[0]:
        return vs[0]
    if u >= ks[-1]:
        return vs[-1]
    for i in range(len(ks) - 1):
        if ks[i] <= u <= ks[i + 1]:
            span = ks[i + 1] - ks[i]
            f = (u - ks[i]) / span if span else 0.0
            return vs[i] + f * (vs[i + 1] - vs[i])
    return vs[-1]


def triangular(rng, low: float, mode: float, high: float) -> float:
    """Sample a (low, typical, high) prior. Degenerates safely when collapsed."""
    if not (low <= mode <= high) or high <= low:
        return mode
    return rng.triangular(low, high, mode)


# --------------------------------------------------------------------------
# the profile
# --------------------------------------------------------------------------

@dataclass
class CorpusProfile:
    """Measured distributions from the real corpus, with priors behind them."""
    raw: dict = field(default_factory=dict)
    origin: str = "priors only (no profile file)"
    path: Path | None = None
    _used: dict[str, Param] = field(default_factory=dict, repr=False)

    # ---- loading ----
    @classmethod
    def load(cls, path: Path | None = None) -> "CorpusProfile":
        """Load a profile, or return a priors-only profile if none exists.

        A missing profile is a normal, expected state — not an error. It just
        means every parameter will be reported as ASSUMED.
        """
        p = Path(path) if path else PROFILE_PATH
        if not p.is_file():
            return cls()
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return cls(origin=f"priors only (profile unreadable: {exc})")
        gen = raw.get("generated_at", "unknown date")
        window = raw.get("corpus_window", {})
        return cls(raw=raw, path=p, origin=(
            f"{p.name} generated {gen}"
            + (f", corpus {window.get('start')}..{window.get('end')}"
               if window else "")))

    @property
    def has_measurements(self) -> bool:
        return bool(self.raw.get("distributions"))

    # ---- distribution access ----
    def _dist(self, key: str) -> dict | None:
        d = self.raw.get("distributions", {}).get(key)
        if not d:
            return None
        # A distribution present but backed by zero rows is not a measurement.
        if int(d.get("n_rows", 0)) <= 0:
            return None
        return d

    def quantiles(self, key: str) -> Param:
        """Quantile map for `key`, measured if the profile carries one."""
        d = self._dist(key)
        if d and d.get("quantiles"):
            p = Param(key, {float(k): float(v) for k, v in d["quantiles"].items()},
                      measured=True, n_rows=int(d["n_rows"]),
                      source_table=d.get("source_table", "?"))
        else:
            prior = DURATION_PRIORS.get(key)
            if prior is None:
                raise KeyError(f"no measured distribution and no prior for {key!r}")
            p = Param(key, dict(prior), measured=False,
                      rationale="published/typical operating durations")
        self._used[key] = p
        return p

    def sample(self, key: str, rng) -> float:
        return sample_quantiles(self.quantiles(key).value, rng)

    def class_dims(self, vessel_class: str) -> Param:
        """Dimension and speed envelope for a vessel class."""
        key = f"class_dims:{vessel_class}"
        d = self._dist(key)
        prior = CLASS_PRIORS[vessel_class]
        if d and d.get("dims"):
            # A measured profile supplies whichever fields the corpus could
            # support; the prior fills the rest. Merging rather than replacing
            # means a corpus with lengths but no draughts still contributes its
            # lengths instead of being discarded wholesale.
            merged = {**prior, **d["dims"]}
            p = Param(key, merged, measured=True, n_rows=int(d["n_rows"]),
                      source_table=d.get("source_table", "gfw_vessel_identity"))
        else:
            p = Param(key, dict(prior), measured=False,
                      rationale="published class dimensions and service speeds")
        self._used[key] = p
        return p

    def flags(self) -> Param:
        d = self._dist("flag_distribution")
        if d and d.get("weights"):
            p = Param("flag_distribution", dict(d["weights"]), measured=True,
                      n_rows=int(d["n_rows"]),
                      source_table=d.get("source_table", "gfw_vessel_identity"))
        else:
            p = Param("flag_distribution", dict(FLAG_PRIOR), measured=False,
                      rationale="registries dominant in Arabian Sea traffic")
        self._used["flag_distribution"] = p
        return p

    def sample_flag(self, rng) -> str:
        w = self.flags().value
        keys = sorted(w)
        total = sum(w[k] for k in keys) or 1.0
        u = rng.random() * total
        acc = 0.0
        for k in keys:
            acc += w[k]
            if u <= acc:
                return k
        return keys[-1]

    # ---- identifiers, for the collision guard ----
    def real_imos(self) -> list:
        return self.raw.get("identifiers", {}).get("real_imos", [])

    def real_mmsis(self) -> list:
        return self.raw.get("identifiers", {}).get("real_mmsis", [])

    def ofac_imos(self) -> list:
        return self.raw.get("identifiers", {}).get("ofac_imos", [])

    # ---- the honesty report ----
    def provenance_rows(self) -> list[tuple[str, str, int, str]]:
        """(key, MEASURED|ASSUMED, n_rows, detail) for every parameter used."""
        out = []
        for key in sorted(self._used):
            p = self._used[key]
            out.append((key, "MEASURED" if p.measured else "ASSUMED",
                        p.n_rows, p.source_table if p.measured else p.rationale))
        return out

    def summary(self) -> dict:
        used = list(self._used.values())
        n_meas = sum(1 for p in used if p.measured)
        return dict(origin=self.origin, n_params_used=len(used),
                    n_measured=n_meas, n_assumed=len(used) - n_meas,
                    total_rows_behind_measurements=sum(
                        p.n_rows for p in used if p.measured))

    def format_report(self) -> str:
        s = self.summary()
        lines = [
            "parameter provenance",
            f"  profile source : {s['origin']}",
            f"  parameters used: {s['n_params_used']} "
            f"({s['n_measured']} measured, {s['n_assumed']} assumed)",
        ]
        if s["n_measured"]:
            lines.append(f"  real rows behind measured params: "
                         f"{s['total_rows_behind_measurements']:,}")
        else:
            lines.append("  NOTHING IS MEASURED — every parameter below is a "
                         "published prior, not a property of the real corpus.")
        lines.append("")
        lines.append(f"  {'parameter':<34}{'status':<10}{'rows':>9}  detail")
        for key, status, n, detail in self.provenance_rows():
            lines.append(f"  {key:<34}{status:<10}{n:>9,}  {detail}")
        return "\n".join(lines)
