"""Maritime ISR — fusion intelligence for the Arabian Sea / Indian west-coast EEZ.

Two build lineages share this package:
  - the live-data pipeline (execution-spec units 0.0-0.5: ingest/, process/,
    infra/, storage via store.py/db.py/writer.py) — runs on the deploy host
    with real credentials;
  - the synthetic prototype (roadmap Phases 1-6: detect/, tracks/, fusion/,
    graph/, anomaly/, product/) — proves the fusion architecture end to end
    on deterministic synthetic data.
See README.md for both paths.
"""

__version__ = "0.7.0"
