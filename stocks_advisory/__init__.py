"""stocks_advisory — Stock/ETF Backtest v1 (QQQ -> TQQQ/SQQQ opening-range
backtest) and Paper Advisory Bot v1 (QQQ -> TQQQ/SQQQ practice lane).

Backtest/research and paper/advisory only. QQQ is the signal source;
TQQQ/SQQQ are the only tradeable vehicles. No broker, execution, futures, or
options_manager coupling of any kind -- a separate lane from both existing
systems. See `tqqq_sqqq_backtest.py` for the backtest decision/resolution
logic and `backtest_models.py` for its data model; see `tqqq_sqqq_decision.py`
for the paper-advisory decision rules and `tqqq_sqqq_models.py` for its data
model.
"""

from __future__ import annotations

from .backtest_models import (
    Bar,
    BacktestConfig,
    BacktestSummary,
    BacktestTradeResult,
    DaySession,
    DEFAULT_SLIPPAGE_STRESS_LEVELS,
    EquityPoint,
    InSampleOutOfSampleResult,
    LEVERAGED_ETF_FACTOR,
    SkippedDay,
    SkippedDayEntry,
    SlippageSensitivityResult,
    SlippageStressPoint,
    SlippageStressReport,
    StopModel,
    TargetModel,
    TradeDirection,
    TradeLogEntry,
    WalkForwardFold,
    WalkForwardResult,
)
from .csv_loader import (
    CsvValidationError,
    LoadedSymbolCsv,
    SessionBuildReport,
    build_day_sessions,
    load_bars_from_csv,
)
from .tqqq_sqqq_backtest import (
    evaluate_day,
    run_backtest,
    run_in_sample_out_of_sample,
    run_slippage_stress,
    run_walk_forward,
    summarize_trades,
)
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
    # Backtest v1 (backtest_models / csv_loader / tqqq_sqqq_backtest)
    "Bar",
    "BacktestConfig",
    "BacktestSummary",
    "BacktestTradeResult",
    "CsvValidationError",
    "DaySession",
    "DEFAULT_SLIPPAGE_STRESS_LEVELS",
    "EquityPoint",
    "InSampleOutOfSampleResult",
    "LEVERAGED_ETF_FACTOR",
    "LoadedSymbolCsv",
    "SessionBuildReport",
    "SkippedDay",
    "SkippedDayEntry",
    "SlippageSensitivityResult",
    "SlippageStressPoint",
    "SlippageStressReport",
    "StopModel",
    "TargetModel",
    "TradeDirection",
    "TradeLogEntry",
    "WalkForwardFold",
    "WalkForwardResult",
    "build_day_sessions",
    "evaluate_day",
    "load_bars_from_csv",
    "run_backtest",
    "run_in_sample_out_of_sample",
    "run_slippage_stress",
    "run_walk_forward",
    "summarize_trades",
    # Paper Advisory Bot v1 (tqqq_sqqq_models / tqqq_sqqq_decision)
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
