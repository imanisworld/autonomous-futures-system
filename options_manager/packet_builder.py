"""Phase 1 packet builder — packet-level validation only.

No broker calls, no order calls, no execution logic. Validates the shape and
basic sanity of an inbound signal into an OptionTradePacket, journals it, and
sends an outbound Discord notification either way.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Optional

from .journal import log_packet
from .models import OptionTradePacket
from .notify import notify_packet

MIN_SIGNA_SCORE = 30
ALLOWED_GRADES = ("A", "B")
MIN_DAYS_TO_EXPIRY = 14
MAX_PREMIUM_CEILING = 3.00
MAX_CONTRACTS_CEILING = 2


def build_packet(raw_input: dict) -> OptionTradePacket:
    ticker = raw_input["ticker"]
    direction = raw_input["direction"]
    entry_price = float(raw_input["entry_price"])
    signa_score = int(raw_input["signa_score"])
    signa_grade = raw_input["signa_grade"]
    signa_bias = raw_input["signa_bias"]
    gex_regime = raw_input.get("gex_regime", "")
    gex_wall_above = raw_input.get("gex_wall_above")
    gex_wall_below = raw_input.get("gex_wall_below")
    contract_strike = float(raw_input["contract_strike"])
    contract_expiry = _parse_date(raw_input["contract_expiry"])
    max_premium = float(raw_input.get("max_premium", MAX_PREMIUM_CEILING))
    max_contracts = int(raw_input.get("max_contracts", MAX_CONTRACTS_CEILING))
    account_tag = raw_input.get("account_tag", "agentic_micro_account")
    source = raw_input.get("source", "claude_session")

    # Cap (not reject) contract count above the ceiling.
    if max_contracts > MAX_CONTRACTS_CEILING:
        max_contracts = MAX_CONTRACTS_CEILING

    price_target_raw: Optional[Any] = raw_input.get("price_target")
    price_target = float(price_target_raw) if price_target_raw is not None else 0.0

    rejection_reason = _validate(
        direction=direction,
        signa_score=signa_score,
        signa_grade=signa_grade,
        signa_bias=signa_bias,
        contract_expiry=contract_expiry,
        max_premium=max_premium,
        price_target_raw=price_target_raw,
        price_target=price_target,
        entry_price=entry_price,
    )

    status = "REJECTED" if rejection_reason else "PENDING"

    packet = OptionTradePacket(
        ticker=ticker,
        direction=direction,
        entry_price=entry_price,
        price_target=price_target,
        signa_score=signa_score,
        signa_grade=signa_grade,
        signa_bias=signa_bias,
        gex_regime=gex_regime,
        gex_wall_above=gex_wall_above,
        gex_wall_below=gex_wall_below,
        contract_strike=contract_strike,
        contract_expiry=contract_expiry,
        max_premium=max_premium,
        max_contracts=max_contracts,
        account_tag=account_tag,
        source=source,
        created_at=datetime.now(timezone.utc),
        status=status,
        rejection_reason=rejection_reason,
    )

    log_packet(packet)
    notify_packet(packet)

    return packet


def _validate(
    *,
    direction: str,
    signa_score: int,
    signa_grade: str,
    signa_bias: str,
    contract_expiry: date,
    max_premium: float,
    price_target_raw: Optional[Any],
    price_target: float,
    entry_price: float,
) -> Optional[str]:
    if signa_score < MIN_SIGNA_SCORE:
        return f"signa_score {signa_score} below minimum {MIN_SIGNA_SCORE}"

    if signa_grade not in ALLOWED_GRADES:
        return f"signa_grade '{signa_grade}' not allowed (require A or B)"

    if direction == "CALL" and signa_bias != "BULLISH":
        return f"signa_bias '{signa_bias}' does not align with CALL (requires BULLISH)"
    if direction == "PUT" and signa_bias != "BEARISH":
        return f"signa_bias '{signa_bias}' does not align with PUT (requires BEARISH)"

    days_out = (contract_expiry - date.today()).days
    if days_out < MIN_DAYS_TO_EXPIRY:
        return f"contract_expiry {days_out}d out below minimum {MIN_DAYS_TO_EXPIRY}d"

    if max_premium > MAX_PREMIUM_CEILING:
        return f"max_premium {max_premium} exceeds ceiling {MAX_PREMIUM_CEILING}"

    if price_target_raw is None:
        return "price_target is required"

    if direction == "CALL" and price_target <= entry_price:
        return "price_target must be above entry_price for CALL"
    if direction == "PUT" and price_target >= entry_price:
        return "price_target must be below entry_price for PUT"

    return None


def _parse_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))
