#!/usr/bin/env python3
"""Read-only audit of existing MNQ LONG families on sustained-trend capture.

Preregistered in docs/prereg-mnq-sustained-trend-existing-strategy-audit-2026-09-22.md.
This script changes no strategy/runtime configuration and never calls an external broker.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict, deque
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import load_config
from execution.broker_interface import BracketOrder
from execution.paper_broker import NextBarOHLC, PaperBroker
from replay.candle_loader import ReplayCandleLoader
from replay.replay_engine import ReplayEngine
from strategy.shadow_setups import ShadowSetupCandidate, evaluate_shadow_setups


INSTRUMENT = "MNQ"
FAMILIES = (
    "ema_pullback_trend",
    "impulse_first_pullback_observed",
    "strat_22_continuation_observed",
    "trend_consolidation_break_observed",
)
SLIPPAGE_TICKS = 1.0
IOC_TOLERANCE_TICKS = 32.0
ROUND_TURN_COMMISSION = 1.48
MAX_TRADES_PER_DAY = 3
PF_HURDLE = 1.94
TERMINAL = {"WIN", "LOSS", "BREAKEVEN"}


def _file_date(path: Path) -> str:
    token = path.stem[-10:]
    date.fromisoformat(token)  # fail closed on an ambiguous corpus filename
    return token


def _bar_dict(candle) -> dict[str, Any]:
    return {
        "ts": candle.timestamp,
        "open": candle.open,
        "high": candle.high,
        "low": candle.low,
        "close": candle.close,
        "volume": candle.volume,
    }


def _geometry_key(candidate: ShadowSetupCandidate) -> tuple[Any, ...]:
    return (
        candidate.strategy,
        candidate.direction,
        float(candidate.entry),
        float(candidate.stop),
        float(candidate.target),
    )


def _broker() -> PaperBroker:
    return PaperBroker(
        starting_balance=100_000.0,
        slippage_ticks=SLIPPAGE_TICKS,
        pessimistic_both_hit=True,
        breakeven_at_1r=False,
        runner_mode=False,
        entry_fill_model="ioc_limit",
        entry_tolerance_ticks_by_root={INSTRUMENT: IOC_TOLERANCE_TICKS},
    )


def _order(candidate: ShadowSetupCandidate) -> BracketOrder:
    return BracketOrder(
        instrument=INSTRUMENT,
        direction=candidate.direction,
        entry=float(candidate.entry),
        stop=float(candidate.stop),
        target=float(candidate.target),
        rr_ratio=float(candidate.rr_ratio),
        strategy=candidate.strategy,
        contracts=1,
        post_fill_validation_required=False,
    )


def _max_drawdown(pnls: list[float]) -> float:
    equity = peak = drawdown = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return round(drawdown, 2)


def _profit_factor(pnls: list[float]) -> float | None:
    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = -sum(p for p in pnls if p < 0)
    if gross_loss <= 0:
        return None
    return round(gross_profit / gross_loss, 4)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    terminal = [r for r in rows if r.get("result") in TERMINAL]
    terminal = sorted(terminal, key=lambda r: (r["date"], str(r.get("exit_ts") or "")))
    pnls = [float(r["net_pnl"]) for r in terminal]
    net = round(sum(pnls), 2)
    pf = _profit_factor(pnls)

    by_day: dict[str, float] = defaultdict(float)
    for row in terminal:
        by_day[row["date"]] += float(row["net_pnl"])
    best_day = max(by_day.items(), key=lambda kv: kv[1]) if by_day else (None, 0.0)
    positive_days = sorted((p for p in by_day.values() if p > 0), reverse=True)
    top3 = sum(positive_days[:3])
    top3_share = round(top3 / net, 4) if net > 0 else None

    filled = [r for r in rows if r.get("actual_entry") is not None]
    return {
        "candidates": len(rows),
        "fills": len(filled),
        "no_fills": sum(r.get("result") == "NO_FILL" for r in rows),
        "busy_skips": sum(r.get("result") == "SKIPPED_BUSY" for r in rows),
        "max_trade_skips": sum(r.get("result") == "SKIPPED_MAX_TRADES" for r in rows),
        "terminal": len(terminal),
        "open_eod": sum(r.get("result") == "OPEN" for r in rows),
        "wins": sum(r.get("result") == "WIN" for r in terminal),
        "losses": sum(r.get("result") == "LOSS" for r in terminal),
        "breakevens": sum(r.get("result") == "BREAKEVEN" for r in terminal),
        "economic_positive": sum(float(r["net_pnl"]) > 0 for r in terminal),
        "economic_negative": sum(float(r["net_pnl"]) < 0 for r in terminal),
        "net_pnl": net,
        "expectancy_per_terminal": round(net / len(terminal), 2) if terminal else None,
        "profit_factor": pf,
        "profit_factor_infinite": bool(pnls and pf is None and any(p > 0 for p in pnls)),
        "max_drawdown": _max_drawdown(pnls),
        "distinct_filled_days": len({r["date"] for r in filled}),
        "best_day": {"date": best_day[0], "net_pnl": round(best_day[1], 2)},
        "leave_best_day_out_net": round(net - best_day[1], 2) if by_day else None,
        "top3_positive_day_contribution_to_net": top3_share,
        "clears_pf_1_94": bool(pf is not None and pf >= PF_HURDLE),
    }


def research_gate(all_summary: dict[str, Any], first: dict[str, Any], second: dict[str, Any]) -> str:
    checks = (
        int(all_summary["terminal"]) >= 30,
        float(all_summary["net_pnl"]) > 0,
        all_summary["profit_factor"] is not None
        and float(all_summary["profit_factor"]) >= PF_HURDLE,
        float(first["net_pnl"]) > 0,
        float(second["net_pnl"]) > 0,
        all_summary["leave_best_day_out_net"] is not None
        and float(all_summary["leave_best_day_out_net"]) > 0,
    )
    return "PROMISING_BUT_UNPROVEN" if all(checks) else "DOES_NOT_CLEAR_RESEARCH_GATE"


def run(candle_dir: Path) -> dict[str, Any]:
    files = sorted(candle_dir.glob("*.jsonl"))
    if not files:
        raise RuntimeError(f"no .jsonl candle files found in {candle_dir}")

    dates = [_file_date(path) for path in files]
    midpoint = dates[len(dates) // 2]

    config = load_config()
    engine = ReplayEngine(config=config, log_dir="/tmp/mnq-trend-day-existing-strategy-audit")
    loader = ReplayCandleLoader()
    history: deque[dict[str, Any]] = deque(maxlen=8)
    previous = None
    previous_file_date: date | None = None
    rows: dict[str, list[dict[str, Any]]] = {family: [] for family in FAMILIES}

    for path, day_text in zip(files, dates):
        day = date.fromisoformat(day_text)
        if previous_file_date is not None and (day - previous_file_date).days >= 3:
            history.clear()
        previous_file_date = day

        candles = loader.load_jsonl(path)
        brokers = {family: _broker() for family in FAMILIES}
        active: dict[str, dict[str, Any] | None] = {family: None for family in FAMILIES}
        filled_today = {family: 0 for family in FAMILIES}
        seen = {family: set() for family in FAMILIES}

        for candle in candles:
            # Resolve positions opened on PRIOR bars before considering a new
            # decision-close entry on this bar. This forbids same-bar hindsight.
            for family in FAMILIES:
                row = active[family]
                if row is None:
                    continue
                fill = brokers[family]._resolve_position_impl(  # research: bypass mirror hook
                    NextBarOHLC(high=candle.high, low=candle.low, open=candle.open)
                )
                if fill is None:
                    continue
                gross = float(fill.pnl_dollars or 0.0)
                row.update(
                    result=str(fill.result),
                    exit_reason=fill.exit_reason,
                    exit_price=fill.exit_price,
                    exit_ts=candle.timestamp,
                    gross_pnl=round(gross, 2),
                    commission=ROUND_TURN_COMMISSION,
                    net_pnl=round(gross - ROUND_TURN_COMMISSION, 2),
                )
                active[family] = None

            history.append(_bar_dict(candle))
            state = engine._market_state_from_candle(candle, previous)
            previous = candle
            root = str(state.instrument or "").upper().replace("1!", "")
            if root != INSTRUMENT:
                continue

            candidates = evaluate_shadow_setups(state, list(history), config)
            for candidate in candidates:
                family = candidate.strategy
                if family not in rows or str(candidate.direction).upper() != "LONG":
                    continue

                key = _geometry_key(candidate)
                if key in seen[family]:
                    continue
                seen[family].add(key)

                row = {
                    "date": day_text,
                    "decision_ts": candle.timestamp,
                    "strategy": family,
                    "direction": "LONG",
                    "planned_entry": float(candidate.entry),
                    "stop": float(candidate.stop),
                    "target": float(candidate.target),
                    "planned_rr": float(candidate.rr_ratio),
                    "decision_close": float(candle.close),
                    "actual_entry": None,
                    "result": None,
                    "exit_reason": None,
                    "exit_ts": None,
                    "gross_pnl": None,
                    "commission": None,
                    "net_pnl": None,
                }
                rows[family].append(row)

                if active[family] is not None:
                    row["result"] = "SKIPPED_BUSY"
                    continue
                if filled_today[family] >= MAX_TRADES_PER_DAY:
                    row["result"] = "SKIPPED_MAX_TRADES"
                    continue

                fill = brokers[family]._execute_bracket_impl(  # research: bypass mirror hook
                    _order(candidate), market_price=float(candle.close)
                )
                if fill.result != "OPEN":
                    row.update(
                        result="NO_FILL",
                        exit_reason=fill.exit_reason or fill.no_fill_reason or fill.result,
                        actual_entry=float(fill.entry_price),
                    )
                    # CANCELLED is not a fill even though PaperBroker reports the
                    # planned/attempted entry_price for audit identity.
                    row["actual_entry"] = None
                    continue

                row["actual_entry"] = float(fill.entry_price)
                row["result"] = "OPEN"
                row["paper_order_id"] = fill.paper_order_id
                active[family] = row
                filled_today[family] += 1

        # Same-day horizon is deliberate. Anything still open remains OPEN with
        # no invented EOD mark or terminal P&L.
        for family in FAMILIES:
            if active[family] is not None:
                active[family]["exit_reason"] = "EOD_OPEN"

    output: dict[str, Any] = {
        "study": "mnq_sustained_trend_existing_strategy_audit_v1",
        "mode": "RESEARCH_ONLY",
        "input": {
            "candle_dir": str(candle_dir),
            "files": len(files),
            "first_date": dates[0],
            "last_date": dates[-1],
            "midpoint_date": midpoint,
        },
        "frozen_rules": {
            "instrument": INSTRUMENT,
            "direction": "LONG",
            "families": list(FAMILIES),
            "max_trades_per_day": MAX_TRADES_PER_DAY,
            "one_position_at_a_time_per_family": True,
            "entry_fill_model": "ioc_limit",
            "ioc_tolerance_ticks": IOC_TOLERANCE_TICKS,
            "slippage_ticks": SLIPPAGE_TICKS,
            "round_turn_commission": ROUND_TURN_COMMISSION,
            "pessimistic_both_hit": True,
            "same_bar_resolution": False,
            "profit_factor_hurdle": PF_HURDLE,
        },
        "families": {},
    }

    for family in FAMILIES:
        selected = rows[family]
        first_rows = [r for r in selected if r["date"] < midpoint]
        second_rows = [r for r in selected if r["date"] >= midpoint]
        all_summary = summarize(selected)
        first_summary = summarize(first_rows)
        second_summary = summarize(second_rows)
        output["families"][family] = {
            "all": all_summary,
            "first_half": first_summary,
            "second_half": second_summary,
            "research_gate": research_gate(all_summary, first_summary, second_summary),
        }
    return output


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candles", type=Path, required=True, help="full MNQ 15m corpus directory")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    report = run(args.candles)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
