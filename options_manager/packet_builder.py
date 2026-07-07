"""Phase 1 packet builder — packet-level validation only.

No broker calls, no order calls, no execution logic. Validates the shape and
basic sanity of an inbound signal into an OptionTradePacket, journals it, and
sends an outbound Discord notification either way.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Optional

from .journal import log_packet
from .models import OptionTradePacket
from .notify import notify_packet

logger = logging.getLogger(__name__)

ALLOWED_DIRECTIONS = ("CALL", "PUT")
MIN_SIGNA_SCORE = 30
MAX_SIGNA_SCORE = 100
ALLOWED_GRADES = ("A", "B")
MIN_DAYS_TO_EXPIRY = 14
MAX_PREMIUM_CEILING = 3.00
MIN_CONTRACTS_FLOOR = 1
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

    # Cap (not reject) contract count above the ceiling. Floor violations
    # (<= 0) are rejected in _validate, not capped.
    if max_contracts > MAX_CONTRACTS_CEILING:
        max_contracts = MAX_CONTRACTS_CEILING

    price_target_raw: Optional[Any] = raw_input.get("price_target")
    price_target = float(price_target_raw) if price_target_raw is not None else 0.0

    rejection_reason = _validate(
        direction=direction,
        signa_score=signa_score,
        signa_grade=signa_grade,
        signa_bias=signa_bias,
        entry_price=entry_price,
        contract_strike=contract_strike,
        contract_expiry=contract_expiry,
        max_premium=max_premium,
        max_contracts=max_contracts,
        price_target_raw=price_target_raw,
        price_target=price_target,
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

    # Discord is best-effort observability only — a missing/failed webhook
    # must never affect packet creation or its status.
    notified = notify_packet(packet)
    if not notified:
        logger.info(
            "options_manager: Discord notification not sent for %s "
            "(no webhook configured or send failed); packet creation unaffected",
            packet.ticker,
        )

    return packet


def _validate(
    *,
    direction: str,
    signa_score: int,
    signa_grade: str,
    signa_bias: str,
    entry_price: float,
    contract_strike: float,
    contract_expiry: date,
    max_premium: float,
    max_contracts: int,
    price_target_raw: Optional[Any],
    price_target: float,
) -> Optional[str]:
    if direction not in ALLOWED_DIRECTIONS:
        return f"direction '{direction}' is invalid; must be CALL or PUT"

    if not (0 <= signa_score <= MAX_SIGNA_SCORE):
        return f"signa_score {signa_score} outside allowed range 0-{MAX_SIGNA_SCORE}"

    if signa_score < MIN_SIGNA_SCORE:
        return f"signa_score {signa_score} below minimum {MIN_SIGNA_SCORE}"

    if signa_grade not in ALLOWED_GRADES:
        return f"signa_grade '{signa_grade}' not allowed (require A or B)"

    if direction == "CALL" and signa_bias != "BULLISH":
        return f"signa_bias '{signa_bias}' does not align with CALL (requires BULLISH)"
    if direction == "PUT" and signa_bias != "BEARISH":
        return f"signa_bias '{signa_bias}' does not align with PUT (requires BEARISH)"

    if entry_price <= 0:
        return f"entry_price {entry_price} must be > 0"

    if contract_strike <= 0:
        return f"contract_strike {contract_strike} must be > 0"

    days_out = (contract_expiry - date.today()).days
    if days_out < MIN_DAYS_TO_EXPIRY:
        return f"contract_expiry {days_out}d out below minimum {MIN_DAYS_TO_EXPIRY}d"

    if max_premium <= 0:
        return f"max_premium {max_premium} must be > 0"
    if max_premium > MAX_PREMIUM_CEILING:
        return f"max_premium {max_premium} exceeds ceiling {MAX_PREMIUM_CEILING}"

    if max_contracts < MIN_CONTRACTS_FLOOR:
        return f"max_contracts {max_contracts} must be at least {MIN_CONTRACTS_FLOOR}"

    if price_target_raw is None:
        return "price_target is required"

    if price_target <= 0:
        return f"price_target {price_target} must be > 0"

    if direction == "CALL" and price_target <= entry_price:
        return "price_target must be above entry_price for CALL"
    if direction == "PUT" and price_target >= entry_price:
        return "price_target must be below entry_price for PUT"

    return None


def _parse_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))
