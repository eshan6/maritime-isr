"""Synthetic organizational world for Phase 4 — ownership, sanctions, and
registry history, engineered so the graph's acceptance criteria are
testable with exact truth:

  1-hop chain   Redwater Marine LLC (OFAC-listed) owns 419500001
                (rendezvous #1 participant) AND 419100002 (the dark
                merchant) → rendezvous alert on 419500000, dark-gap alert
                on 419100002.
  2-hop chain   Almadina Shipping FZE owns 419500003 (rendezvous #2);
                Almadina owned-by Karadeniz Holdings; Karadeniz OFAC-listed
                → alert on 419500002 through two ownership hops.
  cycle         Nautic Shell A ⇄ Nautic Shell B own each other (shell-loop,
                as real registries contain); Shell A owns 419500005
                (rendezvous #3). NO sanctions — the traversal must
                terminate and stay silent. Robustness + negative in one.
  identity      registry v2 (day 15) vs v1 (day 0): 419100004's hull is
                renamed AND reflagged; hull IMO 9500009 swaps MMSI
                419100009 → 419700099. Both must surface as
                identity_changed events with closed identified-as edges.
  as-of         Karadeniz's listing starts 2026-04-15 (before everything);
                a decoy listing on "Gulf Blue Lines" is valid ONLY to
                2026-05-01 — expired before the window; must never alert.

Deterministic, stdlib-only, no RNG (this world is scripted, not sampled).
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

DATA = Path(sys.argv[1] if len(sys.argv) > 1 else "data")


def ep(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp()


MERCH = {419100000 + i: (9500000 + i) for i in range(10)}
FISH = {419200000 + i: (9510000 + i) for i in range(5)}
OTHER = {419400001: 9520001, 419400002: 9520002, 419300001: 9530001,
         **{419500000 + i: 9540000 + i for i in range(6)},
         419600001: 9550001, 419600002: 9550002,
         419600003: 9550003, 419600004: 9550004}
ALL_IMO = {**MERCH, **FISH, **OTHER}

NAMES = {419100002: "MV SEPTEMBER TIDE", 419100004: "MV COPPER STAR",
         419100009: "MV KHOR AMAYA", 419400001: "FV LONG HAUL",
         419500001: "FV RED DAWN", 419500003: "FV ALMADINA QUEEN",
         419500005: "FV SHELL GAME"}

registry_len = json.loads((DATA / "synthetic_registry.json").read_text())


def vessel_row(mmsi, name=None, flag="PA"):
    return dict(imo=ALL_IMO[mmsi], mmsi=mmsi,
                name=name or NAMES.get(mmsi, f"MV SYN {mmsi % 1000:03d}"),
                flag=flag, length_m=registry_len.get(str(mmsi)))


v1 = dict(as_of="2026-06-15", as_of_epoch=ep("2026-06-15T00:00:00"),
          vessels=[vessel_row(m, flag=("IN" if m in FISH else "PA"))
                   for m in ALL_IMO])

v2_vessels = []
for m in ALL_IMO:
    row = vessel_row(m, flag=("IN" if m in FISH else "PA"))
    if m == 419100004:                       # rename + reflag, same hull
        row["name"] = "MV GOLDEN DAWN"
        row["flag"] = "KM"
    if m == 419100009:                       # hull swaps its MMSI
        row["mmsi"] = 419700099
    v2_vessels.append(row)
v2 = dict(as_of="2026-06-30", as_of_epoch=ep("2026-06-30T00:00:00"),
          vessels=v2_vessels)

ownership = dict(
    organizations=[
        dict(name="Redwater Marine LLC", jurisdiction="AE"),
        dict(name="Almadina Shipping FZE", jurisdiction="AE",
             parent="Karadeniz Holdings", confidence=0.8),
        dict(name="Karadeniz Holdings", jurisdiction="TR"),
        dict(name="Nautic Shell A", jurisdiction="MH",
             parent="Nautic Shell B", confidence=0.7),
        dict(name="Nautic Shell B", jurisdiction="MH",
             parent="Nautic Shell A", confidence=0.7),   # deliberate cycle
        dict(name="Gulf Blue Lines", jurisdiction="IN"),
        dict(name="Porbandar Fisheries Co-op", jurisdiction="IN"),
    ],
    vessel_owners=(
        [dict(mmsi=419500001, org="Redwater Marine LLC", confidence=0.85),
         dict(mmsi=419100002, org="Redwater Marine LLC", confidence=0.85),
         dict(mmsi=419500003, org="Almadina Shipping FZE", confidence=0.8),
         dict(mmsi=419500005, org="Nautic Shell A", confidence=0.7)] +
        [dict(mmsi=m, org="Gulf Blue Lines") for m in MERCH
         if m != 419100002] +
        [dict(mmsi=m, org="Porbandar Fisheries Co-op") for m in FISH] +
        [dict(mmsi=m, org="Gulf Blue Lines") for m in
         (419400001, 419400002, 419500000, 419500002, 419500004,
          419600001, 419600002, 419600003, 419600004)]))

sanctions = [
    dict(registry="OFAC", entry_id="SDN-77001", name="Redwater Marine LLC",
         entry_type="entity", program="IRAN-EO13902",
         valid_from_epoch=ep("2026-05-01T00:00:00"), valid_to_epoch=None),
    dict(registry="OFAC", entry_id="SDN-77002", name="Karadeniz Holdings",
         entry_type="entity", program="RUSSIA-EO14024",
         valid_from_epoch=ep("2026-04-15T00:00:00"), valid_to_epoch=None),
    # decoy: delisted BEFORE the observation window — must never alert
    dict(registry="OFAC", entry_id="SDN-66000", name="Gulf Blue Lines",
         entry_type="entity", program="VENEZUELA-EO13850",
         valid_from_epoch=ep("2025-01-01T00:00:00"),
         valid_to_epoch=ep("2026-05-01T00:00:00")),
]

expected_alerts = [
    dict(rule="sanctioned_owner_rendezvous", subject_mmsi=419500000,
         via="Redwater Marine LLC", hops=1),
    dict(rule="sanctioned_owner_rendezvous", subject_mmsi=419500002,
         via="Karadeniz Holdings", hops=2),
    dict(rule="sanctioned_owner_dark_gap", subject_mmsi=419100002,
         via="Redwater Marine LLC", hops=1),
]

(DATA / "synthetic_registry_v1.json").write_text(json.dumps(v1, indent=1))
(DATA / "synthetic_registry_v2.json").write_text(json.dumps(v2, indent=1))
(DATA / "synthetic_ownership.json").write_text(json.dumps(ownership, indent=1))
(DATA / "synthetic_sanctions_phase4.json").write_text(json.dumps(sanctions, indent=1))
(DATA / "synthetic_graph_truth_phase4.json").write_text(
    json.dumps(dict(expected_alerts=expected_alerts,
                    expected_identity_events=3,   # name, flag, mmsi
                    cycle_orgs=["Nautic Shell A", "Nautic Shell B"]), indent=1))
print(f"org world: {len(ownership['organizations'])} orgs, "
      f"{len(ownership['vessel_owners'])} ownerships, "
      f"{len(sanctions)} sanctions entries (1 expired decoy), "
      f"{len(v1['vessels'])} registry rows ×2 snapshots, "
      f"{len(expected_alerts)} expected alerts")
