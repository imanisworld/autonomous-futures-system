"""MNQ Sustained Trend Continuation v1 — frozen research-only detector.

Controlling documents:
- docs/prereg-mnq-sustained-trend-continuation-v1-2026-09-22.md
- docs/mnq-sustained-trend-continuation-v1-detector-spec-2026-09-22.md

This module is deliberately isolated under research/. It has no broker, risk,
webhook, runtime, environment, or persistence integration.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from math import isfinite
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

from config.futures_contracts import contract_economics


INSTRUMENT = "MNQ"
TICK_SIZE, TICK_VALUE = contract_economics(INSTRUMENT)
ATR_PERIOD = 20
ARM_WINDOW_BARS = 8
DISPLACEMENT_ATR_MULTIPLE = 2.0
MIN_EFFICIENCY = 0.65
MIN_UP_TRANSITIONS = 5
MAX_PULLBACK_BARS = 3
MAX_RETRACE_FRACTION = 0.50
MAX_STOP_TICKS = 120.0
TARGET_R = 2.0
ROUND_TURN_COMMISSION = 1.48
MAX_FILLS_PER_OBSERVATION_DAY = 3

_ET = ZoneInfo("America/New_York")


def observation_day(ts: datetime) -> str:
    """CME-style observation day used elsewhere in the project: 18:00 ET roll."""
    if ts.tzinfo is None:
        raise ValueError("observation_day requires an offset-aware datetime")
    return (ts.astimezone(_ET) + timedelta(hours=6)).date().isoformat()


def _finite(value: Any) -> float:
    out = float(value)
    if not isfinite(out):
        raise ValueError("non-finite price")
    return out


def _bar_ts(bar: Mapping[str, Any]) -> str:
    value = bar.get("timestamp", bar.get("ts"))
    if value is None:
        raise ValueError("bar missing timestamp/ts")
    return str(value)


def true_range(bar: Mapping[str, Any], prev_close: Optional[float]) -> float:
    high = _finite(bar["high"])
    low = _finite(bar["low"])
    if high < low:
        raise ValueError("bar high below low")
    if prev_close is None:
        return high - low
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


@dataclass(frozen=True)
class ArmMetrics:
    net_displacement: float
    path: float
    efficiency: float
    up_transitions: int
    atr20: float
    passes: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute_arm_metrics(
    bars: list[Mapping[str, Any]],
    atr20: float,
) -> ArmMetrics:
    """Compute the exact frozen 8-bar arm metrics.

    atr20 is supplied by the detector's causal Wilder/RMA ATR(20) state.
    """
    if len(bars) != ARM_WINDOW_BARS:
        raise ValueError(f"arm requires exactly {ARM_WINDOW_BARS} bars")
    atr = _finite(atr20)
    if atr <= 0:
        raise ValueError("ATR20 must be positive")

    first_open = _finite(bars[0]["open"])
    closes = [_finite(bar["close"]) for bar in bars]
    net = closes[-1] - first_open
    path = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
    efficiency = net / path if path > 0 else 0.0
    ups = sum(closes[i] > closes[i - 1] for i in range(1, len(closes)))

    passes = bool(
        net >= DISPLACEMENT_ATR_MULTIPLE * atr
        and efficiency >= MIN_EFFICIENCY
        and ups >= MIN_UP_TRANSITIONS
        and closes[-1] > first_open
    )
    return ArmMetrics(
        net_displacement=net,
        path=path,
        efficiency=efficiency,
        up_transitions=ups,
        atr20=atr,
        passes=passes,
    )


@dataclass
class Episode:
    arm_bar_ts: str
    arm_close_time: datetime
    arm_window_open: float
    arm_close: float
    atr20: float
    net_displacement: float
    path: float
    efficiency: float
    up_transitions: int
    arm_move: float
    midpoint: float
    previous_15m_close: float
    state: str = "ARMED"
    pullback_count: int = 0
    pullback_low: Optional[float] = None
    pullback_high: Optional[float] = None
    has_qualifying_pullback: bool = False
    pullback_ready_time: Optional[datetime] = None


@dataclass(frozen=True)
class DetectorEvent:
    event: str
    event_ts: str
    source_bar_ts: str
    arm_bar_ts: str
    arm_window_open: float
    arm_close: float
    atr20: float
    net_displacement: float
    path: float
    efficiency: float
    up_transitions: int
    arm_move: float
    midpoint: float
    pullback_count: int
    pullback_low: Optional[float]
    pullback_high: Optional[float]
    planned_entry: Optional[float] = None
    modeled_fill: Optional[float] = None
    stop: Optional[float] = None
    target: Optional[float] = None
    stop_ticks: Optional[float] = None
    session: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _event(
    name: str,
    event_time: datetime,
    source_bar: Mapping[str, Any],
    ep: Episode,
    **extra: Any,
) -> DetectorEvent:
    return DetectorEvent(
        event=name,
        event_ts=event_time.isoformat(),
        source_bar_ts=_bar_ts(source_bar),
        arm_bar_ts=ep.arm_bar_ts,
        arm_window_open=ep.arm_window_open,
        arm_close=ep.arm_close,
        atr20=ep.atr20,
        net_displacement=ep.net_displacement,
        path=ep.path,
        efficiency=ep.efficiency,
        up_transitions=ep.up_transitions,
        arm_move=ep.arm_move,
        midpoint=ep.midpoint,
        pullback_count=ep.pullback_count,
        pullback_low=ep.pullback_low,
        pullback_high=ep.pullback_high,
        session=str(source_bar.get("session")) if source_bar.get("session") is not None else None,
        **extra,
    )


class SustainedTrendContinuationV1:
    """Pure causal state machine for the frozen v1 LONG detector."""

    def __init__(self) -> None:
        self._history: deque[dict[str, Any]] = deque(maxlen=ATR_PERIOD)
        self._prev_close: Optional[float] = None
        self._atr20: Optional[float] = None
        self._atr_seed: list[float] = []
        self.episode: Optional[Episode] = None

    @property
    def atr20(self) -> Optional[float]:
        return self._atr20

    def _update_atr(self, bar: Mapping[str, Any]) -> None:
        tr = true_range(bar, self._prev_close)
        close = _finite(bar["close"])
        if self._atr20 is None:
            self._atr_seed.append(tr)
            if len(self._atr_seed) == ATR_PERIOD:
                self._atr20 = sum(self._atr_seed) / ATR_PERIOD
        else:
            self._atr20 = (
                self._atr20 * (ATR_PERIOD - 1) + tr
            ) / ATR_PERIOD
        self._prev_close = close

    def on_15m(
        self,
        bar: Mapping[str, Any],
        *,
        close_time: datetime,
    ) -> list[DetectorEvent]:
        if close_time.tzinfo is None:
            raise ValueError("close_time must be offset-aware")

        prior_close = self._prev_close
        try:
            self._update_atr(bar)
            normalized = {
                "timestamp": _bar_ts(bar),
                "open": _finite(bar["open"]),
                "high": _finite(bar["high"]),
                "low": _finite(bar["low"]),
                "close": _finite(bar["close"]),
                "session": bar.get("session"),
            }
        except (KeyError, TypeError, ValueError):
            return []
        self._history.append(normalized)

        if self.episode is not None:
            ep = self.episode
            if close_time <= ep.arm_close_time:
                return []

            ep.pullback_count += 1
            if ep.pullback_count >= 4:
                out = [_event("ARM_EXPIRED", close_time, normalized, ep)]
                self.episode = None
                return out

            high = normalized["high"]
            low = normalized["low"]
            close = normalized["close"]
            open_ = normalized["open"]
            ep.pullback_low = low if ep.pullback_low is None else min(ep.pullback_low, low)
            ep.pullback_high = high if ep.pullback_high is None else max(ep.pullback_high, high)
            compare_close = (
                ep.previous_15m_close if ep.previous_15m_close is not None else prior_close
            )
            qualifies = close < open_ or (
                compare_close is not None and close < compare_close
            )
            ep.has_qualifying_pullback = ep.has_qualifying_pullback or qualifies
            ep.previous_15m_close = close

            if close < ep.midpoint:
                out = [_event("ARM_INVALIDATED_MIDPOINT", close_time, normalized, ep)]
                self.episode = None
                return out

            retrace = ep.arm_close - float(ep.pullback_low)
            if retrace > MAX_RETRACE_FRACTION * ep.arm_move:
                out = [_event("ARM_INVALIDATED_RETRACE", close_time, normalized, ep)]
                self.episode = None
                return out

            if ep.has_qualifying_pullback and ep.state == "ARMED":
                ep.state = "PULLBACK_READY"
                ep.pullback_ready_time = close_time
                return [_event("PULLBACK_READY", close_time, normalized, ep)]
            return []

        if self._atr20 is None or len(self._history) < ARM_WINDOW_BARS:
            return []

        window = list(self._history)[-ARM_WINDOW_BARS:]
        try:
            metrics = compute_arm_metrics(window, self._atr20)
        except (TypeError, ValueError):
            return []
        if not metrics.passes:
            return []

        arm_open = _finite(window[0]["open"])
        arm_close = _finite(window[-1]["close"])
        arm_move = arm_close - arm_open
        if arm_move <= 0:
            return []
        ep = Episode(
            arm_bar_ts=_bar_ts(normalized),
            arm_close_time=close_time,
            arm_window_open=arm_open,
            arm_close=arm_close,
            atr20=metrics.atr20,
            net_displacement=metrics.net_displacement,
            path=metrics.path,
            efficiency=metrics.efficiency,
            up_transitions=metrics.up_transitions,
            arm_move=arm_move,
            midpoint=arm_open + 0.5 * arm_move,
            previous_15m_close=arm_close,
        )
        self.episode = ep
        return [_event("ARMED", close_time, normalized, ep)]

    def on_5m(
        self,
        bar: Mapping[str, Any],
        *,
        close_time: datetime,
    ) -> list[DetectorEvent]:
        if close_time.tzinfo is None:
            raise ValueError("close_time must be offset-aware")
        ep = self.episode
        if (
            ep is None
            or ep.state != "PULLBACK_READY"
            or ep.pullback_ready_time is None
            or close_time <= ep.pullback_ready_time
            or ep.pullback_high is None
            or ep.pullback_low is None
        ):
            return []

        try:
            close = _finite(bar["close"])
            _finite(bar["high"])
            _finite(bar["low"])
        except (KeyError, TypeError, ValueError):
            return []
        if close <= ep.pullback_high:
            return []

        planned_entry = close
        modeled_fill = planned_entry + TICK_SIZE
        stop = float(ep.pullback_low) - TICK_SIZE
        risk_points = modeled_fill - stop
        stop_ticks = risk_points / TICK_SIZE if TICK_SIZE > 0 else float("inf")

        extra = {
            "planned_entry": planned_entry,
            "modeled_fill": modeled_fill,
            "stop": stop,
            "target": None,
            "stop_ticks": stop_ticks,
        }
        if (
            not all(isfinite(v) for v in (planned_entry, modeled_fill, stop, stop_ticks))
            or risk_points <= 0
            or stop_ticks > MAX_STOP_TICKS + 1e-9
        ):
            out = [_event("STOP_CAP_REJECTED", close_time, bar, ep, **extra)]
            self.episode = None
            return out

        target = modeled_fill + TARGET_R * risk_points
        extra["target"] = target
        out = [_event("TRIGGERED", close_time, bar, ep, **extra)]
        self.episode = None
        return out


@dataclass
class CapacityGate:
    """Pure research-harness capacity accounting."""

    max_fills_per_day: int = MAX_FILLS_PER_OBSERVATION_DAY
    open_position: bool = False
    fills_by_day: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def classify_trigger(self, day: str) -> str:
        if self.open_position:
            return "SKIPPED_BUSY"
        if self.fills_by_day.get(day, 0) >= self.max_fills_per_day:
            return "SKIPPED_MAX_TRADES"
        self.open_position = True
        self.fills_by_day[day] = self.fills_by_day.get(day, 0) + 1
        return "FILLED"

    def mark_closed(self) -> None:
        self.open_position = False


@dataclass
class ResearchTrade:
    observation_day: str
    session: str
    arm_bar_ts: str
    trigger_ts: str
    entry: float
    stop: float
    target: float
    stop_ticks: float
    result: str = "OPEN"
    exit_ts: Optional[str] = None
    exit_price: Optional[float] = None
    gross_pnl: Optional[float] = None
    commission: Optional[float] = None
    net_pnl: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def resolve_trade_on_bar(
    trade: ResearchTrade,
    bar: Mapping[str, Any],
    *,
    close_time: datetime,
) -> Optional[ResearchTrade]:
    """Resolve on a strictly later 5m bar, stop-first on an ambiguous straddle."""
    if close_time.tzinfo is None:
        raise ValueError("close_time must be offset-aware")
    try:
        high = _finite(bar["high"])
        low = _finite(bar["low"])
    except (KeyError, TypeError, ValueError):
        return None

    stop_hit = low <= trade.stop
    target_hit = high >= trade.target
    if not stop_hit and not target_hit:
        return None

    exit_price = trade.stop if stop_hit else trade.target
    result = "LOSS" if stop_hit else "WIN"
    gross_ticks = (exit_price - trade.entry) / TICK_SIZE
    gross = gross_ticks * TICK_VALUE
    trade.result = result
    trade.exit_ts = close_time.isoformat()
    trade.exit_price = exit_price
    trade.gross_pnl = round(gross, 2)
    trade.commission = ROUND_TURN_COMMISSION
    trade.net_pnl = round(gross - ROUND_TURN_COMMISSION, 2)
    return trade
