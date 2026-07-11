"""stocks_advisory — Stock/ETF Backtest v1: QQQ -> TQQQ/SQQQ
opening-range backtest.

Backtest/research only. QQQ is the signal source; TQQQ/SQQQ are the
only tradeable vehicles. No broker, execution, futures, or
options_manager coupling of any kind -- a separate lane from both
existing systems. See `tqqq_sqqq_backtest.py` for the decision/
resolution logic and `backtest_models.py` for the data model.
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

__all__ = [
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
]
