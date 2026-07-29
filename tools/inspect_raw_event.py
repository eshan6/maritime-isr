"""Show the real shape of a landed GFW event, so mapping is fixed from data.

The connectors land raw API responses immutably under data/raw/, precisely so a
mapping bug can be diagnosed without re-downloading anything. This prints the
structure of one event per kind: top-level keys, the vessel block, and any field
that looks like an identifier.

    python tools/inspect_raw_event.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from maritime_isr.config import cfg  # noqa: E402

RAW = cfg.data_root / "raw" / "gfw-events"


def summarise(value, depth: int = 0) -> str:
    pad = "  " * depth
    if isinstance(value, dict):
        if depth >= 2:
            return f"{{{', '.join(list(value)[:8])}}}"
        lines = []
        for k, v in list(value.items())[:25]:
            lines.append(f"{pad}  {k}: {summarise(v, depth + 1)}")
        return "\n" + "\n".join(lines)
    if isinstance(value, list):
        if not value:
            return "[] (empty)"
        return f"[{len(value)} items] first -> {summarise(value[0], depth + 1)}"
    text = repr(value)
    return text if len(text) <= 90 else text[:87] + "..."


def main() -> int:
    if not RAW.exists():
        print(f"No raw events found at {RAW}")
        print("Run `python -m maritime_isr.cli ingest gfw-events --weeks 8` first.")
        return 1

    files = sorted(RAW.rglob("*.json"))
    if not files:
        print(f"No .json files under {RAW}")
        return 1

    seen: set[str] = set()
    for path in files:
        kind = path.name.split("_")[0]
        if kind in seen:
            continue
        try:
            events = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            print(f"{path.name}: unreadable ({e})")
            continue
        if not isinstance(events, list) or not events:
            print(f"\n=== {kind}: {len(events) if isinstance(events, list) else '?'} events ===")
            continue
        seen.add(kind)

        ev = events[0]
        print("\n" + "=" * 78)
        print(f"{kind.upper()}  —  {len(events):,} events in {path.name}")
        print("=" * 78)
        print("top-level keys:", ", ".join(sorted(ev)) if isinstance(ev, dict) else type(ev))
        print("\nstructure:", summarise(ev))

        # Anything that might be the vessel identifier, with its exact length.
        print("\nid-shaped fields:")
        def walk(o, prefix=""):
            if isinstance(o, dict):
                for k, v in o.items():
                    p = f"{prefix}.{k}" if prefix else k
                    if isinstance(v, (dict, list)):
                        walk(v, p)
                    elif isinstance(v, str) and ("id" in k.lower() or "ssvid" in k.lower()):
                        print(f"  {p:<40} len={len(v):<4} {v[:60]!r}")
            elif isinstance(o, list) and o:
                walk(o[0], f"{prefix}[0]")
        walk(ev)

    print("\n" + "=" * 78)
    print("A GFW vessel id is a UUID: 8-4-4-4-12 hex characters, 36 total.")
    print("Anything else in a vessel_id field is the mapping bug.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
