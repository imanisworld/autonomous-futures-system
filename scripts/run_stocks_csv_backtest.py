#!/usr/bin/env python3
"""scripts/run_stocks_csv_backtest.py

Runs the merged Stock/ETF Backtest v1 (`stocks_advisory`) against real
QQQ/TQQQ/SQQQ intraday CSV files. Research/backtest only: reads the
three local file paths given on the command line, builds `DaySession`
objects via `stocks_advisory.csv_loader`, and runs the base backtest,
slippage stress, in-sample/out-of-sample split, and walk-forward test.
Prints a JSON report and exits -- no file is written, no order is
placed, no network call is made, no options/futures/broker code is
touched.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stocks_advisory.backtest_models import BacktestConfig
from stocks_advisory.csv_loader import build_day_sessions, load_bars_from_csv
from stocks_advisory.tqqq_sqqq_backtest import (
    DEFAULT_SLIPPAGE_STRESS_LEVELS,
    run_backtest,
    run_in_sample_out_of_sample,
    run_slippage_stress,
    run_walk_forward,
)


def _default_config() -> BacktestConfig:
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


def _summary_dict(summary) -> dict:
    d = dataclasses.asdict(summary)
    d.pop("trade_log", None)
    d.pop("skipped_days", None)
    d.pop("equity_curve", None)
    return d


def _full_trade_log(summary) -> list[dict]:
    return [dataclasses.asdict(t) for t in summary.trade_log]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qqq", required=True, help="Path to QQQ intraday CSV")
    parser.add_argument("--tqqq", required=True, help="Path to TQQQ intraday CSV")
    parser.add_argument("--sqqq", required=True, help="Path to SQQQ intraday CSV")
    parser.add_argument("--walk-forward-train", type=int, default=2)
    parser.add_argument("--walk-forward-test", type=int, default=1)
    parser.add_argument("--full-trade-log", action="store_true")
    args = parser.parse_args()

    qqq = load_bars_from_csv(args.qqq)
    tqqq = load_bars_from_csv(args.tqqq)
    sqqq = load_bars_from_csv(args.sqqq)
    sessions, report = build_day_sessions(qqq, tqqq, sqqq)

    config = _default_config()

    result: dict = {
        "input_validation": {
            "qqq": {"path": qqq.path, "rows_read": qqq.rows_read, "rth_bars": len(qqq.rth_bars), "rows_outside_regular_hours": qqq.rows_outside_regular_hours},
            "tqqq": {"path": tqqq.path, "rows_read": tqqq.rows_read, "rth_bars": len(tqqq.rth_bars), "rows_outside_regular_hours": tqqq.rows_outside_regular_hours},
            "sqqq": {"path": sqqq.path, "rows_read": sqqq.rows_read, "rth_bars": len(sqqq.rth_bars), "rows_outside_regular_hours": sqqq.rows_outside_regular_hours},
            "qqq_dates": report.qqq_dates,
            "tqqq_dates": report.tqqq_dates,
            "sqqq_dates": report.sqqq_dates,
            "common_dates": report.common_dates,
            "sessions_built": report.sessions_built,
            "excluded_dates": report.excluded_dates,
        },
        "config": dataclasses.asdict(config),
    }

    if len(sessions) < 2:
        result["error"] = f"only {len(sessions)} session(s) buildable; nothing meaningful to backtest"
        print(json.dumps(result, indent=2, default=str))
        return 1

    base_summary = run_backtest(sessions, config)
    result["base_backtest"] = _summary_dict(base_summary)
    if args.full_trade_log:
        result["base_backtest"]["trade_log"] = _full_trade_log(base_summary)

    slippage_report = run_slippage_stress(sessions, config, levels=DEFAULT_SLIPPAGE_STRESS_LEVELS)
    result["slippage_stress"] = {
        "points": [
            {"slippage_percent": p.slippage_percent, "summary": _summary_dict(p.summary)}
            for p in slippage_report.points
        ],
        "only_profitable_at_zero_slippage": slippage_report.only_profitable_at_zero_slippage(),
    }

    io_result = run_in_sample_out_of_sample(sessions, config)
    result["in_sample_out_of_sample"] = {
        "in_sample_session_count": io_result.in_sample_session_count,
        "out_of_sample_session_count": io_result.out_of_sample_session_count,
        "split_date": io_result.split_date,
        "in_sample_summary": _summary_dict(io_result.in_sample_summary),
        "out_of_sample_summary": _summary_dict(io_result.out_of_sample_summary),
    }

    wf_result = run_walk_forward(
        sessions, config, train_size=args.walk_forward_train, test_size=args.walk_forward_test
    )
    result["walk_forward"] = {
        "fold_count": len(wf_result.folds),
        "folds": [
            {
                "fold_index": f.fold_index,
                "train_start_date": f.train_start_date,
                "train_end_date": f.train_end_date,
                "test_start_date": f.test_start_date,
                "test_end_date": f.test_end_date,
                "train_summary": _summary_dict(f.train_summary),
                "test_summary": _summary_dict(f.test_summary),
            }
            for f in wf_result.folds
        ],
    }

    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
