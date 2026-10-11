#!/usr/bin/env python3
"""Keltner KC reversal LONG only — vault Pine isolate (Hybrid-Keltner vault #488273).

Chosen before outcomes: Long_KC path only (crossunder close, lowerKC; exit crossover close, emaBasis;
stop 1.5*ATR; limit 3*ATR). Trend and short legs excluded to avoid hybrid signal conflicts.
"""
from __future__ import annotations

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
KC_LEN = 20
KC_MULT = 1.5
ATR_STOP_MULT = 1.5
ATR_TP_MULT = 3.0  # 2 * ATR_STOP_MULT in Pine
TICK = 0.25
POINT_VALUE = 2.0
TICK_VALUE = 0.50
COMMISSION_RT = 1.48
SLIP_TICKS = 1.0
BAR_MIN = 60

FROZEN_SPEC = {
    "trial": "AFS-KELTNER-KC-LONG-MNQ-EXPLORATORY-2026-10-11",
    "vault": "Hybrid-Keltner-Channel-EMA-Based-Trading-System-with-ATR-Risk-Management.md",
    "chosen_leg": "Long_KC only (reversal); Short_KC and Trend legs excluded",
    "economic_reason": (
        "Isolated Keltner mean-reversion long is fully specified in Pine with ATR bracket; "
        "mixing trend+reversal in one book creates opposing signals on the same symbol."
    ),
    "timeframe": "60m RTH bars aggregated from 15m corpus",
    "entry": "ta.crossunder(close, lowerKC) confirmed at bar close; fill next 60m open + 1 tick",
    "exit_signal": "ta.crossover(close, emaBasis) confirmed at bar close; fill next 60m open - 1 tick",
    "stop": "entry_reference_close - 1.5*ATR(20) at entry bar",
    "target": "entry_reference_close + 3.0*ATR(20) at entry bar",
    "intrabar": "15m path stop-first; pessimistic stop-before-target if both touched",
    "costs": {"commission_rt": COMMISSION_RT, "slippage_ticks": SLIP_TICKS},
    "data_end": "2026-06-26",
}


def parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def trading_day(dt: datetime) -> date:
    return (dt.astimezone(ET) + timedelta(hours=6)).date()


def in_rth(dt: datetime) -> bool:
    et = dt.astimezone(ET)
    return time(9, 30) <= et.time() <= time(16, 0)


def bucket_start(dt: datetime, minutes: int) -> datetime:
    et = dt.astimezone(ET)
    m = (et.hour * 60 + et.minute) // minutes * minutes
    return et.replace(hour=m // 60, minute=m % 60, second=0, microsecond=0)


@dataclass
class Bar60:
    start: datetime
    day: date
    open: float
    high: float
    low: float
    close: float


def load_15m() -> list[dict]:
    rows = []
    for path in sorted(CORPUS.glob("MNQ_*.jsonl")):
        fd = date.fromisoformat(path.stem.split("_", 1)[1])
        if fd >= MNQ_SEAL_START or fd < START:
            continue
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            dt = parse_dt(row["timestamp"])
            if not in_rth(dt):
                continue
            row["_dt"] = dt
            rows.append(row)
    rows.sort(key=lambda r: r["_dt"])
    return rows


def aggregate_60m(rows: list[dict]) -> list[Bar60]:
    buckets: dict[datetime, list[dict]] = defaultdict(list)
    for row in rows:
        start = bucket_start(row["_dt"], BAR_MIN)
        buckets.setdefault(start, []).append(row)
    out: list[Bar60] = []
    for start in sorted(buckets):
        chunk = sorted(buckets[start], key=lambda r: r["_dt"])
        out.append(
            Bar60(
                start=start,
                day=trading_day(chunk[0]["_dt"]),
                open=float(chunk[0]["open"]),
                high=max(float(r["high"]) for r in chunk),
                low=min(float(r["low"]) for r in chunk),
                close=float(chunk[-1]["close"]),
            )
        )
    return out


def ema(series: list[float], length: int) -> list[float | None]:
    out: list[float | None] = [None] * len(series)
    if len(series) < length:
        return out
    k = 2 / (length + 1)
    val = sum(series[:length]) / length
    out[length - 1] = val
    for i in range(length, len(series)):
        val = series[i] * k + val * (1 - k)
        out[i] = val
    return out


def atr(bars: list[Bar60], length: int) -> list[float | None]:
    trs: list[float] = []
    out: list[float | None] = [None] * len(bars)
    for i, b in enumerate(bars):
        if i == 0:
            tr = b.high - b.low
        else:
            prev_c = bars[i - 1].close
            tr = max(b.high - b.low, abs(b.high - prev_c), abs(b.low - prev_c))
        trs.append(tr)
        if i + 1 < length:
            continue
        if i + 1 == length:
            out[i] = sum(trs[:length]) / length
        else:
            prev = out[i - 1]
            assert prev is not None
            out[i] = (prev * (length - 1) + tr) / length
    return out


def crossunder(close_prev, close, band_prev, band) -> bool:
    return close_prev >= band_prev and close < band


def crossover(close_prev, close, band_prev, band) -> bool:
    return close_prev <= band_prev and close > band


@dataclass
class Trade:
    entry_i: int
    exit_i: int
    entry_px: float
    exit_px: float
    stop: float
    target: float
    pnl: float
    reason: str
    stop_usd: float
    mae_usd: float


def run_backtest() -> dict:
    rows15 = load_15m()
    bars = aggregate_60m(rows15)
    closes = [b.close for b in bars]
    ema_basis = ema(closes, KC_LEN)
    atr_vals = atr(bars, KC_LEN)

    trades: list[Trade] = []
    i = KC_LEN
    while i < len(bars) - 1:
        eb = ema_basis[i]
        av = atr_vals[i]
        if eb is None or av is None:
            i += 1
            continue
        upper = eb + KC_MULT * av
        lower = eb - KC_MULT * av
        cp, cc = closes[i - 1], closes[i]
        if ema_basis[i - 1] is None or atr_vals[i - 1] is None:
            i += 1
            continue
        lower_prev = ema_basis[i - 1] - KC_MULT * atr_vals[i - 1]
        if not crossunder(cp, cc, lower_prev, lower):
            i += 1
            continue

        entry_i = i + 1
        if entry_i >= len(bars):
            break
        ref_close = closes[i]
        stop = ref_close - ATR_STOP_MULT * av
        target = ref_close + ATR_TP_MULT * av
        entry_time = bars[entry_i].start.astimezone(timezone.utc)
        entry_px = bars[entry_i].open + SLIP_TICKS * TICK
        stop_usd = (entry_px - stop) * POINT_VALUE

        exit_i = None
        for j in range(entry_i, len(bars)):
            if ema_basis[j] is None:
                continue
            if crossover(closes[j - 1], closes[j], ema_basis[j - 1], ema_basis[j]):
                exit_i = j
                break
        slip = SLIP_TICKS * TICK
        path = [r for r in rows15 if r["_dt"] >= entry_time]
        mae_pts = 0.0
        exit_px = None
        reason = "FORCED_END"
        fill_time = (
            bars[exit_i + 1].start.astimezone(timezone.utc)
            if exit_i is not None and exit_i + 1 < len(bars)
            else None
        )

        for bi, r in enumerate(path):
            dt = r["_dt"]
            if fill_time is not None and dt >= fill_time:
                exit_px = float(r["open"]) - slip
                reason = "KC_MIDLINE_NEXT_OPEN"
                break
            o, h, l = float(r["open"]), float(r["high"]), float(r["low"])
            mae_pts = max(mae_pts, entry_px - l)
            if bi == 0 and o <= stop:
                exit_px = o - slip
                reason = "STOP_GAP"
                break
            if l <= stop and h >= target:
                exit_px = stop - slip
                reason = "STOP_HIT"
                break
            if l <= stop:
                exit_px = stop - slip
                reason = "STOP_HIT"
                break
            if h >= target:
                exit_px = target
                reason = "TARGET_HIT"
                break

        if exit_px is None:
            exit_px = bars[-1].close - slip
            reason = "FORCED_LAST_BAR"
            exit_i = len(bars) - 1

        pnl = (exit_px - entry_px) * POINT_VALUE - COMMISSION_RT
        trades.append(
            Trade(
                entry_i=entry_i,
                exit_i=exit_i or len(bars) - 1,
                entry_px=entry_px,
                exit_px=exit_px,
                stop=stop,
                target=target,
                pnl=pnl,
                reason=reason,
                stop_usd=stop_usd,
                mae_usd=mae_pts * POINT_VALUE,
            )
        )
        i = (exit_i or i) + 1

    if not trades:
        return {"frozen_spec": FROZEN_SPEC, "verdict": "INSUFFICIENT_EVIDENCE", "trades": 0}

    net = sum(t.pnl for t in trades)
    wins = [t for t in trades if t.pnl > 0]
    gw = sum(t.pnl for t in wins)
    gl = -sum(t.pnl for t in trades if t.pnl <= 0)
    pf = gw / gl if gl else float("inf")
    mid = len(trades) // 2
    h1, h2 = sum(t.pnl for t in trades[:mid]), sum(t.pnl for t in trades[mid:])
    eq = peak = max_dd = 0.0
    for t in trades:
        eq += t.pnl
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)
    stops = [t.stop_usd for t in trades]
    maes = [t.mae_usd for t in trades]
    sorted_w = sorted((t.pnl for t in trades if t.pnl > 0), reverse=True)
    top3 = sum(sorted_w[:3])
    b0, b1 = bars[0].open, bars[-1].close
    bh = (b1 - b0) * POINT_VALUE - COMMISSION_RT * 2

    return {
        "frozen_spec": FROZEN_SPEC,
        "bars_60m": len(bars),
        "trade_count": len(trades),
        "net_usd": round(net, 2),
        "profit_factor": round(pf, 3),
        "expectancy_usd": round(net / len(trades), 2),
        "win_rate": round(len(wins) / len(trades), 3),
        "max_drawdown_usd": round(max_dd, 2),
        "worst_trade_usd": round(min(t.pnl for t in trades), 2),
        "best_trade_usd": round(max(t.pnl for t in trades), 2),
        "max_stop_usd": round(max(stops), 2),
        "max_mae_usd": round(max(maes), 2),
        "h1_net": round(h1, 2),
        "h2_net": round(h2, 2),
        "top3_winner_share_of_net": round(abs(top3 / net), 3) if net else None,
        "benchmark_full_bh_usd": round(bh, 2),
        "exit_reasons": dict(
            sorted(
                ((k, sum(1 for t in trades if t.reason == k)) for k in {t.reason for t in trades}),
                key=lambda x: -x[1],
            )
        ),
        "capital_1pct_max_stop": round(max(stops) / 0.01, 2),
        "capital_2pct_max_stop": round(max(stops) / 0.02, 2),
        "capital_5pct_max_stop": round(max(stops) / 0.05, 2),
        "capital_1pct_max_mae": round(max(maes) / 0.01, 2),
        "verdict": (
            "FAIL"
            if net <= 0 or pf < 1.0
            else "PROMISING BUT UNPROVEN"
            if len(trades) >= 30 and h1 > 0 and h2 > 0
            else "INSUFFICIENT_EVIDENCE"
        ),
    }


if __name__ == "__main__":
    print(json.dumps(run_backtest(), indent=2))
