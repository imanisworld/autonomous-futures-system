"""Paper-isolated live evidence lanes for selected MNQ Strat patterns.

The lanes are additive observers. They never call risk, normal strategy
execution, or an external broker. ``paper_sim`` owns its hypothetical position
with :class:`execution.paper_broker.PaperBroker`; ``observe_only`` cannot create
an order. Every mode defaults fail-closed to ``observe_only``.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

from context.market_context import MarketState
from execution.broker_interface import BracketOrder
from execution.paper_broker import NextBarOHLC, PaperBroker, TICK_SIZE, TICK_VALUE
from execution.trailing import compute_trailed_stop
from risk.risk_engine import RiskEngine
from strategy.shadow_setups import evaluate_shadow_setups
from strategy.signal_engine import DecisionEngine

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


VALID_MODES = ("observe_only", "paper_sim")
DEFAULT_MODE = "observe_only"
MNQ_COMMISSION_ROUND_TRIP = 1.48
MAX_SEEN_KEYS = 1000

# Proposed deployment-time overrides from the operator-approved activation.
# These are documentation/data only; lane_mode() still defaults every lane to
# observe_only until the reviewed environment pins are explicitly installed.
INITIAL_ACTIVATION_MODES = {
    "strat_22_reversal": "paper_sim",
    "strat_22_continuation": "observe_only",
    "strat_32": "observe_only",
    "strat_322": "observe_only",
}


@dataclass(frozen=True)
class LaneSpec:
    key: str
    sequence: str
    trigger: str
    env_name: str
    observed_strategy: Optional[str]


LANES: dict[str, LaneSpec] = {
    "strat_22_reversal": LaneSpec(
        key="strat_22_reversal",
        sequence="strat_22_reversal",
        trigger="reversal",
        env_name="MNQ_STRAT_22_REVERSAL_MODE",
        observed_strategy="strat_22_reversal_observed",
    ),
    "strat_22_continuation": LaneSpec(
        key="strat_22_continuation",
        sequence="strat_22_continuation",
        trigger="continuation",
        env_name="MNQ_STRAT_22_CONTINUATION_MODE",
        observed_strategy="strat_22_continuation_observed",
    ),
    "strat_32": LaneSpec(
        key="strat_32",
        sequence="strat_outside_continuation",
        trigger="outside_bar_followthrough",
        env_name="MNQ_STRAT_32_MODE",
        observed_strategy=None,
    ),
    "strat_322": LaneSpec(
        key="strat_322",
        sequence="strat_322_reversal",
        trigger="reversal",
        env_name="MNQ_STRAT_322_MODE",
        observed_strategy="strat_322_reversal_observed",
    ),
}


def lane_mode(lane: str, cfg=None) -> str:
    spec = LANES[lane]
    attr_name = f"mnq_{lane}_mode"
    raw = getattr(cfg, attr_name, None) if cfg is not None else None
    if raw is None:
        raw = os.getenv(spec.env_name, DEFAULT_MODE)
    mode = str(raw or DEFAULT_MODE).strip().lower()
    return mode if mode in VALID_MODES else DEFAULT_MODE


def evidence_path(log_dir: str | Path, lane: str) -> Path:
    return Path(log_dir) / f"mnq_{lane}_evidence.jsonl"


def state_path(log_dir: str | Path, lane: str) -> Path:
    return Path(log_dir) / f"mnq_{lane}_state.json"


def _load_state(log_dir: str | Path, lane: str) -> dict[str, Any]:
    try:
        raw = json.loads(state_path(log_dir, lane).read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"seen": [], "position": None}
    if not isinstance(raw, dict):
        return {"seen": [], "position": None}
    return {
        "seen": list(raw.get("seen") or [])[-MAX_SEEN_KEYS:],
        "position": raw.get("position") if isinstance(raw.get("position"), dict) else None,
    }


def _save_state(log_dir: str | Path, lane: str, state: dict[str, Any]) -> None:
    path = state_path(log_dir, lane)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(state, handle, sort_keys=True)
            handle.write("\n")
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _append_event(log_dir: str | Path, lane: str, event: dict[str, Any]) -> None:
    path = evidence_path(log_dir, lane)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.write(json.dumps(event, separators=(",", ":"), sort_keys=True) + "\n")
            handle.flush()
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def load_flatness_snapshot(
    log_dir: str | Path, *, for_date: Optional[date] = None
) -> dict[str, Any]:
    """Read previously captured preflight evidence; never contact Tradovate."""
    path = Path(log_dir) / "live_preflight_state.json"
    expected_date = (for_date or date.today()).isoformat()
    try:
        raw = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {
            "source": str(path),
            "checked_at": None,
            "positions": {"flat": None, "detail": "snapshot unavailable"},
            "working_orders": {"flat": None, "detail": "snapshot unavailable"},
            "confirmed": False,
            "reason": "BROKER_FLATNESS_EVIDENCE_ABSENT",
        }
    checks = {
        str(item.get("name")): item
        for item in (raw.get("checks") or [])
        if isinstance(item, dict)
    }
    positions = checks.get("no_open_positions") or {}
    orders = checks.get("no_working_orders") or {}
    same_day = str(raw.get("date") or "") == expected_date
    confirmed = bool(
        same_day
        and raw.get("last_preflight_at")
        and positions.get("ok") is True
        and orders.get("ok") is True
    )
    return {
        "source": str(path),
        "checked_at": raw.get("last_preflight_at"),
        "positions": {
            "flat": positions.get("ok"),
            "detail": positions.get("detail"),
        },
        "working_orders": {
            "flat": orders.get("ok"),
            "detail": orders.get("detail"),
        },
        "confirmed": confirmed,
        "reason": (
            "TRADOVATE_DEMO_FLAT_CONFIRMED"
            if confirmed
            else "BROKER_FLATNESS_EVIDENCE_UNCONFIRMED"
        ),
    }


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return value
    return str(value)


def _context(state: MarketState) -> dict[str, Any]:
    return {
        "trend": _jsonable(state.trend),
        "regime": state.market_condition,
        "structural_regime": state.structural_regime,
        "vwap": _jsonable(state.vwap),
        "gex": _jsonable(state.gex),
        "supply_demand": _jsonable(state.sd),
    }


def _forming_bars(state: MarketState) -> list[dict[str, Any]]:
    raw = state.raw if isinstance(state.raw, dict) else {}
    strat = state.strat
    return [
        {
            "role": "two_bars_back",
            "type": getattr(strat, "two_bars_back_type", None),
            "high": raw.get("two_bars_back_high"),
            "low": raw.get("two_bars_back_low"),
        },
        {
            "role": "previous",
            "type": getattr(strat, "previous_bar_type", None),
            "high": raw.get("previous_bar_high"),
            "low": raw.get("previous_bar_low"),
        },
        {
            "role": "signal",
            "type": getattr(strat, "current_bar_type", None),
            "open": state.ohlc.open,
            "high": state.ohlc.high,
            "low": state.ohlc.low,
            "close": state.ohlc.close,
        },
    ]


def _existing_setup(
    state: MarketState, spec: LaneSpec, cfg, recent_bars: list[dict]
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    if spec.key == "strat_32":
        if state.volume and state.volume.relative is not None and state.volume.relative < 0.8:
            return None, "VOLUME_BELOW_EXISTING_STRAT_32_MINIMUM_0.8"
        detail = DecisionEngine(config=cfg)._try_strat_outside_continuation(state)
        if detail is None:
            return None, "EXISTING_STRAT_32_DETECTOR_REJECTED"
        return {
            "direction": detail.direction,
            "entry": detail.entry,
            "stop": detail.stop,
            "target": detail.target,
            "rr_ratio": detail.rr_ratio,
            "source_strategy": detail.strategy,
            "notes": detail.notes,
        }, None

    candidates = evaluate_shadow_setups(state, recent_bars)
    candidate = next(
        (item for item in candidates if item.strategy == spec.observed_strategy), None
    )
    if candidate is None:
        return None, "EXISTING_SHADOW_BRACKET_BUILDER_REJECTED_OR_CONTEXT_MISSING"
    data = candidate.to_dict()
    data["source_strategy"] = data.pop("strategy")
    return data, None


def detect_lane(
    state: MarketState, *, cfg=None, recent_bars: Optional[list[dict]] = None
) -> tuple[Optional[LaneSpec], Optional[dict[str, Any]], Optional[str]]:
    """Map the already-classified Strat sequence to exactly one requested lane."""
    root = str(state.instrument or "").upper().replace("1!", "")
    strat = state.strat
    if root != "MNQ" or strat is None:
        return None, None, None
    spec = next(
        (item for item in LANES.values() if item.sequence == strat.strat_sequence), None
    )
    if spec is None:
        return None, None, None
    if strat.strat_trigger != spec.trigger:
        return spec, None, (
            f"STRAT_TRIGGER_MISMATCH: expected {spec.trigger}, got {strat.strat_trigger}"
        )
    if strat.strat_direction not in ("LONG", "SHORT"):
        return spec, None, "STRAT_DIRECTION_MISSING"
    setup, rejection = _existing_setup(state, spec, cfg, recent_bars or [])
    if setup is not None and setup.get("direction") != strat.strat_direction:
        return spec, None, (
            "STRAT_DIRECTION_MISMATCH: classifier and reused setup builder disagree"
        )
    return spec, setup, rejection


def _candidate_key(state: MarketState, spec: LaneSpec) -> str:
    direction = getattr(state.strat, "strat_direction", None)
    return f"{spec.key}|MNQ|{state.timestamp.isoformat()}|{direction}"


def _actual_rr(direction: str, entry: float, stop: float, target: float) -> float:
    return RiskEngine.calculate_rr(direction, entry, stop, target)


def _candidate_event(
    *,
    state: MarketState,
    spec: LaneSpec,
    mode: str,
    setup: Optional[dict[str, Any]],
    rejection: Optional[str],
    flatness: dict[str, Any],
    lane_position_open: bool,
    cfg,
) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
    key = _candidate_key(state, spec)
    accepted = setup is not None and rejection is None
    reasons: list[str] = []
    if rejection:
        reasons.append(rejection)
    if lane_position_open:
        accepted = False
        reasons.append("LANE_POSITION_ALREADY_OPEN")
    if mode == "paper_sim" and flatness.get("confirmed") is not True:
        accepted = False
        reasons.append("STRUCTURAL_ISOLATION_UNCONFIRMED_FAIL_CLOSED")

    paper_order_id = None
    # Every candidate is explicitly classified as filled or not filled.  In
    # observe_only the reason is non-execution, not a broker attempt.
    fill_status = "NO_FILL"
    fill_entry = None
    position_state = None
    execution_audit = None
    if accepted and mode == "paper_sim":
        slip_ticks = float(getattr(cfg, "fill_slippage_ticks", 1.0) or 0.0)
        tick = TICK_SIZE["MNQ"]
        market = float(state.ohlc.close)
        fill_entry = (
            market + slip_ticks * tick
            if setup["direction"] == "LONG"
            else market - slip_ticks * tick
        )
        geometry_ok = (
            setup["stop"] < fill_entry < setup["target"]
            if setup["direction"] == "LONG"
            else setup["target"] < fill_entry < setup["stop"]
        )
        if not geometry_ok:
            accepted = False
            reasons.append("LIVE_DECISION_PRICE_OUTSIDE_STRUCTURAL_BRACKET")
            fill_status = "NO_FILL"
        else:
            order = BracketOrder(
                instrument="MNQ",
                direction=setup["direction"],
                entry=float(setup["entry"]),
                stop=float(setup["stop"]),
                target=float(setup["target"]),
                rr_ratio=float(setup["rr_ratio"]),
                strategy=spec.key,
                contracts=1,
                force_market_entry=True,
                force_runner_exit=False,
                post_fill_validation_required=False,
            )
            broker = PaperBroker(
                slippage_ticks=slip_ticks,
                pessimistic_both_hit=True,
                runner_mode=False,
            )
            fill = broker.execute_bracket(order, market_price=market)
            if not str(fill.paper_order_id or "").startswith("PAPER-"):
                raise RuntimeError("paper isolation invariant failed: missing PAPER order id")
            paper_order_id = fill.paper_order_id
            fill_entry = float(fill.entry_price)
            fill_status = "FILLED" if fill.result == "OPEN" else "NO_FILL"
            execution_audit = fill.execution_audit
            if fill.result == "OPEN":
                position_state = {
                    "candidate_key": key,
                    "lane": spec.key,
                    "pattern": spec.sequence,
                    "direction": setup["direction"],
                    "session": state.session,
                    "timeframe": state.ohlc.timeframe,
                    "entry_ts": state.timestamp.isoformat(),
                    "requested_entry": setup["entry"],
                    "actual_entry": fill_entry,
                    "stop": setup["stop"],
                    "target": setup["target"],
                    "paper_order_id": paper_order_id,
                    "runner_max_favorable": fill_entry,
                    "last_runner_stop": setup["stop"],
                    "runner_movements": [],
                    "mfe_points": 0.0,
                    "mae_points": 0.0,
                    "bars_held": 0,
                    "commission_round_trip": MNQ_COMMISSION_ROUND_TRIP,
                    "slippage_ticks": slip_ticks,
                    "structural_isolation": flatness,
                }
    elif not accepted:
        fill_status = "NO_FILL"

    event = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "event": "CANDIDATE",
        "candidate_key": key,
        "timestamp": state.timestamp.isoformat(),
        "session": state.session,
        "instrument": "MNQ",
        "timeframe": state.ohlc.timeframe,
        "lane": spec.key,
        "mode": mode,
        "strat_pattern": spec.sequence,
        "pattern_role": spec.trigger,
        "direction": getattr(state.strat, "strat_direction", None),
        "bars": _forming_bars(state),
        "trigger_price": setup.get("entry") if setup else None,
        "current_market_price": state.ohlc.close,
        "entry": setup.get("entry") if setup else None,
        "stop": setup.get("stop") if setup else None,
        "target": setup.get("target") if setup else None,
        "initial_rr": setup.get("rr_ratio") if setup else None,
        "context": _context(state),
        "accepted": accepted,
        "rejection_reason": "; ".join(reasons) if reasons else None,
        "fill_status": fill_status,
        "actual_fill": fill_entry,
        "actual_fill_rr": (
            _actual_rr(setup["direction"], fill_entry, setup["stop"], setup["target"])
            if setup and fill_entry is not None else None
        ),
        "paper_order_id": paper_order_id,
        "execution_audit": execution_audit,
        "structural_isolation_status": (
            "CONFIRMED" if flatness.get("confirmed") is True else "UNCONFIRMED_FAIL_CLOSED"
        ),
        "tradovate_snapshot": flatness,
        "normal_execution_affected": False,
        "broker_route": "PaperBroker" if paper_order_id else None,
    }
    if mode == "observe_only" and accepted:
        event["execution_note"] = "observe_only: order creation disabled"
    return event, position_state


def _advance_position(
    *,
    state: MarketState,
    lane: str,
    lane_state: dict[str, Any],
    log_dir: str | Path,
) -> Optional[dict[str, Any]]:
    position = lane_state.get("position")
    if not isinstance(position, dict):
        return None
    if state.timestamp.isoformat() <= str(position.get("entry_ts") or ""):
        return None

    direction = str(position["direction"])
    entry = float(position["actual_entry"])
    stop = float(position["stop"])
    target = float(position["target"])
    previous_max = float(position.get("runner_max_favorable") or entry)
    active_runner_stop, armed = compute_trailed_stop(
        is_long=direction == "LONG",
        entry=entry,
        original_stop=stop,
        max_favorable=previous_max,
        activation_r=float(os.getenv("RUNNER_ACTIVATION_R", "1.0") or 1.0),
        trail_r=float(os.getenv("RUNNER_TRAIL_R", "0.5") or 0.5),
    )
    if active_runner_stop != float(position.get("last_runner_stop") or stop):
        movement = {
            "ts": state.timestamp.isoformat(),
            "from": position.get("last_runner_stop"),
            "to": active_runner_stop,
            "armed": armed,
        }
        position.setdefault("runner_movements", []).append(movement)
        position["last_runner_stop"] = active_runner_stop
        _append_event(log_dir, lane, {
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "event": "RUNNER_MOVE",
            "candidate_key": position["candidate_key"],
            "lane": lane,
            **movement,
        })

    if direction == "LONG":
        position["mfe_points"] = max(
            float(position.get("mfe_points") or 0.0), state.ohlc.high - entry
        )
        position["mae_points"] = max(
            float(position.get("mae_points") or 0.0), entry - state.ohlc.low
        )
    else:
        position["mfe_points"] = max(
            float(position.get("mfe_points") or 0.0), entry - state.ohlc.low
        )
        position["mae_points"] = max(
            float(position.get("mae_points") or 0.0), state.ohlc.high - entry
        )
    position["bars_held"] = int(position.get("bars_held") or 0) + 1

    broker = PaperBroker(
        slippage_ticks=float(position.get("slippage_ticks") or 0.0),
        pessimistic_both_hit=True,
        runner_mode=False,
    )
    broker.restore_position(
        "MNQ",
        direction,
        entry,
        stop,
        target,
        1,
        paper_order_id=position.get("paper_order_id"),
        runner_max_favorable=previous_max,
    )
    fill = broker.resolve_position(
        NextBarOHLC(open=state.ohlc.open, high=state.ohlc.high, low=state.ohlc.low)
    )
    current_favorable = state.ohlc.high if direction == "LONG" else state.ohlc.low
    position["runner_max_favorable"] = (
        max(previous_max, current_favorable)
        if direction == "LONG" else min(previous_max, current_favorable)
    )
    lane_state["position"] = position
    if fill is None:
        _save_state(log_dir, lane, lane_state)
        return None

    gross_dollars = float(fill.pnl_dollars or 0.0)
    commission = float(position.get("commission_round_trip") or 0.0)
    net_dollars = gross_dollars - commission
    net_ticks = net_dollars / TICK_VALUE["MNQ"]
    event = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "event": "OUTCOME",
        "candidate_key": position["candidate_key"],
        "lane": lane,
        "pattern": position["pattern"],
        "instrument": "MNQ",
        "timeframe": position["timeframe"],
        "session": position["session"],
        "direction": direction,
        "entry_ts": position["entry_ts"],
        "exit_ts": state.timestamp.isoformat(),
        "paper_order_id": fill.paper_order_id,
        "requested_entry": position["requested_entry"],
        "actual_entry": entry,
        "stop": stop,
        "target": target,
        "exit_price": fill.exit_price,
        "exit_reason": fill.exit_reason,
        "result": fill.result,
        "gross_ticks": fill.pnl_ticks,
        "gross_dollars": gross_dollars,
        "commission_dollars": commission,
        "net_ticks": round(net_ticks, 2),
        "net_dollars": round(net_dollars, 2),
        "maximum_favorable_excursion_points": round(float(position["mfe_points"]), 4),
        "maximum_adverse_excursion_points": round(float(position["mae_points"]), 4),
        "maximum_favorable_excursion_ticks": round(
            float(position["mfe_points"]) / TICK_SIZE["MNQ"], 2
        ),
        "maximum_adverse_excursion_ticks": round(
            float(position["mae_points"]) / TICK_SIZE["MNQ"], 2
        ),
        "runner_movements": position.get("runner_movements") or [],
        "bars_held": position["bars_held"],
        "structural_isolation_status": "CONFIRMED",
        "tradovate_snapshot": position["structural_isolation"],
        "broker_route": "PaperBroker",
        "normal_execution_affected": False,
    }
    lane_state["position"] = None
    _save_state(log_dir, lane, lane_state)
    _append_event(log_dir, lane, event)
    return event


def process_mnq_strat_evidence(
    *,
    state: MarketState,
    cfg,
    log_dir: str | Path,
    recent_bars: Optional[list[dict]] = None,
    for_date: Optional[date] = None,
    flatness_snapshot: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    """Advance all lanes, then collect the current bar's one classified lane."""
    if str(state.instrument or "").upper().replace("1!", "") != "MNQ":
        return []
    flatness = flatness_snapshot or load_flatness_snapshot(log_dir, for_date=for_date)
    emitted: list[dict[str, Any]] = []
    states: dict[str, dict[str, Any]] = {}
    for lane in LANES:
        lane_state = _load_state(log_dir, lane)
        states[lane] = lane_state
        outcome = _advance_position(
            state=state, lane=lane, lane_state=lane_state, log_dir=log_dir
        )
        if outcome:
            emitted.append(outcome)

    spec, setup, rejection = detect_lane(
        state, cfg=cfg, recent_bars=recent_bars or []
    )
    if spec is None:
        return emitted
    lane_state = states[spec.key]
    key = _candidate_key(state, spec)
    if key in set(lane_state.get("seen") or []):
        return emitted
    lane_state.setdefault("seen", []).append(key)
    lane_state["seen"] = lane_state["seen"][-MAX_SEEN_KEYS:]
    event, position = _candidate_event(
        state=state,
        spec=spec,
        mode=lane_mode(spec.key, cfg),
        setup=setup,
        rejection=rejection,
        flatness=flatness,
        lane_position_open=lane_state.get("position") is not None,
        cfg=cfg,
    )
    if position is not None:
        lane_state["position"] = position
    _save_state(log_dir, spec.key, lane_state)
    _append_event(log_dir, spec.key, event)
    emitted.append(event)
    return emitted
