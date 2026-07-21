"""Phase 6 — build the product surface.

Runs the full platform, serializes the operational-picture snapshot, and
assembles the self-contained product-surface HTML (map, replay, alert
queue, entity pages, risk board, reports). This is the laptop-rotation
demo: one file, opens in any browser, runs against the live synthetic
backend with no rehearsed data.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from maritime_isr.product.snapshot import build_snapshot

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
TOOLS = ROOT / "tools"

RCOL = {"anomaly_history": "#1a5fb4", "sanction_proximity": "#a13636",
        "flag_opacity": "#9a7d1a", "fingerprint_deviation": "#2f7d5b"}
TYPE_TAG = {"dark_vessel": "red", "dark_candidate": "red", "ais_spoofing": "amber",
            "dark_rendezvous": "amber", "sanctioned_owner_rendezvous": "red",
            "sanctioned_owner_dark_gap": "red", "loitering_sensitive": "amber",
            "port_risk_propagation": "blue", "identity_then_anomaly": "red"}


def main():
    print("building operational snapshot (running Phases 2-5)...")
    snap = build_snapshot(DATA)
    (DATA / "phase6_snapshot.json").write_text(json.dumps(snap))
    s = snap["stats"]
    print(f"  tracks={s['tracks']} dark={s['dark_candidates']} "
          f"alerts={s['alerts']} vessels={s['vessels']} "
          f"entities={len(snap['entities'])} frames={len(snap['frames'])}")

    shell = (TOOLS / "phase6_shell.html").read_text()
    app_js = (TOOLS / "phase6_app.js").read_text()
    html = (shell
            .replace("__SNAP__", json.dumps(snap))
            .replace("__RCOL__", json.dumps(RCOL))
            .replace("__TYPETAG__", json.dumps(TYPE_TAG))
            .replace("__JS__", app_js))
    out = DATA / "phase6_product_surface.html"
    out.write_text(html)
    print(f"product surface -> {out} ({len(html) // 1024} KB)")


if __name__ == "__main__":
    main()
