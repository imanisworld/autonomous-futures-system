"""Complete mechanical options setup proof from causal scanner context.

This module is pure and advisory-only.  It does not fetch data, select an
option contract, place orders, or invent missing inputs.  It takes the causal
bar-context facts already produced for the ticker, SPY and QQQ and decides
whether a mechanically confirmed 2-1-2 has enough proof to advance from the
shared strategy authority's incomplete state to scanner-level TRIGGERED.

The completion rule is intentionally narrow for OPTIONS_PAPER_V1:
- a real 2-1-2 sequence must already be confirmed by the shared authority;
- the authority-supplied entry and invalidation must be valid;
- SPY and QQQ must both align with the requested direction using their causal
  close relative to session VWAP and EMA20;
- the ticker's reconstructed hourly and prior-session daily Strat candles must
  both align with the requested direction (full timeframe continuity);
- Target 1 and Target 2 must be real previously-observed support/resistance
  levels on the correct side of entry.  No R-multiple or synthetic target is
  substituted when those levels do not exist.

No numeric R:R threshold is introduced here.  The resulting R:R values are
recorded as evidence; changing the population later can be done explicitly
rather than silently embedding an unapproved threshold in this completion
step.
"""

from __future__ import annotations

from math import isfinite
from typing import Any, Mapping, Sequence


def _num(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) else None


def _trend(context: Mapping[str, Any] | None) -> str:
    if not context or not context.get("available"):
        return "UNKNOWN"
    close = _num(context.get("close"))
    vwap = _num(context.get("vwap"))
    ema20 = _num(context.get("ema20"))
    if close is None or vwap is None or ema20 is None:
        return "UNKNOWN"
    if close > vwap and close > ema20:
        return "LONG"
    if close < vwap and close < ema20:
        return "SHORT"
    return "MIXED"


def _valid_targets(
    levels: Sequence[Any] | None,
    *,
    direction: str,
    entry: float,
) -> list[float]:
    parsed = {_num(value) for value in (levels or ())}
    clean = {value for value in parsed if value is not None}
    if direction == "CALL":
        return sorted(value for value in clean if value > entry)
    return sorted((value for value in clean if value < entry), reverse=True)


def _blocked(
    reason_code: str,
    *,
    status: str = "WATCH",
    authority_status: Any = None,
    authority_reason: Any = None,
) -> dict[str, Any]:
    return {
        "setup_status": status,
        "setup_reason_code": reason_code,
        "setup_suppression_reason": f"setup_proof_incomplete:{reason_code}",
        "setup_authority_status": authority_status,
        "setup_authority_reason_code": authority_reason,
    }


def complete_setup_proof(
    ticker: Mapping[str, Any] | None,
    spy: Mapping[str, Any] | None,
    qqq: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return scanner fields that complete a confirmed setup, or block it.

    A non-sequence returns an empty mapping so ordinary candles retain the
    shared authority's original NO_TRADE/WATCH/INVALID result unchanged.
    """
    if not ticker or not ticker.get("setup_sequence_confirmed"):
        return {}

    authority_status = ticker.get("setup_status")
    authority_reason = ticker.get("setup_reason_code")
    direction = str(ticker.get("setup_direction") or "").upper()
    if direction not in {"CALL", "PUT"}:
        return _blocked(
            "missing_setup_direction",
            status="INVALID",
            authority_status=authority_status,
            authority_reason=authority_reason,
        )

    entry = _num(ticker.get("setup_entry_trigger"))
    invalidation = _num(ticker.get("setup_invalidation"))
    if entry is None or invalidation is None:
        return _blocked(
            "missing_entry_or_invalidation",
            status="INVALID",
            authority_status=authority_status,
            authority_reason=authority_reason,
        )
    if direction == "CALL" and invalidation >= entry:
        return _blocked(
            "invalidation_wrong_side",
            status="INVALID",
            authority_status=authority_status,
            authority_reason=authority_reason,
        )
    if direction == "PUT" and invalidation <= entry:
        return _blocked(
            "invalidation_wrong_side",
            status="INVALID",
            authority_status=authority_status,
            authority_reason=authority_reason,
        )

    wanted_trend = "LONG" if direction == "CALL" else "SHORT"
    spy_trend = _trend(spy)
    qqq_trend = _trend(qqq)
    if spy_trend != wanted_trend or qqq_trend != wanted_trend:
        return {
            **_blocked(
                "spy_qqq_not_aligned",
                authority_status=authority_status,
                authority_reason=authority_reason,
            ),
            "spy_trend": spy_trend,
            "qqq_trend": qqq_trend,
            "market_context_status": "WAIT",
        }

    expected_candle = "two_up" if direction == "CALL" else "two_down"
    hourly = str(ticker.get("hourly_candle_type") or "")
    daily = str(ticker.get("daily_candle_type") or "")
    if hourly != expected_candle or daily != expected_candle:
        return {
            **_blocked(
                "htf_not_aligned",
                authority_status=authority_status,
                authority_reason=authority_reason,
            ),
            "spy_trend": spy_trend,
            "qqq_trend": qqq_trend,
            "market_context_status": "WAIT",
            "ftfc": False,
            "ftfc_direction": wanted_trend,
        }

    levels_key = "setup_resistance_levels" if direction == "CALL" else "setup_support_levels"
    targets = _valid_targets(ticker.get(levels_key), direction=direction, entry=entry)
    if len(targets) < 2:
        return {
            **_blocked(
                "target_levels_missing",
                status="INVALID",
                authority_status=authority_status,
                authority_reason=authority_reason,
            ),
            "spy_trend": spy_trend,
            "qqq_trend": qqq_trend,
            "market_context_status": "ALIGNED",
            "ftfc": True,
            "ftfc_direction": wanted_trend,
        }

    target_1, target_2 = targets[0], targets[1]
    risk = abs(entry - invalidation)
    rr_1 = abs(target_1 - entry) / risk if risk else None
    rr_2 = abs(target_2 - entry) / risk if risk else None

    sequence = ticker.get("strat_sequence") or "strat_212"
    return {
        "setup_status": "TRIGGERED",
        "setup_reason_code": "setup_proof_complete",
        "setup_suppression_reason": None,
        "setup_authority_status": authority_status,
        "setup_authority_reason_code": authority_reason,
        "pattern": sequence,
        "direction": wanted_trend,
        "entry_trigger": entry,
        "setup_entry_trigger": entry,
        "underlying_invalidation": invalidation,
        "invalidation": invalidation,
        "stop": invalidation,
        "target": target_1,
        "target_1": target_1,
        "target_2": target_2,
        "underlying_risk": risk,
        "underlying_rr_1": rr_1,
        "underlying_rr_2": rr_2,
        "spy_trend": spy_trend,
        "qqq_trend": qqq_trend,
        "market_context_status": "ALIGNED",
        "ftfc": True,
        "ftfc_direction": wanted_trend,
        "full_timeframe_continuity": True,
        "setup_target_source": "prior_completed_hourly_and_daily_levels",
    }
