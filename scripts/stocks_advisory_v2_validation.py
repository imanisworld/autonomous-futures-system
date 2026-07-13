#!/usr/bin/env python3
"""scripts/stocks_advisory_v2_validation.py

Historical validation for Stock/ETF Strategy v2.0 (the two-lane
QQQ->TQQQ/SQQQ continuation strategy in
stocks_advisory/tqqq_sqqq_backtest_v2.py). Runs ONCE against the
existing, already-fetched, unmodified 370-session historical dataset
(data/stocks_advisory_polygon_5m/) -- no new data fetch. Prints Lane 1,
Lane 2, and combined summaries side by side so the operator can decide
freeze vs. reject: this script asserts nothing about whether v2 is an
improvement, it only reports the numbers.

Research/backtest only: reads local CSV paths, makes no network call,
writes no file except the JSON report to stdout (or --out).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stocks_advisory.backtest_models import BacktestSummary
from stocks_advisory.csv_loader import build_day_sessions, load_bars_from_csv
from stocks_advisory.tqqq_sqqq_backtest_v2 import V2Config, run_backtest_v2

DEFAULT_DATA_DIR = "data/stocks_advisory_polygon_5m"

# The frozen, actual paper-proof-window thresholds (see
# data/stocks_advisory_paper_proof/PROOF_MANIFEST.md) -- Lane 1 must use
# these exact values so this validation reflects the real engine, not a
# tuned variant of it.
DEFAULT_CONFIG = V2Config(
    allowed_max_gap_percent=2.0,
    allowed_min_first_hour_range=1.0,
    allowed_max_first_hour_range=10.0,
)


def _summary_to_dict(summary: BacktestSummary) -> dict:
    d = dataclasses.asdict(summary)
    # Trade-level detail is huge and not needed in the headline report --
    # counts/reasons are kept, the per-trade log itself is dropped here.
    d.pop("trade_log", None)
    d.pop("equity_curve", None)
    d.pop("skipped_days", None)
    return d


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    parser.add_argument("--out", default=None, help="Write JSON report here instead of stdout")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    data_dir = Path(args.data_dir)

    qqq = load_bars_from_csv(str(data_dir / "QQQ_5min.csv"))
    tqqq = load_bars_from_csv(str(data_dir / "TQQQ_5min.csv"))
    sqqq = load_bars_from_csv(str(data_dir / "SQQQ_5min.csv"))
    sessions, session_report = build_day_sessions(qqq, tqqq, sqqq)

    result = run_backtest_v2(sessions, DEFAULT_CONFIG)

    combined = result["combined"]
    lane1 = result["lane1"]
    lane2 = result["lane2"]

    lane2_real_trades = [t for t in result["trade_log"] if t.lane == "lane2" and not t.skipped]
    continuation_extended_days = sum(
        1 for t in result["trade_log"]
        if t.lane == "lane2" and t.skipped_reason == "NO_TRADE: CONTINUATION_EXTENDED"
    )

    report = {
        "data_dir": str(data_dir),
        "sessions_built": len(sessions),
        "sessions_excluded": len(session_report.excluded_dates),
        "config": dataclasses.asdict(DEFAULT_CONFIG),
        "lane1_only": _summary_to_dict(lane1),
        "lane2_only": _summary_to_dict(lane2),
        "combined": _summary_to_dict(combined),
        "lane2_real_trade_count": len(lane2_real_trades),
        "lane2_continuation_extended_day_count": continuation_extended_days,
        "note": (
            "This report does not recommend freeze/reject -- compare "
            "lane2_only against lane1_only and combined, and reject v2 if "
            "Lane 2 only helps a specific known day but degrades or adds "
            "no edge across this wider sample, per the operator's own "
            "stated validation requirement."
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
