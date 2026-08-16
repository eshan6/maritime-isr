"""Phase 4 — Object Graph & Ontology.

Where detections become intelligence. The moat is accumulated edges, not
algorithms: the graph starts accumulating the day this module first runs,
and that history cannot be backfilled later.
"""
from .store import Edge, GraphStore
from .ontology import EDGE_TYPES_V1, NODE_TYPES_V1, ONTOLOGY_VERSION
from .identity import (contact_node_id, current_mmsi, fold_registry_snapshot,
                       resolve_mmsi, track_subject_id, vessel_id)
from .ingest import (ensure_world, ingest_encounters, ingest_fusion,
                     ingest_ownership, ingest_sanctions, ingest_tracks)
from .rules import DEFAULT_RULES, ownership_chains, process_events

__all__ = ["GraphStore", "Edge", "ONTOLOGY_VERSION", "NODE_TYPES_V1",
           "EDGE_TYPES_V1", "fold_registry_snapshot", "resolve_mmsi",
           "vessel_id", "current_mmsi", "ensure_world", "ingest_ownership",
           "ingest_sanctions", "ingest_tracks", "ingest_encounters",
           "ingest_fusion", "ownership_chains", "process_events",
           "DEFAULT_RULES", "track_subject_id", "contact_node_id"]
