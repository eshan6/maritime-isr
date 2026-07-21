"""xView3-SAR adapter (roadmap 1.2/1.3): loads xView3 label CSVs into the
harness's truth format, so the deploy host evaluates against the canonical
dark-vessel benchmark with **exactly the same matching and metric code**
as the sandbox synthetic suite. One harness, two suites — a number from
either is comparable release over release.

xView3 label columns used (public schema): detect_lat, detect_lon,
vessel_length_m, is_vessel, is_fishing, scene_id, confidence.
We evaluate vessel detection: rows with is_vessel == True and label
confidence HIGH/MEDIUM form the truth set (LOW-confidence labels are
excluded from both truth and FP-counting inside an exclusion radius —
the benchmark's own convention, honored so our F1 is comparable to
published baselines).
"""
from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

EXCLUSION_RADIUS_M = 200.0


def load_labels(csv_path: str | Path) -> dict[str, dict[str, list[dict]]]:
    """Returns {scene_id: {"truth": [...], "exclusion": [...]}}."""
    scenes: dict[str, dict[str, list[dict]]] = defaultdict(
        lambda: {"truth": [], "exclusion": []})
    with open(csv_path, newline="") as f:
        for row in csv.DictReader(f):
            sid = row["scene_id"]
            rec = {"lat": float(row["detect_lat"]),
                   "lon": float(row["detect_lon"]),
                   "length_m": (float(row["vessel_length_m"])
                                if row.get("vessel_length_m") else None)}
            is_vessel = str(row.get("is_vessel", "")).lower() in ("true", "1")
            conf = str(row.get("confidence", "")).upper()
            if is_vessel and conf in ("HIGH", "MEDIUM"):
                scenes[sid]["truth"].append(rec)
            else:
                scenes[sid]["exclusion"].append(rec)
    return dict(scenes)


def filter_excluded(pred: list[dict], exclusion: list[dict],
                    radius_m: float = EXCLUSION_RADIUS_M) -> list[dict]:
    """Drop predictions that fall on low-confidence/non-vessel labels so
    they are neither TPs nor FPs — the benchmark convention."""
    from .harness import _dist_m
    out = []
    for p in pred:
        if any(_dist_m(p["lat"], p["lon"], e["lat"], e["lon"]) <= radius_m
               for e in exclusion):
            continue
        out.append(p)
    return out
