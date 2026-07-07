"""Independent hard live-trading lock for options_manager.

Modeled in spirit after config/settings.py's LiveTradingBlockedError pattern
for the futures system, but this module shares no code or import with it.
LIVE_OPTIONS_TRADING_ENABLED is a distinct env var from the futures system's
LIVE_TRADING_ENABLED.

This lock exists now, in Phase 1, even though no execution code exists yet —
so it is already enforced before any future phase adds real Robinhood order
calls.
"""

from __future__ import annotations

import os


class LiveOptionsTradingBlockedError(RuntimeError):
    """Raised if LIVE_OPTIONS_TRADING_ENABLED is set true.

    Phase 1 has no broker/order logic at all — this exists purely as a
    scaffold so the lock is already in place before any future phase.
    """


def _as_bool(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in ("1", "true", "yes", "on")


def assert_live_options_trading_disabled() -> None:
    if _as_bool(os.getenv("LIVE_OPTIONS_TRADING_ENABLED")):
        raise LiveOptionsTradingBlockedError(
            "LIVE_OPTIONS_TRADING_ENABLED=true — options_manager has no "
            "execution logic in Phase 1 and must not boot with live options "
            "trading enabled."
        )
