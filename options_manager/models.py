"""OptionTradePacket schema — Phase 1 data shape only.

No execution, fill, or order fields exist here by design. "EXECUTED" is
intentionally not a valid status in Phase 1.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal, Optional


@dataclass(kw_only=True)
class OptionTradePacket:
    ticker: str
    direction: Literal["CALL", "PUT"]
    entry_price: float
    price_target: float
    signa_score: int
    signa_grade: Literal["A", "B", "C"]
    signa_bias: Literal["BULLISH", "BEARISH", "NEUTRAL"]
    gex_regime: str
    gex_wall_above: Optional[float]
    gex_wall_below: Optional[float]
    contract_strike: float
    contract_expiry: date
    max_premium: float = 3.00
    max_contracts: int = 2
    account_tag: str = "agentic_micro_account"
    source: str = "claude_session"
    created_at: datetime
    status: Literal["PENDING", "QUEUED", "REJECTED"]
    rejection_reason: Optional[str] = None
