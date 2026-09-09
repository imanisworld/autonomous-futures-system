"""Paper-only forward collector for MNQ Daily 2-2 continuation.

This is an isolated $5,000 hypothetical swing ledger. It consumes the existing
5-minute context feed, discovers a Daily 2-2 continuation causally, admits the
entry through the same 8-tick IOC model used by the futures paper lanes, and
holds the static Daily bracket across sessions until stop or target.

Safety contract:
- MNQ only, one contract, one open swing position at a time;
- $5,000 hypothetical starting ledger; never reads/writes the real book;
- natural prior-Daily-range stop and 2R target, no stop tightening;
- actual IOC fill-to-stop planned risk <= $1,750;
- actual fill-based R:R >= 2.0;
- current context gates: TRENDING, STRONG, rel-vol >= 0.8, direction-aligned
  trend and EMA 9/21/55 stack;
- one adverse tick per entry/stop exit, $1.48 round-turn commission;
- pessimistic same-bar stop/target handling through PaperBroker;
- 20%/25% drawdown warnings; hard paper halt at 30% from ledger peak;
- no EOD flatten: this is intentionally a multi-day swing evidence lane;
- no external broker, no promotion path, no active-book mutation.

The $1,750 risk ceiling and 30% hard drawdown ceiling were preregistered from
an offline $5k survival check before activation. On the frozen MNQ corpus the
8-tick IOC contract retained 34 non-overlapping trades, +$13,885.18 net,
PF 2.02, both halves and 2024/2025/2026 positive, with 25.15% max drawdown.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from context import wide_stop_ledger_paper as wide_contract
from context.bar_history import _parse_dt
from execution.broker_interface import BracketOrder
from execution.paper_broker import NextBarOHLC, PaperBroker
from strategy.strat_classifier import TWO_DOWN, TWO_UP, StratBar, classify_bar

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

ET = ZoneInfo("America/New_York")
INSTRUMENT = "MNQ"
STRATEGY = "daily_22_continuation"
LEDGER_NAME = "daily_22_5k"
LABEL = "hypothetical_daily_swing"
STARTING_BALANCE = 5_000.0
CONTRACTS = 1
TICK = 0.25
TICK_VALUE = 0.50
POINT_VALUE = 2.0
IOC_TOLERANCE_TICKS = 8.0
SLIPPAGE_TICKS = 1.0
COMMISSION_ROUND_TRIP = 1.48
MIN_COMPLETE_SESSION_BARS = 200
MAX_PLANNED_RISK_DOLLARS = 1_750.0
MIN_ACTUAL_RR = 2.0
WARN_DRAWDOWNS = (0.20, 0.25)
MAX_DRAWDOWN = 0.30
MAX_SEEN = 500
_LOCAL_LOCK = threading.Lock()


def _ledger_dir(log_dir: str | Path) -> Path:
    return Path(log_dir) / wide_contract.JOURNAL_ROOT / LEDGER_NAME


def _state_path(log_dir: str | Path) -> Path:
    return _ledger_dir(log_dir) / "swing_state.json"


def _audit_path(log_dir: str | Path) -> Path:
    return _ledger_dir(log_dir) / "swing_audit.jsonl"


def _epoch(cfg) -> Optional[datetime]:
    raw = wide_contract.epoch_start(cfg)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _empty_state(epoch: datetime) -> dict[str, Any]:
    return {
        "epoch": epoch.isoformat(),
        "balance": STARTING_BALANCE,
        "peak": STARTING_BALANCE,
        "max_drawdown": 0.0,
        "halted": False,
        "position": None,
        "seen": [],
    }


def _load_state(log_dir: str | Path, epoch: datetime) -> dict[str, Any]:
    try:
        raw = json.loads(_state_path(log_dir).read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return _empty_state(epoch)
    if not isinstance(raw, dict) or raw.get("epoch") != epoch.isoformat():
        return _empty_state(epoch)
    return {
        "epoch": epoch.isoformat(),
        "balance": float(raw.get("balance", STARTING_BALANCE)),
        "peak": float(raw.get("peak", STARTING_BALANCE)),
        "max_drawdown": max(0.0, float(raw.get("max_drawdown", 0.0))),
        "halted": bool(raw.get("halted", False)),
        "position": raw.get("position") if isinstance(raw.get("position"), dict) else None,
        "seen": [str(v) for v in list(raw.get("seen") or [])[-MAX_SEEN:]],
    }


def _save_state(log_dir: str | Path, state: dict[str, Any]) -> None:
    path = _state_path(log_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(state, handle, sort_keys=True, default=str)
            handle.write("\n")
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _journal(log_dir: str | Path, row: dict[str, Any]) -> None:
    path = _audit_path(log_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")


@contextmanager
def _lock(log_dir: str | Path):
    path = _ledger_dir(log_dir) / ".collector.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCAL_LOCK:
        with path.open("a") as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _trading_day(ts: datetime) -> Optional[date]:
    local = ts.astimezone(ET)
    clock = local.time().replace(tzinfo=None)
    if time(17, 0) <= clock < time(18, 0):
        return None
    if clock >= time(18, 0):
        return local.date() + timedelta(days=1)
    return local.date()


def _daily_sessions(bars_5m: list[dict]) -> dict[date, dict[str, Any]]:
    sessions: dict[date, dict[str, Any]] = {}
    parsed: list[tuple[datetime, dict]] = []
    for raw in bars_5m:
        ts = _parse_dt(str(raw.get("ts") or raw.get("timestamp") or ""))
        if ts is not None:
            parsed.append((ts, raw))
    parsed.sort(key=lambda item: item[0])
    for ts, raw in parsed:
        day = _trading_day(ts)
        if day is None:
            continue
        try:
            o = float(raw["open"])
            h = float(raw["high"])
            l = float(raw["low"])
            c = float(raw["close"])
        except (KeyError, TypeError, ValueError):
            continue
        bucket = sessions.setdefault(
            day,
            {"open": o, "high": h, "low": l, "close": c, "count": 0, "bars": []},
        )
        bucket["high"] = max(float(bucket["high"]), h)
        bucket["low"] = min(float(bucket["low"]), l)
        bucket["close"] = c
        bucket["count"] = int(bucket["count"]) + 1
        bucket["bars"].append({"ts": ts, "open": o, "high": h, "low": l, "close": c})
    return sessions


def _candidate_for_current_bar(bars_5m: list[dict], current_ts: datetime) -> Optional[dict[str, Any]]:
    current_day = _trading_day(current_ts)
    if current_day is None:
        return None
    sessions = _daily_sessions(bars_5m)
    completed_days = sorted(
        day
        for day, bar in sessions.items()
        if day < current_day and int(bar.get("count") or 0) >= MIN_COMPLETE_SESSION_BARS
    )
    if len(completed_days) < 2 or current_day not in sessions:
        return None
    two_back = sessions[completed_days[-2]]
    previous = sessions[completed_days[-1]]
    previous_type = classify_bar(
        StratBar(high=float(previous["high"]), low=float(previous["low"])),
        StratBar(high=float(two_back["high"]), low=float(two_back["low"])),
    )
    if previous_type not in (TWO_UP, TWO_DOWN):
        return None

    first_break = None
    for bar in sessions[current_day]["bars"]:
        breaks_high = float(bar["high"]) > float(previous["high"])
        breaks_low = float(bar["low"]) < float(previous["low"])
        if not (breaks_high or breaks_low):
            continue
        if breaks_high and breaks_low:
            return {
                "status": "NO_TRADE",
                "reason": "AMBIGUOUS_FIRST_BOUNDARY_BREAK",
                "trading_day": current_day.isoformat(),
                "trigger_ts": bar["ts"],
            }
        first_break = (bar, TWO_UP if breaks_high else TWO_DOWN)
        break
    if first_break is None:
        return None
    trigger_bar, current_type = first_break
    # Never backfill a trigger discovered before this webhook invocation.
    if trigger_bar["ts"] != current_ts:
        return None
    if current_type != previous_type:
        return {
            "status": "NO_TRADE",
            "reason": "FIRST_BREAK_IS_22_REVERSAL_NOT_CONTINUATION",
            "trading_day": current_day.isoformat(),
            "trigger_ts": current_ts,
        }

    if current_type == TWO_UP:
        direction = "LONG"
        entry = float(previous["high"]) + TICK
        stop = float(previous["low"]) - TICK
        risk_points = entry - stop
        target = entry + 2.0 * risk_points
    else:
        direction = "SHORT"
        entry = float(previous["low"]) - TICK
        stop = float(previous["high"]) + TICK
        risk_points = stop - entry
        target = entry - 2.0 * risk_points
    return {
        "status": "CANDIDATE",
        "reason": "DAILY_22_CONTINUATION_FIRST_BREAK",
        "trading_day": current_day.isoformat(),
        "trigger_ts": current_ts,
        "direction": direction,
        "planned_entry": entry,
        "stop": stop,
        "target": target,
        "previous_high": float(previous["high"]),
        "previous_low": float(previous["low"]),
        "previous_type": previous_type,
    }


def _get(obj, name: str, default=None):
    value = getattr(obj, name, default)
    return default if value is None else value


def _relative_volume(payload) -> float:
    direct = _get(payload, "reconstructed_rel_vol", None)
    if direct is not None:
        try:
            return float(direct)
        except (TypeError, ValueError):
            return 0.0
    try:
        avg = float(_get(payload, "avg_volume", 0.0) or 0.0)
        return float(_get(payload, "volume", 0.0) or 0.0) / avg if avg > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def _context_gate(payload, direction: str) -> tuple[bool, str, dict[str, Any]]:
    market_condition = str(_get(payload, "market_condition", "")).upper()
    trend_strength = str(_get(payload, "trend_strength", "")).upper()
    trend_direction = str(
        _get(payload, "trend_direction", None)
        or _get(payload, "reconstructed_trend_direction", "")
    ).upper()
    rel_vol = _relative_volume(payload)
    want = "UP" if direction == "LONG" else "DOWN"
    try:
        close = float(_get(payload, "close"))
        ema9 = float(_get(payload, "ema_9"))
        ema21 = float(_get(payload, "ema_21"))
        ema55 = float(_get(payload, "ema_55"))
        ema_ok = close > ema9 > ema21 > ema55 if direction == "LONG" else close < ema9 < ema21 < ema55
    except (TypeError, ValueError):
        ema_ok = False
    audit = {
        "market_condition": market_condition,
        "trend_strength": trend_strength,
        "trend_direction": trend_direction,
        "relative_volume": round(rel_vol, 6),
        "ema_stack_aligned": ema_ok,
    }
    if market_condition != "TRENDING":
        return False, "MARKET_NOT_TRENDING", audit
    if trend_strength != "STRONG":
        return False, "TREND_NOT_STRONG", audit
    if rel_vol < 0.8:
        return False, "RELATIVE_VOLUME_BELOW_0_8", audit
    if trend_direction != want:
        return False, "TREND_DIRECTION_MISMATCH", audit
    if not ema_ok:
        return False, "EMA_STACK_MISMATCH_OR_MISSING", audit
    return True, "CONTEXT_APPROVED", audit


def _expected_ioc_fill(candidate: dict[str, Any], market: float) -> tuple[Optional[float], str]:
    planned = float(candidate["planned_entry"])
    direction = str(candidate["direction"])
    tol = IOC_TOLERANCE_TICKS * TICK
    slip = SLIPPAGE_TICKS * TICK
    if direction == "LONG":
        limit_px = planned + tol
        if market > limit_px:
            return None, "ENTRY_NOT_FILLED"
        return min(limit_px, market + slip), "IOC_MARKETABLE"
    limit_px = planned - tol
    if market < limit_px:
        return None, "ENTRY_NOT_FILLED"
    return max(limit_px, market - slip), "IOC_MARKETABLE"


def _actual_rr(candidate: dict[str, Any], fill: float) -> float:
    stop = float(candidate["stop"])
    target = float(candidate["target"])
    if candidate["direction"] == "LONG":
        risk = fill - stop
        reward = target - fill
    else:
        risk = stop - fill
        reward = fill - target
    return reward / risk if risk > 0 else -1.0


def _broker(balance: float) -> PaperBroker:
    return PaperBroker(
        starting_balance=balance,
        slippage_ticks=SLIPPAGE_TICKS,
        pessimistic_both_hit=True,
        breakeven_at_1r=False,
        runner_mode=False,
        entry_fill_model="ioc_limit",
        entry_tolerance_ticks_by_root={INSTRUMENT: IOC_TOLERANCE_TICKS},
    )


def _base_row(state: dict[str, Any], event: str, current_ts: datetime) -> dict[str, Any]:
    peak = float(state["peak"])
    balance = float(state["balance"])
    dd = max(0.0, (peak - balance) / peak) if peak > 0 else 1.0
    return {
        "label": LABEL,
        "hypothetical": True,
        "promotion_path": False,
        "external_broker": False,
        "active_book_mutated": False,
        "collector": "daily_22_swing_v1",
        "collector_event": event,
        "instrument": INSTRUMENT,
        "strategy": STRATEGY,
        "ledger": LEDGER_NAME,
        "ledger_starting_balance": STARTING_BALANCE,
        "ledger_balance": round(balance, 2),
        "ledger_peak": round(peak, 2),
        "drawdown_percent": round(dd * 100.0, 4),
        "max_drawdown_percent_seen": round(float(state["max_drawdown"]) * 100.0, 4),
        "hard_drawdown_percent": MAX_DRAWDOWN * 100.0,
        "timestamp": current_ts.isoformat(),
    }


def _resolve_position(state: dict[str, Any], payload, current_ts: datetime) -> Optional[dict[str, Any]]:
    position = state.get("position")
    if not isinstance(position, dict):
        return None
    entry_time = _parse_dt(str(position.get("entry_time") or ""))
    if entry_time is None or current_ts < entry_time:
        return None
    broker = _broker(float(state["balance"]))
    broker.restore_position(
        instrument=INSTRUMENT,
        direction=str(position["direction"]),
        entry=float(position["entry"]),
        stop=float(position["stop"]),
        target=float(position["target"]),
        contracts=CONTRACTS,
        paper_order_id=position.get("paper_order_id"),
    )
    high = float(payload.high)
    low = float(payload.low)
    entry = float(position["entry"])
    if position["direction"] == "LONG":
        position["mae_points"] = max(float(position.get("mae_points", 0.0)), max(0.0, entry - low))
        position["mfe_points"] = max(float(position.get("mfe_points", 0.0)), max(0.0, high - entry))
    else:
        position["mae_points"] = max(float(position.get("mae_points", 0.0)), max(0.0, high - entry))
        position["mfe_points"] = max(float(position.get("mfe_points", 0.0)), max(0.0, entry - low))
    fill = broker.resolve_position(NextBarOHLC(high=high, low=low))
    if fill is None:
        state["position"] = position
        return None

    gross = round(float(fill.pnl_dollars or 0.0), 2)
    net = round(gross - COMMISSION_ROUND_TRIP, 2)
    state["balance"] = round(float(state["balance"]) + net, 2)
    state["peak"] = max(float(state["peak"]), float(state["balance"]))
    dd = max(0.0, (float(state["peak"]) - float(state["balance"])) / float(state["peak"]))
    state["max_drawdown"] = max(float(state["max_drawdown"]), dd)
    if dd >= MAX_DRAWDOWN:
        state["halted"] = True
    row = _base_row(state, "OUTCOME", current_ts)
    row.update(
        candidate_key=position.get("candidate_key"),
        outcome_result="WIN" if net > 0 else "LOSS" if net < 0 else "BREAKEVEN",
        broker_result=fill.result,
        exit_reason=fill.exit_reason,
        entry_price=fill.entry_price,
        exit_price=fill.exit_price,
        gross_pnl_dollars=gross,
        commission_round_trip=COMMISSION_ROUND_TRIP,
        net_pnl_dollars=net,
        mae_points=round(float(position.get("mae_points", 0.0)), 4),
        mfe_points=round(float(position.get("mfe_points", 0.0)), 4),
        holding_hours=round((current_ts - entry_time).total_seconds() / 3600.0, 3),
        drawdown_warning_20=dd >= WARN_DRAWDOWNS[0],
        drawdown_warning_25=dd >= WARN_DRAWDOWNS[1],
        hard_halt=bool(state["halted"]),
    )
    state["position"] = None
    return row


def process_five_min_bar(
    *, payload, cfg, bars_5m: list[dict], log_dir: str | Path, for_date: Optional[date] = None
) -> list[dict[str, Any]]:
    """Resolve an open swing, then evaluate a causal Daily 2-2 first break."""
    if not wide_contract.evaluate(cfg).active:
        return []
    root = "".join(ch for ch in str(getattr(payload, "ticker", "")).upper() if ch.isalpha())[:3]
    if root != INSTRUMENT:
        return []
    current_ts = _parse_dt(str(getattr(payload, "timestamp", "") or ""))
    epoch = _epoch(cfg)
    if current_ts is None or epoch is None or current_ts < epoch:
        return []

    events: list[dict[str, Any]] = []
    with _lock(log_dir):
        state = _load_state(log_dir, epoch)
        outcome = _resolve_position(state, payload, current_ts)
        if outcome is not None:
            _journal(log_dir, outcome)
            events.append(outcome)

        candidate = _candidate_for_current_bar(bars_5m, current_ts)
        if candidate is None:
            _save_state(log_dir, state)
            return events
        key = f"{candidate.get('trading_day')}|{candidate.get('reason')}|{candidate.get('direction', '')}"
        if key in state["seen"]:
            _save_state(log_dir, state)
            return events
        state["seen"].append(key)
        state["seen"] = state["seen"][-MAX_SEEN:]

        row = _base_row(state, "CANDIDATE", current_ts)
        row.update(candidate_key=key, **candidate)
        if candidate.get("status") != "CANDIDATE":
            row.update(lane_result="NO_TRADE", lane_failed_rule=candidate.get("reason"))
            _journal(log_dir, row)
            events.append(row)
            _save_state(log_dir, state)
            return events
        if state["halted"]:
            row.update(lane_result="BLOCKED", lane_failed_rule="MAX_DRAWDOWN_HALT")
            _journal(log_dir, row)
            events.append(row)
            _save_state(log_dir, state)
            return events
        if state.get("position") is not None:
            row.update(lane_result="BLOCKED", lane_failed_rule="OPEN_SWING_POSITION")
            _journal(log_dir, row)
            events.append(row)
            _save_state(log_dir, state)
            return events

        context_ok, context_reason, context_audit = _context_gate(payload, str(candidate["direction"]))
        row["context"] = context_audit
        if not context_ok:
            row.update(lane_result="REJECTED_CONTEXT", lane_failed_rule=context_reason)
            _journal(log_dir, row)
            events.append(row)
            _save_state(log_dir, state)
            return events

        market = float(payload.close)
        expected_fill, admission = _expected_ioc_fill(candidate, market)
        if expected_fill is None:
            row.update(lane_result="CANCELLED", fill_status="CANCELLED", fill_reason=admission)
            _journal(log_dir, row)
            events.append(row)
            _save_state(log_dir, state)
            return events
        rr = _actual_rr(candidate, expected_fill)
        risk_dollars = abs(expected_fill - float(candidate["stop"])) * POINT_VALUE
        row.update(
            expected_fill=round(expected_fill, 4),
            actual_fill_rr=round(rr, 6),
            planned_risk_dollars=round(risk_dollars, 2),
            max_planned_risk_dollars=MAX_PLANNED_RISK_DOLLARS,
        )
        if rr < MIN_ACTUAL_RR:
            row.update(lane_result="REJECTED_RISK", lane_failed_rule="ACTUAL_RR_BELOW_2")
            _journal(log_dir, row)
            events.append(row)
            _save_state(log_dir, state)
            return events
        if risk_dollars > MAX_PLANNED_RISK_DOLLARS:
            row.update(lane_result="REJECTED_RISK", lane_failed_rule="PLANNED_RISK_ABOVE_1750")
            _journal(log_dir, row)
            events.append(row)
            _save_state(log_dir, state)
            return events

        broker = _broker(float(state["balance"]))
        fill = broker.execute_bracket(
            BracketOrder(
                instrument=INSTRUMENT,
                direction=str(candidate["direction"]),
                entry=float(candidate["planned_entry"]),
                stop=float(candidate["stop"]),
                target=float(candidate["target"]),
                rr_ratio=2.0,
                strategy=STRATEGY,
                contracts=CONTRACTS,
            ),
            market_price=market,
        )
        row.update(
            fill_status=fill.result,
            fill_price=fill.entry_price if fill.result == "OPEN" else None,
            fill_reason=fill.exit_reason,
            fill_paper_order_id=getattr(fill, "paper_order_id", None),
        )
        if fill.result == "OPEN":
            close_time = current_ts + timedelta(minutes=5)
            state["position"] = {
                "candidate_key": key,
                "direction": candidate["direction"],
                "planned_entry": candidate["planned_entry"],
                "entry": float(fill.entry_price),
                "stop": candidate["stop"],
                "target": candidate["target"],
                "actual_rr": rr,
                "planned_risk_dollars": risk_dollars,
                "entry_time": close_time.isoformat(),
                "paper_order_id": getattr(fill, "paper_order_id", None),
                "mae_points": 0.0,
                "mfe_points": 0.0,
            }
            row["lane_result"] = "OPEN"
        else:
            row["lane_result"] = "CANCELLED"
        _journal(log_dir, row)
        events.append(row)
        _save_state(log_dir, state)
    return events
