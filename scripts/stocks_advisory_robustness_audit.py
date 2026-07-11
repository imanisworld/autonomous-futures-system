#!/usr/bin/env python3
"""scripts/stocks_advisory_robustness_audit.py

Predeclared robustness audit for the Stock/ETF Backtest v1 QQQ->TQQQ/SQQQ
strategy. Runs ONCE against the SAME locked config as
scripts/run_stocks_csv_backtest.py's _default_config() (imported directly
from that module, never redefined, so the two can never drift) and the
same checksummed Polygon dataset. This is explicitly NOT a parameter
search: every cut below either re-slices the one existing trade log by a
read-only-computed bucket key, or re-runs the identical strategy logic
with only a stress dimension (commission/slippage) varied -- mirroring
the pattern the codebase already uses for run_slippage_stress(). No
entry/exit/stop/target rule is ever changed.

Research/backtest only: reads three local CSV paths given on the command
line, makes no network call, writes no file except the JSON report to
stdout (or --out).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.run_stocks_csv_backtest import _default_config  # the one locked config, never redefined here
from stocks_advisory.backtest_models import BacktestTradeResult, DaySession, TradeDirection
from stocks_advisory.csv_loader import build_day_sessions, load_bars_from_csv
from stocks_advisory.tqqq_sqqq_backtest import (
    _max_drawdown,
    _max_losing_streak,
    _opening_range_bars,
    run_backtest,
)

# Commission assumption for the "fees + slippage" cut only (item 9 of the
# predeclared audit). Not part of the locked base config -- a documented,
# clearly-labeled robustness assumption, not a strategy parameter.
ASSUMED_COMMISSION_PER_TRADE_DOLLARS = 1.00

# Robinhood real cost model (item 1 of the follow-up cost-model audit).
# Robinhood charges $0 commission on stock/ETF trades; the only per-trade
# cost is the regulatory fee pass-through, charged on the SELL leg only
# (every trade here is buy-to-open/sell-to-close, never a short sale).
# Rates below are the standard, publicly published SEC Section 31 fee and
# FINRA Trading Activity Fee (TAF) structure as commonly cited -- the SEC
# rate in particular is adjusted periodically (roughly annually) by the
# SEC's own fee-rate formula, so treat this as a representative, not a
# guaranteed-current, rate. Not sourced from Robinhood's own fee
# disclosure directly (no live lookup performed in this session) --
# flagged as an assumption, same as the $1/trade placeholder it replaces.
SEC_FEE_RATE_PER_DOLLAR_OF_SELL_PROCEEDS = 0.0000080  # ~$8.00 per $1,000,000 sold
FINRA_TAF_RATE_PER_SHARE_SOLD = 0.000166
FINRA_TAF_MAX_PER_TRADE_DOLLARS = 8.30


def robinhood_regulatory_fee_dollars(shares_sold: float, sell_proceeds_dollars: float) -> float:
    """SEC fee + FINRA TAF for one round-trip trade's sell leg only.
    Both fees apply to SELL transactions of covered equity securities;
    buys are always free of these regulatory fees. Commission itself is
    $0 at Robinhood -- there is no separate commission term here."""
    sec_fee = max(0.0, sell_proceeds_dollars) * SEC_FEE_RATE_PER_DOLLAR_OF_SELL_PROCEEDS
    taf = min(max(0.0, shares_sold) * FINRA_TAF_RATE_PER_SHARE_SOLD, FINRA_TAF_MAX_PER_TRADE_DOLLARS)
    return sec_fee + taf


def _session_gap_and_range_percent(day: DaySession, opening_range_minutes: int) -> Optional[tuple[float, float]]:
    """Recomputes gap_percent/range_percent for one session using the
    EXACT formula in tqqq_sqqq_backtest.evaluate_day() -- read-only,
    never imported as a private call into the live decision path, just
    duplicated here for bucketing already-decided trades. Returns None
    if the day lacks enough data (mirrors evaluate_day's own early-outs)."""
    if not day.qqq_bars or day.qqq_previous_close <= 0:
        return None
    opening_range = _opening_range_bars(day.qqq_bars, opening_range_minutes)
    if not opening_range:
        return None
    day_open = day.qqq_bars[0].open
    gap_percent = (day_open - day.qqq_previous_close) / day.qqq_previous_close * 100.0
    range_high = max(b.high for b in opening_range)
    range_low = min(b.low for b in opening_range)
    range_percent = (range_high - range_low) / day_open * 100.0 if day_open > 0 else 0.0
    return gap_percent, range_percent


def _taken_trades(trade_log: tuple[BacktestTradeResult, ...]) -> list[BacktestTradeResult]:
    return [t for t in trade_log if not t.skipped and t.dollar_result is not None]


def _quarter_of(date_str: str) -> str:
    year, month, _ = date_str.split("-")
    q = (int(month) - 1) // 3 + 1
    return f"{year}-Q{q}"


def _month_of(date_str: str) -> str:
    year, month, _ = date_str.split("-")
    return f"{year}-{month}"


def _bucket_stats(trades: list[BacktestTradeResult]) -> dict:
    n = len(trades)
    if n == 0:
        return {"trade_count": 0, "win_rate_percent": None, "total_pnl_dollars": 0.0, "expectancy_dollars": None}
    wins = [t for t in trades if (t.dollar_result or 0) > 0]
    losses = [t for t in trades if (t.dollar_result or 0) < 0]
    total_pnl = sum(t.dollar_result or 0.0 for t in trades)
    gross_win = sum(t.dollar_result for t in wins) if wins else 0.0
    gross_loss = abs(sum(t.dollar_result for t in losses)) if losses else 0.0
    return {
        "trade_count": n,
        "win_rate_percent": 100.0 * len(wins) / n,
        "total_pnl_dollars": total_pnl,
        "expectancy_dollars": total_pnl / n,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else None,
    }


# ── 1. Results by quarter and month ─────────────────────────────────────────
def by_quarter_and_month(trades: list[BacktestTradeResult]) -> dict:
    by_q: dict[str, list] = defaultdict(list)
    by_m: dict[str, list] = defaultdict(list)
    for t in trades:
        by_q[_quarter_of(t.trade_date)].append(t)
        by_m[_month_of(t.trade_date)].append(t)
    return {
        "by_quarter": {k: _bucket_stats(v) for k, v in sorted(by_q.items())},
        "by_month": {k: _bucket_stats(v) for k, v in sorted(by_m.items())},
    }


# ── 2. Long (TQQQ) vs inverse (SQQQ) side ───────────────────────────────────
def by_vehicle(trades: list[BacktestTradeResult]) -> dict:
    by_v: dict[str, list] = defaultdict(list)
    for t in trades:
        by_v[t.vehicle_symbol].append(t)
    return {k: _bucket_stats(v) for k, v in sorted(by_v.items())}


# ── 3. Gap-size buckets, 4. volatility/opening-range buckets ───────────────
_GAP_BUCKET_EDGES = [(-999, -1.0, "gap < -1.0%"), (-1.0, -0.3, "-1.0% to -0.3%"),
                      (-0.3, 0.3, "-0.3% to +0.3%"), (0.3, 1.0, "+0.3% to +1.0%"),
                      (1.0, 999, "gap > +1.0%")]
_RANGE_BUCKET_EDGES = [(0.0, 0.3, "range < 0.3%"), (0.3, 0.6, "0.3% to 0.6%"),
                        (0.6, 1.0, "0.6% to 1.0%"), (1.0, 2.0, "1.0% to 2.0%"),
                        (2.0, 999, "range > 2.0%")]


def _bucket_label(value: float, edges: list[tuple[float, float, str]]) -> str:
    for lo, hi, label in edges:
        if lo <= value < hi:
            return label
    return "unbucketed"


def by_gap_and_range(trades: list[BacktestTradeResult], day_context: dict[str, tuple[float, float]]) -> dict:
    by_gap: dict[str, list] = defaultdict(list)
    by_range: dict[str, list] = defaultdict(list)
    unmatched = 0
    for t in trades:
        ctx = day_context.get(t.trade_date)
        if ctx is None:
            unmatched += 1
            continue
        gap_percent, range_percent = ctx
        by_gap[_bucket_label(gap_percent, _GAP_BUCKET_EDGES)].append(t)
        by_range[_bucket_label(range_percent, _RANGE_BUCKET_EDGES)].append(t)
    return {
        "by_gap_bucket": {k: _bucket_stats(v) for k, v in by_gap.items()},
        "by_opening_range_bucket_as_volatility_proxy": {k: _bucket_stats(v) for k, v in by_range.items()},
        "trades_with_no_day_context_matched": unmatched,
    }


# ── 5/6. Largest winners/losers + P&L concentration ─────────────────────────
def concentration(trades: list[BacktestTradeResult]) -> dict:
    ranked = sorted(trades, key=lambda t: t.dollar_result or 0.0, reverse=True)
    total_pnl = sum(t.dollar_result or 0.0 for t in trades)
    top5 = ranked[:5]
    top10 = ranked[:10]
    bottom5 = ranked[-5:][::-1]

    def _fmt(t: BacktestTradeResult) -> dict:
        return {"date": t.trade_date, "vehicle": t.vehicle_symbol, "direction": t.direction.value,
                "dollar_result": t.dollar_result, "exit_reason": t.exit_reason}

    top5_pnl = sum(t.dollar_result or 0.0 for t in top5)
    top10_pnl = sum(t.dollar_result or 0.0 for t in top10)
    return {
        "top_5_winners": [_fmt(t) for t in top5],
        "top_5_losers": [_fmt(t) for t in bottom5],
        "total_pnl_dollars": total_pnl,
        "top_5_winners_pnl_dollars": top5_pnl,
        "top_5_winners_share_of_total_pnl_percent": (100.0 * top5_pnl / total_pnl) if total_pnl else None,
        "top_10_winners_pnl_dollars": top10_pnl,
        "top_10_winners_share_of_total_pnl_percent": (100.0 * top10_pnl / total_pnl) if total_pnl else None,
    }


# ── 8. Results excluding the best 5 trades ──────────────────────────────────
def excluding_best_n(trades: list[BacktestTradeResult], n: int = 5) -> dict:
    ranked = sorted(trades, key=lambda t: t.dollar_result or 0.0, reverse=True)
    excluded_ids = {id(t) for t in ranked[:n]}
    remaining = [t for t in trades if id(t) not in excluded_ids]
    return {"excluded_count": n, "with_best_excluded": _bucket_stats(remaining)}


# ── 7. Exposure-adjusted benchmark comparison ───────────────────────────────
def exposure_adjusted_benchmark(base_summary, sessions: list[DaySession]) -> dict:
    strategy_total_return_percent = None
    if base_summary.equity_curve:
        start_capital = 1000.0  # position_dollar_size, the base unit each trade risks
        end_dollars = base_summary.equity_curve[-1].cumulative_dollars
        strategy_total_return_percent = 100.0 * end_dollars / start_capital

    exposure_fraction = (base_summary.exposure_percent or 0.0) / 100.0
    bh_qqq = base_summary.buy_and_hold_qqq_return_percent
    bh_tqqq = base_summary.buy_and_hold_tqqq_return_percent

    return {
        "strategy_exposure_percent": base_summary.exposure_percent,
        "strategy_total_return_percent_on_position_dollar_size": strategy_total_return_percent,
        "buy_and_hold_qqq_return_percent_full_period": bh_qqq,
        "buy_and_hold_tqqq_return_percent_full_period": bh_tqqq,
        "buy_and_hold_tqqq_return_percent_scaled_to_strategy_exposure_fraction": (
            bh_tqqq * exposure_fraction if bh_tqqq is not None else None
        ),
        "note": (
            "The scaled buy-and-hold figure approximates 'if you were only exposed "
            "to TQQQ/SQQQ for the same fraction of time the strategy was in a "
            "position' -- a rough capital-efficiency comparison, not a claim that "
            "partial buy-and-hold exposure is achievable at that exact timing."
        ),
    }


# ── 9. Fees + 0.10%/0.15% slippage combined ─────────────────────────────────
def fees_plus_slippage(sessions: list[DaySession], config) -> dict:
    scenarios = {}
    for slip in (0.10, 0.15):
        stressed_config = dataclasses.replace(
            config, slippage_percent=slip, commission_per_trade=ASSUMED_COMMISSION_PER_TRADE_DOLLARS
        )
        summary = run_backtest(sessions, stressed_config)
        scenarios[f"slippage_{slip}pct_plus_${ASSUMED_COMMISSION_PER_TRADE_DOLLARS:.2f}_commission"] = {
            "total_trades": summary.total_trades,
            "win_rate_percent": summary.win_rate_percent,
            "expectancy_dollars": summary.expectancy_dollars,
            "profit_factor": summary.profit_factor,
            "max_drawdown_dollars": summary.max_drawdown_dollars,
        }
    return {
        "assumed_commission_per_trade_dollars": ASSUMED_COMMISSION_PER_TRADE_DOLLARS,
        "assumption_note": (
            "No commission figure was specified by the operator; $1.00/trade "
            "(round-trip) is a documented placeholder approximating a "
            "discount/retail broker, not sourced from any specific broker's "
            "actual fee schedule."
        ),
        "scenarios": scenarios,
    }


def _summary_from_adjusted_pnl(adjusted_results: list[float]) -> dict:
    """Recomputes the same headline stats run_backtest's summarize_trades()
    produces, from an externally-adjusted per-trade P&L series (i.e. after
    subtracting a real, per-trade-varying fee that the flat
    commission_per_trade config field can't express). Reuses the exact
    same _max_drawdown/_max_losing_streak helpers as the real backtest
    engine, not a reimplementation."""
    n = len(adjusted_results)
    if n == 0:
        return {"total_trades": 0, "win_rate_percent": None, "expectancy_dollars": None,
                "profit_factor": None, "max_drawdown_dollars": None}
    wins = [r for r in adjusted_results if r > 0]
    losses = [r for r in adjusted_results if r < 0]
    gross_win = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0
    return {
        "total_trades": n,
        "win_rate_percent": 100.0 * len(wins) / n,
        "expectancy_dollars": sum(adjusted_results) / n,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else None,
        "max_drawdown_dollars": _max_drawdown(adjusted_results),
        "max_losing_streak": _max_losing_streak(adjusted_results),
    }


# ── Item 1 follow-up: real Robinhood regulatory-fee cost model ─────────────
def robinhood_realistic_cost_stress(sessions: list[DaySession], config) -> dict:
    """Replaces the flat $1/trade placeholder with Robinhood's real cost
    structure (regulatory fees only, sell-side, computed per-trade from
    actual shares/exit price -- not a flat number). Re-derives shares from
    entry_price the same way _resolve_vehicle_trade() does
    (position_dollar_size / entry_price), applied post-hoc to the
    UNMODIFIED trade log -- the strategy/backtest engine itself is never
    changed, only the cost accounting layered on top of it."""
    scenarios = {}
    for slip in (0.0, 0.05, 0.10, 0.15, 0.25):
        stressed_config = dataclasses.replace(config, slippage_percent=slip, commission_per_trade=0.0)
        summary = run_backtest(sessions, stressed_config)
        trades = _taken_trades(summary.trade_log)
        adjusted_results = []
        total_fees = 0.0
        for t in trades:
            shares = config.position_dollar_size / t.entry_price if t.entry_price else 0.0
            sell_proceeds = shares * (t.exit_price or 0.0)
            fee = robinhood_regulatory_fee_dollars(shares, sell_proceeds)
            total_fees += fee
            adjusted_results.append((t.dollar_result or 0.0) - fee)
        scenarios[f"slippage_{slip}pct_plus_robinhood_regulatory_fees"] = {
            **_summary_from_adjusted_pnl(adjusted_results),
            "total_regulatory_fees_dollars": total_fees,
            "average_regulatory_fee_per_trade_dollars": (total_fees / len(trades)) if trades else None,
        }
    return {
        "fee_model": "Robinhood: $0 commission, SEC fee + FINRA TAF on sells only",
        "rates_used": {
            "sec_fee_rate_per_dollar_sold": SEC_FEE_RATE_PER_DOLLAR_OF_SELL_PROCEEDS,
            "finra_taf_rate_per_share_sold": FINRA_TAF_RATE_PER_SHARE_SOLD,
            "finra_taf_max_per_trade_dollars": FINRA_TAF_MAX_PER_TRADE_DOLLARS,
        },
        "rates_caveat": (
            "Standard publicly-cited SEC/FINRA rates, not a live lookup against "
            "Robinhood's own disclosure in this session. SEC rate is adjusted "
            "periodically by the SEC's own formula -- treat as representative, "
            "not guaranteed-current."
        ),
        "scenarios": scenarios,
    }


# ── Item 3 follow-up: OHLCV bar-range consistency check ─────────────────────
# NAMING NOTE: this was originally named fill_plausibility_check(), which
# overstated its scope -- "plausibility" reads as a claim about real-world
# fillability. Renamed to ohlcv_fill_range_check() to make the actual, much
# narrower scope unambiguous from the name alone: it checks arithmetic
# consistency against OHLCV bar ranges, nothing about execution realism.
def ohlcv_fill_range_check(trades: list[BacktestTradeResult], sessions: list[DaySession]) -> dict:
    """Coarse consistency check ONLY: verifies every trade's entry_price
    and exit_price fall within [low, high] of that day's vehicle OHLCV
    bars. This proves nothing about real-world execution -- not whether a
    fill was achievable, not the bid/ask spread at that instant, not
    market impact, not quote staleness, not partial-fill risk. It only
    catches the narrow defect class of a backtest fabricating a price
    outside the day's recorded trading range.

    True spread-aware execution validation requires NBBO quote or trade
    tick data. That data was requested from Polygon and is NOT AVAILABLE
    on the current plan: /v3/quotes, /v2/last/nbbo, and /v3/trades all
    returned 403 NOT_AUTHORIZED. No quote-level validation of any kind
    was performed anywhere in this script. This function does not resolve,
    approximate, or substitute for that gap."""
    sessions_by_date = {s.date: s for s in sessions}
    out_of_range: list[dict] = []
    checked = 0
    for t in trades:
        session = sessions_by_date.get(t.trade_date)
        if session is None:
            continue
        vehicle_bars = session.tqqq_bars if t.vehicle_symbol == "TQQQ" else session.sqqq_bars
        if not vehicle_bars:
            continue
        day_low = min(b.low for b in vehicle_bars)
        day_high = max(b.high for b in vehicle_bars)
        checked += 1
        # A small tolerance for slippage-adjusted prices, which are
        # deliberately pushed slightly outside the raw bar range by
        # config.slippage_percent -- that is expected, not a defect.
        tolerance = max(day_high - day_low, 0.01) * 0.05
        if t.entry_price is not None and not (day_low - tolerance <= t.entry_price <= day_high + tolerance):
            out_of_range.append({"trade_date": t.trade_date, "field": "entry_price", "value": t.entry_price,
                                  "day_range": [day_low, day_high]})
        if t.exit_price is not None and not (day_low - tolerance <= t.exit_price <= day_high + tolerance):
            out_of_range.append({"trade_date": t.trade_date, "field": "exit_price", "value": t.exit_price,
                                  "day_range": [day_low, day_high]})
    return {
        "check_type": "ohlcv_bar_range_consistency_only",
        "trades_checked": checked,
        "fills_outside_day_ohlc_range": len(out_of_range),
        "out_of_range_fills": out_of_range[:20],
        "polygon_quote_data_entitlement": "403 NOT_AUTHORIZED on /v3/quotes, /v2/last/nbbo, and /v3/trades",
        "quote_level_validation_performed": False,
        "scope_limitation_note": (
            "This check ONLY confirms fills fall within the day's traded OHLC "
            "range. It does NOT establish and makes NO claim about: the actual "
            "bid/ask spread at decision time, whether a fill was available at "
            "the modeled timestamp, market impact, quote staleness, or "
            "partial-fill risk. Real NBBO/trades data is required for any of "
            "those and was NOT AUTHORIZED (403) on this Polygon plan for "
            "/v3/quotes, /v2/last/nbbo, and /v3/trades, all tested. No "
            "quote-level validation was performed anywhere in this script. "
            "This remains an open gap, not resolved here."
        ),
    }


# ── 10. Stability under the same unchanged configuration ───────────────────
def determinism_check(sessions: list[DaySession], config) -> dict:
    run_a = run_backtest(sessions, config)
    run_b = run_backtest(sessions, config)
    fields = ["total_trades", "win_rate_percent", "expectancy_dollars", "profit_factor", "max_drawdown_dollars"]
    identical = all(getattr(run_a, f) == getattr(run_b, f) for f in fields)
    return {
        "identical_on_repeat_run_same_config_same_data": identical,
        "fields_compared": fields,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qqq", required=True)
    parser.add_argument("--tqqq", required=True)
    parser.add_argument("--sqqq", required=True)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    qqq = load_bars_from_csv(args.qqq)
    tqqq = load_bars_from_csv(args.tqqq)
    sqqq = load_bars_from_csv(args.sqqq)
    sessions, session_report = build_day_sessions(qqq, tqqq, sqqq)

    config = _default_config()  # the SAME locked config, imported not redefined
    base_summary = run_backtest(sessions, config)
    trades = _taken_trades(base_summary.trade_log)

    sessions_by_date = {s.date: s for s in sessions}
    day_context: dict[str, tuple[float, float]] = {}
    for date_key, session in sessions_by_date.items():
        ctx = _session_gap_and_range_percent(session, config.opening_range_minutes)
        if ctx is not None:
            day_context[date_key] = ctx

    result = {
        "config_used": dataclasses.asdict(config),
        "no_parameter_tuning": True,
        "base_summary": {
            "total_trades": base_summary.total_trades,
            "win_rate_percent": base_summary.win_rate_percent,
            "expectancy_dollars": base_summary.expectancy_dollars,
            "profit_factor": base_summary.profit_factor,
            "max_drawdown_dollars": base_summary.max_drawdown_dollars,
        },
        "1_by_quarter_and_month": by_quarter_and_month(trades),
        "2_by_vehicle_long_vs_inverse": by_vehicle(trades),
        "3_and_4_gap_and_volatility_buckets": by_gap_and_range(trades, day_context),
        "5_and_6_concentration": concentration(trades),
        "7_exposure_adjusted_benchmark": exposure_adjusted_benchmark(base_summary, sessions),
        "8_excluding_best_5_trades": excluding_best_n(trades, 5),
        "9_fees_plus_slippage": fees_plus_slippage(sessions, config),
        "10_determinism_check": determinism_check(sessions, config),
        "11_robinhood_realistic_cost_stress": robinhood_realistic_cost_stress(sessions, config),
        "12_ohlcv_fill_range_check": ohlcv_fill_range_check(trades, sessions),
    }

    out_str = json.dumps(result, indent=2, default=str)
    if args.out:
        Path(args.out).write_text(out_str, encoding="utf-8")
        print(f"Report written -> {args.out}")
    else:
        print(out_str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
