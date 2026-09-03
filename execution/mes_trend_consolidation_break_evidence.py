"""Paper-isolated evidence lane for MES trend-consolidation breaks.

This lane is intentionally narrow:

* MES only.
* Existing ``trend_consolidation_break_observed`` detector only.
* ``observe_only`` or ``paper_sim`` only; never Tradovate/demo/live.
* No normal strategy, risk, broker, ORB, VWAP, or Strat behavior changes.

The observed setup is a stop-entry beyond a completed consolidation cluster, so
``paper_sim`` first creates a pending PaperBroker stop-market order. It may fill
only on the next causal bar. If both stop and target are touched on the fill/exit
bar, PaperBroker's pessimistic resolver books the stop first.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

from context.market_context import MarketState
from execution.broker_interface import BracketOrder
from execution.mnq_strat_evidence import load_flatness_snapshot
from execution.paper_broker import NextBarOHLC, PaperBroker, TICK_SIZE, TICK_VALUE
from ops.watcher_memory_guard import read_critical_memory_block
from execution.trailing import compute_trailed_stop
from risk.risk_engine import RiskEngine
from strategy.shadow_setups import evaluate_shadow_setups

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


LANE = "trend_consolidation_break"
OBSERVED_STRATEGY = "trend_consolidation_break_observed"
CONFLUENCE_ONLY_STRATEGIES = {"impulse_first_pullback_observed"}
VALID_MODES = ("observe_only", "paper_sim")
DEFAULT_MODE = "observe_only"
ENV_NAME = "MES_TREND_CONSOLIDATION_BREAK_MODE"
MES_COMMISSION_ROUND_TRIP = 1.48
MAX_SEEN_KEYS = 1000

# Documentation/data only. Bare checkouts still fail closed to observe_only;
# production activation requires an explicit reviewed env pin.
INITIAL_ACTIVATION_MODES = {LANE: "paper_sim"}


def lane_mode(cfg=None) -> str:
    raw = getattr(cfg, "mes_trend_consolidation_break_mode", None) if cfg else None
    if raw is None:
        raw = os.getenv(ENV_NAME, DEFAULT_MODE)
    mode = str(raw or DEFAULT_MODE).strip().lower()
    return mode if mode in VALID_MODES else DEFAULT_MODE


def evidence_path(log_dir: str | Path) -> Path:
    return Path(log_dir) / "mes_trend_consolidation_break_evidence.jsonl"


def state_path(log_dir: str | Path) -> Path:
    return Path(log_dir) / "mes_trend_consolidation_break_state.json"


def _load_state(log_dir: str | Path) -> dict[str, Any]:
    try:
        raw = json.loads(state_path(log_dir).read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"seen": [], "pending_order": None, "position": None}
    if not isinstance(raw, dict):
        return {"seen": [], "pending_order": None, "position": None}
    return {
        "seen": list(raw.get("seen") or [])[-MAX_SEEN_KEYS:],
        "pending_order": (
            raw.get("pending_order") if isinstance(raw.get("pending_order"), dict) else None
        ),
        "position": raw.get("position") if isinstance(raw.get("position"), dict) else None,
    }


def _save_state(log_dir: str | Path, state: dict[str, Any]) -> None:
    path = state_path(log_dir)
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


def _append_event(log_dir: str | Path, event: dict[str, Any]) -> None:
    path = evidence_path(log_dir)
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


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return value
    return str(value)


def _context(state: MarketState, decision=None) -> dict[str, Any]:
    return {
        "trend": _jsonable(state.trend),
        "regime": state.market_condition,
        "structural_regime": state.structural_regime,
        "vwap": _jsonable(state.vwap),
        "gex": _jsonable(state.gex),
        "supply_demand": _jsonable(state.sd),
        "normal_runtime_decision": (
            {
                "decision": getattr(decision, "decision", None),
                "reason": getattr(decision, "reason", None),
                "failed_gates": list(getattr(decision, "failed_gates", None) or []),
                "regime": getattr(decision, "regime", None),
                "setup": _jsonable(getattr(decision, "setup", None)),
            }
            if decision is not None else None
        ),
    }


def _bar_snapshot(bar: dict[str, Any], role: str) -> dict[str, Any]:
    return {
        "role": role,
        "ts": bar.get("ts") or bar.get("timestamp"),
        "open": bar.get("open"),
        "high": bar.get("high"),
        "low": bar.get("low"),
        "close": bar.get("close"),
        "volume": bar.get("volume"),
    }


def _forming_bars(recent_bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    roles = [
        "impulse_first",
        "impulse_second",
        "cluster_first",
        "cluster_second",
        "signal_cluster_final",
    ]
    return [
        _bar_snapshot(bar, roles[idx])
        for idx, bar in enumerate((recent_bars or [])[-5:])
    ]


def _candidate_key(state: MarketState, direction: str) -> str:
    return f"{LANE}|MES|{state.timestamp.isoformat()}|{direction}"


def _actual_rr(direction: str, entry: float, stop: float, target: float) -> float:
    return RiskEngine.calculate_rr(direction, entry, stop, target)


def detect_candidate(
    state: MarketState, recent_bars: Optional[list[dict[str, Any]]] = None
) -> tuple[Optional[dict[str, Any]], list[dict[str, Any]], Optional[str]]:
    root = str(state.instrument or "").upper().replace("1!", "")
    if root != "MES":
        return None, [], None
    candidates = evaluate_shadow_setups(state, recent_bars or [])
    owner = next((item for item in candidates if item.strategy == OBSERVED_STRATEGY), None)
    confluence = [
        {
            **item.to_dict(),
            "ownership_status": "CONFLUENCE_ONLY_DUPLICATE_SUPPRESSED",
        }
        for item in candidates
        if item.strategy in CONFLUENCE_ONLY_STRATEGIES
    ]
    if owner is None:
        return None, confluence, "EXISTING_TREND_CONSOLIDATION_BREAK_OBSERVER_REJECTED"
    data = owner.to_dict()
    data["source_strategy"] = data.pop("strategy")
    return data, confluence, None


def _bracket_order(setup: dict[str, Any]) -> BracketOrder:
    return BracketOrder(
        instrument="MES",
        direction=setup["direction"],
        entry=float(setup["entry"]),
        stop=float(setup["stop"]),
        target=float(setup["target"]),
        rr_ratio=float(setup["rr_ratio"]),
        strategy=LANE,
        contracts=1,
        force_market_entry=False,
        force_runner_exit=False,
        post_fill_validation_required=False,
    )


def _broker(cfg) -> PaperBroker:
    return PaperBroker(
        slippage_ticks=float(getattr(cfg, "fill_slippage_ticks", 1.0) or 0.0),
        pessimistic_both_hit=True,
        runner_mode=False,
        entry_fill_model="stop_market",
    )


def _open_pending_order(
    *, setup: dict[str, Any], cfg, flatness: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    broker = _broker(cfg)
    fill = broker.execute_bracket(_bracket_order(setup))
    if fill.result != "PENDING":
        raise RuntimeError(f"paper isolation invariant failed: expected PENDING, got {fill.result}")
    if not str(fill.paper_order_id or "").startswith("PAPER-"):
        raise RuntimeError("paper isolation invariant failed: missing PAPER order id")
    pending = {
        "candidate_key": None,
        "lane": LANE,
        "instrument": "MES",
        "direction": setup["direction"],
        "session": None,
        "timeframe": None,
        "signal_ts": None,
        "requested_entry": setup["entry"],
        "stop": setup["stop"],
        "target": setup["target"],
        "rr_ratio": setup["rr_ratio"],
        "paper_order_id": fill.paper_order_id,
        "commission_round_trip": MES_COMMISSION_ROUND_TRIP,
        "slippage_ticks": float(getattr(cfg, "fill_slippage_ticks", 1.0) or 0.0),
        "structural_isolation": flatness,
    }
    return fill.paper_order_id, pending


def _candidate_event(
    *,
    state: MarketState,
    cfg,
    setup: Optional[dict[str, Any]],
    confluence: list[dict[str, Any]],
    rejection: Optional[str],
    flatness: dict[str, Any],
    lane_state: dict[str, Any],
    recent_bars: list[dict[str, Any]],
    decision=None,
) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
    mode = lane_mode(cfg)
    accepted = setup is not None and rejection is None
    reasons: list[str] = []
    if rejection:
        reasons.append(rejection)
    if lane_state.get("pending_order") is not None or lane_state.get("position") is not None:
        accepted = False
        reasons.append("LANE_ORDER_OR_POSITION_ALREADY_OPEN")
    if mode == "paper_sim" and flatness.get("confirmed") is not True:
        accepted = False
        reasons.append("STRUCTURAL_ISOLATION_UNCONFIRMED_FAIL_CLOSED")
    if mode == "paper_sim":
        _memory_block = read_critical_memory_block()
        if _memory_block is not None:
            accepted = False
            reasons.append(str(_memory_block.get("code") or "MEMORY_CRITICAL"))

    paper_order_id = None
    pending_order = None
    fill_status = "NO_ORDER"
    execution_audit = None
    if accepted and mode == "paper_sim":
        paper_order_id, pending_order = _open_pending_order(
            setup=setup, cfg=cfg, flatness=flatness
        )
        pending_order.update({
            "candidate_key": _candidate_key(state, setup["direction"]),
            "session": state.session,
            "timeframe": state.ohlc.timeframe,
            "signal_ts": state.timestamp.isoformat(),
        })
        fill_status = "PENDING"
        execution_audit = {
            "entry_model": "stop_market_next_causal_bar",
            "paper_broker": "PaperBroker",
            "adverse_slippage_ticks": pending_order["slippage_ticks"],
            "same_bar_ambiguity": "stop_first",
        }
    elif accepted and mode == "observe_only":
        fill_status = "NO_FILL"
    else:
        fill_status = "NO_FILL"

    event = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "event": "CANDIDATE",
        "candidate_key": _candidate_key(state, setup["direction"]) if setup else None,
        "timestamp": state.timestamp.isoformat(),
        "session": state.session,
        "instrument": "MES",
        "timeframe": state.ohlc.timeframe,
        "lane": LANE,
        "mode": mode,
        "source_observer": OBSERVED_STRATEGY,
        "direction": setup.get("direction") if setup else None,
        "bars": _forming_bars(recent_bars),
        "trigger_price": setup.get("entry") if setup else None,
        "current_market_price": state.ohlc.close,
        "entry": setup.get("entry") if setup else None,
        "stop": setup.get("stop") if setup else None,
        "target": setup.get("target") if setup else None,
        "initial_rr": setup.get("rr_ratio") if setup else None,
        "context": _context(state, decision=decision),
        "normal_runtime_gate": {
            "decision": getattr(decision, "decision", None),
            "reason": getattr(decision, "reason", None),
            "failed_gates": list(getattr(decision, "failed_gates", None) or []),
            "regime": getattr(decision, "regime", None),
        } if decision is not None else None,
        "confluence_observers": confluence,
        "accepted": accepted,
        "rejection_reason": "; ".join(reasons) if reasons else None,
        "fill_status": fill_status,
        "actual_fill": None,
        "actual_fill_rr": None,
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
    return event, pending_order


def _position_from_fill(
    *, pending: dict[str, Any], fill, state: MarketState
) -> dict[str, Any]:
    entry = float(fill.entry_price)
    return {
        "candidate_key": pending["candidate_key"],
        "lane": LANE,
        "instrument": "MES",
        "direction": pending["direction"],
        "session": pending["session"],
        "timeframe": pending["timeframe"],
        "signal_ts": pending["signal_ts"],
        "entry_ts": state.timestamp.isoformat(),
        "requested_entry": pending["requested_entry"],
        "actual_entry": entry,
        "stop": pending["stop"],
        "target": pending["target"],
        "paper_order_id": pending["paper_order_id"],
        "runner_max_favorable": entry,
        "last_runner_stop": pending["stop"],
        "runner_movements": [],
        "mfe_points": 0.0,
        "mae_points": 0.0,
        "bars_held": 0,
        "commission_round_trip": pending["commission_round_trip"],
        "slippage_ticks": pending["slippage_ticks"],
        "structural_isolation": pending["structural_isolation"],
    }


def _outcome_event(position: dict[str, Any], fill, state: MarketState) -> dict[str, Any]:
    gross_dollars = float(fill.pnl_dollars or 0.0)
    commission = float(position.get("commission_round_trip") or 0.0)
    net_dollars = gross_dollars - commission
    net_ticks = net_dollars / TICK_VALUE["MES"]
    return {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "event": "OUTCOME",
        "candidate_key": position["candidate_key"],
        "lane": LANE,
        "instrument": "MES",
        "timeframe": position["timeframe"],
        "session": position["session"],
        "direction": position["direction"],
        "signal_ts": position["signal_ts"],
        "entry_ts": position["entry_ts"],
        "exit_ts": state.timestamp.isoformat(),
        "paper_order_id": fill.paper_order_id,
        "requested_entry": position["requested_entry"],
        "actual_entry": position["actual_entry"],
        "stop": position["stop"],
        "target": position["target"],
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
            float(position["mfe_points"]) / TICK_SIZE["MES"], 2
        ),
        "maximum_adverse_excursion_ticks": round(
            float(position["mae_points"]) / TICK_SIZE["MES"], 2
        ),
        "runner_movements": position.get("runner_movements") or [],
        "bars_held": position["bars_held"],
        "structural_isolation_status": "CONFIRMED",
        "tradovate_snapshot": position["structural_isolation"],
        "broker_route": "PaperBroker",
        "normal_execution_affected": False,
    }


def _update_excursions(position: dict[str, Any], state: MarketState) -> None:
    direction = str(position["direction"])
    entry = float(position["actual_entry"])
    if direction == "LONG":
        position["mfe_points"] = max(float(position.get("mfe_points") or 0.0), state.ohlc.high - entry)
        position["mae_points"] = max(float(position.get("mae_points") or 0.0), entry - state.ohlc.low)
    else:
        position["mfe_points"] = max(float(position.get("mfe_points") or 0.0), entry - state.ohlc.low)
        position["mae_points"] = max(float(position.get("mae_points") or 0.0), state.ohlc.high - entry)
    position["bars_held"] = int(position.get("bars_held") or 0) + 1


def _maybe_runner_event(position: dict[str, Any], state: MarketState, log_dir: str | Path) -> None:
    direction = str(position["direction"])
    previous_max = float(position.get("runner_max_favorable") or position["actual_entry"])
    active_runner_stop, armed = compute_trailed_stop(
        is_long=direction == "LONG",
        entry=float(position["actual_entry"]),
        original_stop=float(position["stop"]),
        max_favorable=previous_max,
        activation_r=float(os.getenv("RUNNER_ACTIVATION_R", "1.0") or 1.0),
        trail_r=float(os.getenv("RUNNER_TRAIL_R", "0.5") or 0.5),
    )
    if active_runner_stop != float(position.get("last_runner_stop") or position["stop"]):
        movement = {
            "ts": state.timestamp.isoformat(),
            "from": position.get("last_runner_stop"),
            "to": active_runner_stop,
            "armed": armed,
        }
        position.setdefault("runner_movements", []).append(movement)
        position["last_runner_stop"] = active_runner_stop
        _append_event(log_dir, {
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "event": "RUNNER_MOVE",
            "candidate_key": position["candidate_key"],
            "lane": LANE,
            **movement,
        })
    current_favorable = state.ohlc.high if direction == "LONG" else state.ohlc.low
    position["runner_max_favorable"] = (
        max(previous_max, current_favorable)
        if direction == "LONG" else min(previous_max, current_favorable)
    )


def _resolve_position(
    *, state: MarketState, lane_state: dict[str, Any], log_dir: str | Path
) -> Optional[dict[str, Any]]:
    position = lane_state.get("position")
    if not isinstance(position, dict):
        return None
    if state.timestamp.isoformat() < str(position.get("entry_ts") or ""):
        return None
    _maybe_runner_event(position, state, log_dir)
    _update_excursions(position, state)
    broker = PaperBroker(
        slippage_ticks=float(position.get("slippage_ticks") or 0.0),
        pessimistic_both_hit=True,
        runner_mode=False,
    )
    broker.restore_position(
        "MES",
        position["direction"],
        float(position["actual_entry"]),
        float(position["stop"]),
        float(position["target"]),
        1,
        paper_order_id=position.get("paper_order_id"),
        runner_max_favorable=float(position.get("runner_max_favorable") or position["actual_entry"]),
    )
    fill = broker.resolve_position(
        NextBarOHLC(open=state.ohlc.open, high=state.ohlc.high, low=state.ohlc.low)
    )
    lane_state["position"] = position
    if fill is None:
        _save_state(log_dir, lane_state)
        return None
    event = _outcome_event(position, fill, state)
    lane_state["position"] = None
    _save_state(log_dir, lane_state)
    _append_event(log_dir, event)
    return event


def _advance_pending_order(
    *, state: MarketState, lane_state: dict[str, Any], log_dir: str | Path, cfg
) -> list[dict[str, Any]]:
    pending = lane_state.get("pending_order")
    if not isinstance(pending, dict):
        return []
    if state.timestamp.isoformat() <= str(pending.get("signal_ts") or ""):
        return []
    broker = _broker(cfg)
    broker.restore_pending_stop_entry(
        _bracket_order({
            "direction": pending["direction"],
            "entry": pending["requested_entry"],
            "stop": pending["stop"],
            "target": pending["target"],
            "rr_ratio": pending["rr_ratio"],
        }),
        contracts=1,
        paper_order_id=pending["paper_order_id"],
    )
    fill = broker.resolve_position(
        NextBarOHLC(open=state.ohlc.open, high=state.ohlc.high, low=state.ohlc.low)
    )
    emitted: list[dict[str, Any]] = []
    if fill is not None and fill.result == "CANCELLED":
        event = {
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "event": "NO_FILL",
            "candidate_key": pending["candidate_key"],
            "lane": LANE,
            "instrument": "MES",
            "session": pending["session"],
            "signal_ts": pending["signal_ts"],
            "resolved_at": state.timestamp.isoformat(),
            "paper_order_id": pending["paper_order_id"],
            "requested_entry": pending["requested_entry"],
            "direction": pending["direction"],
            "reason": fill.exit_reason,
            "fill_status": "NO_FILL",
            "broker_route": "PaperBroker",
            "normal_execution_affected": False,
        }
        lane_state["pending_order"] = None
        _save_state(log_dir, lane_state)
        _append_event(log_dir, event)
        return [event]

    # stop_market activation returns None whether the newly opened position is
    # still open or the pending entry simply had no terminal fill object. The
    # broker's internal state is not inspectable, so reproduce the causal trigger
    # test from PaperBroker to decide whether to restore a position.
    entry = float(pending["requested_entry"])
    direction = pending["direction"]
    tick = TICK_SIZE["MES"]
    slip = float(pending.get("slippage_ticks") or 0.0) * tick
    actual_entry: float | None = None
    if direction == "LONG":
        if state.ohlc.open >= entry:
            actual_entry = state.ohlc.open + slip
        elif state.ohlc.high >= entry:
            actual_entry = entry + slip
    elif direction == "SHORT":
        if state.ohlc.open <= entry:
            actual_entry = state.ohlc.open - slip
        elif state.ohlc.low <= entry:
            actual_entry = entry - slip
    geometry_ok = (
        actual_entry is not None
        and (
            float(pending["stop"]) < actual_entry < float(pending["target"])
            if direction == "LONG"
            else float(pending["target"]) < actual_entry < float(pending["stop"])
        )
    )
    if not geometry_ok:
        # Defensive: PaperBroker should already have returned CANCELLED here.
        event = {
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "event": "NO_FILL",
            "candidate_key": pending["candidate_key"],
            "lane": LANE,
            "instrument": "MES",
            "session": pending["session"],
            "signal_ts": pending["signal_ts"],
            "resolved_at": state.timestamp.isoformat(),
            "paper_order_id": pending["paper_order_id"],
            "requested_entry": pending["requested_entry"],
            "direction": direction,
            "reason": (
                "ENTRY_NOT_TRIGGERED"
                if actual_entry is None
                else "ENTRY_BRACKET_INVALID_AT_FILL"
            ),
            "fill_status": "NO_FILL",
            "broker_route": "PaperBroker",
            "normal_execution_affected": False,
        }
        lane_state["pending_order"] = None
        _save_state(log_dir, lane_state)
        _append_event(log_dir, event)
        return [event]

    pseudo_fill = type(
        "PaperFill",
        (),
        {
            "entry_price": actual_entry,
            "paper_order_id": pending["paper_order_id"],
        },
    )()
    position = _position_from_fill(pending=pending, fill=pseudo_fill, state=state)
    fill_event = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "event": "FILL",
        "candidate_key": pending["candidate_key"],
        "lane": LANE,
        "instrument": "MES",
        "session": pending["session"],
        "timeframe": pending["timeframe"],
        "signal_ts": pending["signal_ts"],
        "entry_ts": state.timestamp.isoformat(),
        "paper_order_id": pending["paper_order_id"],
        "requested_entry": pending["requested_entry"],
        "actual_entry": actual_entry,
        "actual_fill_rr": _actual_rr(direction, actual_entry, float(pending["stop"]), float(pending["target"])),
        "stop": pending["stop"],
        "target": pending["target"],
        "direction": direction,
        "fill_status": "FILLED",
        "broker_route": "PaperBroker",
        "normal_execution_affected": False,
        "execution_audit": {
            "entry_model": "stop_market_next_causal_bar",
            "adverse_slippage_ticks": pending["slippage_ticks"],
            "same_bar_ambiguity": "stop_first",
        },
    }
    lane_state["pending_order"] = None
    lane_state["position"] = position
    _save_state(log_dir, lane_state)
    _append_event(log_dir, fill_event)
    emitted.append(fill_event)
    # Same-bar stop/target resolution is owned by the caller's single
    # _resolve_position pass — resolving here as well double-counted the fill
    # bar (bars_held +1) and let the runner track arm off the fill bar's own
    # extreme (intra-bar look-ahead in the observational runner evidence).
    return emitted


def process_mes_trend_consolidation_break_evidence(
    *,
    state: MarketState,
    cfg,
    log_dir: str | Path,
    recent_bars: Optional[list[dict]] = None,
    decision=None,
    for_date: Optional[date] = None,
    flatness_snapshot: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    """Advance the MES consolidation-break lane and record current candidates."""
    if str(state.instrument or "").upper().replace("1!", "") != "MES":
        return []

    flatness = flatness_snapshot or load_flatness_snapshot(log_dir, for_date=for_date)
    lane_state = _load_state(log_dir)
    emitted: list[dict[str, Any]] = []
    emitted.extend(
        _advance_pending_order(
            state=state, lane_state=lane_state, log_dir=log_dir, cfg=cfg
        )
    )
    outcome = _resolve_position(state=state, lane_state=lane_state, log_dir=log_dir)
    if outcome:
        emitted.append(outcome)

    setup, confluence, rejection = detect_candidate(state, recent_bars or [])
    if setup is None and not confluence:
        return emitted
    key = _candidate_key(state, setup["direction"]) if setup else f"{LANE}|MES|{state.timestamp.isoformat()}|NONE"
    if key in set(lane_state.get("seen") or []):
        return emitted
    lane_state.setdefault("seen", []).append(key)
    lane_state["seen"] = lane_state["seen"][-MAX_SEEN_KEYS:]
    event, pending = _candidate_event(
        state=state,
        cfg=cfg,
        setup=setup,
        confluence=confluence,
        rejection=rejection,
        flatness=flatness,
        lane_state=lane_state,
        recent_bars=recent_bars or [],
        decision=decision,
    )
    if pending is not None:
        lane_state["pending_order"] = pending
    _save_state(log_dir, lane_state)
    _append_event(log_dir, event)
    emitted.append(event)
    return emitted
