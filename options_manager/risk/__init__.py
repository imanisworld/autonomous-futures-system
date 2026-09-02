"""Measurement-only options risk telemetry.

This package projects already-persisted canonical advisory risk facts. It does
not re-run portfolio-risk formulas, choose limits, size positions, submit
orders, or alter portfolio policy.
"""

from .telemetry import (
    RiskTelemetryResult,
    RiskTelemetrySnapshot,
    RiskTelemetryStatus,
    measure_risk_telemetry,
)

__all__ = [
    "RiskTelemetryResult",
    "RiskTelemetrySnapshot",
    "RiskTelemetryStatus",
    "measure_risk_telemetry",
]
