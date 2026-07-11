"""stocks_advisory/backtest_models.py

Data model for the QQQ -> TQQQ/SQQQ opening-range backtest -- Stock/ETF
Backtest v1 (refined). Research/backtest only: every dataclass here is
a plain, frozen data container built from caller-supplied historical
bars. Nothing here fetches a quote, places an order, reads the system
clock, or touches a broker, Robinhood, futures, or `options_manager`
code.

`DaySession` bundles one trading day's QQQ signal bars with the
*separate* TQQQ/SQQQ bars used for execution pricing -- the backtest
must never price a TQQQ/SQQQ fill off a QQQ bar (see
`tqqq_sqqq_backtest.py`'s no-lookahead rules). `qqq_previous_close`/
`qqq_previous_high`/`qqq_previous_low` are supplied explicitly per
session rather than derived from a prior `DaySession` in the same run,
so a day's gap/target calculation can never accidentally read a later
(or adjacent) day's data through the run itself.

This module decides nothing -- it is data only. All decision,
resolution, and rollup logic lives in `tqqq_sqqq_backtest.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


LEVERAGED_ETF_FACTOR = 3.0
"""TQQQ/SQQQ target ~3x QQQ's daily percent move. Used only to translate
a QQQ-side distance (to the opening-range edge, to the prior day's
high/low, or an ATR-style range measure) into a stop/target distance on
the actual tradable vehicle -- a documented, named assumption, not a
hidden constant."""

DEFAULT_SLIPPAGE_STRESS_LEVELS: tuple[float, ...] = (0.00, 0.05, 0.10, 0.15, 0.25)


class TradeDirection(str, Enum):
    LONG_TQQQ = "long_tqqq"
    LONG_SQQQ = "long_sqqq"
    NO_TRADE = "no_trade"
    CONFLICT = "conflict"


class StopModel(str, Enum):
    """How the initial stop distance is derived. Trailing is not its
    own independent model here -- it is an overlay
    (`BacktestConfig.trailing_stop_enabled`) that can ratchet any of
    these models' initial stop tighter as price moves in favor."""

    OPPOSITE_RANGE_EDGE = "opposite_range_edge"
    PERCENT = "percent"
    ATR_RANGE = "atr_range"


class TargetModel(str, Enum):
    FIXED_R_MULTIPLE = "fixed_r_multiple"
    PRIOR_HIGH_LOW = "prior_high_low"
    END_OF_DAY = "end_of_day"
    TRAILING_STOP_EXIT = "trailing_stop_exit"


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
    before building a `DaySession`. `qqq_relative_volume` is supplied by
    the caller (e.g. today's volume-by-this-point vs. a trailing
    average computed elsewhere) -- this module never computes a
    cross-day rolling average itself."""

    date: str
    qqq_previous_close: float
    qqq_previous_high: float
    qqq_previous_low: float
    qqq_bars: tuple[Bar, ...] = ()
    tqqq_bars: tuple[Bar, ...] = ()
    sqqq_bars: tuple[Bar, ...] = ()
    qqq_relative_volume: Optional[float] = None


@dataclass(frozen=True, kw_only=True)
class BacktestConfig:
    """Backtest configuration. Every threshold is an explicit field --
    nothing here is a hidden module-level default silently applied
    without the caller seeing it in the config they built."""

    max_gap_percent: float
    min_opening_range_percent: float
    max_opening_range_percent: float
    opening_range_minutes: int = 60
    vwap_required: bool = True
    relative_volume_filter_enabled: bool = False
    min_relative_volume: float = 1.0
    exit_cutoff_time: str = "15:55"
    slippage_percent: float = 0.0
    commission_per_trade: float = 0.0
    max_trades_per_day: int = 1
    position_dollar_size: float = 1000.0
    same_day_conflict_priority: Optional[str] = None  # None, "TQQQ", or "SQQQ"

    stop_model: StopModel = StopModel.OPPOSITE_RANGE_EDGE
    stop_percent: Optional[float] = None  # required when stop_model == PERCENT
    stop_atr_multiple: Optional[float] = None  # required when stop_model == ATR_RANGE
    trailing_stop_enabled: bool = False
    trailing_stop_activation_r: float = 1.0
    trailing_stop_trail_r: float = 0.5

    target_model: TargetModel = TargetModel.FIXED_R_MULTIPLE
    target_r_multiple: float = 1.0


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
    NO_TRADE (gap/range/no-breakout/VWAP-conflict/low-relative-volume),
    which still gets a full `BacktestTradeResult` entry in the trade
    log."""

    date: str
    reason: str


@dataclass(frozen=True, kw_only=True)
class EquityPoint:
    date: str
    cumulative_dollars: float


@dataclass(frozen=True, kw_only=True)
class BacktestSummary:
    """Deterministic rollup of a backtest run. `None` fields mean there
    was not enough data to compute that metric responsibly -- never a
    forced number on a thin sample."""

    total_days_tested: int
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
    worst_single_loss_dollars: Optional[float]
    best_single_win_dollars: Optional[float]
    exposure_percent: Optional[float]
    buy_and_hold_qqq_return_percent: Optional[float] = None
    buy_and_hold_tqqq_return_percent: Optional[float] = None
    trade_log: tuple[BacktestTradeResult, ...] = ()
    skipped_days: tuple[SkippedDay, ...] = ()
    equity_curve: tuple[EquityPoint, ...] = ()
    skipped_days_by_reason: dict = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class SlippageStressPoint:
    slippage_percent: float
    summary: BacktestSummary


@dataclass(frozen=True, kw_only=True)
class SlippageStressReport:
    points: tuple[SlippageStressPoint, ...] = ()

    def only_profitable_at_zero_slippage(self) -> Optional[bool]:
        """True if expectancy is positive at 0% slippage but not
        positive at every other tested level -- the exact "does this
        only work with no friction at all" check. `None` if 0%
        slippage was not one of the tested points, or there are no
        other levels to compare against."""
        zero_point = next((p for p in self.points if p.slippage_percent == 0.0), None)
        if zero_point is None or zero_point.summary.expectancy_dollars is None:
            return None
        if not zero_point.summary.expectancy_dollars > 0:
            return False
        others = [p for p in self.points if p.slippage_percent != 0.0]
        if not others:
            return None
        return all(
            p.summary.expectancy_dollars is not None and p.summary.expectancy_dollars <= 0
            for p in others
        )


@dataclass(frozen=True, kw_only=True)
class InSampleOutOfSampleResult:
    in_sample_summary: BacktestSummary
    out_of_sample_summary: BacktestSummary
    in_sample_session_count: int
    out_of_sample_session_count: int
    split_date: str


@dataclass(frozen=True, kw_only=True)
class WalkForwardFold:
    fold_index: int
    train_session_count: int
    test_session_count: int
    train_summary: BacktestSummary
    test_summary: BacktestSummary
    train_start_date: str
    train_end_date: str
    test_start_date: str
    test_end_date: str


@dataclass(frozen=True, kw_only=True)
class WalkForwardResult:
    folds: tuple[WalkForwardFold, ...] = ()


# Exact names requested for this module's "Required outputs" list --
# aliases onto the types above rather than a disruptive rename, since
# they represent the identical concept.
TradeLogEntry = BacktestTradeResult
SkippedDayEntry = SkippedDay
SlippageSensitivityResult = SlippageStressReport
