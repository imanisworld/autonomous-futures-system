"""
webhook/dedupe.py

In-memory duplicate-alert protection for the paper phase.

A TradingView alert is considered a duplicate when the same symbol fires the
same alert/event on the same bar OF THE SAME TIMEFRAME, regardless of the
price quoted in the payload. The dedupe key is therefore:

    symbol + alert_name(or event_type) + bar_time + timeframe

Price is deliberately EXCLUDED from the key — the same bar re-sent with a
slightly different quoted price is still the same bar.

Timeframe is deliberately INCLUDED: every 15m bar-open (:00/:15/:30/:45) is
also a 5m bar-open, so with both feeds live the 5m alert (arriving at
open+5min) and the 15m alert (open+15min) share symbol+event+bar_time. With
the key missing timeframe, the 15m alert's survival hinged on whether the
~600s arrival gap beat the 600s TTL — seconds of delivery jitter decided
every quarter-hour, silently blanking the 15m decision feed (incident class
2026-07-14 → 2026-07-17, root-caused from packet capture 2026-07-17 02:45Z:
MES's 15m alert survived at gap 602.7s while MNQ's died at 593.6s).

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


def dedupe_key(
    symbol: str | None,
    alert: str | None,
    bar_time: str | None,
    timeframe: str | None = None,
) -> str:
    """Build the canonical dedupe key. Price is intentionally not included;
    timeframe is (a 5m and a 15m bar sharing an open time are different bars)."""
    return "|".join(
        str(part if part is not None else "").strip().upper()
        for part in (symbol, alert, bar_time, timeframe)
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

    def is_duplicate(
        self,
        symbol: str | None,
        alert: str | None,
        bar_time: str | None,
        timeframe: str | None = None,
    ) -> bool:
        """Return True if this (symbol, alert, bar_time, timeframe) was seen
        within the TTL.

        Records the key as seen (refreshing its timestamp) as a side effect, so a
        first occurrence returns False and subsequent ones within the TTL return
        True. Always evicts expired entries first.
        """
        now = self.clock()
        self._evict_expired(now)
        key = dedupe_key(symbol, alert, bar_time, timeframe)
        seen = key in self._seen
        self._seen[key] = now
        return seen

    def clear(self) -> None:
        self._seen.clear()

    def __len__(self) -> int:  # pragma: no cover - trivial
        return len(self._seen)
