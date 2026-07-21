"""Phase 5 — Analytics, Alerting & Anomaly Library.

Six precision-gated anomaly detectors over the object graph, a composite
risk score that decomposes into its evidence, and the analyst-disposition
feedback loop that makes the whole thing improve in a way a competitor
cloning the architecture can't replicate — because the labels are the asset.
"""
from .library import (SENSITIVE_ZONES, run_anomaly_library,
                      detect_dark_vessels, detect_spoofing,
                      detect_dark_rendezvous, detect_sensitive_loitering,
                      detect_identity_then_anomaly, detect_port_risk)
from .risk import rank_vessels, risk_score
from .feedback import feedback_summary, propose_retune, RetuneResult

__all__ = ["run_anomaly_library", "SENSITIVE_ZONES", "detect_dark_vessels",
           "detect_spoofing", "detect_dark_rendezvous",
           "detect_sensitive_loitering", "detect_identity_then_anomaly",
           "detect_port_risk", "risk_score", "rank_vessels",
           "propose_retune", "feedback_summary", "RetuneResult"]
