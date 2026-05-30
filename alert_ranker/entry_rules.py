"""Entry qualification rules for advisory options alerts.

These rules are intentionally separate from setup scoring. Scoring can say a
name is interesting; entry qualification decides whether it is actionable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class EntryRuleResult:
    eligible: bool
    status: str
    reason: str
    notes: list[str] = field(default_factory=list)


def evaluate_entry_rules(data: dict[str, Any]) -> EntryRuleResult:
    direction = str(data.get("direction") or "").upper()
    zone_type = _lower(data.get("zone_type") or data.get("zone"))
    zone_state = _lower(data.get("zone_state") or data.get("zone_freshness"))
    ftfc = _lower(data.get("ftfc_direction") or data.get("ftfc"))

    if direction not in {"LONG", "SHORT"}:
        return EntryRuleResult(False, "blocked", "direction_unknown")

    expected_zone = "demand" if direction == "LONG" else "supply"
    if zone_type != expected_zone:
        reason = "calls_require_demand" if direction == "LONG" else "puts_require_supply"
        return EntryRuleResult(False, "blocked", reason)

    if zone_state in {"used", "old", "stale", "invalid"}:
        return EntryRuleResult(False, "blocked", "zone_not_fresh")

    notes = [f"{expected_zone} zone present"]
    if _truthy(data.get("zone_touched") or data.get("zone_retest")):
        notes.append("price retested the zone")
    else:
        return EntryRuleResult(False, "forming", "waiting_for_zone_retest", notes)

    ema_check = _ema_alignment(data, direction)
    if ema_check is False:
        return EntryRuleResult(False, "blocked", "against_ema_trend", notes)
    if ema_check is True:
        notes.append("EMA alignment supports direction")

    if _ftfc_opposes(ftfc, direction):
        return EntryRuleResult(False, "blocked", "ftfc_opposes_direction", notes)
    if ftfc in {"up", "down", "long", "short", "bullish", "bearish"}:
        notes.append("FTFC does not oppose direction")

    candle = _confirmation_candle(data, direction)
    if candle == "confirmed":
        notes.append("confirmation candle supports entry")
        return EntryRuleResult(True, "confirmed", "entry_confirmed", notes)
    if candle == "opposes":
        return EntryRuleResult(False, "blocked", "candle_rejects_entry", notes)
    return EntryRuleResult(False, "forming", "waiting_for_confirmation_candle", notes)


def _ema_alignment(data: dict[str, Any], direction: str) -> bool | None:
    alignment = _lower(data.get("ema_alignment") or data.get("ema_state"))
    if alignment:
        if direction == "LONG":
            return alignment in {"above", "bullish", "call", "calls", "long"}
        return alignment in {"below", "bearish", "put", "puts", "short"}

    price = _num(data.get("price"))
    ema_values = [_num(data.get(key)) for key in ("ema8", "ema20", "ema50")]
    ema_values = [value for value in ema_values if value is not None]
    if price is None or not ema_values:
        return None
    if direction == "LONG":
        return all(price > value for value in ema_values)
    return all(price < value for value in ema_values)


def _confirmation_candle(data: dict[str, Any], direction: str) -> str:
    candle = _lower(
        data.get("confirmation")
        or data.get("candle_confirmation")
        or data.get("candle_signal")
    )
    bullish = {"bullish_rejection", "bottom_wick", "bullish_close", "green_close", "bullish_engulfing", "pin_bar_up"}
    bearish = {"bearish_rejection", "top_wick", "bearish_close", "red_close", "bearish_engulfing", "pin_bar_down"}
    if direction == "LONG" and candle in bullish:
        return "confirmed"
    if direction == "SHORT" and candle in bearish:
        return "confirmed"
    if direction == "LONG" and candle in bearish:
        return "opposes"
    if direction == "SHORT" and candle in bullish:
        return "opposes"
    return "missing"


def _ftfc_opposes(ftfc: str, direction: str) -> bool:
    if not ftfc:
        return False
    if direction == "LONG":
        return ftfc in {"down", "short", "bearish"}
    return ftfc in {"up", "long", "bullish"}


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _lower(value) in {"1", "true", "yes", "y", "retest", "touched", "touch"}


def _lower(value: Any) -> str:
    return str(value or "").strip().lower()


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
