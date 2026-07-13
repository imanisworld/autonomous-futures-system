"""
tests/test_stocks_tqqq_sqqq_decision.py

stocks_advisory/tqqq_sqqq_decision.py tests. Proves the v1 decision
rules produce the documented TAKE_PAPER/NO_TRADE verdicts in the right
order, never raise on malformed input, produce at most one trade per
day, contain no order/action/submit field of any kind, and have no
Robinhood/broker/execution/futures/options_manager coupling.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import stocks_advisory.tqqq_sqqq_decision as tqqq_sqqq_decision_module
import stocks_advisory.tqqq_sqqq_models as tqqq_sqqq_models_module
from stocks_advisory.tqqq_sqqq_decision import (
    check_tqqq_sqqq_decision_intake,
    evaluate_tqqq_sqqq_decision,
)
from stocks_advisory.tqqq_sqqq_models import (
    PaperTradeRecord,
    PaperTradeStatus,
    QQQSignalInput,
    TqqqSqqqDecisionResult,
    TqqqSqqqDirection,
    TqqqSqqqVerdict,
)

_SCANNED_MODULES = (tqqq_sqqq_decision_module, tqqq_sqqq_models_module)

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


def _module_source(module) -> str:
    return Path(module.__file__).read_text()


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


def _clean_payload(**overrides) -> dict:
    payload = dict(
        date="2026-07-13",
        qqq_open=406.0,
        qqq_previous_day_high=408.0,
        qqq_previous_day_low=403.0,
        qqq_previous_day_close=405.5,
        qqq_gap_percent=0.5,
        qqq_first_hour_high=410.0,
        qqq_first_hour_low=405.0,
        qqq_first_hour_close=407.0,
        qqq_vwap=407.0,
        qqq_current_price=407.0,
        relative_volume=1.1,
        allowed_max_gap_percent=2.0,
        allowed_min_first_hour_range=1.0,
        allowed_max_first_hour_range=10.0,
    )
    payload.update(overrides)
    return payload


# --- 1. bullish breakout above first-hour high + above VWAP returns TAKE_PAPER / TQQQ ---------------------


def test_bullish_breakout_above_first_hour_high_and_vwap_returns_take_paper_tqqq():
    result = check_tqqq_sqqq_decision_intake(_clean_payload(qqq_current_price=412.0))
    assert result.verdict == TqqqSqqqVerdict.TAKE_PAPER
    assert result.trade.direction == TqqqSqqqDirection.LONG_TQQQ
    assert result.trade.vehicle_symbol == "TQQQ"
    assert result.trade.signal_symbol == "QQQ"
    assert result.trade.status == PaperTradeStatus.WATCHING


# --- 2. bearish breakdown below first-hour low + below VWAP returns TAKE_PAPER / SQQQ ----------------------


def test_bearish_breakdown_below_first_hour_low_and_vwap_returns_take_paper_sqqq():
    result = check_tqqq_sqqq_decision_intake(_clean_payload(qqq_current_price=403.0))
    assert result.verdict == TqqqSqqqVerdict.TAKE_PAPER
    assert result.trade.direction == TqqqSqqqDirection.LONG_SQQQ
    assert result.trade.vehicle_symbol == "SQQQ"


# --- 3. price inside first-hour range returns NO_TRADE --------------------------------------------------


def test_price_inside_first_hour_range_returns_no_trade():
    result = check_tqqq_sqqq_decision_intake(_clean_payload(qqq_current_price=407.0))
    assert result.verdict == TqqqSqqqVerdict.NO_TRADE
    assert result.trade.direction == TqqqSqqqDirection.NO_TRADE
    assert "inside" in result.trade.reason.lower()


# --- 4. gap too large returns NO_TRADE -------------------------------------------------------------------


def test_gap_too_large_returns_no_trade():
    result = check_tqqq_sqqq_decision_intake(
        _clean_payload(qqq_gap_percent=5.0, qqq_current_price=412.0)  # would otherwise be a TQQQ breakout
    )
    assert result.verdict == TqqqSqqqVerdict.NO_TRADE
    assert "gap" in result.trade.reason.lower()


# --- 5. first-hour range too small returns NO_TRADE ------------------------------------------------------


def test_first_hour_range_too_small_returns_no_trade():
    result = check_tqqq_sqqq_decision_intake(
        _clean_payload(qqq_first_hour_high=405.5, qqq_first_hour_low=405.0, qqq_current_price=406.0)
    )
    assert result.verdict == TqqqSqqqVerdict.NO_TRADE
    assert "first-hour range" in result.trade.reason.lower()
    assert "below the minimum" in result.trade.reason.lower()


# --- 6. first-hour range too large returns NO_TRADE -------------------------------------------------------


def test_first_hour_range_too_large_returns_no_trade():
    result = check_tqqq_sqqq_decision_intake(
        _clean_payload(qqq_first_hour_high=420.0, qqq_first_hour_low=405.0, qqq_current_price=421.0)
    )
    assert result.verdict == TqqqSqqqVerdict.NO_TRADE
    assert "first-hour range" in result.trade.reason.lower()
    assert "exceeds the maximum" in result.trade.reason.lower()


# --- 7. VWAP conflict returns NO_TRADE ---------------------------------------------------------------------


def test_breakout_above_high_but_below_vwap_conflict_returns_no_trade():
    # price clears the first-hour high but VWAP sits above price -- no confirmation
    result = check_tqqq_sqqq_decision_intake(
        _clean_payload(qqq_current_price=411.0, qqq_vwap=413.0)
    )
    assert result.verdict == TqqqSqqqVerdict.NO_TRADE
    assert "conflicts with vwap" in result.trade.reason.lower()


def test_breakdown_below_low_but_above_vwap_conflict_returns_no_trade():
    result = check_tqqq_sqqq_decision_intake(
        _clean_payload(qqq_current_price=404.0, qqq_vwap=402.0)
    )
    assert result.verdict == TqqqSqqqVerdict.NO_TRADE
    assert "conflicts with vwap" in result.trade.reason.lower()


# --- 8. missing required fields returns INVALID safely, never raises --------------------------------------


def test_missing_required_field_returns_invalid_not_exception():
    payload = _clean_payload()
    del payload["qqq_vwap"]
    result = check_tqqq_sqqq_decision_intake(payload)
    assert result.verdict == TqqqSqqqVerdict.INVALID
    assert "qqq_vwap" in result.missing_fields
    assert result.trade is None


def test_non_dict_payload_returns_invalid_not_exception():
    for bad_payload in (None, "not a dict", 42, ["a", "list"]):
        result = check_tqqq_sqqq_decision_intake(bad_payload)
        assert isinstance(result, TqqqSqqqDecisionResult)
        assert result.verdict == TqqqSqqqVerdict.INVALID
        assert "malformed payload" in result.blocking_reasons[0]
        assert result.trade is None


def test_invalid_numeric_field_returns_invalid_not_exception():
    result = check_tqqq_sqqq_decision_intake(_clean_payload(qqq_vwap="not-a-number"))
    assert result.verdict == TqqqSqqqVerdict.INVALID
    assert result.trade is None


# --- 9. max one trade per day is represented in the model/result ------------------------------------------


def test_result_represents_at_most_one_trade_per_day():
    result = check_tqqq_sqqq_decision_intake(_clean_payload(qqq_current_price=412.0))
    assert isinstance(result.trade, PaperTradeRecord)
    result_fields = {f.name: f.type for f in dataclasses.fields(TqqqSqqqDecisionResult)}
    assert "trades" not in result_fields  # no plural/list-of-trades field exists
    trade_field_names = {f.name for f in dataclasses.fields(PaperTradeRecord)}
    assert not any("trades" in name or "history" in name for name in trade_field_names)


def test_evaluate_directly_on_typed_input_returns_single_trade():
    signal = QQQSignalInput(**_clean_payload(qqq_current_price=412.0))
    result = evaluate_tqqq_sqqq_decision(signal)
    assert result.verdict == TqqqSqqqVerdict.TAKE_PAPER
    assert isinstance(result.trade, PaperTradeRecord)


# --- 10. no order/action/submit fields exist anywhere on the result or records -----------------------------


def test_no_order_action_submit_fields_on_records():
    forbidden = ("order", "order_id", "ticket", "submit", "place", "execute", "broker_order")
    for dc in (PaperTradeRecord, TqqqSqqqDecisionResult, QQQSignalInput):
        field_names = {f.name for f in dataclasses.fields(dc)}
        for word in forbidden:
            assert word not in field_names, f"{dc.__name__} must not have a {word!r} field"


def test_modules_have_no_order_action_verbs():
    for module in _SCANNED_MODULES:
        source = _module_source(module)
        for forbidden in _FORBIDDEN_ORDER_ACTION_IDENTIFIERS:
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


# --- 11. no Robinhood/broker/execution/futures/options_manager imports, no I/O -----------------------------


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
            and name not in ("__future__", "dataclasses", "enum", "typing")
        ]
        assert not outside, f"{module.__name__} has an unexpected import: {outside}"


def test_modules_do_not_read_or_write_files():
    for module in _SCANNED_MODULES:
        source = _module_source(module)
        for forbidden in ("open(", ".write(", ".write_text(", ".write_bytes(", ".read_text("):
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_modules_have_no_network_call_text():
    for module in _SCANNED_MODULES:
        source = _module_source(module).lower()
        for forbidden in ("httpx.", "requests.", "socket."):
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_modules_have_no_clock_access():
    for module in _SCANNED_MODULES:
        source = _module_source(module)
        for forbidden in ("datetime.now(", "time.time(", "date.today("):
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_no_futures_or_options_module_imports_stocks_advisory():
    """Checks actual import statements, not a raw substring search -- confirms
    this new lane has zero coupling in either direction with futures or
    options_manager code."""
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
