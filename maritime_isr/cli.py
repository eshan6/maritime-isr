"""maritime-isr CLI. Thin dispatcher so the spec's exit-test commands exist.

    maritime-isr ingest s1   --days 90
    maritime-isr ingest ais  --hours 72
    maritime-isr ingest gfw
    maritime-isr ingest registries
    maritime-isr config
"""
from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    parser = argparse.ArgumentParser(prog="maritime-isr")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ing = sub.add_parser("ingest", help="run a source connector")
    ing_sub = p_ing.add_subparsers(dest="source", required=True)

    p_s1 = ing_sub.add_parser("s1")
    p_s1.add_argument("--days", type=int, default=90)
    p_s1.add_argument("--catalog-only", action="store_true")

    p_ais = ing_sub.add_parser("ais")
    p_ais.add_argument("--hours", type=float, default=None,
                       help="stop after N hours (default: run forever as a service)")

    ing_sub.add_parser("gfw")
    ing_sub.add_parser("registries")
    ing_sub.add_parser("noaa").add_argument("--month", required=True, help="YYYY-MM")

    sub.add_parser("config", help="print resolved config")

    args = parser.parse_args(argv)

    if args.cmd == "config":
        from .config import _main
        return _main()

    if args.cmd == "ingest":
        if args.source == "s1":
            from .ingest.copernicus import run
            return run(days=args.days, catalog_only=args.catalog_only)
        if args.source == "ais":
            from .ingest.aisstream import run
            return run(max_hours=args.hours)
        if args.source == "gfw":
            from .ingest.gfw import run
            return run()
        if args.source == "registries":
            from .ingest.registries import run
            return run()
        if args.source == "noaa":
            from .ingest.noaa_ais import run
            return run(month=args.month)
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    sys.exit(main())
