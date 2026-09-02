"""Measurement-only options risk telemetry.

This package records already-proven advisory risk facts. It does not choose
risk limits, size positions, submit orders, or alter portfolio policy.
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
