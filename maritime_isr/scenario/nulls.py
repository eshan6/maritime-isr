"""Match the real corpus's null rates, so synthetic rows are not separable.

**The bug this exists to fix.** The generator populated every field it could.
The real corpus does not: `gfw_encounters.imo` is **100% null**,
`gfw_vessel_identity.length_m` and `tonnage_gt` are **98.6% null**, `imo` there
is **74.8% null**, `call_sign` **55.3%**. So `WHERE imo IS NOT NULL` was a
perfect synthetic-row detector, and every precision figure measured on a
combined corpus would have been measuring that filter rather than the system.

The track-level separability test passed the whole time. It compared report
cadence, position noise and speed — and the distinction leaked through the
*columns* instead. That is the "null-that-looks-populated" failure family from
STATE.md, arrived at from the other direction.

**How the masking works.** For every field where the profile carries a real null
rate, each synthetic row is nulled with that probability. The draw is
**deterministic** — a hash of (table, field, natural key) — so masking does not
consume the generator's RNG stream and a re-run at the same seed produces the
same nulls.

**Required fields are the exception, and they are declared, not assumed.** Some
scenarios genuinely need a field present: B2's whole premise is that detection
must rest on the physical fingerprint, which needs `length_m`; B1, B2 and B4
turn on the IMO. Those vessels keep those fields, and everything else is masked
at the measured rate. The required set is small enough (a handful of hulls out
of 114) that it does not visibly shift the marginal — and `verify_rates` checks
exactly that rather than trusting the argument.

**What is deliberately NOT matched: H3.** The real event tables carry only
`h3_r7` and `h3_r9`, because they were landed before ADR-015. Matching that
would mean degrading synthetic rows to reproduce a known defect. The right fix
is on the real side — `tools/restamp_h3.py` recomputes the missing resolutions
from lat/lon — so synthetic rows keep all five and the discrepancy is recorded
as work to do, not copied.
"""
from __future__ import annotations

import hashlib

#: (table, field) -> the entity ids that MUST keep this field populated,
#: because a scenario's detectability depends on it. Anything not listed is
#: masked at the measured rate.
REQUIRED: dict[tuple[str, str], set[str]] = {
    # B2's detection "must rest on the physical fingerprint alone" — without a
    # length there is no fingerprint and the scenario cannot be scored.
    ("gfw_vessel_identity", "length_m"): {
        "vessel:identity_break", "vessel:spine", "vessel:receiver_alpha",
        "vessel:dhow_sub_floor",
    },
    ("gfw_vessel_identity", "tonnage_gt"): {
        "vessel:identity_break", "vessel:spine",
    },
    # The identity-manipulation group turns on the hull number: B1 keeps it
    # across a phoenix, B2 replaces it, B4 wears a dead hull's. A masked IMO
    # would delete the thing being tested.
    ("gfw_vessel_identity", "imo"): {
        "vessel:spine", "vessel:identity_break", "vessel:zombie",
        "vessel:brazen", "vessel:converge_b", "vessel:clone_real",
        "vessel:clone_ghost", "vessel:voyage_flag",
    },
    # ADR-018's call-sign tier is policy set before it can fire; keeping a few
    # populated is what lets it fire at all.
    ("gfw_vessel_identity", "call_sign"): {
        "vessel:spine", "vessel:brazen", "vessel:identity_break",
    },
}

#: Fields never masked regardless of the measured rate — masking them would
#: break the row's identity or its provenance envelope.
NEVER_MASK = {
    "vessel_id", "event_id", "start_time", "end_time", "valid_from", "valid_to",
    "lat", "lon", "source_id", "source_ref", "acquired_at", "ingested_at",
    "pipeline_version", "is_synthetic", "mmsi", "record_kind", "event_kind",
    "ship_name", "flag",
    # ---- port-visit structure -------------------------------------------
    # These are not independent columns; they are one fact recorded in seven
    # places, and the real mapper derives them all from the same anchorage
    # records. Masking each at its own measured rate would hit the marginal
    # rates and produce rows that are jointly impossible — a dwell with no
    # observed stop, a port name with no anchorage it came from — and anything
    # that trusts the relationship would then break on synthetic data alone.
    # `land.apply_visit_structure` emits them coherently at the measured class
    # mix instead, and `validate.RULE_VISIT_STRUCTURE` checks both the
    # coherence and the mix, which is strictly more than masking could give.
    # `port_id` and `port_name` are here for the same reason: they come from
    # the stop anchorage and their nullity is decided by whether there was one.
    "port_id", "port_name", "dwell_hours",
}

#: Prefixes covered by the same exemption. The anchorage records are seven
#: columns each and adding one must not quietly reopen the hole.
NEVER_MASK_PREFIXES = ("visit_", "start_anchorage_", "end_anchorage_",
                       "anchorage_")


def _draw(table: str, field: str, key: str) -> float:
    """Deterministic uniform in [0,1) for this (table, field, row).

    Hashed rather than drawn from the generator's RNG so masking cannot shift
    the rest of the corpus: adding a field to the mask must not renumber every
    vessel or move every rendezvous.
    """
    h = hashlib.sha256(f"{table}|{field}|{key}".encode()).digest()
    return int.from_bytes(h[:8], "big") / float(1 << 64)


class NullMask:
    """Applies the real corpus's null rates to generated rows."""

    def __init__(self, profile, *, min_rate: float = 0.02):
        self.profile = profile
        self.min_rate = min_rate
        #: (table, field) -> (n_seen, n_nulled), for the verification report.
        self.applied: dict[tuple[str, str], list[int]] = {}

    def rate(self, table: str, field: str) -> float | None:
        if field in NEVER_MASK or field.startswith(NEVER_MASK_PREFIXES):
            return None
        r = self.profile.null_rate(table, field)
        if r is None or r < self.min_rate:
            return None
        return r

    def apply(self, row: dict, *, table: str, key: str) -> dict:
        """Null fields in `row` at the real corpus's measured rates, in place."""
        for field in list(row):
            r = self.rate(table, field)
            if r is None or row.get(field) is None:
                continue
            required = REQUIRED.get((table, field), set())
            stat = self.applied.setdefault((table, field), [0, 0])
            stat[0] += 1
            if key in required:
                continue
            if _draw(table, field, key) < r:
                row[field] = None
                stat[1] += 1
        return row

    # ---- reporting ----
    def report_rows(self) -> list[tuple[str, str, int, float, float]]:
        """(table, field, n, achieved_rate, target_rate), sorted."""
        out = []
        for (table, field), (n, nulled) in sorted(self.applied.items()):
            target = self.profile.null_rate(table, field) or 0.0
            out.append((table, field, n, (nulled / n) if n else 0.0, target))
        return out

    def format_report(self) -> str:
        rows = self.report_rows()
        if not rows:
            return ("null-rate matching: no profile null rates available — "
                    "synthetic rows populate every field and are separable "
                    "from real rows by a single IS NOT NULL filter.")
        lines = ["null-rate matching (synthetic rows masked to the real rates)",
                 f"  {'table':<26}{'field':<18}{'rows':>7}"
                 f"{'achieved':>10}{'real':>9}"]
        for table, field, n, got, target in rows:
            lines.append(f"  {table:<26}{field:<18}{n:>7}"
                         f"{got:>10.3f}{target:>9.3f}")
        return "\n".join(lines)

    def verify(self, *, tolerance: float = 0.12) -> list[str]:
        """Fields whose achieved rate misses the real one. Empty is good.

        The tolerance is generous because the required-field exemptions pull
        the achieved rate down on small populations — the check is that the
        exemptions have not swallowed the mask, not that the rates match to
        three decimals.
        """
        bad = []
        for table, field, n, got, target in self.report_rows():
            if n < 20:
                continue                 # too few rows to judge a rate
            if abs(got - target) > tolerance:
                bad.append(
                    f"{table}.{field}: masked {got:.3f} of rows but the real "
                    f"corpus is {target:.3f} null over {n} synthetic row(s) — "
                    f"a filter on this column would separate the two")
        return bad
