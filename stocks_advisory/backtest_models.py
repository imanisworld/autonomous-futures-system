"""stocks_advisory/backtest_models.py

Data model for the QQQ -> TQQQ/SQQQ first-hour backtest -- Stock/ETF
Backtest v1. Research/backtest only: every dataclass here is a plain,
frozen data container built from caller-supplied historical bars.
Nothing here fetches a quote, places an order, reads the system clock,
or touches a broker, Robinhood, futures, or `options_manager` code.

`DaySession` bundles one trading day's QQQ signal bars with the
*separate* TQQQ/SQQQ bars used for execution pricing -- the backtest
must never price a TQQQ/SQQQ fill off a QQQ bar (see
`tqqq_sqqq_backtest.py`'s no-lookahead rules). `previous_close` is
supplied explicitly per session rather than derived from a prior
`DaySession` in the same run, so a day's gap calculation can never
accidentally read a later day's data.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


LEVERAGED_ETF_FACTOR = 3.0
"""TQQQ/SQQQ target ~3x QQQ's daily percent move. Used only to translate
a QQQ-side invalidation distance (percent to VWAP at entry) into a
stop distance on the actual tradable vehicle -- a documented, named
assumption, not a hidden constant."""

DEFAULT_SLIPPAGE_STRESS_LEVELS: tuple[float, ...] = (0.00, 0.05, 0.10, 0.15, 0.25)


@dataclass(frozen=True, kw_only=True)
class Bar:
    """One OHLCV bar. `timestamp` is an ISO-8601 string; this module
    never reads the system clock -- every time comparison is against a
    caller-supplied bar timestamp."""

    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True, kw_only=True)
class DaySession:
    """One trading day's data. `qqq_bars` drives signal detection only;
    `tqqq_bars`/`sqqq_bars` are the *only* source of fill/exit prices for
    a TQQQ or SQQQ trade. All three bar sequences are assumed
    chronologically ordered and index-aligned by the caller (bar i of
    each sequence shares the same timestamp) -- a day where that does
    not hold is the caller's responsibility to exclude or realign
    before building a `DaySession`."""

    date: str
    qqq_previous_close: float
    qqq_bars: tuple[Bar, ...] = ()
    tqqq_bars: tuple[Bar, ...] = ()
    sqqq_bars: tuple[Bar, ...] = ()


@dataclass(frozen=True, kw_only=True)
class BacktestConfig:
    """Backtest configuration. Every threshold is an explicit field --
    nothing here is a hidden module-level default silently applied
    without the caller seeing it in the config they built."""

    max_gap_percent: float
    min_first_hour_range_percent: float
    max_first_hour_range_percent: float
    first_hour_minutes: int = 60
    exit_cutoff_time: str = "15:55"
    slippage_percent: float = 0.0
    commission_per_trade: float = 0.0
    max_trades_per_day: int = 1
    target_r_multiple: float = 1.0
    position_dollar_size: float = 1000.0
    same_day_conflict_priority: Optional[str] = None  # None, "TQQQ", or "SQQQ"


class TradeDirection(str, Enum):
    LONG_TQQQ = "long_tqqq"
    LONG_SQQQ = "long_sqqq"
    NO_TRADE = "no_trade"
    CONFLICT = "conflict"


@dataclass(frozen=True, kw_only=True)
class BacktestTradeResult:
    """One day's backtest outcome. A day that never entered a position
    still gets a record here (`skipped=True`) with a `skipped_reason` --
    "trade log contains skipped reasons" is this field, not a separate
    log. `SkippedDay` (below) is the narrower subset of days that could
    not even be evaluated (missing required bar data)."""

    trade_date: str
    vehicle_symbol: str
    direction: TradeDirection
    entry_time: Optional[str] = None
    entry_price: Optional[float] = None
    stop_price: Optional[float] = None
    target_price: Optional[float] = None
    exit_time: Optional[str] = None
    exit_price: Optional[float] = None
    exit_reason: str = ""
    r_result: Optional[float] = None
    dollar_result: Optional[float] = None
    percent_result: Optional[float] = None
    skipped: bool = False
    skipped_reason: str = ""
    notes: str = ""


@dataclass(frozen=True, kw_only=True)
class SkippedDay:
    """A day that could not be evaluated at all -- required bar data
    was missing. Distinct from a day the strategy legitimately read as
    NO_TRADE (gap/range/no-breakout/VWAP-conflict), which still gets a
    full `BacktestTradeResult` entry in the trade log."""

    date: str
    reason: str


@dataclass(frozen=True, kw_only=True)
class BacktestSummary:
    """Deterministic rollup of a backtest run. `None` fields mean there
    was not enough data to compute that metric responsibly -- never a
    forced number on a thin sample."""

    total_trades: int
    win_rate_percent: Optional[float]
    average_win_dollars: Optional[float]
    average_loss_dollars: Optional[float]
    expectancy_dollars: Optional[float]
    profit_factor: Optional[float]
    max_drawdown_dollars: Optional[float]
    max_losing_streak: int
    average_trade_return_percent: Optional[float]
    annualized_return_percent: Optional[float]
    sharpe_ratio: Optional[float]
    trade_log: tuple[BacktestTradeResult, ...] = ()
    skipped_days: tuple[SkippedDay, ...] = ()


@dataclass(frozen=True, kw_only=True)
class SlippageStressPoint:
    slippage_percent: float
    summary: BacktestSummary


@dataclass(frozen=True, kw_only=True)
class SlippageStressReport:
    points: tuple[SlippageStressPoint, ...] = ()
