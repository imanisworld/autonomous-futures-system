"""Signa deterministic quality gate."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from context.market_context import MarketState

SignaStatus = Literal["PASS", "FAIL", "NEUTRAL"]


@dataclass(frozen=True)
class SignaGateResult:
    status: SignaStatus
    failed_gate: str | None = None
    reasons: list[str] = field(default_factory=list)


def evaluate_signa(state: MarketState, direction: str | None) -> SignaGateResult:
    signa = state.signa
    if signa is None or (signa.grade is None and signa.weekly_direction is None and signa.score is None):
        return SignaGateResult("NEUTRAL", reasons=["Signa data absent"])
    if direction not in {"LONG", "SHORT"}:
        return SignaGateResult("NEUTRAL", reasons=["direction absent"])

    grade = str(signa.grade or "").strip().upper()
    weekly = _direction(signa.weekly_direction)
    if grade in {"C", "D", "F"}:
        return SignaGateResult("FAIL", "SIGNA_GRADE_FAIL", [f"Signa grade {grade}"])
    if weekly and weekly != "NEUTRAL" and weekly != direction:
        return SignaGateResult("FAIL", "SIGNA_WEEKLY_OPPOSES", [f"weekly Signa {weekly} opposes {direction}"])
    if grade in {"A", "B"}:
        return SignaGateResult("PASS", reasons=[f"Signa grade {grade}"])
    return SignaGateResult("NEUTRAL", reasons=["Signa present but not decisive"])


def _direction(value: str | None) -> str:
    value = str(value or "").strip().lower()
    if value in {"up", "bull", "bullish", "long"}:
        return "LONG"
    if value in {"down", "bear", "bearish", "short"}:
        return "SHORT"
    if value in {"neutral", "mixed", "flat"}:
        return "NEUTRAL"
    return ""
