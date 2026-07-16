"""
webhook/runner.py

Core pipeline for processing a single TradingView bar-close alert.

Flow per bar:
  1. Restore any open position from today's journal and try to resolve it
     with this bar's OHLC (target/stop check).
  2. Check daily limits (max trades, loss lockout).
  3. Run the DecisionEngine → RiskEngine chain.
  4. If approved: execute a bracket order via the configured broker and log it.
  5. Return a structured result dict.

Broker selection is intentionally paper-only for Hetzner deployment.
Position resolution uses PaperBroker simulated fills and never routes orders to
Tradovate (or paper simulation).
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from config.settings import SystemConfig, load_config
from execution.broker_interface import BracketOrder, BrokerInterface
from context.bar_history import BarHistory
from context.structural_regime import (
    classify_structural_regime,
    observe_structured_range_candidates,
)
from context.live_direction import apply_live_direction
from context.mnq_orb_reclaim_proof import (
    evaluate_mnq_orb_reclaim_proof,
    is_mnq_orb_reclaim_candidate,
    record_campaign_attempt,
)
from context.mnq_vwap_hold_proof import (
    evaluate_mnq_vwap_hold_proof,
    is_mnq_vwap_hold_candidate,
    record_campaign_attempt as record_vwap_hold_campaign_attempt,
)
from context.mnq_orb_breakout_proof import (
    evaluate_mnq_orb_breakout_proof,
    is_mnq_orb_breakout_candidate,
    record_campaign_attempt as record_orb_breakout_campaign_attempt,
)
from context.mnq_entry_refresh import (
    entry_refresh_instruments,
    entry_refresh_max_detachment_r,
    entry_refresh_mode,
    entry_refresh_strategies,
    is_entry_refresh_candidate,
    refresh_detached_entry,
)
from execution.entry_refresh_shadow import (
    append_entry_refresh_shadow_evidence,
    close_shadow_position,
    get_pending_shadow_position,
    open_shadow_position,
    resolve_shadow_position,
)
from context.mnq_vwap_hold_early import (
    detect_early_vwap_hold,
    is_vwap_hold_early_candidate,
    vwap_hold_early_mode,
)
from execution.vwap_hold_early_shadow import (
    append_vwap_hold_early_shadow_evidence,
    close_shadow_position as close_vwap_hold_early_shadow_position,
    get_pending_shadow_position as get_pending_vwap_hold_early_shadow_position,
    open_shadow_position as open_vwap_hold_early_shadow_position,
    resolve_shadow_position as resolve_vwap_hold_early_shadow_position,
)
from context.five_min_feed import (
    arm_fifteen_min_setup,
    clear_armed_setup,
    five_min_enabled,
    is_five_min,
    record_five_min,
    recent_five_min,
    triggered_armed_setup,
)
from execution.paper_broker import TICK_SIZE, NextBarOHLC, PaperBroker
from journal.journal_logger import JournalLogger
from risk.risk_engine import DailyState, RiskEngine, RiskResult, TradeSetup
from strategy.confluence_scorer import score_setup as _score_setup
from strategy.stop_sizing import apply_stop_multiplier
from strategy.shadow_resolver import resolve_pending_shadow_outcomes
from strategy.shadow_setups import evaluate_shadow_setups
from strategy.signal_engine import DecisionEngine
from webhook.payload import AlertPayload
from webhook.state_builder import build_market_state
from context.wall_context import build_wall_context as _build_wall_context
from context.range_signal import (
    build_range_state as _build_range_state,
    build_range_signal as _build_range_signal,
)

logger = logging.getLogger(__name__)

# ── Tick values ($ per tick, 0.25-point ticks) ────────────────────────────────
_TICK_VALUES: dict[str, float] = {
    "MES": 1.25,   # $5/pt × 0.25pt/tick
    "ES":  12.50,  # $50/pt
    "MNQ": 0.50,   # $2/pt × 0.25pt/tick
    "NQ":  5.00,   # $20/pt
    "MGC": 1.00,   # $10/troy oz × 0.10pt/tick
    "MCL": 1.00,
}

# Maximum price deviation from entry before a position is considered stale
# (as a fraction of entry price). Equity index futures trade in a narrow
# daily range — a 5% move vs entry means the wrong chart/instrument is feeding
# the webhook. Commodity futures get a slightly wider 10% threshold.
_STALE_PRICE_MISMATCH_THRESHOLD: dict[str, float] = {
    "MES": 0.05,
    "ES":  0.05,
    "MNQ": 0.05,
    "NQ":  0.05,
    "MGC": 0.10,
    "MCL": 0.10,
}

def _tick_value_for(instrument: str) -> float:
    root = (instrument or "").upper().rstrip("!1234567890HMUZ")
    return _TICK_VALUES.get(root, 1.25)


def _record_candidate_audit(
    decision,
    state,
    log_dir: str,
    for_date: date,
) -> list[str]:
    """Persist every formed strategy candidate, not only the selected one."""
    rows = list(getattr(decision, "candidate_audit", []) or [])
    if not rows or not any(row.get("direction_role") for row in rows):
        return []
    try:
        from adaptive.opportunity_tracker import (
            NO_SETUP,
            QUALITY_BLOCKED,
            OpportunityCandidate,
            OpportunityStore,
        )

        store = OpportunityStore(log_dir=str(Path(log_dir) / "opportunities"))
        candidate_ids: list[str] = []
        source_bar_id = state.timestamp.isoformat()
        for row in rows:
            candidate_id = OpportunityCandidate.make_id(
                state.instrument,
                source_bar_id,
                str(row.get("strategy") or "unknown"),
                str(row.get("direction") or "unknown"),
            )
            if row.get("selected"):
                candidate_ids.append(candidate_id)
            reject_code = row.get("reject_code")
            candidate = OpportunityCandidate(
                candidate_id=candidate_id,
                source_bar_id=source_bar_id,
                detected_at=state.timestamp.isoformat(),
                instrument=state.instrument,
                session=state.session,
                timeframe=str(getattr(state.ohlc, "timeframe", "15")),
                strategy=str(row.get("strategy") or "unknown"),
                direction=str(row.get("direction") or ""),
                entry=float(row.get("entry")),
                stop=float(row.get("stop")),
                target=float(row.get("target")),
                failed_gates=[reject_code] if reject_code else [],
                market_condition=decision.market_condition,
                block_type=QUALITY_BLOCKED if reject_code else NO_SETUP,
                snapshots={"decision": decision.decision},
                expires_at=(state.timestamp + timedelta(hours=8)).isoformat(),
                direction_role=row.get("direction_role"),
                htf_primary_direction=row.get("htf_primary_direction"),
                daily_direction=row.get("daily_direction"),
                four_hour_direction=row.get("four_hour_direction"),
                direction_reason=row.get("direction_reason"),
                selected=bool(row.get("selected")),
                attempted=bool(row.get("attempted")),
                fallback_attempt=bool(row.get("fallback_attempt")),
                reject_code=reject_code,
                reject_reason=row.get("reject_reason"),
            )
            store.record_candidate(candidate, for_date=for_date)
        return candidate_ids
    except Exception:
        logger.warning("opportunity candidate audit write failed", exc_info=True)
        return []


def _record_candidate_lifecycle(
    candidate_ids: list[str],
    log_dir: str,
    for_date: date,
    stage: str,
    **fields,
) -> None:
    if not candidate_ids:
        return
    try:
        from adaptive.opportunity_tracker import OpportunityStore

        store = OpportunityStore(log_dir=str(Path(log_dir) / "opportunities"))
        for candidate_id in candidate_ids:
            store.record_lifecycle(
                candidate_id, stage, for_date=for_date, **fields
            )
    except Exception:
        logger.warning("opportunity lifecycle write failed", exc_info=True)


def _resolve_pending_opportunities(
    state,
    log_dir: str,
    for_date: date,
) -> None:
    """Resolve earlier same-day candidates from causal future bars."""
    try:
        from adaptive.opportunity_tracker import (
            OpportunityCandidate,
            OpportunityStore,
            resolve_outcome,
        )

        store = OpportunityStore(log_dir=str(Path(log_dir) / "opportunities"))
        rows = store.read_day(for_date)
        resolved_ids = {
            row.get("candidate_id")
            for row in rows
            if row.get("_type") == "outcome"
        }
        bars = BarHistory(log_dir=log_dir).recent(
            state.instrument, 500, for_date=for_date
        )
        for row in rows:
            if row.get("_type") != "candidate":
                continue
            if row.get("instrument") != state.instrument:
                continue
            if row.get("candidate_id") in resolved_ids:
                continue
            candidate = OpportunityCandidate.from_dict(row)
            future = [
                bar
                for bar in bars
                if str(bar.get("ts") or "") > candidate.detected_at
            ]
            if not future:
                continue
            outcome = resolve_outcome(candidate, future)
            expired = False
            if candidate.expires_at:
                expires = datetime.fromisoformat(
                    candidate.expires_at.replace("Z", "+00:00")
                )
                expired = state.timestamp >= expires
            if outcome.result in {"TARGET_HIT", "STOP_HIT"} or expired:
                store.record_outcome(outcome, for_date=for_date)
    except Exception:
        logger.warning("opportunity resolution failed", exc_info=True)


def _paper_broker(starting_balance: float, cfg: Optional[SystemConfig]) -> PaperBroker:
    """PaperBroker wired with the configured fill-realism settings."""
    return PaperBroker(
        starting_balance=starting_balance,
        slippage_ticks=float(getattr(cfg, "fill_slippage_ticks", 0.0) or 0.0),
        pessimistic_both_hit=bool(getattr(cfg, "fill_pessimistic_both_hit", False)),
        runner_mode=bool(getattr(cfg, "runner_mode", False)),
        runner_activation_r=float(getattr(cfg, "runner_activation_r", 1.0) or 1.0),
        runner_trail_r=float(getattr(cfg, "runner_trail_r", 0.5) or 0.5),
    )


def _make_broker(
    starting_balance: float = 1500.0, cfg: Optional[SystemConfig] = None
) -> BrokerInterface:
    """Return the configured broker.

    BROKER env var controls selection:
      - "tradovate" → TradovateBroker (demo/live per TRADOVATE_ENV)
      - anything else (default) → PaperBroker (local simulation)

    LIVE_TRADING_ENABLED must never be set to true — the LiveTradingBlockedError
    guard in RiskEngine will raise at startup if it is.
    """
    broker_type = os.getenv("BROKER", "paper").strip().lower()
    if broker_type == "tradovate":
        from execution.tradovate_broker import TradovateBroker, TradovateConfig
        config = TradovateConfig.from_env()
        logger.info("Using TradovateBroker (env=%s)", config.env)
        return TradovateBroker(config=config)
    return _paper_broker(starting_balance, cfg)


# Tick size per instrument root — used to align entry/stop/target to valid broker
# prices. A non-tick price (e.g. 30342.1613) is rejected or silently re-rounded by
# Tradovate, which also breaks exit reconciliation against the bracket prices.
_TICK_SIZE_BY_ROOT = {"MES": 0.25, "ES": 0.25, "MNQ": 0.25, "NQ": 0.25, "MGC": 0.1, "MCL": 0.01}


def _round_to_tick(price: Optional[float], instrument: str) -> Optional[float]:
    if price is None:
        return None
    root = (instrument or "").upper().rstrip("!1234567890HMUZ")
    tick = _TICK_SIZE_BY_ROOT.get(root, 0.25)
    return round(round(float(price) / tick) * tick, 4)


def _candidate_snapshot(
    *,
    setup,
    instrument: str,
    session: Optional[str],
    timeframe,
    reject_code: Optional[str],
    reject_reason: Optional[str],
    blocking_gate: Optional[str],
    contracts: Optional[int] = None,
    entry: Optional[float] = None,
    stop: Optional[float] = None,
    target: Optional[float] = None,
    event_id: Optional[str] = None,
) -> dict:
    """Return an audit-only snapshot of a rejected would-be trade.

    The snapshot is derived only after a decision or risk gate has rejected the
    setup. It is never queued, retried, or routed to a broker.
    """
    candidate_entry = entry if entry is not None else getattr(setup, "entry", None)
    candidate_stop = stop if stop is not None else getattr(setup, "stop", None)
    candidate_target = target if target is not None else getattr(setup, "target", None)
    snapshot = {
        "symbol": instrument,
        "direction": getattr(setup, "direction", None),
        "strategy": getattr(setup, "strategy", None),
        "entry": candidate_entry,
        "stop": candidate_stop,
        "target": candidate_target,
        "contracts": contracts,
        "timeframe": timeframe,
        "session": session,
        "rr": getattr(setup, "rr_ratio", None),
        "reject_code": reject_code,
        "reject_reason": reject_reason,
        "blocking_gate": blocking_gate,
        "no_trade_taken": True,
    }
    snapshot["missing_fields"] = [
        key
        for key in ("symbol", "direction", "strategy", "entry", "stop", "target", "session")
        if snapshot.get(key) in (None, "")
    ]
    if event_id:
        snapshot["event_id"] = event_id
    for key in (
        "direction_role",
        "htf_primary_direction",
        "daily_direction",
        "four_hour_direction",
        "direction_reason",
    ):
        value = getattr(setup, key, None)
        if value is not None:
            snapshot[key] = value
    return snapshot


def process_alert(
    payload: AlertPayload,
    config: Optional[SystemConfig] = None,
    log_dir: str = "logs",
    for_date: Optional[date] = None,
) -> dict:
    """
    Run one bar-close alert through the paper-trading pipeline.

    Args:
        payload:  Validated AlertPayload from the webhook endpoint.
        config:   SystemConfig (loaded from risk_rules.yaml if not provided).
        log_dir:  Journal directory.
        for_date: Override date for testing; defaults to date.today().

    Returns:
        A dict with keys:
            timestamp, instrument, session,
            resolution  (WIN | LOSS | None),
            decision    (TRADE | NO_TRADE | BLOCKED_* | RISK_REJECTED),
            risk        ({result, failed_rule, reason} | None),
            fill        ({status, instrument, direction, entry, stop, target,
                          strategy} | None),
    """
    cfg = config or load_config()
    five_min_trigger = None
    five_min_trigger_payload = None

    # In paper mode we SIMULATE entry + resolution locally via PaperBroker
    # (next-bar OHLC), regardless of the BROKER env var. BROKER=tradovate is kept
    # for live price quotes and eventual live trading, but routing automated paper
    # fills through the Tradovate demo broker means resolve_position() has no
    # surviving order IDs across webhook calls and never closes the position —
    # the trade stays open forever. paper_mode → simulate is the correct,
    # self-contained behavior (and what the $1,500 paper balance + fill model
    # were built for). Default True so a missing flag fails safe (never live).
    simulate = bool(getattr(cfg, "paper_mode", True))

    # ── Step 0: Data-quality gate ─────────────────────────────────────────────
    quality_error = _check_payload_quality(payload, cfg)
    if quality_error:
        today = for_date or date.today()
        journal = JournalLogger(log_dir=log_dir)
        journal.log_decision(
            {
                "ts": _safe_bar_ts(payload),
                "instrument": payload.ticker,
                "session": payload.session,
                "decision": "BLOCKED_DATA_QUALITY",
                "reason": quality_error,
                "market_condition": payload.market_condition,
                "setup": None,
                "failed_gates": [quality_error],
                "received_timeframe": payload.timeframe,
            },
            None,
            for_date=today,
        )
        return {
            "timestamp": payload.timestamp,
            "instrument": payload.ticker,
            "session": None,
            "resolution": None,
            "decision": "BLOCKED_DATA_QUALITY",
            "risk": None,
            "fill": None,
            "context": None,
            "regime": None,
            "gex_status": None,
            "signa_status": None,
            "failed_gates": [quality_error],
            "confidence_score": None,
        }

    # ── Step 0a: 5-minute entry feed ──────────────────────────────────────────
    # When FIVE_MIN_FEED_ENABLED, a 5M alert is NOT a misconfigured 15M bar — it
    # is entry-timing context. Store it on its own lane. It may trigger only the
    # exact original bracket armed by an authoritative 15M decision; it never
    # evaluates strategy from 5M data. Default OFF → 5M falls through to the
    # timeframe guard below exactly as before.
    if five_min_enabled() and is_five_min(payload.timeframe):
        five_min_trigger_payload = payload
        try:
            record_five_min(payload, log_dir, for_date=for_date)
            five_min_trigger = triggered_armed_setup(payload, log_dir, for_date)
        except Exception as _exc:  # ingestion must never break alert handling
            logger.warning("5m feed: record skipped: %s", _exc)
            five_min_trigger = None
        if five_min_trigger:
            try:
                payload = AlertPayload(**five_min_trigger["payload"])
            except Exception as _exc:
                logger.warning("5m feed: invalid armed 15m payload: %s", _exc)
                five_min_trigger = None
        # ── vwap_hold early-signal shadow lane (upstream-timing fix) ──────────
        # Detection: does THIS SAME 5-minute alert, run through the real
        # decision pipeline in isolation (context.mnq_vwap_hold_early), produce
        # a qualifying vwap_hold TRADE 10-14 minutes before the corresponding
        # 15-minute bar would close? Independent of the retest mechanism above
        # — runs regardless of whether five_min_trigger fired. No risk engine,
        # no broker, never touches `result`/`five_min_trigger`/the main
        # decision or journal. Fail-soft: never breaks the 5-minute lane.
        if is_vwap_hold_early_candidate(
            five_min_trigger_payload.ticker, five_min_trigger_payload.timeframe, cfg
        ):
            try:
                _vhe_today = for_date or date.today()
                _vhe_state = build_market_state(five_min_trigger_payload)
                _vhe_daily = JournalLogger(log_dir=log_dir).get_daily_state(_vhe_today)
                vwap_hold_early_audit = detect_early_vwap_hold(_vhe_state, _vhe_daily, cfg)
                if vwap_hold_early_audit is not None:
                    append_vwap_hold_early_shadow_evidence(
                        log_dir,
                        {
                            "kind": "detection",
                            "mode": vwap_hold_early_mode(cfg),
                            "ts": _vhe_state.timestamp.isoformat(),
                            **vwap_hold_early_audit,
                        },
                    )
                    if (
                        vwap_hold_early_audit.get("signal_detected")
                        and vwap_hold_early_mode(cfg) == "shadow"
                    ):
                        open_vwap_hold_early_shadow_position(
                            log_dir,
                            direction=vwap_hold_early_audit["direction"],
                            entry=vwap_hold_early_audit["entry"],
                            stop=vwap_hold_early_audit["stop"],
                            target=vwap_hold_early_audit["target"],
                            entry_ts=_vhe_state.timestamp.isoformat(),
                            rr_ratio=vwap_hold_early_audit.get("rr_ratio"),
                        )
            except Exception:
                logger.debug("vwap_hold early-signal detection skipped", exc_info=True)

        # Resolution: walk any pending shadow position forward against every
        # 5-minute bar since its entry. Independent of detection above — runs
        # on every 5-minute bar so a position opened earlier keeps resolving.
        if vwap_hold_early_mode(cfg) == "shadow":
            try:
                _vhe_position = get_pending_vwap_hold_early_shadow_position(log_dir)
                if _vhe_position is not None:
                    _vhe_entry_ts = str(_vhe_position.get("entry_ts") or "")
                    _vhe_bars = [
                        b for b in recent_five_min("MNQ", log_dir, 500, for_date=for_date)
                        if str(b.get("ts") or "") > _vhe_entry_ts
                    ]
                    _vhe_outcome = resolve_vwap_hold_early_shadow_position(_vhe_position, _vhe_bars)
                    if _vhe_outcome is not None:
                        append_vwap_hold_early_shadow_evidence(
                            log_dir,
                            {"kind": "resolution", "position": _vhe_position, **_vhe_outcome},
                        )
                        close_vwap_hold_early_shadow_position(log_dir)
            except Exception:
                logger.debug("vwap_hold early-signal resolution skipped", exc_info=True)

        if not five_min_trigger:
            return {
                "timestamp": five_min_trigger_payload.timestamp,
                "instrument": five_min_trigger_payload.ticker,
                "session": five_min_trigger_payload.session,
                "resolution": None,
                "decision": "FIVE_MIN_CONTEXT",
                "risk": None,
                "fill": None,
                "context": None,
                "regime": None,
                "gex_status": None,
                "signa_status": None,
                "failed_gates": [],
                "confidence_score": None,
            }

    # ── Step 0b: Timeframe guard (CONFIG_BLOCKED / TIMEFRAME_MISMATCH) ─────────
    # The strategy is tuned and replay-validated on 15m. A webhook arriving on
    # any other timeframe (e.g. 5m) is a MISCONFIGURED ALERT, not a tradeable
    # bar — its trend reads SIDEWAYS/WEAK and every setup gets silently filtered.
    # Reject it as a config error and journal it under a distinct category so it
    # is NEVER counted or evaluated as a normal NO_TRADE. The dashboard reads
    # these entries to raise the "LIVE ALERT MISCONFIGURED" banner.
    tf_mismatch = _check_timeframe(payload, cfg)
    if tf_mismatch:
        today = for_date or date.today()
        journal = JournalLogger(log_dir=log_dir)
        journal.log_decision(
            {
                "ts": _safe_bar_ts(payload),
                "instrument": payload.ticker,
                "session": payload.session,
                "decision": "CONFIG_BLOCKED",
                "config_block": "TIMEFRAME_MISMATCH",
                "reason": tf_mismatch["reason"],
                "expected_timeframe": tf_mismatch["expected"],
                "received_timeframe": tf_mismatch["received"],
            },
            None,
            for_date=today,
        )
        return {
            "timestamp": payload.timestamp,
            "instrument": payload.ticker,
            "session": payload.session,
            "resolution": None,
            "decision": "CONFIG_BLOCKED",
            "config_block": "TIMEFRAME_MISMATCH",
            "expected_timeframe": tf_mismatch["expected"],
            "received_timeframe": tf_mismatch["received"],
            "risk": None,
            "fill": None,
            "context": None,
            "regime": None,
            "gex_status": None,
            "signa_status": None,
            "failed_gates": [tf_mismatch["reason"]],
            "confidence_score": None,
        }

    _maybe_enrich_payload_with_signa(payload, cfg)
    state = build_market_state(payload)
    if five_min_trigger and five_min_trigger_payload:
        # Keep strategy authority/context from the validated 15M payload, but
        # execute and journal against the CURRENT 5M bar's timestamp and prices.
        # Otherwise risk/confluence would see the stale 15M close while an order
        # is being sent from a later 5M retest.
        _trigger_state = build_market_state(five_min_trigger_payload)
        state.timestamp = _trigger_state.timestamp
        state.session = _trigger_state.session
        state.ohlc = _trigger_state.ohlc

    # ── Rolling bar history (Phase 3): continuous price record + gap detection ──
    # Record EVERY ingested bar (traded or not) so regime can be judged over a
    # window and ingestion gaps are visible. Fail-soft: a history hiccup must
    # never affect ingestion, the decision, or risk.
    bar_gap = None
    recent_bars: list[dict] = []
    structural_bars: list[dict] = []
    if not five_min_trigger:
        try:
            bar_hist = BarHistory(log_dir=log_dir)
            tf_min = _bar_timeframe_minutes(payload, cfg)
            # Reference date for gap/window reads. Defaults to today (live behavior);
            # an explicit for_date (replay/tests) anchors reads to the bar's own day so
            # they don't depend on wall-clock now.
            bar_gap = bar_hist.detect_gap(
                state.instrument, payload.timestamp, tf_min, for_date=for_date
            )
            bar_hist.record(
                state.instrument,
                ts=payload.timestamp,
                open=state.ohlc.open,
                high=state.ohlc.high,
                low=state.ohlc.low,
                close=state.ohlc.close,
                volume=state.volume.current_bar if state.volume else None,
                timeframe=state.ohlc.timeframe,
            )
            # Window regime: include this just-recorded bar in the lookback.
            state.window_direction = BarHistory.window_direction(
                bar_hist.recent(state.instrument, 6, for_date=for_date)
            )
            recent_bars = bar_hist.recent(state.instrument, 8, for_date=for_date)
            structural_bars = bar_hist.recent(state.instrument, 64, for_date=for_date)
            if getattr(cfg, "htf_direction_mode", "off") == "prioritize":
                _resolve_pending_opportunities(
                    state, log_dir, for_date or date.today()
                )
        except Exception:  # noqa: BLE001 — fail-soft, never break ingestion
            logger.warning("bar history update failed", exc_info=True)

    # Shared MES/MNQ structural regime — observation only.  The result is
    # additive journal evidence and has no authority over decision/risk/routing.
    try:
        if state.instrument in {"MES", "MNQ"}:
            structural = classify_structural_regime(
                structural_bars, instrument=state.instrument
            )
            state.structural_regime = structural.to_dict(
                current_market_condition=state.market_condition
            )
            state.structural_range_candidates = observe_structured_range_candidates(
                structural, structural_bars, instrument=state.instrument
            )
    except Exception:  # noqa: BLE001 — evidence must never affect ingestion
        logger.warning("structural regime observation failed", exc_info=True)
        state.structural_regime = None
        state.structural_range_candidates = []

    # ── Live HTF direction (opt-in): compute daily/4H from price, not labels ──
    # The payload's higher-TF labels come from completed bars and lag turns
    # (2026-07-02: UP labels through a full-afternoon FULL_SHORT selloff kept
    # gate_direction LONG on every bar). With htf_direction_source=live the two
    # direction fields are always live-computed; on any failure they are
    # cleared to None (source unavailable) — never left holding a stale payload
    # label the operator believes was replaced. Runs for the 5m-trigger path
    # too so execution bars are judged with the same direction source.
    if getattr(cfg, "htf_direction_source", "payload") == "live":
        try:
            _dir_bars = BarHistory(log_dir=log_dir).recent(
                state.instrument, 40, for_date=for_date
            )
            apply_live_direction(state, _dir_bars)
        except Exception:  # noqa: BLE001 — ingestion survives; direction fails closed
            logger.warning("live direction computation failed", exc_info=True)
            if state.htf is not None:
                state.htf.daily_direction = None
                state.htf.four_hour_direction = None
                state.htf.direction_source = "live"

    # ── Range observation (journal-only, no effect on decisions) ──────────────
    # Wall context + range state/signal, mirroring the GEX observe pattern: we
    # measure whether range structure predicts our outcomes BEFORE letting it
    # gate anything. Disabled by default (range_observe_enabled); fail-soft —
    # a build hiccup must never affect ingestion, the decision, or risk.
    _wall_ctx_dict: dict = {}
    _range_state_dict: dict = {}
    _range_signal_dict: dict = {}
    if getattr(cfg, "range_observe_enabled", False):
        try:
            _wall_ctx = _build_wall_context(
                state, zone_state=getattr(payload, "zone_state", None)
            )
            _wall_ctx_dict = _wall_ctx.to_dict()
            _orb_status = str(getattr(getattr(state, "orb", None), "status", None) or "")
            _range_state = _build_range_state(
                _wall_ctx, state.market_condition or "", orb_status=_orb_status
            )
            _range_state_dict = _range_state.to_dict()
            _range_signal = _build_range_signal(_range_state, _wall_ctx)
            _range_signal_dict = _range_signal.to_dict()
        except Exception:  # noqa: BLE001 — fail-soft, never break ingestion
            logger.debug("wall_context/range_signal build failed", exc_info=True)

    journal = JournalLogger(log_dir=log_dir)
    today = for_date or date.today()
    daily_state = journal.get_daily_state(today)
    open_position_date = today
    open_pos = journal.get_open_position(today) if daily_state.has_open_position else None
    if open_pos is None:
        # Walk back up to 7 calendar days so Friday→Monday carry works across weekends.
        for days_back in range(1, 8):
            candidate = today - timedelta(days=days_back)
            candidate_state = journal.get_daily_state(candidate)
            if candidate_state.has_open_position:
                open_pos = journal.get_open_position(candidate)
                open_position_date = candidate
                daily_state.has_open_position = True
                break

    result: dict = {
        "timestamp": (
            five_min_trigger_payload.timestamp if five_min_trigger_payload else payload.timestamp
        ),
        "instrument": state.instrument,
        "session": state.session,
        "resolution": None,
        "decision": None,
        "risk": None,
        "fill": None,
        "context": _market_state_context(state),
        "regime": None,
        "gex_status": None,
        "signa_status": None,
        "failed_gates": [],
        "confidence_score": None,
        "bar_gap": bar_gap,
        "window_direction": state.window_direction,
    }

    # Shadow setups: audit-only observation of fade/reclaim/range opportunities
    # the live strategy does NOT trade. Read-only — never places an order; just
    # surfaced on `result` + journal for offline study. Fail-soft.
    try:
        shadow_candidates = [
            c.to_dict() for c in evaluate_shadow_setups(state, recent_bars)
        ]
    except Exception:
        shadow_candidates = []
    if shadow_candidates:
        result["shadow_candidates"] = shadow_candidates

    bar_ts = state.timestamp.isoformat()
    if not journal.claim_bar(instrument=state.instrument, bar_ts=bar_ts, for_date=today):
        result["decision"] = "BLOCKED_DUPLICATE_BAR"
        result["failed_gates"] = [f"Duplicate bar already processed: {state.instrument} {bar_ts}"]
        return result

    # Shadow candidate resolution: causally resolve PRIOR bars' journaled
    # observe-only candidates (shadow_setups + range_signal lanes) against the
    # bars ingested since, appending SHADOW_OUTCOME evidence rows. Runs AFTER
    # claim_bar (its rows must never precede a bar claim) and only on the 15M
    # ingestion path. Read-only for trading; fail-soft — a resolver hiccup must
    # never affect the decision, risk, or execution.
    if not five_min_trigger and getattr(cfg, "shadow_resolver_enabled", True):
        try:
            _resolved_shadow = resolve_pending_shadow_outcomes(
                log_dir=log_dir,
                instrument=state.instrument,
                current_bar_ts=bar_ts,
                for_date=for_date,
            )
            if _resolved_shadow:
                result["shadow_outcomes_resolved"] = len(_resolved_shadow)
        except Exception:  # noqa: BLE001 — evidence lane must never break ingestion
            logger.warning("shadow outcome resolution failed", exc_info=True)

    # ── Entry-refresh shadow resolution (Phase 1, PR #265) ────────────────────
    # Resolve any pending hypothetical REFRESHED position for THIS instrument
    # against bars ingested since it opened, using the same runner-exit math
    # real positions use. Independent of today's decision — a shadow position
    # opened on an earlier NO_TRADE bar is checked here on every later bar
    # until it resolves. Sends no order, calls no risk engine, calls no
    # broker. Fail-soft: a resolution hiccup must never affect trading.
    if entry_refresh_mode(cfg) == "shadow":
        _er_root = (state.instrument or "").upper().replace("1!", "")
        if _er_root in entry_refresh_instruments(cfg):
            for _er_strategy in entry_refresh_strategies(cfg):
                try:
                    _er_pos = get_pending_shadow_position(log_dir, _er_root, _er_strategy)
                    if _er_pos is None:
                        continue
                    _er_bars = BarHistory(log_dir=log_dir).recent(_er_root, 200, for_date=today)
                    _er_entry_ts = str(_er_pos.get("entry_ts") or "")
                    # STRICTLY after the entry bar — the hypothetical entry is
                    # priced at that bar's own close, so checking that same
                    # bar's low/high for a stop/target hit is invalid
                    # look-ahead (it "sees" price action from before the
                    # entry existed).
                    _er_since = (
                        [b for b in _er_bars if str(b.get("ts", "")) > _er_entry_ts]
                        if _er_entry_ts else _er_bars
                    )
                    _er_outcome = resolve_shadow_position(
                        _er_pos, _er_since,
                        activation_r=float(os.getenv("RUNNER_ACTIVATION_R", "1.0") or 1.0),
                        trail_r=float(os.getenv("RUNNER_TRAIL_R", "0.5") or 0.5),
                    )
                    if _er_outcome is not None:
                        append_entry_refresh_shadow_evidence(log_dir, {
                            "instrument": _er_root,
                            "strategy": _er_strategy,
                            "direction": _er_pos.get("direction"),
                            "original_entry": _er_pos.get("entry"),
                            "original_stop": _er_pos.get("stop"),
                            "original_target": _er_pos.get("target"),
                            "refresh_policy": _er_pos.get("refresh_policy"),
                            "detachment_ticks": _er_pos.get("detachment_ticks"),
                            "detachment_r": _er_pos.get("detachment_r"),
                            "entry_ts": _er_pos.get("entry_ts"),
                            "opened_at": _er_pos.get("opened_at"),
                            **_er_outcome,
                        })
                        close_shadow_position(log_dir, _er_root, _er_strategy)
                except Exception:  # noqa: BLE001 — shadow lane must never break ingestion
                    logger.debug("entry-refresh shadow resolution skipped", exc_info=True)

    # Companion options paper lane: refresh marks on any OPEN paper option rows so
    # each incoming bar advances them toward WIN/LOSS/EXPIRED. Independent of whether
    # THIS alert produces a futures fill. Fail-soft; never touches futures state.
    _maybe_resolve_companions(cfg, state)

    # ── Step 1: Resolve any open position ────────────────────────────────────
    # Paper mode: simulate using next-bar OHLC.
    # Tradovate mode: query actual fills from the broker — the bracket child
    # orders (stop and target) are already live on Tradovate's side.
    if daily_state.has_open_position:
        if open_pos and _position_is_complete(open_pos):
            broker_type = os.getenv("BROKER", "paper").strip().lower()
            _open_pos_strategy = open_pos.get("strategy") or (open_pos.get("setup") or {}).get("strategy")
            _reclaim_proof_audit = open_pos.get("mnq_orb_reclaim_proof_audit")
            _breakout_proof_audit = open_pos.get("mnq_orb_breakout_proof_audit")
            _vwap_hold_proof_audit = open_pos.get("mnq_vwap_hold_proof_audit")
            _proof_paper_position = bool(
                (
                    isinstance(_reclaim_proof_audit, dict)
                    and _reclaim_proof_audit.get("proof_mode") == "paper_sim"
                    and _reclaim_proof_audit.get("force_paper_broker") is True
                    and is_mnq_orb_reclaim_candidate(open_pos.get("instrument"), _open_pos_strategy)
                )
                or (
                    isinstance(_breakout_proof_audit, dict)
                    and _breakout_proof_audit.get("proof_mode") == "paper_sim"
                    and _breakout_proof_audit.get("force_paper_broker") is True
                    and is_mnq_orb_breakout_candidate(open_pos.get("instrument"), _open_pos_strategy)
                )
                or (
                    isinstance(_vwap_hold_proof_audit, dict)
                    and _vwap_hold_proof_audit.get("proof_mode") == "paper_sim"
                    and _vwap_hold_proof_audit.get("force_paper_broker") is True
                    and is_mnq_vwap_hold_candidate(open_pos.get("instrument"), _open_pos_strategy)
                )
            )
            # A proof paper position remains paper-owned for its entire
            # lifecycle. Global BROKER=tradovate must never pull it into the
            # Tradovate resolver on a later bar.
            _using_tradovate_position = (
                not simulate and broker_type == "tradovate" and not _proof_paper_position
            )
            # Only resolve a position against bars of its OWN instrument. An MNQ
            # position must never be resolved against a MES bar's OHLC (different
            # price scale → false no-hit, and the price-mismatch safety net would
            # then force-close at the wrong instrument's price). Each instrument's
            # own next bar resolves its own position.
            _open_root = (open_pos.get("instrument") or "").upper().rstrip("!1234567890HMUZ")
            _bar_root = (state.instrument or "").upper().rstrip("!1234567890HMUZ")
            same_instrument = bool(_open_root) and _open_root == _bar_root
            if _using_tradovate_position:
                from execution.tradovate_broker import TradovateBroker, TradovateConfig
                from execution.broker_interface import Position as _Position
                tv = TradovateBroker(config=TradovateConfig.from_env())
                tv._last_position = _Position(
                    instrument=open_pos["instrument"] or state.instrument,
                    direction=open_pos["direction"],
                    entry_price=float(open_pos["entry"]),
                    stop=float(open_pos["stop"]),
                    target=(
                        None
                        if open_pos.get("exit_mode") == "runner_live"
                        else float(open_pos["target"])
                    ),
                    quantity=int(open_pos.get("contracts", 1)),
                    open=True,
                )
                # Restore the OSO order ids (if persisted) so exit attribution uses
                # order-id matching instead of degrading to price-matching after a
                # restart. Only restore a dict — absent or a corrupt/typed payload
                # → None → resolve_position falls back to price-matching safely
                # (never stalls on a non-dict ids.get()).
                _restored_ids = open_pos.get("order_ids")
                tv._last_order_ids = _restored_ids if isinstance(_restored_ids, dict) else None
                fill = tv.resolve_position()
            elif same_instrument:
                # Paper simulation: resolve against THIS bar's OHLC.
                _paper_balance = journal.get_account_balance(
                    cfg.position_sizing.starting_balance, today
                )
                if _proof_paper_position:
                    broker = PaperBroker(
                        starting_balance=_paper_balance,
                        slippage_ticks=float(getattr(cfg, "fill_slippage_ticks", 0.0) or 0.0),
                        pessimistic_both_hit=bool(getattr(cfg, "fill_pessimistic_both_hit", False)),
                        runner_mode=True,
                        runner_activation_r=float(getattr(cfg, "runner_activation_r", 1.0) or 1.0),
                        runner_trail_r=float(getattr(cfg, "runner_trail_r", 0.5) or 0.5),
                        entry_fill_model="market",
                    )
                else:
                    broker = _paper_broker(_paper_balance, cfg)
                broker.restore_position(
                    instrument=open_pos["instrument"] or state.instrument,
                    direction=open_pos["direction"],
                    entry=float(open_pos["entry"]),
                    stop=float(open_pos["stop"]),
                    target=float(open_pos["target"]),
                    contracts=int(open_pos.get("contracts", 1)),
                )
                broker._active_order_id = open_pos.get("paper_order_id")
                # Active runner: the broker is rebuilt every bar, so its
                # _runner_max_fav resets to entry each call and the trail can never
                # accumulate (the runner is inert without this). Reconstruct the
                # favourable extreme from bars-since-entry (PRIOR bars only — drop
                # the current bar, no intra-bar look-ahead) and seed it so
                # _resolve_runner trails correctly across the trade's life.
                if bool(getattr(cfg, "runner_mode", False)):
                    try:
                        _r_inst = open_pos.get("instrument") or state.instrument
                        _r_bars = BarHistory(log_dir=log_dir).recent(_r_inst, 200, for_date=today)
                        _r_ets = str(open_pos.get("bar_ts") or open_pos.get("ts") or "")
                        _r_since = [b for b in _r_bars if str(b.get("ts", "")) >= _r_ets] if _r_ets else _r_bars
                        _r_prior = _r_since[:-1]  # exclude the current (latest) bar
                        if _r_prior:
                            if open_pos["direction"] == "LONG":
                                broker._runner_max_fav = max(float(b["high"]) for b in _r_prior)
                            else:
                                broker._runner_max_fav = min(float(b["low"]) for b in _r_prior)
                    except Exception as _exc:  # never break resolution
                        logger.debug("runner max-fav reconstruct skipped: %s", _exc)
                fill = broker.resolve_position(
                    NextBarOHLC(high=payload.high, low=payload.low)
                )
            else:
                # Different-instrument bar (e.g. MES bar while an MNQ position is
                # open) — leave the position untouched for its own next bar.
                fill = None

            # ── Trailing-stop SHADOW (log-only — sends NOTHING) ───────────────
            # Increment 2 of the live-trailing build: observe where a runner trail
            # WOULD move the stop this bar, using the SAME math as the sim. Flag-
            # gated (RUNNER_SHADOW_ENABLED) and fail-soft — inert by default and
            # never affects resolution, orders, or state.
            _explicit_exit_mode = os.getenv("EXIT_MODE")
            _shadow_exit_active = (
                getattr(cfg, "exit_mode", "static") == "runner_shadow"
                if _explicit_exit_mode is not None
                else os.getenv("RUNNER_SHADOW_ENABLED", "").strip().lower()
                in ("1", "true", "yes")
            )
            if same_instrument and _shadow_exit_active:
                try:
                    from execution.trail_shadow import shadow_trail, format_shadow_log
                    _inst = open_pos.get("instrument") or state.instrument
                    _bars = BarHistory(log_dir=log_dir).recent(_inst, 60, for_date=today)
                    _entry_ts = str(open_pos.get("bar_ts") or open_pos.get("ts") or "")
                    _since = [b for b in _bars if str(b.get("ts", "")) >= _entry_ts] if _entry_ts else _bars
                    _shadow = shadow_trail(
                        open_pos, _since,
                        activation_r=float(os.getenv("RUNNER_ACTIVATION_R", "1.0") or 1.0),
                        trail_r=float(os.getenv("RUNNER_TRAIL_R", "0.5") or 0.5),
                    )
                    if _shadow:
                        logger.info(format_shadow_log(_shadow, _inst))
                        try:
                            from ops.runner_shadow_evidence import append_runner_shadow_evidence
                            # Fill-fiction guard (2026-07-02 incident): a journal-
                            # open position whose IOC entry NEVER filled at the
                            # broker still produces shadow math here — armed
                            # evidence for a trade that didn't exist (same
                            # artifact class PR #150 exposed in replay). Gate the
                            # evidence row on the broker-confirmed entry fill:
                            # definitive no-fill → suppress (the reconciler will
                            # phantom-clear the position); unreadable → keep the
                            # row tagged fill_confirmed=null so review filters it.
                            if _using_tradovate_position:
                                _oids = open_pos.get("order_ids")
                                _entry_oid = (
                                    _oids.get("entry") if isinstance(_oids, dict) else None
                                )
                                _fill_confirmed = tv.entry_order_filled(_entry_oid)
                            else:
                                # Paper sim: the entry fills by construction.
                                _fill_confirmed = True
                            if _fill_confirmed is False:
                                logger.info(
                                    "[trail-shadow] %s evidence suppressed — entry "
                                    "order never filled at broker (fill fiction)",
                                    _inst,
                                )
                            else:
                                _setup = open_pos.get("strategy")
                                append_runner_shadow_evidence(
                                    log_dir,
                                    instrument=_inst,
                                    setup=str(_setup) if _setup else None,
                                    bar_ts=getattr(payload, "timestamp", None),
                                    result=_shadow,
                                    fill_confirmed=_fill_confirmed,
                                )
                        except Exception as _evidence_exc:
                            logger.debug("runner shadow evidence write skipped: %s", _evidence_exc)
                except Exception as _exc:  # shadow must never affect trading
                    logger.debug("trail-shadow skipped: %s", _exc)

            # ── Trailing-stop LIVE (active stop-replace) ──────────────────────
            # Increment 3: actually MOVE the resting Tradovate stop where the
            # shadow trail (same math) says it should go. Flag-gated
            # (RUNNER_LIVE_ENABLED, default OFF), Tradovate path only, and only
            # when this bar did NOT already resolve the position (fill is None).
            # replace_stop is atomic + never-loosen + fail-safe, and this whole
            # block is fail-soft, so it can never break trading or leave a naked
            # position even if it errors.
            if (
                _using_tradovate_position
                and same_instrument
                and fill is None
                and open_pos.get("direction_role") != "COUNTERTREND_SCALP"
                and (
                    getattr(cfg, "exit_mode", "static") == "runner_live"
                    if _explicit_exit_mode is not None
                    else os.getenv("RUNNER_LIVE_ENABLED", "").strip().lower()
                    in ("1", "true", "yes")
                )
            ):
                try:
                    from execution.trail_shadow import shadow_trail
                    _inst = open_pos.get("instrument") or state.instrument
                    _bars = BarHistory(log_dir=log_dir).recent(_inst, 60, for_date=today)
                    _entry_ts = str(open_pos.get("bar_ts") or open_pos.get("ts") or "")
                    _since = [b for b in _bars if str(b.get("ts", "")) >= _entry_ts] if _entry_ts else _bars
                    _t = shadow_trail(
                        open_pos, _since,
                        activation_r=float(os.getenv("RUNNER_ACTIVATION_R", "1.0") or 1.0),
                        trail_r=float(os.getenv("RUNNER_TRAIL_R", "0.5") or 0.5),
                    )
                    if _t and _t.get("moved") and tv.replace_stop(_t["would_stop"]):
                        logger.info(
                            "[trail-live] %s stop %s → %s (order sent)",
                            _inst, _t["original_stop"], _t["would_stop"],
                        )
                        # Persist both the possibly-reminted stop order id and
                        # accepted stop price. A restart must resume from the
                        # broker-confirmed trail, never the original bracket.
                        journal.log_order_ids(
                            instrument=_inst,
                            session=state.session,
                            order_ids=getattr(tv, "_last_order_ids", None) or {},
                            for_date=open_position_date,
                            stop=float(_t["would_stop"]),
                            exit_mode="runner_live",
                        )
                except Exception as _exc:  # live trail must never break trading
                    logger.warning("trail-live skipped: %s", _exc)

            # ── Stale-position safety net (paper mode only) ───────────────────
            # If resolve_position returned None (stop/target not hit), check
            # whether the position has gone stale:
            #   (a) price-scale mismatch: current close differs from stored entry
            #       by more than 5% — prices are from wrong instrument/chart.
            #   (b) age timeout: position has been open for more than 8 hours —
            #       intraday paper positions should not survive a full session.
            # In either case, force-close at the current bar's close price so
            # the system never stays blocked by an unresolvable open position.
            # Tradovate mode: skip — the broker manages the bracket; None just
            # means still open.
            if fill is None and not _using_tradovate_position and same_instrument:
                entry_price = float(open_pos["entry"])
                price_ratio = abs(payload.close - entry_price) / entry_price if entry_price else 1.0
                position_age_hours: float = 999.0
                open_pos_ts = open_pos.get("ts") or open_pos.get("opened_at")
                if open_pos_ts:
                    try:
                        opened_at = datetime.fromisoformat(str(open_pos_ts).replace("Z", "+00:00"))
                        position_age_hours = (datetime.now(timezone.utc) - opened_at).total_seconds() / 3600
                    except (ValueError, TypeError):
                        pass

                instrument_root = (open_pos.get("instrument") or state.instrument or "").upper().rstrip("!1234567890HMUZ")
                mismatch_threshold = _STALE_PRICE_MISMATCH_THRESHOLD.get(instrument_root, 0.05)
                is_price_mismatch = price_ratio > mismatch_threshold
                is_timed_out = position_age_hours > 8.0
                is_stale = is_price_mismatch or is_timed_out

                if is_stale:
                    reason = "PRICE_MISMATCH" if is_price_mismatch else "SESSION_TIMEOUT"
                    direction = open_pos.get("direction", "LONG")
                    contracts = int(open_pos.get("contracts", 1))
                    # Compute realistic P&L at current close vs entry
                    tick_size = 0.25
                    tick_value = _tick_value_for(open_pos.get("instrument") or state.instrument)
                    raw_ticks = (payload.close - entry_price) / tick_size
                    signed_ticks = raw_ticks if direction == "LONG" else -raw_ticks
                    pnl_dollars = round(signed_ticks * tick_value * contracts, 2)
                    exit_result = "WIN" if signed_ticks > 0 else "LOSS" if signed_ticks < 0 else "BREAKEVEN"
                    logger.warning(
                        "Force-closing stale paper position: instrument=%s reason=%s "
                        "entry=%.2f current_close=%.2f age_hours=%.1f pnl=$%.2f",
                        open_pos.get("instrument"), reason, entry_price,
                        payload.close, position_age_hours, pnl_dollars,
                    )
                    journal.log_outcome(
                        instrument=open_pos.get("instrument") or state.instrument,
                        session=state.session,
                        result=exit_result,
                        entry_price=entry_price,
                        exit_price=payload.close,
                        exit_reason=f"FORCE_CLOSE_{reason}",
                        pnl_ticks=signed_ticks,
                        pnl_dollars=pnl_dollars,
                        contracts=contracts,
                        for_date=open_position_date,
                        paper_order_id=open_pos.get("paper_order_id"),
                    )
                    result["resolution"] = f"FORCE_CLOSE_{reason}"
                    daily_state.has_open_position = False
                    daily_state.realized_pnl_dollars += pnl_dollars
                    if exit_result == "LOSS":
                        daily_state.consecutive_losses += 1
                        daily_state.last_loss_at = state.timestamp
                    elif exit_result in ("WIN", "BREAKEVEN"):
                        daily_state.consecutive_losses = 0
                    # Discord alert — force-close means something went wrong with
                    # the candle feed or position tracking; needs operator attention.
                    _notify_force_close(
                        instrument=open_pos.get("instrument") or state.instrument,
                        reason=reason,
                        contracts=contracts,
                        pnl_dollars=pnl_dollars,
                        config=cfg,
                        simulate=simulate,
                    )

            if fill is not None:
                journal.log_outcome(
                    instrument=fill.instrument,
                    session=state.session,
                    result=fill.result,
                    entry_price=fill.entry_price,
                    exit_price=fill.exit_price,
                    exit_reason=fill.exit_reason,
                    pnl_ticks=fill.pnl_ticks,
                    pnl_dollars=fill.pnl_dollars,
                    contracts=fill.contracts,
                    for_date=open_position_date,
                    paper_order_id=getattr(fill, "paper_order_id", None),
                )
                result["resolution"] = fill.result
                if fill.result in {"WIN", "LOSS"}:
                    logger.info(
                        "%s: %s %sc P&L $%.2f",
                        fill.result, fill.instrument, fill.contracts, float(fill.pnl_dollars or 0),
                    )
                daily_state.has_open_position = False
                daily_state.realized_pnl_dollars += float(fill.pnl_dollars or 0.0)
                if fill.result == "LOSS":
                    daily_state.consecutive_losses += 1
                    daily_state.last_loss_at = state.timestamp
                elif fill.result in ("WIN", "BREAKEVEN"):
                    daily_state.consecutive_losses = 0
                if fill.result in {"WIN", "LOSS", "BREAKEVEN"}:
                    _notify_trade_closed(
                        fill=fill,
                        session=state.session,
                        day_pnl_dollars=daily_state.realized_pnl_dollars,
                        config=cfg,
                        simulate=simulate,
                    )

    # ── Step 2: Check hard daily capacity before evaluating a new signal ─────
    total_daily_capacity = cfg.max_trades_per_day + int(getattr(cfg, "bonus_trades_after_max", 0) or 0)
    if daily_state.trade_count >= total_daily_capacity:
        result["decision"] = "BLOCKED_MAX_TRADES"
        return result
    # max_consecutive_losses is the hard stop regardless of circuit breaker setting.
    # circuit_breaker_losses (lower threshold) triggers a temporary pause via adaptive layer.
    if daily_state.consecutive_losses >= cfg.max_consecutive_losses:
        result["decision"] = "BLOCKED_LOSS_LOCKOUT"
        return result
    if daily_state.has_open_position:
        result["decision"] = "BLOCKED_OPEN_POSITION"
        return result

    # ── Step 3: Decision engine ───────────────────────────────────────────────
    if five_min_trigger:
        from strategy.signal_engine import DecisionOutput, SetupDetail
        _s = five_min_trigger["setup"]
        decision = DecisionOutput(
            timestamp=state.timestamp,
            instrument=state.instrument,
            session=state.session,
            decision="TRADE",
            reason="Armed 15m setup triggered by 5m retest of original entry.",
            market_condition=five_min_trigger.get("payload", {}).get("market_condition"),
            setup=SetupDetail(**_s),
            failed_gates=[],
        )
    else:
        # Every new authoritative 15M decision invalidates the previous arm.
        if five_min_enabled():
            clear_armed_setup(state.instrument, log_dir, for_date)
        decision = DecisionEngine(config=cfg).evaluate(state, daily_state)

    # ── MNQ orb_reclaim proof mode (Stage 2, 2026-07-11) ──────────────────────
    # Scoped narrowly: only ever runs for instrument==MNQ, strategy==orb_reclaim.
    # Never touches MNQ range_break_close, MES, or the RANGE_BOUND/
    # require_trending_condition gate above (this block runs strictly after a
    # TRADE decision has already cleared that gate). This block is PURE
    # AUDIT/OVERRIDE-COMPUTATION — it never changes decision.decision or
    # decision.setup. The existing MNQ orb_reclaim path (TRADE_INTENT → risk →
    # broker, current entry-type/exit-mode config) is byte-for-byte unaffected
    # in observe_only mode; only paper_sim/tradovate_demo apply the market-entry
    # + runner-exit override, and only at the broker/BracketOrder layer further
    # below — never by suppressing or redirecting the decision itself. See
    # context/mnq_orb_reclaim_proof.py for the mode semantics.
    # Restoration candidate #2 (2026-07-13/14, docs/orb-breakout-entry-study-
    # 2026-07-11.md): identical architecture, independent lane, MNQ orb_breakout
    # only. A decision matches at most ONE of these two candidate checks
    # (decision.setup.strategy is a single string), so they are mutually
    # exclusive by construction — never both active for the same decision.
    mnq_proof_decision = None
    mnq_proof_audit = None
    mnq_breakout_proof_decision = None
    mnq_breakout_proof_audit = None
    mnq_vwap_hold_proof_decision = None
    mnq_vwap_hold_proof_audit = None
    if (
        decision.decision == "TRADE"
        and decision.setup is not None
        and is_mnq_orb_reclaim_candidate(state.instrument, decision.setup.strategy)
    ):
        mnq_proof_decision = evaluate_mnq_orb_reclaim_proof(
            cfg=cfg,
            log_dir=log_dir,
            orb_high=getattr(state.orb, "high", None) if state.orb else None,
            orb_low=getattr(state.orb, "low", None) if state.orb else None,
            direction=decision.setup.direction,
            for_date=for_date,
        )
        mnq_proof_audit = {
            **mnq_proof_decision.to_audit_dict(),
            "would_be_setup": {
                "direction": decision.setup.direction,
                "entry": decision.setup.entry,
                "stop": decision.setup.stop,
                "target": decision.setup.target,
                "rr_ratio": decision.setup.rr_ratio,
            },
        }
        if mnq_proof_decision.suppress:
            # Only reachable when an operator has explicitly opted into
            # paper_sim/tradovate_demo AND this exact ORB campaign already had
            # its one proof attempt today — never reachable under the default
            # observe_only mode, so default behavior is unaffected.
            import dataclasses as _dataclasses
            decision = _dataclasses.replace(
                decision,
                decision="NO_TRADE",
                setup=None,
                reason=mnq_proof_decision.reason,
                failed_gates=list(decision.failed_gates or []) + ["MNQ_ORB_RECLAIM_PROOF_DUPLICATE"],
            )
    elif (
        decision.decision == "TRADE"
        and decision.setup is not None
        and is_mnq_orb_breakout_candidate(state.instrument, decision.setup.strategy)
    ):
        mnq_breakout_proof_decision = evaluate_mnq_orb_breakout_proof(
            cfg=cfg,
            log_dir=log_dir,
            orb_high=getattr(state.orb, "high", None) if state.orb else None,
            orb_low=getattr(state.orb, "low", None) if state.orb else None,
            direction=decision.setup.direction,
            for_date=for_date,
        )
        mnq_breakout_proof_audit = {
            **mnq_breakout_proof_decision.to_audit_dict(),
            "would_be_setup": {
                "direction": decision.setup.direction,
                "entry": decision.setup.entry,
                "stop": decision.setup.stop,
                "target": decision.setup.target,
                "rr_ratio": decision.setup.rr_ratio,
            },
        }
        if mnq_breakout_proof_decision.suppress:
            import dataclasses as _dataclasses
            decision = _dataclasses.replace(
                decision,
                decision="NO_TRADE",
                setup=None,
                reason=mnq_breakout_proof_decision.reason,
                failed_gates=list(decision.failed_gates or []) + ["MNQ_ORB_BREAKOUT_PROOF_DUPLICATE"],
            )
    elif (
        decision.decision == "TRADE"
        and decision.setup is not None
        and is_mnq_vwap_hold_candidate(state.instrument, decision.setup.strategy)
    ):
        # Restoration candidate #3 (2026-07-14, docs/strategy-matrix-tranche1-
        # 2026-07-14.md): MNQ + vwap_hold + new_york only. A vwap_hold TRADE
        # can only exist here because the signal engine's permission-gate
        # exception opened for paper_sim+new_york (vwap_hold is SHADOW_ONLY
        # globally) — or because an operator re-promoted it in risk_rules.yaml,
        # in which case evaluate() returns a no-op and the normal path is
        # their explicit choice. In paper_sim the decision below either gets
        # the full paper override or is suppressed as a duplicate: it never
        # falls through to the normal IOC/static/Tradovate path.
        mnq_vwap_hold_proof_decision = evaluate_mnq_vwap_hold_proof(
            cfg=cfg,
            log_dir=log_dir,
            session=state.session,
            direction=decision.setup.direction,
            for_date=for_date,
        )
        mnq_vwap_hold_proof_audit = {
            **mnq_vwap_hold_proof_decision.to_audit_dict(),
            "would_be_setup": {
                "direction": decision.setup.direction,
                "entry": decision.setup.entry,
                "stop": decision.setup.stop,
                "target": decision.setup.target,
                "rr_ratio": decision.setup.rr_ratio,
            },
        }
        if mnq_vwap_hold_proof_decision.suppress:
            import dataclasses as _dataclasses
            decision = _dataclasses.replace(
                decision,
                decision="NO_TRADE",
                setup=None,
                reason=mnq_vwap_hold_proof_decision.reason,
                failed_gates=list(decision.failed_gates or []) + ["MNQ_VWAP_HOLD_PROOF_DUPLICATE"],
            )
    # Unified handle for the downstream broker/BracketOrder override layer,
    # which does not need to know WHICH proof lane is active, only whether
    # one is. Mutually exclusive per decision, so at most one is ever non-None.
    _active_mnq_proof_decision = (
        mnq_proof_decision or mnq_breakout_proof_decision or mnq_vwap_hold_proof_decision
    )
    result["decision"] = decision.decision
    result["regime"] = decision.regime
    result["gex_status"] = decision.gex_status
    result["signa_status"] = decision.signa_status
    result["failed_gates"] = decision.failed_gates
    result["confidence_score"] = decision.confidence_score
    opportunity_candidate_ids = _record_candidate_audit(
        decision, state, log_dir, today
    )

    # ── Entry-refresh decision (Phase 1, PR #265) ──────────────────────────────
    # Pure audit/decision computation on an ENTRY_DETACHED_FROM_PRICE candidate,
    # scoped to context.mnq_entry_refresh's configured instrument/strategy set
    # (default MNQ + orb_reclaim only). NEVER changes decision.decision or
    # decision.setup — the existing NO_TRADE outcome is unaffected in every
    # mode. observe_only/shadow both attach a pure audit dict; only shadow may
    # additionally open a hypothetical position (never a real order, never
    # risk-evaluated, never broker-evaluated).
    entry_refresh_audit = None
    _er_mode = entry_refresh_mode(cfg)
    if (
        _er_mode != "off"
        and decision.decision != "TRADE"
        and "ENTRY_DETACHED_FROM_PRICE" in (decision.failed_gates or [])
    ):
        _er_candidate = next(
            (
                c for c in (decision.candidate_audit or [])
                if c.get("reject_code") == "ENTRY_DETACHED_FROM_PRICE"
                and is_entry_refresh_candidate(state.instrument, c.get("strategy"), cfg)
            ),
            None,
        )
        if _er_candidate is not None:
            try:
                _er_root = (state.instrument or "").upper().replace("1!", "")
                _er_tick = TICK_SIZE.get(_er_root, 0.25)
                _er_live_price = float(state.ohlc.close) if state.ohlc else float(_er_candidate["entry"])
                _er_decision = refresh_detached_entry(
                    direction=_er_candidate["direction"],
                    entry=float(_er_candidate["entry"]),
                    stop=float(_er_candidate["stop"]),
                    target=float(_er_candidate["target"]),
                    live_price=_er_live_price,
                    tick=_er_tick,
                    max_detachment_r=entry_refresh_max_detachment_r(cfg),
                )
                entry_refresh_audit = {
                    "mode": _er_mode,
                    "strategy": _er_candidate.get("strategy"),
                    **_er_decision.to_audit_dict(),
                }
                if _er_mode == "shadow" and _er_decision.outcome == "REFRESHED":
                    open_shadow_position(
                        log_dir,
                        instrument=_er_root,
                        strategy=_er_candidate.get("strategy"),
                        direction=_er_decision.direction,
                        entry=_er_decision.refreshed_entry,
                        stop=_er_decision.refreshed_stop,
                        target=_er_decision.refreshed_target,
                        entry_ts=bar_ts,
                        refresh_policy="translate",
                        detachment_ticks=_er_decision.detachment_ticks,
                        detachment_r=_er_decision.detachment_r,
                    )
            except Exception:  # noqa: BLE001 — entry-refresh audit must never break decisions
                logger.debug("entry-refresh decision skipped", exc_info=True)
                entry_refresh_audit = None

    if decision.decision != "TRADE" or decision.setup is None:
        if (
            five_min_enabled()
            and decision.setup is not None
            and (
                "ENTRY_DETACHED_FROM_PRICE" in decision.failed_gates
                or "COUNTERTREND_REQUIRES_5M" in decision.failed_gates
            )
        ):
            try:
                arm_fifteen_min_setup(
                    state.instrument,
                    log_dir,
                    setup=decision.to_dict()["setup"],
                    payload=payload.model_dump(),
                    for_date=for_date,
                )
            except OSError as _exc:
                logger.warning("5m feed: setup arm skipped: %s", _exc)
        journal_entry = decision.to_dict()
        journal_entry["context"] = _market_state_context(state)
        if shadow_candidates:
            journal_entry["shadow_candidates"] = shadow_candidates
        if mnq_proof_audit is not None:
            journal_entry["mnq_orb_reclaim_proof_audit"] = mnq_proof_audit
        if mnq_breakout_proof_audit is not None:
            journal_entry["mnq_orb_breakout_proof_audit"] = mnq_breakout_proof_audit
        if mnq_vwap_hold_proof_audit is not None:
            journal_entry["mnq_vwap_hold_proof_audit"] = mnq_vwap_hold_proof_audit
        if entry_refresh_audit is not None:
            journal_entry["entry_refresh_audit"] = entry_refresh_audit
        gex_observed = _maybe_observe_gex(state, cfg)
        if gex_observed:
            journal_entry["gex_observed"] = gex_observed
        if _wall_ctx_dict:
            journal_entry["wall_context"] = _wall_ctx_dict
        if state.market_condition in ("RANGE_BOUND", "CHOPPY"):
            if _range_state_dict:
                journal_entry["range_state"] = _range_state_dict
            if _range_signal_dict:
                journal_entry["range_signal"] = _range_signal_dict
        journal.log_decision(journal_entry, None, for_date=today)
        if decision.setup is not None:
            result["candidate"] = _candidate_snapshot(
                setup=decision.setup,
                instrument=state.instrument,
                session=state.session,
                timeframe=state.ohlc.timeframe if state.ohlc else None,
                reject_code=decision.decision,
                reject_reason=decision.reason,
                blocking_gate=(
                    decision.failed_gates[-1] if decision.failed_gates else None
                ),
                event_id=result.get("event_id"),
            )
        _record_candidate_lifecycle(
            opportunity_candidate_ids,
            log_dir,
            today,
            "DECISION_BLOCKED",
            decision=decision.decision,
            failed_gates=list(decision.failed_gates or []),
        )
        return result

    # ── Step 3a: Per-instrument stop-width multiplier ─────────────────────────
    # MNQ's tight stops get swept then reverse (81% of stopped MNQ trades later
    # hit the original target vs 43% MES), so widen the stop (entry→stop risk) by
    # the configured multiplier BEFORE sizing/bracketing. Target is left fixed
    # (matches the validated backtest); the runner, when on, drops it anyway.
    # 1.0 / unset instrument = no change. Mutates the SetupDetail in place so the
    # journal records the actual stop used.
    _mult = apply_stop_multiplier(
        decision.setup,
        state.instrument,
        getattr(cfg, "stop_multiplier_per_instrument", {}) or {},
    )
    if _mult != 1.0:
        logger.info(
            "stop-width ×%.2f on %s: stop→%s (R/R %.2f)",
            _mult, state.instrument, decision.setup.stop, decision.setup.rr_ratio,
        )

    # ── Step 3b: Score confluence ─────────────────────────────────────────────
    confluence = _score_setup(state, decision.setup)
    result["confidence_score"] = confluence.score
    result["confluence"] = {
        "score": confluence.score,
        "grade": confluence.grade,
        "factors": confluence.factors,
        "penalties": confluence.penalties,
    }
    journal_entry = decision.to_dict()
    journal_entry["context"] = _market_state_context(state)
    if shadow_candidates:
        journal_entry["shadow_candidates"] = shadow_candidates
    if mnq_proof_audit is not None:
        journal_entry["mnq_orb_reclaim_proof_audit"] = mnq_proof_audit
    if mnq_breakout_proof_audit is not None:
        journal_entry["mnq_orb_breakout_proof_audit"] = mnq_breakout_proof_audit
    if mnq_vwap_hold_proof_audit is not None:
        journal_entry["mnq_vwap_hold_proof_audit"] = mnq_vwap_hold_proof_audit
    journal_entry["confluence"] = result["confluence"]
    gex_observed = _maybe_observe_gex(state, cfg)
    if gex_observed:
        journal_entry["gex_observed"] = gex_observed
    if _wall_ctx_dict:
        journal_entry["wall_context"] = _wall_ctx_dict
    if _range_signal_dict:
        journal_entry["shadow_range_signal"] = _range_signal_dict

    # ── Step 4: Risk validation ───────────────────────────────────────────────
    journal_balance = journal.get_account_balance(
        cfg.position_sizing.starting_balance, today
    )
    broker = (
        _paper_broker(journal_balance, cfg)
        if simulate
        else _make_broker(starting_balance=journal_balance, cfg=cfg)
    )
    if (
        _active_mnq_proof_decision is not None
        and _active_mnq_proof_decision.apply_override
        and _active_mnq_proof_decision.force_paper_broker
    ):
        # paper_sim mode: force a dedicated PaperBroker (market entry, runner
        # exit) regardless of the box's normal paper_mode/BROKER selection —
        # this proof mode never touches the real broker in paper_sim.
        broker = PaperBroker(
            starting_balance=journal_balance,
            slippage_ticks=float(getattr(cfg, "fill_slippage_ticks", 0.0) or 0.0),
            pessimistic_both_hit=bool(getattr(cfg, "fill_pessimistic_both_hit", False)),
            runner_mode=True,
            runner_activation_r=float(getattr(cfg, "runner_activation_r", 1.0) or 1.0),
            runner_trail_r=float(getattr(cfg, "runner_trail_r", 0.5) or 0.5),
            entry_fill_model="market",
        )
    account_balance = broker.get_account_balance()
    if account_balance is None:
        account_balance = journal_balance
    daily_state.account_balance = account_balance
    daily_state.account_peak_balance = journal.get_account_peak_balance(
        cfg.position_sizing.starting_balance, today
    )
    risk_engine = RiskEngine(config=cfg)
    contracts = risk_engine.recommended_contracts(state.instrument, account_balance)
    if decision.setup.direction_role == "COUNTERTREND_SCALP":
        contracts = 1
    # Tick-align entry/stop/target to valid broker prices.
    entry_px = _round_to_tick(decision.setup.entry, state.instrument)
    stop_px = _round_to_tick(decision.setup.stop, state.instrument)
    target_px = _round_to_tick(decision.setup.target, state.instrument)
    # Persist the real contract count + rounded prices into the journaled setup so
    # stateless resolution uses the correct quantity (was None → defaulted to 1).
    if isinstance(journal_entry.get("setup"), dict):
        journal_entry["setup"].update(
            {"contracts": contracts, "entry": entry_px, "stop": stop_px, "target": target_px}
        )
    trade_setup = TradeSetup(
        direction=decision.setup.direction,
        entry=entry_px,
        stop=stop_px,
        target=target_px,
        rr_ratio=decision.setup.rr_ratio,
        strategy=decision.setup.strategy,
        instrument=state.instrument,
        session=state.session,
        notes=decision.setup.notes,
        entry_time=state.timestamp,
        contracts=contracts,
        confluence_grade=confluence.grade,
    )
    risk_result = risk_engine.validate(trade_setup, daily_state)
    if risk_result.approved and decision.setup.direction_role == "COUNTERTREND_SCALP":
        root = state.instrument.upper().rstrip("!1234567890HMUZ")
        tick_size = _TICK_SIZE_BY_ROOT.get(root, 0.25)
        tick_value = _tick_value_for(root)
        stop_ticks = abs(float(entry_px) - float(stop_px)) / tick_size
        planned_risk = stop_ticks * tick_value
        normal_budget = (
            float(account_balance or 0)
            * float(getattr(cfg, "max_account_risk_per_trade_percent", 1.0) or 1.0)
            / 100.0
        )
        countertrend_budget = normal_budget * 0.5
        if planned_risk > countertrend_budget:
            risk_result = RiskResult(
                result="REJECTED",
                failed_rule="countertrend_risk_cap",
                reason=(
                    f"Countertrend scalp risk ${planned_risk:.2f} exceeds "
                    f"50% risk budget ${countertrend_budget:.2f}; structural stop "
                    "is not tightened or widened."
                ),
            )
    risk_dict = {
        "result": risk_result.result,
        "failed_rule": risk_result.failed_rule,
        "reason": risk_result.reason,
    }
    result["risk"] = risk_dict
    _record_candidate_lifecycle(
        opportunity_candidate_ids,
        log_dir,
        today,
        "RISK_CHECK",
        risk_result=risk_result.result,
        risk_failed_rule=risk_result.failed_rule,
    )
    if not risk_result.approved:
        # Update journal entry decision before writing so the log reflects reality.
        journal_entry["decision"] = "RISK_REJECTED"
        journal_entry["reason"] = risk_result.reason or journal_entry.get("reason")
    else:
        # Confirmed-execution model (2026-07-10, EXECUTION_STATE_BUG fix): the
        # pre-broker row is an INTENT, not an open position. Log it as
        # decision="TRADE_INTENT" so NO reader (get_open_position /
        # _compute_daily_state / risk gates / reconciler / status) treats it as an
        # open, counted trade. The authoritative decision="TRADE" row — the only row
        # any reader treats as an open position — is written ONLY after the broker
        # confirms an OPEN position with order ids, further below.
        journal_entry["decision"] = "TRADE_INTENT"
    journal.log_decision(journal_entry, risk_dict, for_date=today)

    if not risk_result.approved:
        result["decision"] = "RISK_REJECTED"
        result["candidate"] = _candidate_snapshot(
            setup=decision.setup,
            instrument=state.instrument,
            session=state.session,
            timeframe=state.ohlc.timeframe if state.ohlc else None,
            reject_code=risk_result.failed_rule,
            reject_reason=risk_result.reason,
            blocking_gate=risk_result.failed_rule,
            contracts=contracts,
            entry=entry_px,
            stop=stop_px,
            target=target_px,
            event_id=result.get("event_id"),
        )
        if risk_result.failed_rule in {"circuit_breaker", "max_daily_loss", "max_drawdown"}:
            logger.warning("CIRCUIT_BREAKER: %s", risk_result.reason)
        return result

    # ── Step 5: Execute bracket order ────────────────────────────────────────
    # Safety: if the broker itself reports live env, block unless the config
    # flag agrees. This catches TRADOVATE_ENV=live overriding LIVE_TRADING_ENABLED.
    if getattr(broker, "is_live", False) and not getattr(cfg, "live_trading_enabled", False):
        logger.error(
            "BLOCKED: broker.is_live=True but live_trading_enabled=False — "
            "set LIVE_TRADING_ENABLED=true in config to allow real orders"
        )
        result["decision"] = "LIVE_TRADING_BLOCKED"
        return result

    order = BracketOrder(
        instrument=state.instrument,
        direction=decision.setup.direction,
        entry=entry_px,
        stop=stop_px,
        target=target_px,
        rr_ratio=decision.setup.rr_ratio,
        strategy=decision.setup.strategy,
        notes=decision.setup.notes,
        contracts=contracts,
        force_market_entry=bool(_active_mnq_proof_decision and _active_mnq_proof_decision.force_market_entry),
        force_runner_exit=bool(_active_mnq_proof_decision and _active_mnq_proof_decision.force_runner_exit),
    )

    # ── Schedule-mode execution gate (Phase 3 safety chokepoint) ──────────────
    # In "current" this always allows (no behavior change). always_on_shadow
    # suppresses ALL orders; always_on_paper allows only paper_eligible_sessions.
    from adaptive.execution_gate import order_placement_allowed
    _allowed, _gate_reason = order_placement_allowed(
        schedule_mode=getattr(cfg, "schedule_mode", "current"),
        session=state.session,
        live_trading_enabled=getattr(cfg, "live_trading_enabled", False),
        paper_eligible_sessions=getattr(cfg, "paper_eligible_sessions", []),
    )
    if not _allowed:
        logger.info("Order suppressed by schedule gate: %s", _gate_reason)
        result["decision"] = "SHADOW_NO_ORDER"
        result["gate_reason"] = _gate_reason
        _record_candidate_lifecycle(
            opportunity_candidate_ids,
            log_dir,
            today,
            "ORDER_SUPPRESSED",
            broker_result="NOT_SENT",
            gate_reason=_gate_reason,
        )
        return result

    # ── Final working-order recheck (execution-safety gate) ──────────────────
    # Immediately before sending a real order: re-read the broker's own order
    # book for this account. A working/pending order the local journal/paper
    # state doesn't know about (manual intervention, a crashed prior run, an
    # order placed outside this system) must block a new entry, not be
    # silently missed. Only meaningful for a real broker connection — PaperBroker
    # cannot have this conflict (execute_bracket already refuses a second
    # concurrent order internally: `self._position is not None or
    # self._pending_stop_entry is not None`) and has no order book to read.
    # Gated on "not PaperBroker" rather than broker.is_live: TradovateBroker.is_live
    # is True ONLY for TRADOVATE_ENV=live, not demo, but this recheck must run for
    # Tradovate demo too (demo is the realistic dry run for this exact gate).
    if getattr(cfg, "working_order_recheck_enabled", True) and not isinstance(broker, PaperBroker):
        _wo_reason = None
        try:
            from execution.live_preflight import _list_orders, _order_status, WORKING_ORDER_STATUSES

            _account_id = getattr(broker, "_account_id", None)
            _existing_orders = _list_orders(broker)
            _working = [
                o for o in _existing_orders
                if _order_status(o) in WORKING_ORDER_STATUSES
                and (_account_id is None or o.get("accountId") in (None, _account_id))
            ]
            if _working:
                _wo_reason = (
                    f"working_order_conflict: {len(_working)} working order(s) on account"
                )
        except Exception as exc:
            # Fail closed: an unreadable order book is NOT the same as "no
            # working orders." Never treat a failed read as clear.
            logger.warning("Working-order recheck failed to read broker state: %s", exc)
            _wo_reason = f"order_state_unreadable: {exc}"

        if _wo_reason:
            logger.info("Order suppressed by working-order recheck: %s", _wo_reason)
            result["decision"] = "ORDER_SUPPRESSED"
            result["gate_reason"] = _wo_reason
            _record_candidate_lifecycle(
                opportunity_candidate_ids,
                log_dir,
                today,
                "ORDER_SUPPRESSED",
                broker_result="NOT_SENT",
                gate_reason=_wo_reason,
            )
            return result

    # Diagnostic-only tracing around the broker call (EXECUTION_STATE_BUG
    # investigation, 2026-07-10): confirms whether execute_bracket is reached
    # and what it actually returns, independent of whether the broker's own
    # internal success/failure logging fires. No behavior depends on these
    # log lines — safe to remove once the mechanism is understood.
    logger.info(
        "EXEC_TRACE pre-submit: instrument=%s strategy=%s direction=%s "
        "entry=%s stop=%s target=%s broker=%s paper_mode=%s broker_env=%s",
        order.instrument, order.strategy, order.direction,
        order.entry, order.stop, order.target,
        type(broker).__name__, getattr(cfg, "paper_mode", None),
        getattr(getattr(broker, "config", None), "env", None),
    )
    if mnq_proof_decision is not None and mnq_proof_decision.apply_override:
        # Record the campaign attempt right before the real broker call — a
        # schedule-gate/working-order suppression above never reaches here, so
        # a legitimately-retried attempt is not falsely deduped. Recorded
        # whether or not this attempt fills.
        record_campaign_attempt(
            log_dir,
            orb_high=getattr(state.orb, "high", None) if state.orb else None,
            orb_low=getattr(state.orb, "low", None) if state.orb else None,
            direction=decision.setup.direction,
            for_date=for_date,
        )
    elif mnq_breakout_proof_decision is not None and mnq_breakout_proof_decision.apply_override:
        record_orb_breakout_campaign_attempt(
            log_dir,
            orb_high=getattr(state.orb, "high", None) if state.orb else None,
            orb_low=getattr(state.orb, "low", None) if state.orb else None,
            direction=decision.setup.direction,
            for_date=for_date,
        )
    elif mnq_vwap_hold_proof_decision is not None and mnq_vwap_hold_proof_decision.apply_override:
        record_vwap_hold_campaign_attempt(
            log_dir,
            direction=decision.setup.direction,
            for_date=for_date,
        )
    # Proof-lane paper market entry fills at the LIVE price (decision bar's
    # close — the same reference the entry-sanity guard uses), never at the
    # anchored plan level. Only the forced PaperBroker takes this argument;
    # Tradovate's force_market_entry is a real Market order and needs nothing.
    _proof_market_px = (
        state.ohlc.close
        if (
            state.ohlc is not None
            and isinstance(broker, PaperBroker)
            and _active_mnq_proof_decision is not None
            and _active_mnq_proof_decision.apply_override
            and _active_mnq_proof_decision.force_market_entry
        )
        else None
    )
    _submit_ts = datetime.now(timezone.utc)
    fill = (
        broker.execute_bracket(order, market_price=_proof_market_px)
        if _proof_market_px is not None
        else broker.execute_bracket(order)
    )
    _cancel_ts = datetime.now(timezone.utc)
    logger.info(
        "EXEC_TRACE post-submit: instrument=%s fill_result=%s fill_reason=%s "
        "order_ids_present=%s",
        order.instrument, fill.result, getattr(fill, "reason", None),
        bool(getattr(broker, "_last_order_ids", None)),
    )
    _record_candidate_lifecycle(
        opportunity_candidate_ids,
        log_dir,
        today,
        "BROKER_RESULT",
        broker_result=fill.result,
    )
    if fill.result != "OPEN":
        # Broker did NOT establish a position. A CANCELLED result is an EXPECTED
        # IOC limit no-fill (the broker accepted the order; the limit just didn't
        # fill) — log it at WARNING so it doesn't pollute error counts / alerting.
        # Anything else (rejected / exception / naked-flatten) is a genuine
        # execution failure: log ERROR and fire the live-order-blocked alert.
        if fill.result == "CANCELLED":
            logger.warning(
                "ENTRY_NOT_FILLED: %s %s — limit not filled (CANCELLED)",
                order.instrument, order.direction,
            )
        else:
            logger.error("ORDER FAILED: %s %s — %s", order.instrument, order.direction, fill.result)
            if os.getenv("BROKER", "paper").strip().lower() == "tradovate":
                try:
                    from notifications.discord_notifier import send_discord_alert
                    send_discord_alert(
                        cfg,
                        "LIVE ORDER BLOCKED: Tradovate did not accept the order. "
                        f"No order sent/kept open. Reason: {fill.result}. "
                        f"Setup: {order.direction} {order.instrument} {order.contracts}c "
                        f"@ {order.entry} stop {order.stop} target {order.target}.",
                    )
                except Exception as exc:  # pragma: no cover - notification must never affect trading
                    logger.warning("Live-order-blocked Discord alert failed: %s", exc)
        # The decision was journaled as a TRADE (open) above, but the broker did
        # NOT establish a position (rejected / no-fill / naked-flattened). Book a
        # CANCELLED outcome NOW so the journal doesn't carry a phantom-open that
        # blocks this instrument until the 20-min reconciler sweeps it. Mirrors the
        # reconciler's own clear, and via CANCELLED-not-counted it also un-counts
        # the failed attempt from the daily/session trade limits.
        journal.log_outcome(
            instrument=order.instrument,
            session=state.session,
            result="CANCELLED",
            entry_price=order.entry,
            exit_price=None,
            exit_reason=f"execution_failed:{fill.result}",
            pnl_ticks=0.0,
            pnl_dollars=0.0,
            contracts=order.contracts,
            for_date=today,
            no_fill_reason=getattr(fill, "no_fill_reason", None),
            order_type=getattr(fill, "order_type", None),
            broker_status_raw=fill.exit_reason,
            strategy=order.strategy,
            signal_timestamp=state.timestamp.isoformat() if state.timestamp else None,
            submit_timestamp=_submit_ts.isoformat(),
            cancel_timestamp=_cancel_ts.isoformat(),
            seconds_until_cancel=(_cancel_ts - _submit_ts).total_seconds(),
            requested_entry=order.entry,
            # Not currently captured anywhere in the execution path (no live
            # quote/order-book snapshot at submit or cancel time) — reserved
            # for future instrumentation rather than faked.
            last_price_at_submit=None,
            last_price_at_cancel=None,
            best_bid_at_submit=None,
            best_ask_at_submit=None,
            ticks_moved_from_entry=None,
        )
        daily_state.has_open_position = False
        result["decision"] = "BLOCKED_EXECUTION_FAILED"
        result["fill"] = {
            "status": fill.result,
            "instrument": state.instrument,
            "direction": decision.setup.direction,
            "entry": decision.setup.entry,
            "stop": decision.setup.stop,
            "target": decision.setup.target,
        }
        return result

    # ── Broker returned OPEN: confirm before journaling an open position ──────
    # Fail closed if either adapter reports OPEN without its required identity:
    # structured broker ids for real adapters, or the synthetic PAPER-* id for
    # the internal simulator. Never journal an untraceable confirmed trade.
    _order_ids = getattr(broker, "_last_order_ids", None)
    _requires_order_ids = not isinstance(broker, PaperBroker)
    _paper_order_id = getattr(fill, "paper_order_id", None)
    _confirmation_missing = (
        (_requires_order_ids and not _order_ids)
        or (isinstance(broker, PaperBroker) and not _paper_order_id)
    )
    if _confirmation_missing:
        logger.error(
            "ORDER_CONFIRMATION_MISSING: %s %s — broker returned OPEN but no order "
            "ids; failing closed (not marking open, not counting the trade).",
            order.instrument, order.direction,
        )
        if _requires_order_ids and os.getenv("BROKER", "paper").strip().lower() == "tradovate":
            try:
                from notifications.discord_notifier import send_discord_alert
                send_discord_alert(
                    cfg,
                    "LIVE ORDER BLOCKED: broker reported OPEN but returned no order ids. "
                    "Position NOT marked open (fail-closed) — verify in Tradovate. "
                    f"Setup: {order.direction} {order.instrument} {order.contracts}c "
                    f"@ {order.entry} stop {order.stop} target {order.target}.",
                )
            except Exception as exc:  # pragma: no cover - notification must never affect trading
                logger.warning("Order-confirmation-missing Discord alert failed: %s", exc)
        # Book a non-open CANCELLED so the attempt is un-counted and audit-visible,
        # exactly like the non-OPEN path. no_fill_reason distinguishes it from a
        # plain IOC no-fill for the taxonomy.
        journal.log_outcome(
            instrument=order.instrument,
            session=state.session,
            result="CANCELLED",
            entry_price=order.entry,
            exit_price=None,
            exit_reason="order_confirmation_missing:OPEN_without_order_identity",
            pnl_ticks=0.0,
            pnl_dollars=0.0,
            contracts=order.contracts,
            for_date=today,
            no_fill_reason="ORDER_CONFIRMATION_MISSING",
            order_type=getattr(fill, "order_type", None),
            broker_status_raw=fill.exit_reason,
            strategy=order.strategy,
            signal_timestamp=state.timestamp.isoformat() if state.timestamp else None,
            submit_timestamp=_submit_ts.isoformat(),
            cancel_timestamp=_cancel_ts.isoformat(),
            seconds_until_cancel=(_cancel_ts - _submit_ts).total_seconds(),
            requested_entry=order.entry,
        )
        daily_state.has_open_position = False
        result["decision"] = "BLOCKED_ORDER_CONFIRMATION_MISSING"
        result["fill"] = {
            "status": "ORDER_CONFIRMATION_MISSING",
            "instrument": state.instrument,
            "direction": decision.setup.direction,
            "entry": decision.setup.entry,
            "stop": decision.setup.stop,
            "target": decision.setup.target,
        }
        return result

    if isinstance(broker, PaperBroker):
        journal_entry["paper_order_id"] = _paper_order_id

    # Broker confirmed an OPEN position (with order ids on a real broker). NOW write
    # the authoritative decision="TRADE" row — the ONLY row any reader treats as an
    # open, counted position — carrying the same full payload as the TRADE_INTENT row.
    journal_entry["decision"] = "TRADE"
    # Proof-lane market entry: the paper fill was at the LIVE price, not the
    # anchored plan. Position reconstruction (get_open_position -> restore_
    # position) and P&L both resolve from THIS row's setup.entry, so the
    # confirmed row must carry the actual fill; the anchored plan remains on
    # the TRADE_INTENT row and in the proof audit's would_be_setup.
    _proof_fill_entry = getattr(fill, "entry_price", None)
    if (
        _proof_market_px is not None
        and _proof_fill_entry is not None
        and isinstance(journal_entry.get("setup"), dict)
    ):
        journal_entry["setup"] = {**journal_entry["setup"], "entry": _proof_fill_entry}
        journal_entry["proof_fill_entry_price"] = _proof_fill_entry
    journal.log_decision(journal_entry, risk_dict, for_date=today)

    logger.info(
        "TRADE: %s %s %sc @ %s stop %s target %s",
        order.instrument, order.direction, order.contracts, order.entry, order.stop, order.target,
    )
    if five_min_trigger:
        # Consume the 15M authority only after the broker confirms an OPEN
        # position. Risk/schedule/capacity blocks and IOC no-fills may retry
        # within the short arm TTL.
        clear_armed_setup(state.instrument, log_dir, for_date)
    daily_state.trade_count += 1
    daily_state.has_open_position = True

    # Persist the broker's OSO order ids next to the open position so a restart can
    # restore order-id exit attribution (see resolve_position) rather than degrade
    # to price-matching. Tradovate only — PaperBroker has none, so this is skipped.
    # Fail-soft: a persistence hiccup must never affect trading.
    try:
        if _order_ids:
            journal.log_order_ids(
                instrument=order.instrument,
                session=state.session,
                order_ids=_order_ids,
                for_date=today,
                stop=decision.setup.stop,
                exit_mode=getattr(cfg, "exit_mode", "static"),
            )
    except Exception as _exc:  # pragma: no cover - persistence must never break trading
        logger.warning("order-id persist skipped: %s", _exc)

    result["fill"] = {
        "status": "OPEN",
        "instrument": state.instrument,
        "direction": decision.setup.direction,
        "entry": (
            _proof_fill_entry
            if (_proof_market_px is not None and _proof_fill_entry is not None)
            else decision.setup.entry
        ),
        "stop": decision.setup.stop,
        "target": decision.setup.target,
        "rr_ratio": decision.setup.rr_ratio,
        "strategy": decision.setup.strategy,
        "contracts": order.contracts,
        "paper_order_id": _paper_order_id,
    }

    # Companion options paper lane: a fully-approved, OPENED futures trade derives an
    # internal paper options candidate (Signa-gated). Fail-soft, audit-only; never
    # mutates futures state/journal/counts. No-op unless the lane is enabled.
    _maybe_create_companion(cfg, state, decision, order, result)
    return result


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _companion_provider_and_store(cfg: SystemConfig):
    """Build a read-only Public chain provider + companion ledger. Returns
    (provider, store) or None if the lane is disabled/misconfigured. Never raises."""
    try:
        from options_companion.chain_provider import PublicChainProvider
        from options_companion.store import OptionsCompanionStore

        provider = PublicChainProvider(
            base_url=getattr(cfg, "public_base_url", "https://api.public.com"),
            api_key=os.getenv("PUBLIC_API_KEY", "").strip(),
            account_id=os.getenv("PUBLIC_ACCOUNT_ID", "").strip(),
        )
        store = OptionsCompanionStore(
            getattr(cfg, "options_companion_sqlite_path", "logs/options_companion.sqlite")
        )
        return provider, store
    except Exception:  # noqa: BLE001 — companion setup must never affect futures
        logger.warning("companion provider/store init failed", exc_info=True)
        return None


def _maybe_create_companion(cfg: SystemConfig, state, decision, order, result: dict) -> None:
    """Post-fill hook: derive a paper options companion from an OPENED futures trade.

    Fail-soft and isolated — a companion error must NEVER affect the futures result.
    Gated on cfg.options_companion_enabled. Attaches audit to result["companion"].
    """
    if not getattr(cfg, "options_companion_enabled", False):
        return
    try:
        built = _companion_provider_and_store(cfg)
        if built is None:
            return
        provider, store = built
        from options_companion.evaluator import CompanionConfig, run_companion_create

        companion_cfg = CompanionConfig(
            enforce_signa_gate=getattr(cfg, "options_companion_strict_signa", True),
        )
        result["companion"] = run_companion_create(
            state=state,
            futures_instrument=state.instrument,
            futures_direction=decision.setup.direction,
            provider=provider,
            store=store,
            config=companion_cfg,
            now=state.timestamp,
            futures_timestamp=state.timestamp.isoformat() if state.timestamp else None,
        )
    except Exception:  # noqa: BLE001 — never break futures on a companion error
        logger.warning("companion create hook failed", exc_info=True)


def _maybe_resolve_companions(cfg: SystemConfig, state) -> None:
    """Per-webhook hook: refresh open companion paper marks. Fail-soft, isolated."""
    if not getattr(cfg, "options_companion_enabled", False):
        return
    try:
        built = _companion_provider_and_store(cfg)
        if built is None:
            return
        provider, store = built
        from options_companion.resolver import run_companion_resolve

        run_companion_resolve(provider, store, now=state.timestamp)
    except Exception:  # noqa: BLE001 — never break ingestion on a companion error
        logger.warning("companion resolve hook failed", exc_info=True)


def _maybe_enrich_payload_with_signa(payload: AlertPayload, cfg: SystemConfig) -> None:
    """Best-effort Signa shadow enrichment. Never raises, never blocks."""
    if not getattr(cfg, "signa_api_enabled", False):
        return
    try:
        from sources.signa_client import enrich_payload_with_signa
        signal = enrich_payload_with_signa(payload, cfg)
        if signal and not signal.ok:
            logger.info("Signa enrichment skipped: %s", signal.error)
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.warning("Signa enrichment failed: %s", exc)


def _maybe_observe_gex(state, cfg: SystemConfig) -> Optional[dict]:
    """Observe-only GEX context for the journal, computed in-house from the
    Public.com chain (sources/gex_observer.py).

    Disabled by default (gex_observe_enabled). When on, returns a compact GEX
    record (net GEX / gamma-flip / walls for the instrument's tracking ETF) to be
    journaled as `gex_observed`. NEVER mutates state.gex or the gex_gate — this
    exists so the GEX shadow analysis can measure whether the gamma context
    predicts our outcomes BEFORE letting it gate anything. Never raises, blocks.
    """
    if not getattr(cfg, "gex_observe_enabled", False):
        return None
    try:
        from sources.gex_observer import observe_gex
        record = observe_gex(getattr(state, "instrument", ""), cfg)
        if record and record.get("ok"):
            return record
        return None
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.warning("GEX observe failed: %s", exc)
        return None


def _notify_force_close(
    *,
    instrument: str,
    reason: str,
    contracts: int,
    pnl_dollars: float,
    config,
    simulate: bool = False,
) -> None:
    """Fire a Discord notification when a position is force-closed.

    Force-close means the candle feed or position tracking has drifted —
    the operator should investigate. Runs in a background thread so it
    never blocks the webhook response. Simulated paper/replay closes never
    notify live operator channels. Prefers the DiscordRouter "error" channel
    when configured; falls back to the legacy single webhook URL.
    """
    if simulate:
        return
    import threading
    from notifications.discord_notifier import _post_json
    import json as _json

    sign = "+" if pnl_dollars >= 0 else ""
    message = (
        f"⚠️ FORCE_CLOSE ({reason})\n"
        f"{instrument} {contracts}c  P&L {sign}${pnl_dollars:.2f}\n"
        f"Position closed by safety net — check candle feed / position tracking."
    )

    def _send():
        try:
            from notifications.discord_router import DiscordRouter

            router = DiscordRouter()
            if router.is_enabled("error"):
                router.send("error", message)
                return
        except Exception as exc:
            logger.debug("Discord error route unavailable: %s", exc)
        # Legacy fallback — gated on its OWN config, independent of the router
        # attempt above, so an unset DISCORD_WEBHOOK_URL never suppresses the
        # router path (that was the bug: these checks used to gate the entire
        # function, before the router was ever tried).
        if not getattr(config, "discord_notifications_enabled", False):
            return
        url = getattr(config, "discord_webhook_url", "")
        if not url:
            return
        try:
            body = _json.dumps({"content": message}).encode("utf-8")
            _post_json(url, body, {"Content-Type": "application/json"})
        except Exception as exc:
            logger.warning("Force-close Discord notification failed: %s", exc)

    threading.Thread(target=_send, daemon=True).start()


def _notify_trade_closed(
    *,
    fill,
    session: str,
    day_pnl_dollars: float,
    config,
    simulate: bool = False,
) -> None:
    """Send one fail-soft live broker outcome notification in the background."""
    if simulate:
        return

    import threading

    pnl = float(fill.pnl_dollars or 0.0)
    ticks = float(fill.pnl_ticks or 0.0)
    if fill.result == "WIN":
        icon, label = "🟢", "WIN"
    elif fill.result == "LOSS":
        icon, label = "🔴", "LOSS"
    else:
        icon, label = "⚪", "BREAKEVEN"
    sign = "+" if pnl >= 0 else "-"
    root = (fill.instrument or "").upper().rstrip("!1234567890HMUZ")
    points = abs(ticks) * _TICK_SIZE_BY_ROOT.get(root, 0.25)
    points_sign = "+" if ticks >= 0 else "-"
    day_sign = "+" if day_pnl_dollars >= 0 else "-"
    reason = fill.exit_reason or "CLOSED"
    entry = "?" if fill.entry_price is None else f"{fill.entry_price:g}"
    exit_price = "?" if fill.exit_price is None else f"{fill.exit_price:g}"
    session_label = (session or "").replace("_", " ").title()
    message = (
        f"{icon} {label} — {fill.instrument} {fill.direction}  "
        f"{sign}${abs(pnl):.2f}  ({points_sign}{points:.2f} pts)\n"
        f"Entry {entry} → Exit {exit_price} · {reason} · {fill.contracts}c · {session_label}\n"
        f"Day P&L: {day_sign}${abs(day_pnl_dollars):.2f}"
    )

    def _send() -> None:
        try:
            from notifications.discord_router import DiscordRouter

            router = DiscordRouter()
            if router.is_enabled("daily_report"):
                router.send("daily_report", message)
                return
        except Exception as exc:
            logger.debug("Discord daily_report route unavailable: %s", exc)
        try:
            from notifications.discord_notifier import send_discord_alert

            send_discord_alert(config, message)
        except Exception as exc:
            logger.warning("Trade-closed Discord notification failed: %s", exc)

    threading.Thread(target=_send, daemon=True).start()


def _position_is_complete(pos: dict) -> bool:
    """All required keys present and non-None."""
    return all(pos.get(k) is not None for k in ("direction", "entry", "stop", "target"))


def _market_state_context(state) -> dict:
    """Public, JSON-safe snapshot of the market state derived from the alert."""
    context = {
        "instrument": state.instrument,
        "session": state.session,
        "timestamp": state.timestamp.isoformat(),
        "close": state.ohlc.close,
        "timeframe": state.ohlc.timeframe,
        "vwap": {
            "value": state.vwap.value,
            "price_vs_vwap": state.vwap.price_vs_vwap,
            "reclaimed": state.vwap.reclaimed,
            "holding": state.vwap.holding,
        },
        "orb": {
            "high": state.orb.high,
            "low": state.orb.low,
            "status": state.orb.status,
        },
        "trend": {
            "direction": state.trend.direction if state.trend else None,
            "strength": state.trend.strength if state.trend else None,
        },
        "market_condition": state.market_condition,
        "previous_day": {
            "high": state.previous_day.high,
            "low": state.previous_day.low,
            "close": state.previous_day.close,
            "price_vs_pdh": state.previous_day.price_vs_pdh,
            "price_vs_pdl": state.previous_day.price_vs_pdl,
        },
        "volume": {
            "current_bar": state.volume.current_bar,
            "avg_bar": state.volume.avg_bar,
            "relative": state.volume.relative,
        },
        "strat": {
            "current_bar_type": state.strat.current_bar_type if state.strat else None,
            "previous_bar_type": state.strat.previous_bar_type if state.strat else None,
            "two_bars_back_type": state.strat.two_bars_back_type if state.strat else None,
            "strat_sequence": state.strat.strat_sequence if state.strat else None,
            "strat_trigger": state.strat.strat_trigger if state.strat else None,
            "strat_direction": state.strat.strat_direction if state.strat else None,
        },
        "htf": {
            "daily_direction": state.htf.daily_direction if state.htf else None,
            "four_hour_direction": state.htf.four_hour_direction if state.htf else None,
            "one_hour_direction": state.htf.one_hour_direction if state.htf else None,
            "ftfc_direction": state.htf.ftfc_direction if state.htf else None,
            "ftfc_aligned": state.htf.ftfc_aligned if state.htf else None,
        },
        "gex": {
            "gex_flip": state.gex.gex_flip if state.gex else None,
            "call_wall": state.gex.call_wall if state.gex else None,
            "put_wall": state.gex.put_wall if state.gex else None,
            "hvl": state.gex.hvl if state.gex else None,
            "max_pain": state.gex.max_pain if state.gex else None,
            "ghost": state.gex.ghost if state.gex else None,
            "mid_upper": state.gex.mid_upper if state.gex else None,
            "mid_lower": state.gex.mid_lower if state.gex else None,
            "vol_trigger_up": state.gex.vol_trigger_up if state.gex else None,
            "vol_trigger_down": state.gex.vol_trigger_down if state.gex else None,
            "gex_regime": state.gex.gex_regime if state.gex else None,
            "delta_bias": state.gex.delta_bias if state.gex else None,
        },
        "signa": {
            "grade": state.signa.grade if state.signa else None,
            "score": state.signa.score if state.signa else None,
            "daily_direction": state.signa.daily_direction if state.signa else None,
            "weekly_direction": state.signa.weekly_direction if state.signa else None,
        },
        "icc": {
            "phase": state.icc.phase if state.icc else None,
            "entry_signal": state.icc.entry_signal if state.icc else None,
            "indication_type": state.icc.indication_type if state.icc else None,
            "indication_level": state.icc.indication_level if state.icc else None,
            "last_swing_high": state.icc.last_swing_high if state.icc else None,
            "last_swing_low": state.icc.last_swing_low if state.icc else None,
            "correction_high": state.icc.correction_high if state.icc else None,
            "correction_low": state.icc.correction_low if state.icc else None,
            "stop_loss": state.icc.stop_loss if state.icc else None,
            "tp1": state.icc.tp1 if state.icc else None,
            "tp2": state.icc.tp2 if state.icc else None,
            "htf_phase": state.icc.htf_phase if state.icc else None,
        },
    }
    if state.structural_regime is not None:
        context.update(state.structural_regime)
    if state.structural_range_candidates:
        context["structural_range_candidates"] = state.structural_range_candidates
    return context


def _safe_bar_ts(payload: AlertPayload) -> str:
    """Best-effort ISO bar timestamp for journaling a pre-state-build rejection."""
    try:
        from webhook.state_builder import parse_timestamp
        return parse_timestamp(payload.timestamp).isoformat()
    except Exception:
        return str(payload.timestamp)


def normalize_timeframe_minutes(timeframe: object) -> Optional[int]:
    """Normalize a TradingView timeframe token to whole minutes.

    Accepts the forms TradingView's `{{interval}}` / `timeframe.period` emit:
        "15", "5", "1"        → minutes as-is
        "15m", "5min"         → minutes (strip suffix)
        "1h", "60"            → 60
        "1D"/"D", "1W"/"W"    → 1440 / 10080
    Returns None if the token cannot be parsed (treated as a mismatch).
    """
    if timeframe is None:
        return None
    s = str(timeframe).strip().lower()
    if not s:
        return None
    # Pure number → minutes (TradingView intraday intervals are minute counts).
    if s.isdigit():
        return int(s)
    # Day / week / month tokens.
    if s in ("d", "1d", "day", "1day"):
        return 1440
    if s in ("w", "1w", "week", "1week"):
        return 10080
    if s in ("m_month", "mo", "1mo", "month"):
        return 43200
    # Suffixed forms: 15m, 5min, 1h, 2h, 4h.
    import re
    match = re.fullmatch(r"(\d+)\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours)", s)
    if match:
        value = int(match.group(1))
        unit = match.group(2)
        if unit.startswith("h"):
            return value * 60
        return value
    return None


def _check_timeframe(payload: AlertPayload, cfg: SystemConfig) -> Optional[dict]:
    """Return mismatch info if the alert's timeframe != the expected one, else None.

    Disabled (returns None) when expected_timeframe_minutes <= 0.
    """
    expected = int(getattr(cfg, "expected_timeframe_minutes", 15) or 0)
    if expected <= 0:
        return None
    received_raw = payload.timeframe
    received = normalize_timeframe_minutes(received_raw)
    if received == expected:
        return None

    def _label(minutes: Optional[int], raw: object) -> str:
        if minutes is None:
            return f"{raw!r}"
        if minutes % 1440 == 0:
            return f"{minutes // 1440}D"
        if minutes % 60 == 0:
            return f"{minutes // 60}h"
        return f"{minutes}m"

    exp_label = _label(expected, expected)
    recv_label = _label(received, received_raw)
    return {
        "expected": exp_label,
        "received": recv_label,
        "expected_minutes": expected,
        "received_minutes": received,
        "reason": (
            f"Live alert misconfigured: expected {exp_label} chart, "
            f"received {recv_label}. Recreate the TradingView alert on the "
            f"{exp_label} chart."
        ),
    }


def _bar_timeframe_minutes(payload: AlertPayload, cfg: SystemConfig) -> int:
    """Bar timeframe in minutes for gap detection: the payload's own timeframe,
    falling back to the configured expected timeframe (default 15)."""
    received = normalize_timeframe_minutes(payload.timeframe)
    if received and received > 0:
        return received
    expected = int(getattr(cfg, "expected_timeframe_minutes", 15) or 0)
    return expected if expected > 0 else 15


def _check_payload_quality(payload: AlertPayload, cfg: SystemConfig) -> Optional[str]:
    """
    Return a rejection reason string if the bar data is clearly bad, else None.
    Checks: contradictory OHLC (high < low), and stale bar timestamp.
    """
    if payload.high < payload.low:
        return f"Contradictory OHLC: high {payload.high} < low {payload.low}"

    max_staleness = int(getattr(cfg, "max_staleness_seconds", 300) or 0)
    if max_staleness > 0:
        try:
            from webhook.state_builder import parse_timestamp
            bar_open_ts = parse_timestamp(payload.timestamp)
            # TradingView stamps a bar with its OPEN time, so a freshly-closed
            # bar is already (timeframe) seconds old. Measure staleness from the
            # bar CLOSE instead — that keeps max_staleness a pure delivery-lag
            # budget, independent of the decision timeframe (5m vs 15m). Without
            # this, every 15m bar reads as ~900s old and trips the 600s cap.
            tf_digits = "".join(c for c in str(payload.timeframe) if c.isdigit())
            tf_minutes = int(tf_digits) if tf_digits else 0
            bar_close_ts = bar_open_ts + timedelta(seconds=tf_minutes * 60)
            age_seconds = (datetime.now(timezone.utc) - bar_close_ts).total_seconds()
            if age_seconds > max_staleness:
                return f"Stale bar: {int(age_seconds)}s past close (max {max_staleness}s)"
        except Exception as exc:
            return f"Invalid bar timestamp: {payload.timestamp!r} ({exc})"

    return None
