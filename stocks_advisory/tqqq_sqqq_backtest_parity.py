"""stocks_advisory/tqqq_sqqq_backtest_parity.py

Historical-Engine Parity build. Reproduces, without changing, the exact
strategy that produced the +$4.08/trade, profit-factor-2.10, 290-trade
result documented in
private/stocks-advisory-backtest-2026-07-11.md --
`tqqq_sqqq_backtest.evaluate_day()` run under
`run_stocks_csv_backtest._default_config()` -- and wraps it in a
paper-CLI-compatible shape (one day's bars in, one decision+resolution
out, journaled via the existing `paper_journal.py`). This is NOT the same
engine as `tqqq_sqqq_decision.py` (the v1 paper-harness engine, rejected)
or `tqqq_sqqq_backtest_v2.py` (the two-lane continuation build, v2
rejected as a freeze candidate / Lane 2 research-only). It is a third,
distinct decision path: `STRATEGY_VERSION = "tqqq_sqqq_backtest_parity_v1"`.

No new entry, stop, target, or exit logic is written anywhere in this
file. `evaluate_day()` and `_default_config()` are imported and called
directly, unmodified -- every number this module produces traces back to
that one existing, already-validated function. The only things this
module adds are: (1) a gross-vs-friction-adjusted split (two calls to the
same unmodified `evaluate_day()`, once at 0% slippage/$0 commission,
once at 0.15% slippage with the real Robinhood regulatory fee added
post-hoc -- matching section 9 of the original evidence report's exact
methodology, not a new friction design), and (2) a thin per-day wrapper
so a single day's decision can be journaled the same way the v1 paper
harness already journals its own decisions.

Research/backtest-compatible only in this pass: this module is NOT wired
into any live-execution path, does not import
`broker`/`execution`/`futures`/`options_manager`, does not read the
system clock, and is not (yet) declared the official forward-proof
source -- that remains a separate, later decision gated on the parity
report this module exists to produce.
"""

from __future__ import annotations

import dataclasses
from typing import Optional

from .backtest_models import BacktestConfig, BacktestTradeResult, DaySession, SkippedDay
from .tqqq_sqqq_backtest import evaluate_day

STRATEGY_VERSION = "tqqq_sqqq_backtest_parity_v1"

# Robinhood regulatory-fee rates -- duplicated from
# scripts/stocks_advisory_robustness_audit.py's own
# robinhood_regulatory_fee_dollars(), not imported (scripts/ files are
# not treated as importable libraries in this codebase's convention --
# see scripts/polygon_stocks_backfill.py's own docstring on why it
# doesn't import sources.polygon_client either). Same rates, same
# formula, same sell-leg-only application.
SEC_FEE_RATE_PER_DOLLAR_OF_SELL_PROCEEDS = 0.0000080
FINRA_TAF_RATE_PER_SHARE_SOLD = 0.000166
FINRA_TAF_MAX_PER_TRADE_DOLLARS = 8.30

FRICTION_ADJUSTED_SLIPPAGE_PERCENT = 0.15
"""Matches the predeclared `DEFAULT_SLIPPAGE_STRESS_LEVELS` entry the
original evidence report's section 9 used for its +$0.71/trade,
profit-factor-1.13 friction-adjusted number -- not a new value chosen
for this module."""


def _default_config() -> BacktestConfig:
    """Byte-for-byte duplicate of
    `scripts/run_stocks_csv_backtest.py::_default_config()`'s field
    values -- duplicated rather than imported, matching this codebase's
    established convention that `scripts/` files are entrypoints, not a
    library `stocks_advisory/` modules import from (see
    `scripts/polygon_stocks_backfill.py`'s own docstring on the same
    point). No field here may ever differ from that function's values;
    if that function's defaults ever change, this one must be updated
    to match, not the other way around -- this module exists to
    reproduce that config, not to own it."""
    return BacktestConfig(
        max_gap_percent=2.0,
        min_opening_range_percent=0.1,
        max_opening_range_percent=5.0,
        opening_range_minutes=60,
        exit_cutoff_time="15:55",
        slippage_percent=0.0,
        commission_per_trade=0.0,
        target_r_multiple=1.0,
        position_dollar_size=1000.0,
    )


def _robinhood_regulatory_fee_dollars(shares_sold: float, sell_proceeds_dollars: float) -> float:
    sec_fee = max(0.0, sell_proceeds_dollars) * SEC_FEE_RATE_PER_DOLLAR_OF_SELL_PROCEEDS
    taf = min(max(0.0, shares_sold) * FINRA_TAF_RATE_PER_SHARE_SOLD, FINRA_TAF_MAX_PER_TRADE_DOLLARS)
    return sec_fee + taf


@dataclasses.dataclass(frozen=True, kw_only=True)
class ParityDayResult:
    """One day's parity-engine outcome. `gross` and `friction_adjusted`
    are both full `BacktestTradeResult` objects from two independent,
    unmodified `evaluate_day()` calls -- never a single blended number.
    `no_new_decision_logic` is a literal, checkable marker (not
    documentation-only) that this module re-derives no entry/exit rule."""

    trade_date: str
    gross: BacktestTradeResult
    friction_adjusted: BacktestTradeResult
    friction_adjusted_regulatory_fees_dollars: Optional[float] = None
    no_new_decision_logic: bool = True


def run_parity_day(day: DaySession) -> ParityDayResult | SkippedDay:
    """Runs one day through the unmodified historical engine twice:
    once at the original 0%-slippage/$0-commission config (gross,
    reproduces the original evidence report's base case exactly), once
    at 0.15% slippage (the friction-adjusted case), with the real
    Robinhood regulatory fee added post-hoc to the friction-adjusted
    trade's dollar result -- matching the original report's section 9
    methodology exactly. Returns a `SkippedDay` unchanged if the
    (identical) gross evaluation itself could not be evaluated at all
    (missing bar data)."""
    gross_config = _default_config()
    gross_result = evaluate_day(day, gross_config)
    if isinstance(gross_result, SkippedDay):
        return gross_result

    friction_config = dataclasses.replace(gross_config, slippage_percent=FRICTION_ADJUSTED_SLIPPAGE_PERCENT)
    friction_result = evaluate_day(day, friction_config)
    if isinstance(friction_result, SkippedDay):
        # Cannot happen in practice (same day, same gating logic just
        # evaluated successfully above) -- fails closed rather than
        # guessing if it ever does.
        return friction_result

    regulatory_fees: Optional[float] = None
    if not friction_result.skipped and friction_result.dollar_result is not None:
        shares = gross_config.position_dollar_size / friction_result.entry_price if friction_result.entry_price else 0.0
        sell_proceeds = shares * (friction_result.exit_price or 0.0)
        regulatory_fees = _robinhood_regulatory_fee_dollars(shares_sold=shares, sell_proceeds_dollars=sell_proceeds)
        friction_result = dataclasses.replace(
            friction_result,
            dollar_result=friction_result.dollar_result - regulatory_fees,
        )

    return ParityDayResult(
        trade_date=day.date,
        gross=gross_result,
        friction_adjusted=friction_result,
        friction_adjusted_regulatory_fees_dollars=regulatory_fees,
    )
