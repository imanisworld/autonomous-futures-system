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
from typing import Optional

from config.settings import SystemConfig, load_config
from execution.broker_interface import BracketOrder, BrokerInterface
from context.bar_history import BarHistory
from execution.paper_broker import NextBarOHLC, PaperBroker
from journal.journal_logger import JournalLogger
from risk.risk_engine import DailyState, RiskEngine, TradeSetup
from strategy.confluence_scorer import score_setup as _score_setup
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


def _paper_broker(starting_balance: float, cfg: Optional[SystemConfig]) -> PaperBroker:
    """PaperBroker wired with the configured fill-realism settings."""
    return PaperBroker(
        starting_balance=starting_balance,
        slippage_ticks=float(getattr(cfg, "fill_slippage_ticks", 0.0) or 0.0),
        pessimistic_both_hit=bool(getattr(cfg, "fill_pessimistic_both_hit", False)),
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

    # ── Rolling bar history (Phase 3): continuous price record + gap detection ──
    # Record EVERY ingested bar (traded or not) so regime can be judged over a
    # window and ingestion gaps are visible. Fail-soft: a history hiccup must
    # never affect ingestion, the decision, or risk.
    bar_gap = None
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
    except Exception:  # noqa: BLE001 — fail-soft, never break ingestion
        logger.warning("bar history update failed", exc_info=True)

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
        "timestamp": payload.timestamp,
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
        shadow_candidates = [c.to_dict() for c in evaluate_shadow_setups(state)]
    except Exception:
        shadow_candidates = []
    if shadow_candidates:
        result["shadow_candidates"] = shadow_candidates

    bar_ts = state.timestamp.isoformat()
    if not journal.claim_bar(instrument=state.instrument, bar_ts=bar_ts, for_date=today):
        result["decision"] = "BLOCKED_DUPLICATE_BAR"
        result["failed_gates"] = [f"Duplicate bar already processed: {state.instrument} {bar_ts}"]
        return result

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
            # Only resolve a position against bars of its OWN instrument. An MNQ
            # position must never be resolved against a MES bar's OHLC (different
            # price scale → false no-hit, and the price-mismatch safety net would
            # then force-close at the wrong instrument's price). Each instrument's
            # own next bar resolves its own position.
            _open_root = (open_pos.get("instrument") or "").upper().rstrip("!1234567890HMUZ")
            _bar_root = (state.instrument or "").upper().rstrip("!1234567890HMUZ")
            same_instrument = bool(_open_root) and _open_root == _bar_root
            if not simulate and broker_type == "tradovate":
                from execution.tradovate_broker import TradovateBroker, TradovateConfig
                from execution.broker_interface import Position as _Position
                tv = TradovateBroker(config=TradovateConfig.from_env())
                tv._last_position = _Position(
                    instrument=open_pos["instrument"] or state.instrument,
                    direction=open_pos["direction"],
                    entry_price=float(open_pos["entry"]),
                    stop=float(open_pos["stop"]),
                    target=float(open_pos["target"]),
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
                broker = _paper_broker(
                    journal.get_account_balance(
                        cfg.position_sizing.starting_balance, today
                    ),
                    cfg,
                )
                broker.restore_position(
                    instrument=open_pos["instrument"] or state.instrument,
                    direction=open_pos["direction"],
                    entry=float(open_pos["entry"]),
                    stop=float(open_pos["stop"]),
                    target=float(open_pos["target"]),
                    contracts=int(open_pos.get("contracts", 1)),
                )
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
            if same_instrument and os.getenv("RUNNER_SHADOW_ENABLED", "").strip().lower() in ("1", "true", "yes"):
                try:
                    from execution.trail_shadow import shadow_trail, format_shadow_log
                    _inst = open_pos.get("instrument") or state.instrument
                    _bars = BarHistory(log_dir=cfg.log_dir).recent(_inst, 60)
                    _entry_ts = str(open_pos.get("ts") or "")
                    _since = [b for b in _bars if str(b.get("ts", "")) >= _entry_ts] if _entry_ts else _bars
                    _shadow = shadow_trail(
                        open_pos, _since,
                        activation_r=float(os.getenv("RUNNER_ACTIVATION_R", "1.0") or 1.0),
                        trail_r=float(os.getenv("RUNNER_TRAIL_R", "0.5") or 0.5),
                    )
                    if _shadow:
                        logger.info(format_shadow_log(_shadow, _inst))
                except Exception as _exc:  # shadow must never affect trading
                    logger.debug("trail-shadow skipped: %s", _exc)

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
            if fill is None and simulate and same_instrument:
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
    decision = DecisionEngine(config=cfg).evaluate(state, daily_state)
    result["decision"] = decision.decision
    result["regime"] = decision.regime
    result["gex_status"] = decision.gex_status
    result["signa_status"] = decision.signa_status
    result["failed_gates"] = decision.failed_gates
    result["confidence_score"] = decision.confidence_score

    if decision.decision != "TRADE" or decision.setup is None:
        journal_entry = decision.to_dict()
        journal_entry["context"] = _market_state_context(state)
        if shadow_candidates:
            journal_entry["shadow_candidates"] = shadow_candidates
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
        return result

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
    account_balance = broker.get_account_balance()
    if account_balance is None:
        account_balance = journal_balance
    daily_state.account_balance = account_balance
    daily_state.account_peak_balance = journal.get_account_peak_balance(
        cfg.position_sizing.starting_balance, today
    )
    risk_engine = RiskEngine(config=cfg)
    contracts = risk_engine.recommended_contracts(state.instrument, account_balance)
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
    risk_dict = {
        "result": risk_result.result,
        "failed_rule": risk_result.failed_rule,
        "reason": risk_result.reason,
    }
    result["risk"] = risk_dict
    if not risk_result.approved:
        # Update journal entry decision before writing so the log reflects reality.
        journal_entry["decision"] = "RISK_REJECTED"
        journal_entry["reason"] = risk_result.reason or journal_entry.get("reason")
    journal.log_decision(journal_entry, risk_dict, for_date=today)

    if not risk_result.approved:
        result["decision"] = "RISK_REJECTED"
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
        return result

    fill = broker.execute_bracket(order)
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

    logger.info(
        "TRADE: %s %s %sc @ %s stop %s target %s",
        order.instrument, order.direction, order.contracts, order.entry, order.stop, order.target,
    )
    daily_state.trade_count += 1
    daily_state.has_open_position = True

    # Persist the broker's OSO order ids next to the open position so a restart can
    # restore order-id exit attribution (see resolve_position) rather than degrade
    # to price-matching. Tradovate only — PaperBroker has none, so this is skipped.
    # Fail-soft: a persistence hiccup must never affect trading.
    try:
        _order_ids = getattr(broker, "_last_order_ids", None)
        if _order_ids:
            journal.log_order_ids(
                instrument=order.instrument,
                session=state.session,
                order_ids=_order_ids,
                for_date=today,
            )
    except Exception as _exc:  # pragma: no cover - persistence must never break trading
        logger.warning("order-id persist skipped: %s", _exc)

    result["fill"] = {
        "status": "OPEN",
        "instrument": state.instrument,
        "direction": decision.setup.direction,
        "entry": decision.setup.entry,
        "stop": decision.setup.stop,
        "target": decision.setup.target,
        "rr_ratio": decision.setup.rr_ratio,
        "strategy": decision.setup.strategy,
        "contracts": order.contracts,
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
) -> None:
    """Fire a Discord notification when a position is force-closed.

    Force-close means the candle feed or position tracking has drifted —
    the operator should investigate. Runs in a background thread so it
    never blocks the webhook response.
    """
    import threading
    from notifications.discord_notifier import _post_json
    import json as _json

    if not getattr(config, "discord_notifications_enabled", False):
        return
    url = getattr(config, "discord_webhook_url", "")
    if not url:
        return

    sign = "+" if pnl_dollars >= 0 else ""
    message = (
        f"⚠️ FORCE_CLOSE ({reason})\n"
        f"{instrument} {contracts}c  P&L {sign}${pnl_dollars:.2f}\n"
        f"Position closed by safety net — check candle feed / position tracking."
    )

    def _send():
        try:
            body = _json.dumps({"content": message}).encode("utf-8")
            _post_json(url, body, {"Content-Type": "application/json"})
        except Exception as exc:
            logger.warning("Force-close Discord notification failed: %s", exc)

    threading.Thread(target=_send, daemon=True).start()


def _position_is_complete(pos: dict) -> bool:
    """All required keys present and non-None."""
    return all(pos.get(k) is not None for k in ("direction", "entry", "stop", "target"))


def _market_state_context(state) -> dict:
    """Public, JSON-safe snapshot of the market state derived from the alert."""
    return {
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
