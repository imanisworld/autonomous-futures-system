"""stocks_advisory/tqqq_sqqq_decision.py

TQQQ/SQQQ paper-practice decision -- Stock/ETF Paper Advisory Bot v1.
QQQ first-hour structure decides whether TQQQ, SQQQ, or NO_TRADE is
the day's paper candidate. This module is advisory/reporting only: it
has no order, ticket, or execution field of any kind, never places or
queues a trade, and never calls a broker, a scanner, or a market-data
feed. Performs no I/O of any kind: no quote fetch, no candle fetch, no
broker call, no order placement, no execution, no alert sending, no
file access at runtime, no network calls, no system-clock reads. Does
not import Robinhood, any broker package, `execution/`, `futures`
code, or `options_manager` -- this is a separate lane from both.

`evaluate_tqqq_sqqq_decision()` takes an already-typed `QQQSignalInput`
and returns a `TqqqSqqqDecisionResult` with a `TqqqSqqqVerdict` of
`TAKE_PAPER`, `NO_TRADE`, or `INVALID` (`WAIT` is defined but not
reachable from v1's rules -- see `tqqq_sqqq_models.py`).
`check_tqqq_sqqq_decision_intake()` is the manual-payload entry point --
a loose dict, typed in by hand -- that normalizes into a
`QQQSignalInput` and runs the same evaluation. Never raises regardless
of how malformed the payload is, the same non-throwing, fail-closed
pattern established throughout `options_manager/validation/` (a
missing or uncoercible required field returns `INVALID` naming the
problem, rather than throwing or guessing).

Decision order (first match wins -- more restrictive conditions are
checked first):

1. `qqq_gap_percent` above `allowed_max_gap_percent` -> NO_TRADE
2. first-hour range (`qqq_first_hour_high - qqq_first_hour_low`) below
   `allowed_min_first_hour_range` -> NO_TRADE
3. first-hour range above `allowed_max_first_hour_range` -> NO_TRADE
4. `qqq_current_price` above the first-hour high AND above VWAP ->
   TAKE_PAPER / LONG_TQQQ
5. `qqq_current_price` below the first-hour low AND below VWAP ->
   TAKE_PAPER / LONG_SQQQ
6. `qqq_current_price` inside the first-hour range -> NO_TRADE
7. A breakout beyond the first-hour high/low without VWAP confirming
   the same direction -> NO_TRADE ("signal conflicts with VWAP")

Hard rules this module enforces structurally rather than by extra
logic: at most one `PaperTradeRecord` is ever produced per
`evaluate_tqqq_sqqq_decision()` call (there is no multi-trade
container anywhere in the return type), there is no overnight-hold,
averaging-down, or re-entry-after-stop concept anywhere in this module
(a fresh call is a fresh, independent day's read), and there is no
agent-discretion field -- the decision is entirely determined by the
rules above, not by any judgment call layered on top.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Mapping

from .tqqq_sqqq_models import (
    PaperTradeRecord,
    PaperTradeStatus,
    QQQSignalInput,
    TqqqSqqqDecisionResult,
    TqqqSqqqDirection,
    TqqqSqqqVerdict,
)

SIGNAL_SYMBOL = "QQQ"
BULLISH_VEHICLE = "TQQQ"
BEARISH_VEHICLE = "SQQQ"


def _first_hour_range(signal: QQQSignalInput) -> float:
    return signal.qqq_first_hour_high - signal.qqq_first_hour_low


def _no_trade(signal: QQQSignalInput, reason: str) -> TqqqSqqqDecisionResult:
    trade = PaperTradeRecord(
        trade_date=signal.date,
        signal_symbol=SIGNAL_SYMBOL,
        vehicle_symbol="",
        direction=TqqqSqqqDirection.NO_TRADE,
        entry_trigger="",
        invalidation="",
        stop_price=None,
        target_1=None,
        target_2=None,
        reason=reason,
        skipped_reason=reason,
        status=PaperTradeStatus.NO_TRADE,
    )
    return TqqqSqqqDecisionResult(verdict=TqqqSqqqVerdict.NO_TRADE, trade=trade)


def evaluate_tqqq_sqqq_decision(signal: QQQSignalInput) -> TqqqSqqqDecisionResult:
    """Runs the fixed v1 decision rules against an already-typed
    `QQQSignalInput`. See the module docstring for the exact decision
    order. Always returns exactly one `PaperTradeRecord` -- never more
    than one trade per day, by construction."""
    if signal.qqq_gap_percent > signal.allowed_max_gap_percent:
        return _no_trade(
            signal,
            f"gap percent {signal.qqq_gap_percent:.2f}% exceeds max allowed "
            f"{signal.allowed_max_gap_percent:.2f}%",
        )

    first_hour_range = _first_hour_range(signal)
    if first_hour_range < signal.allowed_min_first_hour_range:
        return _no_trade(
            signal,
            f"first-hour range {first_hour_range:.2f} is below the minimum "
            f"{signal.allowed_min_first_hour_range:.2f}",
        )
    if first_hour_range > signal.allowed_max_first_hour_range:
        return _no_trade(
            signal,
            f"first-hour range {first_hour_range:.2f} exceeds the maximum "
            f"{signal.allowed_max_first_hour_range:.2f}",
        )

    above_high = signal.qqq_current_price > signal.qqq_first_hour_high
    below_low = signal.qqq_current_price < signal.qqq_first_hour_low
    above_vwap = signal.qqq_current_price > signal.qqq_vwap
    below_vwap = signal.qqq_current_price < signal.qqq_vwap

    if above_high and above_vwap:
        reason = "QQQ broke above first-hour high and holds above VWAP"
        trade = PaperTradeRecord(
            trade_date=signal.date,
            signal_symbol=SIGNAL_SYMBOL,
            vehicle_symbol=BULLISH_VEHICLE,
            direction=TqqqSqqqDirection.LONG_TQQQ,
            entry_trigger=f"QQQ above first-hour high {signal.qqq_first_hour_high:.2f} and above VWAP",
            invalidation=f"QQQ closes back below VWAP {signal.qqq_vwap:.2f}",
            stop_price=signal.qqq_vwap,
            target_1=None,
            target_2=None,
            reason=reason,
            skipped_reason="",
            status=PaperTradeStatus.WATCHING,
        )
        return TqqqSqqqDecisionResult(verdict=TqqqSqqqVerdict.TAKE_PAPER, trade=trade)

    if below_low and below_vwap:
        reason = "QQQ broke below first-hour low and holds below VWAP"
        trade = PaperTradeRecord(
            trade_date=signal.date,
            signal_symbol=SIGNAL_SYMBOL,
            vehicle_symbol=BEARISH_VEHICLE,
            direction=TqqqSqqqDirection.LONG_SQQQ,
            entry_trigger=f"QQQ below first-hour low {signal.qqq_first_hour_low:.2f} and below VWAP",
            invalidation=f"QQQ closes back above VWAP {signal.qqq_vwap:.2f}",
            stop_price=signal.qqq_vwap,
            target_1=None,
            target_2=None,
            reason=reason,
            skipped_reason="",
            status=PaperTradeStatus.WATCHING,
        )
        return TqqqSqqqDecisionResult(verdict=TqqqSqqqVerdict.TAKE_PAPER, trade=trade)

    if not above_high and not below_low:
        return _no_trade(signal, "QQQ is inside the first-hour range")

    return _no_trade(signal, "QQQ breakout direction conflicts with VWAP")


_STR_FIELDS = ("date",)
_FLOAT_FIELDS = (
    "qqq_open",
    "qqq_previous_day_high",
    "qqq_previous_day_low",
    "qqq_previous_day_close",
    "qqq_gap_percent",
    "qqq_first_hour_high",
    "qqq_first_hour_low",
    "qqq_first_hour_close",
    "qqq_vwap",
    "qqq_current_price",
    "relative_volume",
    "allowed_max_gap_percent",
    "allowed_min_first_hour_range",
    "allowed_max_first_hour_range",
)

_REQUIRED_FIELD_NAMES = tuple(
    f.name
    for f in dataclasses.fields(QQQSignalInput)
    if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
)


def _is_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def check_tqqq_sqqq_decision_intake(payload: Any) -> TqqqSqqqDecisionResult:
    """Normalizes a manual dict-like payload into a `QQQSignalInput` and
    evaluates it with `evaluate_tqqq_sqqq_decision()`. Never raises
    regardless of how malformed `payload` is -- a malformed payload, a
    missing required field, or an uncoercible field value returns
    `INVALID` naming the problem, with `trade=None`, the same
    fail-closed convention `check_contract_quality_intake()` and every
    other `check_*_intake()` in `options_manager/validation/`
    established."""
    if not isinstance(payload, Mapping):
        return TqqqSqqqDecisionResult(
            verdict=TqqqSqqqVerdict.INVALID,
            blocking_reasons=(
                f"malformed payload: expected a dict-like mapping, got {type(payload).__name__}",
            ),
        )

    missing = [name for name in _REQUIRED_FIELD_NAMES if not _is_present(payload.get(name))]
    if missing:
        return TqqqSqqqDecisionResult(
            verdict=TqqqSqqqVerdict.INVALID,
            missing_fields=tuple(missing),
            blocking_reasons=tuple(f"missing {name}" for name in missing),
        )

    coercion_errors: list[str] = []
    normalized: dict[str, Any] = {}

    for name in _REQUIRED_FIELD_NAMES:
        raw_value = payload[name]
        try:
            if name in _STR_FIELDS:
                normalized[name] = str(raw_value)
            elif name in _FLOAT_FIELDS:
                normalized[name] = float(raw_value)
        except (TypeError, ValueError) as exc:
            coercion_errors.append(f"invalid value for {name}: {exc}")

    if "market_regime_label" in payload and payload["market_regime_label"] is not None:
        normalized["market_regime_label"] = str(payload["market_regime_label"])

    if coercion_errors:
        return TqqqSqqqDecisionResult(
            verdict=TqqqSqqqVerdict.INVALID,
            blocking_reasons=tuple(coercion_errors),
        )

    signal = QQQSignalInput(**normalized)
    return evaluate_tqqq_sqqq_decision(signal)
