"""Measure what the pipeline did against what the scenarios actually were.

**This is the only module allowed to read `scenario_truth`, and it runs after
the pipeline has finished.** Detection, fusion, graph, scoring and alerting code
may never touch it — a test greps those paths and fails the build if any of them
does. A detector with the answer key measures nothing.

Three outcomes are counted, and they are counted separately because they mean
different things:

  **DETECTED** — a true anomaly whose entities drew at least one alert inside
  its window. Good.

  **MISSED** — a true anomaly that drew nothing. This is *tuning information*,
  not a defect. The instruction for this session is to measure first and tune as
  a separate decision, so a miss is reported and left alone.

  **FALSE POSITIVE** — a decoy that drew an alert. This is the number that
  matters most, because ADR-004 makes precision a product policy with a figure
  attached: alert fatigue destroys analyst trust before accuracy problems do.

Deliberate misses are scored separately again. They are *expected* not to fire,
so a quiet result there is a pass, and firing on one is a failure of a different
kind — it means the system claimed a capability it does not have.

**Attribution of an alert to a scenario is by entity and time window.** An alert
on a vessel inside a scenario's window counts for that scenario. A vessel in two
scenarios at once would make this ambiguous, which is one more reason the
generator refuses to put one there.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..ingest.landing import read_table
from .truth import (DECOY, DELIBERATE_MISS, TABLE as TRUTH_TABLE, TRUE_ANOMALY)


@dataclass
class ScenarioOutcome:
    scenario_id: str
    family: str
    truth_class: str
    expected_detection: bool
    expected_types: list
    fired_types: list = field(default_factory=list)
    n_alerts: int = 0
    notes: str = ""
    capability_boundary: str = ""

    @property
    def detected(self) -> bool:
        return self.n_alerts > 0

    @property
    def outcome(self) -> str:
        if self.truth_class == TRUE_ANOMALY:
            return "DETECTED" if self.detected else "MISSED"
        if self.truth_class == DECOY:
            return "FALSE_POSITIVE" if self.detected else "correctly quiet"
        return "FIRED (should not have)" if self.detected else "correctly silent"

    @property
    def type_match(self) -> bool:
        """Did the *expected* detector fire, or did something else?

        Distinguishing these matters: a true anomaly caught by the wrong rule is
        a weaker result than one caught by the rule designed for it, and
        collapsing both into "detected" would hide that.
        """
        if not self.expected_types:
            return self.detected
        return bool(set(self.expected_types) & set(self.fired_types))


def _ts(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, datetime):
        return (v if v.tzinfo else v.replace(tzinfo=timezone.utc)).timestamp()
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def load_truth() -> list[dict]:
    return read_table(TRUTH_TABLE)


def alias_map() -> dict[str, str]:
    """Every graph node id that means "this scenario vessel", mapped to it.

    **The pipeline uses two vessel keyspaces and they do not join.** The landed
    graph populator keys hulls by `vessel:gfw:<vessel_id>`; the Phase 2/5 path
    resolves an AIS track through `graph.identity.resolve_mmsi`, which mints
    `vessel:mmsi:<mmsi>`. An alert raised by the anomaly library therefore
    lands on a node the truth table has never heard of, and the first
    measurement run scored 0 of 23 true anomalies entirely because of it.

    That is a real defect in the codebase — the same class as ADR-015, where
    two modules computed different keys and every join between them silently
    returned nothing — and it is reported as a finding rather than papered over.
    This map exists so the *measurement* can still be taken while the defect
    stands; it is built from the landed identity table, which is data, and it
    is used only after the pipeline has finished. No detector sees it.

    A vessel that changed MMSI mid-window (B1's phoenix, B5's clone) has
    several aliases, and all of them map back to the one hull.
    """
    out: dict[str, str] = {}
    for r in read_table("gfw_vessel_identity"):
        if not r.get("is_synthetic"):
            continue
        vid = r.get("vessel_id")
        if not vid:
            continue
        out[vid] = vid
        out[f"vessel:gfw:{vid}"] = vid
        mmsi = r.get("mmsi")
        if mmsi not in (None, ""):
            out[f"vessel:mmsi:{int(mmsi)}"] = vid
    return out


def measure(store, *, slack_hours: float = 36.0) -> list[ScenarioOutcome]:
    """Score every scenario against the alerts the pipeline actually raised.

    `slack_hours` widens each scenario's window on both sides. A detector that
    fires on a port call the morning after a transfer is responding to that
    transfer; requiring the alert timestamp to fall exactly inside the window
    would score a correct detection as a miss purely on rounding.
    """
    truth_rows = load_truth()
    aliases = alias_map()
    # Read ALL alerts, not only the ones the store believes are synthetic:
    # whether an alert's subject node got flagged depends on which code path
    # created it, and that is exactly the seam being measured. The alias map
    # decides attribution; the flag is reported separately.
    alerts = store.alerts()

    # entity -> [(t, anomaly_type)]
    by_entity: dict[str, list] = {}

    def attribute(node_id, ts, atype) -> None:
        if not node_id:
            return
        eid = aliases.get(node_id, node_id)
        by_entity.setdefault(eid, []).append((ts, atype))

    for a in alerts:
        attribute(a["subject"], a["ts"], a.get("anomaly_type"))
        # Alerts whose subject is a detection or an alert id carry the vessel in
        # props; attribute those too rather than dropping them.
        for key in ("vessel", "counterpart", "subject_vessel"):
            attribute((a.get("props") or {}).get(key), a["ts"],
                      a.get("anomaly_type"))

    out: list[ScenarioOutcome] = []
    slack = slack_hours * 3600.0
    for row in truth_rows:
        entities = [e for e in str(row.get("entity_ids") or "").split(",") if e]
        t0 = (_ts(row.get("t_start")) or 0.0) - slack
        t1 = (_ts(row.get("t_end")) or 0.0) + slack
        fired, n = [], 0
        for e in entities:
            for ts, atype in by_entity.get(e, []):
                if t0 <= ts <= t1:
                    n += 1
                    if atype:
                        fired.append(atype)
        out.append(ScenarioOutcome(
            scenario_id=row["scenario_id"],
            family=row.get("scenario_family", "?"),
            truth_class=row.get("truth_class", "?"),
            expected_detection=bool(row.get("expected_detection")),
            expected_types=[x for x in str(
                row.get("expected_anomaly_types") or "").split(",") if x],
            fired_types=sorted(set(fired)),
            n_alerts=n,
            notes=row.get("notes", ""),
            capability_boundary=row.get("capability_boundary", ""),
        ))
    return sorted(out, key=lambda o: o.scenario_id)


def unattributed(store, outcomes: list[ScenarioOutcome]) -> dict:
    """Alerts that landed on no scenario at all.

    **Background traffic is, by construction, unremarkable** — twelve merchants
    on real lanes and a fishing fleet working a ground. An alert on one of them
    is a false positive with no decoy attached, and the scenario-level precision
    figure cannot see it, because there is no truth row to score against.

    Reporting it separately matters: a run can post 100% scenario precision
    while firing dozens of times on ordinary traffic, and an analyst opening the
    queue sees the dozens. This is the number that would decide whether the
    queue is usable.
    """
    aliases = alias_map()
    scored: set[str] = set()
    truth_entities: set[str] = set()
    for row in load_truth():
        for e in str(row.get("entity_ids") or "").split(","):
            if e:
                truth_entities.add(e)

    per_type: dict[str, int] = {}
    per_vessel: dict[str, int] = {}
    total = 0
    for a in store.alerts():
        eid = aliases.get(a["subject"], a["subject"])
        if eid in truth_entities:
            continue
        total += 1
        atype = a.get("anomaly_type") or "?"
        per_type[atype] = per_type.get(atype, 0) + 1
        per_vessel[eid] = per_vessel.get(eid, 0) + 1
    return dict(total=total, by_type=per_type,
                n_vessels=len(per_vessel),
                worst=sorted(per_vessel.items(), key=lambda kv: -kv[1])[:5])


def summarise(outcomes: list[ScenarioOutcome]) -> dict:
    """Precision and recall overall and by family.

    **Precision is computed over scenarios, not over alerts**, and the
    difference is worth stating: one decoy that draws forty alerts is one false
    positive here, but forty interruptions to an analyst. The alert-level count
    is reported alongside so neither view is missing.
    """
    tp = [o for o in outcomes if o.truth_class == TRUE_ANOMALY and o.detected]
    fn = [o for o in outcomes if o.truth_class == TRUE_ANOMALY and not o.detected]
    fp = [o for o in outcomes if o.truth_class == DECOY and o.detected]
    tn = [o for o in outcomes if o.truth_class == DECOY and not o.detected]
    misses = [o for o in outcomes if o.truth_class == DELIBERATE_MISS]

    def ratio(a, b):
        return (a / b) if b else None

    by_family: dict[str, dict] = {}
    for o in outcomes:
        d = by_family.setdefault(o.family, dict(tp=0, fn=0, fp=0, tn=0,
                                                miss_ok=0, miss_fired=0))
        if o.truth_class == TRUE_ANOMALY:
            d["tp" if o.detected else "fn"] += 1
        elif o.truth_class == DECOY:
            d["fp" if o.detected else "tn"] += 1
        else:
            d["miss_fired" if o.detected else "miss_ok"] += 1
    for d in by_family.values():
        d["precision"] = ratio(d["tp"], d["tp"] + d["fp"])
        d["recall"] = ratio(d["tp"], d["tp"] + d["fn"])

    return dict(
        n_scenarios=len(outcomes),
        true_anomalies=len(tp) + len(fn),
        detected=len(tp), missed=len(fn),
        decoys=len(fp) + len(tn),
        false_positives=len(fp), correctly_quiet=len(tn),
        deliberate_misses=len(misses),
        deliberate_misses_correctly_silent=sum(1 for o in misses
                                               if not o.detected),
        precision=ratio(len(tp), len(tp) + len(fp)),
        recall=ratio(len(tp), len(tp) + len(fn)),
        alert_level_false_positives=sum(o.n_alerts for o in fp),
        detected_by_expected_rule=sum(1 for o in tp if o.type_match),
        by_family=by_family,
    )


def format_measurement(outcomes: list[ScenarioOutcome], store=None) -> str:
    s = summarise(outcomes)
    lines = ["=" * 76,
             "scenario detection results (measured against scenario_truth)",
             "=" * 76]

    def pct(v):
        return f"{v:.0%}" if v is not None else "n/a"

    lines.append(f"true anomalies    : {s['true_anomalies']:>3}   "
                 f"DETECTED {s['detected']}   MISSED {s['missed']}")
    lines.append(f"decoys            : {s['decoys']:>3}   "
                 f"FALSE POSITIVE {s['false_positives']}   "
                 f"correctly quiet {s['correctly_quiet']}")
    lines.append(f"deliberate misses : {s['deliberate_misses']:>3}   "
                 f"correctly silent "
                 f"{s['deliberate_misses_correctly_silent']}")
    lines.append("")
    lines.append(f"precision {pct(s['precision'])}    recall {pct(s['recall'])}"
                 f"    (ADR-004 target: precision >= 70%)")
    lines.append(f"of {s['detected']} detections, "
                 f"{s['detected_by_expected_rule']} came from the rule the "
                 f"scenario expected")
    lines.append(f"alert-level false positives: "
                 f"{s['alert_level_false_positives']} "
                 f"(an analyst sees alerts, not scenarios)")

    lines.append("")
    lines.append(f"{'family':<26}{'P':>7}{'R':>7}{'TP':>5}{'FN':>5}"
                 f"{'FP':>5}{'TN':>5}")
    for fam in sorted(s["by_family"]):
        d = s["by_family"][fam]
        lines.append(f"{fam:<26}{pct(d['precision']):>7}{pct(d['recall']):>7}"
                     f"{d['tp']:>5}{d['fn']:>5}{d['fp']:>5}{d['tn']:>5}")

    lines.append("")
    lines.append(f"{'id':<7}{'class':<17}{'outcome':<26}{'alerts':>7}  fired")
    for o in outcomes:
        lines.append(f"{o.scenario_id:<7}{o.truth_class:<17}"
                     f"{o.outcome:<26}{o.n_alerts:>7}  "
                     f"{','.join(o.fired_types) or '-'}")

    boundaries = [o for o in outcomes if o.capability_boundary]
    if boundaries:
        lines.append("")
        lines.append("capability boundaries, with numbers attached:")
        for o in boundaries:
            lines.append(f"  {o.scenario_id}: {o.capability_boundary}")

    if store is not None:
        u = unattributed(store, outcomes)
        lines.append("")
        lines.append("alerts on entities with NO truth row "
                     "(background traffic — nothing to find there):")
        lines.append(f"  {u['total']} alert(s) across {u['n_vessels']} vessel(s)")
        for atype, n in sorted(u["by_type"].items(), key=lambda kv: -kv[1]):
            lines.append(f"    {atype:<26}{n:>6}")
        if u["worst"]:
            lines.append("  worst offenders: " + ", ".join(
                f"{v.split(':')[-1]} x{n}" for v, n in u["worst"]))
        lines.append("  Scenario-level precision cannot see these: there is no "
                     "decoy to score them against. An analyst would still open "
                     "every one.")

    lines.append("")
    lines.append("These are SYNTHETIC-SUITE numbers. They say nothing about "
                 "precision on real feeds, which must be re-measured on the "
                 "deploy host and will be lower (CLAUDE.md §4.6).")
    return "\n".join(lines)
