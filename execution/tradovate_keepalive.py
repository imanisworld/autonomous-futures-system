"""
execution/tradovate_keepalive.py

Background task that keeps the Tradovate session token fresh so it never expires
from inactivity (e.g. an overnight gap with no bars). The access token lives in a
PROCESS-GLOBAL shared store (_SharedAuth in tradovate_broker), so renewing via any
TradovateBroker instance keeps the session the whole app uses alive — which avoids
the re-login that triggers the recurring API-key 401.

Launched from the FastAPI lifespan; no-op unless BROKER=tradovate. Never raises
out of its loop.
"""
from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

# Renew well under the ~90-minute token life so it never lapses between cycles.
KEEPALIVE_INTERVAL_SECONDS = 30 * 60


async def run_tradovate_keepalive(interval_s: int = KEEPALIVE_INTERVAL_SECONDS) -> None:
    """Renew the shared Tradovate token every interval_s seconds until cancelled."""
    from execution.tradovate_broker import TradovateBroker, TradovateConfig

    logger.info("Tradovate keepalive started (interval=%ds)", interval_s)
    while True:
        try:
            await asyncio.sleep(interval_s)
            if os.getenv("BROKER", "paper").strip().lower() != "tradovate":
                continue
            broker = TradovateBroker(config=TradovateConfig.from_env())
            ok = await asyncio.to_thread(broker.keep_alive)
            if ok:
                logger.info("Tradovate keepalive: session token refreshed")
            else:
                logger.warning(
                    "Tradovate keepalive: could not refresh (cooldown or rejected creds)"
                )
        except asyncio.CancelledError:
            logger.info("Tradovate keepalive stopped")
            break
        except Exception as exc:  # never let the loop die
            logger.warning("Tradovate keepalive error: %s", exc)
