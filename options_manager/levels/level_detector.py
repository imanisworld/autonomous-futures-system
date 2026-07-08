"""options_manager/levels/level_detector.py

Advisory-only local level detector — Increment 15. Pure function of
caller-supplied OHLC bars only -> LevelDetectionResult. Performs no I/O
of any kind: no market-data fetch, no vendor/GEX/Signa dependency, no
broker call, no order placement, no execution.

Detects only what caller-supplied candles already prove: prior-bar,
inside-bar, and outside-bar high/low; prior-day (PDH/PDL) and prior-week
(PWH/PWL) high/low from caller-supplied daily/weekly bars; opening-range
(ORB) high/low from caller-supplied opening-range bars; swing highs/lows
from a caller-supplied bar sequence; and support/resistance clusters
grouped from whichever raw levels were found. Whenever a caller omits an
input (or supplies too few bars for swing detection), this module skips
that category and records a warning -- it never fabricates a level for
data it wasn't given.

Not wired into options_manager/strategies/strat_212.py or
options_manager/scanner -- this is an additive, standalone module. A
caller (e.g. a future row builder) may use its output to populate
LevelFinderInputs/AdapterUnderlyingSnapshot; integration is a separate,
explicitly-scoped increment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class OHLCBar:
    """A single caller-supplied bar's high/low. Nothing here is fetched
    -- every OHLCBar is whatever the caller already has in hand."""

    high: float
    low: float


@dataclass(frozen=True)
class LevelCandidate:
    """One detected level, labeled by which local-structure rule
    produced it (e.g. "prior_high", "pdh", "orb_low", "swing_high",
    "cluster")."""

    level: float
    label: str


@dataclass(kw_only=True)
class LevelDetectionResult:
    """Advisory-only output. Never a broker call, never an order --  a
    pure description of what caller-supplied OHLC alone can prove about
    nearby levels. `levels` holds every individual raw candidate found
    (deterministically ordered); `resistance_levels`/`support_levels`
    are only populated when `current_price` was supplied, since
    otherwise this module cannot know which side of price a level sits
    on. `warnings` records which requested categories could not be
    computed and why -- never a fabricated level."""

    levels: list[LevelCandidate] = field(default_factory=list)
    resistance_levels: tuple[float, ...] = ()
    support_levels: tuple[float, ...] = ()
    warnings: list[str] = field(default_factory=list)


def _swing_levels(bars: tuple[OHLCBar, ...], lookback: int) -> list[LevelCandidate]:
    n = len(bars)
    candidates: list[LevelCandidate] = []
    if n < 2 * lookback + 1:
        return candidates
    for i in range(lookback, n - lookback):
        window_indices = [j for j in range(i - lookback, i + lookback + 1) if j != i]
        if all(bars[i].high > bars[j].high for j in window_indices):
            candidates.append(LevelCandidate(level=bars[i].high, label="swing_high"))
        if all(bars[i].low < bars[j].low for j in window_indices):
            candidates.append(LevelCandidate(level=bars[i].low, label="swing_low"))
    return candidates


def _cluster_values(values: list[float], cluster_distance: float) -> list[float]:
    if not values:
        return []
    ordered = sorted(values)
    clusters: list[list[float]] = [[ordered[0]]]
    for value in ordered[1:]:
        if value - clusters[-1][-1] <= cluster_distance:
            clusters[-1].append(value)
        else:
            clusters.append([value])
    return [sum(group) / len(group) for group in clusters]


def detect_local_levels(
    *,
    current_price: Optional[float] = None,
    prior_bar: Optional[OHLCBar] = None,
    inside_bar: Optional[OHLCBar] = None,
    outside_bar: Optional[OHLCBar] = None,
    prior_day_bar: Optional[OHLCBar] = None,
    prior_week_bar: Optional[OHLCBar] = None,
    opening_range_bars: tuple[OHLCBar, ...] = (),
    swing_bars: tuple[OHLCBar, ...] = (),
    swing_lookback: int = 2,
    cluster_distance: Optional[float] = None,
) -> LevelDetectionResult:
    """Pure function of its explicit inputs -> LevelDetectionResult.

    Every parameter is independently optional: whichever bars/sequences
    are supplied get translated into level candidates; whichever are
    omitted (or, for opening_range_bars/swing_bars, too few to support
    detection) are skipped with a warning rather than guessed at.
    """
    levels: list[LevelCandidate] = []
    warnings: list[str] = []

    if prior_bar is not None:
        levels.append(LevelCandidate(level=prior_bar.high, label="prior_high"))
        levels.append(LevelCandidate(level=prior_bar.low, label="prior_low"))
    else:
        warnings.append("prior_bar not supplied; no prior_high/prior_low")

    if inside_bar is not None:
        levels.append(LevelCandidate(level=inside_bar.high, label="inside_bar_high"))
        levels.append(LevelCandidate(level=inside_bar.low, label="inside_bar_low"))
    else:
        warnings.append("inside_bar not supplied; no inside_bar_high/inside_bar_low")

    if outside_bar is not None:
        levels.append(LevelCandidate(level=outside_bar.high, label="outside_bar_high"))
        levels.append(LevelCandidate(level=outside_bar.low, label="outside_bar_low"))
    else:
        warnings.append("outside_bar not supplied; no outside_bar_high/outside_bar_low")

    if prior_day_bar is not None:
        levels.append(LevelCandidate(level=prior_day_bar.high, label="pdh"))
        levels.append(LevelCandidate(level=prior_day_bar.low, label="pdl"))
    else:
        warnings.append("prior_day_bar not supplied; no pdh/pdl")

    if prior_week_bar is not None:
        levels.append(LevelCandidate(level=prior_week_bar.high, label="pwh"))
        levels.append(LevelCandidate(level=prior_week_bar.low, label="pwl"))
    else:
        warnings.append("prior_week_bar not supplied; no pwh/pwl")

    if opening_range_bars:
        orb_high = max(bar.high for bar in opening_range_bars)
        orb_low = min(bar.low for bar in opening_range_bars)
        levels.append(LevelCandidate(level=orb_high, label="orb_high"))
        levels.append(LevelCandidate(level=orb_low, label="orb_low"))
    else:
        warnings.append("opening_range_bars empty; no orb_high/orb_low")

    if swing_bars:
        swing_candidates = _swing_levels(swing_bars, swing_lookback)
        if swing_candidates:
            levels.extend(swing_candidates)
        else:
            warnings.append(
                f"insufficient swing_bars for swing_lookback={swing_lookback} "
                f"(need at least {2 * swing_lookback + 1}, got {len(swing_bars)}); "
                f"no swing highs/lows"
            )
    else:
        warnings.append("swing_bars empty; no swing highs/lows")

    if cluster_distance is not None and levels:
        clustered = _cluster_values([c.level for c in levels], cluster_distance)
        cluster_candidates = [
            LevelCandidate(level=value, label="cluster") for value in clustered
        ]
    else:
        cluster_candidates = []

    all_levels = levels + cluster_candidates
    all_levels.sort(key=lambda c: (c.level, c.label))

    resistance_levels: tuple[float, ...] = ()
    support_levels: tuple[float, ...] = ()
    if current_price is not None:
        source_values = [c.level for c in (cluster_candidates or levels)]
        resistance_levels = tuple(
            sorted(v for v in source_values if v > current_price)
        )
        support_levels = tuple(
            sorted((v for v in source_values if v < current_price), reverse=True)
        )

    return LevelDetectionResult(
        levels=all_levels,
        resistance_levels=resistance_levels,
        support_levels=support_levels,
        warnings=warnings,
    )
