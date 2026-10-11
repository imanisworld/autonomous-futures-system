#!/usr/bin/env python3
"""NR7 + Inside Day (ChartArt NR7ID Pine) — exploratory MNQ research, offline only.

Capital-unconstrained scoring: valid structural stop only (stop below next open).
Production / Track B risk caps are not applied here.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
ET = ZoneInfo("America/New_York")
CORPUS = REPO / "data/replay_polygon_v2/MNQ"
MNQ_SEAL_START = date(2026, 6, 29)
START = date(2024, 10, 1)
SMA_LEN = 14
TICK = 0.25
TICK_VALUE = 0.50
POINT_VALUE = 2.0
COMMISSION_RT = 1.48
SLIP_TICKS = 1.0
FULL_RTH_BARS = 27  # 09:30 .. 16:00 inclusive on 15m
MIN_TRADES_CREDIBLE = 20

FROZEN_EXPLORATORY_SPEC = {
    "trial": "AFS-NR7ID-MNQ-EXPLORATORY-2026-10-11",
    "evidence_class": "exploratory_historical_pre_seal_not_forward_validation",
    "signal_source": "ChartArt NR7ID Pine (vault Narrow-Range-Inside-Day-Breakout-Strategy.md)",
    "signal_rules": {
        "nr7": "range < range[1..6]",
        "inside_day": "high < high[1] and low > low[1]",
        "bullish": "open < close",
        "sma": "SMA(close,14) rising vs prior session",
        "direction": "long_only",
    },
    "entry": "Next RTH session first 15m bar open + 1 adverse tick",
    "stop": "Signal-day RTH low; 15m stop-first; gap through stop at open - 1 tick",
    "exit": (
        "Pine: strategy.close(long, when=open<close). Causal: bullish day confirmed at "
        "session close; fill at next RTH session first 15m open - 1 adverse tick"
    ),
    "costs": {"commission_rt_usd": COMMISSION_RT, "slippage_ticks": SLIP_TICKS},
    "population": "All NR7ID signals with protective stop below next-session entry; invalid geometry excluded",
    "positioning": "One contract, non-overlapping (skip signal if already in trade)",
    "data_end_inclusive": "2026-06-26",
    "seal": f"No bar dates >= {MNQ_SEAL_START.isoformat()}",
}


def parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def trading_day(dt: datetime) -> date:
    return (dt.astimezone(ET) + timedelta(hours=6)).date()


def in_rth(dt: datetime) -> bool:
    et = dt.astimezone(ET)
    t = et.time()
    return time(9, 30) <= t <= time(16, 0)


@dataclass
class DailyBar:
    day: date
    open: float
    high: float
    low: float
    close: float
    bar_count: int
    last_bar_time: time


@dataclass
class Signal:
    day: date
    low: float
    high: float
    open: float
    close: float
    stop_distance_ticks: float
    valid_stop: bool


@dataclass
class Trade:
    signal_day: date
    entry_day: date
    entry_px: float
    exit_day: date
    exit_px: float
    exit_reason: str
    pnl: float
    stop_ticks: float
    stop_usd: float
    mae_usd: float = 0.0


def load_rth_bars() -> list[dict]:
    rows: list[dict] = []
    for path in sorted(CORPUS.glob("MNQ_*.jsonl")):
        file_day = date.fromisoformat(path.stem.split("_", 1)[1])
        if file_day >= MNQ_SEAL_START or file_day < START:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            dt = parse_dt(row["timestamp"])
            if not in_rth(dt):
                continue
            row["_dt"] = dt
            row["_td"] = trading_day(dt)
            rows.append(row)
    rows.sort(key=lambda r: r["_dt"])
    return rows


def build_daily(bars: list[dict]) -> list[DailyBar]:
    by_day: dict[date, list[dict]] = defaultdict(list)
    for row in bars:
        by_day[row["_td"]].append(row)
    out: list[DailyBar] = []
    for day in sorted(by_day):
        chunk = sorted(by_day[day], key=lambda r: r["_dt"])
        last_et = chunk[-1]["_dt"].astimezone(ET).time()
        out.append(
            DailyBar(
                day=day,
                open=float(chunk[0]["open"]),
                high=max(float(r["high"]) for r in chunk),
                low=min(float(r["low"]) for r in chunk),
                close=float(chunk[-1]["close"]),
                bar_count=len(chunk),
                last_bar_time=last_et,
            )
        )
    return out


def calendar_audit(daily: list[DailyBar]) -> dict:
    short = [
        {
            "day": d.day.isoformat(),
            "bars": d.bar_count,
            "last_bar_et": d.last_bar_time.isoformat(),
        }
        for d in daily
        if d.bar_count != FULL_RTH_BARS
    ]
    known = {
        "2025-06-19": "Juneteenth (short)",
        "2026-06-19": "Juneteenth (short)",
        "2025-01-20": "MLK (short)",
        "2024-11-28": "Thanksgiving (short)",
    }
    for row in short:
        row["note"] = known.get(row["day"])
    return {
        "full_rth_sessions": sum(1 for d in daily if d.bar_count == FULL_RTH_BARS),
        "short_or_holiday_sessions": len(short),
        "short_session_examples": short[:25],
        "short_session_total_listed": len(short),
    }


def sma(values: list[float], end_idx: int, length: int) -> float | None:
    if end_idx + 1 < length:
        return None
    window = values[end_idx + 1 - length : end_idx + 1]
    return sum(window) / length


def detect_signals(daily: list[DailyBar]) -> list[Signal]:
    closes = [d.close for d in daily]
    signals: list[Signal] = []
    for i in range(6, len(daily)):
        d = daily[i]
        rng = d.high - d.low
        nr7 = all(rng < (daily[i - k].high - daily[i - k].low) for k in range(1, 7))
        inside = d.high < daily[i - 1].high and d.low > daily[i - 1].low
        bullish = d.open < d.close
        ma_now = sma(closes, i, SMA_LEN)
        ma_prev = sma(closes, i - 1, SMA_LEN)
        if ma_now is None or ma_prev is None:
            continue
        ma_up = ma_now > ma_prev
        if not (nr7 and inside and bullish and ma_up):
            continue
        if i + 1 >= len(daily):
            continue
        entry_open = daily[i + 1].open
        entry_exec = entry_open + SLIP_TICKS * TICK
        stop = d.low
        dist_pts = entry_exec - stop
        dist_ticks = dist_pts / TICK
        valid = dist_pts > 0
        signals.append(
            Signal(
                day=d.day,
                low=d.low,
                high=d.high,
                open=d.open,
                close=d.close,
                stop_distance_ticks=dist_ticks if valid else float("nan"),
                valid_stop=valid,
            )
        )
    return signals


def day_index(daily: list[DailyBar]) -> dict[date, int]:
    return {d.day: i for i, d in enumerate(daily)}


def next_trading_day(daily: list[DailyBar], idx: int) -> int | None:
    return idx + 1 if idx + 1 < len(daily) else None


def simulate_trade(
    signal: Signal,
    daily: list[DailyBar],
    bars: list[dict],
    idx: dict[date, int],
) -> Trade | None:
    sig_i = idx[signal.day]
    entry_i = sig_i + 1
    if entry_i >= len(daily):
        return None
    entry_day = daily[entry_i].day
    entry_bars = [b for b in bars if b["_td"] == entry_day]
    if not entry_bars:
        return None
    entry_px = float(entry_bars[0]["open"]) + SLIP_TICKS * TICK
    stop = signal.low
    slip = SLIP_TICKS * TICK

    path = [b for b in bars if b["_td"] >= entry_day]
    exit_px: float | None = None
    exit_day: date | None = None
    exit_reason = ""
    pending_exit_after: date | None = None
    mae_pts = 0.0

    day_ptr = 0
    while day_ptr < len(path):
        td = path[day_ptr]["_td"]
        day_bars: list[dict] = []
        while day_ptr < len(path) and path[day_ptr]["_td"] == td:
            day_bars.append(path[day_ptr])
            day_ptr += 1

        if pending_exit_after is not None and td > pending_exit_after:
            exit_px = float(day_bars[0]["open"]) - slip
            exit_day = td
            exit_reason = "PINE_BULLISH_NEXT_OPEN"
            break

        for bi, bar in enumerate(day_bars):
            o, h, l = float(bar["open"]), float(bar["high"]), float(bar["low"])
            mae_pts = max(mae_pts, entry_px - l)
            if bi == 0 and o <= stop:
                exit_px = o - slip
                exit_day = td
                exit_reason = "STOP_GAP"
                break
            if l <= stop:
                exit_px = stop - slip
                exit_day = td
                exit_reason = "STOP_HIT"
                break
        if exit_px is not None:
            break

        db = daily[idx[td]]
        if db.open < db.close:
            nxt = next_trading_day(daily, idx[td])
            if nxt is None:
                exit_px = db.close - slip
                exit_day = td
                exit_reason = "PINE_BULLISH_LAST_DAY_CLOSE"
            else:
                pending_exit_after = td
        if pending_exit_after is not None and exit_px is None:
            continue

    if exit_px is None and pending_exit_after is not None:
        nxt_i = idx[pending_exit_after] + 1
        if nxt_i < len(daily):
            nd = daily[nxt_i].day
            nb = [b for b in bars if b["_td"] == nd]
            if nb:
                exit_px = float(nb[0]["open"]) - slip
                exit_day = nd
                exit_reason = "PINE_BULLISH_NEXT_OPEN"

    if exit_px is None:
        last = daily[-1]
        exit_px = last.close - slip
        exit_day = last.day
        exit_reason = "FORCED_LAST_DAY"

    stop_usd = signal.stop_distance_ticks * TICK_VALUE
    pnl = (exit_px - entry_px) * POINT_VALUE - COMMISSION_RT
    return Trade(
        signal_day=signal.day,
        entry_day=entry_day,
        entry_px=entry_px,
        exit_day=exit_day,
        exit_px=exit_px,
        exit_reason=exit_reason,
        pnl=pnl,
        stop_ticks=signal.stop_distance_ticks,
        stop_usd=stop_usd,
        mae_usd=mae_pts * POINT_VALUE,
    )


def exposure_matched_benchmark(
    trades: list[Trade],
    daily: list[DailyBar],
    idx: dict[date, int],
) -> dict:
    """MNQ buy-and-hold over the same entry→exit RTH sessions as the strategy."""
    if not trades:
        return {"net_usd": None, "sessions_in_market": 0}
    in_market: set[date] = set()
    for t in trades:
        i0 = idx[t.entry_day]
        i1 = idx[t.exit_day]
        for j in range(i0, i1 + 1):
            in_market.add(daily[j].day)
    net = 0.0
    days_sorted = sorted(in_market)
    for d in days_sorted:
        i = idx[d]
        if i == 0:
            continue
        prev = daily[i - 1]
        cur = daily[i]
        net += (cur.open - prev.close) * POINT_VALUE  # hold from prior close through open gap
        net += (cur.close - cur.open) * POINT_VALUE
    net -= COMMISSION_RT * 2 * len(trades)  # same number of round-turns for comparability
    return {
        "net_usd": round(net, 2),
        "sessions_in_market": len(in_market),
        "description": "Long MNQ each RTH session from entry day through exit day (daily open-to-close chain)",
    }


def capital_requirements(trades: list[Trade]) -> dict:
    if not trades:
        return {}
    stop_usd = [t.stop_usd for t in trades]
    mae = [t.mae_usd for t in trades]
    max_stop = max(stop_usd)
    max_mae = max(mae)
    median_stop = statistics.median(stop_usd)
    # CME MNQ intraday margin varies by broker; cite typical micro range as UNVERIFIED
    return {
        "per_trade_structural_stop_usd": {
            "min": round(min(stop_usd), 2),
            "median": round(median_stop, 2),
            "max": round(max_stop, 2),
        },
        "per_trade_mae_usd_observed": {
            "max": round(max_mae, 2),
        },
        "suggested_capital_1pct_risk_on_max_stop": round(max_stop / 0.01, 2),
        "suggested_capital_2pct_risk_on_max_stop": round(max_stop / 0.02, 2),
        "suggested_capital_1pct_risk_on_median_stop": round(median_stop / 0.01, 2),
        "suggested_capital_2pct_risk_on_median_stop": round(median_stop / 0.02, 2),
        "suggested_capital_1pct_risk_on_max_mae": round(max(mae) / 0.01, 2),
        "suggested_capital_2pct_risk_on_max_mae": round(max(mae) / 0.02, 2),
        "illustrative_mnq_micro_margin_usd": "UNVERIFIED — broker/day-trading margin often ~$50–$200 micro; overnight higher",
        "gap_slippage_note": "STOP_GAP exits use open - 1 tick; entry + stop path uses 15m lows",
    }


def classify_verdict(trades: list[Trade], net: float, pf: float, h1: float, h2: float) -> str:
    if len(trades) < MIN_TRADES_CREDIBLE:
        return "INSUFFICIENT_EVIDENCE"
    if net > 0 and pf >= 1.2 and h1 > 0 and h2 > 0:
        return "PROMISING BUT UNPROVEN"
    return "FAIL"


def exploratory_performance_label(trades: list[Trade], net: float, pf: float) -> str:
    if not trades:
        return "N/A"
    if net <= 0 or pf < 1.0:
        return "FAIL (exploratory economics on available sample)"
    return "positive_on_tiny_sample_not_credible"


def run_exploratory(*, independent_valid_trades: bool = False) -> dict:
    bars = load_rth_bars()
    daily = build_daily(bars)
    idx = day_index(daily)
    cal = calendar_audit(daily)
    signals = detect_signals(daily)

    valid_signals = [s for s in signals if s.valid_stop]
    invalid = [s for s in signals if not s.valid_stop]

    trades: list[Trade] = []
    skipped_overlap_days: list[str] = []
    flat_after: date | None = None
    for sig in valid_signals:
        if not independent_valid_trades and flat_after is not None and sig.day <= flat_after:
            skipped_overlap_days.append(sig.day.isoformat())
            continue
        t = simulate_trade(sig, daily, bars, idx)
        if not t:
            continue
        trades.append(t)
        if not independent_valid_trades:
            flat_after = t.exit_day

    report: dict = {
        "frozen_exploratory_spec": FROZEN_EXPLORATORY_SPEC,
        "corpus": str(CORPUS.relative_to(REPO)),
        "daily_rth_sessions": len(daily),
        "window": f"{daily[0].day}..{daily[-1].day}",
        "calendar_audit": cal,
        "signal_count": len(signals),
        "invalid_stop_geometry": len(invalid),
        "valid_stop_signals": len(valid_signals),
        "trades_skipped_overlap": len(skipped_overlap_days),
        "skipped_overlap_signal_days": skipped_overlap_days,
        "position_model": "independent_per_signal" if independent_valid_trades else "single_contract_non_overlap",
        "reference_prior_run": {
            "signals": 7,
            "valid_stops": 5,
            "invalid_stops": 2,
            "matches": len(signals) == 7 and len(valid_signals) == 5 and len(invalid) == 2,
        },
    }

    b0, b1 = daily[0].open, daily[-1].close
    buy_hold_full = (b1 - b0) * POINT_VALUE - COMMISSION_RT * 2

    if not trades:
        report["verdict"] = "INSUFFICIENT_EVIDENCE"
        report["valid_trade_count"] = 0
        return report

    net = sum(t.pnl for t in trades)
    wins = [t for t in trades if t.pnl > 0]
    gw = sum(t.pnl for t in wins)
    gl = -sum(t.pnl for t in trades if t.pnl <= 0)
    pf = gw / gl if gl else float("inf")
    mid = len(trades) // 2
    h1 = sum(t.pnl for t in trades[:mid])
    h2 = sum(t.pnl for t in trades[mid:])

    eq = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in trades:
        eq += t.pnl
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)

    sorted_wins = sorted((t.pnl for t in trades if t.pnl > 0), reverse=True)
    top1 = sorted_wins[0] if sorted_wins else 0.0
    top3 = sum(sorted_wins[:3])
    conc_top1 = abs(top1 / net) if net else None
    conc_top3 = abs(top3 / net) if net else None

    bench = exposure_matched_benchmark(trades, daily, idx)

    report.update(
        {
            "valid_trade_count": len(trades),
            "net_usd": round(net, 2),
            "win_rate": round(len(wins) / len(trades), 3),
            "profit_factor": round(pf, 3),
            "expectancy_usd_per_trade": round(net / len(trades), 2),
            "max_drawdown_usd_on_cumulative_pnl": round(max_dd, 2),
            "worst_trade_usd": round(min(t.pnl for t in trades), 2),
            "best_trade_usd": round(max(t.pnl for t in trades), 2),
            "largest_structural_stop_usd": round(max(t.stop_usd for t in trades), 2),
            "chronological_halves_net": {"first_half": round(h1, 2), "second_half": round(h2, 2)},
            "concentration": {
                "largest_winner_share_of_net": round(conc_top1, 3) if conc_top1 is not None else None,
                "top3_winners_share_of_net": round(conc_top3, 3) if conc_top3 is not None else None,
            },
            "benchmark_full_window_buy_hold_usd": round(buy_hold_full, 2),
            "benchmark_exposure_matched": bench,
            "exit_reason_counts": dict(
                sorted(
                    ((k, sum(1 for t in trades if t.exit_reason == k)) for k in {t.exit_reason for t in trades}),
                    key=lambda x: -x[1],
                )
            ),
            "trades": [
                {
                    "signal_day": t.signal_day.isoformat(),
                    "entry_day": t.entry_day.isoformat(),
                    "exit_day": t.exit_day.isoformat(),
                    "pnl_usd": round(t.pnl, 2),
                    "stop_usd": round(t.stop_usd, 2),
                    "stop_ticks": round(t.stop_ticks, 1),
                    "exit_reason": t.exit_reason,
                }
                for t in trades
            ],
            "invalid_signal_days": [s.day.isoformat() for s in invalid],
            "capital_requirements": capital_requirements(trades),
            "verdict": classify_verdict(trades, net, pf, h1, h2),
            "exploratory_performance": exploratory_performance_label(trades, net, pf),
            "verdict_note": (
                f"n={len(trades)} executed trades from {len(valid_signals)} valid-stop signals "
                f"(credible sample gate is {MIN_TRADES_CREDIBLE}+)."
            ),
        }
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--independent-valid",
        action="store_true",
        help="Score each valid-stop signal as its own trade (no overlap skip)",
    )
    parser.add_argument("--out", type=Path, help="Optional JSON output path")
    args = parser.parse_args()
    report = run_exploratory(independent_valid_trades=args.independent_valid)
    text = json.dumps(report, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
