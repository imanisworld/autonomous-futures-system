"""Process-wide Tradovate broker session.

Tradovate limits authentication requests. Reusing one broker instance preserves
its access token, account ID, contract cache, and auth circuit-breaker state
across webhook, status, and manual-action paths.
"""

from __future__ import annotations

from threading import Lock

from execution.tradovate_broker import TradovateBroker

_BROKER: TradovateBroker | None = None
_LOCK = Lock()


def shared_tradovate_broker() -> TradovateBroker:
    global _BROKER
    if _BROKER is None:
        with _LOCK:
            if _BROKER is None:
                _BROKER = TradovateBroker()
    return _BROKER


def reset_shared_tradovate_broker() -> None:
    """Test/reload hook. Normal runtime code should not reset the session."""
    global _BROKER
    with _LOCK:
        _BROKER = None
