"""Phase 4 end-to-end exercise — the graph turns on.

  AIS feed → tracks (Phase 2) → fusion (Phase 3)      [in memory]
  registry v1+v2 fold → identity events
  ownership + sanctions → org edges
  tracks/encounters/fusion → entities, evidence edges, EVENTS
  event engine → alerts with evidence chains
  live acceptance checks: entity coverage, canonical chain (organic +
  synthetic inject), cycle survival, MIGRATION TEST (zero recompute)
  → eval vs expected alerts → ledger → dashboard snapshot

Everything SYNTHETIC, as ever: the rendezvous are organic in the sense
that the track engine derived them from the simulated feed; ownership and
sanctions are scripted files standing in for corporate/OFAC registries.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from maritime_isr import fusion, graph, tracks as trk
from maritime_isr.config import AOI_V1, DATA_ROOT, GRAPH_DB_NAME
from maritime_isr.connectors import ais as ais_conn, satais
from maritime_isr.eval.graph import evaluate_graph, record_to_ledger

DATA = Path(__file__).resolve().parent.parent / "data"

# ---- 1. upstream chain, in memory (identical to the Phase 3 runner) ------
payload = (DATA / "synthetic_ais_30d.nmea").read_bytes()
parser = ais_conn.AivdmParser("multi", aoi=AOI_V1)
messages = []
for line in payload.decode().splitlines():
    ts_s, rx, sentence = line.split("\t")
    parser.receiver = rx
    m = parser.feed(sentence, datetime.fromisoformat(ts_s))
    if m and m["msg_type"] != 5:
        messages.append(m)
pos = ais_conn.conform(messages, source="ais_synthetic:multi",
                       source_ref="p4").to_pandas()
sched = trk.SatPassSchedule(satais.parse_pass_predictions(
    (DATA / "synthetic_sat_passes.json").read_bytes()))
eng = trk.run_track_engine(pos, source_ref="p4", sat_schedule=sched,
                           partition_day="p4", aoi=AOI_V1.name,
                           write_outputs=False)
scenes = json.loads((DATA / "synthetic_scenes_phase3.json").read_text())
registry = {int(k): v for k, v in json.loads(
    (DATA / "synthetic_registry.json").read_text()).items()}
fus = fusion.run_fusion(scenes, eng["tracks"], eng["coverage_model"], registry,
                        gaps=eng["gaps"], spoof_events=eng["spoof_events"],
                        source_ref="p4", partition_day="p4",
                        aoi=AOI_V1.name, write_outputs=False)
print(f"upstream: {len(eng['tracks'])} tracks, "
      f"{len(fus['associations'])} associations, "
      f"{sum(1 for v in fus['verdicts'] if v['status']=='dark_candidate')} dark candidates")

# ---- 2. build the graph ---------------------------------------------------
db = DATA_ROOT / GRAPH_DB_NAME
if db.exists():
    db.unlink()
g = graph.GraphStore(db)
graph.ensure_world(g)

id_events = []
for snap_file in ("synthetic_registry_v1.json", "synthetic_registry_v2.json"):
    snap = json.loads((DATA / snap_file).read_text())
    id_events += graph.fold_registry_snapshot(g, snap, source_ref=snap_file)
print(f"registry folded: {len(id_events)} identity-change events "
      f"({[e['field'] for e in id_events]})")

own = json.loads((DATA / "synthetic_ownership.json").read_text())
graph.ingest_ownership(g, own, source_ref="synthetic_ownership.json",
                       as_of=pd.Timestamp("2026-06-15", tz="UTC").timestamp())
graph.ingest_sanctions(g, json.loads(
    (DATA / "synthetic_sanctions_phase4.json").read_text()),
    source_ref="synthetic_sanctions_phase4.json")
graph.ingest_tracks(g, eng["tracks"], source_ref="p4")
graph.ingest_encounters(g, eng["encounters"], source_ref="p4")
graph.ingest_fusion(g, fus["associations"], fus["verdicts"], source_ref="p4")
print(f"graph: {g.n_nodes()} nodes ({g.n_nodes('vessel')} vessels, "
      f"{g.n_nodes('organization')} orgs), {g.n_edges()} edge assertions")

# ---- 3. the event engine --------------------------------------------------
acct = graph.process_events(g)
alerts = g.alerts()
print(f"events processed: {acct['events_processed']}, "
      f"alerts fired: {len(alerts)}")
for a in alerts:
    m = graph.current_mmsi(g, a["subject"], a["ts"])
    print(f"  [{a['rule']}] {a['subject']} "
          f"(mmsi {m}) conf={a['confidence']:.2f} "
          f"chain: " + " -> ".join(
              f"{c['edge']}" for c in a["evidence"]))

# ---- 4. live acceptance checks -------------------------------------------
# (a) entity coverage: every AIS-active vessel is an entity w/ track history
missing = []
for tr in eng["tracks"]:
    vid = graph.resolve_mmsi(g, tr.mmsi, at=tr.points["ts"].min().timestamp())
    if not any(e.dst == f"track:{tr.track_id}"
               for e in g.edges(vid, "resolved-from")):
        missing.append(tr.track_id)
entity_coverage = 1.0 - len(missing) / max(len(eng["tracks"]), 1)
print(f"\nentity coverage: {entity_coverage:.0%} of tracks attached "
      f"({len(missing)} missing)")

# (b) synthetic inject: fabricate a rendezvous with a sanctioned owner and
# confirm the canonical chain fires on demand (roadmap 4.5)
g.upsert_node("vessel:imo:9999901", "vessel", dict(mmsi=999000001))
g.upsert_node("vessel:imo:9999902", "vessel", dict(mmsi=999000002))
t_inj = pd.Timestamp("2026-07-01", tz="UTC").timestamp()
g.add_edge("owned-by", "vessel:imo:9999902", "org:Redwater Marine LLC",
           t_start=t_inj, t_end=None, confidence=0.9, observed_at=t_inj,
           source="inject", source_ref="synthetic_inject")
g.add_edge("met-with", "vessel:imo:9999901", "vessel:imo:9999902",
           t_start=t_inj, t_end=t_inj + 1800, confidence=0.95,
           observed_at=t_inj, source="inject", source_ref="synthetic_inject",
           props=dict(encounter_id="INJ1"))
g.emit("met_with", "vessel:imo:9999901", t_inj,
       dict(counterpart="vessel:imo:9999902", encounter_id="INJ1"))
inj = graph.process_events(g)
inject_fired = len(inj["alerts_fired"]) >= 1
print(f"synthetic inject chain fired: {inject_fired}")

# (c) migration test: add an edge type at runtime, zero recompute
n_before = g.n_edges()
sum_before = g.edges_checksum()
v_new = g.migrate_add_edge_type(
    "insured-by", dict(src=["vessel"], dst=["organization"],
                       half_life_days=180.0, kind="state"))
g.upsert_node("org:Neptune P&I Club", "organization",
              dict(name="Neptune P&I Club"))
g.add_edge("insured-by", "vessel:imo:9500001", "org:Neptune P&I Club",
           t_start=t_inj, t_end=None, confidence=0.8, observed_at=t_inj,
           source="migration_test", source_ref="p4")
import sqlite3
con = sqlite3.connect(str(db))
import hashlib
h = hashlib.sha256()
for row in con.execute("SELECT * FROM edges WHERE rowid<=? ORDER BY rowid",
                       (n_before,)):
    h.update(repr(row).encode())
migration_pass = (h.hexdigest() == sum_before
                  and g.n_edges() == n_before + 1
                  and g.ontology_version() == v_new)
print(f"migration test (add 'insured-by', v{v_new}): "
      f"{'PASS — zero recompute, prior rows byte-identical' if migration_pass else 'FAIL'}")

# ---- 5. eval vs expected, ledger ------------------------------------------
truth = json.loads((DATA / "synthetic_graph_truth_phase4.json").read_text())
r = evaluate_graph(g, alerts, truth, id_events,
                   entity_coverage=entity_coverage,
                   migration_pass=migration_pass, inject_fired=inject_fired)
print(f"\nalert precision: {r.alert_precision:.0%}  recall: {r.alert_recall:.0%} "
      f"(expected {r.n_expected}, fired {r.n_fired})")
print(f"identity events: {r.n_identity_events}/{truth['expected_identity_events']} "
      f"  cycle survived: {r.cycle_survived}")
record_to_ledger(r)
print("ledger row appended (suite=phase4_graph_synthetic)")

# ---- 6. dashboard snapshot ------------------------------------------------
def neighborhood(vid, depth=2):
    seen, out = {vid}, []
    frontier = [vid]
    for _ in range(depth):
        nxt = []
        for n in frontier:
            for d in ("out", "in"):
                for e in g.edges(n, direction=d):
                    out.append(dict(t=e.edge_type, s=e.src, d=e.dst,
                                    c=round(g.edge_confidence(e), 2),
                                    closed=e.t_end is not None))
                    other = e.dst if d == "out" else e.src
                    if other not in seen:
                        seen.add(other)
                        nxt.append(other)
        frontier = nxt
    dedup = {(x["t"], x["s"], x["d"]): x for x in out}
    return list(dedup.values())

subjects = sorted({a["subject"] for a in alerts})
snap = dict(
    generated_at=datetime.now(timezone.utc).isoformat(),
    stats=dict(nodes=g.n_nodes(), vessels=g.n_nodes("vessel"),
               orgs=g.n_nodes("organization"), edges=g.n_edges(),
               ontology_version=g.ontology_version()),
    alerts=[dict(rule=a["rule"], subject=a["subject"],
                 mmsi=g.node(a["subject"])["props"].get("mmsi"),
                 conf=round(a["confidence"], 2), ts=a["ts"],
                 chain=a["evidence"]) for a in alerts],
    identity_events=id_events,
    neighborhoods={s: neighborhood(s) for s in subjects},
    metrics=dict(alert_precision=r.alert_precision,
                 alert_recall=r.alert_recall,
                 entity_coverage=entity_coverage,
                 migration_pass=migration_pass,
                 inject_fired=inject_fired,
                 n_identity_events=r.n_identity_events),
)
(DATA / "phase4_snapshot.json").write_text(json.dumps(snap))
print(f"snapshot -> {DATA / 'phase4_snapshot.json'}")
g.close()
