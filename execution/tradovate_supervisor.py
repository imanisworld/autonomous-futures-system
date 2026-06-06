"""Single-owner Tradovate session reliability supervisor."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from context.futures_session import futures_session_active
from execution.tradovate_broker import (
    AUTH_COOLDOWN,
    AUTH_CREDENTIALS_REJECTED,
    AUTH_HEALTHY,
    AuthResult,
    TradovateBroker,
    TradovateConfig,
)

logger = logging.getLogger(__name__)

SUPERVISOR_INTERVAL_SECONDS = 60
HEARTBEAT_INTERVAL_SECONDS = 5 * 60
HEARTBEAT_FRESH_SECONDS = 6 * 60
DEGRADED_ALERT_SECONDS = 5 * 60


@dataclass
class ReliabilitySnapshot:
    state: str = "STARTING"
    ready: bool = False
    failure_reason: Optional[str] = None
    outage_started_at: Optional[float] = None
    last_successful_heartbeat: Optional[float] = None
    last_successful_renewal: Optional[float] = None
    next_renewal_at: Optional[float] = None
    recovery_attempts: int = 0
    cooldown_until: Optional[float] = None
    market_active: bool = False
    degraded_notified: bool = False
    action_required_notified: bool = False
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)


_SNAPSHOT = ReliabilitySnapshot()


def _iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def reliability_snapshot() -> dict:
    with _SNAPSHOT.lock:
        raw = {
            "state": _SNAPSHOT.state,
            "ready": _SNAPSHOT.ready,
            "failure_reason": _SNAPSHOT.failure_reason,
            "outage_started_at": _SNAPSHOT.outage_started_at,
            "last_successful_heartbeat": _SNAPSHOT.last_successful_heartbeat,
            "last_successful_renewal": _SNAPSHOT.last_successful_renewal,
            "next_renewal_at": _SNAPSHOT.next_renewal_at,
            "recovery_attempts": _SNAPSHOT.recovery_attempts,
            "cooldown_until": _SNAPSHOT.cooldown_until,
            "market_active": _SNAPSHOT.market_active,
        }
    for key in (
        "outage_started_at",
        "last_successful_heartbeat",
        "last_successful_renewal",
        "next_renewal_at",
        "cooldown_until",
    ):
        raw[key] = _iso(raw[key])
    return raw


def reset_reliability_snapshot() -> None:
    global _SNAPSHOT
    _SNAPSHOT = ReliabilitySnapshot()


def tradovate_order_ready(now: Optional[float] = None) -> bool:
    """Final execution gate. Only active when the deployment selects Tradovate."""
    if os.getenv("BROKER", "paper").strip().lower() != "tradovate":
        return True
    now = now or time.time()
    with _SNAPSHOT.lock:
        heartbeat = _SNAPSHOT.last_successful_heartbeat
        return bool(
            _SNAPSHOT.ready
            and _SNAPSHOT.state == "HEALTHY"
            and heartbeat is not None
            and (now - heartbeat) <= HEARTBEAT_FRESH_SECONDS
        )


def _notify(message: str) -> None:
    try:
        from config.settings import load_config
        from notifications.system_notifier import notify_system

        notify_system(message, config=load_config())
    except Exception as exc:
        logger.warning("Tradovate supervisor notification failed: %s", exc)


def _mark_failure(result: AuthResult, now: float, market_active: bool) -> None:
    action_required = result.status in {AUTH_CREDENTIALS_REJECTED, AUTH_COOLDOWN}
    notify_action = False
    notify_degraded = False
    with _SNAPSHOT.lock:
        if _SNAPSHOT.outage_started_at is None:
            _SNAPSHOT.outage_started_at = now
        _SNAPSHOT.ready = False
        _SNAPSHOT.state = "ACTION_REQUIRED" if action_required else "DEGRADED"
        _SNAPSHOT.failure_reason = result.detail or result.status
        _SNAPSHOT.recovery_attempts += 1
        _SNAPSHOT.cooldown_until = (
            now + result.retry_after_seconds if result.retry_after_seconds else None
        )
        if action_required and not _SNAPSHOT.action_required_notified:
            _SNAPSHOT.action_required_notified = True
            notify_action = True
        if (
            market_active
            and not action_required
            and not _SNAPSHOT.degraded_notified
            and now - _SNAPSHOT.outage_started_at >= DEGRADED_ALERT_SECONDS
        ):
            _SNAPSHOT.degraded_notified = True
            notify_degraded = True
    if notify_action:
        _notify(
            "TRADOVATE ACTION REQUIRED: authentication credentials/API key were "
            "rejected or the auth safety cooldown is active. Orders remain blocked."
        )
    elif notify_degraded:
        _notify(
            "TRADOVATE DEGRADED: broker connectivity has been unavailable for "
            "more than five minutes during market hours. Orders remain blocked; "
            "automatic recovery is continuing."
        )


def _mark_healthy(broker: TradovateBroker, now: float, market_active: bool) -> None:
    notify_recovery = False
    with _SNAPSHOT.lock:
        notify_recovery = _SNAPSHOT.degraded_notified or _SNAPSHOT.action_required_notified
        _SNAPSHOT.state = "HEALTHY"
        _SNAPSHOT.ready = True
        _SNAPSHOT.failure_reason = None
        _SNAPSHOT.outage_started_at = None
        _SNAPSHOT.last_successful_heartbeat = now
        _SNAPSHOT.last_successful_renewal = broker._auth_state.last_renewed_at
        token = broker._token
        _SNAPSHOT.next_renewal_at = (
            token.expires_at - broker.config.token_refresh_buffer if token else None
        )
        _SNAPSHOT.recovery_attempts = 0
        _SNAPSHOT.cooldown_until = None
        _SNAPSHOT.market_active = market_active
        _SNAPSHOT.degraded_notified = False
        _SNAPSHOT.action_required_notified = False
    if notify_recovery:
        _notify(
            "TRADOVATE RECOVERED: authentication, account access, and broker "
            "position state are confirmed. Paper orders are enabled."
        )


def supervisor_step(
    broker: TradovateBroker,
    *,
    now: Optional[float] = None,
    market_active: Optional[bool] = None,
) -> dict:
    """Run one deterministic supervisor iteration; exposed for focused tests."""
    now = now or time.time()
    active = futures_session_active() if market_active is None else market_active
    with _SNAPSHOT.lock:
        was_active = _SNAPSHOT.market_active
        last_heartbeat = _SNAPSHOT.last_successful_heartbeat
        current_state = _SNAPSHOT.state
        cooldown_until = _SNAPSHOT.cooldown_until
        _SNAPSHOT.market_active = active
    if cooldown_until is not None and now < cooldown_until:
        return reliability_snapshot()

    auth = broker.authenticate_result()
    if not auth.ok:
        _mark_failure(auth, now, active)
        return reliability_snapshot()

    heartbeat_due = bool(
        current_state == "STARTING"
        or (
            active
            and (
                not was_active
                or current_state != "HEALTHY"
                or last_heartbeat is None
                or now - last_heartbeat >= HEARTBEAT_INTERVAL_SECONDS
            )
        )
    )
    if heartbeat_due:
        heartbeat = broker.reliability_heartbeat()
        if not heartbeat.ok:
            _mark_failure(heartbeat, now, active)
            return reliability_snapshot()
        _mark_healthy(broker, now, active)
    else:
        with _SNAPSHOT.lock:
            _SNAPSHOT.last_successful_renewal = broker._auth_state.last_renewed_at
            token = broker._token
            _SNAPSHOT.next_renewal_at = (
                token.expires_at - broker.config.token_refresh_buffer if token else None
            )
    return reliability_snapshot()


async def run_tradovate_supervisor(
    interval_s: int = SUPERVISOR_INTERVAL_SECONDS,
) -> None:
    """Own Tradovate renewal/recovery for the lifetime of the FastAPI process."""
    logger.info("Tradovate reliability supervisor started (interval=%ds)", interval_s)
    broker = None
    while True:
        try:
            if os.getenv("BROKER", "paper").strip().lower() == "tradovate":
                if broker is None:
                    broker = TradovateBroker(config=TradovateConfig.from_env())
                await asyncio.to_thread(supervisor_step, broker)
            await asyncio.sleep(interval_s)
        except asyncio.CancelledError:
            logger.info("Tradovate reliability supervisor stopped")
            break
        except Exception as exc:
            logger.warning("Tradovate reliability supervisor error: %s", exc)
            await asyncio.sleep(interval_s)
