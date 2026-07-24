"""Canonical day-only exit policy shared by runtime and replay paths."""

from __future__ import annotations

from datetime import datetime, time
from typing import Mapping, Optional
from zoneinfo import ZoneInfo

from execution.broker_interface import Fill
from execution.paper_broker import PaperBroker


EASTERN = ZoneInfo("America/New_York")
DAY_ONLY_EXIT_REASON = "DAY_ONLY_FLATTEN"
MISSING_EOD_PRICE = "MISSING_EOD_PRICE"
DAY_ONLY_STRATEGIES = frozenset({"strat_4hr_retrigger"})
_EOD_BAR_START = time(15, 55)
_FALLBACK_START = time(16, 0)
_TICK_SIZE = {"MNQ": 0.25, "MES": 0.25}
_TICK_VALUE = {"MNQ": 0.50, "MES": 1.25}


def strategy_is_day_only(strategy: object) -> bool:
    return str(strategy or "").strip() in DAY_ONLY_STRATEGIES


def _as_aware_datetime(value: datetime | str | int | float) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)) or str(value).strip().isdigit():
        raw = float(value)
        if raw > 10_000_000_000:
            raw /= 1000.0
        parsed = datetime.fromtimestamp(raw, tz=ZoneInfo("UTC"))
    else:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("day-only timestamps must include a timezone")
    return parsed


def _timeframe_minutes(timeframe: object) -> Optional[int]:
    token = str(timeframe or "").strip().lower()
    aliases = {"5": 5, "5m": 5, "5min": 5, "5minute": 5, "5minutes": 5}
    return aliases.get(token)


def is_exact_eod_bar(timestamp: datetime | str | int | float, timeframe: object) -> bool:
    """True only for the 5-minute bar that starts 15:55 ET and closes 16:00 ET."""
    if _timeframe_minutes(timeframe) != 5:
        return False
    local = _as_aware_datetime(timestamp).astimezone(EASTERN)
    return local.time().replace(tzinfo=None) == _EOD_BAR_START


def is_after_eod_close(timestamp: datetime | str | int | float) -> bool:
    """True for bars starting at/after 16:00 ET; never valid EOD substitutes."""
    local = _as_aware_datetime(timestamp).astimezone(EASTERN)
    return local.time().replace(tzinfo=None) >= _FALLBACK_START


def fallback_is_authorized(now: datetime) -> bool:
    """The HTTP fallback may act only at or after 16:00 New York wall time."""
    local = _as_aware_datetime(now).astimezone(EASTERN)
    return local.time().replace(tzinfo=None) >= _FALLBACK_START


def classify_result(direction: object, entry_price: float, exit_price: float) -> str:
    direction_key = str(direction or "").strip().upper()
    if direction_key not in {"LONG", "SHORT"}:
        raise ValueError(f"unsupported direction: {direction!r}")
    signed_move = (
        float(exit_price) - float(entry_price)
        if direction_key == "LONG"
        else float(entry_price) - float(exit_price)
    )
    if signed_move > 0:
        return "WIN"
    if signed_move < 0:
        return "LOSS"
    return "BREAKEVEN"


def instrument_root(value: object) -> str:
    symbol = str(value or "").strip().upper().replace("1!", "")
    for root in ("MNQ", "MES"):
        if symbol.startswith(root):
            return root
    return symbol


def positions_agree(journal_position: Mapping, broker_position: object) -> tuple[bool, str]:
    if broker_position is None:
        return False, "BROKER_POSITION_MISSING"
    journal_instrument = instrument_root(journal_position.get("instrument"))
    broker_instrument = instrument_root(getattr(broker_position, "instrument", None))
    if not journal_instrument or journal_instrument != broker_instrument:
        return False, "INSTRUMENT_MISMATCH"
    journal_direction = str(journal_position.get("direction") or "").upper()
    broker_direction = str(getattr(broker_position, "direction", "") or "").upper()
    if not journal_direction or journal_direction != broker_direction:
        return False, "DIRECTION_MISMATCH"
    journal_qty = journal_position.get("contracts")
    broker_qty = getattr(broker_position, "quantity", None)
    if journal_qty is not None and broker_qty is not None:
        try:
            if int(journal_qty) != int(broker_qty):
                return False, "QUANTITY_MISMATCH"
        except (TypeError, ValueError):
            return False, "QUANTITY_UNREADABLE"
    return True, "MATCH"


def build_day_only_fill(position: Mapping, exit_price: float) -> Fill:
    instrument = str(position.get("instrument") or "")
    root = instrument_root(instrument)
    direction = str(position.get("direction") or "").upper()
    entry_price = float(position["entry"])
    price = float(exit_price)
    contracts = max(1, int(position.get("contracts") or 1))
    tick_size = _TICK_SIZE.get(root, 0.25)
    tick_value = _TICK_VALUE.get(root, 1.0)
    signed_move = price - entry_price if direction == "LONG" else entry_price - price
    pnl_ticks = signed_move / tick_size
    return Fill(
        instrument=instrument,
        direction=direction,
        contracts=contracts,
        entry_price=entry_price,
        exit_price=round(price, 4),
        exit_reason=DAY_ONLY_EXIT_REASON,
        result=classify_result(direction, entry_price, price),
        pnl_ticks=round(pnl_ticks, 2),
        pnl_dollars=round(pnl_ticks * tick_value * contracts, 2),
        paper_order_id=position.get("paper_order_id"),
    )


def resolve_paper_eod(
    broker: PaperBroker,
    position: Mapping,
    *,
    timestamp: datetime | str | int | float,
    timeframe: object,
    close: float,
) -> Optional[Fill]:
    """Resolve an allowlisted paper position only on the exact EOD bar."""
    if not strategy_is_day_only(position.get("strategy")):
        return None
    if not is_exact_eod_bar(timestamp, timeframe):
        return None
    fill = build_day_only_fill(position, close)
    # force_resolve owns PaperBroker's balance/flat mutation. Override only the
    # generic manual reason; result and P&L are independently deterministic.
    resolved = broker.force_resolve(fill.result, float(close))
    if resolved is None:
        return None
    resolved.exit_reason = DAY_ONLY_EXIT_REASON
    return resolved
