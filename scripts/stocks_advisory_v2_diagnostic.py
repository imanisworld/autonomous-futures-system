#!/usr/bin/env python3
"""scripts/stocks_advisory_v2_diagnostic.py

Diagnostic report answering one question: did the market logic behind
Stock/ETF Strategy v2.0 fail, or did the v2 backtest accidentally test a
DIFFERENT "Lane 1" than the one whose earlier positive results
(private/stocks-advisory-backtest-2026-07-11.md, +$4.08/trade,
profit factor 2.10) justified this project?

Runs THREE independent engines against the SAME checksummed 370-session
dataset (data/stocks_advisory_polygon_5m/), with NO new thresholds and NO
parameter search -- every config below is an already-existing, previously
established default, reused verbatim:

1. "historical_module" -- stocks_advisory/tqqq_sqqq_backtest.py via
   scripts/run_stocks_csv_backtest.py::_default_config() (imported
   directly, never redefined) -- the ACTUAL module and config that
   produced the +$4.08/trade result documented in
   private/stocks-advisory-backtest-2026-07-11.md.
2. "v2_lane1" -- the new tqqq_sqqq_backtest_v2.py's Lane 1, which calls
   qqq_signal_builder.build_qqq_signal() + tqqq_sqqq_decision
   .evaluate_tqqq_sqqq_decision() + paper_simulator.advance_lifecycle(),
   using the frozen paper-harness thresholds from
   data/stocks_advisory_paper_proof/PROOF_MANIFEST.md.
3. "v2_lane2" -- the new continuation lane, operator-specified
   parameters, backtested under the same real friction model as Lane 1.

For each engine: gross vs. friction-adjusted P&L, trade count, win rate,
average win, average loss, expectancy, profit factor, max drawdown,
long-TQQQ vs. long-SQQQ split, and exit-reason distribution. For Lane 2
specifically: a count of days where a real pivot structure existed but
entry never triggered, split by which condition blocked it.

This script changes no strategy logic and adds no new parameter -- it
only re-slices and re-reports results already computed by three existing,
unmodified engines.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.run_stocks_csv_backtest import _default_config as historical_module_config
from stocks_advisory.backtest_models import BacktestSummary, TradeDirection
from stocks_advisory.csv_loader import build_day_sessions, load_bars_from_csv
from stocks_advisory.tqqq_sqqq_backtest import run_backtest as run_historical_module
from stocks_advisory.tqqq_sqqq_backtest_v2 import (
    V2Config,
    _confirm_pivots_through,
    _evaluate_lane1,
    _evaluate_lane2,
    _intraday_vwap_series,
    _is_confirmed_higher_low,
    _is_confirmed_lower_high,
    _opening_range_bars,
    _pivot_candidates,
    _running_extreme,
    CONTINUATION_EXTENDED_REASON,
    LANE2_ELIGIBILITY_END,
    LANE2_ELIGIBILITY_START,
)

DEFAULT_DATA_DIR = "data/stocks_advisory_polygon_5m"

V2_LANE1_CONFIG = V2Config(
    allowed_max_gap_percent=2.0,
    allowed_min_first_hour_range=1.0,
    allowed_max_first_hour_range=10.0,
)


def _bar_time(bar):
    from datetime import datetime
    return datetime.fromisoformat(bar.timestamp).time()


def _win_loss_stats(dollar_results: list[float]) -> dict:
    wins = [d for d in dollar_results if d > 0]
    losses = [d for d in dollar_results if d < 0]
    total = len(dollar_results)
    return {
        "trades": total,
        "win_rate_percent": (len(wins) / total * 100.0) if total else None,
        "average_win_dollars": (sum(wins) / len(wins)) if wins else None,
        "average_loss_dollars": (sum(losses) / len(losses)) if losses else None,
        "expectancy_dollars": (sum(dollar_results) / total) if total else None,
        "profit_factor": (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else None,
    }


def _max_drawdown(values: list[float]) -> float:
    peak = float("-inf")
    max_dd = 0.0
    running = 0.0
    for v in values:
        running += v
        peak = max(peak, running)
        max_dd = max(max_dd, peak - running)
    return max_dd


def _gross_vs_net(trades) -> dict:
    net = [t.dollar_result for t in trades if t.dollar_result is not None]
    gross = [t.gross_pnl_dollars for t in trades if getattr(t, "gross_pnl_dollars", None) is not None]
    return {
        "net": _win_loss_stats(net) | {"max_drawdown_dollars": _max_drawdown(net)},
        "gross": (_win_loss_stats(gross) | {"max_drawdown_dollars": _max_drawdown(gross)}) if gross else None,
    }


def _vehicle_split(trades) -> dict:
    out = {}
    for symbol in ("TQQQ", "SQQQ"):
        subset = [t for t in trades if t.vehicle_symbol == symbol and t.dollar_result is not None]
        out[symbol] = _win_loss_stats([t.dollar_result for t in subset])
    return out


def _exit_reason_distribution(trades) -> dict:
    return dict(Counter(t.exit_reason for t in trades if t.exit_reason))


def _historical_module_diagnostics(sessions) -> dict:
    config = historical_module_config()
    summary: BacktestSummary = run_historical_module(sessions, config)
    taken = [t for t in summary.trade_log if not t.skipped and t.dollar_result is not None]
    return {
        "config": dataclasses.asdict(config),
        "total_trades": summary.total_trades,
        "win_rate_percent": summary.win_rate_percent,
        "average_win_dollars": summary.average_win_dollars,
        "average_loss_dollars": summary.average_loss_dollars,
        "expectancy_dollars": summary.expectancy_dollars,
        "profit_factor": summary.profit_factor,
        "max_drawdown_dollars": summary.max_drawdown_dollars,
        "vehicle_split": _vehicle_split(taken),
        "exit_reason_distribution": _exit_reason_distribution(taken),
        "entry_mechanism": (
            "Scans EVERY bar after the opening range for the first close beyond "
            "range_high/range_low with VWAP confirmation, any time during the day. "
            "Stop = OPPOSITE_RANGE_EDGE (QQQ-side distance to the far edge of the "
            "opening range, x3 leveraged-ETF factor). Target = FIXED_R_MULTIPLE "
            "(1.0R) -- a real, systematic profit target. Sizing = fractional shares."
        ),
    }


def _v2_lane_diagnostics(trade_log, lane: str) -> dict:
    lane_trades = [t for t in trade_log if t.lane == lane and not t.skipped]
    gross_net = _gross_vs_net(lane_trades)
    return {
        "gross_vs_net": gross_net,
        "vehicle_split": _vehicle_split(lane_trades),
        "exit_reason_distribution": _exit_reason_distribution(lane_trades),
    }


def _lane2_missed_vs_false(sessions, config: V2Config) -> dict:
    """For every day Lane 2 evaluated (Lane 1 was NO_TRADE), classify:
    - 'extension_blocked': a real pivot+trend+entry-break+room setup existed
      but only the vehicle-extension filter blocked it (already counted as
      CONTINUATION_EXTENDED in the main run).
    - 'pivot_existed_no_entry': at least 2 confirmed pivots existed (a real
      lower-high/higher-low structure formed) but no bar ever satisfied
      the full entry gate (structural or extension) -- a candidate for
      "missed valid trend", though this script makes no claim about
      what the financial outcome would have been if entered.
    - 'no_pivot_structure': fewer than 2 confirmed pivots ever formed --
      no continuation structure was even present to evaluate.
    'false_continuation_entries' = real Lane 2 trades that lost money --
    already computed directly from the trade log elsewhere, cross-referenced
    here as the win/loss counts.
    """
    extension_blocked = 0
    pivot_existed_no_entry = 0
    no_pivot_structure = 0
    lane2_real_trade_days = 0

    for day in sessions:
        lane1 = _evaluate_lane1(day, config)
        if not (hasattr(lane1, "skipped") and lane1.skipped):
            continue  # Lane 1 took the day; Lane 2 never ran
        lane2 = _evaluate_lane2(day, config)
        if not lane2.skipped:
            lane2_real_trade_days += 1
            continue
        if lane2.skipped_reason == CONTINUATION_EXTENDED_REASON:
            extension_blocked += 1
            continue

        # Did a real >=2-pivot structure exist anywhere in the day, even
        # though no bar satisfied the full entry gate?
        qqq_bars = day.qqq_bars
        opening_range = _opening_range_bars(qqq_bars)
        if not opening_range:
            no_pivot_structure += 1
            continue
        eligible_indices = [
            i for i, bar in enumerate(qqq_bars)
            if LANE2_ELIGIBILITY_START <= _bar_time(bar) <= LANE2_ELIGIBILITY_END
        ]
        if not eligible_indices:
            no_pivot_structure += 1
            continue
        eligible_from = eligible_indices[0]
        qqq_vwap = _intraday_vwap_series(qqq_bars)
        high_candidates = _pivot_candidates(qqq_bars, eligible_from_index=eligible_from, high=True)
        low_candidates = _pivot_candidates(qqq_bars, eligible_from_index=eligible_from, high=False)
        last_index = len(qqq_bars) - 1
        confirmed_highs = _confirm_pivots_through(qqq_bars, high_candidates, through_index=last_index, high=True)
        confirmed_lows = _confirm_pivots_through(qqq_bars, low_candidates, through_index=last_index, high=False)
        had_structure = _is_confirmed_lower_high(confirmed_highs, qqq_vwap) or _is_confirmed_higher_low(
            confirmed_lows, qqq_vwap
        )
        if had_structure:
            pivot_existed_no_entry += 1
        else:
            no_pivot_structure += 1

    return {
        "days_lane2_evaluated": extension_blocked + pivot_existed_no_entry + no_pivot_structure + lane2_real_trade_days,
        "lane2_real_trade_days": lane2_real_trade_days,
        "extension_blocked_days": extension_blocked,
        "pivot_existed_no_entry_days": pivot_existed_no_entry,
        "no_pivot_structure_days": no_pivot_structure,
        "note": (
            "'pivot_existed_no_entry' is a count of days a lower-high/higher-low "
            "formed without ever meeting the full entry gate -- it is NOT a claim "
            "these were profitable missed trades, only that structure existed. "
            "Whether entering them would have helped or hurt is not evaluated here."
        ),
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    parser.add_argument("--out", default=None)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    data_dir = Path(args.data_dir)

    qqq = load_bars_from_csv(str(data_dir / "QQQ_5min.csv"))
    tqqq = load_bars_from_csv(str(data_dir / "TQQQ_5min.csv"))
    sqqq = load_bars_from_csv(str(data_dir / "SQQQ_5min.csv"))
    sessions, session_report = build_day_sessions(qqq, tqqq, sqqq)

    from stocks_advisory.tqqq_sqqq_backtest_v2 import run_backtest_v2

    v2_result = run_backtest_v2(sessions, V2_LANE1_CONFIG)

    report = {
        "sessions_built": len(sessions),
        "historical_module": _historical_module_diagnostics(sessions),
        "v2_lane1": {
            **_v2_lane_diagnostics(v2_result["trade_log"], "lane1"),
            "entry_mechanism": (
                "Decision made ONCE per day using ONLY the opening range + exactly "
                "1 confirming bar (paper_runner.py's decision_cutoff = "
                "len(opening_range)+1) -- if price is not ALREADY beyond the "
                "first-hour high/low with VWAP confirming at that single bar, the "
                "day is NO_TRADE. Never scans later bars in the day. Stop = QQQ "
                "closes back across VWAP (dynamic, re-checked every bar). Target = "
                "NONE (v1's decision engine never sets one; the position runs until "
                "VWAP invalidation or session end/EXPIRED). Sizing = floor shares. "
                "allowed_min/max_first_hour_range are ABSOLUTE DOLLAR thresholds "
                "(1.0-10.0), not percent-of-price like the historical module's "
                "min/max_opening_range_percent (0.1%-5.0%)."
            ),
        },
        "v2_lane2": _v2_lane_diagnostics(v2_result["trade_log"], "lane2"),
        "lane2_missed_vs_false": _lane2_missed_vs_false(sessions, V2_LANE1_CONFIG),
        "key_question": (
            "Did the market logic fail, or did v2 accidentally test a different "
            "Lane 1 than the one whose earlier results justified this project? "
            "See 'historical_module' vs 'v2_lane1' entry_mechanism fields above -- "
            "they are two independently-implemented engines with materially "
            "different entry timing (full-day scan vs. single-bar-only decision), "
            "exit philosophy (systematic 1R target vs. no target/VWAP-invalidation-"
            "only), and first-hour-range filter units (percent-of-price vs. "
            "absolute dollars). The historical module's +$4.08/trade result never "
            "characterized tqqq_sqqq_decision.py (the live paper-harness engine) "
            "-- it characterized a different, more permissive entry+exit design."
        ),
    }

    output = json.dumps(report, indent=2, default=str)
    if args.out:
        Path(args.out).write_text(output)
        print(f"Report written to {args.out}")
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
