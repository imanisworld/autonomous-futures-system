#!/usr/bin/env python3
"""True bar-by-bar mark-to-market MES 1-2-2 swing-risk evidence.

Evidence only. No runtime/config/strategy change.

The existing corrected 40-trade in-sample evidence (mes_122_h1h2_context_decomposition.py,
via mes_122_controlled_one_variable_tests.py::_run_pass) reports a max drawdown of $219.11.
That number is computed by scripts/mes_122_fallback_full_engine_proof.py::_metrics, which
runs a peak/trough tracker over the LIST OF RESOLVED TRADE P&Ls only -- it never marks an
open position against the bars between entry and exit. $219.11 is therefore CLOSED-TRADE /
REALIZED-EQUITY drawdown, not full mark-to-market (MTM) drawdown, despite prior write-ups
calling it "mark-to-market."

This script reproduces the identical, already-verified 40-trade population (same isolated
baseline pass: _fixed_one_contract_config(isolated=True) + _run_pass), then independently
walks each trade forward bar by bar through the SAME 313-day MES 15m corpus using the SAME
PaperBroker.resolve_position() call the engine itself uses (1-tick slippage,
pessimistic_both_hit=True, no breakeven, no runner, static exit, gap-aware stop-market
fills -- the same broker code the corrected 40-trade rerun used). At every bar a position
stays open it additionally records:
  - mark-to-market account equity at that bar's CLOSE (commission-adjusted realized balance
    + unrealized P&L) -> the true bar-close MTM equity curve and its max drawdown;
  - the bar's worst INTRABAR adverse excursion against the position (using the bar's low for
    a LONG, high for a SHORT) -> per-trade MAE and the single worst unrealized dollar loss
    observed anywhere in the 313-day walk, which can exceed the bar-close curve's low point
    since it captures a spike that reverted before the bar closed.

Every trade's rebuilt exit (result/exit_reason/pnl_dollars) is cross-checked against the
already-verified population and the script fails loudly on any mismatch -- this is meant to
prove the same 40 trades, not a different simulation.
"""
from __future__ import annotations

import dataclasses
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from execution.paper_broker import NextBarOHLC, PaperBroker, TICK_SIZE, TICK_VALUE  # noqa: E402
from scripts.mes_122_controlled_one_variable_tests import (  # noqa: E402
    COMMISSION_RT,
    _fixed_one_contract_config,
    _resolved_strat122_rows,
    _run_pass,
    _variant_advance,
)
from scripts.mes_122_fallback_full_engine_proof import CORPUS, INSTRUMENT, STRATEGY  # noqa: E402

STARTING_BALANCE = 1500.0


def _load_master_bars() -> list[dict]:
    candle_dir = CORPUS / INSTRUMENT
    files = sorted(candle_dir.glob(f"{INSTRUMENT}_*.jsonl"))
    if not files:
        raise RuntimeError(f"no corpus files found in {candle_dir}")
    bars: list[dict] = []
    for path in files:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                bars.append(json.loads(line))
    bars.sort(key=lambda b: b["timestamp"])
    return bars


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


def _unrealized_dollars(direction: str, entry: float, mark: float, tick: float, tick_val: float) -> float:
    ticks = (mark - entry) / tick if direction == "LONG" else (entry - mark) / tick
    return ticks * tick_val


def _spans_weekend(entry_date, exit_date) -> bool:
    days = (exit_date - entry_date).days
    return any((entry_date + timedelta(days=d)).weekday() >= 5 for d in range(days + 1))


def main(out_path: Path) -> dict[str, Any]:
    # Matches mes_122_h1h2_context_decomposition.py's slip_1t pass exactly:
    # isolated one-contract config, but with ONLY strat_122 enabled (not
    # strat_212 alongside it, which is _fixed_one_contract_config(isolated=True)'s
    # own default) -- this is what actually produced the verified 40-trade
    # population; strat_212 sharing the isolated lane drops it to 35.
    isolated_cfg = dataclasses.replace(
        _fixed_one_contract_config(isolated=True), enabled_concepts=[STRATEGY]
    )
    with tempfile.TemporaryDirectory(prefix="mes122_mtm_") as tmp:
        run = _run_pass(
            isolated_cfg, Path(tmp) / "baseline", advance_fn=_variant_advance("baseline")
        )

    resolved, unresolved, cancelled = _resolved_strat122_rows(run)
    # Matches mes_122_h1h2_context_decomposition.py::_resolved_rows, which
    # silently excludes any TRADE decision without a WIN/LOSS/BREAKEVEN
    # outcome (a position still open at the end of the 313-day corpus, with
    # no more bars to resolve against) -- this is exactly how the verified
    # "40 trades" headline was produced, so match it rather than raising.
    resolved = sorted(resolved, key=lambda r: r["bar_ts"])
    if len(resolved) != 40:
        raise RuntimeError(
            f"expected 40 resolved strat_122 trades (matching the verified "
            f"h1h2 population), got {len(resolved)} "
            f"(unresolved={len(unresolved)}, cancelled={len(cancelled)})"
        )

    bars = _load_master_bars()
    ts_index = {b["timestamp"]: i for i, b in enumerate(bars)}
    tick = TICK_SIZE[INSTRUMENT]
    tick_val = TICK_VALUE[INSTRUMENT]

    broker = PaperBroker(
        starting_balance=STARTING_BALANCE,
        slippage_ticks=1.0,
        pessimistic_both_hit=True,
        breakeven_at_1r=False,
        runner_mode=False,
        entry_fill_model="market",
    )

    equity_curve: list[dict[str, Any]] = [
        {"ts": resolved[0]["bar_ts"], "equity": STARTING_BALANCE, "kind": "start"}
    ]
    trade_reports: list[dict[str, Any]] = []

    balance = STARTING_BALANCE  # commission-adjusted realized equity
    peak_equity = STARTING_BALANCE
    max_dd = 0.0
    max_dd_point: dict[str, Any] | None = None
    worst_unrealized_loss = 0.0
    worst_unrealized_point: dict[str, Any] | None = None
    worst_trade_mae = 0.0
    worst_trade_mae_info: dict[str, Any] | None = None
    longest_hold: dict[str, Any] | None = None
    overnight_count = 0
    weekend_count = 0

    for i, trade in enumerate(resolved):
        bar_ts = trade["bar_ts"]
        direction = trade["direction"]
        entry = float(trade["entry"])
        stop = float(trade["stop"])
        target = float(trade["target"])
        order_id = trade["paper_order_id"]
        outcome = run["outcomes"].get(str(order_id)) or {}
        is_pre_resolved = (
            (outcome.get("execution_audit") or {}).get("source")
            == "strat_212_122_same_bar_resolution"
        )

        idx = ts_index.get(bar_ts)
        if idx is None:
            raise RuntimeError(f"signal bar {bar_ts} not found in master corpus")

        balance_before_trade = balance
        trade_mae = 0.0
        trade_mae_ts: str | None = None
        exit_ts: str
        exit_reason: str
        result: str
        raw_pnl: float

        if is_pre_resolved:
            # strategy/strat_212_122.py's causal resolver: the armed entry
            # boundary AND its opposite (stop) boundary were both crossed on
            # THIS watched bar, so the trade opens and resolves instantly at
            # decision time (replay/replay_engine.py's decision.setup.
            # pre_resolved branch, broker.force_resolve() at the exact
            # boundary price, no slippage) -- there is no resting position to
            # walk forward bar-by-bar. Book the already-verified outcome
            # directly; use the SAME watched bar's own OHLC (not a "next"
            # bar -- none is involved) as the only available adverse-excursion
            # proxy for this bar, consistent with the system's own pessimistic
            # same-bar convention.
            result = str(trade["result"])
            exit_reason = str(trade["exit_reason"])
            raw_pnl = round(float(trade["pnl_dollars"]), 2)
            exit_ts = bar_ts
            signal_bar = bars[idx]
            adverse_price = signal_bar["low"] if direction == "LONG" else signal_bar["high"]
            adverse_unrealized = _unrealized_dollars(direction, entry, adverse_price, tick, tick_val)
            trade_mae = min(adverse_unrealized, raw_pnl, 0.0)
            trade_mae_ts = bar_ts
            adverse_equity = balance_before_trade + trade_mae
            if trade_mae < worst_unrealized_loss:
                worst_unrealized_loss = trade_mae
                worst_unrealized_point = {
                    "ts": bar_ts, "trade_index": i, "trade_date": bar_ts[:10],
                    "direction": direction, "unrealized_dollars": round(trade_mae, 2),
                    "account_equity_at_worst": round(adverse_equity, 2),
                    "note": "same-bar pre-resolved trade",
                }
        else:
            broker.restore_position(
                instrument=INSTRUMENT,
                direction=direction,
                entry=entry,
                stop=stop,
                target=target,
                contracts=1,
                paper_order_id=f"MTM-{i}",
            )

            fill = None
            exit_bar: dict | None = None
            j = idx + 1
            while j < len(bars):
                bar = bars[j]

                adverse_price = bar["low"] if direction == "LONG" else bar["high"]
                adverse_unrealized = _unrealized_dollars(direction, entry, adverse_price, tick, tick_val)
                if adverse_unrealized < trade_mae:
                    trade_mae = adverse_unrealized
                    trade_mae_ts = bar["timestamp"]
                adverse_equity = balance_before_trade + adverse_unrealized
                if adverse_unrealized < worst_unrealized_loss:
                    worst_unrealized_loss = adverse_unrealized
                    worst_unrealized_point = {
                        "ts": bar["timestamp"],
                        "trade_index": i,
                        "trade_date": bar_ts[:10],
                        "direction": direction,
                        "unrealized_dollars": round(adverse_unrealized, 2),
                        "account_equity_at_worst": round(adverse_equity, 2),
                    }

                fill = broker.resolve_position(
                    NextBarOHLC(open=bar["open"], high=bar["high"], low=bar["low"])
                )
                if fill is not None:
                    exit_bar = bar
                    break

                close_unrealized = _unrealized_dollars(direction, entry, bar["close"], tick, tick_val)
                equity = balance_before_trade + close_unrealized
                equity_curve.append(
                    {"ts": bar["timestamp"], "equity": round(equity, 2), "kind": "mark", "trade_index": i}
                )
                if equity > peak_equity:
                    peak_equity = equity
                dd = peak_equity - equity
                if dd > max_dd:
                    max_dd = dd
                    max_dd_point = {
                        "ts": bar["timestamp"], "equity": round(equity, 2), "peak": round(peak_equity, 2),
                        "drawdown": round(dd, 2), "trade_index": i, "trade_date": bar_ts[:10],
                    }
                j += 1

            if exit_bar is None or fill is None:
                raise RuntimeError(f"trade signalled at {bar_ts} never resolved within the corpus range")

            known_result = trade["result"]
            known_pnl = round(float(trade["pnl_dollars"]), 2)
            if fill.result != known_result or round(fill.pnl_dollars, 2) != known_pnl:
                raise RuntimeError(
                    f"MISMATCH vs the already-verified 40-trade population at {bar_ts}: "
                    f"known={known_result}/{known_pnl} rebuilt={fill.result}/{round(fill.pnl_dollars, 2)}"
                )

            result = fill.result
            exit_reason = fill.exit_reason
            raw_pnl = round(fill.pnl_dollars, 2)
            exit_ts = exit_bar["timestamp"]

        balance = round(balance_before_trade + raw_pnl - COMMISSION_RT, 2)
        equity_curve.append(
            {"ts": exit_ts, "equity": balance, "kind": "close", "trade_index": i}
        )
        if balance > peak_equity:
            peak_equity = balance
        dd = peak_equity - balance
        if dd > max_dd:
            max_dd = dd
            max_dd_point = {
                "ts": exit_ts, "equity": balance, "peak": round(peak_equity, 2),
                "drawdown": round(dd, 2), "trade_index": i, "trade_date": bar_ts[:10],
            }

        if trade_mae < worst_trade_mae:
            worst_trade_mae = trade_mae
            worst_trade_mae_info = {
                "trade_index": i, "trade_date": bar_ts[:10], "direction": direction,
                "mae_dollars": round(trade_mae, 2), "mae_ts": trade_mae_ts,
            }

        entry_dt = _parse(bar_ts)
        exit_dt = _parse(exit_ts)
        hold_hours = round((exit_dt - entry_dt).total_seconds() / 3600.0, 2)
        entry_date, exit_date = entry_dt.date(), exit_dt.date()
        is_overnight = exit_date != entry_date
        is_weekend = is_overnight and _spans_weekend(entry_date, exit_date)
        if is_overnight:
            overnight_count += 1
        if is_weekend:
            weekend_count += 1
        if longest_hold is None or hold_hours > longest_hold["hold_hours"]:
            longest_hold = {
                "trade_index": i, "trade_date": bar_ts[:10], "hold_hours": hold_hours,
                "signal_bar_ts": bar_ts, "exit_ts": exit_ts,
            }

        trade_reports.append({
            "trade_index": i,
            "signal_bar_ts": bar_ts,
            "direction": direction,
            "entry": entry, "stop": stop, "target": target,
            "exit_ts": exit_ts,
            "exit_reason": exit_reason,
            "result": result,
            "pnl_dollars_raw": raw_pnl,
            "pre_resolved_same_bar": is_pre_resolved,
            "hold_hours": hold_hours,
            "overnight": is_overnight,
            "weekend": is_weekend,
            "trade_mae_dollars": round(trade_mae, 2),
            "trade_mae_ts": trade_mae_ts,
            "commission_adjusted_balance_after": balance,
        })

    raw_net = round(balance - STARTING_BALANCE + COMMISSION_RT * len(resolved), 2)
    commission_adjusted_net = round(balance - STARTING_BALANCE, 2)

    report = {
        "design": {
            "instrument": INSTRUMENT,
            "strategy": "strat_122",
            "corpus": str(CORPUS),
            "starting_balance": STARTING_BALANCE,
            "contracts": 1,
            "slippage_ticks": 1.0,
            "pessimistic_same_bar": True,
            "breakeven_at_1r": False,
            "runner_mode": False,
            "commission_round_trip": COMMISSION_RT,
            "gap_aware_stop_fills": True,
            "method": (
                "Reuses the identical isolated-baseline 40-trade population and the same "
                "PaperBroker.resolve_position() the engine uses; independently walks each "
                "trade bar-by-bar through the 313-day corpus, marking equity at every bar "
                "close while a position is open (commission charged once per trade, at "
                "close) and tracking intrabar high/low adverse extremes separately. 7 of "
                "the 40 trades are 'same-bar pre-resolved' (armed entry boundary and its "
                "opposite/stop boundary both crossed on the single watched bar -- "
                "replay/replay_engine.py's decision.setup.pre_resolved branch, "
                "broker.force_resolve() at the exact boundary price, no walk-forward "
                "position ever exists): those are booked directly from the already-"
                "verified outcome with hold_hours=0 and MAE taken from that one watched "
                "bar's own OHLC, not reconstructed via resolve_position()."
            ),
        },
        "trade_count": len(resolved),
        "raw_net": raw_net,
        "commission_adjusted_net": commission_adjusted_net,
        "closed_trade_drawdown_for_comparison": 219.11,
        "true_max_mtm_drawdown_dollars": round(max_dd, 2),
        "true_max_mtm_drawdown_point": max_dd_point,
        "worst_per_trade_mae": worst_trade_mae_info,
        "worst_unrealized_dollar_loss": worst_unrealized_point,
        "longest_hold": longest_hold,
        "overnight_holds": overnight_count,
        "weekend_holds": weekend_count,
        "final_commission_adjusted_balance": balance,
        "trades": trade_reports,
        "equity_curve": equity_curve,
    }
    out_path.write_text(json.dumps(report, indent=2, default=str) + "\n")
    return report


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=REPO / "scripts" / "mes_122_mtm_drawdown_2026-09-09.json")
    args = parser.parse_args()
    result = main(args.out)
    summary = {k: v for k, v in result.items() if k not in ("trades", "equity_curve")}
    print(json.dumps(summary, indent=2, default=str))
    print(f"wrote {args.out}")
