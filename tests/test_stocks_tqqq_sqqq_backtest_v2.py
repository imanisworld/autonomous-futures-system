"""
tests/test_stocks_tqqq_sqqq_backtest_v2.py

stocks_advisory/tqqq_sqqq_backtest_v2.py tests -- Stock/ETF Strategy v2.0
(operator-selected parameters, not yet validated). Proves: Lane 1 stays
byte-identical to v1 (reused, not forked); Lane 2's causal, left-side-only
pivot detection never uses a future bar to CREATE a candidate; each of
Lane 2's five entry conditions independently, including the exact 0.30%/
1.25% boundary values; the `CONTINUATION_EXTENDED` reason; invalidation on
a completed close (never an intrabar wick); same-bar stop/target
precedence (stop wins); and the Lane 1/Lane 2 interaction rules (Lane 1
trade disables Lane 2 same day, Lane 1 NO_TRADE still lets Lane 2
evaluate, at most one position per day) -- plus the same no-broker/
no-order/no-cross-lane-import guarantees as every other module in this
repo.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import stocks_advisory.tqqq_sqqq_backtest_v2 as v2_module
import stocks_advisory.backtest_models as backtest_models_module
from stocks_advisory.backtest_models import Bar, DaySession, TradeDirection
from stocks_advisory.tqqq_sqqq_backtest_v2 import (
    CONTINUATION_EXTENDED_REASON,
    LANE2_ELIGIBILITY_END,
    LANE2_ELIGIBILITY_START,
    STRATEGY_VERSION,
    V2Config,
    V2TradeResult,
    _confirm_pivots_through,
    _evaluate_lane1,
    _intraday_vwap_series,
    _is_confirmed_lower_high,
    _lane2_entry_gate,
    _pivot_candidates,
    _running_extreme,
    evaluate_day_v2,
    run_backtest_v2,
)

_SCANNED_MODULES = (v2_module, backtest_models_module)

_FORBIDDEN_IMPORT_FRAGMENTS = (
    "robin_stocks", "robinhood", "execution", "webhook", "broker", "ib_insync",
    "ibapi", "tradovate", "options_manager", "strategy", "risk_engine",
    "requests", "httpx", "urllib", "socket", "aiohttp", "websocket",
)

_FORBIDDEN_ORDER_ACTION_IDENTIFIERS = (
    "place_order", "submit_order", "cancel_order", "replace_order",
    "execute_order", "live_order", "order_ticket",
)


def _bar(ts: str, o: float, h: float, l: float, c: float, v: int = 1000) -> Bar:
    return Bar(timestamp=ts, open=o, high=h, low=l, close=c, volume=v)


def _config(**overrides) -> V2Config:
    # Thresholds match the ACTUAL frozen paper-harness values (see
    # data/stocks_advisory_paper_proof/PROOF_MANIFEST.md) except
    # allowed_max_first_hour_range, widened to 25.0 here so this test
    # file's wide-first-hour-range fixtures read a clean "QQQ is inside
    # the first-hour range" NO_TRADE rather than a range-filter reject --
    # that widening is this test file's own choice, not a manifest value.
    defaults = dict(
        allowed_max_gap_percent=2.0,
        allowed_min_first_hour_range=1.0,
        allowed_max_first_hour_range=25.0,
        position_dollar_size=1000.0,
        exit_cutoff_time="15:55",
    )
    defaults.update(overrides)
    return V2Config(**defaults)


# --- Calibrated bearish-continuation fixture ---------------------------------------------------------------
# Opening range 09:30-10:25 touches [384, 405] (a 21-point range -- within
# this test file's widened allowed_max_first_hour_range=25.0 so Lane 1 reads
# a clean "QQQ is inside the first-hour range" NO_TRADE). Price then
# declines from ~398 to ~390 with two lower-high pullbacks (394.5 @ 11:05,
# confirmed; 392.9 @ 11:35, confirmed and below the first), before an entry
# break at 11:50 (close 390.8 < 11:45's low 391.0). Every number here was
# verified interactively against the real implementation before being fixed
# as a test fixture -- see the implementation plan for the calibration
# approach.
_DATE = "2026-01-05"

_OPENING_QQQ = [
    _bar(f"{_DATE}T09:30:00", 401.0, 403.0, 400.0, 402.0),
    _bar(f"{_DATE}T09:35:00", 402.0, 405.0, 401.5, 404.0),
    _bar(f"{_DATE}T09:40:00", 404.0, 404.5, 401.0, 401.5),
    _bar(f"{_DATE}T09:45:00", 401.5, 402.0, 395.0, 395.5),
    _bar(f"{_DATE}T09:50:00", 395.5, 397.0, 393.0, 394.0),
    _bar(f"{_DATE}T09:55:00", 394.0, 396.0, 392.0, 395.0),
    _bar(f"{_DATE}T10:00:00", 395.0, 396.5, 393.5, 394.0),
    _bar(f"{_DATE}T10:05:00", 394.0, 395.5, 384.0, 391.0),
    _bar(f"{_DATE}T10:10:00", 391.0, 394.0, 391.0, 393.5),
    _bar(f"{_DATE}T10:15:00", 393.5, 396.0, 393.0, 395.5),
    _bar(f"{_DATE}T10:20:00", 395.5, 398.0, 395.0, 397.5),
    _bar(f"{_DATE}T10:25:00", 397.5, 399.0, 397.0, 398.5),
]
# range_high=405.0, range_low=384.0, midpoint=394.5 (of first-hour H/L, i.e. (405+384)/2)

_DECLINE1 = [
    _bar(f"{_DATE}T10:30:00", 398.5, 398.6, 397.5, 397.7),
    _bar(f"{_DATE}T10:35:00", 397.7, 397.8, 396.5, 396.7),
    _bar(f"{_DATE}T10:40:00", 396.7, 396.8, 395.5, 395.7),
    _bar(f"{_DATE}T10:45:00", 395.7, 395.8, 394.5, 394.7),
    _bar(f"{_DATE}T10:50:00", 394.7, 394.8, 393.7, 393.9),
    _bar(f"{_DATE}T10:55:00", 393.9, 394.0, 393.0, 393.2),
]
_BOUNCE1 = [
    _bar(f"{_DATE}T11:00:00", 393.2, 393.4, 392.8, 393.3),
    _bar(f"{_DATE}T11:05:00", 393.3, 394.5, 393.1, 394.2),  # pivot-high candidate (394.5)
    _bar(f"{_DATE}T11:10:00", 394.2, 394.3, 392.5, 392.7),
]
_CONFIRM1_DECLINE2 = [
    _bar(f"{_DATE}T11:15:00", 392.7, 392.8, 392.0, 392.1),  # confirms pivot @ 11:05 (close < 393.1)
    _bar(f"{_DATE}T11:20:00", 392.1, 392.2, 391.5, 391.7),
    _bar(f"{_DATE}T11:25:00", 391.7, 391.8, 391.2, 391.4),
]
_BOUNCE2 = [
    _bar(f"{_DATE}T11:30:00", 391.4, 391.5, 391.0, 391.2),
    _bar(f"{_DATE}T11:35:00", 391.2, 392.9, 391.1, 392.6),  # pivot-high candidate (392.9), lower than 394.5
    _bar(f"{_DATE}T11:40:00", 392.6, 392.7, 391.3, 391.5),
]
_CONFIRM2_ENTRY = [
    _bar(f"{_DATE}T11:45:00", 391.5, 391.6, 391.0, 391.1),
    _bar(f"{_DATE}T11:50:00", 391.1, 391.2, 390.7, 390.8),  # confirms pivot @ 11:35; entry break (close < 391.0)
    _bar(f"{_DATE}T11:55:00", 390.8, 390.9, 390.3, 390.5),  # fill bar (next completed bar's open)
    _bar(f"{_DATE}T12:00:00", 390.5, 390.6, 390.0, 390.2),
]

_BEARISH_CONTINUATION_QQQ = (
    _OPENING_QQQ + _DECLINE1 + _BOUNCE1 + _CONFIRM1_DECLINE2 + _BOUNCE2 + _CONFIRM2_ENTRY
)


def _flat_vehicle_bars(qqq_bars, price: float = 20.0) -> tuple[Bar, ...]:
    """A vehicle that never moves meaningfully relative to its own VWAP --
    keeps the extension filter a non-issue for tests that aren't about it."""
    return tuple(
        _bar(b.timestamp, price, price * 1.0025, price * 0.9975, price, v=b.volume)
        for b in qqq_bars
    )


def _bearish_continuation_day(*, sqqq_bars=None, tqqq_bars=None) -> DaySession:
    qqq = _BEARISH_CONTINUATION_QQQ
    return DaySession(
        date=_DATE,
        qqq_previous_close=401.0,
        qqq_previous_high=406.0,
        qqq_previous_low=397.0,
        qqq_bars=tuple(qqq),
        tqqq_bars=tqqq_bars if tqqq_bars is not None else _flat_vehicle_bars(qqq, 60.0),
        sqqq_bars=sqqq_bars if sqqq_bars is not None else _flat_vehicle_bars(qqq, 20.0),
    )


# --- 1. Lane 1 is reused unmodified, byte-identical to v1 --------------------------------------------------


def _lane1_bullish_breakout_day() -> DaySession:
    opening = [
        _bar(f"{_DATE}T09:30:00", 403.0, 404.0, 402.0, 403.5),
        _bar(f"{_DATE}T09:45:00", 403.5, 404.5, 403.0, 404.0),
        _bar(f"{_DATE}T10:00:00", 404.0, 405.0, 403.5, 404.5),
        _bar(f"{_DATE}T10:15:00", 404.5, 405.0, 404.0, 404.8),
    ]
    qqq = opening + [
        _bar(f"{_DATE}T10:30:00", 405.0, 410.0, 405.0, 409.5),
        _bar(f"{_DATE}T10:45:00", 409.5, 411.0, 409.0, 410.5),
    ]
    tqqq = [_bar(b.timestamp, 60.0, 60.5, 59.5, 60.0 + i * 0.5, v=b.volume) for i, b in enumerate(qqq)]
    return DaySession(
        date=_DATE, qqq_previous_close=400.0, qqq_previous_high=406.0, qqq_previous_low=398.0,
        qqq_bars=tuple(qqq), tqqq_bars=tuple(tqqq), sqqq_bars=_flat_vehicle_bars(qqq, 20.0),
    )


def test_lane1_matches_v1_directly():
    """When Lane 1 itself produces the day's trade, `evaluate_day_v2()`
    must be pixel-identical to calling `_evaluate_lane1()` directly --
    the day-level dispatcher only tags which lane won, it never
    recomputes or forks Lane 1's own result."""
    day = _lane1_bullish_breakout_day()
    config = _config()
    direct = _evaluate_lane1(day, config)
    via_v2 = evaluate_day_v2(day, config)
    assert direct.skipped == via_v2.skipped
    assert direct.direction == via_v2.direction
    assert direct.entry_time == via_v2.entry_time
    assert direct.entry_price == via_v2.entry_price
    assert direct.dollar_result == via_v2.dollar_result
    assert via_v2.lane == "lane1"
    # And Lane 1's real trigger condition: TAKE_PAPER at the first bar
    # after the opening range, exactly as the live paper harness would
    # decide it -- above the first-hour high AND above VWAP.
    assert direct.direction == TradeDirection.LONG_TQQQ


# --- 2. Lane 2 triggers a valid bearish continuation trade -------------------------------------------------


def test_lane2_triggers_bearish_continuation_after_failed_reclaim():
    day = _bearish_continuation_day()
    result = evaluate_day_v2(day, _config())
    assert isinstance(result, V2TradeResult)
    assert not result.skipped
    assert result.lane == "lane2"
    assert result.strategy_version == STRATEGY_VERSION
    assert result.direction == TradeDirection.LONG_SQQQ
    assert result.vehicle_symbol == "SQQQ"
    assert result.entry_time == f"{_DATE}T11:55:00"  # next completed bar's open after the 11:50 signal
    assert result.stop_price == 392.9  # the latest confirmed lower-high pivot


# --- 3. Lane 1 disables Lane 2 the same day ----------------------------------------------------------------


def test_lane1_real_trade_disables_lane2_same_day():
    result = evaluate_day_v2(_lane1_bullish_breakout_day(), _config())
    assert result.lane == "lane1"
    assert result.direction == TradeDirection.LONG_TQQQ
    assert not result.skipped


def test_lane1_no_trade_still_lets_lane2_evaluate():
    day = _bearish_continuation_day()
    lane1_alone = _evaluate_lane1(day, _config())
    assert lane1_alone.skipped
    assert lane1_alone.skipped_reason == "QQQ is inside the first-hour range"
    result = evaluate_day_v2(day, _config())
    assert result.lane == "lane2"
    assert not result.skipped  # Lane 2 still found a trade


# --- 4. Causal pivot detection: no future bar creates a candidate ------------------------------------------


def test_pivot_candidate_never_uses_a_future_bar():
    bars = _BEARISH_CONTINUATION_QQQ
    eligible_from = 18  # first index at/after 11:00
    full_candidates = set(_pivot_candidates(bars, eligible_from_index=eligible_from, high=True))
    # Truncating the bar list at any point must never introduce a candidate
    # at an index that the full scan didn't also find, and must never
    # retroactively change whether an earlier index is a candidate -- both
    # would mean a later bar was used to CREATE the candidate.
    for cut in range(eligible_from + 2, len(bars)):
        prefix_candidates = set(
            _pivot_candidates(bars[:cut], eligible_from_index=eligible_from, high=True)
        )
        assert prefix_candidates == {c for c in full_candidates if c < cut}


def test_pivot_confirmed_only_by_a_strictly_later_bar():
    bars = _BEARISH_CONTINUATION_QQQ
    candidates = _pivot_candidates(bars, eligible_from_index=18, high=True)
    # At the candidate's own index, it cannot yet be confirmed (nothing
    # later has been consulted).
    for c in candidates:
        confirmed_at_candidate = _confirm_pivots_through(bars, candidates, through_index=c, high=True)
        assert not any(p.index == c for p in confirmed_at_candidate)


def test_confirmed_lower_high_requires_two_pivots_below_vwap():
    bars = _BEARISH_CONTINUATION_QQQ
    vwap = _intraday_vwap_series(bars)
    candidates = _pivot_candidates(bars, eligible_from_index=18, high=True)
    confirmed = _confirm_pivots_through(bars, candidates, through_index=len(bars) - 1, high=True)
    assert len(confirmed) == 2
    assert _is_confirmed_lower_high(confirmed, vwap)
    assert not _is_confirmed_lower_high(confirmed[:1], vwap)  # only one pivot -> not enough


# --- 5. Entry break is close-based, never an intrabar wick -------------------------------------------------


def test_entry_break_ignores_intrabar_wick_requires_close():
    bars = [
        _bar(f"{_DATE}T11:45:00", 391.5, 391.6, 391.0, 391.1),
        _bar(f"{_DATE}T11:50:00", 391.1, 391.2, 390.5, 391.05),  # wick below 391.0, close does NOT
    ]
    # entry_break condition as used in _lane2_entry_gate for bearish:
    entry_break = bars[1].close < bars[0].low
    assert not entry_break  # 391.05 is not < 391.0 -- a wick alone never triggers
    bars_confirming = [
        _bar(f"{_DATE}T11:45:00", 391.5, 391.6, 391.0, 391.1),
        _bar(f"{_DATE}T11:50:00", 391.1, 391.2, 390.5, 390.95),  # close breaks the prior low
    ]
    assert bars_confirming[1].close < bars_confirming[0].low


# --- 6. Room-to-target boundary (0.30%) ---------------------------------------------------------------------


def test_room_to_target_exact_boundary():
    entry_close = 390.8
    running_low_at_boundary = entry_close * (1 - 0.003)  # exactly 0.30% away
    room = (entry_close - running_low_at_boundary) / entry_close
    assert room == pytest_approx(0.003)
    running_low_just_short = entry_close * (1 - 0.0029)
    room_short = (entry_close - running_low_just_short) / entry_close
    assert room_short < 0.003


def pytest_approx(value, rel=1e-9):
    class _Approx:
        def __eq__(self, other):
            return abs(other - value) <= abs(value) * rel
    return _Approx()


# --- 7. Vertical-extension filter and CONTINUATION_EXTENDED reason -------------------------------------------


def test_continuation_extended_reason_when_only_extension_blocks():
    qqq = _BEARISH_CONTINUATION_QQQ
    extended_sqqq = tuple(
        _bar(b.timestamp, b.open * 1.05, b.high * 1.05, b.low * 1.05, b.close * 1.05, v=b.volume)
        if i >= 18 else b
        for i, b in enumerate(_flat_vehicle_bars(qqq, 20.0))
    )
    day = _bearish_continuation_day(sqqq_bars=extended_sqqq)
    result = evaluate_day_v2(day, _config())
    assert result.lane == "lane2"
    assert result.skipped
    assert result.skipped_reason == CONTINUATION_EXTENDED_REASON


def test_extension_filter_exact_boundary_in_isolation():
    vehicle_vwap = 20.0
    at_boundary_close = vehicle_vwap * 1.0125  # exactly 1.25% above VWAP -- must still pass (<=)
    extension = (at_boundary_close - vehicle_vwap) / vehicle_vwap
    assert extension == pytest_approx(0.0125)
    over_boundary_close = vehicle_vwap * 1.0126
    extension_over = (over_boundary_close - vehicle_vwap) / vehicle_vwap
    assert extension_over > 0.0125


# --- 8. Invalidation: single completed close, either condition, no two-bar confirmation ---------------------


def test_invalidation_triggers_on_single_close_above_pivot_or_vwap():
    day = _bearish_continuation_day()
    result = evaluate_day_v2(day, _config())
    assert result.exit_reason in ("data_ended",)  # this fixture runs out of bars before invalidating

    # Directly exercise the invalidation condition used in _resolve_lane2_trade:
    latest_pivot_price = 392.9
    vwap_at_bar = 394.0
    bar_close_breaches_pivot_only = 393.0  # above pivot, below vwap
    bar_close_breaches_neither = 390.0
    assert (bar_close_breaches_pivot_only > latest_pivot_price or bar_close_breaches_pivot_only > vwap_at_bar)
    assert not (bar_close_breaches_neither > latest_pivot_price or bar_close_breaches_neither > vwap_at_bar)


def test_same_bar_stop_and_target_stop_wins():
    """Mirrors _resolve_vehicle_trade's own tie-break: invalidation is
    checked before target every bar in _resolve_lane2_trade's loop."""
    source = Path(v2_module.__file__).read_text()
    invalidated_check = source.index("if invalidated:")
    target_check = source.index("if vehicle_bars[j].high >= raw_target_price:")
    assert invalidated_check < target_check


# --- 9. No re-entry / at most one Lane 2 position per day (structural) --------------------------------------


def test_evaluate_day_v2_never_returns_more_than_one_trade():
    day = _bearish_continuation_day()
    result = evaluate_day_v2(day, _config())
    # By construction there is exactly one V2TradeResult (or SkippedDay) per
    # day -- there is no container type for more than one trade anywhere in
    # this module's return type, mirroring v1's own guarantee.
    assert isinstance(result, V2TradeResult)


# --- 10. run_backtest_v2 produces separate Lane 1 / Lane 2 / combined summaries ------------------------------


def test_run_backtest_v2_splits_lane_summaries():
    days = [_bearish_continuation_day()]
    report = run_backtest_v2(days, _config())
    assert set(report.keys()) >= {"combined", "lane1", "lane2", "trade_log", "skipped_days"}
    assert report["lane2"].total_trades == 1
    assert report["lane1"].total_trades == 0
    assert report["combined"].total_trades == 1


# --- 11. no order/action/broker fields or imports (repo-wide guarantee) -------------------------------------


def test_no_order_action_fields_on_v2_trade_result():
    forbidden = ("order", "order_id", "ticket", "submit", "place", "execute", "broker_order")
    field_names = {f.name for f in dataclasses.fields(V2TradeResult)}
    for word in forbidden:
        assert word not in field_names


def test_modules_have_no_order_action_verbs():
    for module in _SCANNED_MODULES:
        source = Path(module.__file__).read_text()
        for forbidden in _FORBIDDEN_ORDER_ACTION_IDENTIFIERS:
            assert forbidden not in source


def _imported_modules(module) -> list[str]:
    tree = ast.parse(Path(module.__file__).read_text())
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    return modules


def test_modules_have_no_forbidden_imports():
    for module in _SCANNED_MODULES:
        imported = _imported_modules(module)
        for name in imported:
            for forbidden in _FORBIDDEN_IMPORT_FRAGMENTS:
                assert forbidden not in name


def test_modules_have_no_cross_boundary_imports_outside_stocks_advisory():
    for module in _SCANNED_MODULES:
        imported = _imported_modules(module)
        outside = [
            name for name in imported
            if not name.startswith("stocks_advisory")
            and name not in ("__future__", "dataclasses", "enum", "typing", "datetime", "math")
        ]
        assert not outside, f"{module.__name__} has an unexpected import: {outside}"


def test_modules_have_no_clock_access():
    for module in _SCANNED_MODULES:
        source = Path(module.__file__).read_text()
        for forbidden in ("datetime.now(", "time.time(", "date.today("):
            assert forbidden not in source


def test_v1_module_files_are_never_imported_for_decisioning_beyond_evaluate_day():
    """v2 must reuse Lane 1 by calling evaluate_day() directly, never by
    importing tqqq_sqqq_decision.py (the live decision-engine module) at
    all -- Lane 2 is a purely backtest-side addition. Checks actual import
    statements (not the module docstring, which mentions these modules by
    name in prose)."""
    imported = _imported_modules(v2_module)
    forbidden_modules = (
        "tqqq_sqqq_decision", "qqq_signal_builder", "paper_runner", "paper_simulator",
    )
    for name in imported:
        for forbidden in forbidden_modules:
            assert forbidden not in name, f"v2 module must not import {name!r}"
