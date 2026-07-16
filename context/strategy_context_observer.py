"""Observation-only context collector for futures strategy selection.

This module records the context variables the operator wants measured before
any new gate is proposed. It has no broker imports, no risk authority, and no
return value that the runner can use to approve or reject a trade.
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import date
from pathlib import Path
from statistics import median
from typing import Any

from context.bar_history import BarHistory
from context.market_context import MarketState

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


EVIDENCE_FILENAME = "strategy_context_observations.jsonl"
SUPPORTED_INSTRUMENTS = {"MNQ", "MES"}
PAIR = {"MNQ": "MES", "MES": "MNQ"}


def append_strategy_context_observation(
    *,
    log_dir: str | Path,
    state: MarketState,
    decision,
    recent_bars: list[dict[str, Any]],
    for_date: date | None = None,
) -> dict[str, Any] | None:
    """Append one read-only observation row and return the written row.

    The row is intentionally descriptive, not prescriptive. It records whether
    context was present and how it looked on the decision bar; it never says
    whether a future trade should be allowed.
    """
    instrument = _root(getattr(state, "instrument", ""))
    if instrument not in SUPPORTED_INSTRUMENTS:
        return None

    history = BarHistory(log_dir=str(log_dir))
    pair = PAIR[instrument]
    pair_bars = history.recent(pair, 8, for_date=for_date)
    record = {
        "kind": "strategy_context_observation",
        "observation_only": True,
        "gate_authoritative": False,
        "broker_evaluated": False,
        "risk_evaluated": False,
        "timestamp": state.timestamp.isoformat(),
        "session": state.session,
        "instrument": instrument,
        "timeframe": getattr(state.ohlc, "timeframe", None),
        "close": getattr(state.ohlc, "close", None),
        "decision": getattr(decision, "decision", None),
        "decision_reason": getattr(decision, "reason", None),
        "failed_gates": list(getattr(decision, "failed_gates", []) or []),
        "selected_setup": _setup_snapshot(getattr(decision, "setup", None)),
        "candidate_audit_count": len(getattr(decision, "candidate_audit", []) or []),
        "trend_persistence": _trend_persistence(recent_bars),
        "mnq_mes_agreement": _pair_agreement(instrument, recent_bars, pair, pair_bars),
        "overnight_range_location": _overnight_location(state),
        "supply_demand_confluence": _supply_demand_confluence(state),
        "key_level_confluence": _key_level_confluence(state),
        "impulse_state": _impulse_state(recent_bars),
        "structural_regime": _jsonable(getattr(state, "structural_regime", None)),
        "market_condition": state.market_condition,
        "vwap": _jsonable(getattr(state, "vwap", None)),
        "gex": _jsonable(getattr(state, "gex", None)),
    }
    path = evidence_path(log_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.write(json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n")
            handle.flush()
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return record


def evidence_path(log_dir: str | Path) -> Path:
    return Path(log_dir) / EVIDENCE_FILENAME


def _root(instrument: str) -> str:
    return (instrument or "").upper().replace("1!", "").rstrip("1234567890HMUZ")


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return value
    if isinstance(value, (str, int, float, bool, list)):
        return value
    return str(value)


def _setup_snapshot(setup) -> dict[str, Any] | None:
    if setup is None:
        return None
    return {
        "strategy": getattr(setup, "strategy", None),
        "direction": getattr(setup, "direction", None),
        "entry": getattr(setup, "entry", None),
        "stop": getattr(setup, "stop", None),
        "target": getattr(setup, "target", None),
        "rr_ratio": getattr(setup, "rr_ratio", None),
        "direction_role": getattr(setup, "direction_role", None),
    }


def _clean_bars(bars: list[dict[str, Any]]) -> list[dict[str, float]]:
    out = []
    for bar in bars or []:
        try:
            out.append(
                {
                    "open": float(bar["open"]),
                    "high": float(bar["high"]),
                    "low": float(bar["low"]),
                    "close": float(bar["close"]),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _close_direction(bars: list[dict[str, Any]]) -> str | None:
    clean = _clean_bars(bars)
    if len(clean) < 2:
        return None
    delta = clean[-1]["close"] - clean[-2]["close"]
    if delta > 0:
        return "UP"
    if delta < 0:
        return "DOWN"
    return "FLAT"


def _trend_persistence(bars: list[dict[str, Any]]) -> dict[str, Any]:
    clean = _clean_bars(bars)
    direction = _close_direction(clean)
    if direction in {None, "FLAT"}:
        return {"direction": direction, "same_direction_closes": 0, "window_bars": len(clean)}
    count = 0
    for idx in range(len(clean) - 1, 0, -1):
        delta = clean[idx]["close"] - clean[idx - 1]["close"]
        step = "UP" if delta > 0 else "DOWN" if delta < 0 else "FLAT"
        if step != direction:
            break
        count += 1
    return {
        "direction": direction,
        "same_direction_closes": count,
        "window_bars": len(clean),
    }


def _pair_agreement(
    instrument: str,
    bars: list[dict[str, Any]],
    pair: str,
    pair_bars: list[dict[str, Any]],
) -> dict[str, Any]:
    direction = _close_direction(bars)
    pair_direction = _close_direction(pair_bars)
    return {
        "instrument": instrument,
        "instrument_direction": direction,
        "pair": pair,
        "pair_direction": pair_direction,
        "agrees": (
            None
            if direction is None or pair_direction is None
            else direction == pair_direction and direction != "FLAT"
        ),
        "pair_bars_available": len(_clean_bars(pair_bars)),
    }


def _overnight_location(state: MarketState) -> dict[str, Any]:
    raw = state.raw if isinstance(state.raw, dict) else {}
    high = raw.get("overnight_high") or raw.get("ovn_high")
    low = raw.get("overnight_low") or raw.get("ovn_low")
    close = getattr(state.ohlc, "close", None)
    if high is None or low is None or close is None:
        return {
            "available": False,
            "overnight_high": high,
            "overnight_low": low,
            "location": None,
        }
    high = float(high)
    low = float(low)
    close = float(close)
    if close > high:
        location = "above_overnight_range"
    elif close < low:
        location = "below_overnight_range"
    else:
        location = "inside_overnight_range"
    width = high - low
    return {
        "available": True,
        "overnight_high": high,
        "overnight_low": low,
        "location": location,
        "range_position": None if width <= 0 else round((close - low) / width, 4),
    }


def _supply_demand_confluence(state: MarketState) -> dict[str, Any]:
    close = float(getattr(state.ohlc, "close", 0.0) or 0.0)
    sd = getattr(state, "sd", None)
    if sd is None:
        return {"available": False, "zone": None}
    zone = None
    if sd.price_in_demand(close):
        zone = "in_demand"
    elif sd.price_in_supply(close):
        zone = "in_supply"
    elif sd.price_at_demand(close):
        zone = "near_demand"
    elif sd.price_at_supply(close):
        zone = "near_supply"
    return {
        "available": True,
        "zone": zone,
        "supply_top": sd.supply_top,
        "supply_bottom": sd.supply_bottom,
        "demand_top": sd.demand_top,
        "demand_bottom": sd.demand_bottom,
    }


def _key_level_confluence(state: MarketState) -> dict[str, Any]:
    close = getattr(state.ohlc, "close", None)
    levels = getattr(state, "key_levels", None)
    previous = getattr(state, "previous_day", None)
    if close is None:
        return {"available": False, "nearest": None}
    candidates = []
    for name, value in (
        ("hod", getattr(levels, "hod", None) if levels else None),
        ("lod", getattr(levels, "lod", None) if levels else None),
        ("prev_week_high", getattr(levels, "prev_week_high", None) if levels else None),
        ("prev_week_low", getattr(levels, "prev_week_low", None) if levels else None),
        ("previous_day_high", getattr(previous, "high", None)),
        ("previous_day_low", getattr(previous, "low", None)),
        ("previous_day_close", getattr(previous, "close", None)),
    ):
        if value is not None:
            candidates.append((name, float(value), abs(float(close) - float(value))))
    if not candidates:
        return {"available": False, "nearest": None}
    name, value, distance = min(candidates, key=lambda item: item[2])
    return {
        "available": True,
        "nearest": {"name": name, "price": value, "distance_points": round(distance, 4)},
    }


def _impulse_state(bars: list[dict[str, Any]]) -> dict[str, Any]:
    clean = _clean_bars(bars)
    if len(clean) < 4:
        return {"state": "insufficient_data", "direction": None, "same_direction_closes": 0}
    persistence = _trend_persistence(clean)
    direction = persistence["direction"]
    count = int(persistence["same_direction_closes"] or 0)
    ranges = [bar["high"] - bar["low"] for bar in clean[-6:] if bar["high"] >= bar["low"]]
    median_range = median(ranges) if ranges else 0.0
    extension = abs(clean[-1]["close"] - clean[-min(len(clean), count + 1)]["close"]) if count else 0.0
    if direction in {None, "FLAT"} or count <= 1:
        state = "pre_impulse"
    elif count in {2, 3}:
        state = "active_impulse"
    elif median_range and extension > 1.5 * median_range:
        state = "late_entry"
    else:
        state = "active_impulse"
    return {
        "state": state,
        "direction": direction,
        "same_direction_closes": count,
        "extension_points": round(extension, 4),
        "median_recent_range": round(median_range, 4),
    }
