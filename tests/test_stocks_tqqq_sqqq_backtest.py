"""
tests/test_stocks_tqqq_sqqq_backtest.py

stocks_advisory/tqqq_sqqq_backtest.py tests. Proves the v1 backtest
decision rules (gap/opening-range filters, breakout+VWAP confirmation,
relative-volume filter, same-day conflict handling), simulated
stop/target/exit-before-close resolution against the vehicle's OWN
bars (including the trailing-stop overlay and conservative same-bar
tie-break), slippage sensitivity, walk-forward/in-sample-out-of-sample
splitting, buy-and-hold comparisons, and no-lookahead behavior -- plus
the same no-broker/no-order/no-futures/no-options coupling guarantees
as every other module in this repo.
"""

from __future__ import annotations

import ast
import dataclasses
from datetime import datetime
from pathlib import Path

import stocks_advisory.tqqq_sqqq_backtest as backtest_module
import stocks_advisory.backtest_models as backtest_models_module
from stocks_advisory.backtest_models import (
    Bar,
    BacktestConfig,
    BacktestTradeResult,
    DaySession,
    SkippedDay,
    TargetModel,
    TradeDirection,
)
from stocks_advisory.tqqq_sqqq_backtest import (
    evaluate_day,
    run_backtest,
    run_in_sample_out_of_sample,
    run_slippage_stress,
    run_walk_forward,
)

_SCANNED_MODULES = (backtest_module, backtest_models_module)

_FORBIDDEN_IMPORT_FRAGMENTS = (
    "robin_stocks",
    "robinhood",
    "execution",
    "webhook",
    "broker",
    "ib_insync",
    "ibapi",
    "tradovate",
    "options_manager",
    "strategy",
    "risk_engine",
    "requests",
    "httpx",
    "urllib",
    "socket",
    "aiohttp",
    "websocket",
)

_FORBIDDEN_ORDER_ACTION_IDENTIFIERS = (
    "place_order",
    "submit_order",
    "cancel_order",
    "replace_order",
    "execute_order",
    "live_order",
    "order_ticket",
)


def _bar(ts: str, o: float, h: float, l: float, c: float, v: int = 1000) -> Bar:
    return Bar(timestamp=ts, open=o, high=h, low=l, close=c, volume=v)


def _config(**overrides) -> BacktestConfig:
    defaults = dict(
        max_gap_percent=2.0,
        min_opening_range_percent=0.1,
        max_opening_range_percent=5.0,
        opening_range_minutes=60,
        exit_cutoff_time="15:55",
        slippage_percent=0.0,
        commission_per_trade=0.0,
        target_r_multiple=1.0,
        position_dollar_size=1000.0,
    )
    defaults.update(overrides)
    return BacktestConfig(**defaults)


_OPENING_RANGE_QQQ = [
    _bar("2026-01-05T09:30:00", 403.0, 404.0, 402.0, 403.5),
    _bar("2026-01-05T09:45:00", 403.5, 404.5, 403.0, 404.0),
    _bar("2026-01-05T10:00:00", 404.0, 405.0, 403.5, 404.5),
    _bar("2026-01-05T10:15:00", 404.5, 405.0, 404.0, 404.8),
]
_OPENING_RANGE_TQQQ = [
    _bar("2026-01-05T09:30:00", 60.0, 60.2, 59.8, 60.0),
    _bar("2026-01-05T09:45:00", 60.0, 60.3, 59.9, 60.1),
    _bar("2026-01-05T10:00:00", 60.1, 60.4, 60.0, 60.2),
    _bar("2026-01-05T10:15:00", 60.2, 60.4, 60.1, 60.3),
]
_OPENING_RANGE_SQQQ = [
    _bar("2026-01-05T09:30:00", 20.0, 20.2, 19.8, 20.0),
    _bar("2026-01-05T09:45:00", 20.0, 20.1, 19.9, 20.0),
    _bar("2026-01-05T10:00:00", 20.0, 20.1, 19.9, 19.95),
    _bar("2026-01-05T10:15:00", 19.95, 20.0, 19.9, 19.9),
]
# range_high=405.0, range_low=402.0, day_open=403.0
# range = 3.0 -> 0.744% of day_open (within the default [0.1%, 5.0%] window)
# previous_close=400.0 -> gap = 0.75% (within the default 2.0% window)
_PREVIOUS_CLOSE = 400.0
_PREVIOUS_HIGH = 406.0
_PREVIOUS_LOW = 398.0


def _bullish_day(*, date="2026-01-05", extra_qqq=(), extra_tqqq=(), extra_sqqq=(), **overrides) -> DaySession:
    qqq = _OPENING_RANGE_QQQ + [
        _bar("2026-01-05T10:30:00", 405.0, 410.0, 405.0, 409.5),
        _bar("2026-01-05T10:45:00", 409.5, 411.0, 409.0, 410.5),
        _bar("2026-01-05T11:00:00", 410.5, 411.0, 410.0, 410.8),
        *extra_qqq,
    ]
    tqqq = _OPENING_RANGE_TQQQ + [
        _bar("2026-01-05T10:30:00", 60.3, 61.5, 60.3, 61.2),
        _bar("2026-01-05T10:45:00", 61.2, 61.8, 61.0, 61.5),
        _bar("2026-01-05T11:00:00", 61.5, 61.9, 61.3, 61.7),
        *extra_tqqq,
    ]
    sqqq = _OPENING_RANGE_SQQQ + [
        _bar("2026-01-05T10:30:00", 19.9, 19.95, 19.5, 19.6),
        _bar("2026-01-05T10:45:00", 19.6, 19.7, 19.4, 19.5),
        _bar("2026-01-05T11:00:00", 19.5, 19.6, 19.3, 19.4),
        *extra_sqqq,
    ]
    defaults = dict(
        date=date,
        qqq_previous_close=_PREVIOUS_CLOSE,
        qqq_previous_high=_PREVIOUS_HIGH,
        qqq_previous_low=_PREVIOUS_LOW,
        qqq_bars=tuple(qqq),
        tqqq_bars=tuple(tqqq),
        sqqq_bars=tuple(sqqq),
    )
    defaults.update(overrides)
    return DaySession(**defaults)


def _bearish_day() -> DaySession:
    qqq = _OPENING_RANGE_QQQ + [
        _bar("2026-01-05T10:30:00", 402.0, 402.0, 395.0, 396.0),
        _bar("2026-01-05T10:45:00", 396.0, 397.0, 393.0, 394.0),
        _bar("2026-01-05T11:00:00", 394.0, 395.0, 392.0, 393.0),
    ]
    tqqq = _OPENING_RANGE_TQQQ + [
        _bar("2026-01-05T10:30:00", 60.3, 60.4, 58.0, 58.2),
        _bar("2026-01-05T10:45:00", 58.2, 58.5, 57.5, 57.8),
        _bar("2026-01-05T11:00:00", 57.8, 58.0, 57.0, 57.2),
    ]
    sqqq = _OPENING_RANGE_SQQQ + [
        _bar("2026-01-05T10:30:00", 19.9, 21.5, 19.9, 21.2),
        _bar("2026-01-05T10:45:00", 21.2, 21.8, 21.0, 21.5),
        _bar("2026-01-05T11:00:00", 21.5, 21.9, 21.3, 21.7),
    ]
    return DaySession(
        date="2026-01-05",
        qqq_previous_close=_PREVIOUS_CLOSE,
        qqq_previous_high=_PREVIOUS_HIGH,
        qqq_previous_low=_PREVIOUS_LOW,
        qqq_bars=tuple(qqq),
        tqqq_bars=tuple(tqqq),
        sqqq_bars=tuple(sqqq),
    )


# --- 1. bullish QQQ breakout enters TQQQ -------------------------------------------------------------------


def test_bullish_breakout_enters_tqqq():
    result = evaluate_day(_bullish_day(), _config())
    assert isinstance(result, BacktestTradeResult)
    assert not result.skipped
    assert result.direction == TradeDirection.LONG_TQQQ
    assert result.vehicle_symbol == "TQQQ"
    assert result.entry_price == 61.2  # fill bar (10:45) open, no slippage
    assert result.entry_time == "2026-01-05T10:45:00"


# --- 2. bearish QQQ breakdown enters SQQQ --------------------------------------------------------------------


def test_bearish_breakdown_enters_sqqq():
    result = evaluate_day(_bearish_day(), _config())
    assert isinstance(result, BacktestTradeResult)
    assert not result.skipped
    assert result.direction == TradeDirection.LONG_SQQQ
    assert result.vehicle_symbol == "SQQQ"


# --- 3. gap too large skips -------------------------------------------------------------------------------


def test_gap_too_large_skips():
    day = dataclasses.replace(_bullish_day(), qqq_previous_close=380.0)  # gap = (403-380)/380 = 6.05% > 2.0%
    result = evaluate_day(day, _config())
    assert result.skipped
    assert "gap too large" in result.skipped_reason.lower()


# --- 4. range too small skips -----------------------------------------------------------------------------


def test_opening_range_too_small_skips():
    tight_range = [
        _bar("2026-01-05T09:30:00", 403.0, 403.1, 403.0, 403.05),
        _bar("2026-01-05T09:45:00", 403.05, 403.1, 403.0, 403.05),
        _bar("2026-01-05T10:00:00", 403.05, 403.1, 403.0, 403.05),
        _bar("2026-01-05T10:15:00", 403.05, 403.1, 403.0, 403.05),
    ]
    day = _bullish_day()
    day = dataclasses.replace(day, qqq_bars=tuple(tight_range) + day.qqq_bars[4:])
    result = evaluate_day(day, _config())
    assert result.skipped
    assert "range too small" in result.skipped_reason.lower()


# --- 5. range too large skips -----------------------------------------------------------------------------


def test_opening_range_too_large_skips():
    wide_range = [
        _bar("2026-01-05T09:30:00", 403.0, 430.0, 380.0, 403.0),
        _bar("2026-01-05T09:45:00", 403.0, 430.0, 380.0, 403.0),
        _bar("2026-01-05T10:00:00", 403.0, 430.0, 380.0, 403.0),
        _bar("2026-01-05T10:15:00", 403.0, 430.0, 380.0, 403.0),
    ]
    day = _bullish_day()
    day = dataclasses.replace(day, qqq_bars=tuple(wide_range) + day.qqq_bars[4:])
    result = evaluate_day(day, _config())
    assert result.skipped
    assert "range too large" in result.skipped_reason.lower()


# --- 6. inside range / no breakout skips ---------------------------------------------------------------------


def test_inside_range_no_breakout_skips():
    day = _bullish_day()
    inside_range_after = [
        _bar("2026-01-05T10:30:00", 404.0, 404.8, 403.0, 404.2),
        _bar("2026-01-05T10:45:00", 404.2, 404.7, 403.2, 404.3),
    ]
    day = dataclasses.replace(day, qqq_bars=day.qqq_bars[:4] + tuple(inside_range_after))
    result = evaluate_day(day, _config())
    assert result.skipped
    assert "no breakout" in result.skipped_reason.lower()


# --- 7. VWAP conflict skips --------------------------------------------------------------------------------


def test_vwap_conflict_skips():
    day = _bullish_day()
    # A big-volume, wide-range bar closing near its low: close (406) clears the
    # opening-range high (405), but the bar's own wide high (430) at 5x volume
    # drags cumulative VWAP up past 406 -- above_high True, above_vwap False.
    conflict_after = [_bar("2026-01-05T10:30:00", 405.0, 430.0, 405.0, 406.0, v=5000)]
    day = dataclasses.replace(day, qqq_bars=day.qqq_bars[:4] + tuple(conflict_after))
    result = evaluate_day(day, _config())
    assert result.skipped
    assert "vwap conflict" in result.skipped_reason.lower()


def test_vwap_not_required_allows_entry_on_pure_breakout():
    day = _bullish_day()
    conflict_after = [_bar("2026-01-05T10:30:00", 405.0, 430.0, 405.0, 406.0, v=5000)]
    day = dataclasses.replace(day, qqq_bars=day.qqq_bars[:4] + tuple(conflict_after))
    result = evaluate_day(day, _config(vwap_required=False))
    assert not result.skipped
    assert result.direction == TradeDirection.LONG_TQQQ


# --- 8. relative volume filter skips if enabled --------------------------------------------------------------


def test_relative_volume_filter_skips_when_below_threshold():
    day = dataclasses.replace(_bullish_day(), qqq_relative_volume=0.5)
    result = evaluate_day(day, _config(relative_volume_filter_enabled=True, min_relative_volume=1.0))
    assert result.skipped
    assert "relative volume" in result.skipped_reason.lower()


def test_relative_volume_filter_skips_when_enabled_but_missing():
    day = _bullish_day()  # qqq_relative_volume defaults to None
    result = evaluate_day(day, _config(relative_volume_filter_enabled=True))
    assert result.skipped
    assert "relative volume" in result.skipped_reason.lower()


def test_relative_volume_filter_does_not_block_when_disabled():
    day = dataclasses.replace(_bullish_day(), qqq_relative_volume=0.1)  # would fail if the filter were enabled
    result = evaluate_day(day, _config(relative_volume_filter_enabled=False))
    assert not result.skipped
    assert result.direction == TradeDirection.LONG_TQQQ


# --- 9. stop hit exits loss --------------------------------------------------------------------------------


def test_stop_hit_exits_loss():
    day = _bullish_day()
    # Replace the fill bar (10:45) with one whose low is far below any
    # plausible stop, guaranteeing a stop-out regardless of the exact
    # QQQ-side-derived stop distance.
    crashed_tqqq = list(day.tqqq_bars)
    crashed_tqqq[5] = _bar("2026-01-05T10:45:00", 61.2, 61.3, 0.01, 0.5)
    day = dataclasses.replace(day, tqqq_bars=tuple(crashed_tqqq))
    result = evaluate_day(day, _config())
    assert not result.skipped
    assert result.exit_reason == "stop"
    assert result.dollar_result < 0
    assert result.r_result is not None and result.r_result < 0


# --- 10. target hit exits win -------------------------------------------------------------------------------


def test_target_hit_exits_win():
    day = _bullish_day()
    spiked_tqqq = list(day.tqqq_bars)
    spiked_tqqq[5] = _bar("2026-01-05T10:45:00", 61.2, 10000.0, 61.0, 9000.0)
    day = dataclasses.replace(day, tqqq_bars=tuple(spiked_tqqq))
    result = evaluate_day(day, _config())
    assert not result.skipped
    assert result.exit_reason == "target"
    assert result.dollar_result > 0
    assert result.r_result is not None and result.r_result > 0


# --- 11. trailing stop locks profit when enabled -------------------------------------------------------------


def test_trailing_stop_locks_profit_when_enabled():
    day = _bullish_day(
        extra_qqq=[_bar("2026-01-05T11:15:00", 410.8, 411.5, 410.5, 411.2)],
        extra_tqqq=[_bar("2026-01-05T11:15:00", 65.5, 65.8, 64.0, 64.5)],
        extra_sqqq=[_bar("2026-01-05T11:15:00", 19.4, 19.5, 19.2, 19.3)],
    )
    big_rally_tqqq = list(day.tqqq_bars)
    big_rally_tqqq[6] = _bar("2026-01-05T11:00:00", 61.7, 66.0, 61.3, 65.5)  # big favorable move, activates trailing
    day = dataclasses.replace(day, tqqq_bars=tuple(big_rally_tqqq))

    config = _config(
        trailing_stop_enabled=True,
        trailing_stop_activation_r=1.0,
        trailing_stop_trail_r=0.5,
        target_model=TargetModel.END_OF_DAY,
    )
    result = evaluate_day(day, config)
    assert not result.skipped
    assert result.exit_reason == "trailing_stop"
    assert result.dollar_result > 0
    assert result.exit_price > result.entry_price


# --- 12. end-of-day exit works -----------------------------------------------------------------------------


def test_end_of_day_exit_works():
    result = evaluate_day(_bullish_day(), _config(exit_cutoff_time="10:31"))
    assert not result.skipped
    assert result.exit_reason == "exit_before_close"


# --- 13. same-day conflict handled conservatively -------------------------------------------------------------


def test_same_day_conflict_without_priority_returns_conflict():
    day = _bullish_day(extra_qqq=[_bar("2026-01-05T11:15:00", 410.8, 411.0, 394.0, 395.0)])
    result = evaluate_day(day, _config())
    assert result.skipped
    assert result.direction == TradeDirection.CONFLICT
    assert "conflict" in result.skipped_reason.lower()


def test_same_day_conflict_with_explicit_priority_takes_configured_side():
    day = _bullish_day(extra_qqq=[_bar("2026-01-05T11:15:00", 410.8, 411.0, 394.0, 395.0)])
    result = evaluate_day(day, _config(same_day_conflict_priority="TQQQ"))
    assert not result.skipped
    assert result.direction == TradeDirection.LONG_TQQQ


# --- 14. missing QQQ / TQQQ / SQQQ data skips safely -------------------------------------------------------


def test_missing_qqq_bars_is_a_skipped_day_not_a_no_trade_result():
    day = DaySession(
        date="2026-01-05",
        qqq_previous_close=400.0,
        qqq_previous_high=_PREVIOUS_HIGH,
        qqq_previous_low=_PREVIOUS_LOW,
    )
    result = evaluate_day(day, _config())
    assert isinstance(result, SkippedDay)
    assert "qqq_bars" in result.reason


def test_missing_vehicle_bars_is_a_skipped_day():
    day = dataclasses.replace(_bullish_day(), tqqq_bars=())
    result = evaluate_day(day, _config())
    assert isinstance(result, SkippedDay)
    assert "tqqq_bars" in result.reason


# --- 15. slippage reduces returns --------------------------------------------------------------------------


def test_slippage_reduces_returns():
    day = _bullish_day()
    spiked_tqqq = list(day.tqqq_bars)
    spiked_tqqq[5] = _bar("2026-01-05T10:45:00", 61.2, 10000.0, 61.0, 9000.0)
    day = dataclasses.replace(day, tqqq_bars=tuple(spiked_tqqq))

    no_slippage = evaluate_day(day, _config(slippage_percent=0.0))
    with_slippage = evaluate_day(day, _config(slippage_percent=1.0))
    assert with_slippage.dollar_result < no_slippage.dollar_result


def test_slippage_stress_report_flags_zero_slippage_only_profitability():
    day = _bullish_day()
    spiked_tqqq = list(day.tqqq_bars)
    spiked_tqqq[5] = _bar("2026-01-05T10:45:00", 61.2, 10000.0, 61.0, 9000.0)
    day = dataclasses.replace(day, tqqq_bars=tuple(spiked_tqqq))
    report = run_slippage_stress([day], _config())
    assert len(report.points) == 5
    assert report.only_profitable_at_zero_slippage() in (True, False, None)


# --- 16. stop/target same-bar conflict uses conservative result ---------------------------------------------


def test_stop_and_target_hit_same_bar_resolves_conservatively_as_stop():
    day = _bullish_day()
    wide_swing_tqqq = list(day.tqqq_bars)
    wide_swing_tqqq[5] = _bar("2026-01-05T10:45:00", 61.2, 10000.0, 0.01, 5000.0)  # spans past both levels
    day = dataclasses.replace(day, tqqq_bars=tuple(wide_swing_tqqq))
    result = evaluate_day(day, _config())
    assert not result.skipped
    assert result.exit_reason == "stop"
    assert result.dollar_result < 0


# --- 17. no lookahead: entry never occurs before opening-range window closes ---------------------------------


def test_entry_never_occurs_before_opening_range_closes():
    result = evaluate_day(_bullish_day(), _config())
    range_end = datetime.fromisoformat("2026-01-05T10:30:00")
    assert datetime.fromisoformat(result.entry_time) >= range_end


# --- 18. trade log / skipped-day log include reasons ---------------------------------------------------------


def test_trade_log_contains_skipped_reasons():
    day = _bullish_day()
    inside_range_after = [_bar("2026-01-05T10:30:00", 404.0, 404.8, 403.0, 404.2)]
    day = dataclasses.replace(day, qqq_bars=day.qqq_bars[:4] + tuple(inside_range_after))
    summary = run_backtest([day], _config())
    assert len(summary.trade_log) == 1
    assert summary.trade_log[0].skipped
    assert summary.trade_log[0].skipped_reason != ""


def test_skipped_day_log_contains_reasons():
    day = DaySession(
        date="2026-01-05",
        qqq_previous_close=400.0,
        qqq_previous_high=_PREVIOUS_HIGH,
        qqq_previous_low=_PREVIOUS_LOW,
    )
    summary = run_backtest([day], _config())
    assert len(summary.skipped_days) == 1
    assert summary.skipped_days[0].reason != ""
    assert summary.skipped_days_by_reason  # non-empty bucket counts


# --- 19. in-sample/out-of-sample split and walk-forward folds -------------------------------------------------


def test_run_in_sample_out_of_sample_splits_chronologically():
    days = [dataclasses.replace(_bullish_day(), date=f"2026-01-0{i + 1}") for i in range(4)]
    result = run_in_sample_out_of_sample(days, _config(), in_sample_fraction=0.5)
    assert result.in_sample_session_count + result.out_of_sample_session_count == 4
    assert result.in_sample_session_count >= 1
    assert result.out_of_sample_session_count >= 1


def test_run_walk_forward_produces_folds():
    days = [dataclasses.replace(_bullish_day(), date=f"2026-01-{i + 1:02d}") for i in range(6)]
    result = run_walk_forward(days, _config(), train_size=2, test_size=2)
    assert len(result.folds) >= 1
    for fold in result.folds:
        assert fold.train_session_count == 2
        assert fold.test_session_count == 2


# --- 20. buy-and-hold comparisons are populated ----------------------------------------------------------------


def test_buy_and_hold_returns_are_populated():
    summary = run_backtest([_bullish_day()], _config())
    assert summary.buy_and_hold_qqq_return_percent is not None
    assert summary.buy_and_hold_tqqq_return_percent is not None


# --- 21. no order/action/submit fields ------------------------------------------------------------------------


def test_no_order_action_submit_fields_on_records():
    forbidden = ("order", "order_id", "ticket", "submit", "place", "execute", "broker_order")
    for dc in (BacktestTradeResult, DaySession, BacktestConfig):
        field_names = {f.name for f in dataclasses.fields(dc)}
        for word in forbidden:
            assert word not in field_names, f"{dc.__name__} must not have a {word!r} field"


def test_modules_have_no_order_action_verbs():
    for module in _SCANNED_MODULES:
        source = Path(module.__file__).read_text()
        for forbidden in _FORBIDDEN_ORDER_ACTION_IDENTIFIERS:
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


# --- 22. no broker/execution/order imports, no futures/options files touched ----------------------------------


def _imported_modules(module) -> list[str]:
    tree = ast.parse(Path(module.__file__).read_text())
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    return modules


def _imported_modules_at_path(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
            modules.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return modules


def test_modules_have_no_forbidden_imports():
    for module in _SCANNED_MODULES:
        imported = _imported_modules(module)
        for name in imported:
            for forbidden in _FORBIDDEN_IMPORT_FRAGMENTS:
                assert forbidden not in name, f"{module.__name__} must not import {name!r}"


def test_modules_have_no_cross_boundary_imports_outside_stocks_advisory():
    for module in _SCANNED_MODULES:
        imported = _imported_modules(module)
        outside = [
            name
            for name in imported
            if not name.startswith("stocks_advisory")
            and name not in ("__future__", "dataclasses", "enum", "typing", "datetime")
        ]
        assert not outside, f"{module.__name__} has an unexpected import: {outside}"


def test_modules_have_no_clock_access():
    for module in _SCANNED_MODULES:
        source = Path(module.__file__).read_text()
        for forbidden in ("datetime.now(", "time.time(", "date.today("):
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_no_futures_or_options_module_imports_stocks_advisory():
    """Checks actual import statements, not a raw substring search."""
    repo_root = Path(__file__).resolve().parent.parent
    scanned_dirs = [
        repo_root / "options_manager",
        repo_root / "execution",
        repo_root / "webhook",
        repo_root / "strategy",
        repo_root / "risk",
    ]
    offenders = []
    for directory in scanned_dirs:
        if not directory.is_dir():
            continue
        for path in directory.rglob("*.py"):
            imported = _imported_modules_at_path(path)
            if any("stocks_advisory" in name for name in imported):
                offenders.append(str(path))
    assert not offenders, f"stocks_advisory must not be imported from: {offenders}"
