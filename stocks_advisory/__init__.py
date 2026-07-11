"""stocks_advisory — Stock/ETF Paper Advisory Bot v1: TQQQ/SQQQ practice lane.

Paper/advisory only. QQQ is the signal source; TQQQ/SQQQ are the only
tradeable vehicles. No broker, execution, futures, or options_manager
coupling of any kind -- a separate lane from both existing systems.
See `tqqq_sqqq_decision.py` for the decision rules and
`tqqq_sqqq_models.py` for the data model.
"""

from __future__ import annotations

from .tqqq_sqqq_models import (
    PaperTradeRecord,
    PaperTradeStatus,
    QQQSignalInput,
    TqqqSqqqDecisionResult,
    TqqqSqqqDirection,
    TqqqSqqqVerdict,
)
from .tqqq_sqqq_decision import (
    BEARISH_VEHICLE,
    BULLISH_VEHICLE,
    SIGNAL_SYMBOL,
    check_tqqq_sqqq_decision_intake,
    evaluate_tqqq_sqqq_decision,
)

__all__ = [
    "PaperTradeRecord",
    "PaperTradeStatus",
    "QQQSignalInput",
    "TqqqSqqqDecisionResult",
    "TqqqSqqqDirection",
    "TqqqSqqqVerdict",
    "BEARISH_VEHICLE",
    "BULLISH_VEHICLE",
    "SIGNAL_SYMBOL",
    "check_tqqq_sqqq_decision_intake",
    "evaluate_tqqq_sqqq_decision",
]
