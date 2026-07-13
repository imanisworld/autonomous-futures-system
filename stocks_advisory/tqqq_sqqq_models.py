"""stocks_advisory/tqqq_sqqq_models.py

Data model for the TQQQ/SQQQ paper practice lane -- Stock/ETF Paper
Advisory Bot v1. QQQ is the signal source; TQQQ/SQQQ are the only
tradeable vehicles. Every dataclass here is a plain, frozen data
container -- no I/O, no broker/execution/futures/options coupling, no
network calls, no clock access. `QQQSignalInput` is the entire set of
facts a caller supplies by hand (or from a market-data read done
elsewhere); nothing here fetches a quote, a candle, or a broker
position.

This module performs no decisioning itself -- see
`tqqq_sqqq_decision.py` for `evaluate_tqqq_sqqq_decision()` and
`check_tqqq_sqqq_decision_intake()`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class TqqqSqqqVerdict(str, Enum):
    """The one-word advisory outcome of a single day's evaluation.
    Always advisory -- never an order, and never itself an execution
    instruction. `WAIT` is reserved for a future increment (e.g.
    mid-session monitoring before the first hour has closed); v1's
    decision rules only ever produce `TAKE_PAPER`, `NO_TRADE`, or
    `INVALID`."""

    TAKE_PAPER = "take_paper"
    WAIT = "wait"
    NO_TRADE = "no_trade"
    INVALID = "invalid"


class TqqqSqqqDirection(str, Enum):
    """Which vehicle, if any, this day's paper candidate is in."""

    LONG_TQQQ = "long_tqqq"
    LONG_SQQQ = "long_sqqq"
    NO_TRADE = "no_trade"


class PaperTradeStatus(str, Enum):
    """A paper trade record's own lifecycle status -- set by whoever
    is tracking the record afterward, never advanced automatically by
    anything in this module. v1's decision function only ever produces
    a fresh record at `WATCHING` or `NO_TRADE`; every other status
    exists for a human (or a later increment) to update by hand as the
    day plays out."""

    WATCHING = "watching"
    TRIGGERED = "triggered"
    ACTIVE = "active"
    EXITED = "exited"
    INVALIDATED = "invalidated"
    NO_TRADE = "no_trade"


@dataclass(frozen=True, kw_only=True)
class QQQSignalInput:
    """One trading day's QQQ read, entirely as reported by the caller --
    nothing here is fetched from a quote or candle feed. The three
    `allowed_*` fields are this day's threshold configuration, supplied
    explicitly rather than hidden behind a module-level default, so a
    given evaluation is a pure function of exactly what's passed in."""

    date: str
    qqq_open: float
    qqq_previous_day_high: float
    qqq_previous_day_low: float
    qqq_previous_day_close: float
    qqq_gap_percent: float
    qqq_first_hour_high: float
    qqq_first_hour_low: float
    qqq_first_hour_close: float
    qqq_vwap: float
    qqq_current_price: float
    relative_volume: float
    allowed_max_gap_percent: float
    allowed_min_first_hour_range: float
    allowed_max_first_hour_range: float
    market_regime_label: Optional[str] = None


@dataclass(frozen=True, kw_only=True)
class PaperTradeRecord:
    """One day's paper-trade record. `paper_entry_price`,
    `paper_exit_price`, `paper_result` are left `None` by v1's decision
    function -- this module only decides the entry plan, it does not
    simulate a forward resolution. Exactly one record represents exactly
    one day's evaluation; there is no field or container here for more
    than one trade per day, by construction."""

    trade_date: str
    signal_symbol: str
    vehicle_symbol: str
    direction: TqqqSqqqDirection
    entry_trigger: str
    invalidation: str
    stop_price: Optional[float]
    target_1: Optional[float]
    target_2: Optional[float]
    reason: str
    skipped_reason: str
    status: PaperTradeStatus
    paper_entry_price: Optional[float] = None
    paper_exit_price: Optional[float] = None
    paper_result: Optional[str] = None
    notes: str = ""


@dataclass(frozen=True, kw_only=True)
class TqqqSqqqDecisionResult:
    """Outcome of evaluating (or intaking) a `QQQSignalInput`. `trade`
    is populated whenever a payload normalized cleanly -- including
    when the verdict is `NO_TRADE` (a NO_TRADE day still gets a
    `PaperTradeRecord` describing why). `missing_fields`/
    `blocking_reasons` are populated only when `verdict` is
    `INVALID`."""

    verdict: TqqqSqqqVerdict
    trade: Optional[PaperTradeRecord] = None
    missing_fields: tuple[str, ...] = ()
    blocking_reasons: tuple[str, ...] = ()
    notes: str = ""
