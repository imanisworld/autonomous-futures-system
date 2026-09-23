#!/usr/bin/env python3
"""Strat source rules (magnitude + FTFC) vs observer brackets — research audit.

Preregistered in docs/prereg-strat-rules-magnitude-ftfc-2026-09-23.md (#934).
Read-only: this script changes no strategy/runtime configuration and never
calls an external broker. PaperBroker is used only as the fill/exit model.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.futures_contracts import contract_economics
from execution.broker_interface import BracketOrder
from execution.paper_broker import NextBarOHLC, PaperBroker
from strategy.strat_classifier import StratBar, classify_bar, classify_sequence

ET = ZoneInfo("America/New_York")
BAR = timedelta(minutes=15)
SESSION_REOPEN = time(18, 0)

IOC_TOLERANCE_TICKS = {"MNQ": 32.0, "MES": 16.0}
SLIPPAGE_TICKS = 1.0
ROUND_TURN_COMMISSION = 1.48
MAX_FILLS_PER_DAY = 3

MIN_TRADES = 40
PF_PASS = 2.55
PF_PROMISING = 1.94
OOS_MIN_TRADES = 10

MAIN_WINDOW = ("2025-08-01", "2026-07-23")
HALF_SPLIT = "2026-01-22"
OOS_WINDOWS = {"oos_a": ("2026-07-24", "2026-09-14"), "oos_b": ("2026-09-15", "2026-09-21")}

# setup -> (classifier sequence or None, arms)
SETUPS: dict[str, tuple[str | None, tuple[str, ...]]] = {
    "rev_2_2": ("strat_22_reversal", ("A", "A+F", "S", "S+F")),
    "rev_3_2_2": ("strat_322_reversal", ("A", "A+F", "S", "S+F")),
    "rev_1_2_2": ("strat_122", ("A", "A+F", "S", "S+F")),
    "rev_2_1_2": ("strat_212_reversal", ("A", "A+F", "S", "S+F")),
    "s_3_1_2": ("strat_312", ("A", "A+F", "S", "S+F")),
    "hammer_shooter": (None, ("S", "S+F")),
    "cont_2_2": ("strat_22_continuation", ("A", "A+F")),
}
CANDIDATE_ARMS = {
    "rev_2_2": ("A+F", "S", "S+F"),
    "rev_3_2_2": ("A+F", "S", "S+F"),
    "rev_1_2_2": ("A+F", "S", "S+F"),
    "rev_2_1_2": ("A+F", "S", "S+F"),
    "s_3_1_2": ("A+F", "S", "S+F"),
    "hammer_shooter": ("S", "S+F"),
    "cont_2_2": ("A+F",),
}
# Strat stop sits beyond the ENTRY bar (t) for these; beyond the inside bar (t-1) otherwise.
STOP_BEYOND_T = {"rev_2_2", "rev_3_2_2", "rev_1_2_2", "hammer_shooter"}
# Magnitude bar offset back from t.
MAGNITUDE_OFFSET = {"rev_1_2_2": 3}  # the motherbar; every other setup uses t-2


@dataclass(frozen=True)
class Bar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    market_condition: str | None
    trading_date: str


@dataclass(frozen=True)
class Seam:
    ts: datetime  # first new-contract bar
    gap: float


@dataclass
class Signal:
    index: int
    setup: str
    direction: str
    ftfc: str  # UP / DOWN / CONFLICT / UNKNOWN
    skip: str | None = None  # GAP / ROLL
    geometry: dict[str, tuple[float, float, float]] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# calendar / data
# --------------------------------------------------------------------------- #


def trading_date(ts: datetime) -> str:
    """CME trading date: a session starting 18:00 ET belongs to the next weekday."""
    local = ts.astimezone(ET)
    day = local.date()
    if local.time() >= SESSION_REOPEN:
        day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day.isoformat()


def _parse_ts(value: str) -> datetime:
    ts = datetime.fromisoformat(value)
    if ts.tzinfo is None:
        raise ValueError(f"naive timestamp {value!r}")
    return ts.astimezone(timezone.utc)


def load_bars(dirs: Sequence[Path]) -> list[Bar]:
    """Concatenate corpus directories; an overlapping bar must be OHLC-identical."""
    by_ts: dict[datetime, Bar] = {}
    for directory in dirs:
        files = sorted(directory.glob("*.jsonl"))
        if not files:
            raise RuntimeError(f"no .jsonl files in {directory}")
        for path in files:
            for line in path.read_text().splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                ts = _parse_ts(row["timestamp"])
                bar = Bar(
                    ts=ts,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    market_condition=row.get("market_condition"),
                    trading_date=trading_date(ts),
                )
                prior = by_ts.get(ts)
                if prior is not None:
                    if (prior.open, prior.high, prior.low, prior.close) != (
                        bar.open, bar.high, bar.low, bar.close,
                    ):
                        raise RuntimeError(f"overlap conflict at {ts.isoformat()}")
                    continue
                by_ts[ts] = bar
    return [by_ts[k] for k in sorted(by_ts)]


def load_seams(manifests: Sequence[Path], instrument: str) -> list[Seam]:
    seams: dict[datetime, Seam] = {}
    for path in manifests:
        payload = json.loads(path.read_text())
        if payload.get("instrument") != instrument:
            raise RuntimeError(f"{path} is not a {instrument} manifest")
        for entry in payload.get("roll_ledger") or []:
            ts = _parse_ts(entry["first_new_bar"])
            seams[ts] = Seam(ts=ts, gap=float(entry["gap_points"]))
    return [seams[k] for k in sorted(seams)]


def fingerprint(dirs: Sequence[Path]) -> str:
    digest = hashlib.sha256()
    for directory in dirs:
        for path in sorted(directory.glob("*.jsonl")):
            digest.update(f"{directory.name}/{path.name}\n".encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def is_gap(prev: Bar, cur: Bar) -> bool:
    """A missing bar inside a session. Session reopens (18:00 ET) are expected."""
    if cur.ts - prev.ts == BAR:
        return False
    return cur.ts.astimezone(ET).time() != SESSION_REOPEN


# --------------------------------------------------------------------------- #
# FTFC (causal opens, back-adjusted across seams)
# --------------------------------------------------------------------------- #


class Opens:
    def __init__(self, bars: Sequence[Bar], seams: Sequence[Seam]):
        self.bars = bars
        self.seams = seams
        self.index = {bar.ts: i for i, bar in enumerate(bars)}
        self.day_open: dict[str, int | None] = {}
        first_bar_of_day: dict[str, int] = {}
        for i, bar in enumerate(bars):
            first_bar_of_day.setdefault(bar.trading_date, i)
        for day, i in first_bar_of_day.items():
            # Known only when the day's first bar IS the 18:00 ET reopen bar.
            self.day_open[day] = i if bars[i].ts.astimezone(ET).time() == SESSION_REOPEN else None
        self.week_first: dict[tuple[int, int], str] = {}
        self.month_first: dict[tuple[int, int], str] = {}
        for day in sorted(first_bar_of_day):
            d = date.fromisoformat(day)
            self.week_first.setdefault(tuple(d.isocalendar())[:2], day)
            self.month_first.setdefault((d.year, d.month), day)

    def _offset(self, ts: datetime) -> float:
        return sum(seam.gap for seam in self.seams if seam.ts <= ts)

    def opens_at(self, i: int) -> dict[str, int | None]:
        bar = self.bars[i]
        d = date.fromisoformat(bar.trading_date)
        week_day = self.week_first[tuple(d.isocalendar())[:2]]
        month_day = self.month_first[(d.year, d.month)]
        local = bar.ts.astimezone(ET)
        hour_start = local.replace(minute=0, second=0, microsecond=0).astimezone(timezone.utc)
        hour_i = self.index.get(hour_start)
        if hour_i is not None and self.bars[hour_i].trading_date != bar.trading_date:
            hour_i = None
        return {
            "month": self.day_open.get(month_day),
            "week": self.day_open.get(week_day),
            "day": self.day_open.get(bar.trading_date),
            "hour": hour_i,
        }

    def state(self, i: int) -> str:
        bar = self.bars[i]
        opens = self.opens_at(i)
        if any(j is None for j in opens.values()):
            return "UNKNOWN"
        values = []
        for j in opens.values():
            if self.bars[j].ts > bar.ts:  # causality guard; unreachable by construction
                raise AssertionError("FTFC open after decision bar")
            values.append(self.bars[j].open + self._offset(bar.ts) - self._offset(self.bars[j].ts))
        if all(bar.close > v for v in values):
            return "UP"
        if all(bar.close < v for v in values):
            return "DOWN"
        return "CONFLICT"


# --------------------------------------------------------------------------- #
# detection
# --------------------------------------------------------------------------- #


def bar_type(cur: Bar, prev: Bar) -> str:
    return classify_bar(StratBar(high=cur.high, low=cur.low), StratBar(high=prev.high, low=prev.low))


def is_hammer(bar: Bar) -> bool:
    rng = bar.high - bar.low
    return rng > 0 and min(bar.open, bar.close) >= bar.low + rng * 2.0 / 3.0


def is_shooter(bar: Bar) -> bool:
    rng = bar.high - bar.low
    return rng > 0 and max(bar.open, bar.close) <= bar.low + rng / 3.0


def _geometry(setup: str, arm_geom: str, direction: str, bars: Sequence[Bar], i: int, tick: float):
    t, t1 = bars[i], bars[i - 1]
    long = direction == "LONG"
    entry = t1.high + tick if long else t1.low - tick
    if arm_geom == "A":
        stop = t1.low - tick if long else t1.high + tick
        risk = abs(entry - stop)
        target = entry + 2 * risk if long else entry - 2 * risk
        return entry, stop, target
    stop_bar = t if setup in STOP_BEYOND_T else t1
    stop = stop_bar.low - tick if long else stop_bar.high + tick
    mag = bars[i - MAGNITUDE_OFFSET.get(setup, 2)]
    target = mag.high if long else mag.low
    return entry, stop, target


def detect(bars: Sequence[Bar], seams: Sequence[Seam], opens: Opens, tick: float) -> dict[int, list[Signal]]:
    day_last: dict[str, int] = {}
    for i, bar in enumerate(bars):
        day_last[bar.trading_date] = i
    seam_ts = [s.ts for s in seams]
    out: dict[int, list[Signal]] = defaultdict(list)
    for i in range(3, len(bars)):
        t = bars[i]
        types = [bar_type(bars[i - k], bars[i - k - 1]) for k in (2, 1, 0)]
        ctx = classify_sequence(*types)
        found: list[tuple[str, str]] = []
        for setup, (sequence, _arms) in SETUPS.items():
            if sequence and ctx.strat_sequence == sequence and ctx.strat_direction in ("LONG", "SHORT"):
                found.append((setup, ctx.strat_direction))
        t1 = bars[i - 1]
        if is_hammer(t1) and types[2] == "two_up":
            found.append(("hammer_shooter", "LONG"))
        elif is_shooter(t1) and types[2] == "two_down":
            found.append(("hammer_shooter", "SHORT"))
        if not found:
            continue
        span = bars[i - 3 : i + 1]  # t-3 .. t
        gap = any(is_gap(a, b) for a, b in zip(span, span[1:]))
        day_end = bars[day_last[t.trading_date]].ts
        roll = any(bars[i - 3].ts < s <= day_end for s in seam_ts)
        ftfc = opens.state(i)
        for setup, direction in found:
            sig = Signal(index=i, setup=setup, direction=direction, ftfc=ftfc,
                         skip="GAP" if gap else "ROLL" if roll else None)
            for geom in ("A", "S"):
                sig.geometry[geom] = _geometry(setup, geom, direction, bars, i, tick)
            out[i].append(sig)
    return out


# --------------------------------------------------------------------------- #
# simulation
# --------------------------------------------------------------------------- #


def _broker(instrument: str) -> PaperBroker:
    return PaperBroker(
        starting_balance=100_000.0,
        slippage_ticks=SLIPPAGE_TICKS,
        pessimistic_both_hit=True,
        breakeven_at_1r=False,
        runner_mode=False,
        entry_fill_model="ioc_limit",
        entry_tolerance_ticks_by_root={instrument: IOC_TOLERANCE_TICKS[instrument]},
    )


def ftfc_alignment(direction: str, state: str) -> str:
    if state == "CONFLICT":
        return "conflict"
    aligned = (direction == "LONG" and state == "UP") or (direction == "SHORT" and state == "DOWN")
    return "aligned" if aligned else "against"


def simulate(instrument: str, bars: Sequence[Bar], seams: Sequence[Seam],
             signals: dict[int, list[Signal]], setup: str, arm: str) -> list[dict[str, Any]]:
    tick, tick_value = contract_economics(instrument)
    tol = IOC_TOLERANCE_TICKS[instrument] * tick
    slip = SLIPPAGE_TICKS * tick
    seam_ts = [s.ts for s in seams]
    geom = "A" if arm.startswith("A") else "S"
    filtered = arm.endswith("+F")
    rows: list[dict[str, Any]] = []
    broker: PaperBroker | None = None
    active: dict[str, Any] | None = None
    fills_by_day: Counter[str] = Counter()

    def close_manual(row: dict[str, Any], exit_bar: Bar, reason: str) -> None:
        sign = 1 if row["direction"] == "LONG" else -1
        exit_px = exit_bar.close - slip * sign
        gross = (exit_px - row["actual_entry"]) * sign / tick * tick_value
        row.update(result=reason, exit_reason=reason, exit_price=exit_px, exit_ts=exit_bar.ts.isoformat(),
                   gross_pnl=round(gross, 2), net_pnl=round(gross - ROUND_TURN_COMMISSION, 2))

    for i, bar in enumerate(bars):
        if active is not None and broker is not None:
            prev = bars[i - 1]
            if bar.trading_date != active["date"]:
                close_manual(active, prev, "SESSION_END")
                active = broker = None
            elif any(prev.ts < s <= bar.ts for s in seam_ts):
                close_manual(active, prev, "ROLL_EXIT")
                active = broker = None
            else:
                fill = broker._resolve_position_impl(  # research: bypass mirror hook
                    NextBarOHLC(high=bar.high, low=bar.low, open=bar.open)
                )
                if fill is not None:
                    gross = float(fill.pnl_dollars or 0.0)
                    active.update(result=str(fill.result), exit_reason=fill.exit_reason,
                                  exit_price=fill.exit_price, exit_ts=bar.ts.isoformat(),
                                  gross_pnl=round(gross, 2),
                                  net_pnl=round(gross - ROUND_TURN_COMMISSION, 2))
                    active = broker = None

        for sig in signals.get(i, ()):
            if sig.setup != setup:
                continue
            entry, stop, target = sig.geometry[geom]
            row: dict[str, Any] = {
                "date": bar.trading_date, "decision_ts": bar.ts.isoformat(), "direction": sig.direction,
                "ftfc": sig.ftfc, "alignment": None if sig.ftfc == "UNKNOWN" else ftfc_alignment(sig.direction, sig.ftfc),
                "market_condition": bar.market_condition, "planned_entry": entry, "stop": stop,
                "target": target, "decision_close": bar.close, "actual_entry": None, "result": None,
                "net_pnl": None,
            }
            rows.append(row)
            if sig.skip:
                row["result"] = sig.skip
                continue
            if sig.ftfc == "UNKNOWN":
                row["result"] = "FTFC_UNKNOWN"
                continue
            if filtered and row["alignment"] != "aligned":
                row["result"] = "FTFC_FILTERED"
                continue
            long = sig.direction == "LONG"
            limit_px = entry + tol if long else entry - tol
            marketable = bar.close <= limit_px if long else bar.close >= limit_px
            if geom == "S" and marketable:
                expected = min(limit_px, bar.close + slip) if long else max(limit_px, bar.close - slip)
                room = (target - expected) if long else (expected - target)
                if room < tick:
                    row["result"] = "MAGNITUDE_INVALID"
                    continue
            if active is not None:
                row["result"] = "SKIPPED_BUSY"
                continue
            if fills_by_day[bar.trading_date] >= MAX_FILLS_PER_DAY:
                row["result"] = "SKIPPED_MAX_FILLS"
                continue
            candidate_broker = _broker(instrument)
            order = BracketOrder(instrument=instrument, direction=sig.direction, entry=entry, stop=stop,
                                 target=target, rr_ratio=abs(target - entry) / abs(entry - stop),
                                 strategy=f"{setup}:{arm}", contracts=1,
                                 post_fill_validation_required=False)
            fill = candidate_broker._execute_bracket_impl(order, market_price=bar.close)  # research: bypass mirror hook
            if fill.result != "OPEN":
                row.update(result="NO_FILL", exit_reason=fill.exit_reason or fill.no_fill_reason or fill.result)
                continue
            row.update(actual_entry=float(fill.entry_price), result="OPEN")
            broker, active = candidate_broker, row
            fills_by_day[bar.trading_date] += 1

    return rows


# --------------------------------------------------------------------------- #
# scoring
# --------------------------------------------------------------------------- #


TERMINAL_OUTCOMES = {"WIN", "LOSS", "BREAKEVEN", "SESSION_END", "ROLL_EXIT"}


def _pf(pnls: list[float]) -> float | None:
    gp = sum(p for p in pnls if p > 0)
    gl = -sum(p for p in pnls if p < 0)
    return round(gp / gl, 4) if gl > 0 else None


def summarize(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(rows)
    terminal = sorted((r for r in rows if r["result"] in TERMINAL_OUTCOMES), key=lambda r: r["decision_ts"])
    pnls = [float(r["net_pnl"]) for r in terminal]
    net = round(sum(pnls), 2)
    by_day: dict[str, float] = defaultdict(float)
    for r in terminal:
        by_day[r["date"]] += float(r["net_pnl"])
    best = max(by_day.items(), key=lambda kv: kv[1]) if by_day else (None, 0.0)
    top3 = sum(sorted((p for p in by_day.values() if p > 0), reverse=True)[:3])
    equity = peak = dd = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
    outcomes = Counter(r["result"] for r in rows)
    pf = _pf(pnls)
    return {
        "signals": len(rows),
        "outcomes": dict(sorted(outcomes.items())),
        "terminal": len(terminal),
        "wins": sum(p > 0 for p in pnls),
        "win_rate": round(sum(p > 0 for p in pnls) / len(pnls), 4) if pnls else None,
        "net_pnl": net,
        "expectancy": round(net / len(pnls), 2) if pnls else None,
        "profit_factor": pf,
        "profit_factor_infinite": bool(pnls and pf is None and any(p > 0 for p in pnls)),
        "max_drawdown": round(dd, 2),
        "best_day": {"date": best[0], "net_pnl": round(best[1], 2)},
        "leave_best_day_out_net": round(net - best[1], 2) if by_day else None,
        "top3_day_share_of_net": round(top3 / net, 4) if net > 0 else None,
    }


def _in(row: dict[str, Any], window: tuple[str, str]) -> bool:
    return window[0] <= row["date"] <= window[1]


def _pf_value(summary: dict[str, Any]) -> float:
    if summary["profit_factor_infinite"]:
        return float("inf")
    return float(summary["profit_factor"] or 0.0)


def q1_gate(main: dict[str, Any], h1: dict[str, Any], h2: dict[str, Any], baseline: dict[str, Any] | None) -> dict[str, Any]:
    pf = _pf_value(main)
    share = main["top3_day_share_of_net"]
    checks = {
        "min_trades": main["terminal"] >= MIN_TRADES,
        "pf_pass": pf >= PF_PASS,
        "halves_positive": h1["net_pnl"] > 0 and h2["net_pnl"] > 0,
        "leave_best_day_positive": (main["leave_best_day_out_net"] or 0) > 0,
        "top3_under_half": share is not None and share < 0.5,
        "beats_arm_a": None if baseline is None else pf > _pf_value(baseline),
    }
    others = [checks[k] for k in ("min_trades", "halves_positive", "leave_best_day_positive", "top3_under_half")]
    beats = checks["beats_arm_a"] is not False  # N/A (no arm A for hammer) does not block
    if all(others) and beats and checks["pf_pass"]:
        verdict = "PASS"
    elif all(others) and beats and pf >= PF_PROMISING:
        verdict = "PROMISING_FORWARD_ONLY"
    else:
        verdict = "DOES_NOT_CLEAR"
    return {"checks": checks, "verdict": verdict}


def oos_label(summary: dict[str, Any]) -> str:
    if summary["terminal"] >= OOS_MIN_TRADES and summary["net_pnl"] > 0 and _pf_value(summary) > 1.0:
        return "OOS_CONFIRMED"
    if summary["terminal"] >= OOS_MIN_TRADES and summary["net_pnl"] < 0:
        return "OOS_CONTRADICTED"
    return "OOS_INSUFFICIENT"


def breakdown(rows: Sequence[dict[str, Any]], key: str) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[str(r.get(key))].append(r)
    out = {}
    for name, items in sorted(groups.items()):
        s = summarize(items)
        out[name] = {k: s[k] for k in ("terminal", "win_rate", "net_pnl", "profit_factor")}
    return out


# --------------------------------------------------------------------------- #
# parity (prereg §8.1)
# --------------------------------------------------------------------------- #


def parity_check(corpus_dir: Path, bars: Sequence[Bar], signals: dict[int, list[Signal]], window: tuple[str, str]) -> dict[str, Any]:
    """Compare detections with the observer's own engine state + shadow brackets."""
    from config.settings import load_config
    from replay.candle_loader import ReplayCandleLoader
    from replay.replay_engine import ReplayEngine
    from strategy.shadow_setups import _missing_strat_family

    engine = ReplayEngine(config=load_config(), log_dir="/tmp/strat-rules-magnitude-ftfc-audit")
    loader = ReplayCandleLoader()
    index = {bar.ts: i for i, bar in enumerate(bars)}
    seq_setup = {seq: s for s, (seq, _a) in SETUPS.items() if seq}
    engine_ids: set[tuple] = set()
    engine_geom: dict[tuple, tuple[float, float]] = {}
    previous = None
    for path in sorted(corpus_dir.glob("*.jsonl")):
        for candle in loader.load_jsonl(path):
            state = engine._market_state_from_candle(candle, previous)
            previous = candle
            ts = _parse_ts(str(candle.timestamp)) if not isinstance(candle.timestamp, datetime) else candle.timestamp.astimezone(timezone.utc)
            i = index.get(ts)
            if i is None or not (window[0] <= bars[i].trading_date <= window[1]):
                continue
            strat = state.strat
            seq = getattr(strat, "strat_sequence", None)
            direction = getattr(strat, "strat_direction", None)
            if seq in seq_setup and direction in ("LONG", "SHORT"):
                key = (i, seq_setup[seq], direction)
                engine_ids.add(key)
                cand = _missing_strat_family(state)
                if cand is not None:
                    engine_geom[key] = (float(cand.entry), float(cand.stop))

    mine_ids: set[tuple] = set()
    mine_geom: dict[tuple, tuple[float, float]] = {}
    for i, sigs in signals.items():
        if not (window[0] <= bars[i].trading_date <= window[1]):
            continue
        for sig in sigs:
            if sig.setup in seq_setup.values() and sig.skip is None:
                key = (i, sig.setup, sig.direction)
                mine_ids.add(key)
                mine_geom[key] = sig.geometry["A"][:2]
    # Bars skipped for GAP/ROLL on our side are excluded from the engine side too.
    skipped = {(i, s.setup, s.direction) for i, sigs in signals.items() for s in sigs if s.skip}
    engine_ids -= skipped
    union = mine_ids | engine_ids
    both = mine_ids & engine_ids
    geom_keys = [k for k in both if k in engine_geom]
    geom_same = [k for k in geom_keys if all(abs(a - b) < 1e-9 for a, b in zip(mine_geom[k], engine_geom[k]))]
    rate = len(both) / len(union) if union else 1.0
    geom_rate = len(geom_same) / len(geom_keys) if geom_keys else 1.0
    per_setup = {}
    for setup in seq_setup.values():
        u = {k for k in union if k[1] == setup}
        b = {k for k in both if k[1] == setup}
        per_setup[setup] = {"union": len(u), "match": len(b), "rate": round(len(b) / len(u), 5) if u else None}
    mismatches = sorted(union - both)[:25]
    return {
        "identity_match_rate": round(rate, 5),
        "identity_union": len(union),
        "only_script": len(mine_ids - engine_ids),
        "only_engine": len(engine_ids - mine_ids),
        "geometry_compared": len(geom_keys),
        "geometry_match_rate": round(geom_rate, 5),
        "per_setup": per_setup,
        "first_mismatches": [
            {"ts": bars[i].ts.isoformat(), "setup": s, "direction": d, "side": "script" if (i, s, d) in mine_ids else "engine"}
            for i, s, d in mismatches
        ],
        "passed": rate >= 0.99 and geom_rate >= 0.99,
    }


# --------------------------------------------------------------------------- #
# driver
# --------------------------------------------------------------------------- #


def run_instrument(instrument: str, main_dir: Path, ext_dir: Path, manifests: Sequence[Path], parity: bool) -> dict[str, Any]:
    bars = load_bars([main_dir, ext_dir])
    seams = load_seams(manifests, instrument)
    tick, _ = contract_economics(instrument)
    opens = Opens(bars, seams)
    signals = detect(bars, seams, opens, tick)
    result: dict[str, Any] = {
        "bars": len(bars),
        "first_bar": bars[0].ts.isoformat(),
        "last_bar": bars[-1].ts.isoformat(),
        "seams": [{"ts": s.ts.isoformat(), "gap": s.gap} for s in seams if bars[0].ts <= s.ts <= bars[-1].ts],
        "corpus_fingerprint": {"main": fingerprint([main_dir]), "extension": fingerprint([ext_dir])},
    }
    if parity:
        result["parity"] = parity_check(main_dir, bars, signals, MAIN_WINDOW)
        if not result["parity"]["passed"]:
            result["aborted"] = "PARITY_FAILED"
            return result

    unknown = Counter()
    for sigs in signals.values():
        for s in sigs:
            if _in({"date": bars[s.index].trading_date}, MAIN_WINDOW) and s.skip is None and s.ftfc == "UNKNOWN":
                unknown[s.setup] += 1
    result["ftfc_unknown_signals_main"] = dict(unknown)

    cells: dict[str, Any] = {}
    all_rows: dict[str, list[dict[str, Any]]] = {}
    for setup, (_seq, arms) in SETUPS.items():
        for arm in arms:
            rows = simulate(instrument, bars, seams, signals, setup, arm)
            all_rows[f"{setup}|{arm}"] = rows
            main = [r for r in rows if _in(r, MAIN_WINDOW)]
            cell = {
                "main": summarize(main),
                "h1": summarize(r for r in main if r["date"] < HALF_SPLIT),
                "h2": summarize(r for r in main if r["date"] >= HALF_SPLIT),
            }
            oos_rows = {name: [r for r in rows if _in(r, w)] for name, w in OOS_WINDOWS.items()}
            for name, items in oos_rows.items():
                cell[name] = summarize(items)
            cell["oos_combined"] = summarize(oos_rows["oos_a"] + oos_rows["oos_b"])
            if arm in ("A", "S"):
                taken = [r for r in main if r["result"] in TERMINAL_OUTCOMES]
                cell["q3_by_ftfc"] = breakdown(taken, "alignment")
                cell["q3_by_market_condition"] = breakdown(taken, "market_condition")
            cells[f"{setup}|{arm}"] = cell
    for setup, arms in CANDIDATE_ARMS.items():
        baseline = cells.get(f"{setup}|A", {}).get("main")
        for arm in arms:
            cell = cells[f"{setup}|{arm}"]
            cell["q1"] = q1_gate(cell["main"], cell["h1"], cell["h2"], baseline)
            cell["oos_label"] = oos_label(cell["oos_combined"])
    result["cells"] = cells
    result["_rows"] = all_rows
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--main-root", type=Path, required=True, help="dir holding MNQ/ and MES/ main corpus")
    parser.add_argument("--ext-root", type=Path, required=True, help="dir holding MNQ/ and MES/ extension pull")
    parser.add_argument("--manifest-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--rows-out", type=Path, required=True)
    args = parser.parse_args(argv)

    script_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report: dict[str, Any] = {
        "study": "strat_rules_magnitude_ftfc_v1",
        "prereg": "docs/prereg-strat-rules-magnitude-ftfc-2026-09-23.md",
        "mode": "RESEARCH_ONLY",
        "script_sha256": script_sha,
        "frozen_rules": {
            "ioc_tolerance_ticks": IOC_TOLERANCE_TICKS, "slippage_ticks": SLIPPAGE_TICKS,
            "round_turn_commission": ROUND_TURN_COMMISSION, "max_fills_per_day": MAX_FILLS_PER_DAY,
            "pessimistic_both_hit": True, "min_trades": MIN_TRADES, "pf_pass": PF_PASS,
            "pf_promising": PF_PROMISING, "main_window": MAIN_WINDOW, "half_split": HALF_SPLIT,
            "oos_windows": OOS_WINDOWS,
        },
        "instruments": {},
    }
    rows_out: dict[str, Any] = {}
    for instrument in ("MNQ", "MES"):
        manifests = [
            args.manifest_dir / f"replay_polygon_v2_{instrument}_MANIFEST.json",
            args.ext_root / instrument / "MANIFEST.json",
        ]
        res = run_instrument(instrument, args.main_root / instrument, args.ext_root / instrument, manifests,
                             parity=instrument == "MNQ")
        rows_out[instrument] = res.pop("_rows", {})
        report["instruments"][instrument] = res
        if res.get("aborted"):
            break

    mnq = report["instruments"]["MNQ"]
    mes = report["instruments"].get("MES", {})
    if not mnq.get("aborted"):
        verdicts = {}
        for setup, arms in CANDIDATE_ARMS.items():
            for arm in arms:
                key = f"{setup}|{arm}"
                cell = mnq["cells"][key]
                v = cell["q1"]["verdict"]
                entry = {"q1": v, "pf": cell["main"]["profit_factor"], "net": cell["main"]["net_pnl"],
                         "terminal": cell["main"]["terminal"]}
                if v != "DOES_NOT_CLEAR":
                    m = mes.get("cells", {}).get(key, {}).get("main")
                    entry["q2_mes"] = ("REPLICATED" if m and m["net_pnl"] > 0 and _pf_value(m) > 1.0 else "NOT_REPLICATED")
                    entry["q2b"] = cell["oos_label"]
                verdicts[key] = entry
        report["verdicts_mnq"] = verdicts
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    args.rows_out.parent.mkdir(parents=True, exist_ok=True)
    args.rows_out.write_text(json.dumps(rows_out, sort_keys=True) + "\n")
    print(json.dumps(report.get("verdicts_mnq") or report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
