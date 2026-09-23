#!/usr/bin/env python3
"""Strat setups on 60m / 4H / daily — magnitude + FTFC, intrabar stop entry.

Preregistered in docs/prereg-strat-htf-magnitude-ftfc-2026-09-23.md (#938).
Read-only research: changes no strategy/runtime configuration and never calls
an external broker. PaperBroker is used only as the fill/exit model.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.futures_contracts import contract_economics
from context.cme_trading_day import cme_trading_day
from execution.broker_interface import BracketOrder
from execution.paper_broker import NextBarOHLC, PaperBroker
from strategy.strat_classifier import INSIDE_BAR, OUTSIDE_BAR, TWO_DOWN, TWO_UP, StratBar, classify_bar

ET = ZoneInfo("America/New_York")
M15 = timedelta(minutes=15)
REOPEN = time(18, 0)

SLIPPAGE_TICKS = 1.0
ROUND_TURN_COMMISSION = 1.48
TFS = ("60m", "4h", "daily")
MIN_TRADES = {"60m": 60, "4h": 40, "daily": 25}
OOS_MIN_TRADES = {"60m": 10, "4h": 5, "daily": None}
EXIT_DAYS_AFTER = {"60m": 0, "4h": 1, "daily": 5}
MAX_FILLS_PER_DAY = {"60m": 3, "4h": None, "daily": None}
PF_FLOOR = 1.94
NULL_REPS = 500
NULL_SEED = 20260923

MAIN_WINDOW = ("2024-11-01", "2026-07-23")
HALF_SPLIT = "2025-09-12"
OOS_WINDOW = ("2026-07-24", "2026-09-21")

MAG_SETUPS = ("rev_2_2", "rev_3_2_2", "rev_1_2_2", "rev_2_1_2", "s_3_1_2")
ARMS = {**{s: ("R", "R+F", "M", "M+F") for s in MAG_SETUPS}, "cont_2_2": ("R", "R+F"), "hammer_level": ("R+F", "M+F")}
CANDIDATES = {**{s: ("M", "M+F", "R+F") for s in MAG_SETUPS}, "cont_2_2": ("R+F",), "hammer_level": ("M+F", "R+F")}
PRIOR_EXPOSED = {("daily", "rev_2_2"), ("60m", "rev_3_2_2")}
TERMINAL = {"WIN", "LOSS", "BREAKEVEN", "TIME_EXIT", "ROLL_EXIT"}


# --------------------------------------------------------------------------- #
# 15m data
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Bar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    market_condition: Optional[str]
    td: str


def trading_date(ts: datetime) -> str:
    """CME equity-index trade date (18:00 ET roll; exchange holidays fold forward).

    Uses the repo's proven calendar (``context.cme_trading_day``, the same rule
    as the Daily 2-2 lane), so a holiday's abbreviated session belongs to the
    next trade date exactly as CME settles it.
    """
    return cme_trading_day(ts, "MNQ").isoformat()


def _parse_ts(value: str) -> datetime:
    ts = datetime.fromisoformat(value)
    if ts.tzinfo is None:
        raise ValueError(f"naive timestamp {value!r}")
    return ts.astimezone(timezone.utc)


def load_bars(dirs_high_to_low: Sequence[Path]) -> tuple[list[Bar], list[dict[str, Any]]]:
    """Precedence merge: a higher source overwrites a lower one; conflicts are listed."""
    by_ts: dict[datetime, Bar] = {}
    source: dict[datetime, str] = {}
    conflicts: list[dict[str, Any]] = []
    for directory in reversed(list(dirs_high_to_low)):
        files = sorted(directory.glob("*.jsonl"))
        if not files:
            raise RuntimeError(f"no .jsonl files in {directory}")
        for path in files:
            for line in path.read_text().splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                ts = _parse_ts(row["timestamp"])
                bar = Bar(ts, float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]),
                          row.get("market_condition") or row.get("reconstructed_market_condition"),
                          trading_date(ts))
                prior = by_ts.get(ts)
                if prior is not None and (prior.open, prior.high, prior.low, prior.close) != (
                        bar.open, bar.high, bar.low, bar.close):
                    conflicts.append({"ts": ts.isoformat(), "kept": str(directory), "replaced": source[ts],
                                      "kept_ohlc": [bar.open, bar.high, bar.low, bar.close],
                                      "replaced_ohlc": [prior.open, prior.high, prior.low, prior.close]})
                by_ts[ts] = bar
                source[ts] = str(directory)
    return [by_ts[k] for k in sorted(by_ts)], conflicts


def load_seams(manifests: Sequence[Path], instrument: str) -> list[tuple[datetime, float]]:
    seams: dict[datetime, float] = {}
    for path in manifests:
        payload = json.loads(path.read_text())
        if payload.get("instrument") != instrument:
            raise RuntimeError(f"{path} is not a {instrument} manifest")
        for entry in payload.get("roll_ledger") or []:
            seams[_parse_ts(entry["first_new_bar"])] = float(entry["gap_points"])
    return sorted(seams.items())


def fingerprint(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(directory.glob("*.jsonl")):
        digest.update(f"{directory.name}/{path.name}\n".encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


class Offsets:
    """Cumulative roll gap at a timestamp (difference back-adjustment)."""

    def __init__(self, seams: Sequence[tuple[datetime, float]]):
        self.seams = list(seams)

    def at(self, ts: datetime) -> float:
        return sum(gap for s, gap in self.seams if s <= ts)

    def between(self, a: datetime, b: datetime) -> bool:
        """A seam at s with a < s <= b."""
        return any(a < s <= b for s, _ in self.seams)


# --------------------------------------------------------------------------- #
# higher-timeframe bars
# --------------------------------------------------------------------------- #


@dataclass
class HBar:
    start: datetime
    i0: int
    i1: int  # exclusive
    open: float
    high: float
    low: float
    close: float
    td: str
    complete: bool


def _bucket_start(tf: str, bar: Bar) -> datetime:
    local = bar.ts.astimezone(ET)
    if tf == "60m":
        return local.replace(minute=0, second=0, microsecond=0)
    if tf == "4h":
        offset = (local.hour - 18) % 24
        return local.replace(minute=0, second=0, microsecond=0) - timedelta(hours=offset % 4)
    # daily: the session that opens 18:00 ET the calendar day before the trading date
    # (Sunday evening for a Monday trading date).
    return datetime.combine(date.fromisoformat(bar.td) - timedelta(days=1), REOPEN, tzinfo=ET)


def _expected(tf: str, start: datetime) -> Optional[int]:
    if tf == "60m":
        return 4
    if tf == "4h":
        return 12 if start.astimezone(ET).hour == 14 else 16
    return None


def build_htf(tf: str, bars: Sequence[Bar]) -> list[HBar]:
    groups: list[tuple[tuple[str, datetime], int, int]] = []
    for i, bar in enumerate(bars):
        key = (bar.td, _bucket_start(tf, bar))
        if groups and groups[-1][0] == key:
            groups[-1] = (key, groups[-1][1], i + 1)
        else:
            groups.append((key, i, i + 1))
    last_of_day: dict[str, int] = {}
    for g, (key, _i0, _i1) in enumerate(groups):
        last_of_day[key[0]] = g
    out: list[HBar] = []
    for g, ((td, start), i0, i1) in enumerate(groups):
        seg = bars[i0:i1]
        contiguous = all(b.ts - a.ts == M15 for a, b in zip(seg, seg[1:]))
        # a daily bar may span a holiday halt that ends at an 18:00 ET reopen
        daily_contiguous = all(b.ts - a.ts == M15 or b.ts.astimezone(ET).time() == REOPEN
                               for a, b in zip(seg, seg[1:]))
        starts_on_time = seg[0].ts == start.astimezone(timezone.utc)
        if tf == "daily":
            complete = daily_contiguous and seg[0].ts.astimezone(ET).time() == REOPEN
        else:
            full = len(seg) == _expected(tf, start)
            complete = starts_on_time and contiguous and (full or last_of_day[td] == g)
        out.append(HBar(start.astimezone(timezone.utc), i0, i1, seg[0].open, max(b.high for b in seg),
                        min(b.low for b in seg), seg[-1].close, td, complete))
    return out


def btype(cur: HBar, prev: HBar) -> str:
    return classify_bar(StratBar(high=cur.high, low=cur.low), StratBar(high=prev.high, low=prev.low))


def is_hammer(b: HBar) -> bool:
    rng = b.high - b.low
    return rng > 0 and min(b.open, b.close) >= b.low + rng * 2.0 / 3.0


def is_shooter(b: HBar) -> bool:
    rng = b.high - b.low
    return rng > 0 and max(b.open, b.close) <= b.low + rng / 3.0


# --------------------------------------------------------------------------- #
# FTFC opens and hammer levels (causal, back-adjusted)
# --------------------------------------------------------------------------- #


class Context:
    def __init__(self, bars: Sequence[Bar], offsets: Offsets):
        self.bars = bars
        self.off = offsets
        self.index = {b.ts: i for i, b in enumerate(bars)}
        first: dict[str, int] = {}
        self.day_bars: dict[str, list[int]] = defaultdict(list)
        for i, b in enumerate(bars):
            first.setdefault(b.td, i)
            self.day_bars[b.td].append(i)
        self.tds = sorted(first)
        self.td_pos = {d: k for k, d in enumerate(self.tds)}
        self.day_open = {d: (i if bars[i].ts.astimezone(ET).time() == REOPEN else None) for d, i in first.items()}
        self.week_first: dict[tuple, str] = {}
        self.month_first: dict[tuple, str] = {}
        for d in self.tds:
            dd = date.fromisoformat(d)
            self.week_first.setdefault(tuple(dd.isocalendar())[:2], d)
            self.month_first.setdefault((dd.year, dd.month), d)
        # period extremes: (low, low_i, high, high_i)
        self.day_ext = {d: self._ext(idx) for d, idx in self.day_bars.items()}
        weeks: dict[tuple, list[int]] = defaultdict(list)
        months: dict[tuple, list[int]] = defaultdict(list)
        for d, idx in self.day_bars.items():
            dd = date.fromisoformat(d)
            weeks[tuple(dd.isocalendar())[:2]].extend(idx)
            months[(dd.year, dd.month)].extend(idx)
        self.week_ext = {k: self._ext(v) for k, v in weeks.items()}
        self.month_ext = {k: self._ext(v) for k, v in months.items()}
        self.week_keys = sorted(weeks)
        self.month_keys = sorted(months)

    def _ext(self, idx: list[int]) -> tuple[float, int, float, int]:
        lo = min(idx, key=lambda i: self.bars[i].low)
        hi = max(idx, key=lambda i: self.bars[i].high)
        return self.bars[lo].low, lo, self.bars[hi].high, hi

    def adj(self, value: float, value_i: int, at: datetime) -> float:
        return value + self.off.at(at) - self.off.at(self.bars[value_i].ts)

    def ftfc(self, i: int, price: float) -> str:
        """FTFC of ``price`` at the START of 15m bar i (opens known by then)."""
        bar = self.bars[i]
        d = date.fromisoformat(bar.td)
        hour_start = bar.ts.astimezone(ET).replace(minute=0, second=0, microsecond=0).astimezone(timezone.utc)
        hour_i = self.index.get(hour_start)
        if hour_i is not None and self.bars[hour_i].td != bar.td:
            hour_i = None
        opens = [self.day_open.get(self.month_first[(d.year, d.month)]),
                 self.day_open.get(self.week_first[tuple(d.isocalendar())[:2]]),
                 self.day_open.get(bar.td), hour_i]
        if any(j is None for j in opens):
            return "UNKNOWN"
        values = []
        for j in opens:
            if self.bars[j].ts > bar.ts:
                raise AssertionError("FTFC open after trigger bar")
            values.append(self.adj(self.bars[j].open, j, bar.ts))
        if all(price > v for v in values):
            return "UP"
        if all(price < v for v in values):
            return "DOWN"
        return "CONFLICT"

    def hammer_levels(self, tf: str, b1: HBar) -> Optional[tuple[list[float], list[float]]]:
        """(low levels, high levels) from completed prior periods, adjusted to b1's contract."""
        at = b1.start
        d = date.fromisoformat(b1.td)
        lows: list[float] = []
        highs: list[float] = []

        def add(ext):
            lo, lo_i, hi, hi_i = ext
            lows.append(self.adj(lo, lo_i, at))
            highs.append(self.adj(hi, hi_i, at))

        wk = tuple(d.isocalendar())[:2]
        wpos = self.week_keys.index(wk)
        if wpos == 0:
            return None
        if tf in ("60m", "4h"):
            k = self.td_pos[b1.td]
            if k == 0:
                return None
            add(self.day_ext[self.tds[k - 1]])
            add(self.week_ext[self.week_keys[wpos - 1]])
        else:
            mk = (d.year, d.month)
            mpos = self.month_keys.index(mk)
            if mpos == 0:
                return None
            add(self.week_ext[self.week_keys[wpos - 1]])
            add(self.month_ext[self.month_keys[mpos - 1]])
        return lows, highs


# --------------------------------------------------------------------------- #
# arming (prereg §5)
# --------------------------------------------------------------------------- #


@dataclass
class Side:
    direction: str
    trigger: float
    stop: float
    magnitude: Optional[float]
    scored: bool = True


@dataclass
class Armed:
    setup: str
    j: int
    sides: list[Side]
    skip: Optional[str] = None
    two_sided: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


def arm(tf: str, h: Sequence[HBar], j: int, tick: float, ctx: Context, offsets: Offsets) -> list[Armed]:
    b1, b2, b3 = h[j - 1], h[j - 2], h[j - 3]
    t1, t2 = btype(b1, b2), btype(b2, b3)
    long_t, long_s = b1.high + tick, b1.low - tick
    short_t, short_s = b1.low - tick, b1.high + tick
    out: list[Armed] = []
    if t1 == TWO_DOWN:
        setup = "rev_3_2_2" if t2 == OUTSIDE_BAR else "rev_1_2_2" if t2 == INSIDE_BAR else "rev_2_2"
        mag = b3.high if setup == "rev_1_2_2" else b2.high
        out.append(Armed(setup, j, [Side("LONG", long_t, long_s, mag)]))
        out.append(Armed("cont_2_2", j, [Side("SHORT", short_t, short_s, None)]))
    elif t1 == TWO_UP:
        setup = "rev_3_2_2" if t2 == OUTSIDE_BAR else "rev_1_2_2" if t2 == INSIDE_BAR else "rev_2_2"
        mag = b3.low if setup == "rev_1_2_2" else b2.low
        out.append(Armed(setup, j, [Side("SHORT", short_t, short_s, mag)]))
        out.append(Armed("cont_2_2", j, [Side("LONG", long_t, long_s, None)]))
    elif t1 == INSIDE_BAR:
        if t2 == TWO_DOWN:
            out.append(Armed("rev_2_1_2", j, [Side("LONG", long_t, long_s, b2.high),
                                             Side("SHORT", short_t, short_s, None, scored=False)], two_sided=True))
        elif t2 == TWO_UP:
            out.append(Armed("rev_2_1_2", j, [Side("SHORT", short_t, short_s, b2.low),
                                             Side("LONG", long_t, long_s, None, scored=False)], two_sided=True))
        elif t2 == OUTSIDE_BAR:
            out.append(Armed("s_3_1_2", j, [Side("LONG", long_t, long_s, b2.high),
                                           Side("SHORT", short_t, short_s, b2.low)], two_sided=True))
    levels = ctx.hammer_levels(tf, b1)
    if levels is not None:
        lows, highs = levels
        if is_hammer(b1) and any(b1.low <= lv for lv in lows):
            out.append(Armed("hammer_level", j, [Side("LONG", long_t, long_s, b2.high)]))
        elif is_shooter(b1) and any(b1.high >= lv for lv in highs):
            out.append(Armed("hammer_level", j, [Side("SHORT", short_t, short_s, b2.low)]))
    if any(not h[k].complete for k in (j - 3, j - 2, j - 1, j)):
        skip = "GAP"
    elif offsets.between(b3.start, ctx.bars[h[j].i1 - 1].ts):
        skip = "ROLL"
    else:
        skip = None
    for a in out:
        a.skip = skip
    return out


# --------------------------------------------------------------------------- #
# resolution (prereg §6)
# --------------------------------------------------------------------------- #


def exit_td_for(tf: str, ctx: Context, entry_td: str) -> Optional[str]:
    k = ctx.td_pos[entry_td] + EXIT_DAYS_AFTER[tf]
    return ctx.tds[k] if k < len(ctx.tds) else None


def resolve_from(instrument: str, bars: Sequence[Bar], offsets: Offsets, i: int, direction: str,
                 fill: float, stop: float, target: float, exit_td: Optional[str]) -> dict[str, Any]:
    """Outcome of a position filled on 15m bar i (fill-bar stop rule, then broker bars)."""
    tick, tick_value = contract_economics(instrument)
    slip = SLIPPAGE_TICKS * tick
    sign = 1 if direction == "LONG" else -1

    def manual(exit_px: float, k: int, result: str, reason: str) -> dict[str, Any]:
        gross = (exit_px - fill) * sign / tick * tick_value
        return {"result": result, "exit_reason": reason, "exit_i": k, "exit_price": exit_px,
                "gross": round(gross, 2), "net": round(gross - ROUND_TURN_COMMISSION, 2)}

    bar = bars[i]
    if (direction == "LONG" and bar.low <= stop) or (direction == "SHORT" and bar.high >= stop):
        return manual(stop - slip * sign, i, "LOSS", "FILL_BAR_STOP")
    broker = PaperBroker(starting_balance=100_000.0, slippage_ticks=SLIPPAGE_TICKS, pessimistic_both_hit=True,
                         breakeven_at_1r=False, runner_mode=False, entry_fill_model="market")
    broker.restore_position(instrument, direction, fill, stop, target, 1)
    for k in range(i + 1, len(bars)):
        prev, cur = bars[k - 1], bars[k]
        if offsets.between(prev.ts, cur.ts):
            return manual(prev.close - slip * sign, k - 1, "ROLL_EXIT", "ROLL_EXIT")
        if exit_td is not None and cur.td > exit_td:
            return manual(prev.close - slip * sign, k - 1, "TIME_EXIT", "TIME_EXIT")
        res = broker._resolve_position_impl(NextBarOHLC(high=cur.high, low=cur.low, open=cur.open))  # research: bypass mirror hook
        if res is not None:
            gross = float(res.pnl_dollars or 0.0)
            return {"result": str(res.result), "exit_reason": res.exit_reason, "exit_i": k,
                    "exit_price": res.exit_price, "gross": round(gross, 2),
                    "net": round(gross - ROUND_TURN_COMMISSION, 2)}
    return {"result": "OPEN", "exit_reason": "DATA_END", "exit_i": None, "exit_price": None, "gross": None, "net": None}


def stop_market_fill(instrument: str, bar: Bar, side: Side, target: float) -> tuple[Optional[float], str]:
    """PaperBroker stop_market activation on one 15m bar."""
    broker = PaperBroker(starting_balance=100_000.0, slippage_ticks=SLIPPAGE_TICKS, pessimistic_both_hit=True,
                         breakeven_at_1r=False, runner_mode=False, entry_fill_model="stop_market")
    broker._execute_bracket_impl(BracketOrder(  # research: bypass mirror hook
        instrument=instrument, direction=side.direction, entry=side.trigger, stop=side.stop, target=target,
        rr_ratio=abs(target - side.trigger) / abs(side.trigger - side.stop), strategy="htf_audit", contracts=1,
        post_fill_validation_required=False))
    res = broker._resolve_position_impl(NextBarOHLC(high=bar.high, low=bar.low, open=bar.open))
    if res is not None:
        return None, str(res.exit_reason or res.no_fill_reason or res.result)
    pos = broker._position
    return (float(pos.entry_price), "FILLED") if pos is not None else (None, "NO_POSITION")


def simulate_cell(tf: str, instrument: str, bars: Sequence[Bar], h: Sequence[HBar], armed_by_j: dict[int, list[Armed]],
                  ctx: Context, offsets: Offsets, setup: str, arm_name: str) -> list[dict[str, Any]]:
    tick, _ = contract_economics(instrument)
    use_mag = arm_name.startswith("M")
    filtered = arm_name.endswith("+F")
    cap = MAX_FILLS_PER_DAY[tf]
    rows: list[dict[str, Any]] = []
    busy_until = -1
    fills_by_td: Counter[str] = Counter()
    for j in range(3, len(h)):
        inst = next((a for a in armed_by_j.get(j, ()) if a.setup == setup), None)
        if inst is None:
            continue
        base = {"j": j, "htf_start": h[j].start.isoformat()}
        if inst.skip:
            rows.append({**base, "result": inst.skip, "date": h[j].td})
            continue
        sides = [s for s in inst.sides]
        targets: dict[int, float] = {}
        invalid = False
        for n, s in enumerate(sides):
            if not s.scored:
                continue
            if use_mag:
                if s.magnitude is None or (s.magnitude - s.trigger) * (1 if s.direction == "LONG" else -1) < tick:
                    invalid = True
                targets[n] = s.magnitude if s.magnitude is not None else s.trigger
            else:
                risk = abs(s.trigger - s.stop)
                targets[n] = s.trigger + 2 * risk if s.direction == "LONG" else s.trigger - 2 * risk
        if invalid and not inst.two_sided:
            rows.append({**base, "result": "MAGNITUDE_INVALID", "date": h[j].td})
            continue
        hit: Optional[tuple[int, int]] = None
        for i in range(h[j].i0, h[j].i1):
            bar = bars[i]
            crossed = [n for n, s in enumerate(sides)
                       if (s.direction == "LONG" and bar.high >= s.trigger) or (s.direction == "SHORT" and bar.low <= s.trigger)]
            if len(crossed) > 1:
                hit = (-1, i)
                break
            if crossed:
                hit = (crossed[0], i)
                break
        if hit is None:
            rows.append({**base, "result": "NOT_TRIGGERED", "date": h[j].td})
            continue
        n, i = hit
        bar = bars[i]
        row = {**base, "date": bar.td, "trigger_ts": bar.ts.isoformat(), "market_condition": bar.market_condition}
        if n == -1:
            rows.append({**row, "result": "AMBIGUOUS"})
            continue
        side = sides[n]
        row.update(direction=side.direction, trigger=side.trigger, stop=side.stop)
        if not side.scored:
            rows.append({**row, "result": "CONTINUATION_FIRST"})
            continue
        if use_mag and ((side.magnitude is None) or
                        (side.magnitude - side.trigger) * (1 if side.direction == "LONG" else -1) < tick):
            rows.append({**row, "result": "MAGNITUDE_INVALID"})
            continue
        target = targets[n]
        row["target"] = target
        state = ctx.ftfc(i, side.trigger)
        row["ftfc"] = state
        row["alignment"] = ("conflict" if state == "CONFLICT" else None if state == "UNKNOWN" else
                            "aligned" if (state == "UP") == (side.direction == "LONG") else "against")
        if state == "UNKNOWN":
            rows.append({**row, "result": "FTFC_UNKNOWN"})
            continue
        if filtered and row["alignment"] != "aligned":
            rows.append({**row, "result": "FTFC_FILTERED"})
            continue
        if i <= busy_until:
            rows.append({**row, "result": "SKIPPED_BUSY"})
            continue
        if cap is not None and fills_by_td[bar.td] >= cap:
            rows.append({**row, "result": "SKIPPED_MAX_FILLS"})
            continue
        fill, why = stop_market_fill(instrument, bar, side, target)
        if fill is None:
            rows.append({**row, "result": "NO_FILL", "no_fill_reason": why})
            continue
        fills_by_td[bar.td] += 1
        out = resolve_from(instrument, bars, offsets, i, side.direction, fill, side.stop, target,
                           exit_td_for(tf, ctx, bar.td))
        busy_until = out["exit_i"] if out["exit_i"] is not None else len(bars)
        rows.append({**row, "fill_i": i, "fill": fill, "result": out["result"], "exit_reason": out["exit_reason"],
                     "exit_price": out["exit_price"], "gross_pnl": out["gross"], "net_pnl": out["net"]})
    return rows


# --------------------------------------------------------------------------- #
# scoring (prereg §7)
# --------------------------------------------------------------------------- #


def _pf(pnls: Sequence[float]) -> Optional[float]:
    gp = sum(p for p in pnls if p > 0)
    gl = -sum(p for p in pnls if p < 0)
    return round(gp / gl, 4) if gl > 0 else None


def pf_value(s: dict[str, Any]) -> float:
    if s["profit_factor_infinite"]:
        return float("inf")
    return float(s["profit_factor"] or 0.0)


def summarize(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(rows)
    term = sorted((r for r in rows if r["result"] in TERMINAL), key=lambda r: r["trigger_ts"])
    pnls = [float(r["net_pnl"]) for r in term]
    net = round(sum(pnls), 2)
    by_day: dict[str, float] = defaultdict(float)
    for r in term:
        by_day[r["date"]] += float(r["net_pnl"])
    best = max(by_day.items(), key=lambda kv: kv[1]) if by_day else (None, 0.0)
    top3 = sum(sorted((p for p in by_day.values() if p > 0), reverse=True)[:3])
    eq = peak = dd = 0.0
    for p in pnls:
        eq += p
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    pf = _pf(pnls)
    return {
        "signals": len(rows), "outcomes": dict(sorted(Counter(r["result"] for r in rows).items())),
        "terminal": len(term), "wins": sum(p > 0 for p in pnls),
        "win_rate": round(sum(p > 0 for p in pnls) / len(pnls), 4) if pnls else None,
        "net_pnl": net, "expectancy": round(net / len(pnls), 2) if pnls else None,
        "profit_factor": pf, "profit_factor_infinite": bool(pnls and pf is None and any(p > 0 for p in pnls)),
        "max_drawdown": round(dd, 2), "best_day": {"date": best[0], "net_pnl": round(best[1], 2)},
        "leave_best_day_out_net": round(net - best[1], 2) if by_day else None,
        "top3_day_share_of_net": round(top3 / net, 4) if net > 0 else None,
    }


def _in(r: dict[str, Any], w: tuple[str, str]) -> bool:
    return w[0] <= r["date"] <= w[1]


def breakdown(rows: Sequence[dict[str, Any]], key: str) -> dict[str, Any]:
    groups: dict[str, list] = defaultdict(list)
    for r in rows:
        groups[str(r.get(key))].append(r)
    return {k: {f: s[f] for f in ("terminal", "win_rate", "net_pnl", "profit_factor")}
            for k, v in sorted(groups.items()) for s in [summarize(v)]}


def null_threshold(tf: str, instrument: str, bars: Sequence[Bar], offsets: Offsets, ctx: Context,
                   cell_rows: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """95th percentile of the max candidate-cell PF under random direction flips."""
    tick, _ = contract_economics(instrument)
    cells: dict[str, list[tuple[float, float, str]]] = {}
    for key, rows in cell_rows.items():
        trades = []
        for r in rows:
            if r["result"] not in TERMINAL or not _in(r, MAIN_WINDOW):
                continue
            flip_dir = "SHORT" if r["direction"] == "LONG" else "LONG"
            fill = r["fill"]
            flipped = resolve_from(instrument, bars, offsets, r["fill_i"], flip_dir, fill,
                                   2 * fill - r["stop"], 2 * fill - r["target"], exit_td_for(tf, ctx, r["date"]))
            if flipped["net"] is None:
                continue
            trades.append((float(r["net_pnl"]), float(flipped["net"]), r["date"]))
        cells[key] = trades
    rng = random.Random(NULL_SEED)
    maxima: list[float] = []
    for _ in range(NULL_REPS):
        best = 0.0
        for trades in cells.values():
            if len(trades) < MIN_TRADES[tf]:
                continue
            pnls = [(b if rng.random() < 0.5 else a) for a, b, _d in trades]
            pf = _pf(pnls)
            value = float("inf") if (pf is None and any(p > 0 for p in pnls)) else (pf or 0.0)
            best = max(best, value)
        maxima.append(best)
    maxima.sort()
    t95 = maxima[int(round(0.95 * NULL_REPS)) - 1]
    return {"reps": NULL_REPS, "seed": NULL_SEED, "t95_max_pf": round(t95, 4) if t95 != float("inf") else "inf",
            "median_max_pf": round(maxima[NULL_REPS // 2], 4),
            "eligible_cells": sorted(k for k, v in cells.items() if len(v) >= MIN_TRADES[tf])}


def q1(tf: str, setup: str, arm_name: str, cell: dict[str, Any], baseline: Optional[dict[str, Any]], t95: float) -> dict[str, Any]:
    main, h1, h2 = cell["main"], cell["h1"], cell["h2"]
    pf = pf_value(main)
    share = main["top3_day_share_of_net"]
    checks = {
        "min_trades": main["terminal"] >= MIN_TRADES[tf],
        "pf_pass": pf >= max(t95, PF_FLOOR),
        "pf_floor": pf >= PF_FLOOR,
        "halves_positive": h1["net_pnl"] > 0 and h2["net_pnl"] > 0,
        "leave_best_day_positive": (main["leave_best_day_out_net"] or 0) > 0,
        "top3_under_half": share is not None and share < 0.5,
        "beats_baseline": None if baseline is None else pf > pf_value(baseline),
    }
    core = all(checks[k] for k in ("min_trades", "halves_positive", "leave_best_day_positive", "top3_under_half"))
    core = core and checks["beats_baseline"] is not False
    capped = tf == "daily" or (tf, setup) in PRIOR_EXPOSED
    if core and checks["pf_pass"] and not capped:
        verdict = "PASS"
    elif core and checks["pf_floor"]:
        verdict = "PROMISING_FORWARD_ONLY"
    else:
        verdict = "DOES_NOT_CLEAR"
    return {"checks": checks, "verdict": verdict, "capped_at_promising": capped}


def oos_label(tf: str, s: dict[str, Any]) -> str:
    need = OOS_MIN_TRADES[tf]
    if need is None or s["terminal"] < need:
        return "OOS_INSUFFICIENT"
    if s["net_pnl"] > 0 and pf_value(s) > 1.0:
        return "OOS_CONFIRMED"
    if s["net_pnl"] < 0:
        return "OOS_CONTRADICTED"
    return "OOS_INSUFFICIENT"


# --------------------------------------------------------------------------- #
# integrity (prereg §8)
# --------------------------------------------------------------------------- #


def integrity(tf_bars: dict[str, list[HBar]], bars: Sequence[Bar]) -> dict[str, Any]:
    from context.daily_22_swing_collector import _daily_sessions

    agg_ok = all(hb.high == max(b.high for b in bars[hb.i0:hb.i1]) and hb.low == min(b.low for b in bars[hb.i0:hb.i1])
                 for hbs in tf_bars.values() for hb in hbs)
    four_h_hours = sorted({hb.start.astimezone(ET).hour for hb in tf_bars["4h"] if hb.complete})
    sessions = _daily_sessions([{"ts": b.ts.isoformat(), "open": b.open, "high": b.high, "low": b.low, "close": b.close}
                                for b in bars])
    compared = same = 0
    mismatches = []
    for hb in tf_bars["daily"]:
        if not hb.complete or not (MAIN_WINDOW[0] <= hb.td <= OOS_WINDOW[1]):
            continue
        ref = sessions.get(date.fromisoformat(hb.td))
        compared += 1
        if ref and (ref["open"], ref["high"], ref["low"], ref["close"]) == (hb.open, hb.high, hb.low, hb.close):
            same += 1
        elif len(mismatches) < 10:
            mismatches.append({"td": hb.td, "ours": [hb.open, hb.high, hb.low, hb.close],
                               "lane": None if not ref else [ref["open"], ref["high"], ref["low"], ref["close"]]})
    rate = same / compared if compared else 0.0
    return {"aggregation_extremes_ok": agg_ok, "four_hour_bucket_hours_et": four_h_hours,
            "four_hour_buckets_ok": set(four_h_hours) <= {18, 22, 2, 6, 10, 14},
            "daily_vs_lane_compared": compared, "daily_vs_lane_identical": same,
            "daily_vs_lane_rate": round(rate, 5), "daily_vs_lane_first_mismatches": mismatches,
            "passed": agg_ok and set(four_h_hours) <= {18, 22, 2, 6, 10, 14} and rate >= 0.99}


# --------------------------------------------------------------------------- #
# driver
# --------------------------------------------------------------------------- #


def run_instrument(instrument: str, dirs: Sequence[Path], manifests: Sequence[Path], *, with_null: bool) -> dict[str, Any]:
    bars, conflicts = load_bars(dirs)
    seams = load_seams(manifests, instrument)
    offsets = Offsets(seams)
    ctx = Context(bars, offsets)
    tick, _ = contract_economics(instrument)
    tf_bars = {tf: build_htf(tf, bars) for tf in TFS}
    result: dict[str, Any] = {
        "bars_15m": len(bars), "first_bar": bars[0].ts.isoformat(), "last_bar": bars[-1].ts.isoformat(),
        "source_conflicts": conflicts,
        "seams": [{"ts": s.isoformat(), "gap": g} for s, g in seams if bars[0].ts <= s <= bars[-1].ts],
        "fingerprints": {str(d): fingerprint(d) for d in dirs},
        "htf_bars": {tf: {"total": len(v), "complete": sum(hb.complete for hb in v)} for tf, v in tf_bars.items()},
    }
    result["integrity"] = integrity(tf_bars, bars)
    if not result["integrity"]["passed"]:
        result["aborted"] = "INTEGRITY_FAILED"
        return result

    result["timeframes"] = {}
    rows_out: dict[str, Any] = {}
    for tf in TFS:
        h = tf_bars[tf]
        armed = {j: arm(tf, h, j, tick, ctx, offsets) for j in range(3, len(h))}
        cells: dict[str, Any] = {}
        cell_rows: dict[str, list] = {}
        for setup, arms in ARMS.items():
            for arm_name in arms:
                rows = simulate_cell(tf, instrument, bars, h, armed, ctx, offsets, setup, arm_name)
                key = f"{setup}|{arm_name}"
                cell_rows[key] = rows
                main = [r for r in rows if _in(r, MAIN_WINDOW)]
                cell = {"main": summarize(main),
                        "h1": summarize(r for r in main if r["date"] < HALF_SPLIT),
                        "h2": summarize(r for r in main if r["date"] >= HALF_SPLIT),
                        "oos": summarize(r for r in rows if _in(r, OOS_WINDOW))}
                if arm_name in ("R", "M"):
                    taken = [r for r in main if r["result"] in TERMINAL]
                    cell["q3_by_ftfc"] = breakdown(taken, "alignment")
                    cell["q3_by_market_condition"] = breakdown(taken, "market_condition")
                cells[key] = cell
        tf_out: dict[str, Any] = {"cells": cells}
        if with_null:
            cand_rows = {f"{s}|{a}": cell_rows[f"{s}|{a}"] for s, arms in CANDIDATES.items() for a in arms}
            tf_out["null"] = null_threshold(tf, instrument, bars, offsets, ctx, cand_rows)
            t95_raw = tf_out["null"]["t95_max_pf"]
            t95 = float("inf") if t95_raw == "inf" else float(t95_raw)
            for setup, arms in CANDIDATES.items():
                for arm_name in arms:
                    cell = cells[f"{setup}|{arm_name}"]
                    base_key = "hammer_level|R+F" if setup == "hammer_level" else f"{setup}|R"
                    baseline = None if (setup == "hammer_level" and arm_name == "R+F") else cells[base_key]["main"]
                    cell["q1"] = q1(tf, setup, arm_name, cell, baseline, t95)
                    cell["oos_label"] = oos_label(tf, cell["oos"])
        result["timeframes"][tf] = tf_out
        rows_out[tf] = cell_rows
    result["_rows"] = rows_out
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canonical-root", type=Path, required=True)
    parser.add_argument("--v2-root", type=Path, required=True)
    parser.add_argument("--ext-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--rows-out", type=Path, required=True)
    args = parser.parse_args(argv)

    report: dict[str, Any] = {
        "study": "strat_htf_magnitude_ftfc_v1",
        "prereg": "docs/prereg-strat-htf-magnitude-ftfc-2026-09-23.md",
        "mode": "RESEARCH_ONLY",
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "frozen_rules": {"slippage_ticks": SLIPPAGE_TICKS, "round_turn_commission": ROUND_TURN_COMMISSION,
                         "min_trades": MIN_TRADES, "oos_min_trades": OOS_MIN_TRADES, "exit_days_after": EXIT_DAYS_AFTER,
                         "max_fills_per_day": MAX_FILLS_PER_DAY, "pf_floor": PF_FLOOR, "null_reps": NULL_REPS,
                         "null_seed": NULL_SEED, "main_window": MAIN_WINDOW, "half_split": HALF_SPLIT,
                         "oos_window": OOS_WINDOW, "prior_exposed": sorted(map(list, PRIOR_EXPOSED))},
        "instruments": {},
    }
    rows_all: dict[str, Any] = {}
    for instrument in ("MNQ", "MES"):
        dirs = [args.canonical_root / instrument, args.v2_root / instrument, args.ext_root / instrument]
        manifests = [args.v2_root / instrument / "MANIFEST.json", args.ext_root / instrument / "MANIFEST.json"]
        res = run_instrument(instrument, dirs, manifests, with_null=instrument == "MNQ")
        rows_all[instrument] = res.pop("_rows", {})
        report["instruments"][instrument] = res
        if res.get("aborted"):
            break

    mnq = report["instruments"]["MNQ"]
    mes = report["instruments"].get("MES", {})
    if not mnq.get("aborted"):
        verdicts = {}
        for tf in TFS:
            for setup, arms in CANDIDATES.items():
                for arm_name in arms:
                    key = f"{setup}|{arm_name}"
                    cell = mnq["timeframes"][tf]["cells"][key]
                    v = {"q1": cell["q1"]["verdict"], "pf": cell["main"]["profit_factor"],
                         "net": cell["main"]["net_pnl"], "terminal": cell["main"]["terminal"]}
                    if v["q1"] != "DOES_NOT_CLEAR":
                        m = mes.get("timeframes", {}).get(tf, {}).get("cells", {}).get(key, {}).get("main")
                        v["q2_mes"] = "REPLICATED" if m and m["net_pnl"] > 0 and pf_value(m) > 1.0 else "NOT_REPLICATED"
                        v["q2b"] = cell["oos_label"]
                    verdicts[f"{tf}|{key}"] = v
        report["verdicts_mnq"] = verdicts
        report["null_thresholds_mnq"] = {tf: mnq["timeframes"][tf]["null"]["t95_max_pf"] for tf in TFS}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
    args.rows_out.parent.mkdir(parents=True, exist_ok=True)
    args.rows_out.write_text(json.dumps(rows_all, sort_keys=True, default=str) + "\n")
    print(json.dumps({"verdicts": report.get("verdicts_mnq"), "null": report.get("null_thresholds_mnq"),
                      "aborted": mnq.get("aborted")}, indent=1, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
