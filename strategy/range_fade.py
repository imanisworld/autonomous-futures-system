"""Causal range detection and edge-rejection signals for offline evaluation."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Iterable, Optional


@dataclass(frozen=True)
class RangeBar:
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    avg_volume: float
    market_condition: str | None = None
    trend_direction: str | None = None
    trend_strength: str | None = None

    @property
    def relative_volume(self) -> float:
        return self.volume / max(self.avg_volume, 1.0)


@dataclass(frozen=True)
class RangeFadeConfig:
    confirmation_bars: int = 6
    min_touches_per_side: int = 2
    touch_zone_percent: float = 0.20
    entry_zone_percent: float = 0.20
    breakout_buffer_percent: float = 0.08
    max_range_atr: float = 4.0
    min_range_atr: float = 1.25
    stop_buffer_percent: float = 0.08
    max_breakout_volume: float = 1.25
    invalidation_closes: int = 2
    accepted_conditions: tuple[str, ...] = ("CONSOLIDATING", "RANGE_BOUND", "CHOPPY")


@dataclass(frozen=True)
class ActiveRange:
    support: float
    resistance: float
    midpoint: float
    width: float
    started_at: str


@dataclass(frozen=True)
class RangeSignal:
    direction: str
    entry: float
    stop: float
    target: float
    support: float
    resistance: float
    timestamp: str


class RangeTracker:
    """Tracks one frozen range and emits rejection signals without lookahead."""

    def __init__(self, config: RangeFadeConfig | None = None):
        self.config = config or RangeFadeConfig()
        self._bars: deque[RangeBar] = deque(maxlen=self.config.confirmation_bars)
        self.active: Optional[ActiveRange] = None
        self.outside_closes = 0

    def update(self, bar: RangeBar, *, allow_signal: bool = True) -> Optional[RangeSignal]:
        """Process a completed bar. Any signal uses a range built from prior bars."""
        signal = None
        if self.active is not None:
            signal = self._evaluate_active(bar, allow_signal=allow_signal)

        self._bars.append(bar)
        if self.active is None:
            self.active = self._qualify(self._bars)
        return signal

    def _qualify(self, bars: Iterable[RangeBar]) -> Optional[ActiveRange]:
        sample = list(bars)
        if len(sample) < self.config.confirmation_bars:
            return None
        accepted = {value.upper() for value in self.config.accepted_conditions}
        if sum(str(bar.market_condition or "").upper() in accepted for bar in sample) < len(sample) - 1:
            return None
        if sum(str(bar.trend_strength or "").upper() == "STRONG" for bar in sample) > 1:
            return None

        support = min(bar.low for bar in sample)
        resistance = max(bar.high for bar in sample)
        width = resistance - support
        if width <= 0:
            return None

        true_ranges = [bar.high - bar.low for bar in sample]
        avg_range = sum(true_ranges) / len(true_ranges)
        if avg_range <= 0:
            return None
        if width < avg_range * self.config.min_range_atr:
            return None
        if width > avg_range * self.config.max_range_atr:
            return None

        touch = width * self.config.touch_zone_percent
        support_touches = sum(bar.low <= support + touch for bar in sample)
        resistance_touches = sum(bar.high >= resistance - touch for bar in sample)
        if min(support_touches, resistance_touches) < self.config.min_touches_per_side:
            return None

        return ActiveRange(
            support=support,
            resistance=resistance,
            midpoint=(support + resistance) / 2,
            width=width,
            started_at=sample[-1].timestamp,
        )

    def _evaluate_active(self, bar: RangeBar, *, allow_signal: bool) -> Optional[RangeSignal]:
        active = self.active
        assert active is not None
        breakout_buffer = active.width * self.config.breakout_buffer_percent
        above = bar.close > active.resistance + breakout_buffer
        below = bar.close < active.support - breakout_buffer

        if above or below:
            self.outside_closes += 1
            if (
                self.outside_closes >= self.config.invalidation_closes
                or bar.relative_volume >= self.config.max_breakout_volume
            ):
                self.active = None
                self.outside_closes = 0
                self._bars.clear()
            return None

        self.outside_closes = 0
        if not allow_signal:
            return None
        if str(bar.trend_strength or "").upper() == "STRONG":
            return None
        if bar.relative_volume >= self.config.max_breakout_volume:
            return None

        entry_zone = active.width * self.config.entry_zone_percent
        stop_buffer = active.width * self.config.stop_buffer_percent

        bullish_rejection = (
            bar.low <= active.support + entry_zone
            and bar.close > bar.open
            and bar.close > active.support
            and bar.close < active.midpoint
        )
        if bullish_rejection:
            return RangeSignal(
                direction="LONG",
                entry=bar.close,
                stop=active.support - stop_buffer,
                target=active.midpoint,
                support=active.support,
                resistance=active.resistance,
                timestamp=bar.timestamp,
            )

        bearish_rejection = (
            bar.high >= active.resistance - entry_zone
            and bar.close < bar.open
            and bar.close < active.resistance
            and bar.close > active.midpoint
        )
        if bearish_rejection:
            return RangeSignal(
                direction="SHORT",
                entry=bar.close,
                stop=active.resistance + stop_buffer,
                target=active.midpoint,
                support=active.support,
                resistance=active.resistance,
                timestamp=bar.timestamp,
            )
        return None

