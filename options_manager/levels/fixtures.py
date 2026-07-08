"""options_manager/levels/fixtures.py

Deterministic level-detector proof fixtures — Increment 16. Static,
in-code fixture cases that exercise every category the local level
detector (options_manager/levels/level_detector.py, Increment 15)
supports, without ever fetching anything. Every fixture case is a fixed,
hardcoded OHLCBar (or bar sequence) -- never loaded from a file, network
call, or live data source.

This module performs no I/O of any kind: no candle fetch, no
option-chain fetch, no market-data fetch, no broker call, no order
placement, no execution, no alert sending, no file reads/writes at
runtime. It never runs the scanner or the strategy validator, does not
implement GEX/gamma or Signa logic, and does not import
replay/replay_engine.py, the live context.market_context
loader, alert_ranker, options_companion, execution, webhook, broker
systems, or risk/risk_engine.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .level_detector import LevelDetectionResult, OHLCBar, detect_local_levels


@dataclass(frozen=True)
class LevelFixtureCase:
    """One named, fixed set of detect_local_levels() keyword arguments."""

    name: str
    detect_kwargs: dict = field(default_factory=dict)


@dataclass(kw_only=True)
class LevelFixtureSummary:
    """Deterministic rollup of a level-detector fixture run. Purely
    computed from the per-case results -- no side effects."""

    total_cases: int
    total_levels_found: int
    total_warnings: int
    levels_by_case: dict[str, int]
    warnings_by_case: dict[str, int]


def _prior_candle_case() -> LevelFixtureCase:
    return LevelFixtureCase(
        name="prior_candle",
        detect_kwargs=dict(prior_bar=OHLCBar(high=100.0, low=95.0)),
    )


def _inside_bar_case() -> LevelFixtureCase:
    return LevelFixtureCase(
        name="inside_bar",
        detect_kwargs=dict(inside_bar=OHLCBar(high=98.5, low=96.5)),
    )


def _outside_bar_case() -> LevelFixtureCase:
    return LevelFixtureCase(
        name="outside_bar",
        detect_kwargs=dict(outside_bar=OHLCBar(high=108.0, low=93.0)),
    )


def _pdh_pdl_case() -> LevelFixtureCase:
    return LevelFixtureCase(
        name="pdh_pdl",
        detect_kwargs=dict(prior_day_bar=OHLCBar(high=110.0, low=90.0)),
    )


def _pwh_pwl_case() -> LevelFixtureCase:
    return LevelFixtureCase(
        name="pwh_pwl",
        detect_kwargs=dict(prior_week_bar=OHLCBar(high=120.0, low=85.0)),
    )


def _orb_case() -> LevelFixtureCase:
    bars = (
        OHLCBar(high=101.0, low=99.0),
        OHLCBar(high=103.0, low=98.0),
        OHLCBar(high=102.0, low=97.5),
    )
    return LevelFixtureCase(name="orb", detect_kwargs=dict(opening_range_bars=bars))


def _swing_case() -> LevelFixtureCase:
    bars = (
        OHLCBar(high=100.0, low=95.0),
        OHLCBar(high=102.0, low=96.0),
        OHLCBar(high=105.0, low=94.0),
        OHLCBar(high=101.0, low=97.0),
        OHLCBar(high=99.0, low=98.0),
    )
    return LevelFixtureCase(
        name="swing", detect_kwargs=dict(swing_bars=bars, swing_lookback=2)
    )


def _clustering_case() -> LevelFixtureCase:
    return LevelFixtureCase(
        name="clustering",
        detect_kwargs=dict(
            current_price=100.0,
            prior_bar=OHLCBar(high=103.0, low=97.0),
            prior_day_bar=OHLCBar(high=103.2, low=96.8),
            cluster_distance=0.5,
        ),
    )


def _insufficient_data_case() -> LevelFixtureCase:
    return LevelFixtureCase(name="insufficient_data", detect_kwargs=dict())


_FIXTURE_CASE_BUILDERS = (
    _prior_candle_case,
    _inside_bar_case,
    _outside_bar_case,
    _pdh_pdl_case,
    _pwh_pwl_case,
    _orb_case,
    _swing_case,
    _clustering_case,
    _insufficient_data_case,
)


def build_level_detector_fixture_dataset() -> dict[str, LevelFixtureCase]:
    """Returns a fresh dict of the 9 fixed level-detector fixture cases,
    keyed by name, covering every category detect_local_levels()
    supports plus a fail-closed (insufficient-data) case. Each call
    rebuilds the cases from the individual builder functions above
    rather than returning a shared/cached dict, so nothing here can
    accumulate mutated state across calls (the cases themselves are
    also frozen dataclasses)."""
    return {builder().name: builder() for builder in _FIXTURE_CASE_BUILDERS}


def run_level_detector_fixture_dataset(
    cases: dict[str, LevelFixtureCase] | None = None,
) -> dict[str, LevelDetectionResult]:
    """Runs `cases` (defaulting to build_level_detector_fixture_dataset())
    through detect_local_levels() and returns one LevelDetectionResult
    per case name. Never fabricates a case; simply calls the pure
    detector with each case's fixed kwargs."""
    if cases is None:
        cases = build_level_detector_fixture_dataset()
    return {
        name: detect_local_levels(**case.detect_kwargs) for name, case in cases.items()
    }


def summarize_level_detector_fixture_dataset(
    results: dict[str, LevelDetectionResult] | None = None,
) -> LevelFixtureSummary:
    """Deterministic rollup of a level-detector fixture run (or
    `results`, if supplied)."""
    if results is None:
        results = run_level_detector_fixture_dataset()

    levels_by_case = {name: len(result.levels) for name, result in results.items()}
    warnings_by_case = {name: len(result.warnings) for name, result in results.items()}

    return LevelFixtureSummary(
        total_cases=len(results),
        total_levels_found=sum(levels_by_case.values()),
        total_warnings=sum(warnings_by_case.values()),
        levels_by_case=levels_by_case,
        warnings_by_case=warnings_by_case,
    )
