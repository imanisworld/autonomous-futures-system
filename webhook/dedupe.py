"""
webhook/dedupe.py

In-memory duplicate-alert protection for the paper phase.

A TradingView alert is considered a duplicate when the same symbol fires the
same alert/event on the same bar, regardless of the price quoted in the
payload. The dedupe key is therefore:

    symbol + alert_name(or event_type) + bar_time

Price is deliberately EXCLUDED from the key — the same bar re-sent with a
slightly different quoted price is still the same bar.

Characteristics:
  - In-memory dict with a configurable TTL (DEDUPE_TTL_SECONDS, default 600).
  - Expired entries are evicted on every check so memory cannot grow unbounded.
  - Restarting the process clears all dedupe state (documented, acceptable for
    the paper phase).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

DEFAULT_TTL_SECONDS = 600


def dedupe_key(symbol: str | None, alert: str | None, bar_time: str | None) -> str:
    """Build the canonical dedupe key. Price is intentionally not included."""
    return "|".join(
        str(part if part is not None else "").strip().upper()
        for part in (symbol, alert, bar_time)
    )


@dataclass
class DedupeCache:
    ttl_seconds: float = DEFAULT_TTL_SECONDS
    clock: Callable[[], float] = time.monotonic
    _seen: dict[str, float] = field(default_factory=dict)

    def _evict_expired(self, now: float) -> None:
        cutoff = now - self.ttl_seconds
        expired = [key for key, stamp in self._seen.items() if stamp < cutoff]
        for key in expired:
            del self._seen[key]

    def is_duplicate(self, symbol: str | None, alert: str | None, bar_time: str | None) -> bool:
        """Return True if this (symbol, alert, bar_time) was seen within the TTL.

        Records the key as seen (refreshing its timestamp) as a side effect, so a
        first occurrence returns False and subsequent ones within the TTL return
        True. Always evicts expired entries first.
        """
        now = self.clock()
        self._evict_expired(now)
        key = dedupe_key(symbol, alert, bar_time)
        seen = key in self._seen
        self._seen[key] = now
        return seen

    def clear(self) -> None:
        self._seen.clear()

    def __len__(self) -> int:  # pragma: no cover - trivial
        return len(self._seen)
