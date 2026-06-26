"""Companion-specific Signa alignment gate — stricter than the futures gate.

The futures ``strategy/signa_gate.evaluate_signa`` keys on the WEEKLY direction and
treats missing Signa as a NEUTRAL pass. That is wrong for this lane: the companion
options trade should only open when Signa actively confirms, so this gate:

- requires grade in {A, B} (C/D/F or missing -> REJECT),
- requires DAILY direction aligned with the futures direction,
- records grade A/B + neutral/missing daily direction as WATCHLIST,
- FAILS CLOSED: missing Signa, low grade, or opposition -> REJECT.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from context.market_context import MarketState
from strategy.signa_gate import _direction  # shared up/down/neutral normalizer

CompanionSignaStatus = Literal["PASS", "WATCHLIST", "REJECT"]


@dataclass(frozen=True)
class CompanionSignaResult:
    status: CompanionSignaStatus
    failed_rule: str | None = None
    reason: str | None = None

    @property
    def passed(self) -> bool:
        return self.status == "PASS"

    @property
    def watchlist(self) -> bool:
        return self.status == "WATCHLIST"


def evaluate_companion_signa(state: MarketState, direction: str) -> CompanionSignaResult:
    side = (direction or "").strip().upper()
    if side not in {"LONG", "SHORT"}:
        return CompanionSignaResult("REJECT", "signa_direction_absent", "futures direction absent")

    signa = state.signa
    if signa is None:
        return CompanionSignaResult("REJECT", "signa_missing", "Signa data absent")

    grade = str(signa.grade or "").strip().upper()
    if grade not in {"A", "B"}:
        # Covers C/D/F and a missing/blank grade (stale snapshots arrive with no grade).
        return CompanionSignaResult(
            "REJECT", "signa_grade", f"Signa grade {grade or 'missing'} below required A/B"
        )

    daily = _direction(signa.daily_direction)
    if not daily or daily == "NEUTRAL":
        return CompanionSignaResult(
            "WATCHLIST", "signa_daily_neutral", "Signa grade A/B but daily direction is missing/neutral"
        )
    if daily != side:
        return CompanionSignaResult(
            "REJECT", "signa_opposes", f"Signa daily {daily} opposes {side}"
        )

    return CompanionSignaResult("PASS", reason=f"Signa grade {grade}, daily {daily} aligned")
