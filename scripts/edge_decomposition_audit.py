#!/usr/bin/env python3
"""Edge decomposition audit: where does each strategy's edge disappear?

Evidence-only. No strategy, risk, replay, broker, config, or deployment code is
changed. Every fill and resolution below is produced by the real
``execution.paper_broker.PaperBroker``; every structural risk verdict by the
real ``risk.risk_engine.RiskEngine`` check methods; every full-engine funnel by
an isolated ``replay.replay_engine.ReplayEngine`` run configured with the same
config-only ``dataclasses.replace`` isolation the #367/#368/#372 closure audits
used.  Nothing is monkeypatched.

One standardized waterfall is computed per lane (strategy x instrument):

  A. raw signal            - the strategy's own detector / predicate, no gates
  B. time-exit control     - next-bar-open entry, exit at 30/60/120 min and EOD,
                             no stop: does the signal carry direction at all?
  C. documented bracket    - the strategy's own stop/target, fills assumed
                             (legacy market model), pessimistic same-bar
  D1. structural gates     - the RiskEngine's structural checks applied to
                             every raw candidate, path-independent
  D2. full engine, floors  - isolated ReplayEngine, drawdown/daily floors off,
      off                    so structural + signal-layer gates are not masked
  D3. full engine, frozen  - isolated ReplayEngine, production risk floors on
  E. IOC / costs           - production-matching ioc_limit fills at the
                             decision-bar close, 1/2/3-tick slippage

Cost convention: $1.48 round-turn commission at the analysis layer (matches
scripts/corrected_ioc_corpus_evidence.py, scripts/orb_breakout_canonical_
evidence.py, scripts/four_hr_retrigger_stop_study.py and the #372 matrix).
PaperBroker's own adverse slippage (entry + stop exit) is applied inside every
bracket P&L.  MNQ $2.00/pt, MES $5.00/pt.

Usage:
    python3 scripts/edge_decomposition_audit.py \
        --data-root ../../../data --transition-dir ../../../logs \
        --logs logs/edge_decomposition --engine-jobs 3 \
        --out scripts/edge_decomposition_audit_results.json \
        --report docs/edge-decomposition-audit-2026-09-07.md
"""
from __future__ import annotations

import argparse
import dataclasses
import gzip
import hashlib
import json
import math
import re
import statistics
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from config.settings import PositionSizingRule, load_config  # noqa: E402
from context.mnq_orb_breakout_inverse_paper import (  # noqa: E402
    MARKETABLE_TICKS as INVERSE_MARKETABLE_TICKS,
    _mirror_prices,
)
from execution.broker_interface import BracketOrder  # noqa: E402
from execution.day_only_exit import (  # noqa: E402
    DAY_ONLY_EXIT_REASON,
    EOD_BAR_MISSING,
    classify_result,
)
from execution.paper_broker import TICK_SIZE, TICK_VALUE, NextBarOHLC, PaperBroker  # noqa: E402
from replay.candle_loader import ReplayCandleLoader  # noqa: E402
from replay.replay_engine import ReplayEngine  # noqa: E402
from risk.risk_engine import DailyState, RiskEngine, TradeSetup  # noqa: E402
from strategy.confluence_scorer import score_setup  # noqa: E402
from strategy.four_hr_retrigger import (  # noqa: E402
    _completed_one_hour_stop,
    advance_4hr_retrigger,
    aggregate_et_bars,
)
from strategy.signal_engine import DecisionEngine, SetupDetail  # noqa: E402
from strategy.strat_322_first_live import advance_strat_322_first_live  # noqa: E402

ET = ZoneInfo("America/New_York")
COMMISSION_ROUND_TRIP = 1.48
HORIZONS_MIN = (30, 60, 120)
SLIPPAGE_SWEEP = (1.0, 2.0, 3.0)
IOC_TOLERANCE = {"MNQ": 32.0, "MES": 16.0}
POINT_VALUE = {root: TICK_VALUE[root] / TICK_SIZE[root] for root in ("MNQ", "MES")}
MAX_HOLD_TRADING_DAYS = 10
TRENDING_GATE_EXEMPT = {"strat_322_first_live"}
STRUCTURAL_RISK_CHECKS = (
    "_check_session",
    "_check_bracket_completeness",
    "_check_direction",
    "_check_entry_stop_target_distinct",
    "_check_rr_ratio",
    "_check_min_confluence_grade",
    "_check_min_target_distance",
    "_check_max_stop_distance",
)
PATH_DEPENDENT_RULES = {
    "max_drawdown", "max_daily_loss", "daily_trade_limit", "open_position_exists",
    "consecutive_loss_limit", "circuit_breaker", "session_trade_limit",
    "position_sizing_no_tier", "position_sizing_instrument", "position_sizing_contracts",
    "early_session_loss_floor", "profit_protect_gate", "win_streak_contracts_exceeded",
}


# ─── Lane definitions ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Lane:
    key: str
    strategy: str
    instrument: str
    corpus: str
    timeframe_minutes: int
    source: str                      # state_machine_4hr | state_machine_322 | research_miyagi | predicate | saved_json
    day_only: bool
    engine: bool                     # has an enabled_concepts entry on main
    predicate: Optional[str] = None
    campaign: Optional[str] = None   # orb_campaign | date_direction | None
    mirror: bool = False
    ioc_tolerance_ticks: Optional[float] = None
    funnel_from: Optional[str] = None
    saved_json: Optional[str] = None
    label: str = ""
    # armed_trigger: the signal bar itself trades through a pre-armed level, so
    # a fill at that level on the signal bar is real (4HR / 3-2-2 / Miyagi).
    # close_confirmed: the predicate is confirmed at bar close against a level
    # the bar has already passed, so the honest documented-bracket fill is a
    # resting order on the next bar (ORB / VWAP predicates, shadow setups).
    entry_style: str = "close_confirmed"

    @property
    def timeframe(self) -> str:
        return f"{self.timeframe_minutes}m"

    @property
    def tolerance(self) -> float:
        return (
            self.ioc_tolerance_ticks
            if self.ioc_tolerance_ticks is not None
            else IOC_TOLERANCE[self.instrument]
        )


CORPUS_5M = "replay_corpus_v1_5m_4hr_audit"
CORPUS_5M_LATE = "replay_corpus_v1_5m"
CORPUS_15M = "replay_corpus_v1_market_condition_fixed"

LANES: dict[str, Lane] = {
    "4hr_mnq": Lane("4hr_mnq", "strat_4hr_retrigger", "MNQ", CORPUS_5M, 5, "state_machine_4hr", True, True, label="4HR Re-Trigger MNQ", entry_style="armed_trigger"),
    "4hr_mes": Lane("4hr_mes", "strat_4hr_retrigger", "MES", CORPUS_5M, 5, "state_machine_4hr", True, True, label="4HR Re-Trigger MES", entry_style="armed_trigger"),
    "322_mnq": Lane("322_mnq", "strat_322_first_live", "MNQ", CORPUS_5M, 5, "state_machine_322", True, True, label="60M 3-2-2 First Live MNQ", entry_style="armed_trigger"),
    "miyagi_mnq": Lane("miyagi_mnq", "strat_12hr_miyagi", "MNQ", CORPUS_5M, 5, "research_miyagi", True, False, label="12HR Miyagi MNQ (causal stop)", entry_style="armed_trigger"),
    "miyagi_mes": Lane("miyagi_mes", "strat_12hr_miyagi", "MES", CORPUS_5M, 5, "research_miyagi", True, False, label="12HR Miyagi MES (causal stop)", entry_style="armed_trigger"),
    "orb_reclaim_mnq": Lane("orb_reclaim_mnq", "orb_reclaim", "MNQ", CORPUS_15M, 15, "predicate", False, True, predicate="_try_orb_reclaim", campaign="orb_campaign", label="ORB Reclaim MNQ"),
    "orb_reclaim_mes": Lane("orb_reclaim_mes", "orb_reclaim", "MES", CORPUS_15M, 15, "predicate", False, True, predicate="_try_orb_reclaim", campaign="orb_campaign", label="ORB Reclaim MES"),
    "orb_breakout_mnq": Lane("orb_breakout_mnq", "orb_breakout", "MNQ", CORPUS_15M, 15, "predicate", False, True, predicate="_try_orb_breakout", campaign="date_direction", label="ORB Breakout MNQ (source)"),
    "orb_breakout_inverse_mnq": Lane("orb_breakout_inverse_mnq", "orb_breakout", "MNQ", CORPUS_15M, 15, "predicate", False, False, predicate="_try_orb_breakout", campaign="date_direction", mirror=True, ioc_tolerance_ticks=float(INVERSE_MARKETABLE_TICKS), funnel_from="orb_breakout_mnq", label="ORB Breakout MNQ (inverted lane)"),
    "vwap_hold_mnq": Lane("vwap_hold_mnq", "vwap_hold", "MNQ", CORPUS_15M, 15, "predicate", False, True, predicate="_try_vwap_hold", label="VWAP Hold MNQ"),
    "transition_mnq": Lane("transition_mnq", "transition_failed_breakdown_reclaim", "MNQ", "replay_polygon_5m", 5, "saved_json", False, False, saved_json="missed_move_transition_MNQ_costed.json", label="Transition failed-breakdown reclaim MNQ (full corpus)"),
    "transition_mnq_audit": Lane("transition_mnq_audit", "transition_failed_breakdown_reclaim", "MNQ", CORPUS_5M_LATE, 5, "saved_json", False, False, saved_json="missed_move_transition_MNQ_5m_full_2026-05-01_to_2026-07-08.json", label="Transition failed-breakdown reclaim MNQ (May-Jul 2026 audit set, re-anchored)"),
    "transition_mes_audit": Lane("transition_mes_audit", "transition_failed_breakdown_reclaim", "MES", CORPUS_5M_LATE, 5, "saved_json", False, False, saved_json="missed_move_transition_MES_costed.json", label="Transition failed-breakdown reclaim MES (Apr-Jul 2026 set, re-anchored)"),
}


# ─── Corpus loading ──────────────────────────────────────────────────────────

def _parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass
class Bars:
    """Flat, chronologically sorted bars for one (corpus, instrument)."""
    corpus_dir: Path
    instrument: str
    rows: list[dict]
    files: list[Path]
    by_ts: dict[str, int]
    by_dt: dict[datetime, int]
    file_of_idx: list[int]           # index into files for each row
    bars_per_day: int

    def et(self, idx: int) -> datetime:
        return self.rows[idx]["_dt"].astimezone(ET)


_BARS_CACHE: dict[tuple[str, str], Bars] = {}


def load_bars(corpus_dir: Path, instrument: str) -> Bars:
    key = (str(corpus_dir), instrument)
    if key in _BARS_CACHE:
        return _BARS_CACHE[key]
    files = sorted((corpus_dir / instrument).glob(f"{instrument}_*.jsonl"))
    if not files:
        raise RuntimeError(f"no corpus files in {corpus_dir / instrument}")
    rows: list[dict] = []
    file_of_idx: list[int] = []
    for fi, path in enumerate(files):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            row["_dt"] = _parse_dt(row["timestamp"])
            rows.append(row)
            file_of_idx.append(fi)
    order = sorted(range(len(rows)), key=lambda i: rows[i]["_dt"])
    rows = [rows[i] for i in order]
    file_of_idx = [file_of_idx[i] for i in order]
    by_ts = {row["timestamp"]: i for i, row in enumerate(rows)}
    by_dt = {row["_dt"]: i for i, row in enumerate(rows)}
    bars = Bars(
        corpus_dir=corpus_dir, instrument=instrument, rows=rows, files=files,
        by_ts=by_ts, by_dt=by_dt, file_of_idx=file_of_idx,
        bars_per_day=max(1, round(len(rows) / len(files))),
    )
    _BARS_CACHE[key] = bars
    return bars


def _tree_fingerprint(root: Path) -> dict:
    paths = sorted(root.rglob("*.jsonl"))
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return {"files": len(paths), "sha256": digest.hexdigest()}


# ─── Candidate model ─────────────────────────────────────────────────────────

@dataclass
class Candidate:
    lane: str
    strategy: str
    instrument: str
    bar_ts: str
    bar_idx: int
    date: str
    session: str
    direction: str
    entry: float
    stop: float
    target: float
    campaign: Optional[str] = None
    extra: dict = field(default_factory=dict)
    state: Any = field(default=None, repr=False, compare=False)

    @property
    def rr(self) -> float:
        return RiskEngine.calculate_rr(self.direction, self.entry, self.stop, self.target)

    def stop_ticks(self) -> float:
        return abs(self.entry - self.stop) / TICK_SIZE[self.instrument]

    def to_dict(self) -> dict:
        return {
            "lane": self.lane, "strategy": self.strategy, "instrument": self.instrument,
            "bar_ts": self.bar_ts, "date": self.date, "session": self.session,
            "direction": self.direction, "entry": self.entry, "stop": self.stop,
            "target": self.target, "rr": round(self.rr, 4),
            "stop_ticks": round(self.stop_ticks(), 2), "campaign": self.campaign,
            "extra": self.extra,
        }


def _candidate(lane: Lane, bars: Bars, idx: int, direction: str, entry: float,
               stop: float, target: float, campaign: Optional[str] = None,
               extra: Optional[dict] = None) -> Candidate:
    row = bars.rows[idx]
    return Candidate(
        lane=lane.key, strategy=lane.strategy, instrument=lane.instrument,
        bar_ts=row["timestamp"], bar_idx=idx, date=bars.et(idx).date().isoformat(),
        session=str(row.get("session") or ""), direction=direction,
        entry=float(entry), stop=float(stop), target=float(target),
        campaign=campaign, extra=dict(extra or {}),
    )


# ─── Stage A: raw signal extraction ──────────────────────────────────────────

_ET_930 = (9, 30)


def _window_rows(bars: Bars, idx: int, lookback_bars: int) -> list[dict]:
    return bars.rows[max(0, idx - lookback_bars): idx + 1]


def extract_state_machine(lane: Lane, bars: Bars) -> list[Candidate]:
    """Walk the pure canonical state machine exactly as the engine would.

    The engine calls the state machine on every bar; before the setup bar it
    only ever returns FORMING and after a terminal status it returns the
    persisted state unchanged, so calling it only inside the setup/entry
    window reproduces the identical candidate sequence.
    """
    advance = advance_4hr_retrigger if lane.source == "state_machine_4hr" else advance_strat_322_first_live
    window_start = (9, 30) if lane.source == "state_machine_4hr" else (10, 0)
    window_end = (11, 0)
    lookback = bars.bars_per_day * 5
    candidates: list[Candidate] = []
    state: dict = {}
    current_day: Optional[date] = None
    for idx, row in enumerate(bars.rows):
        et = row["_dt"].astimezone(ET)
        if et.date() != current_day:
            current_day = et.date()
            state = {}
        hm = (et.hour, et.minute)
        if hm < window_start or hm >= window_end:
            continue
        if state.get("status") in {"TRIGGERED", "INVALIDATED", "EXPIRED"}:
            continue
        state, cand = advance(
            bars_5m=_window_rows(bars, idx, lookback), current_bar_ts=row["_dt"],
            instrument=lane.instrument, persisted_state=state,
        )
        if cand is not None:
            extra = {"entry_time": cand["entry_time"].isoformat()}
            if "stop_bar_ts" in cand:
                extra["stop_bar_ts"] = cand["stop_bar_ts"].isoformat()
            if cand.get("gap_open"):
                extra["gap_open"] = True
            candidates.append(_candidate(
                lane, bars, idx, cand["direction"], cand["entry"], cand["stop"],
                cand["target"], campaign=current_day.isoformat(), extra=extra,
            ))
    return candidates


def extract_miyagi(lane: Lane, bars: Bars) -> tuple[list[Candidate], dict]:
    """Research-detector candidates + the documented causal stop (rules §6).

    Source population: docs/strategy-rules/evidence_12hr_miyagi/*_results.json
    (the pure detector's own output).  The stop stored there carries the
    confirmed lookahead defect; the causal stop is recomputed here with the
    same helper the #366 closure used (`_completed_one_hour_stop`: high/low of
    the last COMPLETED 60-minute candle at the entry bar's close).  Entry is
    the first 5-minute bar in [09:30, 16:00) ET whose range crosses the
    trigger, the runtime entry window on the Miyagi branch.
    """
    path = REPO / "docs/strategy-rules/evidence_12hr_miyagi" / f"{lane.instrument.lower()}_results.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    audit = {"source": str(path.relative_to(REPO)), "detector_candidates": len(data["candidates"]),
             "trigger_not_hit": [], "stop_missing": [], "invalid_bracket": [], "date_missing": []}
    candidates: list[Candidate] = []
    for cand in data["candidates"]:
        day = date.fromisoformat(cand["date"])
        direction = str(cand["direction"]).upper()
        trigger = float(cand["entry_trigger"])
        day_idx = [
            i for i, row in enumerate(bars.rows)
            if row["_dt"].astimezone(ET).date() == day
            and (9, 30) <= (row["_dt"].astimezone(ET).hour, row["_dt"].astimezone(ET).minute) < (16, 0)
        ]
        if not day_idx:
            audit["date_missing"].append(cand["date"])
            continue
        hit = None
        for i in day_idx:
            row = bars.rows[i]
            if (row["high"] >= trigger) if direction == "LONG" else (row["low"] <= trigger):
                hit = i
                break
        if hit is None:
            audit["trigger_not_hit"].append(cand["date"])
            continue
        entry_close = bars.rows[hit]["_dt"] + timedelta(minutes=5)
        history = [r for r in _window_rows(bars, hit, bars.bars_per_day * 2)
                   if r["_dt"] + timedelta(minutes=5) <= entry_close]
        stop, stop_bar_ts = _completed_one_hour_stop(
            aggregate_et_bars(history, 60), entry_close.astimezone(ET), direction
        )
        if stop is None:
            audit["stop_missing"].append(cand["date"])
            continue
        target = float(cand["target"])
        valid = (stop < trigger < target) if direction == "LONG" else (target < trigger < stop)
        if not valid:
            audit["invalid_bracket"].append(cand["date"])
            continue
        candidates.append(_candidate(
            lane, bars, hit, direction, trigger, stop, target, campaign=cand["date"],
            extra={"lookahead_stop": cand.get("stop"), "stop_bar_ts": stop_bar_ts.isoformat(),
                   "target_2": cand.get("target_2")},
        ))
    return candidates, audit


class StateBuilder:
    """Builds MarketState for a corpus bar via the engine's own state builder."""

    def __init__(self, config, bars: Bars):
        self._tmp = tempfile.TemporaryDirectory(prefix="edge_decomp_state_")
        self.engine = ReplayEngine(config=config, log_dir=self._tmp.name)
        self.decision = DecisionEngine(config=config)
        self.bars = bars
        self._cache: dict[int, list] = {}
        self._loader = ReplayCandleLoader()

    def candles(self, file_index: int) -> list:
        if file_index not in self._cache:
            self._cache[file_index] = self._loader.load_jsonl(self.bars.files[file_index])
        return self._cache[file_index]

    def state_at(self, idx: int):
        fi = self.bars.file_of_idx[idx]
        candles = self.candles(fi)
        ts = self.bars.rows[idx]["timestamp"]
        pos = next((i for i, c in enumerate(candles) if c.timestamp == ts), None)
        if pos is None:
            return None
        prev = candles[pos - 1] if pos > 0 else None
        prev_prev = candles[pos - 2] if pos > 1 else None
        return self.engine._market_state_from_candle(candles[pos], prev, prev_prev)


def extract_predicate(lane: Lane, bars: Bars, builder: StateBuilder) -> list[Candidate]:
    """Evaluate the strategy's own `_try_*` predicate on every bar, no gates."""
    try_fn: Callable = getattr(builder.decision, lane.predicate)
    candidates: list[Candidate] = []
    for fi in range(len(bars.files)):
        candles = builder.candles(fi)
        prev = prev_prev = None
        for candle in candles:
            state = builder.engine._market_state_from_candle(candle, prev, prev_prev)
            prev_prev, prev = prev, candle
            setup: Optional[SetupDetail] = try_fn(state)
            if setup is None:
                continue
            idx = bars.by_ts.get(candle.timestamp)
            if idx is None:
                continue
            direction, entry, stop, target = setup.direction, setup.entry, setup.stop, setup.target
            extra = {}
            if lane.mirror:
                mirrored = _mirror_prices(direction, entry, stop, target)
                extra["source_direction"] = direction
                direction, entry, stop, target = (
                    mirrored["direction"], mirrored["entry"], mirrored["stop"], mirrored["target"]
                )
            et_date = bars.et(idx).date().isoformat()
            if lane.campaign == "orb_campaign":
                campaign = f"{et_date}:{candle.orb_high}:{candle.orb_low}"
            elif lane.campaign == "date_direction":
                campaign = f"{et_date}:{setup.direction}"
            else:
                campaign = None
            cand = _candidate(lane, bars, idx, direction, entry, stop, target,
                              campaign=campaign, extra=extra)
            cand.state = state
            candidates.append(cand)
    return candidates


def extract_saved_json(lane: Lane, bars: Bars, transition_dir: Path) -> tuple[list[Candidate], dict]:
    """Saved shadow-candidate population, re-anchored onto the corpus price basis.

    TradingView continuous-contract exports carry a contract-roll offset versus
    the Polygon corpus (0 or +292.75 MNQ, 0 or +62.5 MES in the saved sets).
    Every candidate's notes record the sweep bar's low on the export's own
    basis, so the offset is observable per candidate against the corpus bar
    immediately before the decision bar and snaps onto the (at most two) roll
    levels present; candidates that do not snap are bar-data discrepancies
    and are dropped as suspect rather than guessed.
    """
    path = transition_dir / lane.saved_json
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = [r for r in data["candidates"]
            if str(r.get("instrument", lane.instrument)).upper() == lane.instrument]
    parsed: list[tuple[dict, int, float]] = []
    offsets: Counter = Counter()
    missing = 0
    for row in rows:
        idx = bars.by_dt.get(_parse_dt(row["ts"]))
        if idx is None or idx == 0:
            missing += 1
            continue
        match = re.search(r"sweep_low=([\d.]+)", str(row["candidate"].get("notes") or ""))
        raw = round((float(match.group(1)) - float(bars.rows[idx - 1]["low"])) * 4) / 4 if match else 0.0
        offsets[raw] += 1
        parsed.append((row, idx, raw))
    levels = [value for value, _ in offsets.most_common(2)] or [0.0]
    audit: dict[str, Any] = {
        "source": str(path.name), "saved_candidates": len(rows), "bar_missing_in_corpus": missing,
        "price_offset_levels": levels, "price_offset_histogram": dict(offsets.most_common(6)),
        "reanchor_suspect_dropped": 0, "saved_outcomes": Counter(),
    }
    candidates: list[Candidate] = []
    for row, idx, raw in parsed:
        level = min(levels, key=lambda value: abs(value - raw))
        if abs(level - raw) > 0.25:
            audit["reanchor_suspect_dropped"] += 1
            continue
        c = row["candidate"]
        audit["saved_outcomes"][str((row.get("outcome") or {}).get("result"))] += 1
        candidates.append(_candidate(
            lane, bars, idx, str(c["direction"]).upper(),
            float(c["entry"]) - level, float(c["stop"]) - level, float(c["target"]) - level,
            extra={"saved_result": (row.get("outcome") or {}).get("result"),
                   "session_bucket": row.get("session_bucket"), "price_offset": level},
        ))
    audit["saved_outcomes"] = dict(audit["saved_outcomes"])
    return candidates, audit


# ─── Stage B: time-exit directional control ──────────────────────────────────

def _is_eod_bar(timestamp: str, timeframe_minutes: int) -> bool:
    """The bar that closes at 16:00 ET (15:55 for 5m, 15:45 for 15m)."""
    et = _parse_dt(timestamp).astimezone(ET)
    return (et.hour, et.minute) == divmod(16 * 60 - timeframe_minutes, 60)


def _eod_index(bars: Bars, from_idx: int, lane: Lane) -> Optional[int]:
    """Index of the exact EOD bar on the same ET date as ``from_idx``, strictly
    after it; None when missing."""
    day = bars.et(from_idx).date()
    limit = min(len(bars.rows), from_idx + bars.bars_per_day * 2)
    for j in range(from_idx + 1, limit):
        et = bars.et(j)
        if et.date() != day:
            return None
        if _is_eod_bar(bars.rows[j]["timestamp"], lane.timeframe_minutes):
            return j
    return None


def time_exit_control(lane: Lane, bars: Bars, cand: Candidate, slippage_ticks: float = 1.0) -> dict:
    idx = cand.bar_idx
    if idx + 1 >= len(bars.rows):
        return {"status": "NO_DATA"}
    entry_bar = bars.rows[idx + 1]
    tick = TICK_SIZE[cand.instrument]
    sign = 1.0 if cand.direction == "LONG" else -1.0
    entry = float(entry_bar["open"]) + sign * slippage_ticks * tick
    out = {"status": "OK", "entry_bar_ts": entry_bar["timestamp"], "entry": entry, "horizons": {}}
    exits: dict[str, Optional[int]] = {}
    for h in HORIZONS_MIN:
        target_dt = entry_bar["_dt"] + timedelta(minutes=h - lane.timeframe_minutes)
        exits[f"{h}m"] = bars.by_dt.get(target_dt)
    exits["EOD"] = _eod_index(bars, idx + 1, lane)
    if exits["EOD"] is not None and exits["EOD"] < idx + 1:
        exits["EOD"] = None
    for label, j in exits.items():
        if j is None:
            out["horizons"][label] = None
            continue
        exit_px = float(bars.rows[j]["close"]) - sign * slippage_ticks * tick
        path = bars.rows[idx + 1: j + 1]
        highs = [float(r["high"]) for r in path]
        lows = [float(r["low"]) for r in path]
        mfe = (max(highs) - entry) if sign > 0 else (entry - min(lows))
        mae = (entry - min(lows)) if sign > 0 else (max(highs) - entry)
        pts = sign * (exit_px - entry)
        out["horizons"][label] = {
            "exit_bar_ts": bars.rows[j]["timestamp"],
            "pts": round(pts, 4),
            "net": round(pts * POINT_VALUE[cand.instrument] - COMMISSION_ROUND_TRIP, 2),
            "mfe_pts": round(mfe, 4), "mae_pts": round(mae, 4),
        }
    return out


# ─── Stage C / E: bracket resolution through PaperBroker ─────────────────────

def _bracket_valid_at_fill(cand: Candidate, fill_entry: float) -> bool:
    if cand.direction == "LONG":
        return cand.stop < fill_entry < cand.target
    return cand.target < fill_entry < cand.stop


def resolve_bracket(lane: Lane, bars: Bars, cand: Candidate, *, fill_model: str,
                    slippage_ticks: float, tolerance_ticks: float) -> dict:
    """Open the candidate's bracket with the real PaperBroker and walk forward.

    Day-only lanes flatten on the exact EOD bar (stop/target first on that
    bar) and fail closed as EOD_BAR_MISSING when that bar is absent, matching
    execution/day_only_exit.py.  Other lanes carry until resolved, capped at
    MAX_HOLD_TRADING_DAYS of bars (then OPEN).
    """
    idx = cand.bar_idx
    row = bars.rows[idx]
    broker = PaperBroker(
        starting_balance=100_000.0, slippage_ticks=slippage_ticks, pessimistic_both_hit=True,
        entry_fill_model=fill_model,
        entry_tolerance_ticks_by_root={"MNQ": tolerance_ticks, "MES": tolerance_ticks},
    )
    opened = broker.execute_bracket(
        BracketOrder(instrument=cand.instrument, direction=cand.direction, entry=cand.entry,
                     stop=cand.stop, target=cand.target, rr_ratio=cand.rr,
                     strategy=cand.strategy, contracts=1),
        market_price=float(row["close"]),
    )
    if opened.result == "CANCELLED":
        return {"status": "NO_FILL", "reason": opened.exit_reason, "exit_idx": idx,
                "decision_close": float(row["close"])}
    # stop_market returns PENDING: the resting order is activated (or cancelled)
    # by PaperBroker on the next bar, so the fill price is only known then.
    fill_entry: Optional[float] = None if opened.result == "PENDING" else float(opened.entry_price)
    if fill_entry is not None and not _bracket_valid_at_fill(cand, fill_entry):
        # Same rule PaperBroker applies to a resting entry: a fill that lands
        # beyond its own stop or target is not a trade, it is a rejected order.
        return {"status": "NO_FILL", "reason": "ENTRY_BRACKET_INVALID_AT_FILL", "exit_idx": idx,
                "decision_close": float(row["close"]), "fill_entry": fill_entry}
    day = bars.et(idx).date()
    limit = len(bars.rows) if lane.day_only else min(len(bars.rows), idx + 1 + bars.bars_per_day * MAX_HOLD_TRADING_DAYS)
    for j in range(idx + 1, limit):
        bar = bars.rows[j]
        if lane.day_only and bars.et(j).date() != day:
            if fill_entry is None:
                return {"status": "NO_FILL", "reason": "ENTRY_NOT_TRIGGERED", "exit_idx": idx}
            broker.force_resolve("BREAKEVEN", fill_entry)
            return {"status": "UNRESOLVED", "reason": EOD_BAR_MISSING, "exit_idx": j,
                    "fill_entry": fill_entry}
        fill = broker.resolve_position(NextBarOHLC(high=float(bar["high"]), low=float(bar["low"]),
                                                   open=float(bar["open"])))
        if fill is not None and fill.result == "CANCELLED":
            return {"status": "NO_FILL", "reason": fill.exit_reason, "exit_idx": idx,
                    "decision_close": float(row["close"])}
        if fill_entry is None:
            position = broker.get_position()
            if position is not None:
                fill_entry = float(position.entry_price)
            elif fill is not None:
                fill_entry = float(fill.entry_price)
        if fill is None and lane.day_only and fill_entry is not None and _is_eod_bar(bar["timestamp"], lane.timeframe_minutes):
            close = float(bar["close"])
            fill = broker.force_resolve(classify_result(cand.direction, fill_entry, close), close)
            fill.exit_reason = DAY_ONLY_EXIT_REASON
        if fill is not None:
            gross = float(fill.pnl_dollars or 0.0)
            return {
                "status": "RESOLVED", "result": fill.result, "exit_reason": fill.exit_reason,
                "fill_entry": float(fill.entry_price), "exit_price": float(fill.exit_price or 0.0),
                "exit_idx": j, "exit_bar_ts": bar["timestamp"],
                "gross": round(gross, 2), "net": round(gross - COMMISSION_ROUND_TRIP, 2),
                "bars_held": j - idx,
            }
    if fill_entry is None:
        return {"status": "NO_FILL", "reason": "ENTRY_NOT_TRIGGERED", "exit_idx": idx}
    if lane.day_only:
        broker.force_resolve("BREAKEVEN", fill_entry)
        return {"status": "UNRESOLVED", "reason": EOD_BAR_MISSING, "exit_idx": limit - 1,
                "fill_entry": fill_entry}
    return {"status": "OPEN", "fill_entry": fill_entry, "exit_idx": limit - 1}


def run_bracket_stage(lane: Lane, bars: Bars, candidates: list[Candidate], *, fill_model: str,
                      slippage_ticks: float, tolerance_ticks: float) -> list[dict]:
    """Sequential, one-position-at-a-time walk (max_open_positions: 1)."""
    rows: list[dict] = []
    busy_until = -1
    for cand in sorted(candidates, key=lambda c: c.bar_idx):
        if cand.bar_idx <= busy_until:
            rows.append({"cand": cand, "status": "SKIPPED_POSITION_OPEN"})
            continue
        res = resolve_bracket(lane, bars, cand, fill_model=fill_model,
                              slippage_ticks=slippage_ticks, tolerance_ticks=tolerance_ticks)
        res["cand"] = cand
        rows.append(res)
        if res["status"] in {"RESOLVED", "OPEN", "UNRESOLVED"}:
            busy_until = res["exit_idx"]
    return rows


# ─── Stage D1: structural risk gates, path-independent ───────────────────────

def structural_gates(lane: Lane, cand: Candidate, state, config, risk: RiskEngine,
                     decision: DecisionEngine) -> dict:
    # The inverted lane's gates evaluate the un-mirrored source signal (paper
    # build contract: confluence and RiskEngine see the source, only the
    # broker order is mirrored), so undo the mirror for this stage.
    if cand.extra.get("source_direction"):
        source = _mirror_prices(cand.direction, cand.entry, cand.stop, cand.target)
        cand = dataclasses.replace(cand, direction=source["direction"], entry=source["entry"],
                                   stop=source["stop"], target=source["target"])
    setup = SetupDetail(direction=cand.direction, entry=cand.entry, stop=cand.stop,
                        target=cand.target, rr_ratio=cand.rr, strategy=cand.strategy)
    grade = None
    if state is not None:
        grade = score_setup(state, setup).grade
    trade = TradeSetup(direction=cand.direction, entry=cand.entry, stop=cand.stop,
                       target=cand.target, rr_ratio=cand.rr, strategy=cand.strategy,
                       instrument=cand.instrument, session=cand.session, contracts=1,
                       confluence_grade=grade, entry_time=_parse_dt(cand.bar_ts))
    daily = DailyState(date=cand.date, account_balance=config.position_sizing.starting_balance)
    failed_risk: list[str] = []
    for name in STRUCTURAL_RISK_CHECKS:
        result = getattr(risk, name)(trade, daily)
        if result is not None:
            failed_risk.append(result.failed_rule)
    failed_signal: list[str] = []
    close = None
    if state is not None:
        close = float(state.ohlc.close)
        mc = str(state.market_condition or "").upper()
        if mc in {str(v).upper() for v in config.non_tradable_states}:
            failed_signal.append("MARKET_CONDITION_NOT_TRADABLE")
        elif config.require_trending_condition and mc != "TRENDING" and cand.strategy not in TRENDING_GATE_EXEMPT:
            failed_signal.append("MARKET_CONDITION_NOT_TRENDING")
        if not decision._entry_bracket_straddles_price(cand.direction, cand.entry, cand.stop,
                                                        cand.target, close):
            failed_signal.append("ENTRY_DETACHED_FROM_PRICE")
    return {"confluence_grade": grade, "risk_failed": failed_risk, "signal_failed": failed_signal,
            "market_condition": (str(state.market_condition) if state is not None else None),
            "decision_close": close}


# ─── Stage D2/D3: isolated full-engine runs ──────────────────────────────────

def engine_config(base, lane: Lane, *, floors_off: bool):
    kwargs: dict[str, Any] = dict(
        enabled_concepts=[lane.strategy],
        disabled_concepts_per_instrument={},
        allowed_instruments=["MNQ", "MES"],
        expected_timeframe_minutes=lane.timeframe_minutes,
        entry_fill_model="ioc_limit",
        fill_slippage_ticks=1.0,
        strategy_permission_gate_enabled=False,
        max_trades_per_day=9999,
    )
    if floors_off:
        sizing = base.position_sizing
        kwargs.update(
            max_drawdown_percent=0.0,
            max_daily_loss=1e9,
            position_sizing=dataclasses.replace(
                sizing, starting_balance=1_000_000.0, aggressive_rounding=False,
                sizing_rules=[PositionSizingRule(0.0, None, inst, 1) for inst in ("MNQ", "MES")],
            ),
        )
    return dataclasses.replace(base, **kwargs)


ENGINE_TAGS = {"floors_off": True, "frozen": False}


def engine_log_dir(logs_root: Path, lane: Lane, tag: str) -> Path:
    return logs_root / lane.key / tag


def run_engine_job(lane_key: str, tag: str, data_root: str, logs_root: str) -> dict:
    lane = LANES[lane_key]
    config = engine_config(load_config(), lane, floors_off=ENGINE_TAGS[tag])
    corpus_dir = Path(data_root) / lane.corpus / lane.instrument
    files = sorted(corpus_dir.glob(f"{lane.instrument}_*.jsonl"))
    log_dir = engine_log_dir(Path(logs_root), lane, tag)
    log_dir.mkdir(parents=True, exist_ok=True)
    done_marker = log_dir / "_complete.json"
    if done_marker.exists():
        return json.loads(done_marker.read_text())
    engine = ReplayEngine(config=config, log_dir=str(log_dir))
    ran = 0
    for index, path in enumerate(files, 1):
        day = path.stem.rsplit("_", 1)[-1]
        engine.run(path, review_date=day)
        ran += 1
        if index % 100 == 0 or index == len(files):
            print(f"[engine] {lane_key}/{tag} {index}/{len(files)}", flush=True)
    summary = {"lane": lane_key, "tag": tag, "files": len(files), "ran": ran,
               "config": {
                   "enabled_concepts": config.enabled_concepts,
                   "expected_timeframe_minutes": config.expected_timeframe_minutes,
                   "entry_fill_model": config.entry_fill_model,
                   "entry_tolerance_ticks_by_root": config.entry_tolerance_ticks_by_root,
                   "fill_slippage_ticks": config.fill_slippage_ticks,
                   "strategy_permission_gate_enabled": config.strategy_permission_gate_enabled,
                   "max_trades_per_day": config.max_trades_per_day,
                   "max_drawdown_percent": config.max_drawdown_percent,
                   "max_daily_loss": config.max_daily_loss,
                   "starting_balance": config.position_sizing.starting_balance,
                   "require_trending_condition": config.require_trending_condition,
                   "max_stop_ticks": dict(config.max_stop_ticks),
                   "min_rr_ratio": config.min_rr_ratio,
                   "min_confluence_grade": config.min_confluence_grade,
               }}
    done_marker.write_text(json.dumps(summary, indent=2))
    return summary


def _json_lines(path: Path) -> Iterable[dict]:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            yield json.loads(line)


def classify_engine(lane: Lane, candidates: list[Candidate], log_dir: Path) -> tuple[list[dict], dict]:
    """Anchor every raw candidate on its own journal bar and classify the
    engine's disposition there (the #372/#367 method)."""
    by_bar: dict[str, dict] = {}
    outcomes: dict[str, dict] = {}
    engine_attempts: list[dict] = []
    first_dd_halt: Optional[dict] = None
    for path in sorted(log_dir.glob("journal_*.jsonl")):
        day = path.stem.removeprefix("journal_")
        for entry in _json_lines(path):
            if entry.get("type") == "OUTCOME":
                oid = (entry.get("outcome") or {}).get("paper_order_id")
                if oid:
                    outcomes[oid] = entry["outcome"]
                continue
            bar_ts = entry.get("bar_ts")
            if bar_ts and "decision" in entry:
                by_bar[bar_ts] = entry
            if entry.get("decision") == "RISK_REJECTED" and first_dd_halt is None:
                if (entry.get("risk_check") or {}).get("failed_rule") == "max_drawdown":
                    first_dd_halt = {"date": day, "bar_ts": bar_ts}
            if entry.get("decision") == "TRADE" and (entry.get("risk_check") or {}).get("result") == "APPROVED":
                engine_attempts.append({"date": day, "bar_ts": bar_ts,
                                        "paper_order_id": entry.get("paper_order_id"),
                                        "setup": entry.get("setup") or {}})
    rows: list[dict] = []
    for cand in candidates:
        entry = by_bar.get(cand.bar_ts)
        row: dict[str, Any] = {"cand": cand}
        if entry is None:
            row.update(stage="skipped", disposition="NO_ENGINE_ROW_AT_BAR")
            rows.append(row)
            continue
        decision = entry.get("decision")
        setup = entry.get("setup") or {}
        risk = entry.get("risk_check") or {}
        if decision == "WAIT":
            row.update(stage="path", disposition="WAIT_POSITION_OPEN")
        elif decision == "RISK_REJECTED" and setup.get("strategy") == lane.strategy:
            rule = risk.get("failed_rule") or "UNKNOWN"
            row.update(stage="path" if rule in PATH_DEPENDENT_RULES else "risk",
                       disposition=f"RISK:{rule}", reason=risk.get("reason"),
                       engine_stop=setup.get("stop"), engine_target=setup.get("target"),
                       confluence_grade=(entry.get("confluence") or {}).get("grade"))
        elif decision == "TRADE" and risk.get("result") == "APPROVED":
            outcome = outcomes.get(entry.get("paper_order_id")) or {}
            result = outcome.get("result")
            if result == "CANCELLED":
                row.update(stage="fill", disposition="IOC_NOT_FILLED", reason=outcome.get("exit_reason"))
            elif result in {"WIN", "LOSS", "BREAKEVEN"}:
                gross = float(outcome.get("pnl_dollars") or 0.0)
                row.update(stage="filled", disposition=f"FILLED:{result}", result=result,
                           gross=round(gross, 2), net=round(gross - COMMISSION_ROUND_TRIP, 2),
                           exit_reason=outcome.get("exit_reason"),
                           engine_stop=setup.get("stop"), engine_target=setup.get("target"))
            else:
                row.update(stage="filled", disposition="FILLED:OPEN")
        else:
            gates = list(entry.get("failed_gates") or [])
            audit = [r for r in (entry.get("candidate_audit") or []) if r.get("strategy") == lane.strategy]
            reject = next((r.get("reject_code") for r in audit if r.get("reject_code")), None)
            blocked = (entry.get("blocked_candidate_audit") or {}).get("candidates") or []
            if gates:
                disposition = f"SIGNAL:{gates[0]}"
            elif reject:
                disposition = f"SIGNAL:{reject}"
            elif blocked:
                disposition = "SIGNAL:MARKET_CONDITION_NOT_TRADABLE"
            else:
                disposition = "SIGNAL:NO_CANDIDATE_IN_ENGINE"
            row.update(stage="signal", disposition=disposition, reason=entry.get("reason"),
                       gates=gates)
        rows.append(row)
    engine_totals = {
        "order_attempts": len(engine_attempts),
        "ioc_filled": sum(1 for a in engine_attempts
                          if (outcomes.get(a["paper_order_id"]) or {}).get("result") not in {None, "CANCELLED"}),
        "resolved": sum(1 for a in engine_attempts
                        if (outcomes.get(a["paper_order_id"]) or {}).get("result") in {"WIN", "LOSS", "BREAKEVEN"}),
        "net_after_commission": round(sum(
            float((outcomes.get(a["paper_order_id"]) or {}).get("pnl_dollars") or 0.0) - COMMISSION_ROUND_TRIP
            for a in engine_attempts
            if (outcomes.get(a["paper_order_id"]) or {}).get("result") in {"WIN", "LOSS", "BREAKEVEN"}
        ), 2),
        "first_drawdown_halt": first_dd_halt,
    }
    return rows, engine_totals


# ─── Statistics ──────────────────────────────────────────────────────────────

def _pf(values: list[float]) -> Optional[float]:
    wins = sum(v for v in values if v > 0)
    losses = abs(sum(v for v in values if v < 0))
    if losses:
        return round(wins / losses, 3)
    return math.inf if wins else None


def _max_dd(values: list[float]) -> float:
    equity = peak = dd = 0.0
    for v in values:
        equity += v
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
    return round(dd, 2)


def _concentration(values: list[float], top_n: int = 5) -> Optional[float]:
    winners = sorted((v for v in values if v > 0), reverse=True)
    total = sum(winners)
    return round(sum(winners[:top_n]) / total, 3) if total else None


def trade_stats(nets: list[float], dates: list[str], boundary: Optional[str]) -> dict:
    resolved = len(nets)
    wins = sum(1 for v in nets if v > 0)
    h1 = [v for v, d in zip(nets, dates) if boundary is None or d < boundary]
    h2 = [v for v, d in zip(nets, dates) if boundary is not None and d >= boundary]
    return {
        "resolved": resolved,
        "wins": wins,
        "win_rate": round(wins / resolved, 3) if resolved else None,
        "net": round(sum(nets), 2),
        "expectancy": round(sum(nets) / resolved, 2) if resolved else None,
        "profit_factor": _pf(nets),
        "max_drawdown": _max_dd(nets),
        "top5_concentration": _concentration(nets),
        "h1_net": round(sum(h1), 2), "h1_n": len(h1),
        "h2_net": round(sum(h2), 2), "h2_n": len(h2),
        "both_halves_positive": (sum(h1) > 0 and sum(h2) > 0) if (h1 and h2) else None,
    }


def control_stats(values: list[float]) -> dict:
    n = len(values)
    if not n:
        return {"n": 0}
    mean = statistics.fmean(values)
    sd = statistics.pstdev(values) if n > 1 else 0.0
    return {
        "n": n, "mean": round(mean, 4), "median": round(statistics.median(values), 4),
        "hit_rate": round(sum(1 for v in values if v > 0) / n, 3),
        "t_stat": round(mean / (sd / math.sqrt(n)), 3) if sd > 0 else None,
        "sum": round(sum(values), 2), "profit_factor": _pf(values),
    }


def bracket_summary(rows: list[dict], boundary: Optional[str]) -> dict:
    resolved = [r for r in rows if r["status"] == "RESOLVED"]
    nets = [r["net"] for r in resolved]
    dates = [r["cand"].date for r in resolved]
    attempted = [r for r in rows if r["status"] != "SKIPPED_POSITION_OPEN"]
    filled = [r for r in attempted if r["status"] in {"RESOLVED", "OPEN", "UNRESOLVED"}]
    out = {
        "candidates": len(rows),
        "skipped_position_open": sum(1 for r in rows if r["status"] == "SKIPPED_POSITION_OPEN"),
        "attempted": len(attempted),
        "filled": len(filled),
        "fill_rate": round(len(filled) / len(attempted), 3) if attempted else None,
        "no_fill": sum(1 for r in attempted if r["status"] == "NO_FILL"),
        "open": sum(1 for r in rows if r["status"] == "OPEN"),
        "eod_bar_missing": sum(1 for r in rows if r["status"] == "UNRESOLVED"),
        "exit_reasons": dict(Counter(r.get("exit_reason") for r in resolved)),
        "no_fill_reasons": dict(Counter(r.get("reason") for r in attempted if r["status"] == "NO_FILL")),
    }
    out.update(trade_stats(nets, dates, boundary))
    return out


# ─── Lane orchestration ──────────────────────────────────────────────────────

def _boundary(candidates: list[Candidate]) -> Optional[str]:
    if len(candidates) < 2:
        return None
    ordered = sorted(c.date for c in candidates)
    return ordered[len(ordered) // 2]


def analyze_lane(lane: Lane, data_root: Path, transition_dir: Path, logs_root: Path,
                 base_config, *, skip_engine: bool) -> dict:
    bars = load_bars(data_root / lane.corpus, lane.instrument)
    builder = StateBuilder(base_config, bars)
    audit: dict[str, Any] = {}
    if lane.source in {"state_machine_4hr", "state_machine_322"}:
        candidates = extract_state_machine(lane, bars)
    elif lane.source == "research_miyagi":
        candidates, audit = extract_miyagi(lane, bars)
    elif lane.source == "predicate":
        candidates = extract_predicate(lane, bars, builder)
    elif lane.source == "saved_json":
        candidates, audit = extract_saved_json(lane, bars, transition_dir)
    else:
        raise ValueError(lane.source)
    candidates.sort(key=lambda c: c.bar_idx)
    for cand in candidates:
        if cand.state is None:
            cand.state = builder.state_at(cand.bar_idx)
    boundary = _boundary(candidates)
    result: dict[str, Any] = {
        "lane": lane.key, "label": lane.label, "strategy": lane.strategy,
        "instrument": lane.instrument, "corpus": lane.corpus, "timeframe": lane.timeframe,
        "day_only": lane.day_only, "source": lane.source, "extraction_audit": audit,
        "corpus_range": [bars.et(0).date().isoformat(), bars.et(len(bars.rows) - 1).date().isoformat()],
        "walk_forward_boundary": boundary,
    }

    # A. raw signal
    stop_ticks = sorted(c.stop_ticks() for c in candidates)
    cap = float(base_config.max_stop_ticks.get(lane.instrument, 0) or 0)
    result["A_raw"] = {
        "candidates": len(candidates),
        "campaigns": len({c.campaign for c in candidates}) if lane.campaign or lane.source != "predicate" else None,
        "by_direction": dict(Counter(c.direction for c in candidates)),
        "by_session": dict(Counter(c.session for c in candidates)),
        "first_date": candidates[0].date if candidates else None,
        "last_date": candidates[-1].date if candidates else None,
        "stop_ticks": {
            "median": stop_ticks[len(stop_ticks) // 2] if stop_ticks else None,
            "p90": stop_ticks[int(len(stop_ticks) * 0.9)] if stop_ticks else None,
            "max": stop_ticks[-1] if stop_ticks else None,
            "max_stop_ticks_cap": cap,
            "share_over_cap": round(sum(1 for t in stop_ticks if t > cap) / len(stop_ticks), 3) if stop_ticks and cap else None,
        },
        "rr_median": round(sorted(c.rr for c in candidates)[len(candidates) // 2], 3) if candidates else None,
    }

    # B. time-exit control
    controls = [time_exit_control(lane, bars, c) for c in candidates]
    control_out: dict[str, Any] = {"entry": "next-bar open + 1 tick adverse, exit at horizon close - 1 tick, no stop"}
    for label in [f"{h}m" for h in HORIZONS_MIN] + ["EOD"]:
        pts = [c["horizons"][label]["pts"] for c in controls if c.get("horizons", {}).get(label)]
        net = [c["horizons"][label]["net"] for c in controls if c.get("horizons", {}).get(label)]
        mfe = [c["horizons"][label]["mfe_pts"] for c in controls if c.get("horizons", {}).get(label)]
        mae = [c["horizons"][label]["mae_pts"] for c in controls if c.get("horizons", {}).get(label)]
        control_out[label] = {
            "pts": control_stats(pts), "net_usd": control_stats(net),
            "mfe_pts_mean": round(statistics.fmean(mfe), 3) if mfe else None,
            "mae_pts_mean": round(statistics.fmean(mae), 3) if mae else None,
        }
    result["B_time_exit_control"] = control_out

    # C. documented bracket: legacy plan-price fill vs a resting order at the
    # documented level (PaperBroker stop_market, next bar, gap-through at open).
    plan_rows = run_bracket_stage(lane, bars, candidates, fill_model="market",
                                  slippage_ticks=1.0, tolerance_ticks=lane.tolerance)
    resting_rows = run_bracket_stage(lane, bars, candidates, fill_model="stop_market",
                                     slippage_ticks=1.0, tolerance_ticks=lane.tolerance)
    primary = "plan_fill" if lane.entry_style == "armed_trigger" else "resting_fill"
    plan_summary = bracket_summary(plan_rows, boundary)
    resting_summary = bracket_summary(resting_rows, boundary)
    result["C_documented_bracket"] = {
        "primary": primary,
        "entry_style": lane.entry_style,
        "plan_fill": plan_summary,
        "resting_fill": resting_summary,
        "fill_assumption_delta_net": round(plan_summary["net"] - resting_summary["net"], 2),
    }
    bracket_rows = plan_rows if primary == "plan_fill" else resting_rows
    bracket_by_id = {id(r["cand"]): r for r in bracket_rows}

    # D1. structural gates (path-independent)
    risk = RiskEngine(config=base_config)
    gate_rows = [structural_gates(lane, c, c.state, base_config, risk, builder.decision) for c in candidates]
    risk_hist: Counter = Counter()
    signal_hist: Counter = Counter()
    for g in gate_rows:
        risk_hist.update(g["risk_failed"])
        signal_hist.update(g["signal_failed"])
    risk_ok = [c for c, g in zip(candidates, gate_rows) if not g["risk_failed"]]
    all_ok = [c for c, g in zip(candidates, gate_rows) if not g["risk_failed"] and not g["signal_failed"]]

    def _subset_bracket(subset: list[Candidate]) -> dict:
        rows = [bracket_by_id[id(c)] for c in subset if id(c) in bracket_by_id]
        return bracket_summary(rows, boundary)

    result["D1_structural_gates"] = {
        "risk_rule_failures": dict(risk_hist.most_common()),
        "signal_gate_failures": dict(signal_hist.most_common()),
        "confluence_grades": dict(Counter(g["confluence_grade"] for g in gate_rows)),
        "market_conditions": dict(Counter(g["market_condition"] for g in gate_rows)),
        "survivors_risk_structural": len(risk_ok),
        "survivors_risk_plus_signal": len(all_ok),
        "survivor_share_risk_structural": round(len(risk_ok) / len(candidates), 3) if candidates else None,
        "survivor_share_risk_plus_signal": round(len(all_ok) / len(candidates), 3) if candidates else None,
        "bracket_pnl_of_risk_structural_survivors": _subset_bracket(risk_ok),
        "bracket_pnl_of_all_survivors": _subset_bracket(all_ok),
        "bracket_pnl_of_structurally_rejected": _subset_bracket(
            [c for c, g in zip(candidates, gate_rows) if g["risk_failed"]]),
    }

    # D2/D3. full engine (engine-native lanes; the inverse lane borrows its
    # source lane's funnel because gates evaluate the un-mirrored signal).
    funnel_lane = LANES[lane.funnel_from] if lane.funnel_from else lane
    result["D_engine"] = {}
    if funnel_lane.engine and not skip_engine:
        for tag in ENGINE_TAGS:
            log_dir = engine_log_dir(logs_root, funnel_lane, tag)
            if not (log_dir / "_complete.json").exists():
                result["D_engine"][tag] = {"status": "NOT_RUN"}
                continue
            anchor = candidates
            if lane.mirror:
                anchor = [dataclasses.replace(c, direction=c.extra.get("source_direction", c.direction)) for c in candidates]
            rows, totals = classify_engine(funnel_lane, anchor, log_dir)
            disp = Counter(r["disposition"] for r in rows)
            stage = Counter(r["stage"] for r in rows)
            filled = [r for r in rows if r["stage"] == "filled" and "net" in r]
            nets = [r["net"] for r in filled]
            if lane.mirror:
                nets = [-(r["gross"]) - COMMISSION_ROUND_TRIP for r in filled]
            result["D_engine"][tag] = {
                "status": "OK",
                "config": json.loads((log_dir / "_complete.json").read_text())["config"],
                "dispositions": dict(disp.most_common()),
                "stages": dict(stage),
                "approved_attempts": sum(1 for r in rows if r["stage"] in {"fill", "filled"}),
                "ioc_filled": len([r for r in rows if r["stage"] == "filled"]),
                "filled_pnl": trade_stats(nets, [r["cand"].date for r in filled], boundary),
                "engine_totals_unanchored": totals,
                "note": ("P&L sign-flipped from the engine's un-mirrored fills (mirror lane); "
                         "entry tolerance differs (8 ticks) so treat as approximate") if lane.mirror else None,
            }
    elif not funnel_lane.engine:
        result["D_engine"]["note"] = "no enabled_concepts entry on main; structural stage D1 is the only gate evidence"

    # E. IOC / costs
    ioc_out: dict[str, Any] = {"tolerance_ticks": lane.tolerance, "reference_price": "decision-bar close"}
    for slip in SLIPPAGE_SWEEP:
        rows_all = run_bracket_stage(lane, bars, candidates, fill_model="ioc_limit",
                                     slippage_ticks=slip, tolerance_ticks=lane.tolerance)
        rows_ok = run_bracket_stage(lane, bars, risk_ok, fill_model="ioc_limit",
                                    slippage_ticks=slip, tolerance_ticks=lane.tolerance)
        ioc_out[f"{slip:.0f}tick"] = {
            "all_candidates": bracket_summary(rows_all, boundary),
            "risk_structural_survivors": bracket_summary(rows_ok, boundary),
        }
    result["E_ioc_costs"] = ioc_out

    result["verdict"] = classify_lane(result)
    result["candidates"] = [
        {**c.to_dict(),
         "control": controls[i].get("horizons"),
         "bracket": {k: v for k, v in bracket_by_id[id(c)].items() if k != "cand"},
         "gates": gate_rows[i]}
        for i, c in enumerate(candidates)
    ]
    return result


def classify_lane(result: dict) -> dict:
    control = result["B_time_exit_control"]
    horizon_means = [
        control[label]["net_usd"].get("mean")
        for label in [f"{h}m" for h in HORIZONS_MIN] + ["EOD"]
        if control[label]["net_usd"].get("n")
    ]
    positive_horizons = sum(1 for m in horizon_means if m is not None and m > 0)
    signal_positive = bool(horizon_means) and positive_horizons / len(horizon_means) > 0.5
    best_t = max((control[label]["pts"].get("t_stat") or 0.0)
                 for label in [f"{h}m" for h in HORIZONS_MIN] + ["EOD"]
                 if control[label]["pts"].get("n"))
    bracket = result["C_documented_bracket"][result["C_documented_bracket"]["primary"]]
    bracket_positive = bool(bracket["net"] > 0 and (bracket["profit_factor"] or 0) > 1)
    d1 = result["D1_structural_gates"]
    survivors_share = d1["survivor_share_risk_structural"] or 0.0
    survivors_pnl = d1["bracket_pnl_of_risk_structural_survivors"]
    survivors_positive = bool(survivors_pnl["net"] > 0 and (survivors_pnl["profit_factor"] or 0) > 1)
    ioc = result["E_ioc_costs"]["1tick"]["risk_structural_survivors"]
    ioc3 = result["E_ioc_costs"]["3tick"]["risk_structural_survivors"]
    ioc_positive = bool(ioc["net"] > 0 and (ioc["profit_factor"] or 0) > 1)
    ioc3_positive = bool(ioc3["net"] > 0 and (ioc3["profit_factor"] or 0) > 1)
    fill_rate = ioc["fill_rate"] or 0.0
    flags = {
        "signal_positive_majority_of_horizons": signal_positive,
        "signal_positive_horizons": f"{positive_horizons}/{len(horizon_means)}",
        "signal_best_t_stat": round(best_t, 2),
        "documented_bracket_positive": bracket_positive,
        "structural_survivor_share": round(survivors_share, 3),
        "survivors_bracket_positive": survivors_positive,
        "ioc_1tick_positive": ioc_positive,
        "ioc_3tick_positive": ioc3_positive,
        "ioc_fill_rate": round(fill_rate, 3),
    }
    if not signal_positive:
        primary = "SIGNAL_NOT_DIRECTIONAL"
    elif not bracket_positive:
        primary = "BRACKET_DESTROYS_EDGE"
    elif survivors_share < 0.5 or not survivors_positive:
        primary = "RISK_GATES_REMOVE_EDGE"
    elif not ioc_positive or fill_rate < 0.5:
        primary = "FILL_MODEL_REMOVES_EDGE"
    elif not ioc3_positive:
        primary = "SURVIVES_BUT_COST_FRAGILE"
    else:
        primary = "SURVIVES_PIPELINE"
    return {"primary_failure_stage": primary, "flags": flags}


# ─── Report ──────────────────────────────────────────────────────────────────

def _is_inf(v: Any) -> bool:
    return v == "inf" or (isinstance(v, float) and math.isinf(v))


def _m(v: Any) -> str:
    if v is None:
        return "—"
    if _is_inf(v):
        return "∞"
    return f"${v:,.2f}"


def _pfs(v: Any) -> str:
    if v is None:
        return "—"
    return "∞" if _is_inf(v) else f"{v:.2f}"


def _pct(v: Any) -> str:
    return "—" if v is None else f"{100 * v:.0f}%"


def _top(d: dict, n: int = 4) -> str:
    items = list(d.items())[:n]
    return ", ".join(f"{k} {v}" for k, v in items) if items else "—"


def render_report(results: dict, findings: Optional[str] = None) -> str:
    lanes = results["lanes"]
    lines = [
        "# Edge decomposition audit — where does each strategy's edge disappear?",
        "",
        f"_Generated {results['meta']['generated']} at `{results['meta']['sha'][:9]}`. Evidence only; "
        "no strategy, risk, replay, broker, config, or deployment change._",
        "",
        "Every lane is pushed through one standardized waterfall. Each stage answers one question:",
        "",
        "| Stage | Question | Mechanism |",
        "|---|---|---|",
        "| A raw signal | how often does the strategy's own detector fire, with no gates? | canonical state machine / `_try_*` predicate / research detector / saved shadow population |",
        "| B time-exit control | does the signal carry direction at all? | next-bar-open entry, exit at 30/60/120 min and EOD close, no stop, 1-tick adverse each side |",
        "| C documented bracket | does the strategy's own stop/target keep that direction? | real `PaperBroker`, legacy market fill at the plan price, pessimistic same-bar, day-only flatten where documented |",
        "| D1 structural gates | which candidates does the risk architecture admit, path-independently? | the real `RiskEngine` structural checks (session, bracket, R:R, confluence, min target, max stop) on every raw candidate |",
        "| D2 / D3 full engine | what does the executable system actually do, floors off / frozen? | isolated `ReplayEngine` (config-only isolation, permission gate off, 2026-07-27 evidence posture), each raw candidate anchored on its own journal bar |",
        "| E IOC / costs | what survives production-matching fills? | real `PaperBroker` `ioc_limit` at the decision-bar close, 1/2/3-tick adverse |",
        "",
        f"Costs: ${COMMISSION_ROUND_TRIP:.2f} round-turn commission at the analysis layer plus PaperBroker's own "
        "adverse slippage on entry and stop exit. One contract. Walk-forward halves split at each lane's median candidate date.",
        "",
        "## Summary — primary stage where the edge disappears",
        "",
        "| Lane | Raw n | B: 60m mean $ (t) | B: EOD mean $ (t) | C (primary fill): net / PF | C: plan − resting Δ | D1: survivors | D1 survivors: net / PF | D2 floors-off: approved → filled, net | E: IOC 1-tick fill / net / PF | Primary failure stage |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for lane in lanes:
        b60 = lane["B_time_exit_control"]["60m"]["net_usd"]
        beod = lane["B_time_exit_control"]["EOD"]["net_usd"]
        cblock = lane["C_documented_bracket"]
        c = cblock[cblock["primary"]]
        d1 = lane["D1_structural_gates"]
        sp = d1["bracket_pnl_of_risk_structural_survivors"]
        d2 = lane["D_engine"].get("floors_off") or {}
        e1 = lane["E_ioc_costs"]["1tick"]["risk_structural_survivors"]
        d2_cell = "—"
        if d2.get("status") == "OK":
            d2_cell = f"{d2['approved_attempts']} → {d2['ioc_filled']}, {_m(d2['filled_pnl']['net'])}"
        elif lane["D_engine"].get("note"):
            d2_cell = "n/a (no engine concept)"
        lines.append(
            f"| {lane['label']} | {lane['A_raw']['candidates']} | "
            f"{_m(b60.get('mean'))} ({b60.get('t_stat') if b60.get('t_stat') is not None else '—'}) | "
            f"{_m(beod.get('mean'))} ({beod.get('t_stat') if beod.get('t_stat') is not None else '—'}) | "
            f"{_m(c['net'])} / {_pfs(c['profit_factor'])} | {_m(cblock['fill_assumption_delta_net'])} | "
            f"{d1['survivors_risk_structural']} ({_pct(d1['survivor_share_risk_structural'])}) | "
            f"{_m(sp['net'])} / {_pfs(sp['profit_factor'])} | {d2_cell} | "
            f"{_pct(e1['fill_rate'])} / {_m(e1['net'])} / {_pfs(e1['profit_factor'])} | "
            f"**{lane['verdict']['primary_failure_stage']}** |"
        )
    lines += ["", "Primary-stage rule (mechanical, first failing stage wins): mean net $ not positive at a majority of the "
              "available control horizons → `SIGNAL_NOT_DIRECTIONAL`; documented bracket (primary fill model) net ≤ 0 or PF ≤ 1 → `BRACKET_DESTROYS_EDGE`; structural "
              "survivors < 50% of raw or survivors' bracket P&L not positive → `RISK_GATES_REMOVE_EDGE`; IOC 1-tick on "
              "survivors not positive or fill rate < 50% → `FILL_MODEL_REMOVES_EDGE`; IOC 3-tick not positive → "
              "`SURVIVES_BUT_COST_FRAGILE`; else `SURVIVES_PIPELINE`.", ""]
    if findings:
        lines += [findings.strip(), ""]

    for lane in lanes:
        a = lane["A_raw"]
        lines += [f"## {lane['label']}", "",
                  f"`{lane['strategy']}` · {lane['instrument']} · corpus `{lane['corpus']}` ({lane['corpus_range'][0]} → {lane['corpus_range'][1]}, {lane['timeframe']}) · "
                  f"source: {lane['source']} · day-only: {lane['day_only']} · walk-forward boundary {lane['walk_forward_boundary']}", ""]
        if lane["extraction_audit"]:
            lines += [f"Extraction audit: `{json.dumps(lane['extraction_audit'], sort_keys=True, default=str)}`", ""]
        st = a["stop_ticks"]
        lines += [
            "### A. Raw signal", "",
            f"- candidates: **{a['candidates']}**" + (f" ({a['campaigns']} campaigns)" if a.get("campaigns") else "") +
            f"; direction {a['by_direction']}; sessions {a['by_session']}; {a['first_date']} → {a['last_date']}",
            f"- documented stop width (ticks): median {st['median']}, p90 {st['p90']}, max {st['max']}; policy cap {st['max_stop_ticks_cap']:.0f} → "
            f"**{_pct(st['share_over_cap'])} of raw candidates exceed the cap**; median R:R {a['rr_median']}",
            "", "### B. Time-exit directional control (no stop)", "",
            "| Horizon | n | mean pts | median pts | hit rate | t-stat | mean net $ | PF (net $) | mean MFE / MAE pts |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
        for label in [f"{h}m" for h in HORIZONS_MIN] + ["EOD"]:
            h = lane["B_time_exit_control"][label]
            p, n = h["pts"], h["net_usd"]
            if not p.get("n"):
                lines.append(f"| {label} | 0 | — | — | — | — | — | — | — |")
                continue
            lines.append(f"| {label} | {p['n']} | {p['mean']:.2f} | {p['median']:.2f} | {_pct(p['hit_rate'])} | "
                         f"{p['t_stat'] if p['t_stat'] is not None else '—'} | {_m(n['mean'])} | {_pfs(n['profit_factor'])} | "
                         f"{h['mfe_pts_mean']} / {h['mae_pts_mean']} |")
        cblock = lane["C_documented_bracket"]
        lines += ["", "### C. Documented bracket (real PaperBroker, two fill models)", "",
                  "| Fill model | attempted | filled | fill rate | resolved | WR | net | expectancy | PF | max DD | top-5 conc. | H1 / H2 | both halves | exits / no-fill |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|"]
        for key, title in (("plan_fill", "plan-price fill (legacy, fills assumed)"),
                           ("resting_fill", "resting stop order at the level, next bar")):
            c = cblock[key]
            mark = " **(primary)**" if key == cblock["primary"] else ""
            lines.append(f"| {title}{mark} | {c['attempted']} | {c['filled']} | {_pct(c['fill_rate'])} | {c['resolved']} | {_pct(c['win_rate'])} | "
                         f"{_m(c['net'])} | {_m(c['expectancy'])} | {_pfs(c['profit_factor'])} | {_m(c['max_drawdown'])} | {_pct(c['top5_concentration'])} | "
                         f"{_m(c['h1_net'])} / {_m(c['h2_net'])} | {c['both_halves_positive']} | {c['exit_reasons']} {c['no_fill_reasons'] or ''} |")
        lines += ["", f"- entry style `{cblock['entry_style']}` → primary fill model `{cblock['primary']}`; "
                  f"fill-assumption inflation (plan − resting net): **{_m(cblock['fill_assumption_delta_net'])}**; "
                  f"skipped while a position was open: plan {cblock['plan_fill']['skipped_position_open']} / resting {cblock['resting_fill']['skipped_position_open']}; "
                  f"EOD-bar-missing: {cblock[cblock['primary']]['eod_bar_missing']}",
                  ""]
        d1 = lane["D1_structural_gates"]
        sp, ap, rj = d1["bracket_pnl_of_risk_structural_survivors"], d1["bracket_pnl_of_all_survivors"], d1["bracket_pnl_of_structurally_rejected"]
        lines += ["### D1. Structural risk gates (path-independent, real RiskEngine checks)", "",
                  f"- risk-rule failures across raw candidates: {_top(d1['risk_rule_failures'], 8)}",
                  f"- signal-layer structural failures: {_top(d1['signal_gate_failures'], 6)}",
                  f"- confluence grades: {d1['confluence_grades']}; market conditions at signal: {d1['market_conditions']}",
                  f"- survivors of risk-structural checks: **{d1['survivors_risk_structural']} / {a['candidates']} ({_pct(d1['survivor_share_risk_structural'])})**; "
                  f"survivors of risk + market-condition + entry-attached: {d1['survivors_risk_plus_signal']} ({_pct(d1['survivor_share_risk_plus_signal'])})",
                  f"- bracket P&L (stage-C fills) of risk-structural survivors: n={sp['resolved']}, net **{_m(sp['net'])}**, PF {_pfs(sp['profit_factor'])}, both halves {sp['both_halves_positive']}",
                  f"- bracket P&L of all-gate survivors: n={ap['resolved']}, net {_m(ap['net'])}, PF {_pfs(ap['profit_factor'])}",
                  f"- bracket P&L of the structurally REJECTED candidates: n={rj['resolved']}, net {_m(rj['net'])}, PF {_pfs(rj['profit_factor'])} — what the gates threw away",
                  ""]
        eng = lane["D_engine"]
        if eng.get("note"):
            lines += [f"### D2/D3. Full engine — {eng['note']}", ""]
        for tag, title in (("floors_off", "D2. Full engine, survival floors off"), ("frozen", "D3. Full engine, frozen production risk floors")):
            e = eng.get(tag)
            if not e:
                continue
            if e.get("status") != "OK":
                lines += [f"### {title} — not run", ""]
                continue
            fp, tot = e["filled_pnl"], e["engine_totals_unanchored"]
            lines += [f"### {title}", "",
                      f"- per-candidate dispositions at the candidate's own bar: {_top(e['dispositions'], 10)}",
                      f"- stages: {e['stages']}; approved attempts {e['approved_attempts']} → IOC filled {e['ioc_filled']}",
                      f"- filled P&L: n={fp['resolved']}, WR {_pct(fp['win_rate'])}, net **{_m(fp['net'])}**, PF {_pfs(fp['profit_factor'])}, H1 {_m(fp['h1_net'])} / H2 {_m(fp['h2_net'])}",
                      f"- engine totals regardless of anchoring: {tot['order_attempts']} attempts, {tot['ioc_filled']} filled, {tot['resolved']} resolved, net {_m(tot['net_after_commission'])}"
                      + (f"; first drawdown halt {tot['first_drawdown_halt']}" if tot.get("first_drawdown_halt") else ""),
                      ] + ([f"- {e['note']}"] if e.get("note") else []) + [""]
        ioc = lane["E_ioc_costs"]
        lines += [f"### E. IOC fills and cost stress (tolerance {ioc['tolerance_ticks']:.0f} ticks at the decision-bar close)", "",
                  "| Slippage | Population | attempted | filled | fill rate | resolved | WR | net | PF | both halves |",
                  "|---|---|---:|---:|---:|---:|---:|---:|---:|---|"]
        for slip in SLIPPAGE_SWEEP:
            for pop, label in (("all_candidates", "all raw"), ("risk_structural_survivors", "D1 survivors")):
                s = ioc[f"{slip:.0f}tick"][pop]
                lines.append(f"| {slip:.0f} tick | {label} | {s['attempted']} | {s['filled']} | {_pct(s['fill_rate'])} | {s['resolved']} | "
                             f"{_pct(s['win_rate'])} | {_m(s['net'])} | {_pfs(s['profit_factor'])} | {s['both_halves_positive']} |")
        v = lane["verdict"]
        lines += ["", f"**Primary failure stage: `{v['primary_failure_stage']}`** — flags `{json.dumps(v['flags'])}`", ""]

    lines += ["## Method notes and limitations", "",
              "- Stage A for `orb_reclaim` / `orb_breakout` / `vwap_hold` evaluates the strategy's own `_try_*` predicate on every bar through the engine's own `MarketState` builder; "
              "counts are per bar (campaign counts shown where the strategy has a natural campaign key). Sequential stages (C, E) enforce one position at a time, so overlapping bars are skipped, not double-counted.",
              "- Stage A for the 4HR and 3-2-2 lanes walks the canonical pure state machines (`strategy/four_hr_retrigger.py`, `strategy/strat_322_first_live.py`) exactly as the engine calls them.",
              "- Miyagi has no `enabled_concepts` entry on `main` (PR #362 was closed unmerged); its raw population is the research detector's own output with the documented causal stop recomputed via `_completed_one_hour_stop`, the same helper the #366 closure used. Its D1 stage is therefore the only gate evidence.",
              "- Stage C runs two fill models on the same candidates: the legacy plan-price fill (what the standalone research studies assumed) and PaperBroker's `stop_market` resting order at the documented level (activated on the next bar, gap-through at the open, cancelled if untouched). The primary model follows the lane's entry style: armed-trigger state machines (4HR, 3-2-2, Miyagi) really do trade through their level on the signal bar, so the plan fill is honest for them; close-confirmed predicates and shadow setups confirm at bar close against a level the bar has already passed, so the resting order is their honest documented-bracket fill. The difference between the two is reported as the fill-assumption inflation.",
              "- Transition-reclaim populations are saved shadow candidate sets: the full-corpus MNQ set (3,292 candidates on the Polygon price basis) plus the May-Jul 2026 MNQ (311) and Apr-Jul 2026 MES (415) sets the expectancy audit used, which come from TradingView continuous-contract exports and were re-anchored onto the corpus by the contract-roll offset observable per candidate (see each lane's extraction audit for the offset levels and the handful of bar-data outliers dropped). The detector lives in an uncommitted shadow-lane change and has no engine concept, so D1 is its only gate evidence. It is RANGE/CHOP-conditioned by construction, so `MARKET_CONDITION_NOT_TRENDING` is structural for it under the production `require_trending_condition` rule.",
              "- The inverted ORB lane borrows the un-mirrored source lane's engine funnel (the paper build contract evaluates confluence and risk on the source signal and mirrors only at the broker) and uses the lane's 8-tick IOC cap for stage E.",
              "- D2/D3 use `ioc_limit` fills. D2 disables only the survival floors (20% drawdown breaker, $150 daily loss, balance-tiered sizing → fixed 1 contract) so structural and signal-layer gates are visible unmasked; D3 keeps them. Both disable the strategy-permission policy gate and the isolated-lane `max_trades_per_day=3` cap, restoring the 2026-07-27 evidence posture the #367/#372 closures ran under; MES is re-admitted to `allowed_instruments` for the MES lanes. `require_trending_condition`, STRONG-trend, volume, EMA-stack, R:R, confluence, min-target and max-stop rules are all untouched.",
              "- Stage B's next-bar-open entry deliberately ignores the strategy's entry mechanics; it measures whether the signal bar has directional information, not whether it is tradable.",
              "- Dollar magnitudes are replay-scale historical evidence, not live-fill proof.",
              "", "## Reproduction", "", "```bash",
              "python3 scripts/edge_decomposition_audit.py --data-root <main-tree>/data --transition-dir <main-tree>/logs \\",
              "  --logs logs/edge_decomposition --engine-jobs 3 \\",
              "  --out scripts/edge_decomposition_audit_results.json --report docs/edge-decomposition-audit-2026-09-07.md",
              "```", ""]
    return "\n".join(lines)


# ─── Main ────────────────────────────────────────────────────────────────────

def _default_data_root() -> Path:
    for candidate in (REPO / "data", REPO.parents[2] / "data" if len(REPO.parents) > 2 else None):
        if candidate and (candidate / CORPUS_15M).exists():
            return candidate
    return REPO / "data"


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=REPO, text=True).strip()


def _strip(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _strip(v) for k, v in obj.items() if k != "cand"}
    if isinstance(obj, list):
        return [_strip(v) for v in obj]
    if isinstance(obj, float) and math.isinf(obj):
        return "inf"
    return obj


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-root", type=Path, default=_default_data_root())
    parser.add_argument("--transition-dir", type=Path, default=None)
    parser.add_argument("--logs", type=Path, default=REPO / "logs/edge_decomposition")
    parser.add_argument("--out", type=Path, default=REPO / "scripts/edge_decomposition_audit_results.json")
    parser.add_argument("--report", type=Path, default=REPO / "docs/edge-decomposition-audit-2026-09-07.md")
    parser.add_argument("--findings", type=Path, default=REPO / "docs/edge-decomposition-audit-2026-09-07-findings.md",
                        help="optional markdown inserted after the summary table (interpretation, kept out of code)")
    parser.add_argument("--lanes", nargs="*", default=list(LANES))
    parser.add_argument("--engine-jobs", type=int, default=0, help="0 = do not launch engine runs (use cached)")
    parser.add_argument("--skip-engine", action="store_true")
    parser.add_argument("--engine-only", action="store_true")
    args = parser.parse_args()

    transition_dir = args.transition_dir or (args.data_root.parent / "logs")
    lanes = [LANES[k] for k in args.lanes]
    base_config = load_config()
    risk_hash_before = hashlib.sha256((REPO / "risk_rules.yaml").read_bytes()).hexdigest()

    if args.engine_jobs > 0 and not args.skip_engine:
        jobs = [(l.key, tag) for l in lanes if l.engine for tag in ENGINE_TAGS]
        jobs = [(k, t) for k, t in jobs if not (engine_log_dir(args.logs, LANES[k], t) / "_complete.json").exists()]
        print(f"[engine] launching {len(jobs)} isolated runs with {args.engine_jobs} workers", flush=True)
        with ProcessPoolExecutor(max_workers=args.engine_jobs) as pool:
            for summary in pool.map(run_engine_job, [k for k, _ in jobs], [t for _, t in jobs],
                                    [str(args.data_root)] * len(jobs), [str(args.logs)] * len(jobs)):
                print(f"[engine] done {summary['lane']}/{summary['tag']} ({summary['ran']} days)", flush=True)
    if args.engine_only:
        return 0

    results_lanes = []
    for lane in lanes:
        print(f"[lane] {lane.key}", flush=True)
        results_lanes.append(analyze_lane(lane, args.data_root, transition_dir, args.logs, base_config,
                                          skip_engine=args.skip_engine))
        v = results_lanes[-1]["verdict"]
        print(f"[lane] {lane.key}: raw={results_lanes[-1]['A_raw']['candidates']} → {v['primary_failure_stage']}", flush=True)

    risk_hash_after = hashlib.sha256((REPO / "risk_rules.yaml").read_bytes()).hexdigest()
    if risk_hash_before != risk_hash_after:
        raise RuntimeError("risk_rules.yaml changed during the audit")
    corpora = sorted({l.corpus for l in lanes})
    results = {
        "meta": {
            "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sha": _git("rev-parse", "HEAD"),
            "risk_rules_sha256": risk_hash_after,
            "commission_round_trip": COMMISSION_ROUND_TRIP,
            "horizons_min": list(HORIZONS_MIN),
            "slippage_sweep": list(SLIPPAGE_SWEEP),
            "corpora": {c: _tree_fingerprint(args.data_root / c) for c in corpora},
            "data_root": str(args.data_root),
        },
        "lanes": _strip(results_lanes),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    candidates_path = args.out.with_name(args.out.stem + "_candidates.jsonl.gz")
    with gzip.open(candidates_path, "wt", encoding="utf-8") as handle:
        for lane_result in results["lanes"]:
            for cand in lane_result.pop("candidates"):
                handle.write(json.dumps(cand, sort_keys=True, default=str) + "\n")
    results["meta"]["candidates_file"] = candidates_path.name
    args.out.write_text(json.dumps(results, indent=1, default=str) + "\n")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    findings = args.findings.read_text(encoding="utf-8") if args.findings and args.findings.exists() else None
    args.report.write_text(render_report(results, findings).rstrip() + "\n")
    print(json.dumps({l["lane"]: l["verdict"]["primary_failure_stage"] for l in results["lanes"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
