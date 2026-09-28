"""
webhook/reconciler.py

Background safety reconciler for PHANTOM positions.

resolve_position() only runs when a new same-instrument bar arrives. If the feed
stalls (overnight, a stopped TradingView alert) while the journal shows an open
position the broker has actually closed, the phantom persists indefinitely — this
happened 2026-06-05 (a 3:15am scratch left a journal-open / broker-flat mismatch
that had to be cleared by hand).

This runs on a TIMER (independent of bars) and clears the phantom — but only when
the broker is AUTHENTICATED and DEFINITIVELY FLAT, and the position is stale
enough to be past any fill-propagation/settle window. On any uncertainty
(broker unauthenticated, broker still holding a position, position too recent) it
does NOTHING — it never books a close on a guess.

"Journal open + broker flat" is NOT proof the entry never filled: a completed
trade whose exit filled between 15m bar resolves looks identical (2026-07-06: a
target-hit MES win was erased as CANCELLED $0 this way). Before clearing, the
sweep therefore checks the persisted entry order's fills — fills present → book
the REAL outcome via the broker's order-id-scoped resolver; only a zero-fill
entry is cleared as a phantom.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from notifications import plain_english as _pe

logger = logging.getLogger(__name__)

RECONCILE_INTERVAL_SECONDS = 20 * 60
MIN_AGE_MINUTES = 20  # don't touch a freshly-opened position (settle window)


def _parse_ts(ts) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None


def _resolve_completed_trade(broker, open_pos: dict):
    """Book the real outcome of a filled-then-closed position through the
    broker's own order-id-scoped resolver (weighted partial-fill averaging,
    never prices from an unrelated fill).

    Mirrors the runner's restart-restore path (webhook/runner.py Step 1):
    rebuild Position + order ids from the journal snapshot, then let
    resolve_position() attribute the exit. It is called up to three times so
    its internal fail counter can engage the designed FORCE_CLOSE_UNMATCHED
    degradation (e.g. a manual close with no bracket fill) instead of looping
    unresolved forever across sweeps. Returns a Fill or None (leave open).
    """
    from execution.broker_interface import Position

    stop = open_pos.get("stop")
    target = open_pos.get("target")
    broker._last_position = Position(
        instrument=open_pos.get("instrument"),
        direction=open_pos.get("direction"),
        entry_price=float(open_pos.get("entry") or 0.0),
        stop=float(stop) if stop is not None else None,
        target=(
            None
            if open_pos.get("exit_mode") == "runner_live" or target is None
            else float(target)
        ),
        quantity=int(open_pos.get("contracts", 1) or 1),
        open=True,
    )
    order_ids = open_pos.get("order_ids")
    broker._last_order_ids = order_ids if isinstance(order_ids, dict) else None
    fill = None
    for _ in range(3):
        try:
            fill = broker.resolve_position()
        except Exception as exc:
            logger.warning("reconcile: resolve_position failed: %s", exc)
            return None
        if fill is not None:
            break
    return fill


def reconcile_open_position(
    config,
    log_dir: str = "logs",
    *,
    now: Optional[datetime] = None,
    min_age_minutes: int = MIN_AGE_MINUTES,
    broker=None,
) -> dict:
    """Clear a phantom position if (and only if) the journal shows one open, the
    broker is authenticated and flat, and it's stale. Returns an action dict
    describing what happened (for logging/tests). Never raises out."""
    from journal.journal_logger import JournalLogger

    now = now or datetime.now(timezone.utc)
    today = now.date()
    journal = JournalLogger(log_dir=log_dir)

    # Walk back up to 7 days — same as the runner's restart-restore. A position
    # opened before midnight lives in a PRIOR day's journal; a today-only check
    # makes it invisible to this sweep from 00:00 onward (the 2026-07-21 MES
    # orphan was journal-open all of Jul 22 and this sweep never saw it).
    open_pos = None
    open_pos_date = today
    for days_back in range(0, 8):
        candidate = today - timedelta(days=days_back)
        if journal.get_daily_state(candidate).has_open_position:
            open_pos = journal.get_open_position(candidate)
            open_pos_date = candidate
            break
    if not open_pos:
        return {"action": "none"}

    # Paper positions resolve against bars in the runner — only the real broker
    # path can desync into a phantom.
    if os.getenv("BROKER", "paper").strip().lower() != "tradovate":
        return {"action": "skip_non_tradovate"}

    opened_at = _parse_ts(open_pos.get("ts"))
    if opened_at is not None:
        age_min = (now - opened_at).total_seconds() / 60.0
        if age_min < min_age_minutes:
            return {"action": "too_recent", "age_min": round(age_min, 1)}

    if broker is None:
        from execution.tradovate_broker import TradovateBroker, TradovateConfig
        broker = TradovateBroker(config=TradovateConfig.from_env())

    # Only act on an AUTHENTICATED + DEFINITIVELY FLAT reading.
    if not broker._authenticate():
        return {"action": "broker_unauthenticated"}
    confirmed, pos = broker.get_position_snapshot()
    if not confirmed:
        return {"action": "broker_position_unconfirmed"}
    if pos is not None and getattr(pos, "open", False):
        # The broker genuinely holds the position — but is it still PROTECTED?
        # OSO children can die while the position lives (Day-expiry at session
        # close, observed live MES 2026-07-21: both children expired overnight,
        # the position sat naked ~36h while both exit levels printed). Journal
        # open + broker open + zero working children → loud alert EVERY sweep.
        # Alert-only: exiting the position is an operator decision, never taken
        # here, and an unknowable census (None) must never read as naked.
        _ids = open_pos.get("order_ids")
        broker._last_order_ids = _ids if isinstance(_ids, dict) else None
        census = None
        try:
            census = broker.count_working_children()
        except Exception as exc:
            logger.warning("reconcile: working-children census failed: %s", exc)
        if census is not None and census.get("working", 0) == 0:
            states = ", ".join(
                f"{k}={v}" for k, v in (census.get("states") or {}).items()
            )
            msg = (
                f"🚨 Open position with NO stop-loss — {_pe.side(open_pos.get('direction'))} "
                f"{_pe.contracts(open_pos.get('contracts', 1))} {_pe.market(open_pos.get('instrument'))}\n"
                f"Opened: {_pe.et_date(open_pos_date)}\n"
                "What happened: Tradovate shows this position open, but it has no working "
                "stop-loss or profit target order\n"
                f"Protective orders: {_pe.order_states(census.get('states'))}\n"
                "What the bot did: nothing — it will NOT close this on its own\n"
                "What to do: open Tradovate NOW and close the position or check it\n"
                f"-# details: ORPHAN/NAKED open position, 0 working protective orders ({states})"
            )
            logger.error(msg)
            try:
                from notifications.system_notifier import notify_system
                result = notify_system(msg, config=config)
                if not result.sent:
                    logger.warning(
                        "Orphan-position Discord notification NOT sent (reason=%s)",
                        result.reason,
                    )
            except Exception as exc:
                logger.warning("orphan alert failed: %s", exc)
            return {
                "action": "orphan_open_alerted",
                "instrument": open_pos.get("instrument"),
                "states": (census.get("states") or {}),
            }
        return {"action": "broker_has_position"}

    # ── Completed-trade guard (2026-07-06 erased-win incident) ────────────────
    # "Journal open + broker flat" has TWO causes: the entry never filled (true
    # phantom), or the trade COMPLETED — the exit filled between 15m bar resolves
    # and this sweep won the race against the next alert-driven resolve. Clearing
    # a completed trade as CANCELLED erases a real outcome (a target-hit MES win
    # was misbooked $0 on 2026-07-06). Discriminate on the entry order's fills:
    # fills present → book the REAL outcome through the broker's order-id-scoped
    # resolver; only a zero-fill entry may be cleared as a phantom below.
    order_ids = open_pos.get("order_ids")
    order_ids = order_ids if isinstance(order_ids, dict) else None
    entry_id = (order_ids or {}).get("entry")
    # ── Prior-day positions: the phantom discrimination is UNRELIABLE ─────────
    # /fill/list is session-scoped, so for a position opened on a PRIOR day a
    # readable-but-empty fill list proves nothing — the entry's fill may simply
    # have aged out. entry_filled=False must then NOT phantom-clear (that would
    # erase a real trade's outcome and reverse its trade count — the erased-win
    # class). Book through the resolver instead: real attribution while fills
    # are still readable, else its designed FORCE_CLOSE_UNMATCHED breakeven
    # (loud, auditable, count-preserving).
    stale_fill_window = open_pos_date != today
    resolve_instead_of_clear = stale_fill_window
    if entry_id is not None and not stale_fill_window:
        try:
            entry_filled = broker.entry_order_filled(entry_id)
        except Exception as exc:
            logger.warning("reconcile: entry fill check failed: %s", exc)
            entry_filled = None
        if entry_filled is None:
            # Uncertainty rule (same as an unconfirmed position read): do
            # NOTHING this sweep rather than risk erasing a real trade.
            return {"action": "entry_fill_unconfirmed"}
        resolve_instead_of_clear = entry_filled
    if resolve_instead_of_clear:
        fill = _resolve_completed_trade(broker, open_pos)
        if fill is None:
            # Fills exist but attribution isn't readable yet (settle
            # window). Leave the position for the next bar resolve/sweep.
            return {"action": "entry_filled_unresolved"}
        journal.log_outcome(
            instrument=fill.instrument,
            session="reconcile",
            result=fill.result,
            entry_price=fill.entry_price,
            exit_price=fill.exit_price,
            exit_reason=fill.exit_reason,
            pnl_ticks=fill.pnl_ticks,
            pnl_dollars=fill.pnl_dollars,
            contracts=fill.contracts,
            # The OUTCOME must land in the journal day that holds the TRADE
            # row, or that day's has_open_position never clears.
            for_date=open_pos_date,
        )
        msg = (
            f"Late trade result recorded — {fill.instrument} trade {_pe.result_word(fill.result)} "
            f"{_pe.money(float(fill.pnl_dollars or 0))}\n"
            f"Trade: {_pe.side(fill.direction)} {_pe.contracts(fill.contracts)} {_pe.market(fill.instrument)}\n"
            f"How it ended: {_pe.exit_reason(fill.exit_reason)}\n"
            "What happened: the trade closed between the bot's regular price checks, so the bot "
            "caught up and recorded the result now. Nothing was sent to Tradovate.\n"
            f"-# details: auto-reconcile completed trade {fill.result} ({fill.exit_reason})"
        )
        logger.warning(msg)
        try:
            from notifications.system_notifier import notify_system
            result = notify_system(msg, config=config)
            if result.sent:
                logger.info("Reconciled-trade Discord notification sent")
            else:
                logger.warning(
                    "Reconciled-trade Discord notification NOT sent (reason=%s)",
                    result.reason,
                )
        except Exception as exc:
            logger.warning("reconcile alert failed: %s", exc)
        return {
            "action": "resolved_completed_trade",
            "instrument": fill.instrument,
            "result": fill.result,
            "pnl_dollars": fill.pnl_dollars,
        }

    # FI-4: a flat position is not enough — an entry whose cancel was never
    # confirmed can still be resting and fill later. Clear only when the
    # account has no live order; an unreadable order book is not "none".
    try:
        from execution.live_preflight import (
            TERMINAL_ORDER_STATUSES,
            _list_orders,
            _order_status,
        )

        _account_id = getattr(broker, "_account_id", None)
        _live_orders = [
            o for o in _list_orders(broker)
            if _order_status(o) not in TERMINAL_ORDER_STATUSES
            and (_account_id is None or o.get("accountId") in (None, _account_id))
        ]
    except Exception as exc:
        logger.warning("reconcile: order list unreadable, not clearing: %s", exc)
        return {"action": "orders_unreadable"}
    if _live_orders:
        logger.error(
            "reconcile: journal open, broker flat, but %d live order(s) on the account "
            "— not clearing (an unconfirmed entry may still fill)", len(_live_orders),
        )
        return {"action": "orders_working", "live_orders": len(_live_orders)}

    # Journal OPEN, broker FLAT, position stale → clear the phantom (CANCELLED,
    # exactly what a manual reconcile does — no P&L/win-rate impact).
    journal.log_outcome(
        instrument=open_pos.get("instrument"),
        session="reconcile",
        result="CANCELLED",
        entry_price=float(open_pos.get("entry") or 0.0),
        exit_price=None,
        exit_reason="auto-reconcile: journal showed open but broker is flat (phantom cleared)",
        pnl_ticks=0.0,
        pnl_dollars=0.0,
        contracts=int(open_pos.get("contracts", 1) or 1),
        for_date=open_pos_date,
    )
    msg = (
        f"⚠️ Cleared a trade that never opened — {_pe.side(open_pos.get('direction'))} "
        f"{_pe.market(open_pos.get('instrument'))}\n"
        "What happened: the bot's records showed this trade open, but Tradovate has no position\n"
        "What the bot did: marked it cancelled (no profit or loss counted)\n"
        "What to do: check Tradovate if you didn't expect this\n"
        "-# details: auto-reconcile phantom position cleared (CANCELLED)"
    )
    logger.error(msg)  # ERROR so it surfaces in journald
    try:
        from notifications.system_notifier import notify_system
        result = notify_system(msg, config=config)
        if result.sent:
            logger.info("Phantom-clear Discord notification sent")
        else:
            logger.warning(
                "Phantom-clear Discord notification NOT sent (reason=%s)", result.reason
            )
    except Exception as exc:
        logger.warning("reconcile alert failed: %s", exc)
    return {"action": "reconciled", "instrument": open_pos.get("instrument")}


async def run_reconciler_loop(
    config, log_dir: str = "logs", interval_s: int = RECONCILE_INTERVAL_SECONDS
) -> None:
    """Run reconcile_open_position on a timer until cancelled."""
    logger.info("Phantom reconciler started (interval=%ds)", interval_s)
    while True:
        try:
            await asyncio.sleep(interval_s)
            await asyncio.to_thread(reconcile_open_position, config, log_dir)
        except asyncio.CancelledError:
            logger.info("Phantom reconciler stopped")
            break
        except Exception as exc:
            from execution.tradovate_broker import TradovateEnvConfigError

            if isinstance(exc, TradovateEnvConfigError):
                raise
            logger.warning("reconciler loop error: %s", exc)
