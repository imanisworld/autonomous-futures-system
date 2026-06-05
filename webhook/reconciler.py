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
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional

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

    if not journal.get_daily_state(today).has_open_position:
        return {"action": "none"}
    open_pos = journal.get_open_position(today)
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
    pos = broker.get_position()
    if pos is not None and getattr(pos, "open", False):
        return {"action": "broker_has_position"}

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
        for_date=today,
    )
    msg = (
        f"Auto-reconciled phantom position: journal showed "
        f"{open_pos.get('direction')} {open_pos.get('instrument')} open but the broker "
        f"is flat — cleared (CANCELLED). Verify in Tradovate if unexpected."
    )
    logger.error(msg)  # ERROR so it surfaces in journald
    try:
        from notifications.system_notifier import notify_system
        notify_system(msg, config=config)
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
            logger.warning("reconciler loop error: %s", exc)
